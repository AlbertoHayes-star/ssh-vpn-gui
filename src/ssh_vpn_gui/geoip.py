from __future__ import annotations

import ipaddress
from pathlib import Path
import urllib.request

from .system import DATA_DIR

GEOIP_DB = DATA_DIR / "ip66.mmdb"
GEOIP_URL = "https://downloads.ip66.dev/db/ip66.mmdb"
DOWNLOAD_TIMEOUT_SECONDS = 45

PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


class GeoIpStore:
    def __init__(self, path: Path = GEOIP_DB) -> None:
        self.path = path
        self._reader = None
        self._disabled_reason: str | None = None

    def country_code(self, address: str) -> str | None:
        ip = ipaddress.ip_address(address)
        if any(ip in network for network in PRIVATE_NETWORKS):
            return "private"
        if self._disabled_reason:
            return None
        if not self.path.exists():
            return None
        reader = self._open_reader()
        record = reader.get(address) if reader else None
        if not record:
            return None
        country = record.get("country") or {}
        return (country.get("iso_code") or country.get("code") or "").lower() or None

    def matches(self, address: str, tags: list[str]) -> bool:
        code = self.country_code(address)
        return code is not None and code.lower() in {tag.lower() for tag in tags}

    def _open_reader(self):
        if self._reader is not None:
            return self._reader
        try:
            import maxminddb  # type: ignore
        except ImportError as exc:
            self._disabled_reason = "GeoIP MMDB lookup requires python package 'maxminddb'"
            return None
        self._reader = maxminddb.open_database(str(self.path))
        return self._reader


def update_geoip(*, dry_run: bool = False) -> list[str]:
    if dry_run:
        return [f"download {GEOIP_URL} -> {GEOIP_DB}"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = GEOIP_DB.with_suffix(".mmdb.tmp")
    _download_file(GEOIP_URL, temporary)
    temporary.replace(GEOIP_DB)
    return [f"updated {GEOIP_DB}"]


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
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed: {url}: {exc}") from exc
