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

from rich.text import Text
from textual.app import App
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Label, ListView, ListItem, DataTable, RichLog, Button
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
        ("~", "toggle_log", "System Logs"),
        ("j", "cursor_down", "Move Down"),
        ("k", "cursor_up", "Move Up"),
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
                res = requests.get(f"{self.romm_url}/api/roms", headers=self.headers, params=params, timeout=10)
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
            self.log_msg(f"Cache Error: {err}", severity="error")
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
                slug = p.get("fs_slug", "")
                if not slug: continue
                es_sys = self.transform_romm_name_to_esde_name(slug)
                xml_path = ES_DE_DIR / "gamelists" / es_sys / "gamelist.xml"
                if xml_path.exists():
                    try:
                        tree = ET.parse(xml_path)
                        for game in tree.getroot().findall('game'):
                            path_node = game.find('path')
                            if path_node is not None and path_node.text:
                                self.synced_rom_paths.add(f"{slug}:{path_node.text}")
                    except Exception:
                        self.log_msg(f"[bold yellow]Failed to read local XML: {e}[/]")
                        self.notify(f"Failed to read local XML: {e}", severity="error")

        else:
            platform_slug = self.platforms[platform_id].get("fs_slug", platform_id)
            self.log_msg(f"Loading platform: [cyan]{platform_slug}[/]")

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
