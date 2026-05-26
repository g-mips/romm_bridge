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

from textual.app import App
from textual.containers import Vertical, Horizontal
from textual.widgets import Header, Footer, Label, ListView, DataTable

# Paths
ES_DE_DIR = Path.home() / "ES-DE"
ROM_LIST_DIR = Path.home() / ".local/share/romm/rom_list"
ROMS_DIR = Path.home() / ".local/share/romm/roms"

# Environment/Server configurations
ROMM_URL = os.environ.get("ROMM_URL", "")
ROMM_API_KEY = os.environ.get("ROMM_API_KEY", "")


class RommBridge(App):
    """A TUI application used to sync RomM information with different clients."""

    TITLE = "RomM Bridge"

    BINDINGS = [
        ("q", "quit", "Quit")
    ]

    CSS_PATH = "romm_bridge.tcss"

    def __init__(self):
        super().__init__()

        self.romm_url = ROMM_URL.rstrip('/')
        self.api_key = ROMM_API_KEY

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
