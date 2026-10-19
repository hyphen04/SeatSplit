#!/usr/bin/env python3
"""Multiseat Manager - a small GTK3 app to split one Linux PC into several seats.

Drag device cards (graphics card, keyboard, mouse) between seats, then press Apply.
Works live through systemd-logind, no reboot. Any number of seats (up to 8).

Needs only python3-gi (GTK 3) and polkit (pkexec) - both are on most desktops:
    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 policykit-1

Options:  --demo          fake hardware, nothing is changed (for trying the UI)
          --theme=dark    start in dark or light theme
"""
import glob, json, os, pwd, re, shutil, struct, subprocess, sys, threading, time
from collections import defaultdict
from string import Template

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

try:
    import cairo  # only used for a nicer drag preview
except Exception:  # noqa: BLE001
    cairo = None

APP_NAME = "Multiseat Manager"
APP_ID = "multiseat-manager"
MAIN = "seat0"          # shown as "Seat 1"
MAX_SEATS = 8
GREETER_USERS = {"lightdm", "gdm", "sddm", "Debian-gdm"}
DM_SERVICES = ["lightdm", "gdm3", "gdm", "sddm"]
DEMO = "--demo" in sys.argv
_demo_seats = {}        # device path -> seat, used while in demo mode


def seat_num(seat):
    return int(seat[4:])


def seat_name(seat):
    return f"Seat {seat_num(seat) + 1}"


def norm_seat(s):
    return s if re.fullmatch(r"seat\d+", s or "") else MAIN


# ============================================================ backend
def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).returncode
    except FileNotFoundError:
        return 127


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def props(syspath):
    d = {}
    for line in sh("udevadm", "info", "-q", "property", "-p", syspath).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def monitor_name(edid_path):
    try:
        with open(edid_path, "rb") as f:
            e = f.read()
    except OSError:
        return "Unknown monitor"
    if len(e) < 128:
        return "Unknown monitor"
    for off in (54, 72, 90, 108):
        blk = e[off:off + 18]
        if blk[:3] == b"\x00\x00\x00" and blk[3] == 0xFC:
            return blk[5:18].split(b"\n")[0].decode("ascii", "ignore").strip()
    return "Monitor"


def scan_gpus():
    gpus = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        name = os.path.basename(card)
        if "-" in name:
            continue
        dev = os.path.realpath(card + "/device")
        pci = os.path.basename(dev)
        drv = os.path.basename(os.path.realpath(dev + "/driver")) if os.path.exists(dev + "/driver") else "?"
        desc = ""
        if re.match(r"[0-9a-f]{4}:", pci):
            desc = sh("lspci", "-s", pci).strip().split(": ", 1)[-1]
        desc = re.sub(r"\s*\(rev [0-9a-f]+\)", "", desc) or pci
        mons = []
        for conn in sorted(glob.glob(f"/sys/class/drm/{name}-*")):
            if read(conn + "/status") != "connected":
                continue
            modes = read(conn + "/modes").splitlines()
            mons.append({"conn": os.path.basename(conn)[len(name) + 1:],
                         "name": monitor_name(conn + "/edid"),
                         "res": modes[0] if modes else "?"})
        paths = [os.path.realpath(card)]
        for r in sorted(glob.glob("/sys/class/drm/renderD*")):
            if os.path.realpath(r + "/device") == dev:
                paths.append(os.path.realpath(r))
        for f in sorted(glob.glob("/sys/class/graphics/fb*")):
            if os.path.realpath(f + "/device") == dev:
                paths.append(os.path.realpath(f))
        gpus.append({"name": name, "pci": pci, "desc": desc, "drv": drv, "mons": mons,
                     "boot": read(dev + "/boot_vga") == "1",
                     "seat": props(card).get("ID_SEAT", "seat0"), "paths": paths})
    return gpus


PORT_RE = re.compile(r"/(?P<ctrl>[0-9a-f]{4}:[0-9a-f:.]+)/usb\d+/(?P<port>\d+-\d+)(?=[./:])")


def scan_inputs():
    devs = []
    for inp in sorted(glob.glob("/sys/class/input/input[0-9]*"),
                      key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)))):
        real = os.path.realpath(inp)
        name = read(inp + "/name")
        evs = glob.glob(inp + "/event*")
        event = "/dev/input/" + os.path.basename(evs[0]) if evs else None
        kind, seat = "other", "seat0"
        if evs:
            p = props(evs[0])
            seat = p.get("ID_SEAT", "seat0")
            if p.get("ID_INPUT_KEYBOARD") == "1" and "Control" not in name:
                kind = "keyboard"
            elif p.get("ID_INPUT_MOUSE") == "1":
                kind = "mouse"
        m = PORT_RE.search(real)
        devs.append({"name": name, "kind": kind, "seat": seat, "event": event, "sys": real,
                     "key": (m["ctrl"], m["port"]) if m else None})
    return devs


def skey(k):
    return (k[0], tuple(int(x) for x in re.split(r"[-.]", k[1])))


def build_groups(devs):
    groups = {}
    for d in devs:
        if d["key"] is None:
            continue
        g = groups.setdefault(d["key"], {"devs": [], "seats": set()})
        g["devs"].append(d)
        g["seats"].add(d["seat"])
    return groups


def sessions_by_seat():
    out = defaultdict(list)
    for line in sh("loginctl", "list-sessions", "--no-legend").splitlines():
        p = line.split()
        if len(p) >= 4:
            out[p[3]].append(p[2])
    return out


def detect_dm():
    for n in DM_SERVICES:
        if run("systemctl", "is-active", "--quiet", n) == 0:
            return n
    return os.path.basename(read("/etc/X11/default-display-manager")) or None


def demo_scan():
    def seat_of(*paths):
        for p in paths:
            if p in _demo_seats:
                return _demo_seats[p]
        return MAIN
    gpus = [
        {"name": "card0", "pci": "0000:00:02.0", "desc": "Intel UHD Graphics 630", "drv": "i915",
         "mons": [{"conn": "HDMI-A-1", "name": "DELL P2419H", "res": "1920x1080"}],
         "boot": True, "seat": seat_of("/demo/card0"), "paths": ["/demo/card0"]},
        {"name": "card1", "pci": "0000:01:00.0", "desc": "NVIDIA GeForce GT 710", "drv": "nouveau",
         "mons": [{"conn": "HDMI-A-2", "name": "LG 22MP48", "res": "1920x1080"}],
         "boot": False, "seat": seat_of("/demo/card1"), "paths": ["/demo/card1", "/demo/render1"]},
        {"name": "card2", "pci": "0000:02:00.0", "desc": "AMD Radeon RX 550", "drv": "amdgpu",
         "mons": [{"conn": "DP-1", "name": "Acer KA222Q", "res": "1920x1080"}],
         "boot": False, "seat": seat_of("/demo/card2"), "paths": ["/demo/card2"]},
    ]
    ctrl = "0000:00:14.0"

    def dev(name, kind, port, n):
        p = f"/demo/input{n}"
        return {"name": name, "kind": kind, "seat": seat_of(p), "event": None, "sys": p,
                "key": (ctrl, port)}
    devs = [dev("Logitech K120 Keyboard", "keyboard", "1-1", 1),
            dev("Logitech M90 Mouse", "mouse", "1-2", 2),
            dev("HP Wired Keyboard", "keyboard", "1-5", 3),
            dev("Razer Basilisk Mouse", "mouse", "1-6", 4),
            dev("Dell KB216 Keyboard", "keyboard", "1-7", 5),
            dev("Microsoft Basic Mouse", "mouse", "1-8", 6)]
    sess = {MAIN: ["alice"]}
    for s in set(_demo_seats.values()):
        sess[s] = ["user" + s[4:]]
    return gpus, devs, sess


def scan_all():
    if DEMO:
        time.sleep(0.2)
        return demo_scan()
    return scan_gpus(), scan_inputs(), sessions_by_seat()


# ---------------------------------------------------------------- model
def build_items(gpus, devs):
    """Turn scanned hardware into movable 'cards'."""
    items = []
    for g in gpus:
        seat = norm_seat(g["seat"])
        items.append({"id": "gpu:" + g["name"], "kind": "gpu", "badges": ["GPU"],
                      "title": g["desc"], "drv": g["drv"], "mons": g["mons"], "boot": g["boot"],
                      "paths": g["paths"], "kb_events": [], "ms_events": [], "port": None,
                      "locked": False, "orig": seat, "seat": seat})
    groups = build_groups(devs)
    for k in sorted(groups, key=skey):
        g = groups[k]
        inp = [d for d in g["devs"] if d["kind"] in ("keyboard", "mouse")]
        if not inp:
            continue
        kinds = sorted({d["kind"] for d in inp}, key=lambda x: x != "keyboard")
        names = []
        for d in inp:
            if d["name"] not in names:
                names.append(d["name"])
        others = sorted(norm_seat(s) for s in g["seats"] if norm_seat(s) != MAIN)
        seat = others[0] if others else MAIN
        items.append({"id": f"usb:{k[0]}:{k[1]}", "kind": "input",
                      "badges": [x.upper() for x in kinds], "title": " / ".join(names),
                      "drv": "", "mons": [], "boot": False,
                      "paths": [d["sys"] for d in g["devs"]],
                      "kb_events": [d["event"] for d in inp if d["event"] and d["kind"] == "keyboard"],
                      "ms_events": [d["event"] for d in inp if d["event"] and d["kind"] == "mouse"],
                      "port": k[1], "locked": False, "orig": seat, "seat": seat})
    for d in devs:  # built-in (non-USB) keyboards/mice cannot be moved
        if d["key"] is None and d["kind"] in ("keyboard", "mouse"):
            items.append({"id": "int:" + d["sys"], "kind": "input", "badges": [d["kind"].upper()],
                          "title": d["name"], "drv": "", "mons": [], "boot": False,
                          "paths": [], "kb_events": [], "ms_events": [], "port": None,
                          "locked": True, "orig": MAIN, "seat": MAIN})
    return items


def sort_key(it):
    return (it["kind"] != "gpu", "KEYBOARD" not in it["badges"], it["id"])


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def summary_text(items):
    screens = sum(len(i["mons"]) for i in items if i["kind"] == "gpu")
    kb = sum(1 for i in items if "KEYBOARD" in i["badges"])
    ms = sum(1 for i in items if "MOUSE" in i["badges"])
    return " · ".join([plural(screens, "screen"), plural(kb, "keyboard"), plural(ms, "mouse", "mice")])


def check(items):
    """Return human-readable warnings for the planned split (any number of seats)."""
    used = sorted({i["seat"] for i in items if i["seat"] != MAIN}, key=seat_num)
    if not used:
        return []

    def has(lst, kind):
        return any(kind in i["badges"] for i in lst)
    warn = []
    for s in used:
        mine = [i for i in items if i["seat"] == s]
        gpus = [i for i in mine if i["kind"] == "gpu"]
        n = seat_name(s)
        if not gpus:
            warn.append(f"{n} needs a graphics card.")
        elif not any(i["mons"] for i in gpus):
            warn.append(f"{n}'s graphics card has no monitor connected.")
        if any(i["boot"] for i in gpus):
            warn.append(f"The primary (boot) graphics card is on {n}. This is risky.")
        if not has(mine, "KEYBOARD"):
            warn.append(f"{n} needs a keyboard.")
        if not has(mine, "MOUSE"):
            warn.append(f"{n} needs a mouse.")
    main = [i for i in items if i["seat"] == MAIN]
    n = seat_name(MAIN)
    if not any(i["kind"] == "gpu" for i in main):
        warn.append(f"{n} would be left without a graphics card.")
    if not has(main, "KEYBOARD"):
        warn.append(f"{n} would be left without a keyboard.")
    if not has(main, "MOUSE"):
        warn.append(f"{n} would be left without a mouse.")
    return warn


def plan_suggestion(items, seats, max_seats=MAX_SEATS):
    """Suggest a split. Returns ({item_id: seat}, [newly needed seat ids]).

    Every spare, non-boot graphics card that has a monitor gets its own seat, together
    with the highest-numbered USB keyboard and mouse that are still free. Seat 1 always
    keeps at least one keyboard and one mouse. Seats are created as needed.
    """
    free = [i for i in items if not i["locked"]]
    out = {i["id"]: MAIN for i in free}
    gpus = sorted((i for i in free if i["kind"] == "gpu" and not i["boot"] and i["mons"]),
                  key=lambda i: -len(i["mons"]))
    usb = [i for i in free if i["kind"] == "input"]
    assigned, created = set(), []
    targets = [s for s in seats if s != MAIN]

    def pick(kind):
        pool = [i for i in usb if kind in i["badges"] and i["id"] not in assigned]
        return pool[-1] if len(pool) > 1 else None   # leave one for Seat 1

    n = 0
    while n < len(gpus):
        if n < len(targets):
            seat = targets[n]
        elif len(targets) + 1 < max_seats:
            seat = f"seat{max(seat_num(s) for s in list(seats) + created) + 1}"
        else:
            break
        mine = [i for i in usb if i["orig"] == seat and i["id"] not in assigned]
        if not mine:
            kb = pick("KEYBOARD")
            if kb:
                assigned.add(kb["id"])
                mine.append(kb)
            if not any("MOUSE" in i["badges"] for i in mine):
                ms = pick("MOUSE")
                if ms:
                    assigned.add(ms["id"])
                    mine.append(ms)
        if not (any("KEYBOARD" in i["badges"] for i in mine) and any("MOUSE" in i["badges"] for i in mine)):
            for i in mine:
                assigned.discard(i["id"])
            break
        assigned.update(i["id"] for i in mine)
        out[gpus[n]["id"]] = seat
        for i in mine:
            out[i["id"]] = seat
        if seat not in seats and seat not in created:
            created.append(seat)
            targets.append(seat)
        n += 1
    return out, created


def reload_udev():
    run("udevadm", "control", "--reload")
    run("udevadm", "trigger", "--action=change", "--subsystem-match=drm",
        "--subsystem-match=graphics", "--subsystem-match=input")
    run("udevadm", "settle")
    time.sleep(3)


def apply_plan(plan):
    """plan: {seat_id: [sysfs paths]} for every non-main seat. Returns a list of error strings."""
    global _demo_seats
    if DEMO:
        time.sleep(1)
        _demo_seats = {p: s for s, paths in plan.items() for p in paths}
        return []
    errors = []
    run("loginctl", "flush-devices")
    for seat, paths in plan.items():
        for p in paths:
            r = subprocess.run(["loginctl", "attach", seat, p], capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f"Could not attach {p} to {seat}: {r.stderr.strip()}")
    reload_udev()
    return errors


def undo_all():
    global _demo_seats
    if DEMO:
        time.sleep(0.5)
        _demo_seats = {}
        return
    run("loginctl", "flush-devices")
    reload_udev()


def fix_greeter():
    """Returns (ok, message). Gives every extra seat the simple GTK greeter on lightdm."""
    if DEMO:
        return True, "Demo: nothing changed."
    dm = detect_dm()
    if dm != "lightdm":
        return False, f"Your login manager is {dm or 'unknown'}. This fix is only needed for lightdm."
    if run("apt-get", "install", "-y", "lightdm-gtk-greeter") != 0:
        return False, "Could not install lightdm-gtk-greeter."
    os.makedirs("/etc/lightdm/lightdm.conf.d", exist_ok=True)
    with open("/etc/lightdm/lightdm.conf.d/60-extra-seats-greeter.conf", "w") as f:  # seat0 is left alone
        f.write("".join(f"[Seat:seat{n}]\ngreeter-session=lightdm-gtk-greeter\n\n" for n in range(1, MAX_SEATS)))
    return True, "Done. Restart the login screen to apply."


# ---------------------------------------------------------------- key detection
EV_FMT = "llHHi"                       # struct input_event
EV_SIZE = struct.calcsize(EV_FMT)


def has_press(data):
    """True if the raw evdev bytes contain a key / button press (not just motion or release)."""
    for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
        _s, _u, typ, _c, val = struct.unpack_from(EV_FMT, data, off)
        if typ == 1 and val == 1:
            return True
    return False


def drain(fd):
    try:
        while os.read(fd, 4096):
            pass
    except OSError:
        pass


# ---------------------------------------------------------------- settings
def real_user():
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    try:
        return pwd.getpwuid(int(uid))
    except (TypeError, ValueError, KeyError):
        return None


def cfg_file():
    u = real_user()
    home = u.pw_dir if u else os.path.expanduser("~")
    return os.path.join(home, ".config", APP_ID, "config.json")


def load_cfg():
    try:
        with open(cfg_file()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cfg(cfg):
    path = cfg_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f)
        u = real_user()
        if u and os.geteuid() == 0:
            os.chown(os.path.dirname(path), u.pw_uid, u.pw_gid)
            os.chown(path, u.pw_uid, u.pw_gid)
    except OSError:
        pass


# ============================================================ look
LIGHT = dict(bg="#f4f5f8", card="#ffffff", border="#e3e6ee", text="#1a1d27", dim="#6a7186",
             accent="#4f5bd5", accenth="#424ec0", accentbg="#eef0ff", onaccent="#ffffff",
             chip="#f8f9fc", hover="#eceef5", warn="#b45309", ok="#15803d", danger="#c62828",
             gpubg="#efe9ff", gpufg="#5b3fd0", kbbg="#e6f0ff", kbfg="#1f5fbf",
             msbg="#e3f6f1", msfg="#0f7a63", lockbg="#eceef4", warnbg="#fdf0dd")
DARK = dict(bg="#0f1116", card="#171a22", border="#272c3a", text="#e9ebf2", dim="#8b92a7",
            accent="#7b8bff", accenth="#93a0ff", accentbg="#1c2036", onaccent="#0f1116",
            chip="#1c202b", hover="#232838", warn="#f0a35c", ok="#4ade80", danger="#e5484d",
            gpubg="#2a2250", gpufg="#b7a6ff", kbbg="#18294a", kbfg="#8db6ff",
            msbg="#12332d", msfg="#6fe0c3", lockbg="#242938", warnbg="#3a2c18")

CSS = Template("""
window { background: $bg; color: $text; }
headerbar { background: $card; box-shadow: none; border-bottom: 1px solid $border; color: $text; min-height: 46px; }
headerbar .title { font-weight: 700; color: $text; }
headerbar button { background-image: none; background-color: transparent; border: none; box-shadow: none;
                   text-shadow: none; color: $dim; border-radius: 8px; padding: 6px 12px; min-height: 0; }
headerbar button:hover, headerbar button:checked { background-color: $hover; color: $text; }
headerbar button.suggest { background-color: $accentbg; color: $accent; font-weight: 700; }
headerbar button.suggest:hover { background-color: $hover; color: $accenth; }
scrolledwindow, viewport { background: transparent; }
.hint { color: $dim; font-size: 13px; }
.seat { background: $card; border: 1px solid $border; border-radius: 14px; }
.seat.hover { border-color: $accent; background: $accentbg; }
.seat-title { font-size: 17px; font-weight: 700; color: $text; }
.seat-sub { color: $dim; font-size: 12px; }
.summary { color: $dim; font-size: 12px; }
.dot { font-size: 11px; }
.dot.on { color: $ok; }
.dot.wait { color: $warn; }
.dot.off { color: $dim; }
.rule { background: $border; min-height: 1px; }
.chip { background: $chip; border: 1px solid $border; border-radius: 10px; padding: 10px 12px; }
.chip:hover { border-color: $accent; }
.chip.moved { border-color: $accent; background: $accentbg; }
.chip.dragging { opacity: 0.35; }
.chip.locked { opacity: 0.7; }
.chip-title { font-size: 13px; font-weight: 600; color: $text; }
.chip-sub { font-size: 12px; color: $dim; }
.chip-sub.warn { color: $warn; }
.badge { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 6px; }
.badge-gpu { background: $gpubg; color: $gpufg; }
.badge-keyboard { background: $kbbg; color: $kbfg; }
.badge-mouse { background: $msbg; color: $msfg; }
.badge-lock { background: $lockbg; color: $dim; }
.badge-warn { background: $warnbg; color: $warn; }
.move { background-image: none; background-color: transparent; border: 1px solid $border; box-shadow: none;
        text-shadow: none; color: $dim; border-radius: 8px; padding: 3px 10px; min-height: 0; font-size: 12px; }
.move:hover, .move:checked { background-color: $hover; color: $text; border-color: $accent; }
.mini { background-image: none; background-color: transparent; border: 1px solid $border; box-shadow: none;
        text-shadow: none; color: $dim; border-radius: 8px; padding: 3px 10px; min-height: 0; font-size: 12px; }
.mini:hover, .mini:checked { background-color: $hover; color: $text; border-color: $accent; }
.empty { color: $dim; font-size: 13px; padding: 28px 12px; border: 1px solid $border; border-radius: 10px; }
.bar { background: $card; border: 1px solid $border; border-radius: 14px; padding: 10px 14px; }
.bar-title { font-weight: 700; color: $text; }
.bar-warn { color: $warn; font-size: 12px; }
.panel { background: $card; border: 1px solid $accent; border-radius: 16px; padding: 20px 22px;
         box-shadow: 0 12px 40px rgba(0, 0, 0, 0.30); }
.panel-title { font-size: 12px; font-weight: 700; color: $accent; }
.panel-msg { font-size: 15px; font-weight: 600; color: $text; }
.found { font-size: 13px; font-weight: 600; color: $text; background: $chip; border: 1px solid $border;
         border-radius: 10px; padding: 10px 12px; }
.primary { background-image: none; background-color: $accent; color: $onaccent; border: none; box-shadow: none;
           text-shadow: none; border-radius: 9px; padding: 8px 20px; font-weight: 700; }
.primary:hover { background-color: $accenth; }
.primary:disabled { opacity: 0.5; }
.ghost { background-image: none; background-color: transparent; color: $dim; border: none; box-shadow: none;
         text-shadow: none; border-radius: 9px; padding: 8px 14px; font-weight: 600; }
.ghost:hover { background-color: $hover; color: $text; }
.toast { background: $text; color: $bg; border-radius: 10px; padding: 10px 18px; font-weight: 600; }
.toast.err { background: $danger; color: #ffffff; }
""")


def lbl(text="", cls="", wrap=False, ellipsize=False):
    l = Gtk.Label(label=text)
    l.set_xalign(0)
    if wrap:
        l.set_line_wrap(True)
        l.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        l.set_ellipsize(Pango.EllipsizeMode.END)
        l.set_width_chars(8)
        l.set_max_width_chars(40)
    for c in cls.split():
        l.get_style_context().add_class(c)
    return l


def vbox(spacing=0):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def hbox(spacing=0):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)


def button(text, cls, cb, tip=None):
    b = Gtk.Button(label=text)
    b.get_style_context().add_class(cls)
    b.connect("clicked", cb)
    if tip:
        b.set_tooltip_text(tip)
    return b


def badge(text, cls=None):
    l = lbl(text, "badge " + (cls or "badge-" + text.lower()))
    l.set_valign(Gtk.Align.CENTER)
    return l


def margins(w, a=0, b=None, c=None, d=None):
    """margins(w, all) or margins(w, top, right, bottom, left)"""
    if b is None:
        top = right = bottom = left = a
    else:
        top, right, bottom, left = a, b, c, d
    w.set_margin_top(top)
    w.set_margin_end(right)
    w.set_margin_bottom(bottom)
    w.set_margin_start(left)


TARGET = Gtk.TargetEntry.new("application/x-multiseat-card", Gtk.TargetFlags.SAME_APP, 0)


def move_control(app, it):
    """Button (2 seats) or dropdown (3+ seats) to move a card without dragging."""
    seats = app.seats
    others = [s for s in seats if s != it["seat"]]
    if len(seats) == 2:
        o = others[0]
        fwd = seat_num(o) > seat_num(it["seat"])
        txt = f"{seat_name(o)}  →" if fwd else f"←  {seat_name(o)}"
        b = button(txt, "move", lambda _b: app.move(it["id"], o), f"Move to {seat_name(o)}")
    else:
        b = Gtk.MenuButton(label="Move  ▾")
        b.get_style_context().add_class("move")
        pop = Gtk.Popover()
        box = vbox(2)
        margins(box, 8)
        for o in others:
            m = Gtk.ModelButton()
            m.set_property("text", "Move to " + seat_name(o))
            m.connect("clicked", lambda _b, o=o: (pop.popdown(), app.move(it["id"], o)))
            box.pack_start(m, False, False, 0)
        box.show_all()
        pop.add(box)
        b.set_popover(pop)
    b.set_valign(Gtk.Align.CENTER)
    return b


def make_chip(app, it):
    """One draggable device card."""
    eb = Gtk.EventBox()
    box = hbox(12)
    ctx = box.get_style_context()
    ctx.add_class("chip")
    if it["seat"] != it["orig"]:
        ctx.add_class("moved")
    if it["locked"]:
        ctx.add_class("locked")
    eb.add(box)

    text = vbox(3)
    badges = hbox(4)
    for b in it["badges"]:
        badges.pack_start(badge(b), False, False, 0)
    if it["boot"]:
        badges.pack_start(badge("PRIMARY", "badge-warn"), False, False, 0)
    if it["locked"]:
        badges.pack_start(badge("BUILT-IN", "badge-lock"), False, False, 0)
    text.pack_start(badges, False, False, 0)
    text.pack_start(lbl(it["title"], "chip-title", ellipsize=True), False, False, 0)
    if it["kind"] == "gpu":
        for m in it["mons"]:
            text.pack_start(lbl(f"{m['name']} · {m['res']} · {m['conn']}", "chip-sub", ellipsize=True), False, False, 0)
        if not it["mons"]:
            text.pack_start(lbl("No monitor connected", "chip-sub warn"), False, False, 0)
        text.pack_start(lbl(f"driver: {it['drv']}", "chip-sub"), False, False, 0)
    elif it["port"]:
        text.pack_start(lbl(f"USB port {it['port']}", "chip-sub"), False, False, 0)
    box.pack_start(text, True, True, 0)

    if not it["locked"]:
        box.pack_start(move_control(app, it), False, False, 0)
        eb.set_tooltip_text("Drag to another seat")
        eb.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [TARGET], Gdk.DragAction.MOVE)
        eb.connect("drag-data-get", lambda w, c, data, info, t: data.set(data.get_target(), 8, it["id"].encode()))

        def begin(w, c):
            try:
                if cairo is not None:
                    a = w.get_allocation()
                    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, a.width, a.height)
                    w.draw(cairo.Context(surf))
                    Gtk.drag_set_icon_surface(c, surf)
            except Exception:  # noqa: BLE001
                pass
            box.get_style_context().add_class("dragging")

        eb.connect("drag-begin", begin)
        eb.connect("drag-end", lambda w, c: box.get_style_context().remove_class("dragging"))
    else:
        eb.set_tooltip_text("Built-in device. It always stays on Seat 1.")
    return eb


class Column:
    """A seat: header with status, list of cards, and a drop target."""

    def __init__(self, app, seat):
        self.app, self.seat = app, seat
        self.eb = Gtk.EventBox()
        self.eb.set_size_request(300, -1)
        self.box = vbox(0)
        self.box.get_style_context().add_class("seat")
        self.eb.add(self.box)

        head = vbox(4)
        margins(head, 16, 18, 12, 18)
        row = hbox(8)
        row.pack_start(lbl(seat_name(seat), "seat-title"), False, False, 0)
        row.pack_start(lbl("main" if seat == MAIN else seat, "seat-sub"), False, False, 0)
        acts = hbox(6)
        self.remove_btn = None
        if seat != MAIN:
            self.detect_btn = button("Detect devices", "mini", lambda _b: app.start_detect(seat),
                                     f"Press a key and click a mouse to find {seat_name(seat)}'s devices")
            acts.pack_start(self.detect_btn, False, False, 0)
            self.remove_btn = button("✕", "mini", lambda _b: app.remove_seat(seat), "Remove this empty seat")
            self.remove_btn.set_no_show_all(True)
            acts.pack_start(self.remove_btn, False, False, 0)
        row.pack_end(acts, False, False, 0)
        head.pack_start(row, False, False, 0)
        st = hbox(6)
        self.dot = lbl("●", "dot off")
        self.status = lbl("", "seat-sub")
        st.pack_start(self.dot, False, False, 0)
        st.pack_start(self.status, False, False, 0)
        head.pack_start(st, False, False, 0)
        self.summary = lbl("", "summary")
        head.pack_start(self.summary, False, False, 0)
        self.box.pack_start(head, False, False, 0)

        rule = Gtk.Box()
        rule.get_style_context().add_class("rule")
        self.box.pack_start(rule, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        self.list = vbox(8)
        margins(self.list, 14)
        sw.add(self.list)
        self.box.pack_start(sw, True, True, 0)

        self.eb.drag_dest_set(Gtk.DestDefaults.ALL, [TARGET], Gdk.DragAction.MOVE)
        self.eb.connect("drag-motion", self._motion)
        self.eb.connect("drag-leave", lambda w, c, t: self.box.get_style_context().remove_class("hover"))
        self.eb.connect("drag-data-received", self._received)

    def _motion(self, w, ctx, x, y, t):
        self.box.get_style_context().add_class("hover")
        return False  # let GTK finish the default handling

    def _received(self, w, ctx, x, y, data, info, t):
        raw = data.get_data()
        if raw:
            self.app.move(raw.decode(), self.seat)

    def fill(self, items, placeholder):
        for ch in self.list.get_children():
            self.list.remove(ch)
        for it in items:
            self.list.pack_start(make_chip(self.app, it), False, False, 0)
        if not items:
            self.list.pack_start(lbl(placeholder, "empty", wrap=True), False, False, 0)
        self.list.show_all()

    def set_status(self, kind, text):
        ctx = self.dot.get_style_context()
        for c in ("on", "wait", "off"):
            ctx.remove_class(c)
        ctx.add_class(kind)
        self.status.set_text(text)

    def set_removable(self, yes):
        if self.remove_btn:
            self.remove_btn.set_visible(yes)


# ============================================================ app
class App(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(980, 640)
        self.set_size_request(760, 540)
        self.connect("destroy", Gtk.main_quit)
        self.busy = False
        self.det = None
        self.items, self.sessions = [], {}
        self.seats, self.extra, self.cols = [MAIN, "seat1"], set(), {}
        self._toast_id = 0

        self.cfg = load_cfg()
        theme = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--theme=")), None)
        self.dark = (theme or self.cfg.get("theme", "light")) == "dark"
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()

        self.build_header()
        self.build_body()
        self.show_all()
        self.refresh()

    # ---------------------------------------------------- theme
    def apply_theme(self):
        self.provider.load_from_data(CSS.substitute(DARK if self.dark else LIGHT).encode())
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", self.dark)
        if hasattr(self, "theme_btn"):
            self.theme_btn.set_label("☀  Light" if self.dark else "☾  Dark")

    def toggle_theme(self, _b=None):
        self.dark = not self.dark
        self.apply_theme()
        self.cfg["theme"] = "dark" if self.dark else "light"
        save_cfg(self.cfg)

    # ---------------------------------------------------- layout
    def build_header(self):
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title(APP_NAME + ("  (demo)" if DEMO else ""))
        self.set_titlebar(hb)

        suggest = Gtk.Button(label="✦  Suggest setup")
        suggest.get_style_context().add_class("suggest")
        suggest.set_tooltip_text("Pick a likely split of graphics cards, keyboards and mice")
        suggest.connect("clicked", lambda _b: self.suggest())
        hb.pack_start(suggest)

        pop = Gtk.Popover()
        menu = vbox(2)
        margins(menu, 8)
        entries = [("Fix blank login screen (lightdm)", self.tool_greeter),
                   ("Restart login screen", self.tool_restart_dm),
                   None,
                   ("Remove all seat assignments", self.tool_undo)]
        for e in entries:
            if e is None:
                menu.pack_start(Gtk.Separator(), False, False, 4)
                continue
            m = Gtk.ModelButton()
            m.set_property("text", e[0])
            m.connect("clicked", lambda _b, cb=e[1]: (pop.popdown(), cb()))
            menu.pack_start(m, False, False, 0)
        menu.show_all()
        pop.add(menu)
        tools = Gtk.MenuButton(label="Tools  ▾")
        tools.set_popover(pop)
        self.theme_btn = Gtk.Button(label="")
        self.theme_btn.connect("clicked", self.toggle_theme)
        self.theme_btn.set_label("☀  Light" if self.dark else "☾  Dark")
        refresh = Gtk.Button(label="↻  Refresh")
        refresh.set_tooltip_text("Scan the hardware again")
        refresh.connect("clicked", lambda _b: self.refresh())
        hb.pack_end(tools)
        hb.pack_end(self.theme_btn)
        hb.pack_end(refresh)

    def build_body(self):
        overlay = Gtk.Overlay()
        self.add(overlay)
        main = vbox(14)
        margins(main, 20)
        overlay.add(main)

        top = hbox(12)
        self.hint = lbl("Drag devices between seats, or use the button on each card. "
                        "Nothing changes until you press Apply.", "hint", wrap=True)
        top.pack_start(self.hint, True, True, 0)
        self.add_btn = button("+  Add seat", "mini", lambda _b: self.add_seat(),
                              "Add another seat (for a third or fourth user)")
        top.pack_end(self.add_btn, False, False, 0)
        main.pack_start(top, False, False, 0)

        self.cols_box = hbox(16)
        self.cols_box.set_homogeneous(True)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        sw.add(self.cols_box)
        main.pack_start(sw, True, True, 0)

        # bottom bar with pending changes
        self.bar_rev = Gtk.Revealer()
        self.bar_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        bar = hbox(12)
        bar.get_style_context().add_class("bar")
        info = vbox(2)
        self.bar_title = lbl("", "bar-title")
        self.bar_warn = lbl("", "bar-warn", wrap=True)
        info.pack_start(self.bar_title, False, False, 0)
        info.pack_start(self.bar_warn, False, False, 0)
        bar.pack_start(info, True, True, 0)
        self.reset_btn = button("Reset", "ghost", lambda _b: self.reset())
        self.apply_btn = button("Apply", "primary", lambda _b: self.on_apply())
        bar.pack_end(self.apply_btn, False, False, 0)
        bar.pack_end(self.reset_btn, False, False, 0)
        self.bar_rev.add(bar)
        main.pack_start(self.bar_rev, False, False, 0)

        # guided detection panel (floats in the middle)
        self.det_rev = Gtk.Revealer()
        self.det_rev.set_halign(Gtk.Align.CENTER)
        self.det_rev.set_valign(Gtk.Align.CENTER)
        self.det_rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        panel = vbox(10)
        panel.get_style_context().add_class("panel")
        panel.set_size_request(440, -1)
        self.det_title = lbl("", "panel-title")
        self.det_msg = lbl("", "panel-msg", wrap=True)
        self.det_found = lbl("", "found", wrap=True)
        self.det_spin = Gtk.Spinner()
        self.det_ok = button("", "primary", lambda _b: self.det_action("ok"))
        self.det_retry = button("Try again", "ghost", lambda _b: self.det_action("retry"))
        self.det_skip = button("Skip", "ghost", lambda _b: self.det_action("skip"))
        self.det_cancel = button("Cancel", "ghost", lambda _b: self.det_action("cancel"))
        for w in (self.det_found, self.det_spin, self.det_ok, self.det_retry, self.det_skip, self.det_cancel):
            w.set_no_show_all(True)
        row = hbox(4)
        row.pack_start(self.det_spin, False, False, 6)
        row.pack_end(self.det_ok, False, False, 0)
        row.pack_end(self.det_retry, False, False, 0)
        row.pack_end(self.det_skip, False, False, 0)
        row.pack_end(self.det_cancel, False, False, 0)
        for w in (self.det_title, self.det_msg, self.det_found, row):
            panel.pack_start(w, False, False, 0)
        self.det_rev.add(panel)
        overlay.add_overlay(self.det_rev)

        # toast
        self.toast_rev = Gtk.Revealer()
        self.toast_rev.set_halign(Gtk.Align.CENTER)
        self.toast_rev.set_valign(Gtk.Align.START)
        self.toast_rev.set_margin_top(12)
        self.toast_rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.toast = lbl("", "toast")
        self.toast_rev.add(self.toast)
        overlay.add_overlay(self.toast_rev)
        self.toast_rev.show_all()

    # ---------------------------------------------------- helpers
    def notify(self, msg, err=False):
        ctx = self.toast.get_style_context()
        ctx.remove_class("err")
        if err:
            ctx.add_class("err")
        self.toast.set_text(msg)
        self.toast_rev.set_reveal_child(True)
        self._toast_id += 1
        tid = self._toast_id

        def hide():
            if tid == self._toast_id:
                self.toast_rev.set_reveal_child(False)
            return False
        GLib.timeout_add_seconds(5, hide)

    def confirm(self, text, detail=""):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.YES_NO, text=text)
        if detail:
            d.format_secondary_text(detail)
        r = d.run()
        d.destroy()
        return r == Gtk.ResponseType.YES

    def error(self, text, detail=""):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.CLOSE, text=text)
        if detail:
            d.format_secondary_text(detail)
        d.run()
        d.destroy()

    def bg(self, work, done):
        """Run work() off the UI thread, then done(result) on it."""
        if self.busy:
            self.notify("Please wait, something is still running.", True)
            return False
        self.busy = True

        def runner():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                res = e
            GLib.idle_add(finish, res)

        def finish(res):
            self.busy = False
            done(res)
            return False
        threading.Thread(target=runner, daemon=True).start()
        return True

    # ---------------------------------------------------- data + render
    def refresh(self):
        self.det_stop()
        self.bg(scan_all, self.on_scan)

    def on_scan(self, res):
        if isinstance(res, Exception):
            self.notify(f"Scan failed: {res}", True)
            return
        gpus, devs, sess = res
        self.items = build_items(gpus, devs)
        self.sessions = dict(sess)
        self.render()

    def sync_seats(self):
        used = {MAIN, "seat1"} | self.extra
        for i in self.items:
            used.add(i["orig"])
            used.add(i["seat"])
        self.seats = sorted(used, key=seat_num)

    def rebuild_columns(self):
        for ch in self.cols_box.get_children():
            self.cols_box.remove(ch)
        self.cols = {}
        for s in self.seats:
            col = Column(self, s)
            self.cols[s] = col
            self.cols_box.pack_start(col.eb, True, True, 0)
        self.cols_box.show_all()

    def placeholder(self, seat):
        if seat == MAIN:
            return "Nothing here. Drag devices back to give them to Seat 1."
        if sum(1 for i in self.items if i["kind"] == "gpu") < len(self.seats):
            return ("Each seat needs its own graphics card. Add another GPU (or a USB display adapter), "
                    "plug in its monitor, then press Refresh.")
        return "Drag a graphics card, a keyboard and a mouse here."

    def render(self):
        self.sync_seats()
        if list(self.cols) != self.seats:
            self.rebuild_columns()
        for s, col in self.cols.items():
            mine = sorted((i for i in self.items if i["seat"] == s), key=sort_key)
            col.fill(mine, self.placeholder(s))
            col.summary.set_text(summary_text(mine))
            users = [u for u in self.sessions.get(s, []) if u not in GREETER_USERS]
            if users:
                col.set_status("on", ", ".join(users) + " is logged in")
            elif self.sessions.get(s):
                col.set_status("wait", "Login screen")
            elif not any(i["orig"] == s for i in self.items):
                col.set_status("off", "Not set up yet")
            else:
                col.set_status("off", "Nobody logged in")
            empty = not any(i["orig"] == s or i["seat"] == s for i in self.items)
            col.set_removable(empty and s not in (MAIN, "seat1"))
        self.add_btn.set_sensitive(len(self.seats) < MAX_SEATS)
        self.update_bar()

    def update_bar(self):
        pending = [i for i in self.items if i["seat"] != i["orig"]]
        warns = check(self.items)
        removing = not any(i["seat"] != MAIN for i in self.items)
        self.apply_btn.set_label("Remove extra seats" if removing else "Apply")
        self.apply_btn.set_sensitive(not self.busy)
        self.bar_rev.set_reveal_child(bool(pending))
        if not pending:
            return
        self.bar_title.set_text(plural(len(pending), "change") + " not applied yet")
        self.bar_warn.set_text("\n".join(warns))
        self.bar_warn.set_visible(bool(warns))

    # ---------------------------------------------------- seats + moving
    def add_seat(self):
        if len(self.seats) >= MAX_SEATS:
            self.notify(f"{MAX_SEATS} seats is the maximum.", True)
            return
        sid = f"seat{max(seat_num(s) for s in self.seats) + 1}"
        self.extra.add(sid)
        self.render()
        self.notify(f"Added {seat_name(sid)}. Drag devices onto it.")

    def remove_seat(self, seat):
        self.extra.discard(seat)
        self.render()

    def move(self, iid, seat):
        for it in self.items:
            if it["id"] == iid and not it["locked"] and it["seat"] != seat:
                it["seat"] = seat
                GLib.idle_add(lambda: (self.render(), False)[1])  # rebuild after the drag has finished
                return

    def reset(self):
        for it in self.items:
            it["seat"] = it["orig"]
        self.render()

    def suggest(self):
        out, created = plan_suggestion(self.items, self.seats)
        if all(s == MAIN for s in out.values()):
            self.notify("Need a spare graphics card with a monitor, plus an extra keyboard and mouse.", True)
            return
        for it in self.items:
            if it["id"] in out:
                it["seat"] = out[it["id"]]
        self.extra.update(created)
        self.render()
        n = len({s for s in out.values() if s != MAIN})
        self.notify(f"Suggested {plural(n + 1, 'seat')}. Check it, then press Apply.")

    # ---------------------------------------------------- guided detection
    def start_detect(self, seat):
        self.det_stop()
        fds = {}
        for it in self.items:
            if it["kind"] != "input" or it["locked"]:
                continue
            for kind, evs in (("keyboard", it["kb_events"]), ("mouse", it["ms_events"])):
                for ev in evs:
                    try:
                        fds[os.open(ev, os.O_RDONLY | os.O_NONBLOCK)] = (it, kind)
                    except OSError:
                        pass
        if not fds:
            self.notify("Can't listen to devices here (this needs administrator access).", True)
            return
        self.det = {"seat": seat, "fds": fds, "ids": [], "timer": None, "chosen": {},
                    "kind": "keyboard", "state": "listen", "cur": None, "armed": 0}
        self.det_rev.set_reveal_child(True)
        self.det_listen("keyboard")

    def _det_unwatch(self):
        d = self.det
        if not d:
            return
        for i in d["ids"]:
            GLib.source_remove(i)
        d["ids"] = []

    def det_listen(self, kind):
        d = self.det
        self._det_unwatch()
        if d["timer"]:
            GLib.source_remove(d["timer"])
        d["timer"] = GLib.timeout_add_seconds(120, self._det_timeout)
        d["kind"], d["state"], d["cur"] = kind, "listen", None
        for fd, (it, k) in d["fds"].items():
            if k == kind:
                drain(fd)
                d["ids"].append(GLib.io_add_watch(fd, GLib.IO_IN, self._det_io))
        d["armed"] = time.time() + 0.8     # ignore the click that started this step
        self.det_show()

    def _det_io(self, fd, _cond):
        d = self.det
        if not d or d["state"] != "listen":
            return False
        try:
            data = os.read(fd, 4096)
        except OSError:
            return True
        if time.time() < d["armed"] or not has_press(data):
            return True
        d["cur"] = d["fds"][fd][0]
        d["state"] = "confirm"
        self._det_unwatch()
        self.det_show()
        return False

    def _det_timeout(self):
        if self.det:
            self.det["timer"] = None
            self.det_stop("Detection stopped (nothing pressed for 2 minutes).")
        return False

    def det_show(self):
        d = self.det
        name = seat_name(d["seat"])
        kb = d["kind"] == "keyboard"
        word = "keyboard" if kb else "mouse"
        state = d["state"]
        found = ""
        if state == "listen":
            n_fds = sum(1 for (_it, k) in d["fds"].values() if k == d["kind"])
            title = f"DETECT FOR {name.upper()} · STEP {1 if kb else 2} OF 2"
            if n_fds:
                msg = (f"Press any key on the keyboard that belongs to {name}." if kb
                       else f"Click any button on the mouse that belongs to {name}.")
            else:
                msg = f"No {word} found to listen on. You can skip this step."
            btns = {"skip": "Skip", "cancel": "Cancel"}
        elif state == "confirm":
            it = d["cur"]
            title = f"DETECT FOR {name.upper()} · STEP {1 if kb else 2} OF 2"
            msg = f"Found a {word}. Is this the right one?"
            found = it["title"] + (f"  ·  USB port {it['port']}" if it["port"] else "")
            btns = {"ok": "Yes, use it", "retry": "Try again", "skip": "Skip", "cancel": "Cancel"}
        else:  # "ask": keyboard step is done, offer the mouse
            got = d["chosen"].get("keyboard")
            title = "KEYBOARD DONE" if got else "KEYBOARD SKIPPED"
            msg = f"Detect the mouse for {name} too?"
            found = ("Keyboard: " + got["title"]) if got else ""
            btns = {"ok": "Detect mouse", "skip": "No, finish", "cancel": "Cancel"}
        self.det_title.set_text(title)
        self.det_msg.set_text(msg)
        self.det_found.set_text(found)
        self.det_found.set_visible(bool(found))
        for key, w in (("ok", self.det_ok), ("retry", self.det_retry), ("skip", self.det_skip),
                       ("cancel", self.det_cancel)):
            w.set_visible(key in btns)
            if key in btns:
                w.set_label(btns[key])
        listening = state == "listen"
        self.det_spin.set_visible(listening)
        if listening:
            self.det_spin.start()
        else:
            self.det_spin.stop()

    def det_action(self, act):
        d = self.det
        if not d:
            return
        state, kind = d["state"], d["kind"]
        if act == "cancel":
            self.det_stop("Detection cancelled.")
        elif act == "retry":
            self.det_listen(kind)
        elif act == "ok":
            if state == "confirm":
                d["chosen"][kind] = d["cur"]
                self._det_after(kind)
            elif state == "ask":
                self.det_listen("mouse")
        elif act == "skip":
            if state == "ask":
                self.det_finish()
            else:
                self._det_after(kind)

    def _det_after(self, kind):
        self._det_unwatch()
        if kind == "keyboard":
            self.det["state"] = "ask"
            self.det_show()
        else:
            self.det_finish()

    def det_finish(self):
        d = self.det
        seat = d["seat"]
        chosen = list({it["id"]: it for it in d["chosen"].values()}.values())
        self.det_stop()
        if not chosen:
            self.notify("No devices selected.")
            return
        for it in chosen:
            it["seat"] = seat
        self.render()
        names = " and ".join(it["title"] for it in chosen)
        self.notify(f"Moved {names} to {seat_name(seat)}. Detection paused.")

    def det_stop(self, msg=None):
        d = self.det
        if not d:
            return
        self._det_unwatch()
        if d["timer"]:
            GLib.source_remove(d["timer"])
        for fd in d["fds"]:
            try:
                os.close(fd)
            except OSError:
                pass
        self.det = None
        self.det_spin.stop()
        self.det_rev.set_reveal_child(False)
        if msg:
            self.notify(msg)

    # ---------------------------------------------------- apply
    def on_apply(self):
        warns = check(self.items)
        if warns and not self.confirm("Apply with warnings?", "\n".join(warns)):
            return
        if not DEMO and run("pgrep", "-f", "astermultiseat") == 0 and not self.confirm(
                "ASTER is running", "It may override this setup. Continue anyway?"):
            return
        plan = {}
        for i in self.items:
            if i["seat"] != MAIN and i["paths"]:
                plan.setdefault(i["seat"], []).extend(i["paths"])
        self.det_stop()
        self.apply_btn.set_sensitive(False)
        self.apply_btn.set_label("Applying…")
        if not self.bg(lambda: apply_plan(plan) if plan else (undo_all() or []), self.after_apply):
            self.update_bar()

    def after_apply(self, res):
        if isinstance(res, Exception):
            self.error("Could not apply the setup", str(res))
            self.update_bar()
            return
        if res:
            self.error("Some devices could not be assigned", "\n".join(res))
        else:
            self.notify("Applied. Log in with a different user on each seat.")
            if self.confirm("Restart the login screen now?",
                            "Needed if a seat stays blank. This logs out everyone on every seat."):
                self.restart_dm()
                return
        self.refresh()

    # ---------------------------------------------------- tools
    def restart_dm(self):
        dm = detect_dm()
        if DEMO:
            self.notify("Demo: the login screen would restart now.")
        elif dm:
            run("systemctl", "restart", dm)
        else:
            self.notify("No login manager found.", True)

    def tool_restart_dm(self):
        if self.confirm("Restart the login screen?", "This closes this app and logs out everyone on every seat."):
            self.restart_dm()

    def tool_undo(self):
        if not self.confirm("Remove all seat assignments?", "Every device goes back to Seat 1."):
            return
        self.bg(undo_all, lambda r: (
            self.notify(f"Failed: {r}", True) if isinstance(r, Exception) else self.notify("Extra seats removed."),
            self.refresh()))

    def tool_greeter(self):
        def done(r):
            if isinstance(r, Exception):
                self.notify(f"Failed: {r}", True)
            else:
                self.notify(r[1], not r[0])
        self.bg(fix_greeter, done)


# ============================================================ entry
def elevate():
    """Re-launch as root through pkexec (or sudo), keeping the display variables."""
    keep = ["DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"]
    env = [f"{k}={os.environ[k]}" for k in keep if k in os.environ]
    if "XAUTHORITY" not in os.environ:
        env.append("XAUTHORITY=" + os.path.expanduser("~/.Xauthority"))
    env.append("NO_AT_BRIDGE=1")
    me = [sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
    env_bin = shutil.which("env") or "/usr/bin/env"
    pk = shutil.which("pkexec")
    if pk:
        os.execv(pk, [pk, env_bin] + env + me)
    sd = shutil.which("sudo")
    if sd:
        os.execv(sd, [sd, "-E", env_bin] + env + me)
    print("Administrator access is needed. Install polkit (pkexec) or run: sudo -E python3 " + me[1])
    sys.exit(1)


def main():
    if not DEMO and os.geteuid() != 0:
        elevate()
    GLib.set_prgname(APP_ID)
    GLib.set_application_name(APP_NAME)
    Gtk.Window.set_default_icon_name(APP_ID)
    App()
    Gtk.main()


if __name__ == "__main__":
    main()
