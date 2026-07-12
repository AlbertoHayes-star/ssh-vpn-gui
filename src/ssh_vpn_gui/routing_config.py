from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
from pathlib import Path
from typing import Iterable, Literal

from .paths import SYSTEM_ROUTING_FILE

Action = Literal["direct", "proxy"]
RuleKind = Literal["domain", "ip"]

DEFAULT_ROUTING_FILE = SYSTEM_ROUTING_FILE


class RoutingConfigError(ValueError):
    """Raised when routing.cfg contains an unsupported or malformed rule."""


@dataclass(frozen=True)
class RoutingMatcher:
    name: str
    value: str


@dataclass(frozen=True)
class RoutingRule:
    kind: RuleKind
    action: Action
    matchers: tuple[RoutingMatcher, ...]
    line_number: int


@dataclass(frozen=True)
class RoutingConfig:
    default: Action = "proxy"
    rules: tuple[RoutingRule, ...] = field(default_factory=tuple)


_DEFAULT_RE = re.compile(r"^default\s*:\s*(direct|proxy)\s*$")
_RULE_RE = re.compile(r"^(domain|ip)\((.*)\)\s*->\s*(direct|proxy)\s*$")
_MATCHER_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)$")


def parse_routing_file(path: Path | str = DEFAULT_ROUTING_FILE) -> RoutingConfig:
    return parse_routing_text(Path(path).read_text(encoding="utf-8"))


def parse_routing_text(text: str) -> RoutingConfig:
    default: Action = "proxy"
    rules: list[RoutingRule] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue

        default_match = _DEFAULT_RE.match(line)
        if default_match:
            default = _as_action(default_match.group(1))
            continue

        rule_match = _RULE_RE.match(line)
        if not rule_match:
            raise RoutingConfigError(f"Line {line_number}: malformed rule: {raw_line!r}")

        kind = _as_kind(rule_match.group(1))
        body = rule_match.group(2).strip()
        action = _as_action(rule_match.group(3))
        matchers = tuple(_parse_matcher(part, kind, line_number) for part in _split_csv(body))
        if not matchers:
            raise RoutingConfigError(f"Line {line_number}: rule has no matchers")

        rules.append(RoutingRule(kind=kind, action=action, matchers=matchers, line_number=line_number))

    return RoutingConfig(default=default, rules=tuple(rules))


def _parse_matcher(part: str, kind: RuleKind, line_number: int) -> RoutingMatcher:
    match = _MATCHER_RE.match(part.strip())
    if match:
        name = match.group(1)
        value = _unquote(match.group(2).strip())
    else:
        name = "ip_cidr" if kind == "ip" else "domain"
        value = _unquote(part.strip())

    if kind == "domain" and name not in {"domain", "regexp", "geosite"}:
        raise RoutingConfigError(f"Line {line_number}: unsupported domain matcher {name!r}")
    if kind == "ip" and name not in {"ip", "ip_cidr", "geoip"}:
        raise RoutingConfigError(f"Line {line_number}: unsupported ip matcher {name!r}")

    if kind == "ip" and name in {"ip", "ip_cidr"}:
        value = _normalize_ip_cidr(value, line_number)
        name = "ip_cidr"

    return RoutingMatcher(name=name, value=value)


def _split_csv(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for char in body:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote is not None:
            current.append(char)
            escaped = True
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = None if quote == char else char if quote is None else quote
            continue
        if char == "," and quote is None:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)

    if quote is not None:
        raise RoutingConfigError("Unclosed quote in routing rule")

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def _strip_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        elif char == "#" and quote is None:
            return line[:index]
    return line


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _normalize_ip_cidr(value: str, line_number: int) -> str:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise RoutingConfigError(f"Line {line_number}: invalid IP/CIDR {value!r}") from exc
    return str(network)


def _as_action(value: str) -> Action:
    if value not in {"direct", "proxy"}:
        raise RoutingConfigError(f"Unsupported action {value!r}")
    return value  # type: ignore[return-value]


def _as_kind(value: str) -> RuleKind:
    if value not in {"domain", "ip"}:
        raise RoutingConfigError(f"Unsupported rule kind {value!r}")
    return value  # type: ignore[return-value]


def group_matchers_by_action(config: RoutingConfig, action: Action) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for rule in config.rules:
        if rule.action != action:
            continue
        for matcher in rule.matchers:
            grouped.setdefault(matcher.name, []).append(matcher.value)
    return grouped


def iter_actions(config: RoutingConfig) -> Iterable[Action]:
    yield "direct"
    yield "proxy"
