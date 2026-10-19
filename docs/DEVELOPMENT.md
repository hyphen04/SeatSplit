# Developer Reference — SeatSplit / SeatSplit

This document explains the internal design of `seatsplit.py` for
developers who want to understand, extend, or debug the codebase.

---

## Architecture overview

The entire application is a **single Python file** (`seatsplit.py`).
It is split into four logical sections separated by `# =====` banners:

```
seatsplit.py
├── Backend          (hardware scanning, udev/loginctl wrappers)
├── Model            (item/seat data model, plan generation, validation)
├── Look             (CSS theme constants, GTK widget helpers)
└── App              (Gtk.Window subclass — the full UI)
```

No third-party PyPI packages are used. The only runtime dependencies are
`python3-gi` (GTK 3 bindings) and `polkit` (`pkexec`).

---

## Key concepts

### Items

An *item* is a movable device card. Each item is a plain `dict` with these
fields:

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique ID: `gpu:<name>`, `usb:<ctrl>:<port>`, or `int:<syspath>` |
| `kind` | `str` | `"gpu"` or `"input"` |
| `badges` | `list[str]` | Labels shown on the card: `GPU`, `KEYBOARD`, `MOUSE` |
| `title` | `str` | Human-readable device name |
| `paths` | `list[str]` | sysfs paths passed to `loginctl attach` |
| `kb_events` | `list[str]` | `/dev/input/eventN` paths for keyboard detection |
| `ms_events` | `list[str]` | `/dev/input/eventN` paths for mouse detection |
| `seat` | `str` | Current (draft) seat assignment |
| `orig` | `str` | Seat assignment loaded from udev (before any user edits) |
| `locked` | `bool` | Built-in device — cannot be moved |
| `boot` | `bool` | Primary GPU (used at boot) — moving it shows a warning |

### Seats

Seats are identified by their `systemd-logind` name: `seat0`, `seat1`, …
`seat7` (maximum 8). `seat0` is always shown as **Seat 1**.

`App.seats` is the live sorted list of visible seat IDs.
`App.extra` is a `set` of seats the user manually added but that have no
devices yet.

---

## Data flow

```
Hardware (udev / sysfs)
        │
        ▼  scan_gpus() + scan_inputs()
     raw dicts
        │
        ▼  build_items()
     items list  ←──── user drags / moves
        │
        ▼  check()          → warning strings
        │
        ▼  on_apply() → apply_plan() → loginctl attach → udevadm trigger
```

### Scanning

- `scan_gpus()` — walks `/sys/class/drm/card*`, reads PCI ID, driver, EDID
  (for monitor names), and current `ID_SEAT` udev property.
- `scan_inputs()` — walks `/sys/class/input/input*`, reads `ID_INPUT_KEYBOARD`
  / `ID_INPUT_MOUSE` udev properties. Groups devices by USB controller + port
  number so that a keyboard+mouse hub appears as one card.
- `demo_scan()` — returns hard-coded fake data, used when `--demo` is passed.

### Applying a plan

`apply_plan(plan)` receives `{seat_id: [sysfs_paths]}` and calls
`loginctl attach <seat> <path>` for each path, then triggers a `udevadm`
settle + 3-second sleep to let the display stack catch up.

`undo_all()` calls `loginctl flush-devices` to reset everything to `seat0`.

---

## UI structure

```
App (Gtk.Window)
├── HeaderBar
│   ├── "Suggest setup" button
│   ├── Tools ▾ menu (fix greeter, restart DM, undo all)
│   ├── Theme toggle button
│   └── Refresh button
└── Overlay
    ├── Main VBox
    │   ├── Hint label + "Add seat" button
    │   ├── HBox of Column widgets (one per seat, inside a ScrolledWindow)
    │   └── Revealer → bottom bar (pending changes, Apply/Reset)
    ├── Overlay: Revealer → detection panel (guided device detection)
    └── Overlay: Revealer → toast notification
```

### Column

Each `Column` represents one seat. It owns:
- A header row (seat name, status dot, summary, action buttons).
- A scrollable list of `make_chip()` widget trees.
- Drag-and-drop target (`drag_dest_set`).

### Chip (device card)

`make_chip(app, it)` builds the GTK widget tree for one item. If the item is
not locked, it attaches a drag source and a `move_control` widget (button for
2-seat setups, dropdown menu for 3+).

---

## Guided device detection

The detection flow runs entirely inside `App` using async GLib I/O watches:

1. `start_detect(seat)` — opens all known `/dev/input/eventN` fds in non-blocking
   mode and registers `GLib.io_add_watch` callbacks.
2. `_det_io()` — fires when a file descriptor has data. Parses raw `evdev`
   events using `struct.unpack` and looks for a key-press (`type=1, value=1`).
3. `det_show()` — re-renders the floating panel widget based on the detection
   state machine: `listen → confirm → ask → finish`.
4. `det_finish()` — moves the chosen items to the target seat and re-renders.

---

## Theme system

Two colour palettes are defined as module-level dicts (`LIGHT`, `DARK`).
The CSS template string (`CSS`) uses `string.Template` substitution. Calling
`apply_theme()` loads the substituted CSS into a single `Gtk.CssProvider`
attached to the default screen.

Theme preference is persisted to `~/.config/seatsplit/config.json`.

---

## Background threading

The `App.bg(work, done)` helper runs `work()` in a daemon thread and calls
`done(result)` back on the GTK main thread via `GLib.idle_add`. A `self.busy`
flag prevents overlapping operations.

---

## Adding a new feature — checklist

1. **Backend function** in the *backend* section — keep it pure (no GTK).
2. **Model update** if items/seats data shape changes.
3. **UI wiring** in the `App` class — use `self.bg()` for any blocking I/O.
4. **Test with `--demo`** before touching real hardware.
5. Bump `VER` in `build-deb.sh` and update the changelog in `README.md` if
   this is a release change.

---

## File inventory

| File | Purpose |
|---|---|
| `seatsplit.py` | Entire application (backend + GTK3 UI) |
| `assets/seatsplit.desktop` | XDG desktop entry (app launcher) |
| `assets/seatsplit.svg` | Application icon |
| `packaging/install.sh` | Direct install / uninstall script |
| `packaging/build-deb.sh` | Builds a `.deb` package (accepts optional `VERSION` arg) |
| `.github/workflows/release.yml` | GitHub Actions: auto-build & publish `.deb` on tag push |
| `README.md` | User-facing documentation |
| `docs/CONTRIBUTING.md` | How to contribute |
| `docs/DEVELOPMENT.md` | This file — internal design reference |
| `LICENSE` | MIT licence |
| `.gitignore` | Git ignore rules |
