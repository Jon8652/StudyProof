"""Text-labelled coach and learner messages."""
import customtkinter as ctk
from app.gui.theme import DETAIL_CARD_FG, ACCENT, MUTED_TEXT, PANE_BG
from app.core.agent.questions import short_title
from app.core.recap import split_recap_sections
from app.gui.widgets.tooltip import Tooltip
from app.gui.widgets.citation_chip import CitationChip
from app.core.agent.grounding import validate_quotes

# Wrap-length bounds (unscaled pixels) for the coach thread.  Only a floor
# is needed: the available width is already bounded by the real widget
# geometry via <Configure>, so an artificial ceiling could only ever strand
# the text short of the bubble when the window is maximised.
MIN_WRAPLENGTH = 240
# Small allowance for CTkLabel internal padding so the text hugs the box.
LABEL_INNER_PAD = 4


def target_wraplength(px_width: int, scaling: float) -> int:
    """Convert a Tk physical-pixel row width to an unscaled wraplength.

    Tk reports widget widths in physical pixels while CTk ``wraplength`` is
    interpreted unscaled, so divide first and only then subtract the pad.
    There is deliberately no upper cap — the label's own allocated width is
    the bound, which keeps the text flush with the box behind it at any
    window size or display-scaling factor.
    """
    logical = max(1, int(px_width / (scaling or 1.0)))
    return max(MIN_WRAPLENGTH, logical - LABEL_INNER_PAD)


class ChatBubble(ctk.CTkFrame):
    def __init__(self, master, turn, sources, index, navigate, recap_actions=None):
        # Both bubbles use DETAIL_CARD_FG, which is deliberately *lighter*
        # than the navy PANE_BG thread behind them in dark mode (and a soft
        # blue on the pale pane in light mode), so coach/user messages read
        # as raised cards.  Coach turns keep the accent border to stay
        # distinguishable from learner turns.
        super().__init__(master, corner_radius=12,
                         fg_color=DETAIL_CARD_FG,
                         border_width=1 if turn.role == 'coach' else 0, border_color=ACCENT)
        content = ctk.CTkFrame(self, fg_color='transparent')
        content.pack(fill='x', padx=12, pady=12)
        self._body_content = content
        self._wrapped_labels = []
        self.recap_buttons = []
        self.recap_cards = []

        def text_label(parent, text, *, heading=False, muted=False):
            label = ctk.CTkLabel(parent, text=text, wraplength=self._initial_wraplength(),
                                 justify='left', anchor='w')
            if heading:
                label.configure(font=ctk.CTkFont(size=13, weight='bold'))
            if muted:
                label.configure(text_color=MUTED_TEXT)
            label.pack(fill='x', pady=(8, 2) if heading else (0, 2))
            self._wrapped_labels.append(label)
            return label

        if turn.recap and recap_actions:
            def action_button(text, command, parent=content, width=140):
                button = ctk.CTkButton(parent, text=text, command=command, anchor='w', width=width)
                button.pack(anchor='w', pady=(6, 0))
                button._canvas.configure(takefocus=1)
                button.bind('<Return>', lambda event: button.invoke())
                button.bind('<space>', lambda event: button.invoke())
                self.recap_buttons.append(button)
                return button

            def loose_end_cards():
                for item in turn.recap.get('loose_ends', []):
                    card = ctk.CTkFrame(content, fg_color=PANE_BG, corner_radius=8)
                    card.pack(fill='x', pady=(6, 2))
                    inner = ctk.CTkFrame(card, fg_color='transparent')
                    inner.pack(fill='x', padx=10, pady=8)
                    title = item.get('title') or short_title(item['question'])
                    text_label(inner, title, heading=True)
                    if item.get('title') and title != short_title(item['question']):
                        text_label(inner, item['question'])
                    # Old recaps may have concatenated all selected sources.
                    # Keep their question/date but do not repeat that label.
                    caption = (item['label'] + ' · ') if item.get('title') and item.get('label') else ''
                    text_label(inner, caption + 'Asked ' + item['asked'], muted=True)
                    button = action_button('Resume', lambda link=item: recap_actions['resume'](link),
                                           parent=inner, width=90)
                    button.gate_tip = Tooltip(button, 'Resume: ' + title)
                    self.recap_cards.append(card)

            sections = split_recap_sections(turn.text)
            if sections:
                text_label(content, f"Coach: Your {turn.recap['days']}-day recap", heading=True)
                for key, title in [('effort', 'Your effort'), ('learning', 'Your learning'),
                                   ('loose_ends', 'Loose ends'), ('next_step', 'Your next step')]:
                    text_label(content, title, heading=True)
                    text_label(content, sections[key])
                    if key == 'loose_ends':
                        loose_end_cards()
            else:
                text_label(content, 'Coach: ' + turn.text)
                loose_end_cards()
            if turn.recap.get('revise'):
                action_button('Revise Weak Topics', recap_actions['revise'])
            if turn.recap.get('simplified'):
                ctk.CTkLabel(content, text='Summary assembled locally.', text_color=MUTED_TEXT).pack(anchor='w')
            if turn.recap.get('offer'):
                label = ctk.CTkLabel(content, text='Would you like a 30-day recap instead?',
                                    wraplength=self._initial_wraplength(), justify='left', anchor='w')
                label.pack(fill='x', pady=(8, 0))
                self._wrapped_labels.append(label)
                action_button('Yes — show 30 days', lambda: recap_actions['offer'](turn, True))
                action_button('No — continue studying', lambda: recap_actions['offer'](turn, False))
        else:
            text_label(content, ('Coach: ' if turn.role == 'coach' else 'You: ') + turn.text)
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
        # Bind on both the bubble itself and its inner content frame: the
        # content frame reliably reports growth, while the bubble catches
        # shrink events (a CTkFrame's child canvas does not shrink with a
        # shrinking parent until re-managed).  Both route through the same
        # idempotent handler, which compares before configuring.
        self.bind('<Configure>', self._wrap_content, add='+')
        content.bind('<Configure>', self._wrap_content, add='+')

    def _initial_wraplength(self) -> int:
        """Reasonable starting width before the first Configure event lands."""
        try:
            scaling = float(self._get_widget_scaling()) or 1.0
            # The thread is narrower than the window (sidebar, pane padding,
            # scrollbar).  Start at its real width so a long message does
            # not first appear short and then grow dramatically on Configure.
            width = self.master.winfo_width() - round(24 * scaling)
        except Exception:
            return MIN_WRAPLENGTH
        if width > 1:
            return target_wraplength(width, scaling)
        return MIN_WRAPLENGTH

    def _wrap_content(self, event):
        # Track the configured widget's own row width so the text always
        # fills the box behind it — no upper cap, only the minimum floor.
        try:
            scaling = float(self._get_widget_scaling()) or 1.0
        except Exception:
            scaling = 1.0
        for label in self._wrapped_labels:
            try:
                pixels = event.width if label.master is self._body_content else label.winfo_width()
                width = target_wraplength(max(1, pixels), scaling)
                if label.winfo_exists() and label.cget('wraplength') != width:
                    label.configure(wraplength=width)
            except Exception:
                pass                      # widget torn down mid-resize
