"""Medal badge widget (Part J).

Shows the medal image when the asset is available; otherwise falls back to
a friendly text label ("Bronze"/"Silver"/"Gold"). Missing assets must never
crash the app (FR-79 / ERR-12).
"""

from __future__ import annotations

import customtkinter as ctk

from app.core.medals import MEDAL_ASSET_KEYS, medal_label
from app.utils.resources import load_photo

# Medal accent colors (light/dark pairs)
MEDAL_COLORS = {
    "bronze": ("#a0673f", "#cd854f"),
    "silver": ("#8e9aa6", "#c0ccd8"),
    "gold":   ("#b8912f", "#e6c34c"),
}


class MedalBadge(ctk.CTkLabel):
    """Compact medal indicator for one day. Empty when no medal."""

    def __init__(self, master, medal: str | None = None, size: int = 20, **kwargs):
        super().__init__(master, **kwargs)
        self._size = size
        self._image_ref = None
        self.set_medal(medal)

    def set_medal(self, medal: str | None) -> None:
        self._medal = medal
        if not medal:
            self.configure(image=None, text="", width=0)
            return

        image = load_photo(MEDAL_ASSET_KEYS.get(medal, ""), size=self._size)
        if image is not None:
            # Keep a reference so tkinter doesn't GC the image.
            self._image_ref = image
            self.configure(image=image, text="")
        else:
            # Text fallback when the asset is missing (FR-79).
            self._image_ref = None
            fg = MEDAL_COLORS.get(medal, ("gray40", "gray70"))
            self.configure(
                image=None,
                text=medal_label(medal),
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=fg,
            )
