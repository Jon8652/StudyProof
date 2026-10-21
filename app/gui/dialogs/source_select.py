"""Source selection dialog (Phase 3.1, UI-25 / SEL-1 / A-37).

Modal with grouped checklists (Notes / Ready Documents), a search box that
filters both groups, a per-item size hint, and a live pack-size estimate.
The 5-source cap is enforced *at the checkbox*: clicking a sixth enabled
item never checks it — instead a friendly cap notice appears (A-37/SEL-1).

Returns the chosen sources as ``(kind, id)`` tuples in click order through
``dialog.result``, or None when cancelled.  The dialog itself performs no
mutation; the Coach screen (Phase 5) will use the result to create a
session via ``AppState.create_coach_session``.
"""

from __future__ import annotations

import customtkinter as ctk

from app.gui.dialogs._dialog_base import SafeDialog

from app.constants import MAX_PACK_CHARS, MAX_SOURCES
from app.core.agent.context import estimate_pack_size
from app.gui.theme import ACCENT, MUTED_TEXT


_CAP_NOTICE = (
    f"A coaching session can use up to {MAX_SOURCES} sources. "
    "Untick one before adding another."
)


class SourceSelectDialog(SafeDialog):
    """Modal source picker. Read ``self.result`` after ``wait_window``."""

    def __init__(self, master, state):
        super().__init__(master)
        self._app_state = state
        self.result: list[tuple[str, str]] | None = None
        # Selection order matters for the pack builder (leading pages of
        # earlier sources win budget).  Click order is preserved here.
        self._ordered_ids: list[tuple[str, str]] = []

        self.title("Choose study sources")
        self.transient(master.winfo_toplevel() if hasattr(master,
                                                          "winfo_toplevel")
                       else master)
        self.resizable(True, True)
        self.geometry("560x540")

        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(frame, text="Pick up to "
                     f"{MAX_SOURCES} sources for your next coaching "
                     "session.",
                     font=ctk.CTkFont(size=13), wraplength=500,
                     justify="left").pack(anchor="w", pady=(0, 8))

        self.search_entry = ctk.CTkEntry(
            frame, placeholder_text="Search notes and documents…")
        self.search_entry.pack(fill="x", pady=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda _e: self._rebuild())

        self.notice_label = ctk.CTkLabel(
            frame, text="", font=ctk.CTkFont(size=12),
            text_color=MUTED_TEXT, wraplength=520, justify="left", anchor="w")
        self.notice_label.pack(fill="x")

        self.scroll = ctk.CTkScrollableFrame(frame, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True)
        self.scroll.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(frame, fg_color="transparent")
        bottom.pack(fill="x", pady=(8, 0))

        self.pack_label = ctk.CTkLabel(
            bottom, text=self._pack_text(), font=ctk.CTkFont(size=12),
            text_color=MUTED_TEXT, anchor="w")
        self.pack_label.pack(side="left")

        ctk.CTkButton(bottom, text="Cancel", width=100,
                      fg_color=("gray30", "gray25"),
                      command=self._cancel).pack(side="right", padx=(6, 0))
        self.ok_button = ctk.CTkButton(bottom, text="Start session",
                                       width=130, command=self._confirm)
        self.ok_button.pack(side="right")

        self._checkboxes: dict[tuple[str, str], ctk.CTkCheckBox] = {}
        self._variables: dict[tuple[str, str], ctk.BooleanVar] = {}

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._rebuild()

        self.grab_set()

    # ------------------------------------------------------------------------

    def _entries(self) -> list[dict]:
        return self._app_state.selectable_sources()

    def _pack_text(self) -> str:
        selected = [self._entry_by_key(key) for key in self._ordered_ids]
        selected = [e for e in selected if e is not None]
        chars = estimate_pack_size(selected)
        return (f"Selected {len(self._ordered_ids)} of {MAX_SOURCES} · "
                f"~{chars:,}/{MAX_PACK_CHARS:,} characters")

    def _entry_by_key(self, key: tuple[str, str]) -> dict | None:
        for entry in self._cached_entries:
            if (entry["kind"], entry["id"]) == key:
                return entry
        return None

    def _show_notice(self, message: str) -> None:
        self.notice_label.configure(text=message)
        self.after(4000, lambda: self._clear_notice(message))

    def _clear_notice(self, expected: str) -> None:
        if self.notice_label.cget("text") == expected:
            self.notice_label.configure(text="")

    # ------------------------------------------------------------------------

    def _rebuild(self) -> None:
        query = self.search_entry.get().strip().lower()
        entries = self._entries()
        self._cached_entries = entries
        for child in self.scroll.winfo_children():
            child.destroy()
        self._checkboxes.clear()
        self._variables.clear()

        row = 0
        for group_kind, group_title in (("note", "Notes"),
                                        ("document", "Documents")):
            group = [e for e in entries if e["kind"] == group_kind]
            if query:
                group = [e for e in group
                         if query in e["label"].lower()]
            header = ctk.CTkLabel(
                self.scroll, text=group_title,
                font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
            header.grid(row=row, column=0, sticky="ew", pady=(10, 2))
            row += 1
            shown = 0
            for entry in group:
                key = (entry["kind"], entry["id"])
                checked = key in self._ordered_ids
                var = ctk.BooleanVar(value=checked)
                box = ctk.CTkCheckBox(
                    self.scroll,
                    text=f"{entry['label']}  ·  {entry['meta']}  ·  "
                         f"{entry['chars']:,} chars",
                    variable=var,
                    command=lambda k=key, v=var: self._toggled(k, v))
                if not entry["available"]:
                    box.configure(state="disabled",
                                  text=box.cget("text") +
                                       f"  —  {entry['reason']}")
                box.grid(row=row, column=0, sticky="w", pady=2)
                self._checkboxes[key] = box
                self._variables[key] = var
                row += 1
                shown += 1
            if not shown:
                ctk.CTkLabel(self.scroll, text="(none match)",
                             font=ctk.CTkFont(size=12),
                             text_color=MUTED_TEXT).grid(
                    row=row, column=0, sticky="w", pady=2)
                row += 1

        self._update_ok_state()

    def _toggled(self, key: tuple[str, str], var: ctk.BooleanVar) -> None:
        if var.get():
            if len(self._ordered_ids) >= MAX_SOURCES:
                # A-37: refuse silently + friendly notice, uncheck again.
                var.set(False)
                self._show_notice(_CAP_NOTICE)
                self._update_pack_label()
                return
            if key not in self._ordered_ids:
                self._ordered_ids.append(key)
        else:
            if key in self._ordered_ids:
                self._ordered_ids.remove(key)
        self._update_pack_label()
        self._update_ok_state()

    def _update_pack_label(self) -> None:
        self.pack_label.configure(text=self._pack_text())

    def _update_ok_state(self) -> None:
        self.ok_button.configure(
            state="normal" if self._ordered_ids else "disabled")

    # ------------------------------------------------------------------------

    def _confirm(self) -> None:
        if not self._ordered_ids:
            return
        self.result = list(self._ordered_ids)
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.grab_release()
        self.destroy()


def open_source_select(master, state) -> list[tuple[str, str]] | None:
    """Convenience helper: show the modal and return the selection."""
    dialog = SourceSelectDialog(master, state)
    dialog.wait_window()
    return dialog.result
