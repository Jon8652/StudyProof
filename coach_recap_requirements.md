# Requirements Document — Coach Recap (StudyProof)

**Feature:** "Coach Recap" button on the Study Coach screen
**Status:** Draft — requirements only, no implementation agreed yet
**Date:** 2026-10-25
**Related IDs:** COACH-, REV-, GEN-, PROV-, UI-, ERR-, DATA- (see *AI Requirements Document — StudyProof v2*)

---

## 1. Summary

Add a **Coach Recap** button to the Study Coach screen. Pressing it produces a short,
friendly recap of the student's last **7 days**, covering four sections: their effort,
their learning, loose ends (unanswered Coach questions), and one or two concrete next
steps. The recap is generated from the student's own data and is always framed against
**their personal goal and recent routine** — never against generic thresholds. After the
recap, the app offers an optional **30-day recap**.

### Goals
- Give students a single-glance weekly picture of study effort, learning progress, and unresolved work.
- Make the "next step" advice actionable and small (one or two suggestions, not lists).
- Keep the tone supportive: observations + gentle invitations, never judgment or pressure.

### Non-goals (v1)
- No scheduled/automatic recaps (push, notifications, startup prompts).
- No export/printing of the recap.
- No historical archive of past recaps beyond being stored as coach-session turns (see RECAP-24).
- No new goals-setting UI; the recap consumes whatever goal/routine data already exists (see Open Questions, OQ-1).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Recap window** | The period the recap covers: rolling last 7 calendar days (today inclusive) for the default recap; last 30 days for the extended recap. |
| **Baseline routine** | The student's typical recent study pattern, derived from their own history prior to the recap window (e.g. trailing 4 weeks before the window: sessions/week, minutes/day studied, days studied/week). |
| **Personal goal** | The student's chosen target if one exists (e.g. weekly study-time or sessions goal). If no explicit goal is set, the baseline routine serves as the implicit goal. |
| **Weak topic** | A topic meeting the existing weak-points rule (REV-1). |
| **Improved topic** | A previously-weak topic that no longer meets the weak threshold, or whose weak score decreased, during the recap window (see RECAP-7 for the exact derivation). |
| **Loose end** | A Coach question in status `open` (never answered) belonging to a coach session started within the recap window. |

---

## 3. User Stories / Acceptance Scenarios

1. **Weekly check-in.** As a student, I can press *Coach Recap* on the Coach screen and read a short summary of my last 7 days — time studied, sessions, days studied, topics practised, weak topics improved, topics still needing revision, unanswered questions, and one or two next steps.
2. **Resume what I left.** As a student with unanswered Coach questions, each loose end in the recap shows a *Resume* button that opens the exact session (and scrolls to the open question) so I can continue where I stopped.
3. **No guilt trip.** As a student who studied less than usual, my recap acknowledges it neutrally and suggests one small, specific way back into routine — it does not scold, alarm, or compare me to other students.
4. **No false rest prompt.** As a student who had a heavier-than-usual week but is on track for my goal, my recap celebrates the effort and does not tell me to take a break unless the pattern genuinely warrants it (see RECAP-17).
5. **Extended view.** After reading the 7-day recap, I can accept an offer to see a 30-day recap instead, or decline and return to normal Coach use.
6. **Offline / provider failure.** If live generation fails or no provider is configured, I still get a fully data-accurate fallback recap assembled locally (fixed wording), with the same sections and Resume buttons.

---

## 4. Functional Requirements

### 4.1 Entry point and flow

| ID | Requirement |
|---|---|
| RECAP-1 | A button labelled **"Coach Recap"** shall be added to the top action row of the Coach screen, alongside *New session*, *Sessions…* and *Revise Weak Topics*. It follows the existing button styling and accessibility conventions (UI- requirements), with a tooltip: *"A gentle summary of your last 7 days."* |
| RECAP-2 | Pressing the button while a live generation is busy (`busy == True`) shall be disabled/greyed consistent with the other action buttons; pressing it starts a new recap request and cancels any pending recap request (manual input cancels pending work, per existing coach-screen behaviour). |
| RECAP-3 | The recap renders inside the current Coach conversation thread as an assistant-style message block with the four sections defined in §4.2–§4.5, using the existing ChatBubble rendering. It does not create a new coach session. While generating, show an inline "Putting together your recap…" state; the composer and other actions behave as during any in-flight turn (COACH-15 cancellation rules apply). |

### 4.2 Section 1 — Your effort (last 7 days)

| ID | Requirement |
|---|---|
| RECAP-4 | Shall report: **total study time** (sum of recorded session durations attributed to the window, using the existing day-attribution rules), **number of study sessions recorded**, and **number of distinct days studied** (0–7). |
| RECAP-5 | Figures are derived at request time from existing session data (derived-data policy A-33: nothing new is stored that can be recomputed). Zero values are displayed plainly ("No sessions recorded this week") without negative framing. |

### 4.3 Section 2 — Your learning

| ID | Requirement |
|---|---|
| RECAP-6 | Shall list **topics practised** in the window (topics touched by study sessions or coach activity, deduplicated, most-practised first; cap display at ~6 with "+n more"). |
| RECAP-7 | Shall identify **weak topics improved**: topics that were weak at the start of the window and are no longer weak (or whose weak score dropped) now. Derivation must reuse the REV-1 scoring inputs (confidence, revisions, hints) evaluated over the window vs. before it. |
| RECAP-8 | Shall identify **topics still needing revision**: currently weak topics (REV-1 score ≥ 2) that were also weak before the window, i.e. unchanged-or-worse. Sorted by weak score descending, then most recent activity. |
| RECAP-9 | If there is no coach/Q&A activity in the window, the learning section degrades gracefully to the effort section ("You haven't logged any coached practice in this period — that's fine, here's what your week looked like…"). |

### 4.4 Section 3 — Loose ends

| ID | Requirement |
|---|---|
| RECAP-10 | Shall list **unanswered Coach questions**: every question with `status == "open"` in coach sessions active within the window. Each row shows a truncated question text, the source/topic label, and the session's age ("asked Tue"). |
| RECAP-11 | Each loose-end row carries a **"Resume" button** that switches to that session (same mechanism as the Sessions dialog resume, UI-26/COACH-14) and scrolls the thread to that question. If the session's source has since been deleted, resuming still works and the existing missing-source handling applies (ERR-33). |
| RECAP-12 | If more than 5 loose ends exist, show the 5 most recent plus a summary line ("+3 older ones in your Sessions list"). If none: "No loose ends — every question you've been asked has an answer." |

### 4.5 Section 4 — Your next step

| ID | Requirement |
|---|---|
| RECAP-13 | Shall contain **one or two** specific, manageable suggestions (hard maximum 2). Each suggestion names a concrete topic/action and a bounded size ("one short session", "10 minutes"), drawn from: topics still needing revision, a loose end worth answering, or the routine pattern detected in RECAP-15. |
| RECAP-14 | Suggestions must be actionable from the recap itself where possible (e.g. a suggestion naming a weak topic pairs with the existing *Revise Weak Topics* affordance; a suggestion referencing a loose end sits next to its Resume button). |

### 4.6 Tone engine — comparison basis (the core rule)

| ID | Requirement |
|---|---|
| RECAP-15 | **Every tone/pattern decision compares the window's data against the student's own context only**: (a) their explicit personal goal if set, otherwise (b) their baseline routine computed from their own history before the window. The system shall never compare against population norms, fixed absolute thresholds (e.g. "under 2 hours is low"), or invented targets. |
| RECAP-16 | Low study time relative to goal/routine shall **not** automatically trigger pressure or corrective language. The recap states the observation neutrally and offers at most one small re-entry suggestion. If the drop is mild (within a defined tolerance band, e.g. ≥ ~70% of baseline/goal — final band value configurable constant), the recap treats the week as normal and uses the steady-progress voice. |
| RECAP-17 | High study time alone shall **not** imply the student needs rest. A "consider a lighter day / take a break" suggestion requires corroborating signals (e.g. streak of consecutive heavy days well above baseline, or rising hint counts / falling confidence across the window). Without corroboration, a heavy week gets the celebratory/steady voice. |
| RECAP-18 | Language shall be gentle suggestions, not judgments: invitations ("try", "consider", "when you're ready") and observations ("you studied twice this week"). Prohibited: guilt/shame phrasing, urgency ("falling behind", "must catch up"), medical/wellbeing directives beyond a soft break suggestion, and any claim about the student's character or ability. |
| RECAP-19 | Pattern → voice mapping (illustrative, non-exhaustive; live mode may vary wording, offline mode uses the fixed strings): | 

| Pattern (vs. own goal/routine) | Example response |
| :--- | :--- |
| Less study than usual/goal | "You studied twice this week. Try one short session on cell division to get back into your routine." |
| Steady progress | "You studied across four days and improved two weak topics. Keep this pace and revisit chemical bonding next." |
| Much more study than usual, with fatigue signals | "This was a heavier week than your usual routine. Consider a lighter day or a break before your next session." |
| Much more study than usual, no fatigue signals | "Big week — [X] hours across [Y] days, well above your usual pace, and your answers stayed solid. Nice work; keep going if it feels good." |
| New student, insufficient history (< 2 weeks) | Effort/learning facts only, one neutral suggestion; no comparison language at all (see RECAP-20). |

| ID | Requirement |
|---|---|
| RECAP-20 | If the student has insufficient history to build a reliable baseline (fewer than a defined minimum, e.g. 14 days of data or < 3 prior sessions), the recap shall omit comparisons entirely, present the facts, and close with a single gentle suggestion. It shall state nothing about being "behind" or "above" anything. |

### 4.7 Generation, persistence, and the 30-day follow-up

| ID | Requirement |
|---|---|
| RECAP-21 | Recap content is produced through the existing agent pipeline: one prompt-assembly location (GEN-10 pattern), structured response schema validation and parse-repair (GEN-12 pattern), and provider error handling (PROV- family). A new response `kind: "recap"` extends the schema with `sections {effort, learning, loose_ends, next_step}` and `meta.pattern` (routine-vs-goal classification). Length caps: total text ≤ 1,500 chars; ≤ 2 next-step items. |
| RECAP-22 | All numbers, topic names, and loose-end references shown in the recap must come from the locally computed recap payload (deterministic facts), not from the model. The model phrases; it does not compute. Any figure in model text that mismatches the payload is discarded in favour of the local value. Loose-end Resume buttons are wired to local session/question IDs only — the model cannot introduce new ones. |
| RECAP-23 | The rendered recap is stored as turn(s) in the current coach session's log so it persists on reopen (COACH-13 autosave semantics); deleting the session deletes the recap with it. Repeated presses append a new recap rather than replacing the previous one. |
| RECAP-24 | Immediately after a completed 7-day recap, the thread shall offer a follow-up: *"Would you like a 30-day recap instead?"* with **Yes** / **No** affordances. Yes generates the same structure over the 30-day window (baselines shift accordingly: prior routine excludes the 30-day window). No/dismiss returns the Coach to idle. A 30-day recap does not re-offer another window (no chains). |
| RECAP-25 | Cancellation follows COACH-15: cancelling an in-flight recap stores nothing partial and restores the composer within 1 s. |

### 4.8 Offline / fallback

| ID | Requirement |
|---|---|
| RECAP-26 | When live generation is unavailable (offline, no API key, provider error after retry), the app shall render a **locally assembled recap** using the same payload and fixed template strings implementing the same tone rules (RECAP-15–20), clearly identical in structure and still containing working Resume buttons. No error dialog should block the recap; a small note may indicate simplified wording. |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-R1 | Data assembly (all windows, baselines, weak-topic diffs, loose ends) runs locally in ≤ 200 ms for a library of 10k sessions / 500 coach sessions; heavy work off the UI thread (existing worker/event-queue pattern). |
| NFR-R2 | End-to-end perceived latency: loading state shown within 100 ms of press; live recap completes or falls back within the existing provider timeout budget. |
| NFR-R3 | Recap generation makes at most **one** LLM call (plus the single repair retry permitted by GEN-12); the 30-day variant is a separate single call only if the user opts in. |
| NFR-R4 | Privacy: recap payloads include only the student's own local data sent to the configured provider, consistent with existing grounding/prompt-data rules; no new data leaves the device beyond what coach turns already send. |
| NFR-R5 | Accessibility: the recap block is keyboard-navigable, Resume buttons are real focusable buttons with discernible labels ("Resume: <question snippet>"), and tone strings avoid colour-only meaning. |

---

## 6. Edge Cases and Error Handling

| ID | Situation | Behaviour |
|---|---|---|
| ERR-R1 | No study sessions and no coach activity in the 7-day window | Still produce a recap: zero-effort facts stated neutrally, no loose ends, one tiny suggestion (e.g. "When you have five minutes, ask me a question on any topic to start a thread."). No pressure language. |
| ERR-R2 | Coach history exists but no PDF/library sources | Learning section limited to session-derived topics; loose ends still listed. |
| ERR-R3 | Session containing a loose end was deleted between assembly and Resume click | Resume button becomes inert with a friendly "That session was deleted" note on click; recap itself unaffected. |
| ERR-R4 | Clock/timezone anomaly (session timestamps in future) | Window filtering uses the existing day-attribution rules; out-of-range records are ignored silently. |
| ERR-R5 | Provider returns malformed recap JSON | One repair retry (GEN-12); on second failure fall back to RECAP-26 offline rendering. Never store a partial recap. |
| ERR-R6 | Model invents a topic/figure | RECAP-22 reconciliation replaces it with local payload values; if the offending sentence cannot be reconciled, drop the sentence. |
| ERR-R7 | Very long topic lists (> cap) | Apply display caps from RECAP-6/RECAP-12; full detail remains available via Stats/Review and Sessions screens. |

---

## 7. Data / Schema Notes

- No new persisted entities required. Inputs reused: study sessions (duration, day attribution), coach sessions/questions (`status`, `first_answer`, confidence, hints, revisions), weak-topic scoring (REV-1), settings (theme/AI provider config).
- Optional new setting (pending OQ-1): `weekly_study_goal_minutes` (int, default 0 = unset, sanitized per FR-74 rules) if an explicit goal field does not already exist.
- New prompt kind `"recap"` and its schema live in the existing agent modules (`app/core/agent/*`); the recap payload builder lives beside `compute_review_metrics` in `app/core/stats.py`. *(Location notes only — not an implementation directive.)*

## 8. Testing & Acceptance Criteria (for the eventual implementation)

1. Payload unit tests: window boundaries (midnight-crossing sessions, DST), baseline computation excludes the window, improved/still-weak topic diffing, loose-end selection and ordering.
2. Tone-rule tests (deterministic, headless): given fixture payloads, assert the classifier picks the right pattern row from RECAP-19, including: low-but-within-tolerance → steady voice (RECAP-16); high-volume-no-fatigue → no rest suggestion (RECAP-17); thin-history → no comparisons (RECAP-20).
3. Reconciliation test: a mocked provider returning wrong figures/malicious extra loose ends yields a recap built solely from local payload (RECAP-22).
4. UI tests: button disabled while busy; Resume navigates and scrolls; 30-day offer appears once, Yes regenerates, No dismisses, no re-offer after 30-day recap; cancel stores nothing.
5. Fallback test: provider error path renders the offline recap with all sections and working Resume buttons.

## 9. Open Questions

| ID | Question |
|---|---|
| OQ-1 | Does an explicit study-goal concept exist today? Current settings hold theme/XP-rate/AI config only; home screen shows a daily goal indicator. Where should the weekly goal be set/read — new Settings field, inferred from recent usage, or both (explicit wins)? |
| OQ-2 | Tolerance bands for "less/much more than usual" (e.g. <70% / >150% of baseline) and the fatigue-corroboration signals for RECAP-17 — product decides exact values; propose constants with tests. |
| OQ-3 | Should the 30-day recap replace the 7-day recap visually or append below it? (Proposal: append, matching RECAP-23 repeat-press semantics.) |
| OQ-4 | Are "topics" sourced from notes/documents labels, coach session source packs, or both? Proposal: union, keyed by existing topic/source IDs used by REV-2. |
| OQ-5 | Recurrence: remember "last recap requested" to nudge ("Your last recap: 6 days ago")? Out of scope v1; confirm. |
