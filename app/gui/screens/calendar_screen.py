"""Calendar screen (Part G).

Month grid with medal indicators, prev/next/today navigation, and a modal
day-details dialog on day click. Initial view is the current month; the last
viewed month is preserved while the app runs.

Tile contents: just the date number, with tiny markers underneath — a small
medal icon when the day earned bronze/silver/gold, and a memo-emoji note
marker when the day has notes attached. The notes themselves are listed
inside the day-details dialog.  Every tile is built as the same composite
structure (date button on top, marker row underneath) so the date number
sits at the identical vertical position in all tiles — including days that
carry a note marker, a medal, or both (previously those variants stacked
their content centred and floated the date higher than plain tiles).

Colour scheme: day tiles are light grey in light mode (dark grey in dark
mode); today's tile uses the same blue accent as the "Today" button in the
header so the two match. The selected day gets an accent-blue outline ring
so it stays visible even when its fill colour is neutral.  Date-number
colours follow light/dark mode switches automatically (an appearance-mode
watcher recolours the rendered cells immediately, so no month navigation is
needed after toggling the theme).
"""

from __future__ import annotations

import calendar as pycal
from datetime import date

import customtkinter as ctk

from app.core.medals import MEDAL_ASSET_KEYS
from app.gui.dialogs.day_details_dialog import DayDetailsDialog
from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import ACCENT, BORDER, CARD_FG, MUTED_TEXT
from app.utils.date_utils import add_months
from app.utils.resources import load_photo

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

NOTE_MARKER = "\U0001F4DD"  # memo emoji shown when a day has notes

# Tile fills are deliberately neutral (grey / today-blue): medals are shown
# as a small icon under the date instead of colouring the whole tile.
MEDAL_ICON_SIZE = 14  # px — same scale as the tiny note marker line

# Shared accent for the Today tile and the Today button (both blue).
TODAY_BLUE = ACCENT
TODAY_BLUE_HOVER = "#255a94"

# Plain-tile fills: a soft blue tint in light mode (so the grid harmonises
# with the light-blue sidebar instead of clashing dark grey) and, in dark
# mode, a distinctly *lighter navy* than the calendar background — the old
# "#26263a" was nearly invisible on CTk's dark frame, so tiles now stand
# apart clearly while staying calm.  Text stays legible on both.
PLAIN_FG = ("#d3e2f4", "#313147")
PLAIN_HOVER = ("#c3d6ee", "#3d3d58")


class CalendarScreen(BaseScreen):
    def __init__(self, master, state):
        super().__init__(master, state, title="Calendar")

        today = date.today()
        self._year, self._month = today.year, today.month
        self._selected: date | None = None  # selected on first visit below

        # --- header row: < Month Year > [Today] ------------------------------
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.pack(fill="x", pady=(0, 8))

        nav_font = ctk.CTkFont(size=15, weight="bold")
        ctk.CTkButton(header, text="\u2039", width=40, height=32,
                      corner_radius=8, font=nav_font,
                      command=self._prev_month).pack(side="left")
        self.month_label = ctk.CTkLabel(
            header, text="", font=ctk.CTkFont(size=17, weight="bold"), width=190
        )
        self.month_label.pack(side="left", padx=8)
        ctk.CTkButton(header, text="\u203a", width=40, height=32,
                      corner_radius=8, font=nav_font,
                      command=self._next_month).pack(side="left")
        # Same blue accent as today's tile so the two visually match.
        self.today_button = ctk.CTkButton(
            header, text="Today", width=80, height=32, corner_radius=8,
            fg_color=TODAY_BLUE, hover_color=TODAY_BLUE_HOVER,
            text_color="white", command=self._go_today,
        )
        self.today_button.pack(side="right")

        # --- grid ------------------------------------------------------------
        self.grid_frame = ctk.CTkFrame(
            self.content, corner_radius=14,
            fg_color=CARD_FG, border_width=1, border_color=BORDER)
        # Packed flush into the content frame, whose own padx already gives
        # an equal SCREEN_PAD_X gap on BOTH the left and right edges — no
        # asymmetric extra inset here (that used to make the right gap
        # smaller than the left one).
        self.grid_frame.pack(fill="both", expand=True, pady=(0, 8))

        for col, label in enumerate(WEEKDAY_LABELS):
            ctk.CTkLabel(
                self.grid_frame, text=label, font=ctk.CTkFont(size=12, weight="bold"),
                text_color=MUTED_TEXT,
            ).grid(row=0, column=col, sticky="nsew", padx=2, pady=(10, 4))

        # Day tiles: plain days are CTkButtons; medal days are composite
        # CTkFrames (date button + tiny medal-icon label) — see _make_day_cell.
        self._cells: dict[date, ctk.CTkButton | ctk.CTkFrame] = {}
        # Tk binding tag used to hook <Destroy> on the app's root window so we
        # can detach the appearance-mode watcher without leaving a stale
        # callback behind (see _on_root_destroy).
        self._destroy_tag = f"calendar_appearance_watch_{id(self)}"
        self.bindtags((self._destroy_tag,) + self.bindtags())
        # customtkinter's bind() override drops the tag parameter, so reach
        # the underlying Tk binding for the root window directly.
        self.winfo_toplevel().tk.call(
            "bind", self._destroy_tag, "<Destroy>",
            self._register_root_destroy_callback(),
        )

        # customtkinter only re-themes widgets that are *reconfigured* after an
        # appearance-mode switch; the text colour of our plain day tiles is set
        # explicitly at render time, so it would otherwise keep its old value
        # until the month grid happens to be rebuilt (prev/next/today click).
        # Watch for mode changes and recolour the tiles immediately instead.
        self._appearance_watcher_id: int | None = None
        self._last_appearance_mode = ctk.get_appearance_mode()
        self._start_appearance_watch()

        self._render()

    # --- theme-change handling --------------------------------------------------

    def _register_root_destroy_callback(self) -> str:
        """Wire the root-window <Destroy> event to ``_on_root_destroy``.

        Returns the Tcl command name so it can be registered as a binding via
        ``tk.call("bind", tag, "<Destroy>", ...)`` (customtkinter's own
        ``bind()`` override does not accept a bindtag argument).
        """
        return self.register(lambda *_: self._on_root_destroy())

    def _start_appearance_watch(self) -> None:
        """Begin polling for light/dark mode switches (see module note above)."""
        if self._appearance_watcher_id is not None:
            return
        self._appearance_watcher_id = self.after(250, self._check_appearance_mode)

    def _stop_appearance_watch(self) -> None:
        if self._appearance_watcher_id is not None:
            try:
                self.after_cancel(self._appearance_watcher_id)
            except Exception:
                pass
            self._appearance_watcher_id = None

    def _check_appearance_mode(self) -> None:
        self._appearance_watcher_id = None
        if not self.winfo_exists():
            return
        mode = ctk.get_appearance_mode()
        if mode != self._last_appearance_mode:
            self._last_appearance_mode = mode
            self._apply_cell_text_colors()
        self._start_appearance_watch()

    def _on_root_destroy(self, _event=None) -> None:
        """Tear down the watcher when the app window closes."""
        self._stop_appearance_watch()

    def _apply_cell_text_colors(self) -> None:
        """Re-derive date-number colours for all rendered cells.

        Only the *text* colour needs adjusting on a theme switch: tile
        background colours are (fg, dark) tuples that customtkinter resolves
        against the new mode automatically.  Medal icons live in separate
        CTkLabel children and need no recolouring.
        """
        today = date.today()
        for day, cell in list(self._cells.items()):
            # Composite (medal) tiles keep their date button inside a frame
            # — recolour that instead of the frame itself.
            target = getattr(cell, "num_button", cell)
            try:
                target.configure(text_color=self._cell_text_color(day == today))
            except Exception:
                pass  # cell may have been destroyed mid-teardown

    # --- navigation -----------------------------------------------------------

    def _prev_month(self) -> None:
        anchor = date(self._year, self._month, 1)
        shifted = add_months(anchor, -1)
        self._year, self._month = shifted.year, shifted.month
        self._render()

    def _next_month(self) -> None:
        anchor = date(self._year, self._month, 1)
        shifted = add_months(anchor, 1)
        self._year, self._month = shifted.year, shifted.month
        self._render()

    def _go_today(self) -> None:
        today = date.today()
        self._year, self._month = today.year, today.month
        self._selected = today
        self._render()

    # --- rendering --------------------------------------------------------------

    @staticmethod
    def _cell_text_color(is_today: bool) -> str:
        """Pick the date-number colour for a tile.

        Today's tile is always blue (both themes) with white text; every
        other tile uses the soft-blue PLAIN_FG tint — dark text in light
        mode, white text on the darker fill in dark mode.
        """
        if is_today or ctk.get_appearance_mode().lower() != "light":
            return "white"
        return "gray10"

    def _make_day_cell(self, day, *, medal, note_marker, fg, hover,
                       is_today, selected):
        """Build one day tile.

        Every tile — plain, note-only, medal-only, or medal *and* note —
        uses the exact same composite structure: an outer CTkFrame carrying
        the fill/selection ring, a transparent date button packed at the
        top with fixed padding, and a marker row packed underneath.  That
        uniform layout is what keeps the date number vertically aligned
        across the whole grid; previously note days were single multi-line
        buttons and medal days had extra centred widgets, so their dates
        floated higher than plain tiles'.

        CTkButton cannot embed an image next to its text, which is why the
        medal icon lives in its own label in the marker row.
        """
        icon = load_photo(MEDAL_ASSET_KEYS[medal], size=MEDAL_ICON_SIZE) \
            if medal in MEDAL_ASSET_KEYS else None

        text_color = self._cell_text_color(is_today)

        # Outer frame: identical geometry for *every* day so the date
        # numbers all sit on the same line within the tile.
        frame = ctk.CTkFrame(
            self.grid_frame,
            corner_radius=8,
            fg_color=fg,
            border_width=2 if selected else 0,
            # NOTE: customtkinter forbids the literal "transparent" for
            # border_color (raises ValueError); a theme-aware colour is
            # safe and invisible anyway while border_width is 0.
            border_color=ACCENT if selected else BORDER,
        )
        frame._is_day_cell = True

        # Date button: same height/padding everywhere, showing the number
        # only — markers never touch the date line.
        num_button = ctk.CTkButton(
            frame,
            text=str(day.day),
            font=ctk.CTkFont(size=12),
            height=34,
            width=0,
            corner_radius=6,
            fg_color="transparent",
            hover_color=hover,
            text_color=text_color,
            command=lambda d=day: self._open_day(d),
        )
        num_button.pack(fill="x", padx=2, pady=(3, 0))

        # Marker row: always created (empty when the day has neither a
        # medal nor notes) so the reserved space below the date is
        # identical in all tiles.
        icon_label = ctk.CTkLabel(
            frame,
            image=icon,
            text=note_marker,
            font=ctk.CTkFont(size=9),
            compound="left",
        )
        icon_label.pack(pady=(0, 4))

        frame.num_button = num_button
        return frame

    def _render(self) -> None:
        for child in self.grid_frame.winfo_children():
            if getattr(child, "_is_day_cell", False):
                # Safe teardown: customtkinter widgets can raise
                # AttributeError inside destroy() on some versions.
                try:
                    child.destroy()
                except Exception:
                    pass
        self._cells.clear()
        # The grid was just rebuilt with colours correct for the *current*
        # mode; keep the watcher's cached mode in sync so it does not fire a
        # redundant recolour on its next tick.
        self._last_appearance_mode = ctk.get_appearance_mode()

        month_name = pycal.month_name[self._month]
        self.month_label.configure(text=f"{month_name} {self._year}")

        aggregates = self.state.get_month_view_data(self._year, self._month)
        note_counts = self.state.get_note_counts_by_day()
        today = date.today()

        # Monday-first week layout (calendar.setfirstweekday safe locally).
        cal = pycal.Calendar(firstweekday=0)
        weeks = cal.monthdayscalendar(self._year, self._month)

        for week_index, week_days in enumerate(weeks, start=1):
            for col, day_num in enumerate(week_days):
                if day_num == 0:
                    continue
                day = date(self._year, self._month, day_num)
                aggregate = aggregates.get(day)

                notes_count = note_counts.get(day, 0)
                if notes_count:
                    note_marker = f"{NOTE_MARKER}{notes_count}" if notes_count > 1 \
                        else NOTE_MARKER
                else:
                    note_marker = ""

                # Tiles stay neutral grey; only today is blue (matching the
                # Today button).  Medals show as a small icon under the date.
                is_today = day == today
                if is_today:
                    fg, hover = TODAY_BLUE, TODAY_BLUE_HOVER
                else:
                    # Light grey tiles in light mode, dark grey in dark mode.
                    fg, hover = PLAIN_FG, PLAIN_HOVER

                selected = day == self._selected
                cell = self._make_day_cell(
                    day,
                    medal=(aggregate.medal if aggregate else None),
                    note_marker=note_marker,
                    fg=fg,
                    hover=hover,
                    is_today=is_today,
                    selected=selected,
                )
                cell.grid(row=week_index, column=col, sticky="nsew", padx=2, pady=2)
                self._cells[day] = cell

        for col in range(7):
            self.grid_frame.grid_columnconfigure(col, weight=1)
        for row in range(1, len(weeks) + 1):
            self.grid_frame.grid_rowconfigure(row, weight=1)

    def _open_day(self, day: date) -> None:
        self._selected = day
        DayDetailsDialog(self.winfo_toplevel(), self.state, day)

    def refresh(self) -> None:
        """Called by AppState listeners after any data change."""
        self._render()
