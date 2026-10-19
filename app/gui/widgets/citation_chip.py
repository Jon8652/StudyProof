"""Accessible quotation navigation with hover text."""
import customtkinter as ctk


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.popup = widget, text, None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def show(self, event=None):
        if self.popup or not self.text:
            return
        self.popup = ctk.CTkToplevel(self.widget)
        self.popup.overrideredirect(True)
        self.popup.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+30}')
        ctk.CTkLabel(self.popup, text=self.text, wraplength=380, justify='left').pack(padx=8, pady=5)

    def hide(self, event=None):
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
