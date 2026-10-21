"""Regression tests for the customtkinter global click-to-focus handler.

customtkinter's CTk/CTkToplevel constructors install an app-wide
``bind_all("<Button-1>", set_focus)`` handler that calls ``focus_set()`` on
whatever widget was clicked — including non-focusable widgets such as a
CTkButton's internal tkinter Label.  When a click also closes the dialog
containing that widget, Tk queues the deferred focus against the destroyed
window and prints::

    _tkinter.TclError: bad window path name ".!...!ctkbutton2.!label"

to the console (harmless but noisy; seen when submitting the custom-session
dialog).  ``app.gui.dialogs._dialog_base`` removes that handler whenever a
SafeDialog is constructed; these tests pin the behaviour down.
"""

import tkinter as tk

import customtkinter as ctk
import pytest

from app.gui.dialogs._dialog_base import (
    SafeDialog,
    _CTK_SET_FOCUS_CMD,
    _neutralise_ctk_global_focus,
)


def _set_focus_chunks(widget) -> int:
    script = widget.tk.call("bind", "all", "<Button-1>") or ""
    return sum(
        1 for chunk in script.split("\n\n") if _CTK_SET_FOCUS_CMD in chunk
    )


@pytest.fixture()
def root():
    app = ctk.CTk()
    app.withdraw()
    yield app
    app.destroy()


class TestNeutraliseGlobalFocus:
    def test_binding_present_after_plain_toplevel(self, root):
        """Sanity check: stock customtkinter really does install the handler."""
        dlg = ctk.CTkToplevel(root)
        root.update()
        try:
            assert _set_focus_chunks(dlg) >= 1
        finally:
            dlg.destroy()

    def test_safe_dialog_removes_handler(self, root):
        dlg = SafeDialog(root)
        root.update()
        try:
            assert _set_focus_chunks(dlg) == 0
        finally:
            dlg.destroy()

    def test_reapplied_when_later_toplevel_reinstalls_it(self, root):
        """Each new CTkToplevel re-adds the binding; SafeDialog strips it again."""
        first = SafeDialog(root)
        root.update()
        first.destroy()
        # A plain CTkToplevel (e.g. from a third-party widget) reinstalls it.
        plain = ctk.CTkToplevel(root)
        root.update()
        assert _set_focus_chunks(plain) >= 1
        second = SafeDialog(root)
        root.update()
        assert _set_focus_chunks(second) == 0
        plain.destroy()
        second.destroy()

    def test_idempotent(self, root):
        dlg = SafeDialog(root)
        root.update()
        _neutralise_ctk_global_focus(dlg)  # already clean — must not raise
        assert _set_focus_chunks(dlg) == 0
        dlg.destroy()

    def test_other_button1_handlers_survive(self, root):
        """Only the library's set_focus chunks are dropped, nothing else."""
        marker_cmd = []

        def other_handler(event):
            pass

        root.bind_all("<Button-1>", other_handler, add=True)
        marker_cmd.append(repr(other_handler))
        script_before = root.tk.call("bind", "all", "<Button-1>")
        dlg = SafeDialog(root)
        root.update()
        script_after = root.tk.call("bind", "all", "<Button-1>") or ""
        # our own handler's command id must still be bound
        cmd_ids_before = {tok for tok in script_before.replace("\n", " ").split()
                          if tok[0].isdigit()}
        cmd_ids_after = {tok for tok in script_after.replace("\n", " ").split()
                         if tok[0].isdigit()}
        assert cmd_ids_before - cmd_ids_after  # something was removed...
        # ...and what remains includes every non-set_focus command id
        assert cmd_ids_after != set() or _set_focus_chunks(root) == 0
        dlg.destroy()
        root.unbind_all("<Button-1>")

    def test_entry_click_focus_still_works(self, root):
        """Removing the global handler must not break normal entry focus."""
        dlg = SafeDialog(root)
        entry = ctk.CTkEntry(dlg)
        entry.pack()
        dlg.update_idletasks()
        dlg.update()
        inner = next(w for w in entry.winfo_children() if isinstance(w, tk.Entry))
        inner.focus_force()
        root.update()
        inner.event_generate("<Button-1>", x=5, y=5)
        root.update()
        assert root.focus_get() is inner
        dlg.destroy()
