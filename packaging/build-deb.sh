#!/bin/sh
# Build seatsplit_<VERSION>_all.deb
#
# Usage:
#   cd <repo-root>
#   ./packaging/build-deb.sh [VERSION]
#
# If VERSION is not given, it defaults to the value of VER below.
# The .deb is written to the repo root.
#
# Install the result with:
#   sudo apt install ./seatsplit_1.3.1_all.deb
set -e

# Resolve repo root (one level above this script)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VER="${1:-1.3.1}"
TMP="$(mktemp -d)"
PKG="$TMP/seatsplit_${VER}_all"

echo "Building seatsplit version $VER ..."

# --- Install application files ---
install -D -m 755 "$ROOT_DIR/seatsplit.py" \
    "$PKG/opt/seatsplit/seatsplit.py"
# Stamp the release version into the app so its update check knows what it is
sed -i "s/^VERSION = .*/VERSION = \"$VER\"/" "$PKG/opt/seatsplit/seatsplit.py"
install -d "$PKG/usr/bin"
ln -s /opt/seatsplit/seatsplit.py "$PKG/usr/bin/seatsplit"

# --- Install desktop integration ---
install -D -m 644 "$ROOT_DIR/assets/seatsplit.desktop" \
    "$PKG/usr/share/applications/seatsplit.desktop"
install -D -m 644 "$ROOT_DIR/assets/seatsplit.svg" \
    "$PKG/usr/share/icons/hicolor/scalable/apps/seatsplit.svg"

# --- Copyright / credits ---
install -d "$PKG/usr/share/doc/seatsplit"
cat > "$PKG/usr/share/doc/seatsplit/copyright" <<EOF
Copyright (c) $(date +%Y) Kunj Patel <kunj2604@gmail.com>
Website: https://www.kunj.dev/
GitHub:  https://github.com/hyphen04
EOF
chmod 644 "$PKG/usr/share/doc/seatsplit/copyright"

# --- Debian control files ---
install -d "$PKG/DEBIAN"
cat > "$PKG/DEBIAN/control" <<EOF
Package: seatsplit
Version: $VER
Section: utils
Priority: optional
Architecture: all
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, policykit-1, systemd, udev
Replaces: multiseat-manager
Conflicts: multiseat-manager
Maintainer: Kunj Patel <kunj2604@gmail.com>
Homepage: https://www.kunj.dev/
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
OUT="$ROOT_DIR/seatsplit_${VER}_all.deb"
dpkg-deb --root-owner-group --build "$PKG" "$OUT"
rm -rf "$TMP"
echo "Built: $OUT"
echo "Install with: sudo apt install $OUT"
