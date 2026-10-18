"""Sidebar stopwatch widget (compact digital timer).

Sits in the left contents pane between the nav buttons and the version
footer.  Behaviour:

- Displays elapsed time as ``H:MM:SS``; defaults to ``0:00:00`` on open.
- Start / Stop / Reset buttons drive a monotonic-clock-based tick loop so
  the display stays accurate even if Tk callbacks are delayed.
- On **Stop**, if the elapsed time is over 10 minutes, the session is
  logged automatically as a custom study session, rounded *down* to the
  nearest 10 minutes (e.g. 2:35:12 → 2 h 30 min), and the display resets
  to zero.  Shorter stops keep the reading so it can be resumed.
- Colours come from the shared theme tokens, so the widget reads clearly
  in both light and dark appearance modes.
"""

from __future__ import annotations

import time

import customtkinter as ctk

from app.gui.dialogs.message_dialog import show_message
from app.gui.theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_TEXT,
    BORDER,
    CARD_FG,
    DANGER,
    DANGER_HOVER,
    MUTED_TEXT,
    NEUTRAL,
    NEUTRAL_HOVER,
    NEUTRAL_TEXT,
    TIME_BLUE,
)
from app.state.app_state import AppState

# Floating-point slack so an elapsed time of exactly the threshold counts
# as "over 10 minutes" even if the monotonic clock drifts by microseconds.
_EPS_S = 0.25
# A stopped run only logs a session when it exceeds this many seconds…
AUTO_LOG_THRESHOLD_S = 10 * 60
# …and the logged duration is rounded DOWN to whole multiples of this.
ROUND_TO_MINUTES = 10


def format_stopwatch(elapsed_s: float) -> str:
    """Return ``H:MM:SS`` for an elapsed-second count (e.g. ``2:35:12``)."""
    total = max(0, int(elapsed_s))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


class StopwatchWidget(ctk.CTkFrame):
    def __init__(self, master, app_state: AppState):
        super().__init__(master, corner_radius=12, fg_color=CARD_FG,
                         border_width=1, border_color=BORDER)
        self._app_state = app_state

        self._running = False
        self._accumulated_s = 0.0     # completed running segments
        self._segment_start: float | None = None  # monotonic ts of current run
        self._tick_job: str | None = None

        ctk.CTkLabel(
            self, text="Study Stopwatch",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED_TEXT,
        ).pack(pady=(9, 1))

        self._display = ctk.CTkLabel(
            self, text=self._display_text(),
            font=ctk.CTkFont(size=22, weight="bold", family="Consolas"),
            text_color=TIME_BLUE,
        )
        self._display.pack(pady=(0, 4))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(0, 9))

        self._start_btn = ctk.CTkButton(
            btn_row, text="Start", width=52, height=26, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_start,
        )
        self._start_btn.pack(side="left", padx=3)

        self._stop_btn = ctk.CTkButton(
            btn_row, text="Stop", width=52, height=26, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
            text_color=NEUTRAL_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_stop,
        )
        self._stop_btn.pack(side="left", padx=3)

        self._reset_btn = ctk.CTkButton(
            btn_row, text="Reset", width=58, height=26, corner_radius=8,
            fg_color=DANGER, hover_color=DANGER_HOVER, text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_reset,
        )
        self._reset_btn.pack(side="left", padx=3)

        self._sync_buttons()

    # ------------------------------------------------------------------ state

    @property
    def _elapsed_s(self) -> float:
        if self._running and self._segment_start is not None:
            return self._accumulated_s + (time.monotonic() - self._segment_start)
        return self._accumulated_s

    def _display_text(self) -> str:
        return format_stopwatch(self._elapsed_s)

    # ------------------------------------------------------------- tick loop

    def _schedule_tick(self) -> None:
        self._cancel_tick()
        self._tick_job = self.after(200, self._tick)

    def _cancel_tick(self) -> None:
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self) -> None:
        if not self._running:
            return
        self._display.configure(text=self._display_text())
        self._schedule_tick()

    # --------------------------------------------------------------- controls

    def _on_start(self) -> None:
        if self._running:
            return
        self._running = True
        self._segment_start = time.monotonic()
        self._sync_buttons()
        self._display.configure(text=self._display_text())
        self._schedule_tick()

    def _on_stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._segment_start is not None:
            self._accumulated_s += time.monotonic() - self._segment_start
            self._segment_start = None
        self._cancel_tick()
        self._display.configure(text=self._display_text())
        self._sync_buttons()

        if self._accumulated_s >= AUTO_LOG_THRESHOLD_S - _EPS_S:
            minutes = int(self._accumulated_s // 60) // ROUND_TO_MINUTES \
                * ROUND_TO_MINUTES
            self._accumulated_s = 0.0
            self._display.configure(text=self._display_text())
            # Rounded DOWN to whole 10-minute steps; over the threshold the
            # rounded value is always >= 10 min, so it passes the normal
            # custom-session validation and lands in today's study time.
            result = self._app_state.log_custom_session(
                hours=minutes // 60, minutes=minutes % 60)
            top = self.winfo_toplevel()
            if result.ok:
                show_message(
                    top, "Session logged",
                    f"Your stopwatch run was rounded down to "
                    f"{format_minutes(minutes)} and added to your study "
                    "time.")
            elif result.message:
                show_message(top, "Couldn't log session", result.message)

    def _on_reset(self) -> None:
        # Reset works whether running or not: cancel the tick loop, drop all
        # accumulated time and return the display to 0:00:00.
        self._running = False
        self._segment_start = None
        self._accumulated_s = 0.0
        self._cancel_tick()
        self._display.configure(text=self._display_text())
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self._start_btn.configure(
            state="disabled" if self._running else "normal")
        self._stop_btn.configure(
            state="normal" if self._running else "disabled")

    # ------------------------------------------------------------- lifecycle

    def destroy(self):
        self._cancel_tick()
        super().destroy()


def format_minutes(minutes: int) -> str:
    """Friendly duration like ``2 hours 30 minutes``."""
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts) if parts else "0 minutes"
