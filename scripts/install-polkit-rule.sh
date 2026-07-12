#!/usr/bin/env sh
set -eu

HELPER="/usr/bin/ssh-vpn-helper"
CURRENT_USER="$(id -un)"
policy_file="$(mktemp)"
rule_file="$(mktemp)"

cat > "$policy_file" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>SSH VPN GUI</vendor>

  <action id="com.ssh-vpn-gui.helper">
    <description>Run SSH VPN privileged helper</description>
    <message>Authentication is required to manage SSH VPN routing</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">$HELPER</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
EOF

cat > "$rule_file" <<EOF
polkit.addRule(function(action, subject) {
    if (action.id == "com.ssh-vpn-gui.helper" &&
        subject.user == "$CURRENT_USER") {
        return polkit.Result.YES;
    }
});
EOF

sudo mkdir -p /usr/share/polkit-1/actions /etc/polkit-1/rules.d
sudo install -m 0644 "$policy_file" /usr/share/polkit-1/actions/com.ssh-vpn-gui.helper.policy
sudo install -m 0644 "$rule_file" /etc/polkit-1/rules.d/49-ssh-vpn-gui.rules
rm -f "$policy_file" "$rule_file"

if command -v pkaction >/dev/null 2>&1; then
  pkaction --action-id com.ssh-vpn-gui.helper >/dev/null 2>&1 || true
fi

echo "Installed passwordless Polkit rule for SSH VPN GUI helper."
