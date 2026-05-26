# /// script
# dependencies = [
#     "requests>=2.31.0",
#     "rich>=13.7.0",
#     "textual>=0.50.0",
# ]
# ///
import argparse
import os
from pathlib import Path
import socket
from urllib.parse import urlparse
import requests

from textual.app import App
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Label, ListView, DataTable, RichLog, Button
from textual.screen import ModalScreen

ROMM_BRIDGE_VERSION = "0.0.1"

# Paths
ES_DE_DIR = Path.home() / "ES-DE"
ROM_LIST_DIR = Path.home() / ".local/share/romm/rom_list"
ROMS_DIR = Path.home() / ".local/share/romm/roms"

# Environment/Server configurations
ROMM_URL = os.environ.get("ROMM_URL", "")
ROMM_API_KEY = os.environ.get("ROMM_API_KEY", "")


class SystemLogModal(ModalScreen):
    """A global overlay to view application logs."""

    BINDINGS = [
        ("escape", "close_modal", "Close"),
        ("~", "close_modal", "Close")
    ]

    def __init__(self, logs: list):
        super().__init__()
        self.logs = logs
        self.rich_log = RichLog(id="system-rich-log", highlight=True, markup=True)

    def compose(self) -> ComposeResult:
        with Container(id="log-modal-panel"):
            yield self.rich_log
            with Horizontal(id="modal-actions"):
                yield Button("Close Logs", id="btn-close-log", variant="primary")

    def on_mount(self) -> None:
        for msg in self.logs:
            self.rich_log.write(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-log":
            self.dismiss()

    def action_close_modal(self) -> None:
        self.dismiss()


class RommBridge(App):
    """A TUI application used to sync RomM information with different clients."""

    TITLE = "RomM Bridge"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("~", "toggle_log", "System Logs")
    ]

    CSS_PATH = "romm_bridge.tcss"

    # ==================== #
    #    Init Functions    #
    # ==================== #
    def __init__(self):
        super().__init__()

        self.romm_url = ROMM_URL.rstrip('/')
        self.api_key = ROMM_API_KEY

        self.system_logs = []

        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def compose(self) -> ComposeResult:
        """Setups the look and feel of the application"""
        yield Header()

        with Horizontal(id="main-layout"):
            with Vertical(id="platform-sidebar"):
                yield Label("Platforms", id="sidebar-title")
                yield ListView(id="sidebar-list")

            with Vertical(id="platform-info"):
                yield DataTable(id="roms-table", cursor_type="row")

        yield Footer()

    def on_mount(self) -> None:
        """Initializes client environments and populates server categories."""
        ES_DE_DIR.mkdir(parents=True, exist_ok=True)
        ROM_LIST_DIR.mkdir(parents=True, exist_ok=True)
        ROMS_DIR.mkdir(parents=True, exist_ok=True)

        self.log_msg(f"##### RomM Bridge Version {ROMM_BRIDGE_VERSION} #####")

        if self.romm_url == "":
            self.notify(f"No RomM server was provided. Restart with RomM server provided.", severity="error")
            return

        if not self.can_connect(self.romm_url):
            self.notify(f"Cannot connect to {self.romm_url}. Is the server down?", severity="error")
            return

        if not self.api_key:
            self.notify("No RomM API key was provided.", severity="error")
            return

        if not self.is_api_key_valid():
            self.notify(f"{self.romm_url} rejected your API key.")
            return

        self.log_msg(f"Connected to server: [cyan]{self.romm_url}[/]")

    # ==================== #
    #       Bindings       #
    # ==================== #

    def action_toggle_log(self) -> None:
        """Toggles the global system log overlay."""
        # If it's already open, pop it off the screen
        if isinstance(self.screen, SystemLogModal):
            self.pop_screen()
        else:
            # Otherwise, push it and hand it the persistent log history
            self.push_screen(SystemLogModal(self.system_logs))

    # ==================== #
    #         Misc         #
    # ==================== #

    def log_msg(self, msg) -> None:
        """Logs the given message to the RichLog in the SystemLogModal modal."""
        self.system_logs.append(msg)
        if len(self.system_logs) > 1000:
            self.system_logs.pop(0)

    def can_connect(self, url_or_address: str, default_port: int = 80, timeout: int = 3) -> bool:
        """Attempts a rapid TCP handshake to see if the target is alive and listening."""

        # Extract the raw hostname and port if a full URL is provided
        parsed = urlparse(url_or_address) if "://" in url_or_address else urlparse(f"http://{url_or_address}")
        target_host = parsed.hostname
        target_port = parsed.port or default_port

        if not target_host:
            return False

        try:
            with socket.create_connection((target_host, target_port), timeout=timeout):
                return True
        except (socket.timeout, socket.error, ConnectionRefusedError):
            return False

    def is_api_key_valid(self, timeout: int = 3) -> bool:
        """Makes a lightweight authenticated request to verify the API key."""

        try:
            # /api/users/me requires auth but has a tiny payload that we can just check it for a valid API key
            res = requests.get(f"{self.romm_url.rstrip('/')}/api/users/me", headers=self.headers, timeout=timeout)

            if res.status_code in (401, 403):
                return False

            res.raise_for_status()
            return True
        except requests.RequestException:
            return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog="RommSync",
        description="A terminal dashboard to sync RomM instances with ES-DE.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Server Credentials
    parser.add_argument("-u", "--url", help="URL of your RomM server (overrides ROMM_URL env var)", default=ROMM_URL)
    parser.add_argument("-k", "--api-key", help="RomM API Key (overrides ROMM_API_KEY env var)", default=ROMM_API_KEY)

    args = parser.parse_args()

    ROMM_URL = args.url
    ROMM_API_KEY = args.api_key

    app = RommBridge()
    app.run()
