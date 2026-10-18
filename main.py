"""Study Tracker Desktop — application entry point (Part M).

Run from source:   python main.py
Packaged exe:      built by study_tracker.spec via PyInstaller (run_build.ps1)
"""

from __future__ import annotations

import sys


def main() -> int:
    # Fail fast with an honest message if the GUI toolkit is unavailable,
    # instead of a confusing traceback (non-goal: no silent degradation).
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("Study Tracker requires CustomTkinter. Install dependencies "
              "with: pip install -r requirements.txt")
        return 1

    from app.gui.main_window import MainWindow
    from app.state.app_state import AppState

    app = MainWindow(app_state=AppState())
    app.run_startup_load()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
