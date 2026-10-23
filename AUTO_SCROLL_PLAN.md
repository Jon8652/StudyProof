# Study Coach — Automatic Chat Scrolling: Audit & Optimised Implementation Plan

**Status:** plan only — no code changed yet (as requested).
**Target file:** `app/gui/screens/coach_screen.py` (+ new tests in `tests/test_coach_gui.py`).
**Context:** HEAD is the merge of PR #74, which *reverted* PR #73 ("auto-scroll-chat-implementation").
The working tree currently contains **no auto-scroll code at all**, so this is a fresh, corrected
implementation, not an incremental tweak. The design below was validated empirically against the
installed customtkinter (6.0.0, API-compatible with the required ≥5.2.0) under Xvfb before being
written up.

---

## 1. Repository findings (what I verified, not assumed)

| Finding | Evidence |
|---|---|
| The chat thread is a plain `ctk.CTkScrollableFrame` (`self.thread`, line 46) with **zero** scroll management. | `grep -n "scroll" coach_screen.py` → only comments; no `yview` anywhere in `app/gui/screens/`. |
| Every state change funnels through one method: `CoachScreen.refresh()` destroys **all** thread children and re-packs a `ChatBubble` per turn. It is called after: user send (`_submitted`), coach reply (`finished`), session start/resume (`resume_session`, `revise_topic`, `sessions`), cancel, assessment completion. | `coach_screen.py` lines 165–216, 254–282, 382–437. This single choke point makes it the ideal place to arm the auto-scroll. |
| Bubbles grow **after** they are packed: each `ChatBubble` re-wraps its labels via `<Configure>` handlers (`chat_bubble.py::_wrap_content`), and window resizes re-wrap too. So "scroll once after refresh" is insufficient — content height keeps changing for several event-loop cycles. | `chat_bubble.py` lines 60–91; measured: one bubble text change produced frame heights `1080 → 1987 px` across later idletasks. |
| `CTkScrollableFrame` internals (customtkinter ≥5.2): the real scroller is `sf._parent_canvas` (a `tk.Canvas`) whose `yscrollcommand` drives `sf._scrollbar`; the frame itself is a `tk.Frame` embedded via `create_window(anchor='nw')`; CTk binds the frame's `<Configure>` to update `scrollregion`. CTk sets `yscrollincrement` to **30 px** on Windows / 8 elsewhere — coarse steps make pixel-exact pinning impossible as-is. | Inspected `ctk_scrollable_frame.py` source live. |
| **Measured behaviour that dictates the design:** when content grows while the view sits at the bottom, Tk does **not** keep you pinned — the visible fraction drifts (measured `(0.733, 1.0) → (0.398, 0.543)`). A `<Configure>` **does** fire on the thread frame whenever content height changes, which is the reliable trigger to re-pin. | Xvfb probe (see §6 test notes). |
| Pixel-exact "bottom" is achievable without fighting CTk's increment: temporarily set `yscrollincrement=1`, then `yview_scroll(+N, 'units')` with a large N clamps exactly to the bottom (verified: last widget sat 4 px off the viewport edge = its own pack pady). `yview_moveto(1.0)` snaps to the scrollbar thumb's end but can leave a few pixels unclamped because of the 30 px quantisation. | Xvfb probe. |
| Wheel scrolling on the thread goes through CTk's `_mouse_wheel_all` bound on the canvas/widgets — there is no app-level `bind_all("<MouseWheel>")` that would conflict, and dialogs already use `bind_all("<Button-1>")` only for focus. | `grep bind_all app/` → page_viewer + dialog base only. |
| GUI tests exist and skip cleanly headless (`test_coach_gui.py::pump`, mapped-window pattern `+3000+3000`); this environment now has tk/xvfb installed so they run. | `tests/conftest.py`, `tests/test_coach_gui.py`. |

---

## 2. Problems likely to have reverted PR #73 (defects my design fixes)

Any first attempt at this feature typically fails on one or more of these; each is addressed explicitly below. Treat this list as the acceptance checklist the previous implementation did not pass:

1. **Scrolling before geometry exists.** Calling `yview_moveto(1.0)` synchronously inside `refresh()` runs while the freshly-packed bubbles still report `winfo_height()==1` and the scrollregion is stale → it scrolls to the wrong place (often nowhere). Needs deferred, geometry-aware application.
2. **Growth after the scroll.** Because wraplength is applied asynchronously by `<Configure>`, the thread gets *taller after* the scroll landed, leaving the newest message clipped again — the exact symptom reported. Needs continuous re-pinning until growth settles.
3. **Coarse `yscrollincrement` (30 px on Windows).** `yview_scroll(delta_px, 'units')` computed from raw pixel differences jumps in 30 px steps → misaligned, jittery final position. Needs the increment temporarily forced to 1 px.
4. **Fighting the user.** If the implementation force-scrolls on every timer tick, a user who scrolled up to read history gets yanked back down on the next layout pass. Needs a "stick to bottom" flag driven by actual position + wheel/drag detection.
5. **Session switching direction.** "Always scroll down" is wrong when resuming a *shorter* session while parked mid-scroll — the view must land at the bottom regardless of whether that means moving up or down. Needs absolute pinning, never relative scrolling.
6. **Widget-teardown races.** `after()` callbacks firing after `shutdown()`/`destroy()` (the screen has background workers posting into `UIEventQueue`) raise `TclError`. Needs `winfo_exists()` guards and job cancellation in `shutdown()`.
7. **Resize storms.** Window resize re-wraps every bubble; naive per-event `update_idletasks()` calls cause flicker/reentrancy. Needs debounced, idle-scheduled reads only.

---

## 3. Optimised design (chatbot-style "stick to bottom")

### 3.1 State on `CoachScreen.__init__`

```python
# --- Auto-scroll ("stick to bottom") state ------------------------------
# Mirrors how DeepSeek/Qwen chat clients behave: the newest message is
# always kept in view unless the user deliberately scrolls away to read
# history.  All values are main-thread only; nothing touches Tk from a
# worker (turn results already arrive via UIEventQueue).
self._stick_to_bottom = True        # False only while the user browses history
self._pin_job = None                # pending after_idle pin callback
self._settle_job = None             # periodic settle-poll after a rebuild
self._last_thread_height = -1       # height seen at previous settle poll
self._thread_needs_pin = False      # set by refresh(); consumed at idle time
```

### 3.2 Wire-up (end of `__init__`, after `self.thread` exists)

```python
canvas = getattr(self.thread, '_parent_canvas', None)
if canvas is not None:
    self._thread_canvas = canvas
    # 1 px steps let _apply_thread_pin() land exactly on the last bubble's
    # bottom edge instead of CTk's default 30 px Windows increment.
    canvas.configure(yscrollincrement=1)
    # Any wheel motion over the thread decides stickiness from the *real*
    # post-scroll position (see _on_thread_wheel).
    canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', self._on_thread_wheel, add='+'), add='+')
    canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'), add='+')
    for seq, sign in (('<Button-4>', 1), ('<Button-5>', -1)):   # Linux/Xvfb wheels
        canvas.bind(seq, lambda e, s=sign: self._on_thread_wheel(s), add='+')
    # Dragging the CTk scrollbar thumb also routes here (its command is the
    # canvas yview), so a manual drag-up releases the pin too.
    orig_yview = canvas.yview
    def tracked_yview(*args, **kw):
        result = orig_yview(*args, **kw)
        if args and args[0] == 'scroll' and len(args) >= 3 and int(args[1]) < 0:
            self._user_left_bottom_hint()          # conservative: upward scrolls re-check position
        return result
    canvas.yview = tracked_yview
```

*Why Enter/Leave-scoped `bind_all`: CTkScrollableFrame already binds wheel events; binding them
`add='+'` directly on the widgets would double-handle. Observing globally only while the pointer is
over the thread matches what the user sees and is removed the moment they leave. (If the reverted PR
bound wheel handlers permanently, that alone could hijack wheel input for sibling panes — another
plausible revert cause.)

*Robustness note:* `_parent_canvas` is private CTk API, stable since 5.0, but every access site
guards with `getattr(..., None)` and falls back to `yview_moveto(1.0)` on the frame's own proxy if
it ever disappears — the feature degrades to "roughly at bottom", never crashes.

### 3.3 Core primitives

```python
BOTTOM_EPSILON_PX = 4   # tolerance so sub-pixel rounding/DPI scaling doesn't unstick the view

def _thread_at_bottom(self) -> bool:
    canvas = self._thread_canvas
    if canvas is None or not canvas.winfo_exists():
        return True
    first, last = canvas.yview()
    if last >= 1.0:
        return True
    region = canvas.bbox('all')
    if region is None:
        return True                      # nothing scrollable at all
    return region[3] - canvas.winfo_height() <= BOTTOM_EPSILON_PX

def _scroll_thread_to_bottom(self) -> None:
    """Absolute pin — works whether reaching the bottom means scrolling up or down."""
    canvas = self._thread_canvas
    if canvas is None or not canvas.winfo_exists():
        return
    try:
        canvas.yview_scroll(10_000_000, 'units')   # clamps exactly to scrollregion bottom
    except Exception:
        try:
            canvas.yview_moveto(1.0)               # fallback if private API shape changes
        except Exception:
            pass

def _schedule_thread_pin(self) -> None:
    """Collapse all pin requests in one event-loop cycle into a single idle callback."""
    if self._pin_job is not None:
        return
    try:
        self._pin_job = self.after_idle(self._apply_thread_pin)
    except Exception:
        pass

def _apply_thread_pin(self) -> None:
    self._pin_job = None
    if not self.winfo_exists():
        return
    self._scroll_thread_to_bottom()

def _user_left_bottom_hint(self) -> None:
    # Called from wheel/scrollbar observation; decide from the settled position.
    self.after_idle(lambda: None if self._thread_at_bottom() else setattr(self, '_stick_to_bottom', False))
```

### 3.4 The two triggers

**a) New content (send / coach reply / session switch) — modify `refresh()`:**

```python
# at the very end of refresh(), after the bubbles are (re)packed:
if session is not None:                    # don't fight the empty-state pane
    self._stick_to_bottom = True           # session switch ⇒ ALWAYS land at bottom
    self._thread_needs_pin = True
    self._schedule_thread_pin()
    self._start_settle_poll()
else:
    self._stop_settle_poll()
```

- Putting the arming inside `refresh()` covers *every* call path at once
  (`_submitted`, `finished`, `resume_session`, `new_session`, `revise_topic`,
  `assessment_finished`, `cancel`) — no risk of missing a hook, which is how
  per-callsite implementations rot.
- Setting `_stick_to_bottom = True` on refresh is what implements the second
  requirement: switching sessions scrolls to the bottom **of the new session**
  even when that means scrolling *up* (absolute pin, not a delta).

**b) Late growth (async re-wrap, streamed-looking replies, resize) — settle poll + sticky check:**

```python
SETTLE_POLL_MS = 60
SETTLE_MAX_TICKS = 12                     # ~0.7 s worst case; stops early when height stabilises

def _start_settle_poll(self) -> None:
    self._stop_settle_poll()
    self._last_thread_height = -1
    self._settle_ticks = 0
    self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)

def _stop_settle_poll(self) -> None:
    if self._settle_job is not None:
        try: self.after_cancel(self._settle_job)
        except Exception: pass
        self._settle_job = None

def _settle_tick(self) -> None:
    self._settle_job = None
    if not self.winfo_exists() or not self._stick_to_bottom:
        return                              # user took over, or screen is gone
    canvas = self._thread_canvas
    if canvas is None or not canvas.winfo_exists():
        return
    self._settle_ticks += 1
    region = canvas.bbox('all')
    height = region[3] if region else 0
    if height != self._last_thread_height:  # bubbles still growing/shrinking
        self._last_thread_height = height
        self._scroll_thread_to_bottom()
        self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
    elif self._settle_ticks < SETTLE_MAX_TICKS:
        # Height looks stable, but Tk may still hold queued Configure events
        # (e.g. a wraplength burst). One extra quiet tick, then stop.
        self._scroll_thread_to_bottom()     # cheap no-op when already pinned
        self._settle_job = self.after(SETTLE_POLL_MS, self._settle_tick)
    # else: settled — stop polling entirely (zero idle cost between messages)
```

Plus one cheap reactive hook so growth *after* the settle window (rare, e.g. a resize re-wrap while
already stuck at bottom) still keeps the newest bubble visible:

```python
def _on_thread_configure(self, event=None):
    if self._stick_to_bottom and not self._thread_needs_pin:
        self._schedule_thread_pin()
```
bound with `add='+'` on `self.thread` (it fires exactly when content height changes — verified).
Guard against recursion: `_apply_thread_pin` changes the *canvas origin*, which re-fires the frame's
`<Configure>`; the `_thread_at_bottom()` short-circuit inside `_scroll_thread_to_bottom`
(`if last >= 1.0: return` before touching Tk) keeps this loop bounded to one redundant no-op.

**c) Unstick / restick (user agency):**

```python
def _on_thread_wheel(self, direction_or_event):
    """Direction > 0 = scrolled down. Decide from the post-scroll position."""
    if self._thread_at_bottom():
        self._stick_to_bottom = True
    else:
        self._stick_to_bottom = False
```
Resticking happens automatically on the next `refresh()` (sending a message or receiving a reply
puts you back at the bottom — standard chat-client behaviour), so no "jump to latest" button is
required for this scope.

### 3.5 Shutdown hygiene — extend `shutdown()`

```python
self._stop_settle_poll()
for attr in ('_pin_job',):
    job = getattr(self, attr, None)
    if job is not None:
        try: self.after_cancel(job)
        except Exception: pass
        setattr(self, attr, None)
try:
    self.unbind_all('<MouseWheel>')
except Exception:
    pass
```
(The screen's `shutdown()` is already invoked by tests/main window teardown; cancelling jobs here
fixes defect #6 outright.)

---

## 4. Why this is the optimised version (design rationale)

| Decision | Rationale / alternative rejected |
|---|---|
| Arm inside `refresh()` (single choke point) | Per-callsite hooks (`send`, `finished`, …) miss paths like `assessment_finished` and future ones; `refresh()` is already the invariant "thread was rebuilt" signal. |
| Absolute pin (`yview_scroll(+huge,'units')` with `yscrollincrement=1`) instead of `scroll_last_child_into_view()` | Verified: `bbox(last_widget)` returns `None` for embedded-frame items in this CTk version, and `moveto(1.0)` leaves a quantisation gap; the clamped units-scroll lands pixel-exact (4 px = the bubble's own pady) and inherently handles "bottom might be *above* current view" (session switch). |
| Debounced `after_idle` coalescing + bounded settle poll (≤12 ticks, self-stopping) | Fixes late-growth clipping (#2) without a permanent 60 ms timer running all session; `after_idle` guarantees geometry is recomputed before we read it (#1). |
| Sticky-bottom flag judged from *position*, not event count | Position-based checks survive DPI scaling, synthetic events, and CTk's internal wheel handling; event-count heuristics mis-fire on trackpads. Epsilon of 4 px prevents flapping (#4). |
| `bind_all` wheel observer scoped by Enter/Leave + symmetric unbind | Avoids double-binding CTk's own handler and avoids stealing wheel events from other panes when the pointer isn't over the thread. |
| Everything guarded with `winfo_exists()` + broad `except Exception` on Tk calls | Matches the codebase's existing defensive style (`_apply_responsive_wrapping`, tooltip churn comment); worker-posted callbacks can race teardown (#6). |
| No changes to `ChatBubble`, service layer, or threading model | Turn commits already happen on the main thread via `UIEventQueue`; the feature is pure view logic. Smallest possible diff surface for review. |

Performance: between messages there is **zero** recurring work (poll self-terminates); during a
rebuild the cost is ≤12 × (one `bbox` + one `yview` read + at most one scroll) ≈ negligible.

---

## 5. Exact edit map

1. `CoachScreen.__init__`
   – add state block (§3.1) near the other scheduling fields (~line 30);
   – add canvas wiring (§3.2) right after `self.thread.pack(...)` (~line 47);
   – add `self.thread.bind('<Configure>', self._on_thread_configure, add='+')`.
2. `CoachScreen.refresh()` — append the arming block (§3.4a) at the end (~line 437).
3. New methods `_thread_at_bottom`, `_scroll_thread_to_bottom`, `_schedule_thread_pin`,
   `_apply_thread_pin`, `_start_settle_poll`, `_stop_settle_poll`, `_settle_tick`,
   `_on_thread_configure`, `_on_thread_wheel` — grouped under a `# Auto-scroll` section header,
   following the file's existing comment-dense style.
4. `CoachScreen.shutdown()` — add job cancellation + `unbind_all` (§3.5).
5. Constants `BOTTOM_EPSILON_PX`, `SETTLE_POLL_MS`, `SETTLE_MAX_TICKS` at module top.

No public API changes; `resume_session`, `sessions`, `new_session` need **no** individual edits
(they all reach `refresh()`).

---

## 6. Verification plan (to run when implementing)

Headless-capable GUI tests appended to `tests/test_coach_gui.py` (same skip-if-no-display pattern,
mapped window at `+3000+3000`):

1. **Long session pins to bottom:** seed a session with ~25 turns (reuse `CoachTurn.create` fixture
   pattern from `test_coach_text_width_tracks_the_bubble_at_every_window_size`), small window
   (e.g. `700x400`), `screen.resume_session(sid)`; pump until settle; assert
   `screen._thread_at_bottom()` and that the last bubble's root bottom ≤ canvas viewport bottom
   (i.e. newest message fully visible).
2. **Send arms auto-scroll:** scripted provider (`scripted_provider` fixture) → `screen.composer.insert(...)`,
   `screen.send()`; after `pump(app, lambda: not screen.busy)`, assert pinned again.
3. **Session switch scrolls both directions:** open long session A (pinned), then resume short
   session B; assert pinned; resume A again; assert pinned (this exercises the "up or down" rule).
4. **User scroll-up releases the pin:** synthetically `canvas.yview_moveto(0.3)` + invoke the wheel
   handler; then add another turn via refresh; assert `not screen._stick_to_bottom` and the view
   was not yanked back.
5. **Shutdown cancels jobs:** after `screen.shutdown()`, assert `screen._settle_job is None` and no
   TclError on subsequent `app.update()`.
6. Regression: full `pytest tests/ -x` under `xvfb-run` (environment already prepared: tk 8.6.13,
   customtkinter 6.0.0, Xvfb+xauth installed). Manual checklist mirrors §2 items 1–7 plus Windows
   125 %/150 % scaling (increment trick is scaling-independent because it clamps, not computes).

Empirical facts already proven in this environment (probe used in §1): growth fires the frame
`<Configure>`; clamped units-scroll lands exactly at bottom; drift occurs without re-pinning;
`tk event queue` introspection unavailable (older Tcl) — hence position-based, not event-based,
correctness.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| `_parent_canvas` private-API coupling | Single accessor, `getattr` guard, `yview_moveto(1.0)` fallback; pinned to customtkinter≥5.2 where the attribute exists. |
| Pin fights scrollbar-thumb dragging | Thumb drags route through `canvas.yview('scroll', negative, ...)` via the wrapped command → `_user_left_bottom_hint` re-checks position at idle. |
| Rapid refresh bursts (cancel + assessment + finished in one cycle) | `after_idle` coalescing means one pin per loop cycle regardless of refresh count. |
| Empty session (`thread` shows only placeholder) | Arming skipped when `session is None`; poll stops. |
| Very long single bubble taller than viewport | Bottom-pinning shows its tail (standard chatbot behaviour); user can scroll up freely — pin releases instantly. |

---

Say the word and I'll implement exactly this plan in `coach_screen.py` plus the five tests, and run
the full suite under Xvfb.
