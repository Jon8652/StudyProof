"""Sidebar stopwatch widget (compact digital timer).

Sits in the left contents pane between the nav buttons and the version
footer.  The widget has two modes:

**Stopwatch mode** (the default, unchanged behaviour):

- Displays elapsed time as ``H:MM:SS``; defaults to ``0:00:00`` on open.
- Start / Stop / Reset buttons drive a monotonic-clock-based tick loop so
  the display stays accurate even if Tk callbacks are delayed.
- On **Stop**, if the elapsed time is over 10 minutes, the session is
  logged automatically as a custom study session, rounded *down* to the
  nearest 10 minutes (e.g. 2:35:12 → 2 h 30 min), and the display resets
  to zero.  Shorter stops keep the reading so it can be resumed.  This is
  cheat-safe: real wall-clock time must actually pass while running.

**Countdown mode** (study sessions started from the Study Tracker screen):

- ``start_countdown(minutes)`` switches the widget into a *decreasing*
  timer showing the remaining time.  No XP/time is granted at start —
  only when the countdown genuinely reaches ``0:00:00`` does the widget
  call ``AppState.record_timer_session()`` for the full planned duration.
- The only user control is **Pause/Resume**: pausing freezes the
  remaining time (paused time never counts toward completion).  Cancelling
  abandons the session and grants nothing.
- When the session finishes (or is cancelled) the widget returns to
  normal stopwatch mode with the display reset to ``0:00:00``.
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

# Widget operating modes.
MODE_STOPWATCH = "stopwatch"
MODE_COUNTDOWN = "countdown"


def format_stopwatch(elapsed_s: float) -> str:
    """Return ``H:MM:SS`` for an elapsed-second count (e.g. ``2:35:12``).

    Rounds to the nearest whole second so a freshly-started 1-hour
    countdown displays ``1:00:00`` rather than ``0:59:59`` from the first
    microsecond of drift; a running stopwatch never loses its final
    partial second either.
    """
    total = max(0, int(round(elapsed_s)))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


class StopwatchWidget(ctk.CTkFrame):
    def __init__(self, master, app_state: AppState):
        super().__init__(master, corner_radius=12, fg_color=CARD_FG,
                         border_width=1, border_color=BORDER)
        self._app_state = app_state

        # --- stopwatch-mode state ------------------------------------------
        self._running = False
        self._accumulated_s = 0.0     # completed running segments
        self._segment_start: float | None = None  # monotonic ts of current run
        self._tick_job: str | None = None

        # --- countdown-mode state -------------------------------------------
        self._mode = MODE_STOPWATCH
        self._cd_total_s = 0.0            # planned length in seconds
        self._cd_remaining_s = 0.0        # remaining time (authoritative)
        self._cd_running = False          # counting down right now?
        self._cd_segment_start: float | None = None  # anchor of active segment

        # Optional callback fired whenever a countdown starts, is cancelled,
        # or finishes — lets other screens (e.g. the Study Tracker) mirror
        # the countdown state on their buttons without polling.
        self.on_countdown_change = None

        # Countdowns are driven by a *deferred* finish job (``after``), never
        # only by the 200 ms tick loop: Tk ``after`` callbacks do not run
        # while a modal dialog holds the grab (grab_set + wait_window).  A
        # scheduled job still fires once the mainloop resumes, so completion
        # can never be missed and start-buttons can never stay greyed out
        # with a "finished" countdown that no longer exists.
        self._cd_finish_job: str | None = None

        self._header = ctk.CTkLabel(
            self, text="Study Stopwatch",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED_TEXT,
        )
        self._header.pack(pady=(9, 1))

        self._display = ctk.CTkLabel(
            self, text=self._display_text(),
            font=ctk.CTkFont(size=22, weight="bold", family="Consolas"),
            text_color=TIME_BLUE,
        )
        self._display.pack(pady=(0, 4))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(0, 2))

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

        # Countdown-only controls (hidden while in stopwatch mode).
        cd_row = ctk.CTkFrame(self, fg_color="transparent")
        cd_row.pack(pady=(0, 9))
        self._cd_btn_row = cd_row

        self._pause_btn = ctk.CTkButton(
            cd_row, text="Pause", width=70, height=26, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_pause_toggle,
        )

        self._cancel_btn = ctk.CTkButton(
            cd_row, text="Cancel", width=70, height=26, corner_radius=8,
            fg_color=DANGER, hover_color=DANGER_HOVER, text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.cancel_countdown,
        )

        self._sync_buttons()

    # ------------------------------------------------------------------ state

    @property
    def _elapsed_s(self) -> float:
        if self._running and self._segment_start is not None:
            return self._accumulated_s + (time.monotonic() - self._segment_start)
        return self._accumulated_s

    @property
    def countdown_active(self) -> bool:
        """True while a study-session countdown exists (running or paused)."""
        return self._mode == MODE_COUNTDOWN

    def _remaining_s(self) -> float:
        """Remaining countdown seconds, including any currently-running leg."""
        if self._cd_running and self._cd_segment_start is not None:
            return self._cd_remaining_s - (time.monotonic()
                                           - self._cd_segment_start)
        return self._cd_remaining_s

    def _display_text(self) -> str:
        if self._mode == MODE_COUNTDOWN:
            return format_stopwatch(self._remaining_s())
        return format_stopwatch(self._elapsed_s)

    def _notify_countdown_change(self) -> None:
        """Fire the optional ``on_countdown_change`` listener, if set."""
        callback = getattr(self, "on_countdown_change", None)
        if callback is not None:
            try:
                callback()
            except Exception:
                # A broken listener must never take down the timer itself.
                pass

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

    def _schedule_cd_finish(self) -> None:
        """Arm a deferred finish job at the exact moment remaining hits 0.

        The tick loop also watches for zero, but ``after`` callbacks are
        *not* dispatched while a modal dialog (grab_set + wait_window) is
        open — whereas scheduled jobs still run once the mainloop resumes.
        Scheduling the finish explicitly guarantees completion always fires
        (recording the session and re-enabling the start buttons), even if
        the countdown "ran out" behind an open dialog.
        """
        self._cancel_cd_finish()
        delay_ms = max(0, int(round(self._remaining_s() * 1000)))
        self._cd_finish_job = self.after(delay_ms, self._on_cd_finish_due)

    def _cancel_cd_finish(self) -> None:
        if self._cd_finish_job is not None:
            try:
                self.after_cancel(self._cd_finish_job)
            except Exception:
                pass
            self._cd_finish_job = None

    def _on_cd_finish_due(self) -> None:
        self._cd_finish_job = None
        if self._mode != MODE_COUNTDOWN or not self._cd_running:
            return                      # cancelled/paused — nothing to do
        if self._remaining_s() <= 0:
            self._finish_countdown()

    def _tick(self) -> None:
        if self._mode == MODE_COUNTDOWN:
            if not self._cd_running:
                return
            if self._remaining_s() <= 0:
                self._finish_countdown()
                return
            self._display.configure(text=self._display_text())
            self._schedule_tick()
            return

        if not self._running:
            return
        self._display.configure(text=self._display_text())
        self._schedule_tick()

    # ------------------------------------------------- stopwatch-mode controls

    def _on_start(self) -> None:
        if self._mode == MODE_COUNTDOWN:
            return          # countdown owns the widget; ignore extra clicks
        if self._running:
            return
        self._running = True
        self._segment_start = time.monotonic()
        self._sync_buttons()
        self._display.configure(text=self._display_text())
        self._schedule_tick()

    def _on_stop(self) -> None:
        if self._mode == MODE_COUNTDOWN:
            return
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
        if self._mode == MODE_COUNTDOWN:
            # Reset acts as "Cancel" during a countdown: abandon the session
            # without granting anything.
            self.cancel_countdown()
            return
        # Reset works whether running or not: cancel the tick loop, drop all
        # accumulated time and return the display to 0:00:00.
        self._running = False
        self._segment_start = None
        self._accumulated_s = 0.0
        self._cancel_tick()
        self._display.configure(text=self._display_text())
        self._sync_buttons()

    # ---------------------------------------------------- countdown-mode API

    def start_countdown(self, minutes: int) -> bool:
        """Begin a real-time study countdown of ``minutes`` minutes.

        Returns False (without starting anything) when a countdown is
        already running or the request is invalid — this is what prevents
        users from stacking/spamming sessions.  Nothing is recorded here;
        the session is only written when the countdown hits zero.
        """
        if self._mode == MODE_COUNTDOWN:
            return False
        if not isinstance(minutes, int) or minutes <= 0:
            return False

        # Freeze any in-progress stopwatch run so its elapsed time isn't
        # lost silently (it can be resumed/stopped after the session).
        if self._running:
            self._on_stop()

        self._mode = MODE_COUNTDOWN
        self._cd_total_s = minutes * 60.0
        self._cd_remaining_s = minutes * 60.0
        self._cd_running = True
        self._cd_segment_start = time.monotonic()
        self._header.configure(text=f"Study Session ({format_minutes(minutes)})")
        self._display.configure(text=self._display_text())
        self._sync_buttons()
        self._schedule_tick()
        self._schedule_cd_finish()
        self._notify_countdown_change()
        return True

    def _on_pause_toggle(self) -> None:
        if self._mode != MODE_COUNTDOWN:
            return
        if self._cd_running:
            # Freeze: bank the running leg into the remaining balance.
            if self._cd_segment_start is not None:
                self._cd_remaining_s = self._remaining_s()
                self._cd_segment_start = None
            self._cd_running = False
            self._cancel_tick()
            self._cancel_cd_finish()
        else:
            self._cd_running = True
            self._cd_segment_start = time.monotonic()
            self._schedule_tick()
            self._schedule_cd_finish()
        self._display.configure(text=self._display_text())
        self._sync_buttons()

    def cancel_countdown(self) -> None:
        """Abandon the active countdown.  Grants zero XP and zero time."""
        if self._mode != MODE_COUNTDOWN:
            return
        self._cd_running = False
        self._cd_segment_start = None
        self._cd_remaining_s = 0.0
        self._cd_total_s = 0.0
        self._mode = MODE_STOPWATCH
        self._header.configure(text="Study Stopwatch")
        self._cancel_tick()
        self._cancel_cd_finish()
        self._display.configure(text=self._display_text())
        self._sync_buttons()
        self._notify_countdown_change()

    def _finish_countdown(self) -> None:
        """Countdown hit zero: record the session, then revert to stopwatch."""
        minutes = int(round(self._cd_total_s // 60))
        self._cd_running = False
        self._cd_segment_start = None
        self._cd_remaining_s = 0.0
        self._cd_total_s = 0.0
        self._mode = MODE_STOPWATCH
        self._cancel_tick()
        self._cancel_cd_finish()
        self._header.configure(text="Study Stopwatch")
        self._display.configure(text=self._display_text())
        self._sync_buttons()
        self._notify_countdown_change()

        top = self.winfo_toplevel()
        result = self._app_state.record_timer_session(minutes)
        if result.ok:
            show_message(
                top, "Session complete",
                f"Your {format_minutes(minutes)} study session was "
                "recorded and added to your study time.")
        elif result.message:
            show_message(top, "Couldn't record session", result.message)

    # --------------------------------------------------------------- buttons

    def _sync_buttons(self) -> None:
        if self._mode == MODE_COUNTDOWN:
            # Countdown mode: only Pause/Resume + Cancel are available.
            self._start_btn.pack_forget()
            self._stop_btn.pack_forget()
            self._reset_btn.pack_forget()
            self._pause_btn.configure(
                text="Resume" if not self._cd_running else "Pause")
            self._pause_btn.pack(side="left", padx=3)
            self._cancel_btn.pack(side="left", padx=3)
            return

        self._pause_btn.pack_forget()
        self._cancel_btn.pack_forget()
        self._start_btn.pack(side="left", padx=3)
        self._stop_btn.pack(side="left", padx=3)
        self._reset_btn.pack(side="left", padx=3)
        self._start_btn.configure(
            state="disabled" if self._running else "normal")
        self._stop_btn.configure(
            state="normal" if self._running else "disabled")

    # ------------------------------------------------------------- lifecycle

    def destroy(self):
        self._cancel_tick()
        self._cancel_cd_finish()
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
