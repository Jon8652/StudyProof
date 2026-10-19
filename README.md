# StudyProof

A cross-platform desktop app for tracking daily study sessions with XP,
medals (Bronze / Silver / Gold), streaks, a calendar view, and statistics —
built with Python + CustomTkinter. During active development, run it directly
from source; executable packaging is disabled.

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
- **Study Coach** — offline Mock questions, automatic confidence assessments, grounded hints
  and feedback, immutable first answers, revisions, follow-ups and saved
  conversations. Review highlights weak sources and links back to coaching.
  Includes the accessibility touches from the v2 spec: role prefixes
  ("Coach:"/"You:") and textual verified ticks, tooltips explaining disabled
  gated buttons, and a "General guidance" badge for turns with zero verified
  quotes (ACC-3/ACC-4/ACC-5, GRD-5).
- **Settings** — dark/light theme (persisted), manual save, and a safe
  "Load from file" flow (validate → confirm → backup → replace).
- **Study Stopwatch** — a digital stopwatch in the sidebar (underneath the Settings nav button, above the version label) with **Start / Stop / Reset** controls. It shows elapsed time as `H:MM:SS` and defaults to `0:00:00` when the app opens. Stopping a run of **10 minutes or more** automatically logs it as a custom study session, rounded *down* to the nearest 10 minutes (e.g. `2:35:12` → 2 hours 30 minutes); shorter runs keep their reading so you can resume, and Reset always returns the display to zero. Timing uses a monotonic clock for accuracy, and the widget is themed for both light and dark modes with distinct colors in each.
- **Resilient persistence** — atomic JSON writes, corrupt-file backup +
  quarantine recovery, schema-version protection, close-flush of unsaved
  changes. Data lives in the OS app-data folder (`%APPDATA%\StudyProof`
  on Windows). Notes are stored in a `notes` array inside `studyproof_save.json`
  alongside sessions; invalid note entries are skipped during validation so
  a damaged record never blocks loading.

## Study Coach

Open **Coach → New session**, choose up to five notes or PDFs, and request a
question. Submit your first answer to unlock hints and feedback. The coach
automatically assesses your first answer during feedback. Revisions keep your first answer intact;
follow-ups start a new question in the same saved conversation. Statistics
shows weak topics and can reopen their latest coaching session.

Mock mode works offline without a key. For optional Live mode, open
**Settings → Study Coach (AI)** and enter an OpenAI-compatible Base URL,
model and key. The URL should include the API prefix (for example `/v1`),
without `/chat/completions`. Live requests send only the bounded source pack,
question state and last ten turns. Keys are stored locally in the save file,
masked in Settings and excluded from prompts and error messages. There is
no telemetry. Failed turns offer a one-click Mock fallback; Cancel immediately
restores the composer and discards late responses.

All AI requirements (COACH-, GEN-, GRD-, PROV- and AIS- families) are
implemented and covered by the automated test suite: deterministic Mock
generation with session-seeded output, the JSON repair ladder with Mock
fallback, lazy normalised quote grounding with strip-and-count, the guarded
question state machine (unaided first answer, coach-assessed confidence, gated
hints/feedback, follow-up child questions), saved-session management with
resume/delete, the opt-in `urllib` Live client (30 s timeout, one retry on
429/5xx, strict key redaction, connection testing, sub-second cancellation),
and per-turn Mock fallback when Live credentials are missing (PROV-3).

## Requirements

- Python 3.10+ with Tk/Tkinter (bundled with python.org installers on
  Windows)
- Dependencies: `pip install -r requirements.txt`
  (customtkinter, Pillow, PyMuPDF, platformdirs, python-dateutil, pytest)

## Run from source

From the project folder in PowerShell, set up the environment once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then start the app with:

```powershell
.\.venv\Scripts\python.exe main.py
```

If your environment already exists, only the final command is needed.
Executable builds, distribution folders and release-only checks have been
removed while development continues. Local environments, caches, temporary
files and generated build output are excluded from Git.

## Tests & performance validation

```bash
python -m pytest tests -q          # unit and native GUI integration tests
xvfb-run -a python tests/smoke_gui.py   # end-to-end GUI smoke test (Linux CI)
python scripts/perf_check.py       # 10k sessions plus v2 performance budgets
```

`scripts/perf_check.py` generates exactly 10,000 study sessions and asserts
startup, refresh, index and autosave budgets. It also checks 200-page PDF
extraction ≤15 seconds, page rendering at zoom 1.0 ≤300 ms, Mock generation
≤200 ms, and a five-document pack ≤100 ms. The scalability checks materialise
a 50-document × 50-page index and derive Review over 5,000 attempts, each
within one second. Fixture generation is outside the measured operations.

## Project layout

```
main.py                 entry point
app/constants.py        gameplay rules (XP, medals, streaks, limits)
app/core/               models, study metrics, PDF extraction/rendering
app/core/agent/         coach state machine, grounding, Mock/Live providers
                        (schema, parser, prompt, context pack builder,
                        mock_engine, coach_service, provider_base,
                        provider_openai)
app/persistence/        JSON repository, schema, atomic save, backup/recovery,
                        library binary storage (import/delete/re-link)
app/state/              AppState: single source of truth + incremental index
                        (session and note mutations/views, document and coach
                        indexes, total-notes stat)
app/gui/                seven screens (Home, Calendar, Notes, Library, Coach,
                        Stats, Settings), dialogs (source selection, saved
                        sessions) and widgets (page viewer, chat bubbles,
                        citation chips, stopwatch)
app/utils/              local data/resource paths and asset loader
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

In AI Settings, use **Load available models** to fetch model IDs from your configured service, then select a model from the list. Manual model ID entry is also supported. Coaching sessions accept up to 1,000,000 source characters; Live services may impose their own context limits. Confidence is assessed during feedback from the original answer.
