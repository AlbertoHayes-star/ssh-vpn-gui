from ssh_vpn_gui.dns_proxy import DnsClassifier
from ssh_vpn_gui.routing_config import parse_routing_text


def test_dns_classifier_applies_domain_rules_in_order() -> None:
    classifier = DnsClassifier(
        parse_routing_text(
            """
            default: proxy
            domain(domain:example.com)->direct
            domain(regexp: '(^|[.])blocked[.]test$')->proxy
            domain(geosite:ru)->direct
            """
        )
    )

    assert classifier._classify_domain("www.example.com") == "direct"
    assert classifier._classify_domain("blocked.test") == "proxy"
    assert classifier._classify_domain("yandex.ru") == "direct"
    assert classifier._classify_domain("ifconfig.me") == "proxy"

