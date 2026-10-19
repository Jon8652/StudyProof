"""Text-labelled coach and learner messages."""
import customtkinter as ctk
from app.gui.theme import CARD_FG, DETAIL_CARD_FG, ACCENT, MUTED_TEXT
from app.gui.widgets.citation_chip import CitationChip
from app.core.agent.grounding import validate_quotes


class ChatBubble(ctk.CTkFrame):
    def __init__(self, master, turn, sources, index, navigate):
        super().__init__(master, corner_radius=12,
                         fg_color=CARD_FG if turn.role == 'coach' else DETAIL_CARD_FG,
                         border_width=1 if turn.role == 'coach' else 0, border_color=ACCENT)
        ctk.CTkLabel(self, text=('Coach: ' if turn.role == 'coach' else 'You: ')
                     + turn.text, wraplength=650, justify='left', anchor='w').pack(
                         fill='x', padx=12, pady=8)
        if turn.mode:
            ctk.CTkLabel(self, text=turn.mode.title(), text_color=MUTED_TEXT).pack(anchor='w', padx=12)
        if turn.kind in ('hint', 'feedback') and not turn.quotes and sources:
            ctk.CTkLabel(self, text='General guidance', text_color=MUTED_TEXT).pack(anchor='w', padx=12)
        for quote in turn.quotes:
            source = next((s for s in sources if s.id == quote['source_id']), None)
            available = bool(validate_quotes([quote], index)[0])
            CitationChip(self, quote, source.kind if source else 'document', navigate,
                         available).pack(anchor='w', padx=12, pady=3)
        if turn.ungrounded_count:
            ctk.CTkLabel(self, text=f'{turn.ungrounded_count} quotations could not be verified and were removed.',
                         text_color=MUTED_TEXT).pack(anchor='w', padx=12)
