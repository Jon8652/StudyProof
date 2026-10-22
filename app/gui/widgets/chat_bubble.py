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
        content = ctk.CTkFrame(self, fg_color='transparent')
        content.pack(fill='x', padx=12, pady=12)
        self._wrapped_labels = []
        message = ctk.CTkLabel(content, text=('Coach: ' if turn.role == 'coach' else 'You: ')
                              + turn.text, wraplength=650, justify='left', anchor='w')
        message.pack(fill='x')
        self._wrapped_labels.append(message)
        for quote in turn.quotes:
            source = next((s for s in sources if s.id == quote['source_id']), None)
            available = bool(validate_quotes([quote], index)[0])
            CitationChip(content, quote, source.kind if source else 'document', navigate,
                         available).pack(anchor='w', pady=(6, 0))
        if turn.ungrounded_count:
            notice = ctk.CTkLabel(content,
                                  text=f'{turn.ungrounded_count} quotations could not be verified and were removed.',
                                  text_color=MUTED_TEXT, wraplength=650,
                                  justify='left', anchor='w')
            notice.pack(fill='x', pady=(6, 0))
            self._wrapped_labels.append(notice)
        content.bind('<Configure>', self._wrap_content, add='+')

    def _wrap_content(self, event):
        # Tk reports physical pixels; CTk wrap lengths use unscaled dimensions.
        width = max(1, int(event.width / self._get_widget_scaling()))
        for label in self._wrapped_labels:
            if label.cget('wraplength') != width:
                label.configure(wraplength=width)
