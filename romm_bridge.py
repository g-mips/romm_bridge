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
import urllib.parse
import requests
import json
import asyncio
import xml.etree.ElementTree as ET
from xml.dom import minidom
import threading
from datetime import datetime
import time
import math

from rich.text import Text
from textual.app import App
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Label, ListView, ListItem, DataTable, RichLog, Button, Checkbox, Select
from textual.screen import ModalScreen
from textual import work, on

ROMM_BRIDGE_VERSION = "0.0.1"

# Paths
ES_DE_DIR = Path.home() / "ES-DE"
ROM_LIST_DIR = Path.home() / ".local/share/romm_bridge/rom_list"
ROMS_DIR = Path.home() / ".local/share/romm_bridge/roms"

# Environment/Server configurations
ROMM_URL = os.environ.get("ROMM_URL", "")
ROMM_API_KEY = os.environ.get("ROMM_API_KEY", "")

# App Cache Directory
CACHE_DIR = Path.home() / ".cache/romm_bridge"
CACHE_FILE = CACHE_DIR / "roms_cache.json"


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

    def compose(self):
        with Container(classes="modal-panel"):
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


class SimulationModal(ModalScreen):
    """Dry run modal"""

    def compose(self) -> ComposeResult:
        with Container(classes="modal-panel"):
            yield Label("Calculating metrics and network download size...", id="modal-summary")
            yield RichLog(id="modal-log", highlight=True, markup=True)
            with Horizontal(id="modal-actions"):
                yield Button("Close Preview", id="btn-close-modal", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-modal":
            self.dismiss()


class StatsModal(ModalScreen):
    """An overlay panel displaying live metrics for the selected platform."""

    BINDINGS = [
        ("escape", "close_modal", "Close"),
        ("s", "close_modal", "Close")
    ]

    def __init__(self, platform_name: str, stats_markup: str):
        super().__init__()
        self.platform_name = platform_name
        self.stats_markup = stats_markup

    def compose(self):
        with Container(classes="modal-panel"):
            yield Label(f"{self.platform_name.upper()} STATS", id="modal-title")

            # Use a standard label with markup=True to render our rich text
            yield Label(self.stats_markup, markup=True, id="modal-summary")

            with Horizontal(id="modal-actions"):
                yield Button("Close Dashboard", id="btn-close-stats", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-close-stats":
            self.dismiss()

    def action_close_modal(self) -> None:
        self.dismiss()


class RommTable(DataTable):
    """DataTable that setups up a function for watching hover events."""

    def watch_hover_coordinate(self, value) -> None:
        """Create a tooltip when hovering over the metadata column."""
        if not value:
            self.tooltip = None
            return

        try:
            # Translate the raw terminal cursor coordinate into Textual unique layout keys
            row_key, col_key = self.coordinate_to_cell_key(value)

            # Block if row key is blank or currently printing a loading line
            if not row_key.value or row_key.value == "loading":
                self.tooltip = None
                return

            # TODO: Do I only want to target this type of column? Or do I want to allow hovering on any column?
            # Target the Metadata Status column cells exclusively
            if col_key.value == "col_meta":
                missing_assets = getattr(self.app, "missing_media_cache", {}).get(row_key.value, [])

                if missing_assets:
                    bullet_list = "\n".join(f" 📦 {asset}" for asset in missing_assets)
                    self.tooltip = f"[bold yellow]Missing Media Details:[/]\n{bullet_list}"
                else:
                    self.tooltip = "[bold green]✨ All assets fully synced on disk![/]"
            else:
                # Instantly drop the tooltips if the user hovers over game titles or selection boxes
                self.tooltip = None

        except Exception:
            # Catch coordinate out-of-bounds or header selection exceptions silently
            self.tooltip = None


class RommBridge(App):
    """A TUI application used to sync RomM information with different clients."""

    TITLE = "RomM Bridge"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("~", "toggle_log", "System Logs"),
        ("j", "cursor_down", "Move Down"),
        ("k", "cursor_up", "Move Up"),
        ("m", "toggle_mark", "Mark Range (m...m)"),
        ("s", "show_stats", "Platform Stats")
    ]

    CSS_PATH = "romm_bridge.tcss"

    # ==================== #
    #    Init Functions    #
    # ==================== #
    def __init__(self, skip_cache_refresh: boolean = False):
        super().__init__()

        self.skip_cache_refresh = skip_cache_refresh
        self.romm_url = ROMM_URL.rstrip('/')
        self.api_key = ROMM_API_KEY

        self.system_logs = []

        self.headers = {"Authorization": f"Bearer {self.api_key}"}

        self.media_mappings = [
            ("image", "covers", ".png", "Cover", "path_cover_large", "url_cover", "path_cover_large"),
            ("video", "videos", ".mp4", "Video", "video_path", "video_url", "path_video"),
            ("manual", "manuals", ".pdf", "Manual", "manual_path", "manual_url", "path_manual"),
            ("thumbnail", "3dboxes", ".png", "3D Box", "box3d_path", "box3d_url", None),
            ("marquee", "marquees", ".png", "Marquee", "marquee_path", "marquee_url", None),
            ("boxback", "backcovers", ".png", "Back Cover", "box2d_back_path", "box2d_back_url", None),
            ("fanart", "fanart", ".png", "Fanart", "fanart_path", "fanart_url", None),
            ("miximage", "miximages", ".png", "Mix Image", "miximage_path", "miximage_url", None),
            ("physicalmedia", "physicalmedia", ".png", "Physical Media", "physical_path", "physical_url", None),
            ("titlescreen", "titlescreens", ".png", "Title Screen", "title_screen_path", "title_screen_url", None),
            ("screenshot", "screenshots", ".png", "Screenshot", "screenshot_path", "screenshot_url", None),
        ]

        self.missing_media_cache = {}
        self.active_downloads = set()
        self.current_options = {}
        self.selected_roms = set()
        self.selected_platform = None
        self.roms_cache = {}
        self.seen_rom_ids = set()
        self.synced_rom_paths = set()
        self.keyboard_mark_anchor = None
        self.abort_event = threading.Event()
        self.platforms = None
        self.dry_run = False
        self.local_only = False
        self.total_bytes_to_download = 0
        self.games_processed_count = 0

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r") as f:
                    self.roms_cache = json.load(f)

                # Populate the seen set from the disk cache
                for p_roms in self.roms_cache.values():
                    for rom in p_roms:
                        self.seen_rom_ids.add(str(rom["id"]))

                self.cache_complete = True
            except Exception:
                self.cache_complete = False
        else:
            self.cache_complete = False

    def compose(self):
        """Setups the look and feel of the application"""
        yield Header()

        with Horizontal(id="main-layout"):
            with Vertical(id="platform-sidebar"):
                yield Label("Platforms", id="sidebar-title")
                yield ListView(id="sidebar-list")

            with Vertical(id="platform-info"):
                yield RommTable(id="roms-table", cursor_type="row")

                with Horizontal(id="media-filter-container"):
                    yield Checkbox("Covers", id="chk-sync-covers", value=True)
                    yield Checkbox("3dboxes", id="chk-sync-3dboxes", value=True)
                    yield Checkbox("Videos", id="chk-sync-videos", value=True)
                    yield Checkbox("Titlescreens", id="chk-sync-titlescreens", value=True)
                    yield Checkbox("Miximages", id="chk-sync-miximages", value=True)
                    yield Checkbox("Fanart", id="chk-sync-fanart", value=True)
                    yield Checkbox("Screenshots", id="chk-sync-screenshots", value=True)
                    yield Checkbox("Backcovers", id="chk-sync-backcovers", value=True)
                    yield Checkbox("Marquees", id="chk-sync-marquees", value=True)
                    yield Checkbox("Physicalmedia", id="chk-sync-physicalmedia", value=True)
                    yield Checkbox("Manuals", id="chk-sync-manuals", value=True)

                with Horizontal(classes="button-group"):
                    yield Button("Sync Metadata", id="btn-sync-meta", variant="primary")
                    yield Button("Generate ES Systems", id="btn-gen-systems")
                    yield Button("Toggle All", id="btn-toggle-all")
                    yield Select(
                        options=[
                            ("Update Missing Only", "missing"),
                            ("Full Metadata Refresh", "full"),
                            ("Fast Index (No Media)", "structure_only")],
                        value="missing",
                        id="sync-mode-select",
                        allow_blank=False
                    )
                    yield Checkbox("Dry Run", id="chk-dry-run", value=False)
                    yield Checkbox("Local Only", id="chk-local-only", value=False)

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

        self.fetch_platforms_to_sidebar()

    @work(thread=True)
    def fetch_platforms_to_sidebar(self) -> None:
        """Background agent pulling functional slug listings from the server."""
        try:
            r = requests.get(f"{self.romm_url}/api/platforms", headers=self.headers, timeout=5)
            r.raise_for_status()

            sorted_raw = sorted(r.json(), key=lambda x: x.get("fs_slug") or "")
            self.platforms = {str(p.get("id")): p for p in sorted_raw if p.get("id") is not None}

            def update_ui():
                p_list = self.query_one("#sidebar-list", ListView)

                for item in p_list.query(ListItem):
                    item.remove()

                p_list.append(ListItem(Label("All Platforms"), name="all"))

                # Tally up how many times each display name occurs
                name_counts = {}
                for p in self.platforms.values():
                    base_name = p.get("name") or p.get("fs_slug") or "Unknown"
                    name_counts[base_name] = name_counts.get(base_name, 0) + 1

                # Build the list dynamically
                for p in self.platforms.values():
                    fs_slug = p.get("fs_slug")
                    if not fs_slug:
                        continue

                    base_name = p.get("name", fs_slug)

                    # If this name exists more than once, append the fs_slug to disambiguate
                    if name_counts.get(base_name, 0) > 1:
                        display_name = f"{base_name} ({fs_slug})"
                    else:
                        display_name = base_name

                    p_list.append(ListItem(Label(display_name), name=str(p.get("id"))))

            self.call_from_thread(update_ui)
            if not self.cache_complete or not self.skip_cache_refresh:
                self.preload_all_roms()

        except Exception as e:
            self.call_from_thread(self.log_msg, f"[bold red]Failed to fetch platforms from server: {e}[/]")

    @work(thread=True)
    def preload_all_roms(self) -> None:
        """Background process to fetch ALL ROMs, update the live UI, and save to disk."""
        self.call_from_thread(lambda: setattr(self, "sub_title", "Background Sync: Fetching catalog..."))

        limit, offset = 1000, 0
        total_cached = 0
        fresh_cache = {}

        try:
            while True:
                self.log_msg(f"Gathering up to {limit} ROMs starting at offset {offset}")

                params = {"limit": limit, "offset": offset}
                res = requests.get(f"{self.romm_url}/api/roms", headers=self.headers, params=params, timeout=100)
                res.raise_for_status()

                items = res.json().get("items", [])
                if not items:
                    break

                # Group incoming items by platform_id
                new_roms_by_platform = {}
                for item in items:
                    p_id = str(item.get("platform_id"))
                    r_id = str(item.get("id"))

                    # I don't want to save off the entire JSON object, that's too large.
                    # So let's just grab what we need and save that instead.
                    slim_item = {
                        "id": item.get("id"),
                        "platform_id": item.get("platform_id"),
                        "fs_name": item.get("fs_name"),
                        "name": item.get("name"),
                        "summary": item.get("summary"),
                        "ss_metadata": item.get("ss_metadata", {}),
                        "igdb_metadata": item.get("igdb_metadata", {}),
                        "metadatum": item.get("metadatum", {}),
                        "merged_screenshots": item.get("merged_screenshots", [])
                    }

                    # These are useful later for determining how to download things later.
                    for _, _, _, _, path_key, url_key, root_key in self.media_mappings:
                        if path_key in item: slim_item[path_key] = item[path_key]
                        if url_key in item: slim_item[url_key] = item[url_key]
                        if root_key and root_key in item: slim_item[root_key] = item[root_key]

                    if p_id not in fresh_cache:
                        fresh_cache[p_id] = []
                    fresh_cache[p_id].append(slim_item)

                    # If this is a brand new ROM we haven't seen in our current cache:
                    if r_id not in self.seen_rom_ids:
                        self.seen_rom_ids.add(r_id)

                        # Add it to the live cache so it survives platform switching
                        if p_id not in self.roms_cache:
                            self.roms_cache[p_id] = []
                        self.roms_cache[p_id].append(slim_item)

                        # Queue it to be pushed to the UI if the user is looking at this platform
                        if p_id not in new_roms_by_platform:
                            new_roms_by_platform[p_id] = []
                        new_roms_by_platform[p_id].append(slim_item)

                total_cached += len(items)
                self.call_from_thread(lambda: setattr(self, "sub_title", f"Background Sync: {total_cached} ROMs verified..."))

                # Append brand new games instantly so the user can see the updates.
                if self.selected_platform is not None and str(self.selected_platform) in new_roms_by_platform:
                    self.call_from_thread(
                        self.append_roms_to_table,
                        str(self.selected_platform),
                        new_roms_by_platform[str(self.selected_platform)]
                    )

                if len(items) < limit:
                    break

                offset += limit

            # Fetch complete. Overwrite the old memory cache with our fresh data
            self.roms_cache = fresh_cache

            # Rebuild seen_rom_ids to match exactly what is in fresh_cache.
            # There might be ROMs in this list that don't exist anymore.
            self.seen_rom_ids.clear()
            for p_roms in self.roms_cache.values():
                for rom in p_roms:
                    self.seen_rom_ids.add(str(rom["id"]))

            # Write to disk with indentation so it's readable
            with open(CACHE_FILE, "w") as f:
                json.dump(self.roms_cache, f, indent=2)

            # Clean up stale rows from the UI if a game was deleted on the server
            if self.selected_platform is not None:
                def remove_stale_rows():
                    table = self.query_one("#roms-table", DataTable)
                    valid_ids = {str(r["id"]) for r in self.roms_cache.get(str(self.selected_platform), [])}
                    stale_keys = [rk for rk in table.rows.keys() if rk.value and rk.value not in valid_ids and rk.value != "loading"]
                    for sk in stale_keys:
                        table.remove_row(sk)

                self.call_from_thread(remove_stale_rows)

            self.cache_complete = True
            self.call_from_thread(lambda: setattr(self, "sub_title", "Cache Up-to-date"))

        except Exception as err:
            self.log_msg(f"Cache Error: {err}")
            self.notify(f"Cache Error: {err}", severity="error")
            self.call_from_thread(lambda: setattr(self, "sub_title", f"Failed to gather ROMs"))

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

    def action_cursor_down(self) -> None:
        """Routes 'j' keypresses to move the cursor down on the currently focused widget."""
        focused = self.focused
        # If the highlighted widget has a native cursor-down method, trigger it!
        if focused and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Routes 'k' keypresses to move the cursor up on the currently focused widget."""
        focused = self.focused
        # If the highlighted widget has a native cursor-up method, trigger it!
        if focused and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    def _get_row_key_(self, table, index: int):
        """Maps a row index to its row_key."""
        try:
            return table.coordinate_to_cell_key((index, 0)).row_key
        except Exception:
            raise AttributeError(f"Unable to resolve row-key reference at index {index}.")

    def action_toggle_mark(self) -> None:
        """Handles manual m...m block selection ranges via the keyboard."""
        table = self.query_one("#roms-table", DataTable)
        current_idx = table.cursor_row

        if current_idx is None or current_idx < 0:
            return

        try:
            row_key = self._get_row_key_(table, current_idx)
            rom_id = row_key.value
        except Exception as err:
            self.log_msg(f"[bold red]ERROR targeting current row index:[/] {str(err)}")
            return

        if not rom_id or rom_id == "loading":
            return

        # Place anchor and save the index
        if self.keyboard_mark_anchor is None:
            self.keyboard_mark_anchor = current_idx
            self.log_msg(f"Anchor dropped at row {current_idx + 1}. Move cursor with j/k and press 'm' again to fill block.")

            # Place visual anchor indicator in the selection column
            table.update_cell(row_key, "col_select", Text.from_markup("[yellow]⚓[/]"))

        # Finish selection
        else:
            # They could start and end the anchor in either direction,
            # so just get the min and max from each index.
            start_idx = min(self.keyboard_mark_anchor, current_idx)
            end_idx = max(self.keyboard_mark_anchor, current_idx)

            # Read anchor status to decide if we are mass-checking or mass-unchecking
            try:
                anchor_key = self._get_row_key_(table, self.keyboard_mark_anchor)
                mass_selecting = anchor_key.value not in self.selected_roms
            except Exception:
                mass_selecting = True

            # Sweep across the rows between both anchors in memory
            for idx in range(start_idx, end_idx + 1):
                try:
                    r_key = self._get_row_key_(table, idx)
                    r_id = r_key.value

                    if mass_selecting:
                        self.selected_roms.add(r_id)
                        marker = "[bold green]✔[/]"
                    else:
                        self.selected_roms.discard(r_id)
                        marker = "  "

                    table.update_cell(r_key, "col_select", Text.from_markup(marker))
                except Exception as e:
                    continue

            self.log_msg(f"Committed range block from row {self.keyboard_mark_anchor + 1} to {current_idx + 1}!")

            self.keyboard_mark_anchor = None

    def action_show_stats(self) -> None:
        """Calculates and displays a metric summary for the currently selected platform."""
        if not self.selected_platform:
            self.notify("Please select a platform from the sidebar first!", severity="warning")
            return

        is_all = self.selected_platform == "all"

        # Dynamically fetch our target ROMs and title
        if is_all:
            platform_name = "All Platforms"
            cached_roms = [rom for p_list in self.roms_cache.values() for rom in p_list]
        else:
            platform_slug = self.platforms[self.selected_platform].get("fs_slug", "Unknown")
            platform_name = self.platforms[self.selected_platform].get("name", platform_slug)
            cached_roms = self.roms_cache.get(str(self.selected_platform), [])

        total_roms = len(cached_roms)

        if total_roms == 0:
            self.notify(f"No ROMs loaded yet for {platform_name}.", severity="warning")
            return

        unindexed = 0
        visible_local = 0
        visible_cloud = 0
        hidden = 0

        missing_counts = {friendly_name: 0 for _, _, _, friendly_name, _, _, _ in self.media_mappings}

        local_files_cache = {}
        ghost_files_cache = {}
        needed_pids = {str(r.get("platform_id")) for r in cached_roms if r.get("platform_id")}

        for pid in needed_pids:
            if pid not in self.platforms: continue
            slug = self.platforms[pid].get("fs_slug")
            local_files_cache[pid] = self.gather_installed_roms(slug)
            ghost_files_cache[pid] = self.gather_rom_indicators(slug)

        for rom in cached_roms:
            fs_name = rom.get("fs_name")
            p_id = str(rom.get("platform_id"))

            if not fs_name or p_id not in self.platforms:
                continue

            platform_slug = self.platforms[p_id].get("fs_slug")

            # Tally ES-DE visibility
            if fs_name in local_files_cache.get(p_id, set()):
                visible_local += 1
            elif fs_name in ghost_files_cache.get(p_id, set()):
                visible_cloud += 1
            else:
                hidden += 1

            # Tally unindexed
            fs_path_str = f"{platform_slug}:./{fs_name}"
            if fs_path_str not in self.synced_rom_paths:
                unindexed += 1

            # Tally specific missing metadata
            rom_id = str(rom["id"])
            missing_folders_for_rom = self.missing_media_cache.get(rom_id, [])

            for _, es_folder, _, friendly_name, _, _, _ in self.media_mappings:
                if es_folder in missing_folders_for_rom:
                    missing_counts[friendly_name] += 1

        # Format the final output markup using Rich text formatting
        markup = (
            f"[bold cyan]Total Tracked ROMs:[/] {total_roms}\n"
            f"[bold cyan]Unindexed in ES-DE:[/] [yellow]{unindexed}[/]\n\n"
            f"[bold underline]ES-DE Visibility[/]\n"
            f" 📁 Local (Downloaded): [green]{visible_local}[/]\n"
            f" ☁️ Cloud (Ghost File): [blue]{visible_cloud}[/]\n"
            f" 👻 Hidden (Not Synced): [magenta]{hidden}[/]\n\n"
            f"[bold underline]Missing Metadata Assets[/]\n"
        )

        for friendly_name, count in missing_counts.items():
            if count > 0:
                markup += f" 📦 {friendly_name}: [red]{count}[/]\n"
            else:
                markup += f" ✨ {friendly_name}: [green]Perfect![/]\n"

        self.push_screen(StatsModal(platform_name, markup))

    # ==================== #
    #        Events        #
    # ==================== #

    @on(DataTable.RowSelected, "#roms-table")
    def handle_keyboard_enter(self, event: DataTable.RowSelected) -> None:
        """Allows standard keyboard 'Enter' or Double-Clicks to toggle single items."""
        rom_id = event.row_key.value
        if not rom_id or rom_id == "loading":
            return

        table = self.query_one("#roms-table", DataTable)

        if rom_id in self.selected_roms:
            self.selected_roms.remove(rom_id)
            new_marker = "  "
        else:
            self.selected_roms.add(rom_id)
            new_marker = "[bold green]✔[/]"

        table.update_cell(event.row_key, "col_select", Text.from_markup(new_marker))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # TODO: This causes on_list_view_selected to be called twice.
        self.on_list_view_selected(event)

    def update_synced_rom_paths(self, platform_slug):
        es_system = self.transform_romm_name_to_esde_name(platform_slug)
        xml_path = ES_DE_DIR / "gamelists" / es_system / "gamelist.xml"
        if xml_path.exists():
            try:
                tree = ET.parse(xml_path)
                for game in tree.getroot().findall('game'):
                    path_node = game.find('path')
                    if path_node is not None and path_node.text:
                        # Use namespacing here too so append_roms works identically!
                        self.synced_rom_paths.add(f"{platform_slug}:{path_node.text}")
            except Exception as e:
                self.log_msg(f"[bold yellow]Failed to read local XML: {e}[/]")
                self.notify(f"Failed to read local XML: {e}", severity="error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Triggers row redrawing when platform is highlighted."""
        if not event.item:
            return

        platform_id = getattr(event.item, "name", None)
        if not platform_id:
            return

        platform_id = str(platform_id)
        self.selected_platform = platform_id
        self.synced_rom_paths = set()

        if platform_id == "all":
            self.log_msg("Loading platform: [cyan]All Platforms[/]")

            # Build a global synced_paths lookup safely namespaced by platform
            for p in self.platforms.values():
                platform_slug = p.get("fs_slug", "")
                if not platform_slug: continue
                self.update_synced_rom_paths(platform_slug)
        else:
            platform_slug = self.platforms[platform_id].get("fs_slug", platform_id)
            self.log_msg(f"Loading platform: [cyan]{platform_slug}[/]")
            self.update_synced_rom_paths(platform_slug)

        self.populate_roms(platform_id)

    def populate_roms(self, platform_id: str) -> None:
        """Initializes the table structure and triggers an immediate read from the memory cache."""
        table = self.query_one("#roms-table", DataTable)
        table.clear(columns=True)

        if not platform_id:
            table.add_column("Status Message")
            table.add_row(f"Platform ID of {platform_id} is not supported!")
            return

        # Setup columns dynamically
        table.add_column("Sel", key="col_select", width=4)
        if platform_id == "all":
            table.add_column("Platform", key="col_platform", width=15)
        table.add_column("Game Title", key="col_title")
        table.add_column("ES-DE Visibility", key="col_visibility", width=18)
        table.add_column("Metadata Status", key="col_meta", width=18)

        # Pull whatever we currently have in the cache
        if platform_id == "all":
            # Flatten all cache lists into one massive list
            cached_roms = [rom for p_list in self.roms_cache.values() for rom in p_list]
        else:
            cached_roms = self.roms_cache.get(str(platform_id), [])

        if cached_roms:
            # Kick off the async streaming worker instead of blocking the thread!
            self.stream_roms_to_table(str(platform_id), cached_roms)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox = event.checkbox
        if checkbox.id == "chk-dry-run":
            self.dry_run = checkbox.value
        elif checkbox.id == "chk-local-only":
            self.local_only = checkbox.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Centralized event routing framework supporting dynamic morphing action buttons."""
        btn = event.button

        if str(btn.label) == "Stop":
            self._stop_button_(btn)
        elif btn.id == "btn-sync-meta":
            self._sync_meta_data_button_(btn)
        elif btn.id == "btn-toggle-all":
            if not self.selected_platform:
                return

            self._toggle_all_button_()
        elif btn.id == "btn-gen-systems":
            self._generate_es_systems_xml_()

    def _stop_button_(self, btn) -> None:
        self.abort_event.set()
        btn.disabled = True

    def _sync_meta_data_button_(self, btn):
        # Morph button state into the emergency kill switch
        btn.label = "Stop"
        btn.variant = "warning"

        sync_mode = self.query_one("#sync-mode-select", Select).value

        enabled_folders = set()
        if self.query_one("#chk-sync-covers", Checkbox).value:
            enabled_folders.add("covers")
        if self.query_one("#chk-sync-3dboxes", Checkbox).value:
            enabled_folders.add("3dboxes")
        if self.query_one("#chk-sync-videos", Checkbox).value:
            enabled_folders.add("videos")
        if self.query_one("#chk-sync-titlescreens", Checkbox).value:
            enabled_folders.add("titlescreens")
        if self.query_one("#chk-sync-miximages", Checkbox).value:
            enabled_folders.add("miximages")
        if self.query_one("#chk-sync-fanart", Checkbox).value:
            enabled_folders.add("fanart")
        if self.query_one("#chk-sync-screenshots", Checkbox).value:
            enabled_folders.add("screenshots")
        if self.query_one("#chk-sync-backcovers", Checkbox).value:
            enabled_folders.add("backcovers")
        if self.query_one("#chk-sync-marquees", Checkbox).value:
            enabled_folders.add("marquees")
        if self.query_one("#chk-sync-physicalmedia", Checkbox).value:
            enabled_folders.add("physicalmedia")
        if self.query_one("#chk-sync-manuals", Checkbox).value:
            enabled_folders.add("manuals")

        self.sync_es_metadata(
            self.selected_platform,
            sync_mode=sync_mode,
            allowed_media=list(self.selected_roms),
            enabled_folders=enabled_folders
        )

    def set_or_update_tag(self, parent_node, tag_name, text_value, sync_mode):
        if not text_value: return
        node = parent_node.find(tag_name)
        if node is None:
            ET.SubElement(parent_node, tag_name).text = str(text_value)
        elif sync_mode == "full" or not node.text:
            node.text = str(text_value)

    def resolve_remote_size(self, asset_url: str, fallback_bytes: int) -> tuple[int, bool]:
        try:
            req_headers = self.headers if self.romm_url in asset_url else {}
            res = requests.head(asset_url, headers=req_headers, timeout=2.0, allow_redirects=True)
            if res.status_code == 200 and "Content-Length" in res.headers:
                return int(res.headers["Content-Length"]), False
        except Exception:
            pass
        return fallback_bytes, True

    def download_media(self, url, target_dir, filename, sync_mode):
        if not url: return False
        if url.startswith("/"): url = self.romm_url + url
        media_path = target_dir / filename

        if sync_mode != "full" and media_path.exists():
            return True

        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            req_headers = {"Authorization": f"Bearer {self.api_key}"} if self.romm_url in url else {}
            res = requests.get(url, headers=req_headers, stream=True, timeout=10)
            res.raise_for_status()
            with open(media_path, 'wb') as f:
                for chunk in res.iter_content(chunk_size=8192):
                    if self.abort_event.is_set():
                        raise InterruptedError("Cancelled")
                    f.write(chunk)
            return True
        except Exception:
            if media_path.exists(): media_path.unlink()
            return False

    def remove_games_from_romm_list(self, platform_roms, root, platform_slug):
        valid_fs_paths = {f"./{rom.get('fs_name')}" for rom in platform_roms if rom.get("fs_name")}
        valid_filenames = {rom.get('fs_name') for rom in platform_roms if rom.get("fs_name")}

        games_to_remove = [g for g in root.findall('game') if g.find('path') is not None and g.find('path').text not in valid_fs_paths]
        for game in games_to_remove:
            root.remove(game)

        cloud_dir = ROM_LIST_DIR / platform_slug
        if cloud_dir.exists():
            for fake_file in cloud_dir.iterdir():
                if fake_file.is_file() and fake_file.name != "systeminfo.txt" and fake_file.name not in valid_filenames:
                    fake_file.unlink()

    def process_rom(self, rom, root, existing_games, sync_mode, platform_slug, enabled_folders, modal):
        rom_id = str(rom["id"])
        game_name = rom.get('name', 'Unknown')
        es_system = self.transform_romm_name_to_esde_name(platform_slug)
        es_media_dir = ES_DE_DIR / "downloaded_media" / es_system

        self.update_inline_status(rom_id, "[yellow]Simulating...[/]" if self.dry_run else "[cyan]⌛ Specs...[/]")

        # The bulk rom already holds the full metadata payload!
        fs_name = rom.get("fs_name", "")
        fs_path_str = f"./{fs_name}"
        true_stem = Path(fs_name).stem
        ss_meta = rom.get("ss_metadata", {}) or {}

        if self.dry_run:
            self.dry_run_log_msg(f"\n[bold white]Processing Game:[/] {game_name} ({fs_name})")

        if fs_name and not self.dry_run:
            fake_rom_file = ROM_LIST_DIR / platform_slug / fs_name
            real_rom_file = ROMS_DIR / platform_slug / fs_name
            if not fake_rom_file.exists() and not real_rom_file.exists():
                fake_rom_file.parent.mkdir(parents=True, exist_ok=True)
                fake_rom_file.touch()

        if fs_path_str in existing_games:
            game_node = existing_games[fs_path_str]
        else:
            game_node = ET.SubElement(root, "game")
            self.set_or_update_tag(game_node, "path", fs_path_str, sync_mode)

        media_tags_to_strip = ["image", "video", "thumbnail", "marquee", "boxback", "fanart", "miximage", "physicalmedia", "titlescreen", "screenshot", "manual"]
        for tag_to_strip in media_tags_to_strip:
            old_node = game_node.find(tag_to_strip)
            if old_node is not None: game_node.remove(old_node)

        self.set_or_update_tag(game_node, "name", rom.get("name", ""), sync_mode)
        self.set_or_update_tag(game_node, "desc", rom.get("summary", ""), sync_mode)

        igdb_meta = rom.get("igdb_metadata", {}) or {}
        metadatum = rom.get("metadatum", {}) or {}

        if igdb_meta and igdb_meta.get("aggregated_rating"):
            rating_val = float(igdb_meta.get("aggregated_rating")) / 100.0
            self.set_or_update_tag(game_node, "rating", str(round(rating_val, 2)), sync_mode)

        release_ts = metadatum.get("first_release_date")
        if release_ts:
            dt = datetime.fromtimestamp(release_ts / 1000.0)
            self.set_or_update_tag(game_node, "releasedate", dt.strftime("%Y%m%dT%H%M%S"), sync_mode)

        companies = metadatum.get("companies", [])
        if companies:
            self.set_or_update_tag(game_node, "developer", companies[-1] if len(companies) > 1 else companies[0], sync_mode)
            self.set_or_update_tag(game_node, "publisher", companies[0], sync_mode)

        genres = metadatum.get("genres", [])
        if genres: self.set_or_update_tag(game_node, "genre", " / ".join(genres), sync_mode)
        if metadatum.get("player_count"): self.set_or_update_tag(game_node, "players", metadatum.get("player_count"), sync_mode)

        if sync_mode == "structure_only":
            self.update_inline_status(rom_id, "[green]Indexed[/]")
            if self.dry_run:
                self.dry_run_log_msg("  [green]✓ Structural Indexing Mode[/] -> Skipping asset downloading.")
            else:
                self.synced_rom_paths.add(f"{platform_slug}:{fs_path_str}")
            return

        missing_folders = []

        for xml_tag, es_folder, default_ext, friendly_name, path_key, url_key, root_key in self.media_mappings:
            target_url = self.get_optimal_url(path_key, url_key, root_key, ss_meta, rom)

            if target_url:
                ext = Path(urllib.parse.urlparse(target_url).path).suffix or default_ext
                filename = f"{true_stem}{ext}"
                target_path = es_media_dir / es_folder / filename
                is_missing = sync_mode == "full" or not target_path.exists()

                if enabled_folders and es_folder not in enabled_folders:
                    self.dry_run_log_msg(f"  [dim]◯ Skipped[/] -> Folder {es_folder} was not enabled.")
                    continue

                is_local_source = self.romm_url in target_url

                if self.local_only and is_missing and not is_local_source:
                    if self.dry_run:
                        self.dry_run_log_msg(f"  [dim]◯ Skipped[/] -> External asset source for {friendly_name} blocked because it was external from ROMM server ({target_url}).")
                    continue

                if self.dry_run:
                    if is_missing:
                        base_fallback = 4194304 if es_folder == "videos" else 256000
                        asset_bytes, is_est = self.resolve_remote_size(target_url, base_fallback)
                        self.total_bytes_to_download += asset_bytes

                        src_type = "Local Romm" if is_local_source else "External"
                        est_flag = " [dim](est.)[/]" if is_est else ""
                        self.dry_run_log_msg(f"  [yellow]✕ Missing[/] -> ({src_type}) Would download {friendly_name} ({self.format_size(asset_bytes)}{est_flag})")
                    else:
                        self.dry_run_log_msg(f"  [green]✓ Verified[/] -> Local {friendly_name} already exists on disk.")
                else:
                    if friendly_name in ("Cover", "Screenshot", "Video"):
                        src_indicator = "⚡" if is_local_source else "⬇"
                        self.update_inline_status(rom_id, f"[cyan]{src_indicator} {friendly_name}...[/]")

                    success = self.download_media(target_url, es_media_dir / es_folder, filename, sync_mode)
                    if success:
                        self.set_or_update_tag(game_node, xml_tag, f"../../downloaded_media/{es_system}/{es_folder}/{filename}", sync_mode)

                if not target_path.exists():
                    missing_folders.append(es_folder)

        if not self.dry_run:
            self.synced_rom_paths.add(fs_path_str)
            self.missing_media_cache[rom_id] = list(missing_folders)

        if self.dry_run:
            pass
        elif sync_mode == "structure_only":
            pass
        else:
            self.update_inline_status(rom_id, "[yellow]Missing Media[/]" if missing_folders else "[green]Fully Synced[/]")

        if self.dry_run:
            if modal is not None:
                summary_markup = (
                    f"[bold]Target Directory:[/] ~/ES-DE/gamelists/{es_system}/gamelist.xml\n"
                    f"[bold]Queue Count:[/] {self.games_processed_count} Games Selected\n"
                    f"[bold]Estimated Network Footprint:[/] [bold yellow]{self.format_size(self.total_bytes_to_download)}[/]"
                )
                modal_summary = modal.query_one("#modal-summary", Label)
                self.call_from_thread(modal_summary.update, Text.from_markup(summary_markup))

    # Helper function to update the ROM cell of its status.
    def update_inline_status(self, r_id: str, markup_text: str):
        def update_cell():
            try:
                table = self.query_one("#roms-table", DataTable)
                table.update_cell(r_id, "col_meta", Text.from_markup(markup_text))
            except Exception as e:
                self.log_msg(f"Could not update the Metadata Status column for rom: {r_id}: {str(e)}")
        self.call_from_thread(update_cell)

    @work(thread=True)
    def sync_es_metadata(self, platform_id: int, sync_mode: str = "missing", allowed_media: list | None = None, enabled_folders: set | None = None):
        """
        This is the main functions that will get the ROMs synced up with its metadata.

        It will first try to use the local metadata found in the ROMM server itself. If not found, it will use the URLs that ROMM provides.

        NOTE that if those URLs are provided, it will probably be hammering the limits of the API provided.
        """
        self.abort_event.clear()

        allowed_media_set = {str(m_id) for m_id in allowed_media}

        platform_slug = self.platforms[platform_id].get("fs_slug", "")

        es_system = self.transform_romm_name_to_esde_name(platform_slug)
        es_gamelists_dir = ES_DE_DIR / "gamelists" / es_system
        xml_file_path = es_gamelists_dir / "gamelist.xml"

        if self.dry_run:
            modal = SimulationModal()
            self.call_from_thread(self.push_screen, modal)
            time.sleep(0.1)
        else:
            modal = None

        # Print what mode we are in
        mode_text = "FAST STRUCTURAL" if sync_mode == "structure_only" else ("FULL REFRESH" if sync_mode == "full" else "MISSING ONLY")
        self.dry_run_log_msg(f"[bold cyan]Starting local-first metadata engine for {platform_slug} ({mode_text})...[/]")

        platform_roms = self.fetch_roms(platform_id, "btn-sync-meta")
        if platform_roms is None:
            return

        if xml_file_path.exists():
            try:
                root = ET.parse(xml_file_path).getroot()
            except Exception:
                root = ET.Element("gameList")
        else:
            root = ET.Element("gameList")

        if not self.dry_run:
            es_gamelists_dir.mkdir(parents=True, exist_ok=True)
            self.remove_games_from_romm_list(platform_roms, root, platform_slug)

        existing_games = {g.find('path').text: g for g in root.findall('game') if g.find('path') is not None and g.find('path').text}

        self.total_bytes_to_download = 0
        self.games_processed_count = 0

        for idx, rom in enumerate(platform_roms, start=1):
            rom_id = str(rom["id"])

            if allowed_media_set and rom_id not in allowed_media_set:
                continue

            if self.abort_event.is_set():
                self.dry_run_log_msg("[bold red]Stopping ROM metadata processing.[/]")
                break

            self.games_processed_count += 1
            self.process_rom(rom, root, existing_games, sync_mode, platform_slug, enabled_folders, modal)

        ET.indent(root, space="  ", level=0)

        if not self.dry_run:
            self.dry_run_log_msg("Writing streamlined index tree layout structure...")
            tree = ET.ElementTree(root)
            tree.write(xml_file_path, encoding="utf-8", xml_declaration=True)
            self.dry_run_log_msg(f"[bold green]Success![/] Committed profile data changes safely to {xml_file_path}")
            if not self.abort_event.is_set():
                self.selected_roms.clear()
        else:
            self.dry_run_log_msg(f"\n[bold green]✔ Simulation Complete![/] Total calculated network bytes: [bold yellow]{self.format_size(self.total_bytes_to_download)}[/]")
            for r_id in allowed_media_set:
                self.update_inline_status(r_id, "[yellow]Previewed[/]")

        def restore_sync_ui():
            sync_btn = self.query_one("#btn-sync-meta", Button)
            sync_btn.label = "Sync Metadata"
            sync_btn.variant = "primary"
            sync_btn.disabled = False

        self.call_from_thread(self.populate_roms, platform_id)
        self.call_from_thread(restore_sync_ui)

    def fetch_roms(self, platform_id: int, button_id: str = None) -> list | None:
        """A unified, paginated API engine that safely pulls catalog datasets without locking the UI."""

        # Check if we already have this platform's ROMs in the cache
        if platform_id in self.roms_cache:
            self.log_msg("Loading ROM listing from local memory cache")
            return self.roms_cache[platform_id]

        # Not in the cache, let's get the listing from the ROMM server
        self.log_msg("Fetching ROM listings from ROMM server...")
        roms_data = []
        limit, offset = 1000, 0

        try:
            while True:
                params = {"platform_ids": platform_id, "limit": limit, "offset": offset}
                res = requests.get(f"{self.romm_url}/api/roms", headers=self.headers, params=params, timeout=10)
                res.raise_for_status()

                data = res.json()
                items = data.get("items", [])
                roms_data.extend(items)

                if len(items) < limit:
                    break
                offset += limit

            self.log_msg(f"Loaded {len(roms_data)} raw records from server for tracking.")

            # Save the final payload to the cache before returning it
            self.roms_cache[platform_id] = roms_data

            return roms_data

        except Exception as err:
            self.log_msg(f"[bold red]Failed query catalog sync execution: {err}[/]")
            # If a button was passed in, automatically restore it so the UI doesn't freeze
            if button_id:
                self.call_from_thread(lambda: setattr(self.query_one(f"#{button_id}"), 'disabled', False))
            return None

    def _toggle_all_button_(self):
        table = self.query_one("#roms-table", DataTable)
        visible_row_keys = list(table.rows.keys())
        visible_ids = {rk.value for rk in visible_row_keys if rk.value and rk.value != "loading"}

        if not visible_ids:
            return

        if visible_ids.issubset(self.selected_roms):
            for r_id in visible_ids:
                self.selected_roms.discard(r_id)
            mass_selecting = False
        else:
            for r_id in visible_ids:
                self.selected_roms.add(r_id)
            mass_selecting = True

        for row_key in visible_row_keys:
            if row_key.value == "loading":
                continue
            marker = "[bold green]✔[/]" if mass_selecting else "  "
            table.update_cell(row_key, "col_select", Text.from_markup(marker))

    def _generate_es_systems_xml_(self) -> None:
        """Dynamically builds ES-DE's es_systems.xml based on the server's platform list."""
        if not self.platforms:
            self.notify("Platforms not loaded from server yet!", severity="warning")
            return

        extension_map = {
            "n3ds": ".3ds .cci .cxi .zip .7z",
            "atari2600": ".a26 .bin .zip .7z",
            "atari5200": ".a52 .bin .zip .7z",
            "atari7800": ".a78 .bin .zip .7z",
            "cdimono1": ".chd .cue .iso .zip .7z",
            "dreamcast": ".chd .cdi .gdi .zip .7z",
            "famicom": ".nes .zip .7z",
            "gamegear": ".gg .zip .7z",
            "gb": ".gb .zip .7z",
            "gba": ".gba .zip .7z",
            "gbc": ".gbc .zip .7z",
            "gc": ".rvz .iso .gcm .gcz .ciso .zip .7z",
            "genesis": ".md .smd .gen .bin .zip .7z",
            "laserdisc": ".txt .zip .7z",
            "mame": ".zip .7z",
            "mastersystem": ".sms .bin .zip .7z",
            "n64": ".z64 .n64 .v64 .zip .7z",
            "naomi": ".bin .lst .dat .zip .7z .chd",
            "nds": ".nds .zip .7z",
            "neogeo": ".zip .7z",
            "nes": ".nes .zip .7z",
            "ps2": ".iso .bin .chd .cso .gz .zip .7z",
            "psp": ".iso .cso .pbp .chd .zip .7z",
            "psvita": ".vpk .zip .7z",
            "psx": ".cue .chd .m3u .pbp .iso .zip .7z",
            "psx.old": ".cue .chd .m3u .pbp .iso .zip .7z",
            "saturn": ".cue .chd .m3u .iso .zip .7z",
            "sega32x": ".32x .bin .zip .7z",
            "snes": ".smc .sfc .fig .swc .zip .7z",
            "tg16": ".pce .zip .7z",
            "wii": ".rvz .wbfs .iso .nkit.iso .zip .7z",
            "wiiu": ".wua .wud .wux .rpx .zip .7z",
            "xbox": ".iso .xiso .xiso.iso .zip .7z"
        }

        root = ET.Element("systemList")

        for platform_id, p_data in self.platforms.items():
            fs_slug = p_data.get("fs_slug")
            if not fs_slug:
                continue

            esde_name = self.transform_romm_name_to_esde_name(fs_slug)

            system_node = ET.SubElement(root, "system")

            ET.SubElement(system_node, "name").text = esde_name
            ET.SubElement(system_node, "fullname").text = p_data.get("name") or fs_slug
            ET.SubElement(system_node, "path").text = f"~/.local/share/romm_bridge/rom_list/{fs_slug}"
            ET.SubElement(system_node, "extension").text = extension_map.get(esde_name, ".zip .7z")
            ET.SubElement(system_node, "command").text = f"romm_start.sh {fs_slug} %ROM%"

            platform_scrape = esde_name
            if esde_name == "tg16": platform_scrape = "pcengine"
            elif esde_name == "famicom": platform_scrape = "nes"
            elif esde_name == "laserdisc": platform_scrape = "daphne"

            ET.SubElement(system_node, "platform").text = platform_scrape
            ET.SubElement(system_node, "theme").text = esde_name

        # Format it to look pretty
        xml_string = ET.tostring(root, 'utf-8')
        parsed_xml = minidom.parseString(xml_string)
        pretty_xml = '\n'.join([line for line in parsed_xml.toprettyxml(indent="    ").split('\n') if line.strip()])

        # Save to Disk
        target_dir = ES_DE_DIR / "custom_systems"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "es_systems.xml"

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(pretty_xml)

            self.log_msg(f"[bold green]Successfully generated {file_path} for {len(self.platforms)} platforms![/]")
            self.notify("es_systems.xml generated successfully!", severity="information")
        except Exception as e:
            self.log_msg(f"[bold red]Failed to write es_systems.xml: {e}[/]")
            self.notify(f"Error saving XML: {e}", severity="error")

    @work(exclusive=True)
    async def stream_roms_to_table(self, platform_id: str, roms_data: list) -> None:
        """Actively streams rows into the table in batches so the UI doesn't freeze."""
        chunk_size = 75  # Process 75 games at a time

        for i in range(0, len(roms_data), chunk_size):
            chunk = roms_data[i:i + chunk_size]

            # Re-use our safe appending logic
            self.append_roms_to_table(platform_id, chunk)

            # Hopefully this is enough time for textual to update the screen and not lock things up
            await asyncio.sleep(0.001)

    def gather_installed_roms(self, platform_slug):
        # Check for local physical ROMs
        local_platform_dir = ROMS_DIR / platform_slug
        existing_local_files = set()
        if local_platform_dir.exists():
            try:
                existing_local_files = {f.name for f in local_platform_dir.iterdir() if f.is_file()}
            except Exception:
                pass

        return existing_local_files

    def gather_rom_indicators(self, platform_slug):
        # Check for 0 byte ghost trackers
        ghost_platform_dir = ROM_LIST_DIR / platform_slug
        existing_ghost_files = set()
        if ghost_platform_dir.exists():
            try:
                existing_ghost_files = {f.name for f in ghost_platform_dir.iterdir() if f.is_file()}
            except Exception:
                pass

        return existing_ghost_files

    def get_optimal_url(self, path_key, url_key, root_key, ss_meta, rom):
        # Edge-case shortcut for background screenshots
        if path_key == "screenshot_path" and not ss_meta.get("screenshot_path") and not ss_meta.get("screenshot_url") and rom.get("merged_screenshots"):
            scr = rom.get("merged_screenshots")[0]
            return f"{self.romm_url}{scr}" if scr.startswith("/") else f"{self.romm_url}/assets/romm/resources/{scr}"

        # Check if a direct root-level path is populated
        if root_key and rom.get(root_key):
            rk_val = rom.get(root_key)
            return f"{self.romm_url}{rk_val}" if rk_val.startswith("/") else f"{self.romm_url}/assets/romm/resources/{rk_val}"

        # Check if an ss_metadata inner local path is populated
        if ss_meta.get(path_key):
            pk_val = ss_meta.get(path_key)
            return f"{self.romm_url}{pk_val}" if pk_val.startswith("/") else f"{self.romm_url}/assets/romm/resources/{pk_val}"

        # Check for direct root level generic local paths
        if rom.get(path_key):
            rk_val = rom.get(path_key)
            return f"{self.romm_url}{rk_val}" if rk_val.startswith("/") else f"{self.romm_url}/assets/romm/resources/{rk_val}"

        # Fallback strictly to external scraping URLs if no local proxy exists
        return ss_meta.get(url_key) or rom.get(url_key)

    def append_roms_to_table(self, platform_id: str, roms_data: list) -> None:
        """Safely processes and appends new rows to the bottom of the table without disrupting the cursor."""
        table = self.query_one("#roms-table", DataTable)
        is_all = platform_id == "all"

        # Build local disk lookup caches for whatever platforms are actively in this chunk
        local_files_cache = {}
        ghost_files_cache = {}
        es_sys_cache = {}

        needed_pids = {str(r.get("platform_id")) for r in roms_data if r.get("platform_id")}

        for pid in needed_pids:
            if pid not in self.platforms: continue

            slug = self.platforms[pid].get("fs_slug")
            local_files_cache[pid] = self.gather_installed_roms(slug)
            ghost_files_cache[pid] = self.gather_rom_indicators(slug)
            es_sys_cache[pid] = self.transform_romm_name_to_esde_name(slug)

        previously_selected = set(self.selected_roms)
        existing_row_ids = {rk.value for rk in table.rows.keys()}

        new_rows = []

        for rom in roms_data:
            rom_id = str(rom["id"])
            p_id = str(rom.get("platform_id"))

            if rom_id in existing_row_ids:
                continue

            fs_name = rom.get("fs_name", "")
            if not fs_name or p_id not in self.platforms:
                continue

            platform_slug = self.platforms[p_id].get("fs_slug")
            platform_name = self.platforms[p_id].get("name", platform_slug)
            true_stem = Path(fs_name).stem
            self.current_options[rom_id] = fs_name

            # Check visibility using our new lookup caches
            if rom_id in self.active_downloads:
                visibility_col = "[cyan]Downloading...[/]"
            elif fs_name in local_files_cache.get(p_id, set()):
                visibility_col = "[green]Visible (Local)[/]"
            elif fs_name in ghost_files_cache.get(p_id, set()):
                visibility_col = "[blue]Visible (Cloud)[/]"
            else:
                visibility_col = "[magenta]Hidden[/]"

            # Safely check our namespaced XML tracker
            fs_path_str = f"{platform_slug}:./{fs_name}"
            in_xml = fs_path_str in self.synced_rom_paths

            ss_meta = rom.get("ss_metadata", {}) or {}
            missing_folders = []

            es_system = es_sys_cache.get(p_id)
            es_media_dir = ES_DE_DIR / "downloaded_media" / es_system

            for xml_tag, es_folder, default_ext, friendly_name, path_key, url_key, root_key in self.media_mappings:
                target_url = self.get_optimal_url(path_key, url_key, root_key, ss_meta, rom)
                if target_url:
                    ext = Path(urllib.parse.urlparse(target_url).path).suffix
                    if ext.lower() in ['.php', '.html', '.aspx', '.jsp', '']:
                        ext = default_ext
                    filename = f"{true_stem}{ext}"
                    if not (es_media_dir / es_folder / filename).exists():
                        missing_folders.append(es_folder)

            self.missing_media_cache[rom_id] = list(missing_folders)

            if in_xml and len(missing_folders) == 0:
                meta_col = "[green]Fully Synced[/]"
            elif in_xml:
                meta_col = "[yellow]Missing Media[/]"
            else:
                meta_col = "[red]Unindexed[/]"

            chk_marker = "[bold green]✔[/]" if rom_id in previously_selected else "  "

            if is_all:
                new_rows.append((
                    rom_id,
                    Text.from_markup(chk_marker),
                    Text(platform_name),
                    Text(fs_name),
                    Text.from_markup(visibility_col),
                    Text.from_markup(meta_col)
                ))
            else:
                new_rows.append((
                    rom_id,
                    Text.from_markup(chk_marker),
                    Text(fs_name),
                    Text.from_markup(visibility_col),
                    Text.from_markup(meta_col)
                ))

        for row_data in new_rows:
            r_id = row_data[0]
            cells = row_data[1:]
            table.add_row(*cells, key=r_id)

    # ==================== #
    #         Misc         #
    # ==================== #

    def log_msg(self, msg) -> None:
        """Logs the given message to the RichLog in the SystemLogModal modal."""
        self.system_logs.append(msg)
        if len(self.system_logs) > 1000:
            self.system_logs.pop(0)

    def dry_run_log_msg(self, msg):
        if self.dry_run:
            modal_log = self.screen.query_one("#modal-log", RichLog)
            self.call_from_thread(modal_log.write, msg)
        else:
            self.log_msg(msg)

    def can_connect(self, url_or_address: str, default_port: int = 80, timeout: int = 3) -> bool:
        """Attempts a rapid TCP handshake to see if the target is alive and listening."""

        # Extract the raw hostname and port if a full URL is provided
        parsed = urllib.parse.urlparse(url_or_address) if "://" in url_or_address else urllib.parse.urlparse(f"http://{url_or_address}")
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

    def transform_romm_name_to_esde_name(self, platform_slug: str) -> str:
        """Maps RomM labels to official EmulationStation identifiers."""
        # TODO: This is not complete. These are just the ones I have and tested with
        mapping = {
            "3ds": "n3ds",
            "atari2600": "atari2600",
            "atari5200": "atari5200",
            "atari7800": "atari7800",
            "dc": "dreamcast",
            "famicom": "famicom",
            "gamegear": "gamegear",
            "gb": "gb",
            "gba": "gba",
            "gbc": "gbc",
            "genesis": "genesis",
            "laserdisc": "laserdisc",
            "mame": "mame",
            "n64": "n64",
            "naomi": "naomi",
            "nds": "nds",
            "neogeomvs": "neogeo",
            "nes": "nes",
            "ngc": "gc",
            "philips-cd-i": "cdimono1",
            "ps2": "ps2",
            "psp": "psp",
            "psvita": "psvita",
            "psx": "psx",
            "saturn": "saturn",
            "sega32": "sega32x",
            "sms": "mastersystem",
            "snes": "snes",
            "tg16": "tg16",
            "wii": "wii",
            "wiiu": "wiiu",
            "xbox": "xbox"
        }

        return mapping.get(platform_slug.lower(), platform_slug)

    def format_size(self, size_bytes: int) -> str:
        """Converts raw numerical bytes into clean human-readable metrics."""
        if size_bytes == 0:
            return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog="RommSync",
        description="A terminal dashboard to sync RomM instances with ES-DE.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Server Credentials
    parser.add_argument("-u", "--url", help="URL of your RomM server (overrides ROMM_URL env var)", default=ROMM_URL)
    parser.add_argument("-k", "--api-key", help="RomM API Key (overrides ROMM_API_KEY env var)", default=ROMM_API_KEY)

    # Directory Paths
    parser.add_argument("--es-dir", help="Path to your ES-DE configuration directory", type=Path, default=ES_DE_DIR)
    parser.add_argument("--roms-dir", help="Path to extract downloaded physical ROMs", type=Path, default=ROMS_DIR)
    parser.add_argument("--rom-list-dir", help="Path to map cloud ghost files", type=Path, default=ROM_LIST_DIR)

    # Cache Options
    parser.add_argument("--skip-cache-refresh", action="store_true", help="Skip cache refresh, if cache exists")

    args = parser.parse_args()

    ROMM_URL = args.url
    ROMM_API_KEY = args.api_key
    ES_DE_DIR = args.es_dir
    ROMS_DIR = args.roms_dir
    ROM_LIST_DIR = args.rom_list_dir

    app = RommBridge(args.skip_cache_refresh)
    app.run()
