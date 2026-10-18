"""Main application window (Part E).

Owns navigation, screen switching, theme application, startup load flow,
and graceful shutdown. Screens are created once and cached; the visible one
is raised on nav clicks. AppState listeners refresh all screens after any
data change so views stay consistent everywhere (FR-26 / Risk 4).
"""

from __future__ import annotations

import customtkinter as ctk

from app.constants import (
    APP_NAME,
    APP_VERSION,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_TITLE,
    WINDOW_WIDTH,
)
from app.gui.dialogs.message_dialog import show_message
from app.gui.screens.calendar_screen import CalendarScreen
from app.gui.screens.home_screen import HomeScreen
from app.gui.screens.notes_screen import NotesScreen
from app.gui.screens.settings_screen import SettingsScreen
from app.gui.screens.stats_screen import StatsScreen
from app.gui.theme import (
    ACCENT_TEXT,
    NAV_ACTIVE_BG,
    NAV_HOVER,
    SIDEBAR_FG,
    WINDOW_BG,
)
from app.gui.widgets.stopwatch import StopwatchWidget
from app.persistence.schema import FRIENDLY_MESSAGES, FutureSchemaError
from app.state.app_state import AppState
from app.utils.paths import get_default_save_path
from app.utils.resources import load_photo

NAV_ITEMS = (
    ("home", "Home", "home_icon"),
    ("calendar", "Calendar", "calendar_icon"),
    ("notes", "Notes", "notes_icon"),
    ("stats", "Statistics", "stats_icon"),
    ("settings", "Settings", "settings_icon"),
)

# Text colour of inactive nav buttons (light mode / dark mode).
NAV_TEXT_INACTIVE = ("gray25", "gray75")


class MainWindow(ctk.CTk):
    # NOTE: `state` is a reserved Tkinter attribute (window deiconify/iconic
    # status, used by customtkinter's DPI-scaling tracker). The app state must
    # never be assigned to that name on a Tk widget — use `_app_state`.
    def __init__(self, app_state: AppState | None = None):
        super().__init__()
        ctk.set_appearance_mode("dark")  # A-10: dark default before load
        ctk.set_default_color_theme("blue")

        self._app_state = app_state or AppState()

        # Performance: paint the whole window with our own pale-blue/navy
        # background *before* customtkinter inserts its default grey frame.
        # Tk paints immediately on configure (no redraw wait), so the user
        # sees the themed colour instantly instead of CTk's stock "gray92".
        self.configure(fg_color=WINDOW_BG)

        self.title(WINDOW_TITLE)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        icon = load_photo("app_icon", size=64)
        if icon is not None:
            try:
                # CTkImage exposes the underlying PIL image; tkinter needs a
                # PhotoImage for iconphoto. Keep a reference so it survives GC.
                # Import the Tkinter compat modules explicitly: PyInstaller's
                # analysis sometimes misses these lazy Pillow imports, which
                # makes PhotoImage raise in frozen builds.
                from PIL import ImageTk
                import PIL.ImageDraw  # noqa: F401  (ImageTk dependency)

                try:
                    import PIL._imagingtk  # noqa: F401
                    import PIL._tkinter_finder  # noqa: F401
                except ImportError:
                    pass

                self._icon_ref = ImageTk.PhotoImage(icon.pil_image)
                self.iconphoto(True, self._icon_ref)
            except Exception:
                pass  # window icon is best-effort (ERR-12)

        # --- layout: sidebar + content ---------------------------------------
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(
            self, corner_radius=0, width=210, fg_color=SIDEBAR_FG)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # --- brand block: app icon + name + tagline ---------------------------
        brand_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_row.pack(pady=(26, 4), padx=14, fill="x")

        app_icon = load_photo("app_icon", size=34)
        if app_icon is not None:
            self._brand_icon_ref = app_icon
            ctk.CTkLabel(brand_row, image=app_icon, text="").pack(side="left")
        ctk.CTkLabel(
            brand_row, text=APP_NAME,
            font=ctk.CTkFont(size=16, weight="bold"), wraplength=130, anchor="w",
        ).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            self.sidebar, text="Track · Earn XP · Stay consistent",
            font=ctk.CTkFont(size=10), text_color=NAV_TEXT_INACTIVE,
            wraplength=180,
        ).pack(pady=(0, 14), padx=14)

        ctk.CTkFrame(self.sidebar, height=1, corner_radius=0,
                     fg_color=("gray75", "gray25")).pack(fill="x", padx=14,
                                                          pady=(0, 12))

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_icon_refs: dict[str, object] = {}
        for key, label, icon_key in NAV_ITEMS:
            button = ctk.CTkButton(
                self.sidebar, text=f"  {label}", height=40, anchor="w",
                corner_radius=10, fg_color="transparent",
                hover_color=NAV_HOVER,
                font=ctk.CTkFont(size=14),
                command=lambda k=key: self.show_screen(k),
            )
            # Part J task 3: nav icons when the asset is available; the text
            # label always remains, so missing icons never break navigation.
            icon = load_photo(icon_key, size=18)
            if icon is not None:
                self._nav_icon_refs[key] = icon
                button.configure(image=icon)
            button.pack(fill="x", padx=12, pady=3)
            self._nav_buttons[key] = button

        # Sidebar footer with the app version — small visual anchor.
        ctk.CTkLabel(
            self.sidebar, text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=10), text_color=("gray55", "gray45"),
        ).pack(side="bottom", pady=(0, 12))

        # Digital stopwatch in the leftover sidebar space: pinned above the
        # version label (which is packed side="bottom" first), directly under
        # the nav/settings area.  Themed card look; works in light + dark.
        self.stopwatch = StopwatchWidget(self.sidebar, self._app_state)
        self.stopwatch.pack(side="bottom", fill="x", padx=12, pady=(16, 0))

        # Symmetric insets so the screen's left and right outer gaps match:
        # the content area sits 8 px from the window edge on both sides, and
        # BaseScreen adds SCREEN_PAD_X (12) inside it — 20 px each side for
        # every screen.  (The asymmetric (0, 8) used to make the right gap
        # larger than the left one.)
        self.content_area = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_area.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

        # --- screens (created lazily, cached) ----------------------------------
        # PERFORMANCE: building all five screens up front meant ~50 calendar
        # tiles + a full notes list were constructed *before* the window ever
        # painted.  Screens are now instantiated on first visit instead; the
        # dict starts with placeholders so callers can still enumerate keys.
        self.screens: dict[str, object] = {}
        self._screen_factories = {
            "home": lambda: HomeScreen(self.content_area, self._app_state),
            "calendar": lambda: CalendarScreen(self.content_area, self._app_state),
            "notes": lambda: NotesScreen(self.content_area, self._app_state),
            "stats": lambda: StatsScreen(self.content_area, self._app_state),
            "settings": lambda: SettingsScreen(
                self.content_area, self._app_state,
                on_theme_change=self.apply_theme),
        }
        for key in self._screen_factories:
            self.screens[key] = None

        # Any data change refreshes every screen at once.
        self._app_state.add_listener(self.refresh_all)

        self.current_screen_key: str | None = None
        self.show_screen("home")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------------

    def _get_screen(self, key: str):
        """Instantiate (once) and place the screen registered under ``key``."""
        screen = self.screens.get(key)
        if screen is None:
            screen = self._screen_factories[key]()
            screen.grid(row=0, column=0, sticky="nsew")
            self.screens[key] = screen
        return screen

    def show_screen(self, key: str) -> None:
        self.current_screen_key = key
        screen = self._get_screen(key)
        screen.tkraise()
        for other_key, button in self._nav_buttons.items():
            active = other_key == key
            button.configure(
                fg_color=NAV_ACTIVE_BG if active else "transparent",
                text_color=(ACCENT_TEXT if active else NAV_TEXT_INACTIVE),
                # NOTE: customtkinter rejects the literal "transparent" for
                # hover_color (ValueError); a no-op hover colour equal to the
                # widget's own background keeps inactive pills calm.  The
                # inactive pill's bg is transparent over SIDEBAR_FG, so reuse
                # that as the flat no-hover colour.
                hover_color=SIDEBAR_FG if active else NAV_HOVER,
            )

    def refresh_all(self) -> None:
        # PERFORMANCE: only refresh screens that have actually been built.
        # Unvisited (lazily-created) screens render fresh on first visit, so
        # refreshing them here would both crash on None and waste time.
        for screen in self.screens.values():
            if screen is None:
                continue
            try:
                screen.refresh()
            except Exception:
                pass  # a broken refresh must never crash the app (ERR-17)

    def apply_theme(self, theme: str) -> None:
        mode = {"dark": "dark", "light": "light"}.get(theme, "dark")
        ctk.set_appearance_mode(mode)

    # --- startup / shutdown ---------------------------------------------------

    def run_startup_load(self) -> None:
        """Execute the persistence startup flow with friendly notices.

        - Normal load / first-run creation: silent.
        - Recovered from backup or started empty: friendly message.
        - Future schema version: warn and refuse to modify the file (FR-72).
        """
        try:
            outcome = self._app_state.load_startup()
        except FutureSchemaError:
            self._app_state.dataset = type(self._app_state.dataset).empty()
            self._app_state.rebuild_index()
            self.after(200, lambda: show_message(
                self, "Newer data file", FRIENDLY_MESSAGES["future_schema"]))
            return
        except Exception:
            # Absolute last resort: keep the app usable with empty data.
            self.after(200, lambda: show_message(
                self, "Heads up", FRIENDLY_MESSAGES["started_empty"]))
            return

        self.apply_theme(self._app_state.settings.theme)
        self.refresh_all()

        if outcome.notice:
            title = ("Data recovered" if outcome.recovered_from_backup
                     else "Heads up")
            self.after(200, lambda: show_message(self, title, outcome.notice))

    def _on_close(self) -> None:
        """Flush unsaved changes before exiting (FR-58), then quit."""
        if self._app_state.dirty:
            result = self._app_state.autosave()
            if not result.ok:
                show_message(self, "Couldn't save", result.message or "")
        self.destroy()
