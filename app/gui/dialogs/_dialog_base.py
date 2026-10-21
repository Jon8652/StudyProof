"""Shared base class for the app's modal dialogs.

Why this exists
---------------
customtkinter's ``CTkToplevel.__init__`` installs an application-wide binding::

    self.bind_all("<Button-1>", set_focus, add=True)
    # where set_focus does:  if hasattr(event.widget, "focus_set"):
    #                         event.widget.focus_set()

That handler fires *before* any widget-level bindings (bind_all tags sit ahead
of the widget tag in Tk's bindtag order).  So when you click a dialog button
such as "Start session", customtkinter first calls ``focus_set()`` on the
button's internal ``tkinter.Label`` child — and only afterwards runs the
button command.  If that command synchronously destroys the dialog (submit
closes it, OK closes the message box, ...), the queued Tk focus operation can
still be processed against the now-dead label window, raising::

    _tkinter.TclError: bad window path name ".!customsessiondialog.!ctkframe...!label"

Tkinter prints "Exception in Tkinter callback" to the console even though the
app itself behaves fine.  Targeted ``winfo_exists()`` guards cannot catch
this because the offending call lives inside customtkinter/library code, not
in our callbacks.

The fix here is structural: every dialog we create derives from
:class:`SafeDialog`, which overrides ``bind_all`` with a version that wraps
any ``<Button-1>`` handler so the deferred ``focus_set`` is skipped whenever
the event widget no longer exists.  This neutralises the crash at its source
for all current and future dialogs, without changing normal focus behaviour.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


def _wrap_button_press_handler(func):
    """Return *func* wrapped so it only runs while ``event.widget`` is alive."""
    def wrapper(event):
        widget = getattr(event, "widget", None)
        if widget is None:
            return
        try:
            # Raises TclError once the widget has been destroyed.
            widget.winfo_exists()
        except tk.TclError:
            return  # dead widget - skip the library's deferred focus_set
        return func(event)

    # Expose the original so unbind() lookups keep working if ever needed.
    wrapper.__wrapped__ = func
    return wrapper


class SafeDialog(ctk.CTkToplevel):
    """CTkToplevel whose windows never leak ``bad window path name`` errors.

    Subclasses simply build their UI as before; closing still happens via
    ``destroy()`` (or ``_safe_close()`` below).
    """

    def bind_all(self, sequence=None, func=None, add=None):
        if func is not None and sequence and "Button-1" in str(sequence):
            func = _wrap_button_press_handler(func)
        return super().bind_all(sequence, func, add)

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
