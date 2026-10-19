"""Accessible quotation navigation with hover text."""
import customtkinter as ctk


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.popup = widget, text, None
        self._watch = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def show(self, event=None):
        if self.popup or not self.text:
            return
        self.popup = ctk.CTkToplevel(self.widget)
        self.popup.overrideredirect(True)
        self.popup.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+6}')
        ctk.CTkLabel(self.popup, text=self.text, wraplength=380, justify='left').pack(padx=8, pady=5)
        self._watch = self.widget.after(50, self._check_pointer)

    def _check_pointer(self):
        self._watch = None
        x, y = self.widget.winfo_pointerxy()
        left, top = self.widget.winfo_rootx(), self.widget.winfo_rooty()
        if not (left <= x < left + self.widget.winfo_width()
                and top <= y < top + self.widget.winfo_height()):
            self.hide()
        elif self.popup:
            self._watch = self.widget.after(50, self._check_pointer)

    def hide(self, event=None):
        if self._watch is not None:
            self.widget.after_cancel(self._watch)
            self._watch = None
        if self.popup:
            self.popup.destroy()
            self.popup = None


class CitationChip(ctk.CTkButton):
    def __init__(self, master, quote, kind, navigate, available=True):
        label = ('note' if kind == 'note' else f"p. {quote['page']}")
        super().__init__(master, text=label + (' ✓ verified' if available else ' — source removed'),
                         width=110, height=26, command=lambda: navigate(quote),
                         state='normal' if available else 'disabled')
        self.tooltip = Tooltip(self, quote['quote'])
