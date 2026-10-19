#!/bin/sh
# Install (or remove) Multiseat Manager directly onto this machine.
#
# Usage:
#   sudo ./packaging/install.sh              # install
#   sudo ./packaging/install.sh --uninstall  # remove
#
# This script must be run from the repo root OR from the packaging/ directory.
set -e

if [ "$(id -u)" != "0" ]; then
    echo "Root access is required. Run:"
    echo "  sudo $0 $*"
    exit 1
fi

# Resolve repo root (one level above this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP=/opt/multiseat-manager

# Helper: refresh desktop + icon caches
refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database /usr/share/applications || true
    command -v gtk-update-icon-cache   >/dev/null 2>&1 \
        && gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
}

# ---- Uninstall ----
if [ "$1" = "--uninstall" ]; then
    rm -rf "$APP"
    rm -f /usr/local/bin/multiseat-manager \
          /usr/share/applications/multiseat-manager.desktop \
          /usr/share/icons/hicolor/scalable/apps/multiseat-manager.svg
    refresh_caches
    echo "Multiseat Manager removed."
    exit 0
fi

# ---- Check Python/GTK dependency ----
python3 - <<'EOF' || {
    echo ""
    echo "ERROR: PyGObject (python3-gi) is not installed."
    echo "Fix with: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 policykit-1"
    exit 1
}
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
EOF

# ---- Install ----
install -d "$APP"
install -m 755 "$ROOT_DIR/multiseat_manager.py" "$APP/multiseat_manager.py"
ln -sf "$APP/multiseat_manager.py" /usr/local/bin/multiseat-manager
install -D -m 644 "$ROOT_DIR/assets/multiseat-manager.desktop" \
    /usr/share/applications/multiseat-manager.desktop
install -D -m 644 "$ROOT_DIR/assets/multiseat-manager.svg" \
    /usr/share/icons/hicolor/scalable/apps/multiseat-manager.svg
refresh_caches

echo ""
echo "Multiseat Manager installed."
echo "Open 'Multiseat Manager' from your app menu, or run: multiseat-manager"
