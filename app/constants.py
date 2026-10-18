"""Central constants and configuration for Study Tracker Desktop v1."""

# --- Application identity ---------------------------------------------------
APP_NAME = "Study Tracker Desktop"
APP_SLUG = "StudyTrackerDesktop"
APP_VERSION = "1.0.0"

# --- Persistence ------------------------------------------------------------
SAVE_FILE_NAME = "study_tracker_save.json"
BACKUP_FILE_NAME = "study_tracker_save.backup.json"
QUARANTINE_SUFFIX = ".corrupt"

SCHEMA_VERSION = 1               # current supported schema version (FR-70)

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

# --- Preset durations shown on the Home screen (FR-1 / UI-5) -----------------
PRESET_DURATIONS_MINUTES = (30, 60, 120)
PRESET_LABELS = {30: "30 minutes", 60: "1 hour", 120: "2 hours"}

# --- Window ------------------------------------------------------------------
WINDOW_TITLE = APP_NAME
WINDOW_WIDTH = 900
WINDOW_HEIGHT = 640
WINDOW_MIN_WIDTH = 700
WINDOW_MIN_HEIGHT = 520

# --- Session sources ----------------------------------------------------------
SOURCE_PRESET = "preset"
SOURCE_CUSTOM = "custom"
