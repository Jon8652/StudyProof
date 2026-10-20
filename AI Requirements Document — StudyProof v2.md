# AI Requirements Document — StudyProof v2 (Study Coach & PDF Library)

**Document status:** Expansion requirements — plans the Study Coach (AI) subsystem and the PDF Study Library subsystem on top of the consolidated v1 requirements (`new_requirements_document`) and the current repository (`github.com/Jon8652/StudyProof`). It introduces new requirement IDs, re-splits the agreed task list into **AI features** and **non-AI features**, and explicitly **corrects** the small number of v1 clauses that the new features invalidate (§4). Where this document is silent, the consolidated v1 document remains authoritative.
**Target product:** StudyProof with a local PDF library and a Study Coach powered by a user-configured live OpenAI-compatible endpoint.
**Primary technology additions:** PyMuPDF (fitz) for extraction/page rendering; Python stdlib `urllib.request` for live coach calls (no new network dependency); all v1 technologies unchanged.
**Distribution target:** unchanged (PyInstaller one-folder windowed Windows exe; source cross-platform). The frozen build must bundle PyMuPDF and keep extraction/rendering working from `dist\StudyProof\`.
**Assumptions:** All v1 decisions A-1…A-20 remain in force except where corrected in §4; new decisions A-21…A-37 are added below.

**Implementation status (19 October 2026):** The seven implementation phases
are delivered for v1.1.0. `run_build.ps1` passed 122 automated tests, the full
PDF/coaching/Review GUI smoke flow, the performance gates and 32 diagnostic
checks in the actual frozen Windows executable. PyMuPDF code and required
package resources are bundled. Output: `dist/StudyProof/StudyProof.exe`
(distribute the entire `StudyProof` folder). Live-provider behaviour was
verified with fake transports; no real Live endpoint was contacted.
The original requirements below remain the acceptance contract.

---

## 1. Introduction and Scope Changes

The v1 product is a fully offline StudyProof app. v2 adds two subsystems and one cross-cutting rule change:

| Change | Description |
|---|---|
| **[NEW] PDF Study Library** | Import PDFs into a local library, view pages in-app, extract per-page text with PyMuPDF, and store it with page references. The extracted page text is the grounding source for AI quotations. |
| **[NEW] Study Coach (AI)** | Source-grounded questions, hints, feedback and follow-ups through a user-configured live AI endpoint. |
| **[NEW] Coaching log & review** | First answers (immutable), revisions, confidence and hint usage are persisted per coach session; a derived weak-topic review surface surfaces struggling sources. |
| **[UPDATED] Offline rule** | v1's absolute "no network" wording is corrected (§4): local study features remain functional offline; the Study Coach requires its configured AI service; the **only** permitted network use is a user-initiated, user-configured, cancellable live Study Coach call. Telemetry remains forbidden. |
| **[UPDATED] Navigation** | Sidebar grows from five to **seven** destinations (Library and Coach added); corrects UI-1. |
| **[UPDATED] Save schema 1 → 2** | New optional top-level arrays `documents` and `coach_sessions` plus `settings.ai`; v1 saves migrate on load; v1 builds refuse to overwrite v2 files (existing FutureSchemaError protection). |
| **[NEW] Work-package split** | All tasks from the agreed division of responsibilities are re-categorised as AI vs non-AI (§2); five tasks originally listed under the application/data owner are reclassified as AI features because their behaviour is defined by AI interaction semantics. |

The application shall still **not** include: accounts/authentication, cloud sync, social features, telemetry, required network connectivity, multi-user profiles, session editing, and — new for v2 — streaming token output, embeddings/vector-RAG, OCR, and PDF annotation (§19).

---

## 2. Division of Responsibilities and Reclassification

### 2.1 Categorisation criterion
A task is categorised **AI** when its acceptance criteria depend on model-interaction semantics: prompting, structured model output, the coaching turn state machine, grounding of quotations, conversation memory, or provider configuration/erroring. A task is **non-AI** when it exists, behaves identically and is testable with the coach permanently disabled (no provider). Categorisation drives requirement grouping, test gates and provider boundary behavior; implementation ownership (Person A = agent core/provider, Person B = app/data/UI) is recorded per row.

### 2.2 Task categorisation table

| # | Task (as agreed) | Original owner | Category (this document) | Impl. owner | Requirement IDs |
|---|---|---|---|---|---|
| 1 | Design system prompt for Study Coach | A | **AI** | A | GEN-10, PROMPT via COACH-1 |
| 2 | Build basic chatbot logic | A | **AI** | A | COACH-1…COACH-6 |
| 3 | Create conversation history/memory | A | **AI** (persistence half shared) | A + B | COACH-9, DATA-9…DATA-12 |
| 4 | Generate questions from selected notes | A | **AI** | A | GEN-1…GEN-4 |
| 5 | Generate hints and feedback | A | **AI** | A | GEN-5…GEN-8 |
| 6 | Follow-up explanation questions | A | **AI** | A | GEN-9 |
| 7 | Define structured AI response format | A | **AI** | A | GEN-11, GEN-12 |
| 8 | Validate AI quotations against source text | A | **AI** | A | GRD-1…GRD-5 |
| 9 | Integrate DeepSeek/ELM API | A | **AI** | A | PROV-1…PROV-4 |
| 10 | Handle API errors, timeout and missing keys | A | **AI** | A | PROV-5…PROV-8, ERR-25…ERR-28, ERR-34…ERR-36 |
| 11 | PDF upload and local storage | B | Non-AI | B | PDF-1…PDF-3 |
| 12 | PDF viewer | B | Non-AI | B | LIB-2, LIB-3 |
| 13 | PDF text extraction with PyMuPDF | B | Non-AI | B | PDF-4…PDF-6 |
| 14 | Save extracted text with page references | B | Non-AI (**grounding contract co-defined with AI**) | B (contract with A) | PDF-7, GRD-1, GRD-2 |
| 15 | Update existing JSON storage structure | B | Non-AI | B | DATA-7…DATA-12 |
| 16 | Note selection interface | B | Non-AI | B | SEL-1…SEL-3 |
| 17 | **Study Coach question/answer screen** | B | **AI — RECLASSIFIED** | A (behaviour) + B (widgets) | COACH-1…COACH-15, UI-21 |
| 18 | **Confidence rating controls** | B | **AI — RECLASSIFIED** | A (protocol moment) + B (widget) | COACH-4, COACH-10…COACH-12, UI-22 |
| 19 | **Save first answers, revisions and history** | B | **AI — RECLASSIFIED** (rules/schema; persistence mechanics stay non-AI infra) | A (rules) + B (persistence) | COACH-3, COACH-7, COACH-13, DATA-9 |
| 20 | Review weak topics based on confidence | B | Non-AI (derived analytics) | B | REV-1…REV-3 |
| 21 | **AI settings screen (base URL + key)** | B | **AI — RECLASSIFIED** | A (semantics/key hygiene) + B (host card) | AIS-1…AIS-5, UI-23 |
| 22 | **Connect UI to Person A's agent** | B | **AI — RECLASSIFIED** (integration contract) | A + B | INT-1…INT-6 |

### 2.3 Reclassification rationale
- **Rows 17–19, 21, 22 move to the AI category.** The Q&A screen's flow (feedback withheld until an unaided answer and confidence exist; hints gated; revisions append-only; follow-ups spawned from question state) is the coaching protocol rendered — it cannot be specified or tested without the AI turn rules. Confidence is only meaningful at its protocol moment (post-answer, pre-feedback) and is an input to adaptive feedback. The first-answer/revision log exists to preserve unaided attempts and to serve as conversation memory — both AI-integrity concerns. The AI settings card exists solely to configure the provider (mode resolution, key hygiene, connection test). The UI↔agent connection *is* the agent boundary contract (ActionResult actions, provider boundary behavior, cancellation).
- **Rows 11–16, 20 stay non-AI.** They behave identically with the coach deleted: PDF pipeline, library UI, storage schema mechanics, source selection list, and weak-topic derivation (pure arithmetic over the coaching log).
- **Two shared contracts** are co-owned: the **grounding span index** (row 14 storage is non-AI; its normalisation/matching contract is AI, §8.3) and **coaching-log persistence** (row 19 rules are AI; rows 3/15 persistence mechanics are non-AI repository work, §9.5).

---

## 3. Glossary (new terms)

| Term | Definition |
|---|---|
| Study Coach | The v2 subsystem that runs coached question/answer loops over user-selected sources. |
| Coach session | A persisted conversation container: selected sources, generated questions, attempts and turns. |
| Turn | One persisted conversation entry (coach or user) with kind, text, optional quotes, mode and timestamp. |
| Question record | The assessment unit: prompt, source spans, first answer, confidence, revisions, hint count, feedback flag, follow-up links, status. |
| Unaided first answer | The user's first submitted answer for a question, captured before any hint or feedback; immutable thereafter. |
| Confidence rating | One of `low`/`medium`/`high` ("Guessed"/"Unsure"/"Confident"), captured once, after the first answer and before any hint/feedback. |
| Revision | An append-only improved answer submitted after feedback; never overwrites the first answer. |
| Hint | A coach turn that nudges toward the answer without giving it; gated behind answer + confidence. |
| Feedback | A coach turn evaluating the answer (matched/missing ideas) with source-grounded quotations where possible. |
| Follow-up question | A child question generated from the weakest missing idea of a answered question. |
| Source pack | The bounded context built from the selection: ordered spans with `(source_id, page, text)`, ≤ 5 sources and ≤ 6,000 chars. |
| Span | A contiguous excerpt of a source with provenance (note body pseudo-page 1, or PDF page number). |
| Quotation / citation | A `{source_id, page, quote}` triple attached to a coach turn, claimed verbatim from the source. |
| Grounding validation | Checking each quotation against the normalised page text of its cited source/page; failures are stripped and counted. |
| Live mode | Coach mode calling a user-configured OpenAI-compatible endpoint; requires key + valid base URL. |
| Effective mode | Live generation through the configured endpoint. |
| Agent core | GUI-free prompts, parsing, grounding, memory, live provider and coaching state machine. |
| CoachService | The GUI-facing, GUI-free façade of the agent core; every method returns `ActionResult`. |
| Document | A persisted PDF library record (metadata + per-page extracted text) plus its binary copy in the app-data library folder. |
| Textless document | An imported PDF from which no selectable text could be extracted; viewable but not selectable as a coach source. |
| Normalised page index | Lazily built, non-persisted map of `(source_id, page) → whitespace-collapsed text` used for grounding validation. |
| Weak-topic score | Derived per-source score from confidence/revisions/hints; drives the Review surface. |
| Mode badge | Sidebar/screen chip identifying the Live coach. |
| General guidance label | Badge applied to feedback/hints whose quotations all failed grounding (or none were provided despite sources). |

---

## 4. Corrections to the Consolidated v1 Document

Only the clauses below change; every other v1 requirement stands.

| v1 ID / location | v1 wording (summary) | v2 correction | Reason |
|---|---|---|---|
| §1 & §4 "shall not depend on any external server, network service…" / "shall not include … network connectivity requirements" | Absolute prohibition | Core app remains dependency-free; the **only** permitted network use is a live Study Coach turn explicitly triggered by the user in Live mode, to a user-configured endpoint. "No **required** network connectivity" replaces "no network". | Live AI is opt-in; offline functionality is complete without it (A-21). |
| NFR-2 | The application shall support offline local study features; Study Coach requires a configured live AI endpoint. |
| NFR-5 | "No external server dependencies." | "No **required** external server dependencies; an optional user-provided AI endpoint is permitted and never assumed." | Same. |
| UI-15 | Live coach controls shall show configuration or connection errors with friendly guidance. |
| NFR-3 | No telemetry. | Unchanged and **reinforced**: the live turn payload contains only system prompt + source pack + conversation turns; the key travels only in the `Authorization` header; nothing else leaves the machine (NFR-15). | Privacy. |
| UI-1 | Five navigation destinations. | **Seven**: Home, Calendar, Notes, **Library**, **Coach**, Statistics, Settings. | New screens. |
| §5.1 sidebar description | Five nav buttons + stopwatch + version. | Seven nav buttons (icon + label), stopwatch and `v1.1.0` version label unchanged in position. | Consistency. |
| FR-76 / FR-77 | Ten PNG assets. | **Twelve**: add `library_icon`, `coach_icon` (same fallback rules: missing icon → text label). | New destinations. |
| FR-70 / DATA-1 | `schema_version` written as 1. | `schema_version` written as **2**; v2 reads v1 (migration adds empty `documents`, `coach_sessions`, `settings.ai`); a v1 build opening a v2 file raises `FutureSchemaError` and never overwrites it (existing FR-72 behaviour now protects the new collections). | Forward safety. |
| FR-57 | Autosave fires on session/note/theme/load mutations. | Autosave additionally fires on every coach turn, question mutation, document import/re-extract/delete, and AI-settings change. | New first-class data. |
| UI-12 | Settings contains Appearance / Data management / Data status cards. | Settings gains a fourth card: **Study Coach (AI)** (UI-23). | AI configuration. |
| FR-45 / §6.4 Stats | Activity section ends at total notes. | Stats gains a **Review** section (weak topics) and coach activity cards (REV-1…REV-3, UI-24). | Review surface. |
| NFR-6 / NFR-7 | Performance budgets. | Unchanged; **extended** by NFR-12…NFR-14 (extraction, coach latency, page render) and wired into the same perf gate. | New subsystems. |
| A-5 / A-19 | App-data directory contents. | App-data dir additionally contains `library/` (PDF binary copies) beside the save/backup/quarantine files. | Library storage. |
| ARCH-2 | Core usable without GUI. | Extended by ARCH-5 (agent core GUI-free and provider-agnostic) and ARCH-6 (network code confined to one module). | Testability/offline rule. |
| APP_VERSION | `1.0.0`. | `1.1.0` for the v2 release train. | Versioning. |

---

## 5. Confirmed Assumptions and Binding Decisions (new)

| ID | Decision | Consequence / Rule |
|---|---|---|
| A-21 | The Study Coach uses the live provider. A valid base URL and non-empty API key are required. |
| A-22 | Live providers speak the **OpenAI-compatible chat-completions contract**: `POST {base_url}/chat/completions` with `{model, messages, temperature, max_tokens}`; covers DeepSeek and ELM-compatible gateways. | Config: `base_url`, `model` (default `deepseek-chat`), `api_key`; base URL whitespace-stripped, trailing slash removed, scheme must be http(s). |
| A-23 | Parse a single JSON object: strip fences, extract a balanced object, validate fields, then make one repair retry. On failure, show a friendly error and store no turn. |
| A-24 | **Grounding rule:** every quotation must be a verbatim span (whitespace-normalised, case-insensitive match, original casing preserved for display) of the cited page of the cited selected source. Invalid quotations are stripped and counted (`ungrounded_count`); a feedback/hint turn left with zero verified quotations while sources exist is badged "General guidance". | No fabricated page numbers in either mode (GEN-8). |
| A-25 | **Unaided-attempt preservation:** the first answer is immutable once submitted; confidence is captured exactly once, after the first answer and before any hint/feedback is revealed; hints/feedback are gated on both existing; revisions append only. | The coaching log is an integrity-protected record, not an editable draft. |
| A-26 | Assemble the system prompt, selected source pack, last ten turns and current question state for each live request. |
| A-28 | **Key hygiene:** the API key is stored only inside the local save file's `settings.ai`; masked in UI with a show toggle; never written to logs, exceptions, exports or prompts; sent only as `Authorization: Bearer` to the configured base URL; clearable at any time. | Testable (NFR-16). |
| A-29 | Importing a PDF **copies** it to `<app-data>/library/<uuid>.pdf`; the record never depends on the original path; delete removes copy + record + extracted text after confirmation. | Library survives source-file moves/deletions. |
| A-30 | Extraction and page rendering run **off the UI thread** (worker + queue) with progress and cancel; rendered page images are cached LRU (5 pages). | NFR-12/NFR-14 budgets; UI never blocks. |
| A-31 | Schema v2 migration is in-memory at load and persisted on next save; a v1 build encountering `schema_version: 2` refuses to overwrite (FR-72). | No silent startup rewrites; old builds cannot truncate new collections. |
| A-32 | Coach sessions and documents are **first-class persisted data**: autosave, atomic write, backup, quarantine recovery, manual load and partial-record skipping (ERR-21 style) apply exactly as for sessions/notes. | One persistence story. |
| A-33 | Weak topics and coach activity metrics are **derived only**, never stored. | Mirrors v1 derived-data policy. |
| A-34 | Live turns use timeout `AI_TIMEOUT_S = 30`, are user-cancellable, and retry **once** with 2 s backoff on HTTP 429/5xx only; timeouts/network errors go straight to the friendly fallback path. | Bounded waiting; simple retry policy. |
| A-35 | The GUI-free agent core uses an injectable CoachProvider interface implemented by OpenAICompatProvider. |
| A-36 | Confidence scale is fixed at three levels: `low`="Guessed", `medium`="Unsure", `high`="Confident"; stored as key, displayed as label. | No v2 UI editor for the scale. |
| A-37 | Source selection caps at five sources and shows the source-pack size. |

---

## 6. Product Overview Additions

- **Library:** import PDFs (copied locally), browse a document list with status chips, view pages in-app (rendered images with zoom + text pane + open-externally), extract per-page text in the background with progress/cancel, re-extract, delete with confirmation, re-link missing files.
- **Study Coach:** pick up to five sources (Notes and/or ready Documents) → generate a question → submit an **unaided first answer** → rate confidence → receive **source-grounded feedback** (quotations as page chips) → optionally request hints (counted) and submit **revisions** → spawn **follow-up explanation questions** → everything persisted per session; sessions list/resume/delete.
- **Live coach:** all actions validate configuration and use the same state machine, grounding and schema. Failed turns show an error and store nothing.
- **Review:** Stats gains weak-topic rows (per source: weak points, questions, average confidence) and coach activity cards, all derived from the coaching log.
- **AI settings:** mode toggle, base URL, model, masked key, connection test, privacy note — persisted in `settings.ai`.
- Still excluded: accounts, cloud sync, social, telemetry, required networking, multi-user, session editing, streaming output, embeddings/RAG, OCR, PDF annotation.

---

## 7. User Interface Structure Additions

### 7.1 Overall layout **[UPDATED]**
Sidebar holds Home, Calendar, Notes, Library, Coach, Statistics and Settings. The Coach badge shows Live. Screens remain lazily constructed.

### 7.2 Screens (additions/changes)

| Screen | Purpose | Main actions |
|---|---|---|
| Library **[NEW]** | Document list pane + page viewer pane | Import PDF, open/select, prev/next page, zoom, text toggle, open externally, re-extract, delete (confirm), re-link missing |
| Coach **[NEW]** | Coached Q&A: session header (source chips, mode badge, sessions menu, new session), scrollable conversation thread, answer composer, action row | Select sources, generate question, submit answer, rate confidence, request hint, show feedback, submit revision, ask follow-up, cancel live turn |
| Statistics **[UPDATED]** | Adds Review section + coach activity cards | Read-only review; click weak row → open latest session for that source |
| Settings **[UPDATED]** | Adds "Study Coach (AI)" card | Mode toggle, base URL, model, key (mask/show/clear), test connection |

### 7.3 UI Requirements (new)

| ID | Requirement | Notes |
|---|---|---|
| UI-19 | The sidebar shall present seven destinations with icon-when-available plus always-visible labels; the Coach item shall show the effective-mode badge as text. | Corrects UI-1; badge never colour-only. |
| UI-20 | The Library screen shall show a document list (file name, page count, imported date, status chip Ready/Textless/Extracting/Missing) and a viewer pane with page image, "Page i of n", prev/next, zoom (1.0/1.5/2.0), text-pane toggle and open-externally button. | Render on demand + LRU cache (A-30). |
| UI-21 | The Coach screen shall show: session header (source chips with page counts, mode badge, "Sessions…", "New session"), a scrollable thread of role-distinct bubbles (coach/user) with quotation chips, a multiline answer composer with Submit, and an action row (Rate confidence, Request hint, Submit revision, Ask follow-up, Cancel while a live turn is in flight). | Buttons enable/disable strictly per the turn state machine (COACH-1); disabled buttons carry tooltips explaining the gate ("Answer first, then rate confidence"). |
| UI-22 | Confidence shall be captured by three inline labelled buttons (Guessed / Unsure / Confident) shown immediately after a first answer is submitted and before any hint/feedback; once rated, the picker becomes read-only text. | A-25, A-36. |
| UI-23 | Settings shall contain a Study Coach card with base URL, model selector, masked key, show/clear controls, connection testing and a local-key privacy note. |
| UI-24 | Statistics shall show a Review section listing sources with weak-topic score ≥ 2 (label, weak points, questions, average confidence) and coach activity cards (questions asked, answered, confidence distribution, hints used, revisions); friendly empty state when no coach sessions exist. | REV-1…REV-3. |
| UI-25 | Source selection shall use a modal dialog with grouped checklists (Notes / Documents-ready), search box, per-item size hint, live pack-size estimate, and a 5-source cap with friendly cap notice. | SEL-1…SEL-3, A-37. |
| UI-26 | A sessions dialog shall list coach sessions (created date, source labels, question count, mode) with Resume and Delete (confirm). | COACH-14. |
| UI-27 | Quotation chips shall render as "p. N" (documents) or "note" (notes) with a verified tick; clicking opens the Library viewer at that page (or the Notes detail for note sources); ungrounded notices use muted danger text ("2 quotations could not be verified and were removed."). | GRD-4, GRD-3. |
| UI-28 | New surfaces shall use central theme tokens, a Live coach badge, and friendly empty states. |

### 7.4 Dialogs and widgets (new)

| Component | Behaviour |
|---|---|
| SourceSelectDialog | Modal; grouped checklists + search + pack estimate + cap enforcement; returns a selection snapshot. |
| SessionsDialog | Modal list of coach sessions; Resume / Delete (confirm). |
| ChatBubble | Role-styled message block; renders quotation chips and the general-guidance badge; wraps long text safely. |
| CitationChip | "p. N ✓" button; tooltip shows the quote; click navigates to source page. |
| ConfidencePicker | Three labelled buttons → read-only label after rating. |
| PageViewer | PyMuPDF pixmap → CTkImage pane with zoom/prev/next, text toggle, render-failure fallback to text-only with friendly notice. |
| DocumentRow | List row with name, pages, date, status chip. |

---

## 8. Functional Requirements — AI Features

### 8.1 Coaching loop and conversation engine (COACH-)

| ID | Requirement |
|---|---|
| COACH-1 | The agent core shall maintain a per-question turn state machine: `open → answered → confidence_set → feedback_given → (revised | followup)* → closed`. Actions outside the current state are refused with a friendly message and store nothing (ERR-38). |
| COACH-2 | request_question builds the selected source pack and generates one live question with its source spans. |
| COACH-3 | `submit_answer` on an `open` question stores the **first answer immutably** with timestamp, transitions to `answered`, and unlocks confidence capture. Later attempts to change it are refused (A-25). |
| COACH-4 | `rate_confidence` is accepted exactly once per question, only in `answered` state (i.e. before any hint/feedback), storing key + timestamp (A-25, A-36). |
| COACH-5 | request_hint requires an unaided answer, increments hints_seen on success, and validates quoted sources. |
| COACH-6 | `request_feedback` is accepted only in `confidence_set` or later; feedback evaluates matched/missing ideas against the answer and carries validated quotations; sets `feedback_given`. |
| COACH-7 | `submit_revision` is accepted only after feedback; appends `{text, at}` to `revisions`; the first answer remains untouched. |
| COACH-8 | `request_followup` creates a child question record (`parent_id` set) targeting the weakest missing idea; the child runs the same state machine inside the same session. |
| COACH-9 | Conversation memory: all turns persist per session; the context assembler (A-26) feeds generation; memory trimming drops oldest turns beyond 10 with no summarisation. |
| COACH-10 | All coaching actions share context assembly, response schema and grounding. New turns record live mode. |
| COACH-11 | Every coach operation returns `ActionResult`; the GUI stays thin; mutations autosave and broadcast through the existing listener mechanism (coach screen, Stats review refresh; unvisited screens skipped per A-20). |
| COACH-12 | Deleting a coach session requires confirmation; deleting a *source* (note/document) never deletes sessions — affected sessions mark that source missing (ERR-33). |
| COACH-13 | The coaching log schema is §14.4; every turn/question mutation autosaves immediately (FR-57 extension). |
| COACH-14 | Multiple sessions may exist; one is active in the UI; the sessions dialog (UI-26) resumes/deletes. |
| COACH-15 | Cancelling an in-flight live turn stops the waiting worker, re-enables the composer within 1 s, and stores no partial turn. |

### 8.2 Generation (GEN-)

| ID | Requirement |
|---|---|
| GEN-1 | Live question prompts shall include the source pack spans with page references and instruct a schema JSON of kind `question` whose own spans are copied from the pack (never invented). |
| GEN-5 | Live hint/feedback/follow-up prompts shall include the question record (prompt, spans), first answer, confidence, revisions so far, hints seen, and the source pack, and shall require verbatim quotations with page numbers drawn only from the pack. |
| GEN-6 | Feedback (both modes) shall reference the user's specific answer (matched/missing ideas), never generic praise alone. |
| GEN-7 | Follow-ups shall target the weakest missing idea through the live prompt rules. |
| GEN-8 | Neither mode may emit page numbers or spans absent from the source pack (enforced by GRD validation post-generation). |
| GEN-9 | The system prompt shall state: role (patient study coach), unaided-attempt rule (never answer before the user's first answer and confidence), grounding rule, schema rule, tone (brief, encouraging, non-verbatim answers to cloze), and refusal to discuss non-study topics. |
| GEN-10 | All model-visible prompts shall be assembled in exactly one module (`app/core/agent/prompt.py`) and unit-testable headlessly. |
| GEN-11 | The structured response schema shall be `{kind: question|hint|feedback|followup, text: str, quotes: [{source_id, page, quote}], meta: {}}` with length caps (text ≤ 2,000 chars; quotes ≤ 5). |
| GEN-12 | Parsing strips fences, extracts a balanced object, validates fields and makes one repair retry. Failure returns an error without storing a turn. |

### 8.3 Grounding (GRD-)

| ID | Requirement |
|---|---|
| GRD-1 | A normalised page index shall be built lazily per selected source: whitespace collapsed to single spaces; note bodies exposed as pseudo-page 1; index not persisted. |
| GRD-2 | A quotation validates iff its source is in the session selection, the page exists, and the normalised quote appears in that page's normalised text (case-insensitive; display preserves original casing sliced from the page). |
| GRD-3 | Invalid quotations are stripped from the stored turn and counted in `ungrounded_count`; when > 0 the thread shows the muted notice from UI-27. |
| GRD-4 | Verified quotations render as chips (UI-27) and navigate to the source page/note on click. |
| GRD-5 | A feedback/hint turn with zero verified quotations while sources exist keeps its text but is badged "General guidance". |

### 8.4 Provider and errors (PROV-)

| ID | Requirement |
|---|---|
| PROV-1 | An injectable CoachProvider interface is implemented by OpenAICompatProvider. |
| PROV-2 | `OpenAICompatProvider` posts `{model, messages, temperature: 0.2, max_tokens: 700}` to `{base_url}/chat/completions` with `Authorization: Bearer <key>`, using stdlib `urllib.request` in a worker thread with `AI_TIMEOUT_S`. |
| PROV-3 | With a missing key, no call is made. Return friendly Settings guidance and store no turn. |
| PROV-4 | Base URL sanitisation per A-22; invalid URL → friendly validation error, no call. |
| PROV-5 | 401/403 show key guidance; 429/5xx retry once after two seconds; timeout/TLS/DNS/proxy errors show friendly guidance. Failed turns store nothing and may be retried. |
| PROV-6 | Non-JSON or schema-invalid bodies enter the GEN-12 ladder. |
| PROV-7 | Exceptions and logs shall redact the key (`<redacted>`); the key never appears in messages shown to the user beyond the masked settings field. |
| PROV-8 | "Test connection" sends a minimal completion with a 10 s timeout and reports friendly success/failure; results are never persisted. |

### 8.5 AI settings (AIS-)

| ID | Requirement |
|---|---|
| AIS-1 | The Settings AI card (UI-23) edits `settings.ai` = `{mode, base_url, model, api_key}`; changes autosave (FR-57 extension). |
| AIS-2 | AI setting changes take effect on the next turn; new turns record live mode. |
| AIS-3 | Every coach request uses live generation and requires a non-empty key and valid base URL. |
| AIS-4 | Key entry is masked by default with show toggle and clear button (A-28). |
| AIS-5 | Settings.sanitized normalizes mode to live, validates the supported model, strips URL/key strings and supplies defaults for older saves. |

---

## 9. Functional Requirements — Non-AI Features

### 9.1 PDF pipeline (PDF-)

| ID | Requirement |
|---|---|
| PDF-1 | Import via file chooser (`*.pdf`); the file is copied to `<app-data>/library/<uuid>.pdf` (A-29); a record is created only after a successful copy + open. |
| PDF-2 | Limits: ≤ 100 MB and ≤ 2,000 pages; over-limit files are refused with a friendly message (ERR-30). |
| PDF-3 | Duplicate detection by (file name, size): refuse with "already in your library" and offer to open the existing record. |
| PDF-4 | Extraction via PyMuPDF per page in a worker thread with "Extracting page i of n" progress and cancel; result stored as `text_pages` (1-based pages, verbatim UTF-8 text) plus `page_count`, `char_count`, `status`. |
| PDF-5 | Encrypted/unopenable PDFs: friendly ERR-29 message; the copied binary and any partial record are removed. |
| PDF-6 | Zero extracted text across all pages → `status = "textless"`: viewer fully usable, excluded from source selection with an explanatory tooltip/notice (ERR-31). |
| PDF-7 | Page references are 1-based integers; extracted text is stored verbatim; the normalised index for grounding is derived lazily (GRD-1) and never persisted. |

### 9.2 Library screen (LIB-)

| ID | Requirement |
|---|---|
| LIB-1 | List pane per UI-20 with status chips Ready / Textless / Extracting / Missing. |
| LIB-2 | Viewer renders the selected page via PyMuPDF pixmap → CTkImage at zoom 1.0/1.5/2.0 with prev/next and "Page i of n"; text toggle shows the stored page text; open-externally launches the OS default app on the stored copy. |
| LIB-3 | Rendering is on-demand with a 5-page LRU cache (A-30); a render failure falls back to the text pane with a friendly notice (never crashes). |
| LIB-4 | Re-extract replaces `text_pages` (confirm not required); existing quotations re-validate lazily on next display (GRD). |
| LIB-5 | Delete requires confirmation and removes record + binary + selection eligibility; coach sessions keep their history with the source marked missing (ERR-33). |
| LIB-6 | A record whose binary is absent at startup shows `status = "missing"`: viewer disabled, stored text still usable for grounding; the row offers "Re-link…" (choose a file, verified by page count) or remove record. |

### 9.3 Source selection (SEL-)

| ID | Requirement |
|---|---|
| SEL-1 | The selection dialog (UI-25) lists Notes (dated and undated, non-empty) and Documents with `status = "ready"`, with search and checkboxes; cap 5 sources (A-37) with friendly cap notice. |
| SEL-2 | The source pack builder converts notes to pseudo-page 1 bodies and documents to leading pages until the 6,000-char budget, truncating at sentence boundaries; every span carries `(source_id, page, text)`. |
| SEL-3 | The session stores a selection snapshot (`kind`, `id`, `label`) so history stays readable after source deletion. |

### 9.4 Review and weak topics (REV-)

| ID | Requirement |
|---|---|
| REV-1 | Weak points per question: confidence `low` +2, `medium` +1, revisions ≥ 1 +1, hints ≥ 2 +1; per-source score = sum over its questions; sources with score ≥ 2 list in the Review section, sorted descending. |
| REV-2 | Review rows show source label, weak points, question count and average confidence; clicking opens the most recent coach session containing that source. |
| REV-3 | Coach activity cards: questions asked, answered, confidence distribution, hints used, revisions — all derived (A-33), refreshed via listeners. |

### 9.5 Storage and schema mechanics (DATA-)

| ID | Requirement |
|---|---|
| DATA-7 | `schema_version` becomes 2; loading a v1 payload migrates in memory (adds `documents: []`, `coach_sessions: []`, `settings.ai` defaults); persistence of the migrated shape happens on the next save (A-31). |
| DATA-8 | `documents` and `coach_sessions` are optional on read (missing → empty list); a non-list value marks the file corrupt, mirroring the v1 `notes` rule. |
| DATA-9 | Individual document/turn/question records that fail validation are skipped with the existing partial-load warning; duplicate ids are skipped (ERR-21 extension). |
| DATA-10 | PDF binaries live outside the save JSON under `library/`; backup/quarantine/atomic-write cover the JSON only; the library folder is recreated on demand; missing binaries surface as `status = "missing"` (LIB-6), never as corruption. |
| DATA-11 | Manual load replaces documents and coach sessions with the loaded dataset (whole-dataset semantics, A-9); binaries referenced by a foreign save are absent → records load with `status = "missing"` plus a friendly count notice (ERR-40), re-linkable per LIB-6. |
| DATA-12 | `settings.ai` participates in `Settings.sanitized()` (AIS-5); v1 settings load gains ai defaults. |

---

## 10. Integration Contract and Architecture (INT-, ARCH-)

| ID | Requirement |
|---|---|
| INT-1 | The agent core exposes `CoachService` (GUI-free): `start_session(selection)`, `request_question`, `submit_answer`, `rate_confidence`, `request_hint`, `request_feedback`, `submit_revision`, `request_followup`, `cancel_turn`, `delete_session`; all return `ActionResult`. |
| INT-2 | `AppState` owns the `documents`/`coach_sessions` collections and new indexes (`_doc_by_id`, `_coach_by_id`); mutations autosave + broadcast exactly like v1 mutations; weak-topic derivation lives in `app/core/stats.py` (or sibling) as a pure function. |
| INT-3 | GUI modules shall never build prompts, parse model JSON, or validate quotations; they call CoachService and render results (ARCH-1 extension). |
| INT-4 | Every turn uses the live provider after configuration validation (AIS-3); tests inject fake providers/transports (NFR-10 extension). |
| INT-5 | Generated quotations pass through grounding.validate_quotes before storage. |
| INT-6 | New screens register with the existing listener broadcast and lazy-screen cache (A-20); refresh exceptions remain contained per screen (ERR-23). |
| ARCH-5 **[NEW]** | The agent core (`app/core/agent/`) has no GUI and no persistence imports; it receives repositories/state via injection. |
| ARCH-6 **[NEW]** | All socket-level code is confined to `app/core/agent/provider_openai.py` (stdlib urllib); no other module may open network connections; static test asserts this. |
| ARCH-7 **[NEW]** | PDF binary handling is confined to `app/persistence/library.py` (copy/delete/re-link) and `app/core/pdfx.py` (extract/render); GUI receives only images/text objects. |

---

## 11. Accessibility and Usability Additions

| ID | Requirement |
|---|---|
| ACC-4 **[NEW]** | Coach bubbles and mode states shall be distinguishable by text as well as colour (role prefixes "Coach:"/"You:", textual mode badge, textual verified tick), preserving v1's minimal accessibility stance (ACC-3) without regressing it. |
| ACC-5 **[NEW]** | Gated coach buttons shall explain their gate via tooltip/text (e.g. "Rate confidence to unlock feedback"), keeping the friendly, non-technical error style. |

---

## 12. Non-Functional Requirements Additions

| ID | Requirement |
|---|---|
| NFR-12 | Extracting a 200-page PDF shall complete ≤ 15 s wall on a typical modern PC with **zero** UI-thread blocking (worker + queue); progress updates at most ~10 Hz. |
| NFR-13 | Source-pack build should complete within 100 ms for five documents; live turns are bounded by AI_TIMEOUT_S; cancel re-enables the UI within one second. |
| NFR-14 | Page render at zoom 1.0 shall take ≤ 300 ms per page with the LRU cache making repeat navigation instantaneous. |
| NFR-15 | Live payload minimality: system prompt + source pack + ≤ 10 turns + question state only; no settings, paths, telemetry or metadata; key only in the Authorization header (NFR-3 reinforced). |
| NFR-16 | Key hygiene is testable: with a fake key configured, no test-visible log/exception/message string contains it (A-28). |
| NFR-17 | Storage caps per PDF-2; if a successful save exceeds 50 MB the app shows a one-time friendly heads-up ("Your library text is getting large") and continues normally. |
| NFR-18 | The frozen one-folder build shall include PyMuPDF and keep import/extract/render working from `dist\StudyProof\` (spec hidden imports updated; perf/smoke gates run against the frozen layout where practical). |

---

## 13. Error Handling and Edge Cases Additions

| ID | Scenario | Required Behavior |
|---|---|---|
| ERR-25 | Missing key: ask the user to add an AI key in Settings; make no request. |
| ERR-26 | Timeout: show a friendly error, allow retry and store no failed turn. |
| ERR-27 | Unreadable reply: attempt one repair retry, then show a friendly error without storing a turn. |
| ERR-28 | Quotations fail grounding. | Strip + count + muted notice; zero verified with sources present → "General guidance" badge (GRD-3/GRD-5). |
| ERR-29 | Encrypted or corrupt PDF at import. | Friendly "This PDF couldn't be opened (it may be protected or damaged)."; copy and partial record removed. |
| ERR-30 | PDF over 100 MB or 2,000 pages. | Friendly limit message; no import. |
| ERR-31 | PDF yields no selectable text. | Import succeeds as `textless`; viewer works; selection lists it as unavailable with explanation. |
| ERR-32 | Library binary missing on disk. | `status = "missing"`; viewer disabled; text usable; Re-link or remove offered. |
| ERR-33 | Coach session source deleted (note or document). | Session persists; source chip shows "removed"; its spans drop from future packs; existing quotations display "source removed" and fail validation gracefully. |
| ERR-34 | DNS/proxy/TLS failure: show connection guidance, allow retry and store no failed turn. |
| ERR-35 | HTTP 401/403. | "The AI service rejected this key. Check it in Settings." |
| ERR-36 | 429/5xx: retry once after two seconds, then show service-unavailable guidance. |
| ERR-37 | Question requested with empty selection. | Friendly "Select at least one note or document first."; selection dialog opens. |
| ERR-38 | Out-of-order coach action (e.g. feedback before confidence). | Friendly gate message explaining the next step; nothing stored (COACH-1). |
| ERR-39 | Coach worker thread raises unexpectedly. | Exception contained; friendly message; session and dataset intact (mirrors ERR-23). |
| ERR-40 | Manual load of a v2 save whose library binaries are absent. | Records load with `status = "missing"` + friendly count notice; re-link available (DATA-11). |

---

## 14. Save File Data Schema — Version 2

Top-level structure (v1 arrays unchanged):

```json
{
  "schema_version": 2,
  "created_at": "YYYY-MM-DDTHH:MM:SS",
  "updated_at": "YYYY-MM-DDTHH:MM:SS",
  "settings": {
    "theme": "dark",
    "xp_per_10_minutes": 10,
    "ai": { "mode": "live", "base_url": "", "model": "deepseek-chat", "api_key": "" }
  },
  "sessions": [ /* unchanged from v1 */ ],
  "notes":    [ /* unchanged from v1 */ ],
  "documents": [
    {
      "id": "<uuid>",
      "file_name": "biology-ch4.pdf",
      "stored_name": "<uuid>.pdf",
      "original_path": "C:\\…\\biology-ch4.pdf",
      "imported_at_local": "<ISO local datetime>",
      "page_count": 24,
      "char_count": 81234,
      "status": "ready | textless | missing",
      "text_pages": [ { "page": 1, "text": "…" } ]
    }
  ],
  "coach_sessions": [
    {
      "id": "<uuid>",
      "created_at_local": "<ISO>",
      "updated_at_local": "<ISO>",
      "sources": [ { "kind": "note | document", "id": "<uuid>", "label": "Biology ch4" } ],
      "questions": [
        {
          "id": "<uuid>", "parent_id": null,
          "prompt": "…",
          "spans": [ { "source_id": "<uuid>", "page": 3, "text": "…" } ],
          "status": "open | answered | closed",
          "first_answer": "…", "first_answer_at": "<ISO>",
          "confidence": "low | medium | high | null", "confidence_at": "<ISO>",
          "revisions": [ { "text": "…", "at": "<ISO>" } ],
          "hints_seen": 0, "feedback_given": false,
          "followup_ids": [ "<uuid>" ]
        }
      ],
      "turns": [
        {
          "id": "<uuid>", "at_local": "<ISO>",
          "role": "coach | user",
          "kind": "question | answer | confidence | hint | feedback | revision | followup | notice",
          "question_id": "<uuid>",
          "mode": "live",
          "text": "…",
          "confidence": "low | medium | high | null",
          "quotes": [ { "source_id": "<uuid>", "page": 3, "quote": "…", "verified": true } ],
          "ungrounded_count": 0
        }
      ]
    }
  ]
}
```

### 14.1 New/changed field rules

| Field | Type | Required | Rule |
|---|---|---|---|
| schema_version | Integer | Yes | Now 2 (DATA-7); v1 files migrate; v1 apps refuse to overwrite v2 (FR-72). |
| settings.ai | Object | Optional | Missing → defaults (AIS-5); invalid members sanitised, never reject the file. |
| settings.ai.api_key | String | Optional | Local-only (A-28); masked in UI; redacted in errors. |
| documents[] | Array | Optional | Missing → []; non-list → corrupt (DATA-8). |
| documents[].stored_name | String | Yes | `<uuid>.pdf` under `library/`; absent binary → `status="missing"` (not corruption). |
| documents[].text_pages[] | Array | Yes (may be empty) | 1-based `page`, verbatim `text`; oversized individual pages clamped to 200,000 chars defensively. |
| documents[].status | String | Yes | `ready | textless | missing`; unknown → `missing`. |
| coach_sessions[] | Array | Optional | Missing → []; non-list → corrupt. |
| coach_sessions[].sources[] | Array | Yes | Snapshot triples; dangling ids tolerated (ERR-33). |
| questions[] / turns[] | Array | Yes (may be empty) | Invalid individual records skipped with partial-load warning (DATA-9). |
| questions[].first_answer | String | Optional | Immutable once present (A-25); loaders must never rewrite it. |
| turns[].quotes[] | Array | Optional | Stored post-validation with `verified` flag as stored-time truth; re-validation at display uses GRD-2. |

### 14.2 Derived data policy (additions)

| Derived Data | Stored? | Source |
|---|---|---|
| Normalised page index | No | `documents[].text_pages` / note bodies |
| Rendered page images | No (LRU cache only) | Library binary via PyMuPDF |
| Weak-topic scores / review rows | No | Coaching log (REV-1) |
| Coach activity metrics | No | Coaching log |
| Effective mode per turn | Yes (turn field) | Resolved at turn time (AIS-3) |
| Source pack | No | Selection snapshot + pack builder |

---

## 15. Project Structure Additions

```
app/core/agent/            NEW package (ARCH-5)
    context.py             source-pack builder + context assembler (A-26, SEL-2)
    prompt.py              system prompt + per-kind prompt templates (GEN-9/GEN-10)
    schema.py              CoachReply/Quote dataclasses + caps (GEN-11)
    parser.py              repair ladder (GEN-12, A-23)
    grounding.py           normalised index + validate_quotes (GRD-1..GRD-3)
    provider_base.py       CoachProvider interface (PROV-1)
    provider_openai.py     OpenAI-compatible client, urllib, redaction (PROV-2..PROV-7, ARCH-6)
    coach_service.py       CoachService state machine + ActionResult façade (COACH-*, INT-1)
app/core/pdfx.py           NEW extract/render via PyMuPDF (PDF-4..PDF-7, ARCH-7)
app/core/stats.py          EXTENDED review metrics (REV-1..REV-3)
app/core/models.py         EXTENDED Document, CoachSession, QuestionRecord, CoachTurn, AISettings
app/persistence/library.py NEW binary copy/delete/re-link (A-29, LIB-5/6, ARCH-7)
app/persistence/repository.py  EXTENDED schema-2 payload + migration (DATA-7..DATA-12)
app/persistence/validation.py  EXTENDED document/coach record parsers + skipping
app/state/app_state.py     EXTENDED collections, indexes, coach/document mutations (INT-2)
app/gui/screens/library_screen.py, coach_screen.py        NEW
app/gui/screens/stats_screen.py, settings_screen.py       EXTENDED (Review section, AI card)
app/gui/dialogs/source_select.py, sessions_dialog.py      NEW
app/gui/widgets/chat_bubble.py, citation_chip.py,
                confidence_picker.py, page_viewer.py      NEW
app/constants.py           EXTENDED (schema 2, caps, AI_TIMEOUT_S, APP_VERSION 1.1.0, nav/icons)
tests/                     EXTENDED (see §17)
scripts/perf_check.py      EXTENDED (NFR-12..NFR-14 budgets)
StudyProof.spec         EXTENDED (PyMuPDF hidden imports; assets unchanged + 2 icons)
```

---

## 16. Libraries and Tooling Additions

| Library / tool | Role (as used) |
|---|---|
| PyMuPDF (fitz) ≥ 1.24 | Per-page text extraction and page-pixmap rendering; fixture generation in tests. |
| stdlib `urllib.request` + `ssl` | Live provider calls only; no third-party HTTP dependency (keeps frozen build lean; ARCH-6). |
| PyInstaller ≥ 6.0 | Unchanged pipeline; spec gains `fitz` hidden imports and verifies library extraction from the frozen layout (NFR-18). |
| pytest ≥ 8.0 | Unchanged; new suites per §17. |

No other additions; CustomTkinter/Pillow/platformdirs/python-dateutil roles unchanged.

---

## 17. Testing, Build Gates, and Validation Additions

| ID | Requirement |
|---|---|
| QA-8 | Headless parser tests cover fenced JSON, embedded braces, invalid output, repair success and errors after repair fails. |
| QA-9 | Grounding tests: whitespace normalisation, case-insensitivity, wrong page, missing page, missing source, casing preservation. |
| QA-11 | Coach state-machine tests: illegal transitions refused, first-answer immutability, confidence-once, hint/feedback gating, revision append, follow-up linkage. |
| QA-12 | Provider simulation tests with a fake transport: missing key, timeout, 401/403, 429→retry→fail, 5xx, malformed body; assert redaction (NFR-16) and that nothing is stored on failed turns. |
| QA-13 | PDF pipeline tests with PyMuPDF-generated fixtures in temp dirs: import/copy, extract page counts/text, textless detection, caps refusal, duplicate refusal, delete cleanup, re-link. |
| QA-14 | Schema tests: v1→v2 migration, missing/new arrays, invalid record skipping, v1-reader refusal to overwrite v2 (simulated). |
| QA-15 | Review derivation tests: weak-score arithmetic, sorting, average confidence, empty history. |
| QA-16 | GUI tests exercise the coaching loop using fixed test replies, cancellation, PDF sources, Review and persistence. |
| QA-17 | Perf gate extension: NFR-12/13/14 budgets plus a 50-document × 50-page index/pack build and 5,000-attempt review derivation within budget; gate remains a hard build gate in `run_build.ps1`. |

---

## 18. Implementation Order and Milestones

| Milestone | Contents | Exit gate |
|---|---|---|
| M1 — Data & Library (non-AI) | DATA-7…12, PDF-1…7, LIB-1…6, schema-2 repository/validation, library binaries | QA-13, QA-14 green |
| M2 — Selection & Review shell (non-AI) | SEL-1…3, REV-1…3, UI-24/UI-25 | QA-15 green |
| M3 — Agent core (AI) | Live generation, parsing, grounding and state machine | Headless tests |
| M4 — Coach UI (AI) | Coach, library and settings wiring | GUI tests with fixed replies |
| M5 — Live provider (AI) | PROV-1…8, ERR-25…28/34…36, cancellation | QA-12 green |
| M6 — Gates & release | QA-17 perf extension, NFR-18 frozen validation, spec/build updates, README/requirements-doc sync | `run_build.ps1` fully green |

---

## 19. Future / Optional Enhancements — Out of Scope (additions)

| ID | Enhancement | Status |
|---|---|---|
| FUT-15 | Streaming token output in the coach thread. | Out of scope (whole-response turns only). |
| FUT-16 | Embeddings / vector RAG / multi-hop retrieval. | Out of scope (bounded source pack only). |
| FUT-17 | Spaced-repetition scheduling from confidence. | Out of scope (review list only). |
| FUT-18 | Voice input/output for coaching. | Out of scope. |
| FUT-19 | PDF annotation, highlighting, in-viewer search. | Out of scope. |
| FUT-20 | OCR for scanned/textless PDFs. | Out of scope (textless = viewer-only). |
| FUT-21 | OS keychain/credential-manager storage for the API key. | Out of scope (local masked save storage, A-28). |
| FUT-22 | Cross-source questions spanning > 5 sources or > 6,000-char packs. | Out of scope (A-37 caps). |
| FUT-23 | Export of coach transcripts / library text. | Out of scope. |
| FUT-24 | Multiple live providers simultaneously / per-session provider switching. | Out of scope (single configured endpoint). |

v1's out-of-scope list (FUT-1…FUT-14) remains unchanged; FUT-3 stays delivered as the Study Stopwatch.

---

**End of AI Requirements Document.** 
