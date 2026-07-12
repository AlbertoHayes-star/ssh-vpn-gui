#!/usr/bin/env sh
set -eu

sudo rm -f /usr/share/polkit-1/actions/com.ssh-vpn-gui.helper.policy
sudo rm -f /etc/polkit-1/rules.d/49-ssh-vpn-gui.rules

echo "Removed SSH VPN GUI Polkit rule."
