import pytest

from ssh_vpn_gui.routing_config import RoutingConfigError, parse_routing_text


def test_parse_routing_rules() -> None:
    config = parse_routing_text(
        """
        default: proxy
        # comment
        domain(domain:mail.qq.com)->direct
        ip(217.174.105.72)->direct
        domain(regexp: '(^|[.])yandex[.](com|net)$')->direct
        domain(geosite:geolocation-!cn, geosite:google)->proxy
        ip(geoip:private, geoip:ru)->direct
        """
    )

    assert config.default == "proxy"
    assert len(config.rules) == 5
    assert config.rules[0].matchers[0].name == "domain"
    assert config.rules[0].matchers[0].value == "mail.qq.com"
    assert config.rules[1].matchers[0].name == "ip_cidr"
    assert config.rules[1].matchers[0].value == "217.174.105.72/32"
    assert config.rules[2].matchers[0].name == "regexp"
    assert config.rules[2].matchers[0].value == r"(^|[.])yandex[.](com|net)$"
    assert [matcher.value for matcher in config.rules[3].matchers] == ["geolocation-!cn", "google"]
    assert [matcher.value for matcher in config.rules[4].matchers] == ["private", "ru"]


def test_rejects_bad_ip() -> None:
    with pytest.raises(RoutingConfigError, match="invalid IP/CIDR"):
        parse_routing_text("ip(999.1.1.1)->direct")


def test_rejects_unsupported_matcher() -> None:
    with pytest.raises(RoutingConfigError, match="unsupported domain matcher"):
        parse_routing_text("domain(foo:bar)->direct")


def test_keeps_hash_inside_regex() -> None:
    config = parse_routing_text("domain(regexp: '^foo#bar$')->direct # trailing")

    assert config.rules[0].matchers[0].value == "^foo#bar$"
