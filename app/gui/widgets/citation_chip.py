"""Accessible quotation navigation with hover text."""
import customtkinter as ctk

from app.gui.widgets.tooltip import Tooltip

__all__ = ['Tooltip', 'CitationChip']


class CitationChip(ctk.CTkButton):
    def __init__(self, master, quote, kind, navigate, available=True):
        label = ('note' if kind == 'note' else f"p. {quote['page']}")
        super().__init__(master, text=label + (' ✓ verified' if available else ' — source removed'),
                         width=110, height=26, command=lambda: navigate(quote),
                         state='normal' if available else 'disabled')
        self.tooltip = Tooltip(self, quote['quote'])
