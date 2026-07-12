from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
import shutil
import urllib.request
import zipfile

from .system import DATA_DIR

GEOSITE_DIR = DATA_DIR / "geosite"
GEOSITE_ARCHIVE = DATA_DIR / "domain-list-community.zip"
GEOSITE_URL = "https://github.com/v2fly/domain-list-community/archive/refs/heads/master.zip"
DOWNLOAD_TIMEOUT_SECONDS = 45


@dataclass
class GeositeStore:
    root: Path = GEOSITE_DIR
    cache: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def match(self, tag: str, domain: str) -> bool:
        tag = tag.lower()
        if tag.startswith("category-scholar"):
            return self.match("google-scholar", domain)
        if tag == "geolocation-!cn":
            return not self.match("cn", domain)

        domain = domain.rstrip(".").lower()
        for kind, value in self.load(tag):
            if kind == "domain" and _domain_suffix_match(domain, value):
                return True
            if kind == "full" and domain == value:
                return True
            if kind == "keyword" and value in domain:
                return True
            if kind == "regexp" and re.search(value, domain):
                return True
        return False

    def load(self, tag: str) -> list[tuple[str, str]]:
        tag = tag.lower()
        if tag in self.cache:
            return self.cache[tag]

        path = self.root / "data" / tag
        entries = _builtin_entries(tag)
        if path.exists():
            entries.extend(_parse_geosite_file(path))
        self.cache[tag] = entries
        return entries


def update_geosite(*, dry_run: bool = False) -> list[str]:
    if dry_run:
        return [f"download {GEOSITE_URL} -> {GEOSITE_DIR}"]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _download_file(GEOSITE_URL, GEOSITE_ARCHIVE)
    temporary = DATA_DIR / "geosite-new"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(GEOSITE_ARCHIVE) as archive:
        archive.extractall(temporary)

    extracted = next(temporary.glob("domain-list-community-*"))
    shutil.rmtree(GEOSITE_DIR, ignore_errors=True)
    shutil.move(str(extracted), GEOSITE_DIR)
    shutil.rmtree(temporary, ignore_errors=True)
    GEOSITE_ARCHIVE.unlink(missing_ok=True)
    return [f"updated {GEOSITE_DIR}"]


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


def _parse_geosite_file(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.split("#", 1)[0].strip().lower()
        if not line or line.startswith("include:"):
            continue
        if ":" in line:
            kind, value = line.split(":", 1)
        else:
            kind, value = "domain", line
        value = value.split("@", 1)[0].strip()
        if value:
            entries.append((kind, value))
    return entries


def _builtin_entries(tag: str) -> list[tuple[str, str]]:
    builtins: dict[str, list[tuple[str, str]]] = {
        "ru": [
            ("regexp", r"(^|[.]).+[.]ru$"),
            ("regexp", r"(^|[.]).+[.]su$"),
            ("regexp", r"(^|[.]).+[.]рф$"),
            ("domain", "2ip.io"),
        ],
        "google": [("domain", "google.com"), ("domain", "googleapis.com"), ("domain", "gstatic.com"), ("domain", "googleusercontent.com")],
        "google-scholar": [("domain", "scholar.google.com"), ("domain", "scholar.googleusercontent.com")],
        "cn": [("regexp", r"(^|[.]).+[.]cn$")],
    }
    return list(builtins.get(tag, []))


def _domain_suffix_match(domain: str, value: str) -> bool:
    value = value.rstrip(".").lower()
    return domain == value or domain.endswith("." + value)
