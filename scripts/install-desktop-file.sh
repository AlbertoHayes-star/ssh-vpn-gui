#!/usr/bin/env sh
set -eu

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ICON_FILE="$SOURCE_DIR/assets/dev.cursor.SshVpnGui.png"
TARGET_FILE="$APP_DIR/dev.cursor.SshVpnGui.desktop"

mkdir -p "$APP_DIR"
mkdir -p "$ICON_DIR"
cat > "$TARGET_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=SSH VPN GUI
Comment=Manage SSH TUN VPN routing
Exec=ssh-vpn-gui
Icon=dev.cursor.SshVpnGui
Terminal=false
Categories=Network;GTK;
StartupNotify=true
StartupWMClass=dev.cursor.SshVpnGui
EOF
cp "$ICON_FILE" "$ICON_DIR/dev.cursor.SshVpnGui.png"
chmod 0644 "$TARGET_FILE"
chmod 0644 "$ICON_DIR/dev.cursor.SshVpnGui.png"
rm -f "$APP_DIR/ssh-vpn-gui.desktop"

if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$TARGET_FILE"
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi

echo "Installed $TARGET_FILE"
