from __future__ import annotations

from dataclasses import dataclass
import json
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request

from .routing_config import RoutingConfig, RoutingRule, parse_routing_file
from .system import PROXY_MARK

STATE_DIR = Path("/run/ssh-vpn-gui")
DATA_DIR = Path("/var/lib/ssh-vpn-gui")
SINGBOX_CONFIG = STATE_DIR / "sing-box.json"
SINGBOX_PID = STATE_DIR / "sing-box.pid"
SINGBOX_LOG = STATE_DIR / "sing-box.log"
SINGBOX_BIN = DATA_DIR / "bin" / "sing-box"
GEOIP_DB = DATA_DIR / "geoip.db"
GEOSITE_DB = DATA_DIR / "geosite.db"
DOWNLOAD_TIMEOUT_SECONDS = 45

GEO_DOWNLOADS = {
    GEOIP_DB: "https://github.com/SagerNet/sing-geoip/releases/latest/download/geoip.db",
    GEOSITE_DB: "https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite.db",
}


@dataclass(frozen=True)
class SingBoxSettings:
    routing_file: Path
    ssh_tun_interface: str = "tun3"
    singbox_tun_interface: str = "sshvpn0"
    singbox_tun_address: str = "172.29.3.1/30"
    dns_address: str = "1.1.1.1"


def build_config(config: RoutingConfig, settings: SingBoxSettings) -> dict:
    rules = []
    rule_sets: dict[str, dict] = {}
    for rule in config.rules:
        route_rule = _rule_to_singbox(rule, rule_sets)
        route_rule["outbound"] = rule.action
        rules.append(route_rule)

    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {
                    "type": "tls",
                    "tag": "default",
                    "server": settings.dns_address,
                    "server_port": 853,
                    "tls": {},
                },
            ],
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "interface_name": settings.singbox_tun_interface,
                "address": [settings.singbox_tun_address],
                "auto_route": True,
                "strict_route": True,
                "stack": "system",
            }
        ],
        "outbounds": [
            {"type": "direct", "tag": "direct"},
            {
                "type": "direct",
                "tag": "proxy",
                "bind_interface": settings.ssh_tun_interface,
                "routing_mark": int(PROXY_MARK),
            },
            {"type": "block", "tag": "block"},
        ],
        "route": {
            "rules": rules,
            "rule_set": list(rule_sets.values()),
            "final": config.default,
            "auto_detect_interface": True,
        },
        "experimental": {"cache_file": {"enabled": True, "path": str(DATA_DIR / "cache.db")}},
    }


def write_config(settings: SingBoxSettings) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    routing = parse_routing_file(settings.routing_file)
    SINGBOX_CONFIG.write_text(json.dumps(build_config(routing, settings), indent=2), encoding="utf-8")
    return SINGBOX_CONFIG


def start_singbox(settings: SingBoxSettings, *, dry_run: bool = False) -> list[str]:
    if dry_run:
        routing = parse_routing_file(settings.routing_file)
        build_config(routing, settings)
        command = [str(SINGBOX_BIN), "run", "-c", str(SINGBOX_CONFIG)]
        return [" ".join(command)]

    binary = find_singbox_binary()
    config_path = write_config(settings)
    command = [binary, "run", "-c", str(config_path)]
    stop_singbox(dry_run=False)
    check = subprocess.run([binary, "check", "-c", str(config_path)], text=True, capture_output=True, check=False)
    if check.returncode != 0:
        raise RuntimeError((check.stderr or check.stdout).strip() or "sing-box config check failed")

    log_file = SINGBOX_LOG.open("ab")
    process = subprocess.Popen(command, stdout=log_file, stderr=log_file)
    time.sleep(0.5)
    if process.poll() is not None:
        log_file.close()
        log_tail = _read_log_tail(SINGBOX_LOG)
        raise RuntimeError(f"sing-box exited immediately: {log_tail}")
    SINGBOX_PID.write_text(str(process.pid), encoding="utf-8")
    return [f"started sing-box pid {process.pid}"]


def stop_singbox(*, dry_run: bool = False) -> list[str]:
    if not SINGBOX_PID.exists():
        return []

    pid = int(SINGBOX_PID.read_text(encoding="utf-8").strip())
    if dry_run:
        return [f"kill {pid}"]

    try:
        Path(f"/proc/{pid}").stat()
    except FileNotFoundError:
        SINGBOX_PID.unlink(missing_ok=True)
        return []

    try:
        Path(f"/proc/{pid}/cmdline").read_bytes()
        subprocess.run(["kill", "-TERM", str(pid)], check=False)
        _wait_for_exit(pid)
    finally:
        SINGBOX_PID.unlink(missing_ok=True)
    return [f"stopped sing-box pid {pid}"]


def update_geo_databases(*, dry_run: bool = False) -> list[str]:
    messages = []
    for destination, url in GEO_DOWNLOADS.items():
        if dry_run:
            messages.append(f"download {url} -> {destination}")
            continue
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        _download_file(url, temporary)
        temporary.replace(destination)
        messages.append(f"updated {destination}")
    return messages


def install_singbox(*, dry_run: bool = False) -> list[str]:
    if dry_run:
        return [f"download latest sing-box linux-{_singbox_arch()} -> {SINGBOX_BIN}"]

    url = _latest_singbox_asset_url()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SINGBOX_BIN.parent.mkdir(parents=True, exist_ok=True)
    archive_path = DATA_DIR / "sing-box.tar.gz"
    extract_dir = DATA_DIR / "sing-box-extract"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    _download_file(url, archive_path)
    with tarfile.open(archive_path, "r:gz") as archive:
        member = next((item for item in archive.getmembers() if item.name.endswith("/sing-box") or item.name == "sing-box"), None)
        if member is None:
            raise RuntimeError("Downloaded sing-box archive does not contain sing-box binary")
        archive.extract(member, extract_dir)

    extracted = next(extract_dir.rglob("sing-box"))
    shutil.copy2(extracted, SINGBOX_BIN)
    SINGBOX_BIN.chmod(0o755)
    shutil.rmtree(extract_dir, ignore_errors=True)
    archive_path.unlink(missing_ok=True)
    return [f"installed {SINGBOX_BIN}"]


def ensure_singbox(*, dry_run: bool = False) -> list[str]:
    try:
        find_singbox_binary()
        return []
    except FileNotFoundError:
        return install_singbox(dry_run=dry_run)


def find_singbox_binary() -> str:
    if SINGBOX_BIN.exists():
        return str(SINGBOX_BIN)
    binary = shutil.which("sing-box")
    if binary:
        return binary
    raise FileNotFoundError(
        "sing-box is not installed. Click 'Update GeoIP / Geosite' to download it, "
        "or run ssh-vpn-helper update-geo as root"
    )


def _rule_to_singbox(rule: RoutingRule, rule_sets: dict[str, dict]) -> dict:
    result: dict[str, list[str]] = {}
    for matcher in rule.matchers:
        if matcher.name in {"geosite", "geoip"}:
            tag = f"{matcher.name}-{matcher.value}"
            result.setdefault("rule_set", []).append(tag)
            rule_sets.setdefault(tag, _remote_rule_set(matcher.name, matcher.value))
            continue

        key = {"domain": "domain", "regexp": "domain_regex", "ip_cidr": "ip_cidr"}[matcher.name]
        result.setdefault(key, []).append(matcher.value)
    return result


def _remote_rule_set(kind: str, value: str) -> dict:
    return {
        "type": "remote",
        "tag": f"{kind}-{value}",
        "format": "binary",
        "url": f"https://raw.githubusercontent.com/SagerNet/sing-{kind}/rule-set/{kind}-{value}.srs",
        "download_detour": "proxy",
    }


def _wait_for_exit(pid: int) -> None:
    for _ in range(30):
        try:
            subprocess.run(["kill", "-0", str(pid)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return
        time.sleep(0.1)
    subprocess.run(["kill", "-KILL", str(pid)], check=False)


def _read_log_tail(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "no sing-box log"
    return data[-limit:].decode("utf-8", errors="replace").strip()


def _latest_singbox_asset_url() -> str:
    arch = _singbox_arch()
    api_url = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"
    request = urllib.request.Request(api_url, headers={"User-Agent": "ssh-vpn-gui"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Could not fetch latest sing-box release: {exc}") from exc

    suffix = f"linux-{arch}.tar.gz"
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(suffix) and "glibc" not in name and "musl" not in name:
            return asset["browser_download_url"]
    raise RuntimeError(f"No sing-box release asset found for {suffix}")


def _singbox_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported architecture for automatic sing-box install: {machine}")


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ssh-vpn-gui"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with destination.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file.write(chunk)
    except (OSError, urllib.error.URLError) as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"Download failed after {DOWNLOAD_TIMEOUT_SECONDS}s timeout or network error: {url}: {exc}"
        ) from exc
