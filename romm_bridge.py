# /// script
# dependencies = [
#     "requests>=2.31.0",
#     "rich>=13.7.0",
#     "textual>=0.50.0",
# ]
# ///
from textual.app import App

class RommBridge(App):
    """A TUI application used to sync RomM information with different clients."""

    TITLE = "RomM Bridge"

    BINDINGS = [
        ("q", "quit", "Quit")
    ]

    CSS_PATH = "romm_bridge.tcss"


if __name__ == '__main__':
    app = RommBridge()
    app.run()
