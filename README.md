# Study Tracker Desktop

A cross-platform desktop app for tracking daily study sessions with XP,
medals (Bronze / Silver / Gold), streaks, a calendar view, and statistics —
built with Python + CustomTkinter and packaged as a standalone Windows
executable with PyInstaller.

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
- **Settings** — dark/light theme (persisted), manual save, and a safe
  "Load from file" flow (validate → confirm → backup → replace).
- **Study Stopwatch** — a digital stopwatch in the sidebar (underneath the Settings nav button, above the version label) with **Start / Stop / Reset** controls. It shows elapsed time as `H:MM:SS` and defaults to `0:00:00` when the app opens. Stopping a run of **10 minutes or more** automatically logs it as a custom study session, rounded *down* to the nearest 10 minutes (e.g. `2:35:12` → 2 hours 30 minutes); shorter runs keep their reading so you can resume, and Reset always returns the display to zero. Timing uses a monotonic clock for accuracy, and the widget is themed for both light and dark modes with distinct colors in each.
- **Resilient persistence** — atomic JSON writes, corrupt-file backup +
  quarantine recovery, schema-version protection, close-flush of unsaved
  changes. Data lives in the OS app-data folder (`%APPDATA%\StudyTracker`
  on Windows). Notes are stored in a `notes` array inside `study_data.json`
  alongside sessions; invalid note entries are skipped during validation so
  a damaged record never blocks loading.

## Requirements

- Python 3.10+ with Tk/Tkinter (bundled with python.org installers on
  Windows)
- Dependencies: `pip install -r requirements.txt`
  (customtkinter, Pillow, platformdirs, python-dateutil, pyinstaller)

## Run from source

```bash
python main.py
```

## Build the Windows executable

Run on Windows from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_build.ps1
```

The script creates a virtual environment, installs dependencies, runs the
test suite and the performance gate, then builds with
`study_tracker.spec` (PyInstaller **one-folder, windowed** build — no
console window). Output:

```
dist\StudyTracker\StudyTracker.exe   ← working executable
```

Distribute the entire `dist\StudyTracker` folder (it contains the bundled
`assets` folder and all runtime dependencies; no Python installation is
required on the target machine). The exe can also be built manually with:

```bash
pyinstaller --noconfirm study_tracker.spec
```

Assets resolve correctly both from source and in the frozen build via
`app/utils/paths.get_resource_dir()`; missing assets never crash the app
(medal images fall back to “Bronze” / “Silver” / “Gold” text).

## Tests & performance validation

```bash
python -m pytest tests -q          # 57 unit tests (core engines + persistence)
xvfb-run -a python tests/smoke_gui.py   # end-to-end GUI smoke test (Linux CI)
python scripts/perf_check.py       # Part L budgets @ ~10k sessions
```

`scripts/perf_check.py` generates a realistic multi-year history and asserts
responsiveness budgets (startup load, home/calendar/stats refresh, session
delete + autosave). Latest run: **PASS** (startup ≈35 ms, stats ≈3 ms,
delete+save ≈60 ms).

## Project layout

```
main.py                 entry point
study_tracker.spec      PyInstaller build configuration (Part M)
run_build.ps1           Windows release build script
app/constants.py        gameplay rules (XP, medals, streaks, limits)
app/core/               pure logic: models (incl. Note), xp, medals, streaks,
                        stats, dates
app/persistence/        JSON repository, schema, atomic save, backup/recovery
app/state/              AppState: single source of truth + incremental index
                        (session and note mutations/views, total-notes stat)
app/gui/                MainWindow, screens (home/calendar/notes/stats/
                        settings), dialogs (incl. note editor & day details),
                        widgets (medal badge, summary card)
app/utils/              paths (source vs frozen resources), asset loader
assets/               PNG icons & medal images (user artwork, any size/mode)
tests/                  unit tests + GUI smoke test
scripts/perf_check.py   Part L performance validation
```

## Save data location

| OS | Path |
|----|------|
| Windows | `%APPDATA%\StudyTracker\study_data.json` |
| macOS/Linux | platform-standard user config dir |

A `.bak` backup and a quarantine copy of corrupt files are kept alongside.
