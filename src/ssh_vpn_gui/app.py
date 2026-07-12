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


class RoutingEditorWindow(Gtk.Window):
    def __init__(self, parent: "SshVpnWindow") -> None:
        super().__init__(title="Edit Routing Rules", transient_for=parent, modal=True)
        self.parent_window = parent
        self.set_default_size(720, 520)

        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", lambda _button: self.close())
        self.save_button = Gtk.Button(label="Save")
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self._on_save_clicked)

        header = Gtk.HeaderBar()
        header.pack_start(cancel_button)
        header.pack_end(self.save_button)
        self.set_titlebar(header)

        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)

        self.message = Gtk.Label()
        self.message.set_xalign(0)
        self.message.set_wrap(True)
        self.message.add_css_class("dim-label")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_child(self.text_view)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(scroller)
        content.append(self.message)
        self.set_child(content)

        try:
            text = SYSTEM_ROUTING_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            text = ""
            self.message.set_text(f"Unable to read routing rules: {exc}")
            self.save_button.set_sensitive(False)
        self.text_view.get_buffer().set_text(text)

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        buffer = self.text_view.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        self.save_button.set_sensitive(False)
        self.text_view.set_editable(False)
        self.message.set_text("Validating and saving routing rules...")

        def worker() -> None:
            result = run_helper(["save-routing"], password=None, content=content)
            GLib.idle_add(self._handle_save_result, result)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_save_result(self, result: dict) -> bool:
        if result.get("ok"):
            self.close()
            self.parent_window._on_routing_rules_saved()
        else:
            self.message.set_text(result.get("error", "Unable to save routing rules"))
            self.save_button.set_sensitive(True)
            self.text_view.set_editable(True)
        return False


class SshVpnWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="SSH VPN")
        self.set_default_size(520, 420)
        self.connected = False
        self.busy = False
        self._suppress_toggle = False
        self._pending_toggle: tuple[str, bool] | None = None

        saved_config = load_saved_config()
        server_row, self.server_entry = _entry_row("Remote server IP address")
        login_row, self.login_entry = _entry_row("Remote login")
        password_row, self.password_entry = _entry_row("Remote password", password=True)
        self.server_entry.set_text(saved_config.get("server", ""))
        self.login_entry.set_text(saved_config.get("login", "root"))
        self.password_entry.set_text(saved_config.get("password", ""))

        self.routing_switch = Gtk.CheckButton()
        self.routing_switch.set_valign(Gtk.Align.CENTER)
        self.routing_switch.set_active(bool(saved_config.get("routing", True)))
        self.routing_switch.connect("toggled", self._on_routing_toggled)
        routing_row = Adw.ActionRow(title="Routing rules", subtitle="Checked: use routing.cfg. Unchecked: send all traffic through tunnel.")
        routing_row.add_suffix(self.routing_switch)
        routing_row.set_activatable_widget(self.routing_switch)

        self.ovpn_path = saved_config.get("ovpn_path", "")
        self._ovpn_dialog = None
        self.cascade_switch = Gtk.CheckButton()
        self.cascade_switch.set_valign(Gtk.Align.CENTER)
        self.cascade_switch.set_active(bool(saved_config.get("cascade", False)))
        self.cascade_switch.connect("toggled", self._on_cascade_toggled)
        cascade_row = Adw.ActionRow(
            title="Use .ovpn file on remote server",
            subtitle="Run the chosen OpenVPN config on the server and route tunnel traffic through it (cascaded VPN).",
        )
        cascade_row.add_suffix(self.cascade_switch)
        cascade_row.set_activatable_widget(self.cascade_switch)

        self.ovpn_button = Gtk.Button(label="Choose .ovpn file")
        self.ovpn_button.set_valign(Gtk.Align.CENTER)
        self.ovpn_button.connect("clicked", self._on_choose_ovpn_clicked)
        self.ovpn_row = Adw.ActionRow(title="OpenVPN config")
        self.ovpn_row.add_suffix(self.ovpn_button)
        self._update_ovpn_subtitle()

        self.connect_button = Gtk.Button(label="Connect")
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.connect("clicked", self._on_connect_clicked)

        self.disconnect_button = Gtk.Button(label="Disconnect")
        self.disconnect_button.connect("clicked", self._on_disconnect_clicked)
        self.disconnect_button.set_sensitive(False)

        self.update_geo_button = Gtk.Button(label="Update Routing Data")
        self.update_geo_button.connect("clicked", self._on_update_geo_clicked)

        self.edit_routing_button = Gtk.Button(label="Edit Routing Rules")
        self.edit_routing_button.connect("clicked", lambda _button: self.open_routing_editor())

        routing_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        routing_button_box.append(self.update_geo_button)
        routing_button_box.append(self.edit_routing_button)

        self.diagnose_button = Gtk.Button(label="Diagnose")
        self.diagnose_button.connect("clicked", self._on_diagnose_clicked)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        button_box.set_margin_top(12)
        button_box.append(self.connect_button)
        button_box.append(self.disconnect_button)

        self.status = Gtk.Label(label="Ready")
        self.status.set_wrap(True)
        self.status.set_xalign(0)
        self.status.set_yalign(0)
        self.status.set_selectable(True)
        self.status.set_margin_top(8)
        self.status.set_margin_bottom(8)
        self.status.set_margin_start(8)
        self.status.set_margin_end(8)
        self.status.add_css_class("dim-label")

        self.status_scroller = Gtk.ScrolledWindow()
        self.status_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.status_scroller.set_size_request(-1, 120)
        self.status_scroller.set_vexpand(True)
        self.status_scroller.add_css_class("card")
        self.status_scroller.set_child(self.status)

        group = Adw.PreferencesGroup(title="Connection")
        group.add(server_row)
        group.add(login_row)
        group.add(password_row)
        group.add(routing_row)
        group.add(cascade_row)
        group.add(self.ovpn_row)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.append(group)
        content.append(button_box)
        content.append(routing_button_box)
        content.append(self.diagnose_button)
        content.append(Gtk.Separator())
        content.append(self.status_scroller)

        header = Adw.HeaderBar()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(header)
        root.append(content)
        self.set_content(root)
        self._set_busy(False)

    def open_routing_editor(self) -> None:
        RoutingEditorWindow(self).present()

    def _on_routing_rules_saved(self) -> None:
        if self.connected and self.routing_switch.get_active() and not self.busy:
            server = self.server_entry.get_text().strip()
            args = ["routing-on", "--server", server]
            self._run_helper(args, password=None, busy_text="Applying updated routing rules...")
        else:
            self._set_status("Routing rules saved. They will be used on the next connection.")

    def _on_connect_clicked(self, _button: Gtk.Button) -> None:
        server = self.server_entry.get_text().strip()
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not server or not login or not password:
            self._set_status("Enter remote server IP address, login, and password.")
            return

        cascade = self.cascade_switch.get_active()
        if cascade and not self.ovpn_path:
            self._set_status("Choose a .ovpn file or turn off the cascaded VPN option.")
            return

        self._save_config()
        args = ["connect", "--server", server, "--login", login]
        if not self.routing_switch.get_active():
            args.append("--no-routing")
        if cascade:
            args.extend(["--ovpn-file", self.ovpn_path])
            busy_text = "Connecting (preparing OpenVPN cascade; first setup may take a minute)..."
        else:
            busy_text = "Connecting..."
        self._run_helper(args, password=password, busy_text=busy_text)

    def _on_disconnect_clicked(self, _button: Gtk.Button) -> None:
        args = ["disconnect"]
        server = self.server_entry.get_text().strip()
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text()
        if server and login and password:
            self._save_config()
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
        if self._suppress_toggle:
            return
        state = self.routing_switch.get_active()
        if not self.connected:
            self._save_config()
            return
        server = self.server_entry.get_text().strip()
        args = ["routing-on" if state else "routing-off"]
        if server:
            args.extend(["--server", server])
        self._pending_toggle = ("routing", not state)
        self._run_helper(args, password=None, busy_text="Updating routing...")

    def _on_cascade_toggled(self, _button: Gtk.CheckButton) -> None:
        self.ovpn_row.set_sensitive(not self.busy)
        if self._suppress_toggle:
            return

        enabled = self.cascade_switch.get_active()
        if enabled and not Path(self.ovpn_path).is_file():
            self._set_switch_active(self.cascade_switch, False)
            self._set_status("Choose a .ovpn file to use the cascaded VPN.")
            self._save_config()
            return

        if not self.connected:
            self._save_config()
            return

        self._apply_cascade(enabled)

    def _on_choose_ovpn_clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileChooserNative(
            title="Select an OpenVPN configuration",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Open",
            cancel_label="_Cancel",
        )
        ovpn_filter = Gtk.FileFilter()
        ovpn_filter.set_name("OpenVPN config (*.ovpn, *.conf)")
        ovpn_filter.add_pattern("*.ovpn")
        ovpn_filter.add_pattern("*.conf")
        dialog.add_filter(ovpn_filter)
        dialog.connect("response", self._on_ovpn_dialog_response)
        self._ovpn_dialog = dialog
        dialog.show()

    def _on_ovpn_dialog_response(self, dialog: Gtk.FileChooserNative, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            selected = dialog.get_file()
            if selected is not None:
                self.ovpn_path = selected.get_path() or ""
                self._update_ovpn_subtitle()
                self._save_config()
                if self.connected and self.cascade_switch.get_active():
                    self._apply_cascade(True, replacing=True)
        self._ovpn_dialog = None

    def _update_ovpn_subtitle(self) -> None:
        if self.ovpn_path:
            self.ovpn_row.set_subtitle(Path(self.ovpn_path).name)
        else:
            self.ovpn_row.set_subtitle("No file selected")
        self.ovpn_row.set_sensitive(not self.busy)

    def _save_config(self) -> None:
        save_config(
            server=self.server_entry.get_text().strip(),
            login=self.login_entry.get_text().strip(),
            password=self.password_entry.get_text(),
            routing=self.routing_switch.get_active(),
            cascade=self.cascade_switch.get_active(),
            ovpn_path=self.ovpn_path,
        )

    def _apply_cascade(self, enabled: bool, *, replacing: bool = False) -> None:
        server = self.server_entry.get_text().strip()
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not server or not login or not password:
            self._set_switch_active(self.cascade_switch, not enabled)
            self._set_status("Remote server, login, and password are required.")
            return

        args = ["cascade-on" if enabled else "cascade-off", "--server", server, "--login", login]
        if enabled:
            if not Path(self.ovpn_path).is_file():
                self._set_switch_active(self.cascade_switch, False)
                self._set_status("The selected .ovpn file no longer exists.")
                self._save_config()
                return
            args.extend(["--ovpn-file", self.ovpn_path])

        self._pending_toggle = ("cascade", False if replacing else not enabled)
        if replacing:
            busy_text = "Replacing the remote OpenVPN configuration..."
        elif enabled:
            busy_text = "Starting OpenVPN cascade on the remote server..."
        else:
            busy_text = "Stopping OpenVPN cascade on the remote server..."
        self._run_helper(args, password=password, busy_text=busy_text)

    def _set_switch_active(self, switch: Gtk.CheckButton, active: bool) -> None:
        self._suppress_toggle = True
        try:
            switch.set_active(active)
        finally:
            self._suppress_toggle = False
        self.ovpn_row.set_sensitive(not self.busy)

    def _run_helper(self, args: list[str], *, password: str | None, busy_text: str) -> None:
        self._set_busy(True)
        self._set_status(busy_text)

        def worker() -> None:
            result = run_helper(args, password=password)
            GLib.idle_add(self._handle_helper_result, args[0], result)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_helper_result(self, command: str, result: dict) -> bool:
        if result.get("ok"):
            if command == "connect":
                self.connected = True
            elif command == "disconnect":
                self.connected = False
            if command in {"routing-on", "routing-off", "cascade-on", "cascade-off"}:
                self._save_config()
            self._pending_toggle = None
            messages = result.get("messages") or []
            self._set_status("Done." if not messages else "\n".join(messages[-5:]))
        else:
            if self._pending_toggle is not None:
                name, previous_state = self._pending_toggle
                switch = self.routing_switch if name == "routing" else self.cascade_switch
                self._set_switch_active(switch, previous_state)
                self._pending_toggle = None
                self._save_config()
            self._set_status(result.get("error", "Unknown helper error"))
        self._set_busy(False)
        return False

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        available = not busy
        self.connect_button.set_sensitive(available and not self.connected)
        self.disconnect_button.set_sensitive(available and self.connected)
        if self.connected:
            self.connect_button.set_label("Connected")
            self.connect_button.remove_css_class("suggested-action")
            self.disconnect_button.add_css_class("destructive-action")
        else:
            self.connect_button.set_label("Connect")
            self.connect_button.add_css_class("suggested-action")
            self.disconnect_button.remove_css_class("destructive-action")
        self.update_geo_button.set_sensitive(available)
        self.edit_routing_button.set_sensitive(available)
        self.diagnose_button.set_sensitive(available)
        self.routing_switch.set_sensitive(available)
        self.cascade_switch.set_sensitive(available)
        self.ovpn_row.set_sensitive(available)
        credentials_editable = available and not self.connected
        self.server_entry.set_sensitive(credentials_editable)
        self.login_entry.set_sensitive(credentials_editable)
        self.password_entry.set_sensitive(credentials_editable)

    def _set_status(self, text: str) -> None:
        self.status.set_text(text)
        GLib.idle_add(self._scroll_status_to_bottom)

    def _scroll_status_to_bottom(self) -> bool:
        adjustment = self.status_scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False


def run_helper(args: list[str], *, password: str | None, content: str | None = None) -> dict:
    command = [*_privileged_helper_command(), *args, "--routing-file", str(SYSTEM_ROUTING_FILE)]
    input_text = None
    if password and content is not None:
        return {"ok": False, "error": "Cannot send a password and routing content together"}
    if password:
        command.append("--password-stdin")
        input_text = password
    elif content is not None:
        command.append("--content-stdin")
        input_text = content

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
    if command == "cascade-on" or (command == "connect" and "--ovpn-file" in args):
        return 360
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


def load_saved_config() -> dict:
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    result: dict = {}
    for key in ("server", "login", "password", "ovpn_path"):
        if key in data:
            result[key] = str(data[key])
    for key in ("routing", "cascade"):
        if key in data:
            result[key] = bool(data[key])
    return result


def save_config(
    *,
    server: str,
    login: str,
    password: str,
    routing: bool = True,
    cascade: bool = False,
    ovpn_path: str = "",
) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(
            {
                "server": server,
                "login": login,
                "password": password,
                "routing": routing,
                "cascade": cascade,
                "ovpn_path": ovpn_path,
            },
            ensure_ascii=False,
            indent=2,
        ),
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
