"""Settings screen (Part I).

- Dark/light appearance toggle that persists across restarts (FR-51..FR-53).
- Manual Save writes current state immediately (FR-59).
- Manual Load: file chooser -> validate -> confirm -> backup + replace (Flow 4).
- Data status area: save location, total sessions, last saved, backup status.

Sections are presented as bordered cards with a small icon next to each
heading; the active theme button is highlighted with the accent colour.
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import filedialog

from app.gui.dialogs.confirm_dialog import ask_yes_no
from app.gui.dialogs.message_dialog import show_message
from app.gui.screens.base_screen import BaseScreen
from app.gui.theme import (
    ACCENT,
    ACCENT_HOVER,
    CARD_PAD_X,
    MUTED_TEXT,
    NEUTRAL,
    NEUTRAL_HOVER,
    NEUTRAL_TEXT,
    card,
    neutral_button,
)
from app.persistence import backup as backup_ops
from app.utils.date_utils import format_dt
from app.utils.paths import get_default_save_path


def _section_card(parent, icon: str, title: str, subtitle: str) -> ctk.CTkFrame:
    """A bordered settings card with an icon chip + heading + description."""
    section = card(parent, corner_radius=14)
    section.pack(fill="x", pady=(0, 12))

    head = ctk.CTkFrame(section, fg_color="transparent")
    head.pack(fill="x", padx=CARD_PAD_X, pady=(12, 2))
    # CTkLabel indents its inner text by min(corner_radius, height/2)
    # (default corner radius 6 → a hidden ~6 px left inset).  Every label in
    # the card must set corner_radius=0, otherwise its text starts a few
    # pixels right of the pack(padx=CARD_PAD_X) edge and sections stop
    # lining up.
    ctk.CTkLabel(head, text=icon, font=ctk.CTkFont(size=16),
                 corner_radius=0).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(head, text=title, corner_radius=0,
                 font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
    ctk.CTkLabel(section, text=subtitle, corner_radius=0,
                 font=ctk.CTkFont(size=12), text_color=MUTED_TEXT,
                 anchor="w").pack(anchor="w", padx=CARD_PAD_X)
    return section


class SettingsScreen(BaseScreen):
    def __init__(self, master, state, on_theme_change=None):
        super().__init__(master, state, title="Settings")
        self._on_theme_change = on_theme_change

        # --- Appearance ---------------------------------------------------------
        section = _section_card(
            self.content, "\u263E", "Appearance",
            "Choose how the app looks. Your choice is saved automatically.")

        theme_row = ctk.CTkFrame(section, fg_color="transparent")
        theme_row.pack(anchor="w", padx=CARD_PAD_X, pady=(8, 14))
        self.dark_button = ctk.CTkButton(
            theme_row, text="Dark", width=110, height=34, corner_radius=8,
            command=lambda: self._set_theme("dark"))
        self.light_button = ctk.CTkButton(
            theme_row, text="Light", width=110, height=34, corner_radius=8,
            fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER, text_color=NEUTRAL_TEXT,
            command=lambda: self._set_theme("light"))
        self.dark_button.pack(side="left", padx=(0, 8))
        self.light_button.pack(side="left")

        # --- Data management ------------------------------------------------------
        data = _section_card(
            self.content, "\U0001F4BE", "Data management",
            "Your data autosaves after every change. You can also save or "
            "load a file manually.")

        buttons = ctk.CTkFrame(data, fg_color="transparent")
        buttons.pack(anchor="w", padx=CARD_PAD_X, pady=(8, 14))
        ctk.CTkButton(buttons, text="Save now", width=120, height=34,
                      corner_radius=8, command=self._manual_save).pack(
            side="left", padx=(0, 8))
        neutral_button(buttons, text="Load from file", width=140, height=34,
                       command=self._manual_load).pack(side="left")

        # --- Status ------------------------------------------------------------------
        status = _section_card(
            self.content, "\u2139\uFE0F", "Data status",
            "Where your data lives and when it was last written.")
        self.status_label = ctk.CTkLabel(
            status, text="", font=ctk.CTkFont(size=12),
            text_color=MUTED_TEXT, justify="left", anchor="w", wraplength=560,
            # CTkLabel indents its inner text by min(corner_radius, height/2)
            # (default corner radius 6 here).  Zero it so the status text
            # starts at exactly the same left edge (card padx=CARD_PAD_X)
            # as the
            # Appearance and Data management sections below/above it.
            corner_radius=0,
        )
        self.status_label.pack(anchor="w", padx=CARD_PAD_X, pady=(6, 14))

        self.refresh()

    # ------------------------------------------------------------------------

    def _set_theme(self, theme: str) -> None:
        result = self.state.set_theme(theme)
        if not result.ok:
            from app.gui.dialogs.message_dialog import show_error

            show_error(self, result.message or "Could not change the theme.")
            return
        if self._on_theme_change:
            self._on_theme_change(theme)
        self.refresh()

    def _manual_save(self) -> None:
        result = self.state.manual_save()
        if result.ok:
            show_message(self.winfo_toplevel(), "Saved",
                         "Your study data has been saved.")
        else:
            from app.gui.dialogs.message_dialog import show_error

            show_error(self.winfo_toplevel(), result.message or "Saving failed.")
        self.refresh()

    def _manual_load(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Choose a Study Tracker save file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return  # canceled in file chooser: no changes (Flow 4)

        # Step 1: validate WITHOUT touching current data (FR-61).
        prepared = self.state.prepare_manual_load(path)
        if not prepared.ok:
            show_message(
                self.winfo_toplevel(), "Couldn't open that file",
                prepared.message or "That file could not be read as a valid "
                                    "Study Tracker save. Your current data was "
                                    "not changed.",
            )
            return

        # Step 2: confirm before replacing (FR-62 / A-9).
        confirmed = ask_yes_no(
            self.winfo_toplevel(), "Replace current data?",
            "Replace your current study data with the selected save file?\n\n"
            "A backup of your current data will be created first.",
            confirm_text="Replace", cancel_text="Cancel",
        )
        if not confirmed:
            return  # canceled: keep current data unchanged

        # Step 3: backup + replace + autosave as new default (Flow 4).
        result = self.state.commit_manual_load(prepared.value)
        if result.ok:
            show_message(self.winfo_toplevel(), "Data loaded",
                         "Your study data has been replaced with the selected "
                         "file. A backup of the previous data was kept.")
        else:
            show_message(
                self.winfo_toplevel(), "Couldn't finish loading",
                result.message or "The data could not be saved after loading. "
                                  "Your current data was not changed.",
            )
        self.refresh()

    def refresh(self) -> None:
        theme = self.state.settings.theme
        if theme == "dark":
            self.dark_button.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                       text_color="white", text="Dark \u2713")
            self.light_button.configure(fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
                                         text_color=NEUTRAL_TEXT, text="Light")
        else:
            self.light_button.configure(fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                        text_color="white", text="Light \u2713")
            self.dark_button.configure(fg_color=NEUTRAL, hover_color=NEUTRAL_HOVER,
                                       text_color=NEUTRAL_TEXT, text="Dark")

        session_count = len(self.state.dataset.sessions)
        last_saved = self.state.dataset.updated_at
        backup_state = "available" if backup_ops.backup_exists() else "not created yet"
        self.status_label.configure(
            text=(
                f"Save file location:  {get_default_save_path()}\n"
                f"Total sessions:  {session_count}\n"
                f"Last saved:  {format_dt(last_saved) if last_saved else 'not saved yet'}\n"
                f"Backup:  {backup_state}"
            )
        )
