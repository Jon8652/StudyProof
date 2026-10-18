"""Tk-native image label that never leaves a dangling pyimage reference.

customtkinter's CTkLabel drives its inner tkinter.Label from CTkImage
objects whose underlying ``ImageTk.PhotoImage`` lives only as long as the
CTkImage does.  When a rendered PDF page is swapped out (document switch,
zoom change, LRU eviction) and the old CTkImage is garbage-collected while
Tk still holds its ``pyimageN`` name, the *next* redraw of the label —
which can happen asynchronously, e.g. inside CTkTextbox/CTkScrollableFrame
redraws or when clearing the previous image — raises::

    _tkinter.TclError: image "pyimageN" doesn't exist

That crash made page switching blank the viewer until restart.  This widget
sidesteps the whole class of problems by owning the tkinter.Label directly:

- it keeps a strong reference to every PhotoImage it has ever created;
- before assigning a new image it clears the label with an empty string
  (never ``None``, which makes Tk try to re-resolve the stale name);
- every configure call is wrapped defensively so a stale-image error can
  never propagate into the Tk callback/thread machinery again.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

import customtkinter as ctk

try:  # Pillow may be absent in stripped-down environments
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - viewer falls back to text pane
    Image = None
    ImageTk = None


class PageImageLabel(tk.Label):
    """A tkinter.Label that displays PIL images without pyimage races."""

    def __init__(self, master, *, bg: str = "white", fg: str = "gray30",
                 font: Optional[str] = None):
        super().__init__(master, bd=0, highlightthickness=0, bg=bg, fg=fg,
                         anchor="n", justify="left",
                         font=font or ("TkDefaultFont", 11))
        self._photo: Optional[object] = None
        self._retired_photos: list = []   # strong refs; freed only with widget
        self._last_image_key: Optional[tuple] = None
        self.bind("<Destroy>", self._on_destroy, add=True)

    # ------------------------------------------------------------------ API

    def set_message(self, message: str) -> None:
        """Show placeholder text (no image).  Never raises."""
        self._clear_image()
        self._safe_configure(text=message)

    def set_image(self, pil_image) -> None:
        """Display a PIL RGB image at native size.  Never raises."""
        if ImageTk is None or pil_image is None:
            self.set_message("Pillow is not installed — page images can't "
                             "be shown. Use the text view instead.")
            return
        key = (id(pil_image), pil_image.width, pil_image.height)
        if key != self._last_image_key:
            photo = ImageTk.PhotoImage(pil_image)
            # Keep the previous PhotoImage alive until this one is on screen:
            # freeing the old reference first is exactly what strands Tk on a
            # dead pyimageN during the redraw triggered by the swap.
            if self._photo is not None:
                self._retired_photos.append(self._photo)
            self._photo = photo
            self._last_image_key = key
        self._safe_configure(image=self._photo, text="")

    @property
    def has_image(self) -> bool:
        return self._photo is not None

    # ------------------------------------------------------------- internals

    def _clear_image(self) -> None:
        if self._photo is not None:
            self._retired_photos.append(self._photo)
            self._photo = None
        self._last_image_key = None
        self._safe_configure(image="", text="")

    def _safe_configure(self, **kwargs) -> None:
        try:
            self.configure(**kwargs)
        except tk.TclError:
            # A stale pyimageN (e.g. another interpreter-level race) must
            # degrade to "text only", never crash the app.
            try:
                text = kwargs.get("text", "")
                self.configure(image="", text=text)
            except tk.TclError:
                pass

    def _on_destroy(self, _event) -> None:
        self._photo = None
        self._retired_photos.clear()

    # ------------------------------------------------------- appearance sync

    def apply_appearance(self, is_dark: bool) -> None:
        """Match the surrounding viewer card colours to the CTk theme."""
        if is_dark:
            self.configure(bg="#1c1c26", fg="#a8b0bd")
        else:
            self.configure(bg="white", fg="gray30")


def theme_is_dark(widget) -> bool:
    """Best-effort: is customtkinter currently in dark mode for *widget*?"""
    try:
        applied = ctk.get_appearance_mode()
        return str(applied).lower().startswith("dark")
    except Exception:
        return False
