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


def test_ru_geosite_recursively_loads_maintained_service_categories(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "category-ru").write_text(
        "include:mailru-group\ninclude:yandex\n",
        encoding="utf-8",
    )
    (data / "mailru-group").write_text("include:vk\nmail.ru\n", encoding="utf-8")
    (data / "vk").write_text(
        "vk.com\nvk-portal.net\nvkuseraudio.net\n",
        encoding="utf-8",
    )
    (data / "yandex").write_text("yandex.com\nyastatic.net\n", encoding="utf-8")
    store = GeositeStore(root=tmp_path)

    assert store.match("ru", "stats.vk-portal.net")
    assert store.match("ru", "sun9.vkuseraudio.net")
    assert store.match("ru", "passport.yandex.com")
    assert store.match("ru", "mail.ru")
    assert not store.match("ru", "example.com")


def test_geosite_include_cycles_do_not_recurse_forever(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "first").write_text("include:second\nfirst.example\n", encoding="utf-8")
    (data / "second").write_text("include:first\nsecond.example\n", encoding="utf-8")
    store = GeositeStore(root=tmp_path)

    assert store.match("first", "first.example")
    assert store.match("first", "second.example")
