"""Hover-only tooltip popups (single shared window, no per-hover toplevels).

The old implementation created one ``CTkToplevel`` per hover.  On Windows a
top-level that is destroyed while it is still mapped can make Tk crash the
whole process with no Python traceback — exactly the "app closes itself after
hovering many times" symptom — and popups could also linger when a ``<Leave>``
event was dropped (disabled widgets, rapid pointer motion, screen re-layout).

This module replaces that design:

* One lazily created, withdrawn-by-default Toplevel is **shared** by every
  tooltip in the app.  Showing a tip only configures its label and warps it
  over the pointer; hiding a tip withdraws it.  Tooltips therefore never
  create or destroy windows during normal hovering at all.
* Visibility is confirmed by polling the real pointer position on a short
  ``after()`` timer instead of trusting ``<Enter>/<Leave>`` events alone, so
  a popup can never survive the mouse moving away — even if Tk drops an
  event, disables the widget mid-hover, or re-layouts underneath it.
* Every callback checks liveness first and swallows TclErrors, so nothing
  can raise out of a Tk event handler once widgets are gone.
"""

import time
import tkinter as tk

from app.gui.theme import CARD_FG

_HOVER_DELAY_MS = 350     # rest time before a tip appears
_POLL_MS = 120            # how often the pointer is re-checked while visible
_WRAPLENGTH = 460
# Hover text is intentionally a touch larger than the pre-refactor CTkLabel
# popup default (13) for readability; the wraplength grows with it so lines
# stay roughly the same width.
_FONT_FAMILY = 'Roboto'
_FONT_SIZE = 16

# The tooltip window is created once and reused forever.  On Windows Tk has
# historically crashed when a mapped top-level is destroyed during an event
# callback, so we never destroy it — only withdraw it.  (The old per-hover
# CTkToplevel design hit exactly that path and killed the app silently.)
_LIGHT_BG, _DARK_BG = CARD_FG


def _popup_colors():
    try:
        import customtkinter
        dark = customtkinter.get_appearance_mode().lower().startswith('dark')
    except Exception:
        dark = True
    return (_DARK_BG if dark else _LIGHT_BG), ('white' if dark else 'gray10')


class Tooltip:
    """Hover-only explanation text attached to one widget."""

    _shared_window = None      # the single popup window (created on demand)
    _shared_label = None       # its label
    _active = None             # Tooltip instance whose text is currently shown
    _instances = []            # live instances (weakrefs would do; list is fine)
    _poll_job = None           # pending after() id for the pointer poll

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._shown_at = None          # monotonic time the tip became visible
        self._enter_job = None         # pending show-after-delay timer
        Tooltip._instances.append(self)
        widget.bind('<Enter>', self._on_enter, add='+')
        widget.bind('<Leave>', self._on_leave, add='+')
        widget.bind('<Destroy>', self._on_destroy, add='+')

    # -- public API -----------------------------------------------------------

    def set_text(self, text):
        """Update the hover text; hides the popup if this tip was showing."""
        self.text = text
        if Tooltip._active is self:
            self._deactivate()

    def hide(self, event=None):
        """Dismiss this tooltip (kept for callers that treat it per-widget)."""
        if Tooltip._active is self:
            self._deactivate()

    @classmethod
    def dismiss_all(cls):
        """Hide any visible tip; call before re-laying out the UI."""
        cls._cancel_poll()
        cls._hide_window()
        cls._active = None
        for tip in list(cls._instances):
            tip._cancel_enter()

    # -- hover plumbing ---------------------------------------------------------

    def _alive(self):
        try:
            return bool(self.widget.winfo_exists())
        except tk.TclError:
            return False

    def _on_enter(self, event=None):
        if not self.text or not self._alive():
            return
        self._cancel_enter()
        try:
            self._enter_job = self.widget.after(_HOVER_DELAY_MS, self._show_now)
        except tk.TclError:
            pass

    def _on_leave(self, event=None):
        self._cancel_enter()
        # Hide on leave *regardless* of which tip the shared window currently
        # shows.  A <Leave> for this widget always means "this explanation is
        # no longer wanted"; only trusting the active-tip match let popups
        # linger whenever a stale Enter had armed this tooltip while another
        # one owned the window.
        self._deactivate()

    def _on_destroy(self, event=None):
        self._cancel_enter()
        self._deactivate()
        try:
            Tooltip._instances.remove(self)
        except ValueError:
            pass

    def _cancel_enter(self):
        if self._enter_job is not None:
            try:
                if self._alive():
                    self.widget.after_cancel(self._enter_job)
            except tk.TclError:
                pass
            self._enter_job = None

    # -- showing / hiding --------------------------------------------------------

    def _show_now(self):
        self._enter_job = None
        if not self.text or not self._alive():
            return
        # Only show while the pointer actually still rests on the widget —
        # never pop up for a stale Enter whose matching Leave was dropped.
        try:
            under = self.widget.winfo_containing(*self.widget.winfo_pointerxy())
        except tk.TclError:
            return
        if not self._is_over(under) or not self.widget.winfo_viewable():
            return
        # A different widget may have taken over while we were waiting.
        if Tooltip._active is not None and Tooltip._active is not self:
            Tooltip._active._deactivate()
        Tooltip._active = self
        self._shown_at = time.monotonic()
        try:
            window = self._ensure_window()
            bg, fg = _popup_colors()
            Tooltip._shared_label.configure(text=self.text, bg=bg, fg=fg)
            window.configure(bg=bg)
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            window.geometry(f'+{x}+{y}')
            window.deiconify()
            window.lift()
            self._schedule_poll()
        except tk.TclError:
            Tooltip._active = None

    def _is_over(self, under):
        """True when *under* (the widget containing the pointer) is this
        tooltip's widget or one of its descendants.  CTk widgets draw on
        inner canvases/labels, so descendant matching is required."""
        widget = self.widget
        while under is not None:
            if under is widget:
                return True
            under = getattr(under, 'master', None)
        return False

    def _deactivate(self):
        if Tooltip._active is self:
            Tooltip._active = None
        Tooltip._cancel_poll()
        Tooltip._hide_window()

    @classmethod
    def _ensure_window(cls):
        window = cls._shared_window
        if window is not None:
            try:
                if window.winfo_exists():
                    return window
            except tk.TclError:
                cls._shared_window = cls._shared_label = None
        # Built from any live widget's toplevel; kept withdrawn until needed.
        master = None
        for tip in cls._instances:
            try:
                if tip.widget.winfo_exists():
                    master = tip.widget.winfo_toplevel()
                    break
            except tk.TclError:
                continue
        if master is None:
            raise tk.TclError('no live toplevel for tooltip')
        window = tk.Toplevel(master)
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        try:
            window.transient(master)
        except tk.TclError:
            pass
        label = tk.Label(window, text='', justify='left', wraplength=_WRAPLENGTH,
                         font=(_FONT_FAMILY, _FONT_SIZE),
                         bg=CARD_FG[1], fg='white', padx=8, pady=5,
                         highlightthickness=1, highlightbackground='gray30')
        label.pack()
        cls._shared_window, cls._shared_label = window, label
        window.withdraw()
        return window

    @classmethod
    def _hide_window(cls):
        """Withdraw the shared popup.  It is *never* destroyed during hover —
        destroying a mapped top-level inside an event callback is what crashed
        Tk on Windows; withdraw/deiconify of one persistent window is safe."""
        window = cls._shared_window
        if window is None:
            return
        try:
            if window.winfo_exists():
                window.withdraw()
        except tk.TclError:
            pass

    @classmethod
    def _schedule_poll(cls):
        cls._cancel_poll()
        window = cls._shared_window
        if window is None:
            return
        try:
            cls._poll_job = window.after(_POLL_MS, cls._poll_pointer)
        except tk.TclError:
            cls._poll_job = None

    @classmethod
    def _cancel_poll(cls):
        if cls._poll_job is not None:
            window = cls._shared_window
            try:
                if window is not None and window.winfo_exists():
                    window.after_cancel(cls._poll_job)
            except tk.TclError:
                pass
            cls._poll_job = None

    @classmethod
    def _poll_pointer(cls):
        """Ground truth check: keep the tip only while the pointer rests on
        the owning widget."""
        cls._poll_job = None
        active = cls._active
        if active is None or not active._alive():
            cls._hide_window()
            cls._active = None
            return
        widget = active.widget
        try:
            under = widget.winfo_containing(*widget.winfo_pointerxy())
            viewable = widget.winfo_viewable()
        except tk.TclError:
            under, viewable = None, 0
        if viewable and active._is_over(under):
            cls._schedule_poll()
            return
        cls._active = None
        cls._hide_window()
