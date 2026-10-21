"""Shared base class for the app's modal dialogs.

Why this exists
---------------
customtkinter's ``CTkToplevel.__init__`` (and ``CTk.__init__``) install an
application-wide binding::

    self.bind_all("<Button-1>", set_focus, add=True)
    # where set_focus does:  if hasattr(event.widget, "focus_set"):
    #                         event.widget.focus_set()

That handler makes *every* click move keyboard focus to the clicked widget —
including widgets that are not meant to take focus at all, such as a
CTkButton's internal text label (a plain ``tkinter.Label``).  When a single
click both focuses such a label *and* runs a command that destroys the
dialog containing it (submitting/closing a dialog), Tk still has the pending
focus change queued against the now-dead window and prints::

    Exception in Tkinter callback
    _tkinter.TclError: bad window path name ".!customsessiondialog...!label"

to the console even though nothing visible breaks.  Targeted
``winfo_exists()`` guards cannot catch this because the offending call lives
inside customtkinter/Tk code, not in our callbacks.

The fix here is structural: :func:`_neutralise_ctk_global_focus` removes the
library's app-wide focus handler entirely (the same thing as calling
``unbind_all("<Button-1>")`` once at startup).  Every dialog derives from
:class:`SafeDialog`, which applies it on construction, and the main window
applies it too (see ``app.gui.app_state`` / ``main_window``).

Consequences of removing the handler — all acceptable for this app:

* Clicking a button or label no longer steals keyboard focus away from the
  entry you are typing in (arguably an improvement: previously every stray
  click destroyed the caret position in CTkEntries).
* Entries still receive focus on click through their *own* class bindings
  (``tk::EntryButton1``), so normal typing behaviour is unchanged.
* The only lost nicety is clicking a non-entry widget to move focus to it,
  which nothing in this app relies on.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

# Name of the Tcl procedure Tk associates with customtkinter's global
# Button-1 focus handler (CTkToplevel.__init__: ``def set_focus(event)``
# bound via bind_all).  We identify that callback by this string so we can
# remove it without touching any other app-wide bindings.
_CTK_SET_FOCUS_CMD = "set_focus"


def _neutralise_ctk_global_focus(root: tk.Misc) -> None:
    """Remove customtkinter's app-wide ``<Button-1>`` focus handler.

    customtkinter registers its ``set_focus`` callback via ``bind_all``,
    which Tk stores as a single script under the ``all`` bindtag.  We read
    that script, drop every chunk belonging to ``set_focus`` (identified by
    the generated Tcl command name ending in ``set_focus``), and write the
    remainder back — leaving any *other* app-wide Button-1 handlers intact.
    If only ``set_focus`` was bound, the binding is removed entirely.
    Idempotent: once no ``set_focus`` chunk remains, later calls do nothing.
    """
    try:
        script = root.tk.call("bind", "all", "<Button-1>")
    except tk.TclError:
        return
    if not script or _CTK_SET_FOCUS_CMD not in script:
        return          # nothing from customtkinter to neutralise

    kept = []
    removed = False
    for chunk in script.split("\n\n"):
        if not chunk.strip():
            continue
        # bind_all scripts are stored as one Tcl procedure call per binding;
        # customtkinter's looks like "...after...set_focus %# %b ...".
        first_line_tokens = chunk.strip().split("\n", 1)[0].split()
        if any(tok.endswith(_CTK_SET_FOCUS_CMD) for tok in first_line_tokens):
            removed = True
            continue
        kept.append(chunk)

    if not removed:
        return

    try:
        if kept:
            root.tk.call("bind", "all", "<Button-1>", "\n\n".join(kept) + "\n")
        else:
            root.tk.call("bind", "all", "<Button-1>", "")  # unbind entirely
    except tk.TclError:
        pass  # best-effort; worst case the old console-only error remains


class SafeDialog(ctk.CTkToplevel):
    """CTkToplevel whose windows never leak ``bad window path name`` errors.

    Subclasses simply build their UI as before; closing still happens via
    ``destroy()`` (or ``_safe_close()`` below).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Neutralise customtkinter's global click-to-focus handler, which is
        # what re-targets focus onto doomed widgets when a click closes a
        # dialog (see module docstring).
        _neutralise_ctk_global_focus(self)

    # ------------------------------------------------------------------
    # Helpers shared by the dialogs
    # ------------------------------------------------------------------
    def _safe_grab_release(self) -> None:
        try:
            if self.winfo_exists():
                self.grab_release()
        except tk.TclError:
            pass  # already gone / another grab took over

    def _safe_close(self) -> None:
        """Release the grab and destroy the dialog, tolerating races."""
        self._safe_grab_release()
        try:
            if self.winfo_exists():
                self.destroy()
        except tk.TclError:
            pass  # dialog vanished mid-call
