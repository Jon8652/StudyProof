"""Text-labelled coach and learner messages."""
import customtkinter as ctk
from app.gui.theme import CARD_FG, DETAIL_CARD_FG, ACCENT, MUTED_TEXT
from app.gui.widgets.citation_chip import CitationChip
from app.core.agent.grounding import validate_quotes

# Wrap-length bounds (unscaled pixels) for the coach thread.  A narrow /
# windowed layout must not wrap at a width larger than the bubble itself,
# and a maximised window should use the extra room instead of leaving the
# old hard-coded 650px column stranded in the middle of a wide screen.
MIN_WRAPLENGTH = 240
MAX_WRAPLENGTH = 900


class ChatBubble(ctk.CTkFrame):
    def __init__(self, master, turn, sources, index, navigate):
        super().__init__(master, corner_radius=12,
                         fg_color=CARD_FG if turn.role == 'coach' else DETAIL_CARD_FG,
                         border_width=1 if turn.role == 'coach' else 0, border_color=ACCENT)
        content = ctk.CTkFrame(self, fg_color='transparent')
        content.pack(fill='x', padx=12, pady=12)
        self._wrapped_labels = []
        message = ctk.CTkLabel(content, text=('Coach: ' if turn.role == 'coach' else 'You: ')
                              + turn.text, wraplength=self._initial_wraplength(),
                              justify='left', anchor='w')
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
                                  text_color=MUTED_TEXT, wraplength=self._initial_wraplength(),
                                  justify='left', anchor='w')
            notice.pack(fill='x', pady=(6, 0))
            self._wrapped_labels.append(notice)
        content.bind('<Configure>', self._wrap_content, add='+')

    def _initial_wraplength(self) -> int:
        """Reasonable starting width before the first Configure event lands."""
        try:
            toplevel_width = self.winfo_toplevel().winfo_width()
        except Exception:
            toplevel_width = 0
        if toplevel_width and toplevel_width > 1:
            return self._clamped(toplevel_width - 120)
        return 650   # legacy default while the window is still unmeasured

    def _clamped(self, width: int) -> int:
        return max(MIN_WRAPLENGTH, min(MAX_WRAPLENGTH, int(width)))

    def _wrap_content(self, event):
        # Tk reports physical pixels; CTk wrap lengths use unscaled dimensions.
        # Clamp so the text follows the bubble when the window is small and
        # grows with it when the window is opened wide.
        width = max(1, int(event.width / self._get_widget_scaling()))
        width = self._clamped(width)
        for label in self._wrapped_labels:
            if label.cget('wraplength') != width:
                label.configure(wraplength=width)
