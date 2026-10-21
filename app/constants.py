"""Central constants and configuration for StudyProof."""

# --- Application identity ---------------------------------------------------
APP_NAME = "StudyProof"
APP_SLUG = "StudyProof"
APP_VERSION = "1.1.0"            # v2 release train (req doc §4)

# --- Persistence ------------------------------------------------------------
SAVE_FILE_NAME = "studyproof_save.json"
BACKUP_FILE_NAME = "studyproof_save.backup.json"
QUARANTINE_SUFFIX = ".corrupt"

SCHEMA_VERSION = 2               # current supported schema version (FR-70/DATA-7)

# --- PDF Study Library (v2, non-AI) ------------------------------------------
LIBRARY_DIR_NAME = "library"     # <app-data>/library/ binary copies (A-5/A-29)
MAX_PDF_BYTES = 100 * 1024 * 1024    # PDF-2: import size cap (ERR-30)
MAX_PDF_PAGES = 2000                  # PDF-2: page cap (ERR-30)
MAX_PAGE_CHARS = 200_000              # §14.1: defensive per-page clamp
PAGE_RENDER_CACHE_SIZE = 5            # A-30 / LIB-3: LRU page-image cache
ZOOM_LEVELS = (0.5, 1.0, 1.5, 2.0)    # UI-20 viewer zoom steps (start at 1.0×)
EXTRACTION_PROGRESS_HZ = 10           # NFR-12: at most ~10 progress updates/s

# --- Study Coach context (v2) -------------------------------------------------
MAX_SOURCES = 5                    # A-37 / SEL-1: sources per session
# A-37 / SEL-2: source-pack character budget.  Raised from 6,000 to
# 1,000,000 so whole documents/notes can be added to a new session
# without truncation (change request #3).
MAX_PACK_CHARS = 1_000_000
MAX_MEMORY_TURNS = 10              # A-26: last N turns feed generation
AI_TIMEOUT_S = 30                  # A-34 / PROV-2: live call timeout
# A-22: the model must be one of the exact names below; the settings UI
# offers them as a dropdown and the save file coerces anything else
# (change request #4).
AI_MODEL_CHOICES = ("deepseek-flash", "deepseek-v4-pro")
DEFAULT_AI_MODEL = AI_MODEL_CHOICES[0]
VALID_AI_MODES = ("live",)  # AIS-5 mode keys
CONFIDENCE_LEVELS = ("low", "medium", "high")   # A-36
CONFIDENCE_LABELS = {"low": "Guessed", "medium": "Unsure",
                     "high": "Confident"}       # A-36 display labels
DOC_STATUSES = ("ready", "textless", "missing", "extracting")  # LIB-1 chips

# --- Storage heads-up (NFR-17) --------------------------------------------------
SAVE_SIZE_WARN_BYTES = 50 * 1024 * 1024   # one-time friendly notice > 50 MB

# --- Defaults ---------------------------------------------------------------
DEFAULT_THEME = "dark"           # A-10: dark mode on first run
VALID_THEMES = ("dark", "light")

DEFAULT_XP_PER_10_MINUTES = 10   # A-1: 10 XP per completed 10 minutes (FR-9)

# --- Gamification rules (confirmed, fixed for v1) ----------------------------
XP_BLOCK_MINUTES = 10            # completed-block size used by the XP engine

BRONZE_MIN_MINUTES = 120         # FR-18
SILVER_MIN_MINUTES = 180         # FR-19
GOLD_MIN_MINUTES = 300           # FR-20

STREAK_QUALIFYING_MIN_MINUTES = 10   # A-7 / FR-47

# --- Session validation limits (A-4 / FR-4 / FR-5) ---------------------------
MIN_SESSION_MINUTES = 5
MAX_SESSION_MINUTES = 12 * 60    # 12 hours
MIN_INPUT_HOURS = 0
MAX_INPUT_HOURS = 12
MIN_INPUT_MINUTES = 0
MAX_INPUT_MINUTES = 59

# --- Preset countdown durations shown on the Home screen ----------------------
# Anti-cheat redesign: presets no longer log time instantly; they start a
# real-time countdown of the chosen length.  Only 1 h / 2 h presets remain
# (the 30-minute preset was dropped); custom lengths come from the dialog.
PRESET_DURATIONS_MINUTES = (60, 120)
PRESET_LABELS = {60: "1 Hour", 120: "2 Hours"}

# --- Window ------------------------------------------------------------------
WINDOW_TITLE = APP_NAME
WINDOW_WIDTH = 900
WINDOW_HEIGHT = 640
WINDOW_MIN_WIDTH = 700
WINDOW_MIN_HEIGHT = 520

# --- Session sources ----------------------------------------------------------
SOURCE_PRESET = "preset"
SOURCE_CUSTOM = "custom"
SOURCE_TIMER = "timer"   # recorded when a study countdown reaches zero
