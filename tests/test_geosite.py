from ssh_vpn_gui.geosite import GeositeStore


def test_builtin_ru_geosite_matches_ru_domains() -> None:
    store = GeositeStore()

    assert store.match("ru", "yandex.ru")
    assert store.match("ru", "sub.example.su")
    assert store.match("ru", "get-v6.2ip.io")
    assert store.match("ru", "static.2ip.io")
    assert not store.match("ru", "example.com")


def test_builtin_google_geosite_matches_suffix() -> None:
    store = GeositeStore()

    assert store.match("google", "mail.google.com")
    assert store.match("google", "fonts.googleapis.com")
    assert not store.match("google", "google.evil.example")
