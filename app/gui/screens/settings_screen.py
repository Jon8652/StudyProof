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
import threading
from dataclasses import asdict
from app.core.agent.provider_openai import OpenAICompatProvider
from app.core.agent.provider_base import ProviderError
from app.gui.workers import UIEventQueue
from tkinter import filedialog

from app.constants import AI_MODEL_CHOICES
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
        body = ctk.CTkScrollableFrame(self.content, fg_color='transparent')
        body.pack(fill='both', expand=True)
        self.content = body
        self._on_theme_change = on_theme_change
        self._ai_snapshot = None
        self._ai_events = UIEventQueue(self)
        self._ai_events.start()

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

        ai_card = _section_card(self.content, 'AI', 'Study Coach (AI)',
            'Your key stays on this device and is sent only to the base URL above.')
        self.ai_entries = {}
        for key, label in [('base_url', 'Base URL'), ('api_key', 'API key')]:
            row = ctk.CTkFrame(ai_card, fg_color='transparent')
            row.pack(fill='x', padx=CARD_PAD_X, pady=3)
            ctk.CTkLabel(row, text=label, width=80, anchor='w').pack(side='left')
            entry = ctk.CTkEntry(row, show='*' if key == 'api_key' else '')
            entry.pack(side='left', fill='x', expand=True)
            entry.bind('<FocusOut>', lambda _: self._save_ai())
            entry.bind('<Return>', lambda _: self._save_ai())
            self.ai_entries[key] = entry
        # Change request #4: the model is no longer free text.  The user
        # must pick one of the exact supported names from this dropdown.
        model_row = ctk.CTkFrame(ai_card, fg_color='transparent')
        model_row.pack(fill='x', padx=CARD_PAD_X, pady=3)
        ctk.CTkLabel(model_row, text='Model', width=80, anchor='w').pack(side='left')
        self.ai_model_menu = ctk.CTkOptionMenu(
            model_row, values=list(AI_MODEL_CHOICES), command=self._save_ai)
        self.ai_model_menu.pack(side='left', fill='x', expand=True)
        self.ai_entries['model'] = self.ai_model_menu
        controls = ctk.CTkFrame(ai_card, fg_color='transparent')
        controls.pack(fill='x', padx=CARD_PAD_X, pady=4)
        ctk.CTkCheckBox(controls, text='Show key', command=self._toggle_key).pack(side='left')
        ctk.CTkButton(controls, text='Clear key', width=90, command=self._clear_key).pack(side='left', padx=4)
        self.test_button = ctk.CTkButton(controls, text='Test connection', command=self._test_ai)
        self.test_button.pack(side='left', padx=4)
        ctk.CTkButton(controls, text='Save AI settings', command=self._save_ai).pack(side='left')
        self.ai_result = ctk.CTkLabel(ai_card, text='', wraplength=650)
        self.ai_result.pack(fill='x', padx=CARD_PAD_X)

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

    def _save_ai(self, *_ignored):
        raw = {key: entry.get() for key, entry in self.ai_entries.items()}
        raw['mode'] = 'live'
        self.state.set_ai_settings(raw)

    def _toggle_key(self):
        entry = self.ai_entries['api_key']
        entry.configure(show='' if entry.cget('show') else '*')

    def _clear_key(self):
        self.ai_entries['api_key'].delete(0, 'end')
        self._save_ai()

    def _test_ai(self):
        self._save_ai()
        provider = OpenAICompatProvider(self.state.settings.ai)
        self.test_button.configure(state='disabled')
        self.ai_result.configure(text='Testing connection…')
        def run():
            try:
                text = provider.test_connection()
            except ProviderError as exc:
                text = str(exc)
            except Exception:
                text = 'The AI service returned an unreadable reply.'
            self._ai_events.post(lambda: self._test_done(text))
        threading.Thread(target=run, daemon=True).start()

    def _test_done(self, text):
        self.ai_result.configure(text=text)
        self.test_button.configure(state='normal')

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
            title="Choose a StudyProof save file",
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
                                    "StudyProof save. Your current data was "
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
        # ``prepared.value`` is the validated LoadOutcome (v2 mechanics
        # already applied: v1→v2 migration, missing-binary notice).
        loaded = prepared.value
        dataset = getattr(loaded, "dataset", loaded)
        result = self.state.commit_manual_load(dataset)
        if result.ok:
            extra = ""
            if getattr(loaded, "missing_binaries", 0):
                extra = ("\n\n" + (loaded.notice or ""))
            show_message(self.winfo_toplevel(), "Data loaded",
                         "Your study data has been replaced with the selected "
                         "file. A backup of the previous data was kept."
                         + extra)
        else:
            show_message(
                self.winfo_toplevel(), "Couldn't finish loading",
                result.message or "The data could not be saved after loading. "
                                  "Your current data was not changed.",
            )
        self.refresh()

    def refresh(self) -> None:
        raw = asdict(self.state.settings.ai)
        if raw != self._ai_snapshot:
            self._ai_snapshot = raw
            for key, entry in self.ai_entries.items():
                if key == 'model':
                    # CTkOptionMenu has no delete/insert; set the exact
                    # model name (change request #4).
                    entry.set(raw[key])
                    continue
                entry.delete(0, 'end')
                entry.insert(0, raw[key])
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
