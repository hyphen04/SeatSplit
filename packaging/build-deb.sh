#!/bin/sh
# Build multiseat-manager_<VERSION>_all.deb
#
# Usage:
#   cd <repo-root>
#   ./packaging/build-deb.sh [VERSION]
#
# If VERSION is not given, it is read from the VER variable below.
# The .deb is written to the repo root.
#
# Install the result with:
#   sudo apt install ./multiseat-manager_1.1.0_all.deb
set -e

# Resolve repo root (one level above this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VER="${1:-1.1.0}"
TMP="$(mktemp -d)"
PKG="$TMP/multiseat-manager_${VER}_all"

echo "Building multiseat-manager version $VER ..."

# --- Install application files ---
install -D -m 755 "$ROOT_DIR/multiseat_manager.py" \
    "$PKG/opt/multiseat-manager/multiseat_manager.py"
install -d "$PKG/usr/bin"
ln -s /opt/multiseat-manager/multiseat_manager.py "$PKG/usr/bin/multiseat-manager"

# --- Install desktop integration ---
install -D -m 644 "$ROOT_DIR/assets/multiseat-manager.desktop" \
    "$PKG/usr/share/applications/multiseat-manager.desktop"
install -D -m 644 "$ROOT_DIR/assets/multiseat-manager.svg" \
    "$PKG/usr/share/icons/hicolor/scalable/apps/multiseat-manager.svg"

# --- Debian control files ---
install -d "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: multiseat-manager
Version: $VER
Section: utils
Priority: optional
Architecture: all
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, policykit-1, systemd, udev
Maintainer: Multiseat Manager <noreply@example.com>
Description: Split one PC into several seats with drag and drop
 Drag a graphics card, keyboard and mouse onto a second seat and apply it
 live through systemd-logind. Light and dark themes, tiny memory footprint.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications || true
command -v gtk-update-icon-cache   >/dev/null 2>&1 && gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"

# --- Build ---
OUT="$ROOT_DIR/multiseat-manager_${VER}_all.deb"
dpkg-deb --root-owner-group --build "$PKG" "$OUT"
rm -rf "$TMP"
echo "Built: $OUT"
echo "Install with: sudo apt install $OUT"
