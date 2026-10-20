# SeatSplit — SeatSplit

> **Split one Linux PC into several independent seats with drag-and-drop.**  
> Each seat gets its own GPU, keyboard, mouse and a separate login session — live, no reboot.

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![GTK](https://img.shields.io/badge/GTK-3-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

---

## What is multiseat?

Multiseat lets multiple users share one physical PC simultaneously. Each user
has their own monitor, keyboard, and mouse. The OS runs a fully independent
login session for each group of devices. `systemd-logind` handles everything
under the hood — SeatSplit gives you a friendly drag-and-drop UI on top of it.

**Tested on:** Linux Mint 21/22 · Ubuntu 22.04 / 24.04 · Debian 12

---

## Features

- 🖥️ **Drag-and-drop** — move GPU, keyboard and mouse cards between seats
- ✦ **Auto-suggest** — one click proposes a sensible split based on your hardware
- 🔍 **Guided device detection** — press a key / click a mouse to identify which
  device belongs to which seat
- ⚡ **Live, no reboot** — changes are applied through `loginctl` + `udevadm`
- ⬇️ **In-app updates** — checks GitHub Releases and, for `.deb` installs, downloads,
  verifies (SHA-256) and installs the new version in one click (Tools → Check for updates)
- 🔄 **Live updates** — hardware and logins are rescanned every 2 seconds
- 🟢 **Seat state** — applied seats are highlighted green, edited seats amber,
  with a per-seat **Reset** button
- 🌙 **Dark & light themes** — toggle in the header bar; choice is remembered
- 🧪 **Demo mode** — try the full UI without any real hardware (`--demo`)
- 📦 **Tiny footprint** — single Python file, zero PyPI dependencies

---

## Prerequisites

SeatSplit uses **GTK 3 via PyGObject** (`python3-gi`). This library is a
**system package** — it cannot be installed with `pip`.

> [!IMPORTANT]
> Install the system packages below before any other step.

### Debian / Ubuntu / Linux Mint

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 policykit-1
```

### Fedora

```bash
sudo dnf install python3-gobject gtk3 polkit
```

### Arch Linux

```bash
sudo pacman -S python-gobject gtk3 polkit
```

Your system must also have **`systemd`** with `loginctl` and **`udev`** — both
standard on any systemd-based distro.

---

## Installation

### Option A — Debian / Ubuntu / Mint .deb  *(easiest)*

Download the latest `.deb` from the [Releases](../../releases) page and install:

```bash
sudo apt install ./seatsplit_1.3.1_all.deb
```

### Option B — Install script  *(from source)*

```bash
git clone https://github.com/hyphen04/SeatSplit.git
cd SeatSplit
sudo ./packaging/install.sh
```

**To remove:**

```bash
sudo ./packaging/install.sh --uninstall
```

### Option C — Run directly  *(no install)*

```bash
git clone https://github.com/hyphen04/SeatSplit.git
cd SeatSplit
sudo python3 seatsplit.py
```

> [!NOTE]
> Root access is required to attach devices to seats (`loginctl attach`).
> The app auto-elevates via `pkexec` (polkit) or `sudo` when not already root.

---

## Usage

### Launch

After installing, search for **"SeatSplit"** in your application menu,
or run from a terminal:

```bash
seatsplit                # normal (auto-elevates via pkexec)
seatsplit --demo         # demo mode — fake hardware, nothing is changed
seatsplit --theme=dark   # start in dark theme
```

### Workflow

1. **Open the app.** Your GPUs, keyboards and mice appear as cards under Seat 1.
2. **Drag** a GPU card, a keyboard card, and a mouse card onto Seat 2.
   - Or click **✦ Suggest setup** to let the app pick automatically.
   - Or click **Detect devices** and press the physical key / button to identify it.
3. **Check** the warning bar at the bottom — it flags missing devices per seat.
4. **Press Apply** — changes go live immediately (no reboot).
5. A login screen appears on Seat 2's monitor. Log in as a **different user**.

### Tools menu

| Action | What it does |
|---|---|
| Fix blank login screen (lightdm) | Installs `lightdm-gtk-greeter` and writes per-seat config |
| Restart login screen | Restarts the active display manager (logs everyone out) |
| Remove all seat assignments | Resets all devices back to Seat 1 via `loginctl flush-devices` |

---

## Hardware requirements

| | Seat 1 | Extra seats |
|---|---|---|
| GPU | 1 (any) | 1 separate card per seat |
| Monitor | 1 | 1 per seat |
| Keyboard | 1 | 1 USB keyboard per seat |
| Mouse | 1 | 1 USB mouse per seat |

- Any cheap GPU works for extra seats: GT 710, RX 550, USB display adapter, etc.
- Keyboards/mice must be **USB** — PS/2 devices are locked to Seat 1.
- Keep the primary (boot) GPU on Seat 1; the app warns if you move it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Extra seat shows a blank screen | Use *Tools → Fix blank login screen (lightdm)*, then restart the login screen |
| `ImportError: cannot import name 'Gtk'` | PyGObject system package missing — see [Prerequisites](#prerequisites) |
| Changes not visible after Apply | Click *Refresh*, check the status dot, or use *Tools → Restart login screen* |
| "Administrator access needed" error | Check `systemctl status polkit`; try `sudo python3 seatsplit.py` |

---

## Versioning & Releases

SeatSplit follows **Semantic Versioning** (`MAJOR.MINOR.PATCH`):

- `PATCH` — bug fixes, no API/behaviour change
- `MINOR` — new features, backward compatible
- `MAJOR` — breaking changes

**To create a release:**

```bash
git tag v1.2.0
git push origin v1.2.0
```

GitHub Actions will automatically build the `.deb` and publish a GitHub Release.
See [`.github/workflows/release.yml`](.github/workflows/release.yml).

---

## Project structure

```
SeatSplit/
├── seatsplit.py          # Entire application (backend + GTK3 UI)
├── assets/
│   ├── seatsplit.desktop # XDG desktop entry (app launcher)
│   └── seatsplit.svg     # Application icon
├── packaging/
│   ├── install.sh                # Direct install / uninstall script
│   └── build-deb.sh              # Builds a .deb package
├── docs/
│   ├── DEVELOPMENT.md            # Internal design reference for developers
│   └── CONTRIBUTING.md           # How to contribute
├── .github/
│   └── workflows/
│       └── release.yml           # Auto-build & publish .deb on git tag push
├── README.md
└── LICENSE                       # MIT
```

---

## Contributing

Contributions are welcome! Please read [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) first.

Quick start — no installation needed:

```bash
python3 seatsplit.py --demo
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full internal architecture
reference, data model, and guide on adding new features.

---

## Author

Kunj Patel — [kunj.dev](https://www.kunj.dev/) · [GitHub](https://github.com/hyphen04)

---

## License

[MIT](LICENSE) — free to use, modify, and distribute.

