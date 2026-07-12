from ssh_vpn_gui import app


def test_save_config_persists_checkbox_preferences(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_file = config_dir / "config.json"
    monkeypatch.setattr(app, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(app, "CONFIG_FILE", config_file)

    app.save_config(
        server="203.0.113.10",
        login="root",
        password="secret",
        routing=False,
        cascade=True,
        ovpn_path="/tmp/client.ovpn",
    )

    assert app.load_saved_config() == {
        "server": "203.0.113.10",
        "login": "root",
        "password": "secret",
        "ovpn_path": "/tmp/client.ovpn",
        "routing": False,
        "cascade": True,
    }
    assert config_file.stat().st_mode & 0o777 == 0o600


def test_cascade_on_uses_install_timeout() -> None:
    assert app._helper_timeout(["cascade-on"]) == 360
    assert app._helper_timeout(["cascade-off"]) == 90
