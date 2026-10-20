"""Accessible quotation navigation with hover text."""
import customtkinter as ctk


class Tooltip:
    """Hover-only popup text.

    The extra explanation text is shown while the pointer rests on the widget
    and hidden as soon as the pointer leaves it (or the widget is destroyed).
    Popups are tracked at class level so any stale popup left behind by an
    earlier hover is always dismissed before a new one appears.
    """

    _popups = []   # every live popup, so none can linger after its hover ends

    def __init__(self, widget, text):
        self.widget, self.text, self.popup = widget, text, None
        widget.bind('<Enter>', self._on_enter, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<Destroy>', self.on_destroy, add='+')

    def set_text(self, text):
        """Update the hover text; hides any popup that is currently showing."""
        self.text = text
        self.hide()

    def _on_enter(self, event=None):
        # Never leave two popups alive for the same widget.
        self.hide()
        self.show()

    def show(self, event=None):
        if self.popup or not self.text:
            return
        try:
            popup = ctk.CTkToplevel(self.widget)
            popup.overrideredirect(True)
            popup.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + 30}')
            ctk.CTkLabel(popup, text=self.text, wraplength=380, justify='left').pack(padx=8, pady=5)
            # Keep the popup above the window but never steal focus/hover,
            # otherwise it can flicker or survive the pointer leaving.
            popup.transient(self.widget.winfo_toplevel())
        except Exception:
            return
        self.popup = popup
        Tooltip._popups.append(popup)

    def hide(self, event=None):
        popup, self.popup = self.popup, None
        if popup is not None:
            try:
                Tooltip._popups.remove(popup)
            except ValueError:
                pass
            try:
                popup.destroy()
            except Exception:
                pass

    def on_destroy(self, event=None):
        """Call when the owning widget is reconfigured/destroyed so its
        tooltip popup cannot outlive the hover state that created it."""
        self.hide()


class CitationChip(ctk.CTkButton):
    def __init__(self, master, quote, kind, navigate, available=True):
        label = ('note' if kind == 'note' else f"p. {quote['page']}")
        super().__init__(master, text=label + (' ✓ verified' if available else ' — source removed'),
                         width=110, height=26, command=lambda: navigate(quote),
                         state='normal' if available else 'disabled')
        self.tooltip = Tooltip(self, quote['quote'])
