This implementation plan is structured to strictly follow the **"Non-AI first"** constraint. It is divided into 7 incremental phases. 

**Phases 1, 2, and 3** build the entire PDF infrastructure, storage schema, and UI shells. At the end of Phase 3, the application will be fully capable of importing, extracting, and browsing a local PDF library and calculating review metrics, with **zero AI dependencies or network code**. 

**Phases 4, 5, and 6** introduce the Agent Core, the Coach UI, and the Live Provider.

---

### Phase 1: Foundation & Schema v2 (Non-AI)
**Goal:** Upgrade the data layer to support Documents and Coach Sessions without breaking v1 saves.
*   **1.1 Constants & Models:** 
    *   Update `app/constants.py`: Bump `SCHEMA_VERSION` to 2, `APP_VERSION` to "1.1.0". Add new constants (e.g., `MAX_SOURCES = 5`, `MAX_PACK_CHARS = 6000`, `AI_TIMEOUT_S = 30`).
    *   Update `app/core/models.py`: Add `Document`, `CoachSession`, `QuestionRecord`, `CoachTurn`, and `AISettings` dataclasses. Extend `Dataset` to include `documents` and `coach_sessions` lists.
*   **1.2 Persistence & Migration:**
    *   Update `app/persistence/validation.py`: Add parsers for the new arrays. Implement skipping for invalid document/turn records (ERR-21 extension).
    *   Update `app/persistence/repository.py`: Implement in-memory v1 → v2 migration on load (inject empty `documents`/`coach_sessions` and default `settings.ai`). Update `dataset_to_payload` for schema v2 serialization. Ensure v1 builds still refuse to overwrite v2 files (`FutureSchemaError`).
    *   Handle manual load of foreign v2 saves where library binaries are absent: load document records with `status='missing'` and surface a friendly count notice to the user (ERR-40 / DATA-11); re-linkable via the `library.py` re-link logic (LIB-6).
    *   Implement a 50 MB save-size check in the persistence layer: after a successful save whose result exceeds 50 MB, trigger a one-time friendly heads-up notice ("Your library text is getting large") and continue normally (NFR-17).
*   **1.3 Library Storage Infrastructure:**
    *   Create `app/persistence/library.py`: Implement binary copy, delete, and re-link logic for the `<app-data>/library/` folder.
*   **1.4 AppState Extensions:**
    *   Update `app/state/app_state.py`: Add `_doc_by_id` and `_coach_by_id` indexes. Add mutation methods (`import_document`, `delete_document`, `update_document_status`) wired to the existing autosave/listener broadcast.
*   **Phase 1 Exit Gate:** Unit tests pass for schema v2 migration, v1→v2 upgrade, and binary library copy/delete operations.

### Phase 2: PDF Pipeline & Library UI (Non-AI)
**Goal:** Users can import PDFs, extract text in the background, and view pages in-app.
*   **2.1 Dependencies & Build Verification:** 
    *   Add `PyMuPDF` (fitz) to `requirements.txt`.
    *   Verify that `run_build.ps1` correctly installs this new dependency in the virtual environment *before* it attempts to run the test suite.
*   **2.2 Thread Safety Strategy & Extraction Engine:**
    *   **Thread Safety Pattern:** Define a standard `UIEventQueue` (or similar dispatcher wrapping `queue.Queue` and polled via Tkinter's `after()` method) to standardize how background workers communicate with the CustomTkinter main loop. This prevents cross-thread UI crashes.
    *   Create `app/core/pdfx.py`: Implement text extraction per page and page-to-pixmap rendering.
    *   Implement a background worker queue (A-30) utilizing the new thread-safe dispatcher so extraction and rendering never block the Tkinter main thread.
*   **2.3 Library Screen UI:**
    *   Create `app/gui/screens/library_screen.py`: Split pane with Document List (left) and Page Viewer (right).
    *   Create `app/gui/widgets/page_viewer.py`: Pixmap display with zoom (1.0/1.5/2.0), prev/next, text-toggle, and a 5-page LRU cache.
*   **2.4 Import & Edge Cases:**
    *   Wire the "Import PDF" button: File dialog → `library.py` copy → `pdfx.py` background extract → `AppState` mutation.
    *   Implement edge cases: Refuse >100MB/2000pg (ERR-30), handle encrypted files (ERR-29), mark zero-text PDFs as `textless` (ERR-31), and handle missing binaries on startup as `missing` (ERR-32).
    *   Implement duplicate detection by `(file_name, file_size)`. Refuse the import with an "already in your library" message and offer a button to open the existing record (PDF-3).
*   **2.5 Navigation Registration:**
    *   Update `app/gui/main_window.py`: Register the `Library` nav item in `NAV_ITEMS` at the correct position (Home, Calendar, Notes, Library, ...) so the sidebar order matches the seven-destination spec (UI-1/UI-19) from this phase onward, rather than waiting for Phase 5. The `Coach` item stays registered in Phase 5.1 when its screen exists.
*   **Phase 2 Exit Gate:** GUI smoke test can import a generated PDF, wait for background extraction, and navigate pages without UI freezing.

### Phase 3: Source Selection & Review Shell (Non-AI)
**Goal:** Users can select sources for future coaching, and the Stats screen can calculate weak-topic metrics (even if the coach log is currently empty).
*   **3.1 Source Selection Dialog:**
    *   Create `app/gui/dialogs/source_select.py`: Modal with grouped checklists (Notes / Ready Documents), search, and a live character-count estimate.
    *   Enforce the 5-source cap (A-37) with a friendly notice.
*   **3.2 Source Pack Builder:**
    *   Create `app/core/agent/context.py` (Part 1): Implement the pack builder. Convert note bodies to pseudo-page 1, pull leading document pages, truncate at sentence boundaries to hit the 6,000-char limit (SEL-2).
*   **3.3 Review Metrics & Stats UI:**
    *   Update `app/core/stats.py`: Add `compute_review_metrics()` to calculate weak-topic scores based on confidence, revisions, and hints (REV-1).
    *   Update `app/gui/screens/stats_screen.py`: Add the "Review" section (UI-24) and Coach Activity cards. Show friendly empty states when no coach data exists.
*   **Phase 3 Exit Gate:** Users can select up to 5 sources and see a calculated pack size. Stats screen renders the new Review section. *No AI code exists yet.*

---

### Phase 4: Agent Core & Mock Engine (AI)
**Goal:** Build the headless, GUI-free brain of the Study Coach. It must work entirely in Mock mode.
*   **4.1 Schemas & Parsing:**
    *   Create `app/core/agent/schema.py`: Define `CoachReply` and `Quote` dataclasses (GEN-11).
    *   Create `app/core/agent/parser.py`: Implement the JSON repair ladder (strip fences → extract balanced `{}` → validate → mock fallback) (GEN-12).
*   **4.2 Grounding Engine:**
    *   Create `app/core/agent/grounding.py`: Build the lazily-evaluated normalised page index (GRD-1). Implement `validate_quotes()` to strip ungrounded citations and count failures (GRD-2/GRD-3).
*   **4.3 Mock Provider:**
    *   Create `app/core/agent/mock_engine.py`: Deterministic extractive generator. Seed based on session ID. Generate cloze questions from pack sentences, sentence hints, and keyword-overlap feedback (A-27, GEN-2..4).
*   **4.4 State Machine & Prompts:**
    *   Create `app/core/agent/prompt.py`: Centralize system prompts and per-kind templates (GEN-9/10). The system prompt must encode the GEN-9 rules: role (patient study coach), unaided-attempt rule (never answer before the user's first answer + confidence), grounding rule, schema rule, tone (brief, encouraging, non-verbatim answers to cloze), and refusal to discuss non-study topics.
    *   Create `app/core/agent/coach_service.py`: Implement the turn state machine (COACH-1). Enforce unaided-first-answer immutability (COACH-3), single confidence rating (COACH-4), and hint/feedback gating (COACH-5/6). Implement `request_followup` as a child question record (`parent_id` set) targeting the weakest missing idea, running the same state machine inside the same session (COACH-8). All methods return `ActionResult`.
*   **Phase 4 Exit Gate:** 100% headless unit test coverage of the state machine, mock generation determinism, and grounding validation.

### Phase 5: Coach UI & Settings (AI)
**Goal:** Wire the Agent Core to the GUI and expose AI settings.
*   **5.1 Navigation & Settings:**
    *   Update `app/gui/main_window.py`: Add Coach to `NAV_ITEMS` (Library was already registered in Phase 2.5) and move the Mode Badge onto the Coach nav button so the final sidebar is Home, Calendar, Notes, Library, Coach, Statistics, Settings (UI-1/UI-19).
    *   Update `app/gui/screens/settings_screen.py`: Add the "Study Coach (AI)" card (UI-23) with Mock/Live toggle, Base URL, Model, and masked API Key. Update `Settings.sanitized()` for `settings.ai`.
*   **5.2 Coach Screen UI:**
    *   Create `app/gui/screens/coach_screen.py`: Session header, scrollable thread, answer composer, and gated action row (UI-21).
    *   Create widgets: `chat_bubble.py`, `citation_chip.py` (with click-to-navigate), `confidence_picker.py` (UI-22), and `sessions_dialog.py` (UI-26): list sessions (created date, source labels, question count, mode) with Resume and Delete-with-confirm (COACH-14).
    *   Implement accessibility and fallback UI details: role prefixes ("Coach:"/"You:") and textual verified ticks so bubbles/mode states are distinguishable by text as well as colour (ACC-4, preserving ACC-3), tooltips on gated buttons explaining *why* they are disabled (e.g. "Rate confidence to unlock feedback") (ACC-5), and the "General guidance" badge for feedback/hint turns with zero verified quotes while sources exist (GRD-5, surfaced via ERR-28's strip-and-count path).
*   **5.3 Integration:**
    *   Wire the Coach screen to `CoachService`. Ensure every turn mutation triggers an autosave and listener broadcast to refresh Stats/Review.
    *   Enforce session/source deletion semantics in the UI wiring: deleting a coach session requires confirmation; deleting a *source* (note/document) never deletes sessions — affected sessions mark that source missing (COACH-12, ERR-33).
*   **Phase 5 Exit Gate:** A user can run a complete Mock coaching loop (Question → Answer → Confidence → Feedback → Revision) in the GUI, and see the weak-topic score update in Stats.

---

### Phase 6: Live Provider & Cancellation (AI)
**Goal:** Enable opt-in Live mode via OpenAI-compatible endpoints.
*   **6.1 Provider Interface:**
    *   Create `app/core/agent/provider_base.py`: Define `CoachProvider` interface.
*   **6.2 Live Client:**
    *   Create `app/core/agent/provider_openai.py`: Implement `urllib.request` client. Handle Base URL sanitization, Bearer token injection, and strict key redaction in all logs/exceptions (PROV-2..7).
*   **6.3 Error Handling & Cancellation:**
    *   Implement 30s timeout, single retry on 429/5xx, and friendly fallback paths for 401/403 and network errors (PROV-5, ERR-25..28, ERR-34..36).
    *   Implement thread-cancellation for the "Cancel" button in the Coach UI so aborted live turns don't block the composer (COACH-15).
*   **Phase 6 Exit Gate:** Unit tests with a fake HTTP transport verify timeouts, retries, and redaction. GUI test verifies cancellation re-enables the UI within 1 second.

---

### Phase 7: Gates, Packaging & Polish
**Goal:** Ensure the frozen executable works and performance budgets are met.
*   **7.1 Packaging:**
    *   Update `StudyProof.spec`: Add `fitz` (PyMuPDF) to hidden imports. Verify `assets/` bundling still works.
*   **7.2 Performance Gate:**
    *   Update `scripts/perf_check.py`: Add NFR-12 (200-page extraction ≤ 15s), NFR-13 (Mock turn ≤ 200ms), and NFR-14 (Page render ≤ 300ms) budgets.
*   **7.3 Final Testing:**
    *   Update `tests/smoke_gui.py` (QA-16): Add a flow that imports a fixture PDF, selects it, runs a full mock coach loop, and verifies the Review screen updates.
    *   Update `README.md` with v1.1.0 features, PyMuPDF dependency, and AI privacy guarantees.
*   **Phase 7 Exit Gate:** `run_build.ps1` completes successfully, producing a standalone `dist\StudyProof\StudyProof.exe` that passes all gates.

---

### Summary of Effort & Dependencies

| Phase | Focus | Key Dependencies Added | Est. Effort |
| :--- | :--- | :--- | :--- |
| **1** | Schema & Storage | None | Low (1-2 days) |
| **2** | PDF Library | `PyMuPDF` | Medium (3-4 days) |
| **3** | Selection & Stats | None | Low (1-2 days) |
| **4** | Agent Core (Mock) | None (stdlib only) | High (4-5 days) |
| **5** | Coach UI | None | Medium (3-4 days) |
| **6** | Live Provider | `urllib` (stdlib) | Medium (2-3 days) |
| **7** | Packaging & QA | None | Low (1-2 days) |

**Total Estimated Effort:** ~16-22 working days for a single developer.

**Critical Path Note:** Phase 2 (PyMuPDF extraction) and Phase 4 (Agent Core state machine) are the most complex technical challenges. Because they are completely decoupled, they can be worked on in parallel if multiple developers are available, or sequentially as planned above to ensure the data layer is rock-solid before the AI logic is layered on top.

