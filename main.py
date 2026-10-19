"""StudyProof development entry point. Run with: python main.py."""

from __future__ import annotations

import sys


def main() -> int:
    # Fail fast with an honest message if the GUI toolkit is unavailable,
    # instead of a confusing traceback (non-goal: no silent degradation).
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("StudyProof requires CustomTkinter. Install dependencies "
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
