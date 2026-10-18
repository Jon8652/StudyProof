"""Base class for primary screens (Parts E-H).

Every screen gets a title area, a content frame, and a ``refresh()`` hook
that AppState listeners call after any data change.
"""

from __future__ import annotations

import customtkinter as ctk

from app.gui.theme import SCREEN_PAD_X
from app.state.app_state import AppState
from app.utils.resources import load_photo


class BaseScreen(ctk.CTkFrame):
    # Uniform horizontal gap between the screen edge and *all* content
    # (title, cards, buttons, text).  Screens opt out by overriding this
    # with 0 (Statistics keeps its original flush layout).
    PAD_X = SCREEN_PAD_X

    def __init__(self, master, state: AppState, title: str = ""):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.state = state
        self._title_text = title

        self.title_label = ctk.CTkLabel(
            self, text=title, font=ctk.CTkFont(size=22, weight="bold"), anchor="w"
        )
        if title:
            self.title_label.pack(fill="x", pady=(6, 10), padx=self.PAD_X)

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=self.PAD_X)

    def refresh(self) -> None:
        """Re-pull derived data from AppState and update widgets."""
        raise NotImplementedError

    # Friendly empty-state helper shared by all screens (Part E/J/K).
    def show_empty_state(self, parent, message: str):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        image = load_photo("empty_state", size=64)
        if image is not None:  # illustration is optional (FR-79 fallback)
            frame._empty_image_ref = image
            ctk.CTkLabel(frame, image=image, text="").pack(pady=(0, 6))
        label = ctk.CTkLabel(
            frame,
            text=message,
            font=ctk.CTkFont(size=13),
            text_color=("gray40", "gray65"),
            justify="center",
            wraplength=420,
        )
        label.pack()
        return frame
