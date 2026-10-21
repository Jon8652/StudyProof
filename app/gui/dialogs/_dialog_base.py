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

The fix here is structural: :func:`_neutralise_ctk_global_focus` rewrites the
``all`` bindtag's ``<Button-1>`` script so the library's focus handler only
runs for genuinely focusable widget classes (Entry, Text, buttons, etc.) and
is skipped for labels/canvases/frames.  Every dialog derives from
:class:`SafeDialog`, which applies the rewrite on construction; the main
window applies it too (see ``app.gui.main_window``).  Normal keyboard focus
behaviour is unchanged: entries and text boxes still receive focus on click.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

# Name of the Tcl procedure Tk associates with customtkinter's global
# Button-1 focus handler (CTkToplevel.__init__: ``def set_focus(event)``
# bound via bind_all).  We identify that callback by this string so we can
# neutralise it without touching any other app-wide bindings.
_CTK_SET_FOCUS_CMD = "set_focus"

# Marker variable guarding one-time definition of the wrapper proc.
_PATCH_FLAG = "::ctk_safe_focus_patched"


def _neutralise_ctk_global_focus(root: tk.Misc) -> None:
    """Make customtkinter's app-wide ``<Button-1>`` focus handler safe.

    Reads the current ``bind all <Button-1>`` Tcl script, locates the
    interpreter command id of customtkinter's ``set_focus`` callback and
    wraps it in ``_ctk_safe_focus_cb``, which skips the deferred
    ``focus_set()`` whenever the clicked widget's class bindtag denotes a
    non-focusable widget (Label / Canvas / Frame / Image / Toplevel).  All
    other app-wide handlers (e.g. CTkScrollableFrame's mouse-wheel and Shift
    bindings) are preserved verbatim.  Idempotent.
    """
    try:
        script = root.tk.call("bind", "all", "<Button-1>")
    except tk.TclError:
        return
    if not script or _CTK_SET_FOCUS_CMD not in script:
        return          # nothing from customtkinter to neutralise
    if "_ctk_safe_focus_cb" in script:
        return          # already patched

    parts = []
    found = False
    for chunk in script.split("\n\n"):
        if not chunk.strip():
            continue
        cmd_id = None
        if _CTK_SET_FOCUS_CMD in chunk:
            for token in chunk.replace("\n", " ").split():
                if token.endswith(_CTK_SET_FOCUS_CMD):
                    cmd_id = token[: -len(_CTK_SET_FOCUS_CMD)]
                    break
        if cmd_id is None:
            parts.append(chunk)
            continue
        found = True
        inner = (f"{cmd_id} %# %b %f %h %k %s %t %w %x %y %A %E %K %N "
                 f"%W %T %X %Y %D")
        parts.append("_ctk_safe_focus_cb {" + inner + "}")

    if not found:
        return

    preamble = "\n".join([
        f"if {{![info exists {_PATCH_FLAG}]}} {{",
        f"    set {_PATCH_FLAG} 1",
        "    proc _ctk_safe_focus_cb {{cb args}} {{",
        "        if {{[catch {{lindex [bindtags %W] 1}} cls]}} {{return}}",
        "        if {{$cls eq \"Label\" || $cls eq \"Canvas\" || "
        "$cls eq \"Frame\" || $cls eq \"Image\" || $cls eq \"Toplevel\"}} "
        "{{return}}",
        "        eval $cb $args",
        "    }}",
        "}}",
    ])
    patched = preamble + "\n" + "\n\n".join(parts) + "\n"
    try:
        root.tk.call("bind", "all", "<Button-1>", patched)
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
