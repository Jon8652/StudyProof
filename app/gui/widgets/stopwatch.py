"""Sidebar stopwatch widget (compact digital timer).

Sits in the left contents pane between the nav buttons and the version
footer.  The widget has two modes:

**Stopwatch mode** (the default, unchanged behaviour):

- Displays elapsed time as ``H:MM:SS``; defaults to ``0:00:00`` on open.
- Start / Stop / Reset buttons drive a monotonic-clock-based tick loop so
  the display stays accurate even if Tk callbacks are delayed.
- On **Reset**, if the elapsed time is over 10 minutes, the session is
  logged automatically as a custom study session, rounded *down* to the
  nearest 10 minutes (e.g. 2:35:12 → 2 h 30 min), and the display resets
  to zero.  Stop merely pauses the run: its reading is kept so it can be
  resumed or later reset-and-logged.  Shorter resets discard the reading
  without logging anything.  This is cheat-safe: real wall-clock time must
  actually pass while running.

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
import tkinter as tk

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

# Fixed card height (px).  The sidebar packs this widget with fill="x" but
# never constrains its height, so the card used to size itself to its
# children — which *changes* between stopwatch mode (Start/Stop/Reset row)
# and countdown mode (Pause/Cancel row), and stretches oddly when the window
# is maximised / fullscreen because the packed "side=bottom" block grows with
# the taller layout.  Pinning an explicit height (with pack_propagate off)
# makes the card render identically in windowed and fullscreen layouts.
# Trimmed from 158 -> 136 (bottom whitespace), then 136 -> 124: with the
# paddings below the button rows' bottom edge gap is *equal* to the header's
# top gap (6 px unscaled each side), so nothing spills over the rounded
# corner_radius=12 base of the card any more.
CARD_HEIGHT = 116


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
        super().__init__(master, corner_radius=12, height=CARD_HEIGHT,
                         fg_color=CARD_FG,
                         border_width=1, border_color=BORDER)
        # Fixed-size card: children never resize the frame, so the widget is
        # pixel-identical in windowed and maximised/fullscreen layouts.
        try:
            self.pack_propagate(False)
        except Exception:
            pass  # headless/stub masters (tests) may not have a geometry mgr
        self._app_state = app_state

        # IMPORTANT: all state attributes must be assigned *before* any child
        # widget is constructed.  The headless test harness stubs CTk widget
        # __init__ with a bare object (no .tk), so if super().__init__'s
        # internal destroy() ever fell through to our destroy() override,
        # referencing unset attributes would raise AttributeError mid-build.
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

        # Two stacked button rows: only one is ever packed; _sync_buttons()
        # swaps which one by re-packing it *in its original slot*.  The rows
        # are built up-front (before any other child) because CTk's
        # CTkBaseClass.__init__ destroys its own pre-widget children via the
        # plain tkinter Frame.destroy — i.e. OUR destroy() override can run
        # while __init__ is still executing, so anything it touches must
        # already exist by then.
        self._btn_row = ctk.CTkFrame(self, fg_color="transparent")
        cd_row = ctk.CTkFrame(self, fg_color="transparent")
        self._cd_btn_row = cd_row
        # Countdown-only controls (hidden while in stopwatch mode).  Packed
        # at the same position as btn_row so both modes occupy identical
        # vertical space inside the fixed-height card.
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
        self._header = ctk.CTkLabel(
            self, text="Study Stopwatch",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED_TEXT,
        )
        # Compact paddings inside the trimmed fixed-height card.  Symmetry
        # rule for the request "bottom gap == top gap": the header keeps a
        # 6 px top gap and the button rows end with a matching 6 px bottom
        # gap.  CARD_HEIGHT is pinned to exactly the stacked content height
        # (6 + header + 2 + display + row + 6) so pack leaves no slack at
        # the base — measured on Xvfb this puts the visible digits-to-
        # buttons gap at ~7 px and the button-to-edge gap at 6 px.
        self._header.pack(pady=(6, 2))

        self._display = ctk.CTkLabel(
            self, text=self._display_text(),
            font=ctk.CTkFont(size=22, weight="bold", family="Consolas"),
            text_color=TIME_BLUE,
        )
        # Tight stack: pack only pads *between* widgets, so every pixel of
        # slack left in the fixed-height card would otherwise pool beneath
        # the last row and make the base look too long.  The display keeps
        # zero outer pad; the rows below carry the real gaps.
        self._display.pack(pady=0)

        # btn_row was created earlier as self._btn_row (see note there).
        # 6 px bottom gap mirrors the header's 6 px top gap (see above).
        self._btn_row.pack(pady=(0, 6))

        self._start_btn = ctk.CTkButton(
            self._btn_row, text="Start", width=52, height=26, corner_radius=8,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_start,
        )
        self._start_btn.pack(side="left", padx=3)

        self._stop_btn = ctk.CTkButton(
            self._btn_row, text="Stop", width=52, height=26, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
            text_color=NEUTRAL_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_stop,
        )
        self._stop_btn.pack(side="left", padx=3)

        self._reset_btn = ctk.CTkButton(
            self._btn_row, text="Reset", width=58, height=26, corner_radius=8,
            fg_color=DANGER, hover_color=DANGER_HOVER, text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_reset,
        )
        self._reset_btn.pack(side="left", padx=3)

        # Pack *immediately after* btn_row so the two rows are stacked in a
        # fixed order; _sync_buttons swaps which one is visible by
        # re-packing it in place (before= the other row).
        cd_row.pack(after=self._btn_row, pady=(0, 6))

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

    # Long countdowns (the 1 h / 2 h presets) must not park a single
    # ``after(3_600_000, finish)`` job: some Tk event loops — notably when the
    # interpreter is only serviced intermittently by ``update()`` polling, as
    # in automated test harnesses — never dispatch such far-future timers.
    # The finish leg is therefore split into short hops that re-arm while they
    # run; each hop also refreshes the display, so the countdown stays alive
    # even if the separate 200 ms tick loop is starved.
    _CD_MAX_HOP_MS = 5_000

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
        """Arm a deferred finish check at (or before) the moment remaining
        hits 0.

        The tick loop also watches for zero, but ``after`` callbacks are
        *not* dispatched while a modal dialog (grab_set + wait_window) is
        open — whereas scheduled jobs still run once the mainloop resumes.
        Scheduling the finish explicitly guarantees completion always fires
        (recording the session and re-enabling the start buttons), even if
        the countdown "ran out" behind an open dialog.

        The wait is chopped into hops of at most ``_CD_MAX_HOP_MS`` which
        re-arm from inside the callback chain, so a multi-hour countdown is
        never represented by one long-future timer that could be dropped.
        """
        self._cancel_cd_finish()
        delay_ms = max(0, int(round(self._remaining_s() * 1000)))
        self._cd_finish_job = self.after(min(delay_ms, self._CD_MAX_HOP_MS),
                                         self._on_cd_finish_due)

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
            return
        # Hopped close to zero but not there yet: keep the chain alive and
        # refresh the display on the way (belt-and-braces against a starved
        # tick loop).
        try:
            self._display.configure(text=self._display_text())
        except Exception:
            pass
        self._schedule_cd_finish()

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
        # Stop only pauses the run: the reading is kept so it can be
        # resumed, or logged later by pressing Reset.

    def _log_stopped_run_if_long_enough(self) -> None:
        """Credit a finished stopwatch run as a custom study session.

        Called from **Reset** (the moment that ends a stopwatch run for
        good).  Runs under the 10-minute threshold are simply discarded.
        """
        if self._accumulated_s < AUTO_LOG_THRESHOLD_S - _EPS_S:
            return
        minutes = int(self._accumulated_s // 60) // ROUND_TO_MINUTES \
            * ROUND_TO_MINUTES
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
        # Reset works whether running or not: bank any open segment, credit
        # the run if it cleared the threshold, then drop all accumulated
        # time and return the display to 0:00:00.
        if self._running and self._segment_start is not None:
            self._accumulated_s += time.monotonic() - self._segment_start
            self._segment_start = None
        self._log_stopped_run_if_long_enough()
        self._running = False
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
            # Swap the visible rows *in place*: cd_row must go back into
            # btn_row's original slot (before it in the packing order).
            # A plain pack() would append to the end of the order, so after
            # a start/cancel cycle the Start/Stop/Reset row would land
            # *below* the countdown row and the fixed-height card would
            # clip it off the bottom edge.  ``before=`` requires the anchor
            # to be packed, hence the try/except fallback for the very
            # first sync (both rows still unpacked at construction time).
            try:
                self._cd_btn_row.pack(before=self._btn_row, pady=(0, 6))
            except (tk.TclError, AttributeError):
                self._cd_btn_row.pack(pady=(0, 6))
            try:
                self._btn_row.pack_forget()
            except AttributeError:
                pass  # stubbed CTkFrame without Tk internals (headless tests)
            self._pause_btn.configure(
                text="Resume" if not self._cd_running else "Pause")
            return

        try:
            self._btn_row.pack(before=self._cd_btn_row, pady=(0, 6))
        except (tk.TclError, AttributeError):
            self._btn_row.pack(pady=(0, 6))
        try:
            self._cd_btn_row.pack_forget()
        except AttributeError:
            pass  # stubbed CTkFrame without Tk internals (headless tests)
        self._start_btn.configure(
            state="disabled" if self._running else "normal")
        self._stop_btn.configure(
            state="normal" if self._running else "disabled")

    # ------------------------------------------------------------- lifecycle

    def destroy(self):
        # getattr-guarded: CTk's internal __init__ teardown can invoke the
        # overridden destroy() on a half-built instance (and headless test
        # stubs have no Tk at all) — never let cleanup raise during build.
        job = getattr(self, "_tick_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._tick_job = None
        job = getattr(self, "_cd_finish_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
            self._cd_finish_job = None
        try:
            super().destroy()
        except AttributeError:
            pass  # stubbed/headless instance without Tk internals


def format_minutes(minutes: int) -> str:
    """Friendly duration like ``2 hours 30 minutes``."""
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts) if parts else "0 minutes"
