#!/usr/bin/env sh
set -eu

SUDOERS_FILE=/etc/sudoers.d/ssh-vpn-gui
HELPER="/usr/bin/ssh-vpn-helper"
CURRENT_USER="$(id -un)"

tmp_file="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s\n' "$CURRENT_USER" "$HELPER" > "$tmp_file"

sudo install -m 0440 "$tmp_file" "$SUDOERS_FILE"
rm -f "$tmp_file"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

echo "Installed sudoers rule for passwordless SSH VPN GUI helper."
