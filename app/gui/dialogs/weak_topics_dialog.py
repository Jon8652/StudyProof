"""Choose a specific unresolved topic to practise, without exposing ratings."""
import customtkinter as ctk

from app.gui.dialogs._dialog_base import SafeDialog


class WeakTopicsDialog(SafeDialog):
    def __init__(self, master, state):
        super().__init__(master)
        self.result = None
        self._app_state = state
        self.title('Revise Weak Topics')
        self.geometry('720x460')
        self.transient(master.winfo_toplevel())
        self.scroll = ctk.CTkScrollableFrame(self)
        self.rebuild()
        self.after(100, self.grab_set)

    def rebuild(self):
        state, scroll = self._app_state, self.scroll
        for child in scroll.winfo_children():
            child.destroy()
        scroll.pack(fill='both', expand=True, padx=12, pady=12)
        topics = sorted(state.dataset.weak_topics, key=lambda w: w.updated_at, reverse=True)
        if not topics:
            ctk.CTkLabel(scroll, text='No weak topics to revise yet.\nTopics will appear here as you study.').pack(pady=25)
        for topic in topics:
            row = ctk.CTkFrame(scroll)
            row.pack(fill='x', pady=5)
            available = state.source_ref_for(topic.source.kind, topic.source.id) is not None
            text = f'{topic.topic}\n{topic.source.label}\n{topic.question}'
            if not available:
                text += '\nSource removed — restore it to revise this topic.'
            ctk.CTkLabel(row, text=text, wraplength=490, justify='left').pack(side='left', padx=10, pady=8)
            ctk.CTkButton(row, text='Revise', width=90,
                          state='normal' if available else 'disabled',
                          command=lambda tid=topic.id: self.choose(tid)).pack(side='right', padx=8)

    def choose(self, topic_id):
        self.result = topic_id
        self.destroy()
