"""Compatibility shim for a customtkinter 6.0.0 scrollbar bug.

customtkinter 6.0.0 introduced ``CTkScrollbar._motion_center_offset`` (the
offset between the click point and the thumb centre, set on Button-1) but
only initialises it inside the canvas *tag* bindings ``_clicked`` /
``_clicked_scrollbar``.  ``__init__`` never sets it, so any ``<B1-Motion>``
event that reaches ``CTkScrollbar._on_motion`` before a tagged Button-1 press
raises::

    AttributeError: 'CTkScrollbar' object has no attribute '_motion_center_offset'

StudyProof's coach auto-scroll pins deliberately bind extra ``<B1-Motion>``
handlers on every scrollbar with ``add='+'`` (gesture stamping), which makes
such motion-before-press sequences reachable in normal use.

Fix strategy (minimal impact): wrap ``CTkScrollbar.__init__`` so every new
instance gets ``_motion_center_offset = 0`` right after construction — exactly
the value a thumb grab would set — without touching any drag/scroll behaviour.
Idempotent, version-tolerant (no-ops if the library already initialises the
attribute or its internals change shape), and applied at import time so it
covers both ``python main.py`` and the packaged exe.
"""
from __future__ import annotations


def apply() -> None:
    """Patch CTkScrollbar so _motion_center_offset always exists. Safe no-op otherwise."""
    try:
        from customtkinter.windows.widgets.ctk_scrollbar import CTkScrollbar
    except ImportError:  # customtkinter not installed; main.py reports this already
        return

    # Nothing to do if a newer customtkinter initialises the attribute itself.
    probe = CTkScrollbar.__dict__.get("__init__")
    if probe is not None and getattr(probe, "_studyproof_offset_fix", False):
        return
    try:
        src = probe.__code__.co_names if probe is not None else ()
        if "_motion_center_offset" in src:
            return
    except AttributeError:
        pass

    original_init = CTkScrollbar.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Set AFTER super().__init__() because CTkBaseClass clears unknown
        # attributes on the widget during construction.
        self._motion_center_offset = 0

    patched_init._studyproof_offset_fix = True
    CTkScrollbar.__init__ = patched_init


apply()
