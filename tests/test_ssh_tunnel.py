import pytest

from ssh_vpn_gui import ssh_tunnel


class HangingDaemon:
    stdin = None
    stdout = None
    stderr = None
    pid = 4242
    terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def communicate(self, timeout=None):
        assert self.terminated
        return "", "daemon did not become ready"


class DummyStdin:
    def write(self, _value):
        return None

    def close(self):
        return None


def test_start_tunnel_timeout_terminates_daemon_before_collecting_stderr(monkeypatch, tmp_path) -> None:
    daemon = HangingDaemon()
    daemon.stdin = DummyStdin()
    bootstrap_calls = []

    monkeypatch.setattr(ssh_tunnel, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ssh_tunnel, "ensure_data_dir", lambda: None)
    monkeypatch.setattr(ssh_tunnel, "stop_tunnel", lambda dry_run=False: [])
    monkeypatch.setattr(
        ssh_tunnel,
        "_run_password_ssh",
        lambda command, password, **_kwargs: bootstrap_calls.append((command, password)) or ["remote bootstrap complete"],
    )
    monkeypatch.setattr(ssh_tunnel, "_wait_for_daemon_ready", lambda process, timeout: False)
    monkeypatch.setattr(ssh_tunnel.subprocess, "Popen", lambda *_args, **_kwargs: daemon)

    with pytest.raises(ssh_tunnel.SshTunnelError, match="daemon did not become ready"):
        ssh_tunnel.start_tunnel("203.0.113.10", "root", "secret")

    assert bootstrap_calls
    assert daemon.terminated


def test_start_tunnel_dry_run_bootstraps_before_tun() -> None:
    commands = ssh_tunnel.start_tunnel("203.0.113.10", "root", "secret", dry_run=True)

    assert len(commands) == 2
    assert "-w 3:3" not in commands[0]
    assert "PermitTunnel point-to-point" in commands[0]
    assert "-w 3:3" in commands[1]
