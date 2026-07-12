from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .paths import SUDOERS_RULE, SYSTEM_HELPER, SYSTEM_ROUTING_FILE


APP_ID = "dev.cursor.SshVpnGui"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ssh-vpn-gui"
CONFIG_FILE = CONFIG_DIR / "config.json"


class SshVpnWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="SSH VPN")
        self.set_default_size(520, 420)
        self.connected = False

        saved_config = load_saved_config()
        server_row, self.server_entry = _entry_row("Remote server IP address")
        login_row, self.login_entry = _entry_row("Remote login")
        password_row, self.password_entry = _entry_row("Remote password", password=True)
        self.server_entry.set_text(saved_config.get("server", ""))
        self.login_entry.set_text(saved_config.get("login", "root"))
        self.password_entry.set_text(saved_config.get("password", ""))

        self.routing_switch = Gtk.CheckButton()
        self.routing_switch.set_valign(Gtk.Align.CENTER)
        self.routing_switch.set_active(True)
        self.routing_switch.connect("toggled", self._on_routing_toggled)
        routing_row = Adw.ActionRow(title="Routing rules", subtitle="Checked: use routing.cfg. Unchecked: send all traffic through tunnel.")
        routing_row.add_suffix(self.routing_switch)
        routing_row.set_activatable_widget(self.routing_switch)

        self.connect_button = Gtk.Button(label="Connect")
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.connect("clicked", self._on_connect_clicked)

        self.disconnect_button = Gtk.Button(label="Disconnect")
        self.disconnect_button.connect("clicked", self._on_disconnect_clicked)
        self.disconnect_button.set_sensitive(False)

        self.update_geo_button = Gtk.Button(label="Update Routing Data")
        self.update_geo_button.connect("clicked", self._on_update_geo_clicked)

        self.diagnose_button = Gtk.Button(label="Diagnose")
        self.diagnose_button.connect("clicked", self._on_diagnose_clicked)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_margin_top(12)
        button_box.append(self.connect_button)
        button_box.append(self.disconnect_button)

        self.status = Gtk.Label(label="Ready")
        self.status.set_wrap(True)
        self.status.set_xalign(0)
        self.status.add_css_class("dim-label")

        group = Adw.PreferencesGroup(title="Connection")
        group.add(server_row)
        group.add(login_row)
        group.add(password_row)
        group.add(routing_row)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.append(group)
        content.append(button_box)
        content.append(self.update_geo_button)
        content.append(self.diagnose_button)
        content.append(Gtk.Separator())
        content.append(self.status)

        header = Adw.HeaderBar()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(header)
        root.append(content)
        self.set_content(root)

    def _on_connect_clicked(self, _button: Gtk.Button) -> None:
        server = self.server_entry.get_text().strip()
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not server or not login or not password:
            self._set_status("Enter remote server IP address, login, and password.")
            return

        save_config(server=server, login=login, password=password)
        args = ["connect", "--server", server, "--login", login]
        if not self.routing_switch.get_active():
            args.append("--no-routing")
        self._run_helper(args, password=password, busy_text="Connecting...")

    def _on_disconnect_clicked(self, _button: Gtk.Button) -> None:
        args = ["disconnect"]
        server = self.server_entry.get_text().strip()
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text()
        if server and login and password:
            save_config(server=server, login=login, password=password)
            args.extend(["--server", server, "--login", login])
        self._run_helper(args, password=password, busy_text="Disconnecting...")

    def _on_update_geo_clicked(self, _button: Gtk.Button) -> None:
        self._run_helper(["update-geo"], password=None, busy_text="Updating routing data...")

    def _on_diagnose_clicked(self, _button: Gtk.Button) -> None:
        args = ["diagnose"]
        server = self.server_entry.get_text().strip()
        if server:
            args.extend(["--server", server])
        self._run_helper(args, password=None, busy_text="Running diagnostics...")

    def _on_routing_toggled(self, _button: Gtk.CheckButton) -> None:
        if not self.connected:
            return
        server = self.server_entry.get_text().strip()
        state = self.routing_switch.get_active()
        args = ["routing-on" if state else "routing-off"]
        if server:
            args.extend(["--server", server])
        self._run_helper(args, password=None, busy_text="Updating routing...")

    def _run_helper(self, args: list[str], *, password: str | None, busy_text: str) -> None:
        self._set_busy(True)
        self._set_status(busy_text)

        def worker() -> None:
            result = run_helper(args, password=password)
            GLib.idle_add(self._handle_helper_result, args[0], result)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_helper_result(self, command: str, result: dict) -> bool:
        self._set_busy(False)
        if result.get("ok"):
            if command == "connect":
                self.connected = True
            elif command == "disconnect":
                self.connected = False
            self._sync_buttons()
            messages = result.get("messages") or []
            self._set_status("Done." if not messages else "\n".join(messages[-5:]))
        else:
            self._set_status(result.get("error", "Unknown helper error"))
        return False

    def _set_busy(self, busy: bool) -> None:
        self.connect_button.set_sensitive(not busy and not self.connected)
        self.disconnect_button.set_sensitive(not busy and self.connected)
        self.update_geo_button.set_sensitive(not busy)
        self.diagnose_button.set_sensitive(not busy)
        self.routing_switch.set_sensitive(not busy)

    def _sync_buttons(self) -> None:
        self.connect_button.set_sensitive(not self.connected)
        self.disconnect_button.set_sensitive(self.connected)

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)


def run_helper(args: list[str], *, password: str | None) -> dict:
    command = [*_privileged_helper_command(), *args, "--routing-file", str(SYSTEM_ROUTING_FILE)]
    input_text = None
    if password:
        command.append("--password-stdin")
        input_text = password

    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=_helper_timeout(args),
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"Missing executable: {exc.filename}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Helper timed out. Check SSH, DNS-over-TLS, or GitHub access for routing data."}

    output = completed.stdout.strip()
    stderr = completed.stderr.strip()
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError:
        payload = {"ok": False, "error": output or stderr or "Helper returned invalid output"}
    if completed.returncode != 0:
        if not payload.get("ok", False):
            payload.setdefault("error", stderr or output or f"Helper exited with {completed.returncode}")
        else:
            payload = {"ok": False, "error": stderr or f"Helper exited with {completed.returncode}"}
    return payload


def _helper_timeout(args: list[str]) -> int:
    command = args[0] if args else ""
    if command == "update-geo":
        return 180
    if command == "diagnose":
        return 30
    return 90


def _entry_row(title: str, *, password: bool = False) -> tuple[Adw.ActionRow, Gtk.Entry]:
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_valign(Gtk.Align.CENTER)
    if password:
        entry.set_visibility(False)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)

    row = Adw.ActionRow(title=title)
    row.add_suffix(entry)
    row.set_activatable_widget(entry)
    return row, entry


def load_saved_config() -> dict[str, str]:
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return {key: str(value) for key, value in data.items() if key in {"server", "login", "password"}}


def save_config(*, server: str, login: str, password: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps({"server": server, "login": login, "password": password}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    CONFIG_FILE.chmod(0o600)


def _helper_command() -> list[str]:
    return [str(SYSTEM_HELPER)]


def _privileged_helper_command() -> list[str]:
    helper_command = _helper_command()
    if SUDOERS_RULE.exists() and shutil.which("sudo"):
        return ["sudo", "-n", *helper_command]
    return ["pkexec", *helper_command]


class SshVpnApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = SshVpnWindow(self)
        window.present()


def main() -> int:
    app = SshVpnApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
