from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import subprocess

STATE_DIR = Path("/run/ssh-vpn-gui")
DATA_DIR = Path("/var/lib/ssh-vpn-gui")
KNOWN_HOSTS = DATA_DIR / "known_hosts"
LOCAL_TUN = "tun3"
LOCAL_TUN_ADDRESS = "10.255.3.1"
REMOTE_TUN_ADDRESS = "10.255.3.2"
TUN_CIDR = "10.255.3.0/30"
PROXY_MARK = "2023"
PROXY_TABLE = "2023"
OLD_FORCED_ROUTES = ("0.0.0.0/1", "128.0.0.0/1")
SINGBOX_PID = STATE_DIR / "sing-box.pid"


@dataclass
class CommandRunner:
    dry_run: bool = False
    commands: list[str] = field(default_factory=list)

    def run(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str] | None:
        self.commands.append(" ".join(command))
        if self.dry_run:
            return None
        completed = subprocess.run(command, check=False, text=True, capture_output=True)
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"{' '.join(command)} failed: {detail or completed.returncode}")
        return completed

    def run_shell(self, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str] | None:
        self.commands.append(command)
        if self.dry_run:
            return None
        completed = subprocess.run(["sh", "-c", command], check=False, text=True, capture_output=True)
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"{command} failed: {detail or completed.returncode}")
        return completed

    def write_file(self, path: Path, content: str) -> None:
        self.commands.append(f"write {path}")
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("This command must run as root. Start it through pkexec or sudo.")


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def setup_local_tun(runner: CommandRunner) -> None:
    if not runner.dry_run:
        ensure_state_dir()
    runner.run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    runner.run(["ip", "addr", "flush", "dev", LOCAL_TUN], check=False)
    runner.run(
        [
            "ip",
            "addr",
            "add",
            LOCAL_TUN_ADDRESS,
            "peer",
            REMOTE_TUN_ADDRESS,
            "dev",
            LOCAL_TUN,
        ],
        check=False,
    )
    runner.run(["ip", "link", "set", LOCAL_TUN, "up"])


def setup_proxy_routes(runner: CommandRunner) -> None:
    cleanup_proxy_routes(runner)
    runner.run(
        [
            "ip",
            "route",
            "replace",
            "default",
            "dev",
            LOCAL_TUN,
            "src",
            LOCAL_TUN_ADDRESS,
            "table",
            PROXY_TABLE,
        ]
    )
    runner.run(["ip", "rule", "add", "fwmark", PROXY_MARK, "table", PROXY_TABLE, "priority", PROXY_TABLE])


def cleanup_proxy_routes(runner: CommandRunner) -> None:
    runner.run(["ip", "rule", "del", "fwmark", PROXY_MARK, "table", PROXY_TABLE], check=False)
    runner.run(["ip", "route", "flush", "table", PROXY_TABLE], check=False)
    for route in OLD_FORCED_ROUTES:
        runner.run(["ip", "route", "del", route], check=False)
    cleanup_tun_routes(runner)


def setup_full_tunnel_routes(runner: CommandRunner, *, cleanup_first: bool = True) -> None:
    if cleanup_first:
        cleanup_proxy_routes(runner)
    for route in OLD_FORCED_ROUTES:
        runner.run(["ip", "route", "replace", route, "dev", LOCAL_TUN, "src", LOCAL_TUN_ADDRESS])


def preserve_server_route(runner: CommandRunner, server: str) -> None:
    if runner.dry_run:
        runner.commands.append(f"ip route get {server}")
        runner.commands.append(f"ip route replace {server}/32 dev <current-default-interface>")
        return

    route = runner.run(["ip", "route", "get", server])
    if route is None:
        return
    route_words = route.stdout.split()
    command = ["ip", "route", "replace", f"{server}/32"]
    if "via" in route_words:
        command.extend(["via", route_words[route_words.index("via") + 1]])
    if "dev" in route_words:
        command.extend(["dev", route_words[route_words.index("dev") + 1]])
    runner.run(command)


def cleanup_local_tun(runner: CommandRunner, server: str | None = None) -> None:
    if server:
        runner.run(["ip", "route", "del", f"{server}/32"], check=False)
    cleanup_tun_routes(runner)
    runner.run(["ip", "addr", "flush", "dev", LOCAL_TUN], check=False)
    runner.run(["ip", "link", "set", LOCAL_TUN, "down"], check=False)


def cleanup_tun_routes(runner: CommandRunner) -> None:
    if runner.dry_run:
        runner.commands.append(f"delete routes using {LOCAL_TUN}")
        return
    routes = runner.run(["ip", "-4", "route", "show", "dev", LOCAL_TUN], check=False)
    if routes is None:
        return
    for line in routes.stdout.splitlines():
        words = line.split()
        if words:
            runner.run(["ip", "route", "del", words[0]], check=False)


def cleanup_legacy_singbox(runner: CommandRunner) -> None:
    if SINGBOX_PID.exists() and not runner.dry_run:
        pid = SINGBOX_PID.read_text(encoding="utf-8").strip()
        runner.run(["kill", "-TERM", pid], check=False)
        SINGBOX_PID.unlink(missing_ok=True)
    runner.run(["pkill", "-f", "sing-box run -c /run/ssh-vpn-gui/sing-box.json"], check=False)


def remote_bootstrap_script() -> str:
    return """
set -eu
changed=0

if command -v modprobe >/dev/null 2>&1; then
  modprobe tun 2>/dev/null || true
fi
if [ ! -c /dev/net/tun ]; then
  mkdir -p /dev/net
  mknod /dev/net/tun c 10 200 2>/dev/null || true
  chmod 666 /dev/net/tun 2>/dev/null || true
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null
if [ -w /etc/sysctl.conf ] && ! grep -Eq '^net[.]ipv4[.]ip_forward[[:space:]]*=[[:space:]]*1' /etc/sysctl.conf; then
  printf '\\n# Managed by ssh-vpn-gui\\nnet.ipv4.ip_forward=1\\n' >> /etc/sysctl.conf
fi

sshd_config_dir=/etc/ssh/sshd_config.d
sshd_config_file=$sshd_config_dir/99-ssh-vpn-gui.conf
if [ -d "$sshd_config_dir" ]; then
  desired='PermitTunnel point-to-point
PermitRootLogin yes
PasswordAuthentication yes
'
  if ! printf '%s' "$desired" | cmp -s "$sshd_config_file" -; then
    printf '%s' "$desired" > "$sshd_config_file"
    changed=1
  fi
elif ! grep -Eq '^[[:space:]]*PermitTunnel[[:space:]]+(yes|point-to-point)' /etc/ssh/sshd_config; then
  printf '\\n# Managed by ssh-vpn-gui\\nPermitTunnel point-to-point\\nPermitRootLogin yes\\nPasswordAuthentication yes\\n' >> /etc/ssh/sshd_config
  changed=1
fi

if [ "$changed" -eq 1 ]; then
  sshd -t
  if command -v systemctl >/dev/null 2>&1; then
    systemctl reload sshd 2>/dev/null || systemctl reload ssh
  else
    service ssh reload 2>/dev/null || service sshd reload
  fi
fi

sshd -T 2>/dev/null | grep -qi '^permittunnel \\(yes\\|point-to-point\\)' || {
  echo 'PermitTunnel is still disabled after bootstrap' >&2
  exit 1
}
echo SSH_VPN_BOOTSTRAP_READY
""".strip()


def remote_setup_script() -> str:
    return f"""
set -eu
ip addr flush dev {LOCAL_TUN} || true
ip addr add {REMOTE_TUN_ADDRESS} peer {LOCAL_TUN_ADDRESS} dev {LOCAL_TUN} || true
ip link set {LOCAL_TUN} up
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv4.conf.{LOCAL_TUN}.rp_filter=0 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1 || true
if command -v nft >/dev/null 2>&1; then
  nft add table ip ssh_vpn_gui 2>/dev/null || true
  nft 'add chain ip ssh_vpn_gui postrouting {{ type nat hook postrouting priority srcnat; policy accept; }}' 2>/dev/null || true
  nft add rule ip ssh_vpn_gui postrouting ip saddr {TUN_CIDR} oifname != "{LOCAL_TUN}" masquerade 2>/dev/null || true
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -t nat -C POSTROUTING -s {TUN_CIDR} ! -o {LOCAL_TUN} -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -s {TUN_CIDR} ! -o {LOCAL_TUN} -j MASQUERADE
fi
echo SSH_VPN_READY
trap 'nft delete table ip ssh_vpn_gui 2>/dev/null || true; iptables -t nat -D POSTROUTING -s {TUN_CIDR} ! -o {LOCAL_TUN} -j MASQUERADE 2>/dev/null || true; ip addr flush dev {LOCAL_TUN} 2>/dev/null || true' EXIT
while true; do sleep 3600; done
""".strip()


def remote_cleanup_command() -> str:
    return (
        f"nft delete table ip ssh_vpn_gui 2>/dev/null || true; "
        f"ip addr flush dev {LOCAL_TUN} 2>/dev/null || true; "
        f"ip link set {LOCAL_TUN} down 2>/dev/null || true"
    )
