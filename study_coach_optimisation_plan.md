# Study Coach — Optimised Implementation Plan

Two independent work items: (1) responsive coach-text width, (2) user-facing terminology
("source pack" → "notes"). No code is implemented yet; this plan is based on inspection of
the current branch (`main`, merge of `new-rickycode`).

---

## Work Item 1 — Coach text must fill the bubble at every window size

### Problem (verified in code)

The chat text column is bounded by `wraplength`, which Tk treats as a **hard cap**
(text wraps when it reaches it, never uses more). Three separate caps currently fight
each other, and the smallest one wins:

| Location | Current behaviour | Effect when maximised |
|---|---|---|
| `app/gui/widgets/chat_bubble.py` lines 11–12 | `MIN_WRAPLENGTH = 240`, `MAX_WRAPLENGTH = 900` | Text stops at **900 px** even though the bubble/pane is far wider → dead space on the right. This is the primary cause of the bug. |
| `ChatBubble._wrap_content` (lines ~62–70) | Binds to `content` frame `<Configure>`; divides `event.width` by widget scaling, then clamps to `[240, 900]` | Correctly tracks the bubble in windowed mode (which is why windowed "fits well"), but the clamp truncates growth above 900 px. |
| `CoachScreen._apply_responsive_wrapping` (coach_screen.py lines 121–143) | Sets `self.header` / `self.notice` wraplengths from `self.content.winfo_width() - 8`, clamped to the same `[240, 900]` | Header/notice also strand at 900 px; additionally the `- 8` fudge does not account for scrollbar inset or DPI scaling correctly. |
| `header` (line 42) and `notice` (line 101) constructors | Hard-coded initial `wraplength=650` | Legacy value; harmless once Configure fires but should be replaced by the shared helper's initial estimate. |

Secondary issues to fix while in here:

- `ChatBubble` binds `<Configure>` only on its inner `content` frame. A CTkFrame's child
  canvas does **not** shrink when its parent shrinks, so on window *shrink* the wraplength
  can lag until the next thread rebuild (`refresh()` destroys/recreates all bubbles).
  Binding on the bubble itself (`self`) makes shrink events reliable.
- `_initial_wraplength()` uses `toplevel_width - 120` — a magic number unrelated to the real
  chrome insets (nav rail + screen padding + pane padding + bubble padding + scrollbar).
- Scaling division happens *after* subtracting pixel constants in some paths and *before* in
  others; all chrome insets must be converted consistently (Tk reports physical px, CTk
  `wraplength` is unscaled px).

### Goal

Text always fills the box behind it (bubble / header / notice row) minus fixed chrome,
at any window size, any DPI scaling, with no maximum-width cap and no layout feedback loop.

### Design decisions

1. **Remove the upper cap entirely.** Replace `MAX_WRAPLENGTH = 900` with
   `WRAPLENGTH_CEILING = None` semantics: keep only `MIN_WRAPLENGTH = 240` as a floor
   (prevents pathological wrapping during un-mapped/near-zero Configure events).
   Rationale: the available width is already bounded by the actual widget geometry, so an
   artificial ceiling can only ever strand text — exactly the reported bug.
2. **Derive wraplength from the label's own allocated width, not the window width.**
   For each wrapped label bound to `fill='x'`, Tk's `<Configure>` gives `event.width` =
   the label's full row width. Then `wraplength = max(MIN_WRAPLENGTH,
   int(event.width / scaling) - LABEL_INNER_PAD)` where `LABEL_INNER_PAD ≈ 4` absorbs
   CTkLabel internal padding. This is inherently "always fits the box behind it",
   regardless of screen size, nav rail width, or scrollbar presence.
   - Keep the existing `content.winfo_width()` path in `CoachScreen` only as the
     *debounced fallback trigger*, but compute the target per-label via
     `label.winfo_width()` so header/notice track their own rows.
   - For `ChatBubble`: bind `<Configure>` on **both** `self` (bubble, catches shrink) and
     `content` (catches grow), both routed through one idempotent `_wrap_content`.
3. **Kill the feedback loop risk.** Because labels use `fill='x'`, their width is set by the
   manager, not by content; changing `wraplength` cannot re-trigger a width change. Add a
   guard anyway: skip `configure()` when the computed value equals the current
   `cget('wraplength')` (already done in both places — retain it).
4. **Single source of truth for the math.** Move the helpers into `chat_bubble.py` (or a new
   tiny `app/gui/widgets/responsive.py`) and import them in `coach_screen.py`:
   ```python
   MIN_WRAPLENGTH = 240          # floor only; no ceiling
   LABEL_INNER_PAD = 4           # CTkLabel internal padding allowance

   def target_wraplength(px_width: int, scaling: float) -> int:
       logical = max(1, int(px_width / (scaling or 1.0)))
       return max(MIN_WRAPLENGTH, logical - LABEL_INNER_PAD)
   ```
   Delete `MAX_WRAPLENGTH` and update the two import/call sites
   (`coach_screen.py:136–137`, `chat_bubble.py:_clamped`). If any external test imports
   `MAX_WRAPLENGTH`, remove that expectation rather than keeping a dead constant.
5. **Initial values without magic numbers.** Replace constructor defaults
   (`wraplength=650` at coach_screen.py lines 42 & 101; `toplevel_width - 120` in
   `_initial_wraplength`) with `target_wraplength(self.winfo_toplevel().winfo_width(),
   scaling)` guarded by the existing try/except, falling back to `MIN_WRAPLENGTH` while
   un-mapped. The first real `<Configure>` corrects it within one frame anyway.
6. **DPI/scaling correctness.** Always divide raw Tk pixel widths by
   `self._get_widget_scaling()` *before* subtracting the unscaled pad constant; never mix
   physical and logical pixels in the same expression.
7. **Debounce retained.** Keep the 50 ms debounce in `CoachScreen._schedule_wrap_update`;
   add the same cheap guard pattern to `ChatBubble._wrap_content` (it already compares
   before configuring; no timer needed since per-bubble work is O(labels)).

### Step-by-step tasks

1. `chat_bubble.py`:
   1.1 Remove `MAX_WRAPLENGTH`; add `LABEL_INNER_PAD` + module-level
       `target_wraplength(px_width, scaling)` helper.
   1.2 Rewrite `_initial_wraplength()` to use the helper with the toplevel width
       (fallback `MIN_WRAPLENGTH`), drop the `-120` magic number.
   1.3 Rewrite `_wrap_content(event)` to call the helper on `event.width` (no ceiling).
   1.4 Bind `<Configure>` on `self` in addition to `content`; ensure both handlers are
       idempotent and compare-before-configure.
2. `coach_screen.py`:
   2.1 Update `_apply_responsive_wrapping` to use `target_wraplength` (import from
       chat_bubble/responsive module); measure per-label `winfo_width()` instead of
       `self.content.winfo_width() - 8` (fall back to content width if a label is
       un-mapped).
   2.2 Change `header` and `notice` constructor `wraplength=650` to the helper-based
       initial value (or simply omit and let `_apply_responsive_wrapping` set it on first
       Configure; verify no flash of unwrapped single-line text — if there is, keep the
       computed initial).
   2.3 Confirm `_responsive_labels` registration (line 103) still covers header + notice;
       optionally register `confidence_note` (line 57) too, since it also packs `fill='x'`
       and can carry long guidance text.
3. Verify no other module imports `MAX_WRAPLENGTH` (grep shows only coach_screen.py:136).

### Acceptance criteria / manual verification

Run the app (`python main.py`; requires a display — GUI tests skip headless):

- [ ] Windowed (~1000 px wide): text column unchanged vs today — hugs the bubble, no
      regression at small sizes; minimum-size window still floors at 240 px.
- [ ] Maximised on 1920 px and 2560 px screens: coach replies extend across the full
      bubble width (minus ~4 px padding); no stranded right-hand gap; no horizontal
      scrollbar appears in the thread.
- [ ] Shrink after growing (without closing): text re-wraps narrower promptly (bubble-level
      Configure binding), no stale wide wraplength.
- [ ] Windows 125 %/150 %/200 % display scaling: text still fits the box (physical→logical
      conversion verified); compare against `notes_screen.py`'s documented scrollbar-inset
      handling for consistency.
- [ ] Resize storm (drag maximise/restore repeatedly): no flicker/jank; debounced updates;
      no Tcl errors from destroyed widgets mid-resize (existing try/except guards retained).
- [ ] Long coach reply (>900 chars) and short reply both render flush with the card border.

### Automated tests to add (tests/test_coach_gui.py style, skip cleanly headless)

- Unit-test `target_wraplength`: floor at `MIN_WRAPLENGTH`, linear growth, scaling divisor
  (e.g. `target_wraplength(1900, 2.0)` ≈ 946 — proves the old 900 ceiling is gone).
- GUI test: build `MainWindow`, open coach with a seeded session (reuse fixture pattern in
  `test_unconfigured_coach_opens_and_only_prompts_on_interaction`), set geometry to
  1600x900, pump events, assert every `ChatBubble` message label has
  `wraplength > 900` and `<= bubble_logical_width`.
- GUI test: shrink geometry to 700x600, pump, assert wraplength decreased accordingly.

### Out of scope

- Composer textbox, citation chips, suggestion buttons (already `fill='x'`/side-packed).
- Other screens' wraplengths (Statistics/Notes untouched).
- Any change to session/service logic.

---

## Work Item 2 — Never say "source pack" to the user; say "notes"

### Where the phrase can leak (verified)

The model's coaching prose comes from two system prompts in `app/core/agent/prompt.py`.
Both mention the term, which primes the model to echo it in learner-facing text:

- Line 22 (`SYSTEM_PROMPT`): "Ground quotations only in the supplied **source pack**, …"
- Line 25 (`SYSTEM_PROMPT`): "…short verbatim excerpts from the **source pack**; …"
- Line 26 (`SYSTEM_PROMPT`): `{"source_id":"source ID from the pack", …}` — "from the pack".

`CONVERSATION_PROMPT` (lines 4–16) already says "sources"/"notes" and contains no
"source pack" — good baseline.

Non-leaking occurrences (do NOT touch — they are payload keys/internal docstrings):

- `payload['source_pack']` JSON key at prompt.py lines 36, 72, 89 — data contract with the
  provider/validation code; renaming would ripple through `coach_service`/schema parsing.
- `app/state/app_state.py:740` docstring ("Assemble the SEL-2 source pack…") — internal.
- `tests/test_coach.py:407` docstring — internal.
- Requirements docs — documentation only.

Note: the UI itself already uses "notes/PDFs/sources" wording (e.g. coach_screen.py line
280 "select your notes or PDFs"); the only realistic channel for "source pack" reaching the
user is model output.

### Step-by-step tasks (prompt wording only — zero logic changes)

1. In `SYSTEM_PROMPT`, replace the two "source pack" mentions and one "the pack" with
   learner-facing-neutral wording that keeps grounding semantics intact:
   - Line 22 → "Ground quotations only in the supplied notes, copying source_id and page
     exactly."
   - Line 25 → "…short verbatim excerpts from the notes; use an empty list when none are
     needed."
   - Line 26 → `"source_id":"source ID from the notes"` (or simply "a source ID").
2. Add one explicit instruction line to `SYSTEM_PROMPT` (adjacent to the existing
   "Reply naturally"/prose rules, e.g. after line 25):
   "When addressing the learner, always call the material their **notes**; never use the
   terms 'source pack', 'pack', or 'spans' in reply text."
   (Keep "source_id"/"page" field names untouched — they are JSON mechanics, not prose.)
3. Mirror the same vocabulary rule in `CONVERSATION_PROMPT` (one added line), because live
   conversational turns use that prompt instead of `SYSTEM_PROMPT`
   (`messages()` branches at lines 38–40). Suggested placement: after line 15, e.g.
   "Refer to the material as the learner's notes in your replies; never say 'source pack'."
4. Do not modify: `messages()` payload assembly, repair flow, assessment prompts, schema
   validation, or the `source_pack` JSON key anywhere.

### Safeguards / verification

- Check nothing asserts on exact prompt strings: grep tests for
  `SOURCE_PACK`/`'source pack'` string assertions against `messages(...)` output —
  `tests/test_reply_repair.py` and `tests/test_coach.py` import `messages`; review their
  assertions and update only if they match the changed sentences (expected: they assert on
  structure/payload, not prose).
- Run `pytest tests/test_coach.py tests/test_reply_repair.py` headlessly (non-GUI subset).
- Manual end-to-end: start a coached session with a note, request a question + feedback,
  confirm replies say "your notes"; deliberately ask "what is a source pack?" and confirm
  the coach reframes as "your notes". Repeat in conversational mode (send a free-form
  message) to exercise `CONVERSATION_PROMPT`.
- Residual-risk note: prompt instructions reduce but cannot guarantee phrasing. Optional
  belt-and-braces (flag as follow-up, not part of this change unless requested): a
  post-generation text substitution in `CoachService.complete_turn` replacing
  "source pack" → "notes". Deliberately excluded now per "do not change any other logic".

### Acceptance criteria

- [ ] Neither `SYSTEM_PROMPT` nor `CONVERSATION_PROMPT` contains the phrase "source pack"
      or refers to "the pack".
- [ ] Model-visible JSON payload keys unchanged (`source_pack` still present; grounding
      validation tests pass untouched).
- [ ] Sample coach outputs refer to material as "notes" in question, hint, feedback and
      conversational turns.
- [ ] All existing non-GUI tests pass.

---

## Suggested execution order & estimates

| # | Task | Files touched | Est. |
|---|---|---|---|
| 1 | Responsive-wrap helper + ChatBubble rewrite | `app/gui/widgets/chat_bubble.py` | 1 h |
| 2 | CoachScreen header/notice wiring | `app/gui/screens/coach_screen.py` | 0.5 h |
| 3 | New unit + GUI resize tests | `tests/` | 1 h |
| 4 | Prompt rewording (terminology) | `app/core/agent/prompt.py` | 0.5 h |
| 5 | Test sweep + manual multi-DPI/maximised check | — | 0.5 h |

Items 1–3 and 4–5 are independent and can ship as separate commits/PRs.
