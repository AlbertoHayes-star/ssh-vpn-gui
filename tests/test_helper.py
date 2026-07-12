import subprocess

from ssh_vpn_gui import helper


def test_health_check_keeps_tunnel_when_public_ip_services_timeout(monkeypatch) -> None:
    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["curl"], timeout=7)

    monkeypatch.setattr(helper.subprocess, "run", timeout_run)

    assert helper._health_check() == ["public IP health check unavailable; tunnel left running"]


def test_health_check_reports_public_ip_without_rollback(monkeypatch) -> None:
    def public_ip_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["curl"], returncode=0, stdout="198.51.100.10\n", stderr="")

    monkeypatch.setattr(helper.subprocess, "run", public_ip_run)

    assert helper._health_check() == ["public IP: 198.51.100.10"]
