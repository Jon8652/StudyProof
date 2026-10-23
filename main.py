"""StudyProof — application entry point (Part M).

Run from source:   python main.py
Packaged exe:      built by StudyProof.spec via PyInstaller (run_build.ps1)
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == '--verify-release':
        from app.release_check import run
        return run(sys.argv[2])
    # Fail fast with an honest message if the GUI toolkit is unavailable,
    # instead of a confusing traceback (non-goal: no silent degradation).
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("StudyProof requires CustomTkinter. Install dependencies "
              "with: pip install -r requirements.txt")
        return 1
    # Defensive shim for the customtkinter 6.0.0 CTkScrollbar
    # _motion_center_offset bug (see app/gui/ctk_patch.py).
    from app.gui import ctk_patch  # noqa: F401

    from app.gui.main_window import MainWindow
    from app.state.app_state import AppState

    app = MainWindow(app_state=AppState())
    app.run_startup_load()
    app.mainloop()
    return 0


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    sys.exit(main())
