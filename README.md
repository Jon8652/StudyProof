# StudyProof

**StudyProof** (v1.2.0) is a complete cross-platform desktop app for helping you study, supporting study sessions tracking — XP, medals, streaks, stopwatch, calendar, notes, stats, a PDF library, paired with an AI Study Coach. Built with Python + CustomTkinter and packaged as a standalone Windows executable.

## Features

- **One-tap session logging** — 30 min / 1 h / 2 h presets plus a custom
  hours-and-minutes dialog with friendly inline validation (5 min – 12 h).
- **XP engine** — 10 XP per *completed* 10-minute block (partial blocks earn
  nothing); sessions crossing midnight are attributed to their start day.
- **Daily medals** — Bronze ≥ 120 min, Silver ≥ 180 min, Gold ≥ 300 min per
  day, shown on Home, the calendar, and day details (image when available,
  text fallback otherwise).
- **Streaks** — qualifying day = ≥ 10 studied minutes; current & longest
  streaks shown on Stats (today doesn't break the streak until the day ends).
- **Calendar** — Monday-first month grid, medal-colored day cells, click any
  day to review/delete its sessions and see that day's notes. Day tiles are
  light grey in light mode (dark grey in dark mode), and today's tile uses
  the same blue accent as the "Today" button in the top-right of the header
  so the two always match.
- **Notes** — a dedicated Notes screen for titled free-text notes: create,
  edit, search, and delete them via a list + detail-pane layout. Each note
  can optionally be attached to a calendar day (it then also appears on that
  day in the Calendar and in the day-details dialog); undated notes are
  allowed too. The selected note's day and "Updated …" timestamp are shown
  at the bottom-right of the detail pane, next to the Delete button, so they
  never overlap long, wrapped titles. Limits: 120 characters per title,
  100k per body; empty notes (no title *and* no text) are rejected with a
  friendly message. Every create/edit/delete autosaves immediately.
- **Stats** — weekly / monthly / lifetime totals, active days, averages,
  medal counts, and a total-notes count.
- **PDF Library** — import local PDFs, extract page text in the background,
  and browse pages with zoom and a text view. Files are copied into the local
  library. Protected, oversized and textless files receive friendly guidance.
- **Study Coach** — live AI questions, grounded hints
  and feedback, immutable first answers, revisions, follow-ups and saved
  conversations. Statistics lists the same specific weak topics as Revise Weak
  Topics and opens their revision sessions without showing scores or confidence ratings.
  Includes the accessibility touches from the v2 spec: role prefixes
  ("Coach:"/"You:") and textual verified ticks, tooltips explaining disabled
  gated buttons (ACC-3/ACC-4/ACC-5).
- **Settings** — dark/light theme (persisted), manual save, and a safe
  "Load from file" flow (validate → confirm → backup → replace).
- **Study Stopwatch** — a digital stopwatch in the sidebar (underneath the Settings nav button, above the version label) with **Start / Stop / Reset** controls. It shows elapsed time as `H:MM:SS` and defaults to `0:00:00` when the app opens. Stopping only pauses the run (the reading is kept so you can resume). **Resetting** a run of **10 minutes or more** automatically logs it as a custom study session, rounded *down* to the nearest 10 minutes (e.g. `2:35:12` → 2 hours 30 minutes); shorter runs are discarded without logging, and Reset always returns the display to zero. Timing uses a monotonic clock for accuracy, and the widget is themed for both light and dark modes with distinct colors in each.
- **Resilient persistence** — atomic JSON writes, corrupt-file backup +
  quarantine recovery, schema-version protection, close-flush of unsaved
  changes. Data lives in the OS app-data folder (`%APPDATA%\StudyProof`
  on Windows). Notes are stored in a `notes` array inside `studyproof_save.json`
  alongside sessions; invalid note entries are skipped during validation so
  a damaged record never blocks loading.

## Study Coach

Open **Coach → New session**, choose up to five notes or PDFs, and answer the
opening question. Continue naturally: retry an answer, ask for an explanation,
or choose another topic from your sources. The opening question retains strict
JSON validation; later visible replies accept ordinary study prose.

A separate background assessor identifies the specific question and source,
then rates the latest answer with a number from 0–100. These private prompts and
scores never appear in the conversation. Scores below 70 flag a topic; 85 or
higher clears it. Intermediate answers keep an existing flag. Acknowledgments,
explanation requests, and topic switches are not scored. Assessment errors do
not block coach replies or erase existing weak topics.

Use **Revise Weak Topics** beside **New session** and **Sessions** to choose a
flagged topic and practise its saved question with the coach. A strong answer
in either the original conversation or revision mode removes that topic alone.
The `weak_topics` register stores each topic, source, question and private score
inside the application's JSON save, using the same atomic save and backup flow.
Deleting a conversation keeps its unresolved topics. Removed sources remain
listed but cannot be revised until restored.

**Coach Recap** in the Coach screen's top row summarises the last seven
calendar days: recorded study effort, coached topics, improvements supported
by saved assessments, current revision topics and unanswered questions. Resume
opens the exact question, including questions in conversations whose sources
were removed. Loose ends have separate topic/question cards with one Resume
button each; unrelated source titles are never combined into a question title.
Questions inside ordinary Coach replies are tracked too. Older untracked
questions can be recovered from their literal conversation text, and changing
topics or acknowledging a reply does not count as answering the previous question.
Each weekly recap offers an optional 30-day recap, appended
below it. Recaps autosave inside the current conversation; without a selected
conversation, a temporary preview is shown without creating a new session.

Comparisons use your own preceding routine (up to 28 days, with at least
14 days of history and three prior sessions). There is no new goal-setting UI.
Below 70% of that routine gets a neutral invitation; above 150% suggests a
lighter day only with three consecutive unusually heavy days or declining
assessment results. Thin history gets facts without comparisons. Older saves
may lack the evidence needed to establish topic improvement; new private
assessment evidence is retained on answer turns for future comparisons.

Recaps work without an AI connection. When connected, the existing provider
selects from verified wording alternatives using only aggregate recap facts
and topic labels; raw private scores, complete source texts and navigation
IDs are excluded. Invalid or invented content is replaced with local wording.
Cancel saves no partial recap. All figures and Resume links are assembled
locally, including when the provider fails.

The Study Coach requires a configured live AI service. Open
**Settings → Study Coach (AI)** and enter an OpenAI-compatible Base URL,
key, then select deepseek-flash or deepseek-v4-pro. The URL should include the API prefix (for example `/v1`),
without `/chat/completions`. Live requests send only the bounded source pack,
question state and last ten turns. The source budget is 1,000,000 characters;
large packs can produce large provider prompts and token costs. Keys are stored locally in the save file,
masked in Settings and excluded from prompts and error messages. There is
no telemetry. Failed turns show an error and can be retried; Cancel immediately
restores the composer and discards late responses.

The opening question uses bounded JSON parsing with one repair retry. Later
coaching replies use flexible prose while private assessments have their own
validated boundaries. Source quotations on structured replies are verified
against the selected source pack. Sessions preserve first answers and later
attempts, and support follow-up questions and resume/delete. The `urllib` Live
client provides a 30 s timeout, one retry on
429/5xx, strict key redaction, connection testing, sub-second cancellation,
and configuration validation before requests. Missing credentials and failed
replies leave the failed turn unsaved. Automated tests use fixed provider replies.

## Requirements

- Python 3.10+ with Tk/Tkinter (bundled with python.org installers on
  Windows)
- Dependencies: `pip install -r requirements.txt`
  (customtkinter, Pillow, PyMuPDF, platformdirs, python-dateutil, pyinstaller, pytest)

## Run from source

```bash
python main.py
```

## Build the Windows executable

Run on Windows from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_build.ps1
```

The script creates a virtual environment, installs dependencies (including
PyMuPDF before any tests), runs the test suite, performance gates and full
GUI smoke test, then builds with
`StudyProof.spec` (PyInstaller **one-folder, windowed** build — no
console window). It then runs the packaged executable against temporary
data to verify bundled assets, PDF extraction/rendering, coach session
creation, settings and save/reload without network calls. A failed gate stops the build. Output:

```
dist\StudyProof\StudyProof.exe   ← working executable
```

Distribute the entire `dist\StudyProof` folder (it contains the bundled
`assets` folder and all runtime dependencies; no Python installation is
required on the target machine). The exe can also be built manually with:

```bash
pyinstaller --noconfirm StudyProof.spec
```

Assets resolve correctly both from source and in the frozen build via
`app/utils/paths.get_resource_dir()`; missing assets never crash the app
(medal images fall back to “Bronze” / “Silver” / “Gold” text).

## Tests & performance validation

```bash
python -m pytest tests -q          # unit and native GUI integration tests
xvfb-run -a python tests/smoke_gui.py   # end-to-end GUI smoke test (Linux CI)
python scripts/perf_check.py       # 10k sessions plus v2 performance budgets
```

`scripts/perf_check.py` generates exactly 10,000 study sessions and asserts
startup, refresh, index and autosave budgets. It also checks 200-page PDF
extraction ≤15 seconds, page rendering at zoom 1.0 ≤300 ms,
and a five-document pack ≤100 ms. The scalability checks materialise
a 50-document × 50-page index and derive Review over 5,000 attempts, each
within one second. Fixture generation is outside the measured operations.

The frozen verification report is written to `_tmp/frozen-verification.json`.
Release validation on 19 October 2026 passed **122 automated tests**, the
complete GUI smoke test, every performance gate and **32 frozen-app checks**.
To rerun that check independently (without accessing your normal study data):

```powershell
Start-Process -FilePath .\dist\StudyProof\StudyProof.exe -ArgumentList '--verify-release', 'release-report.json' -Wait
Get-Content release-report.json
```

## Project layout

```
main.py                 entry point
StudyProof.spec      PyInstaller build configuration (Part M)
run_build.ps1           Windows release build script
app/constants.py        gameplay rules (XP, medals, streaks, limits)
app/core/               models, study metrics, PDF extraction/rendering
app/core/agent/         coach state machine, grounding, live provider
                        (schema, parser, prompt, context pack builder,
                        coach_service, provider_base,
                        provider_openai)
app/release_check.py    isolated diagnostic used by the frozen build gate
app/persistence/        JSON repository, schema, atomic save, backup/recovery,
                        library binary storage (import/delete/re-link)
app/state/              AppState: single source of truth + incremental index
                        (session and note mutations/views, document and coach
                        indexes, total-notes stat)
app/gui/                seven screens (Home, Calendar, Notes, Library, Coach,
                        Stats, Settings), dialogs (source selection, saved
                        sessions) and widgets (page viewer, chat bubbles,
                        citation chips, stopwatch)
app/utils/              paths (source vs frozen resources), asset loader
assets/               PNG icons & medal images (user artwork, any size/mode)
tests/                  unit tests + GUI smoke test
scripts/perf_check.py   Part L performance validation
```

## Save data location

| OS | Path |
|----|------|
| Windows | `%LOCALAPPDATA%\StudyProof\studyproof_save.json` |
| macOS/Linux | platform-standard user data dir |

A `studyproof_save.backup.json` backup and quarantine copies of corrupt files
are kept alongside.
PDF binaries live in the adjacent `library` folder. Schema 1 saves migrate
to schema 2 on load; coaching history and extracted page text share the same
autosave and backup flow. Copy the library folder along with the JSON when
moving to another device, or re-link missing PDFs in the Library screen.
