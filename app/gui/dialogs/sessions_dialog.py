"""Resume or explicitly delete saved coaching sessions."""
import customtkinter as ctk
from app.gui.dialogs.confirm_dialog import ask_yes_no


class SessionsDialog(ctk.CTkToplevel):
    def __init__(self, master, state, service):
        super().__init__(master)
        self._app_state, self.service, self.result = state, service, None
        self.title('Coach sessions')
        self.geometry('650x420')
        self.transient(master.winfo_toplevel())
        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill='both', expand=True, padx=12, pady=12)
        self.rebuild()
        self.after(100, self.grab_set)

    def rebuild(self):
        for child in self.scroll.winfo_children():
            child.destroy()
        for session in sorted(self._app_state.dataset.coach_sessions, key=lambda s: s.updated_at_local, reverse=True):
            row = ctk.CTkFrame(self.scroll)
            row.pack(fill='x', pady=4)
            modes = ', '.join(sorted({t.mode for t in session.turns if t.mode == 'live'})) or 'Live'
            text = (f'{session.created_at_local:%d %b %Y} · {len(session.questions)} questions · {modes}\n'
                    + ', '.join(s.label for s in session.sources))
            ctk.CTkLabel(row, text=text, wraplength=380, justify='left').pack(side='left', padx=6)
            ctk.CTkButton(row, text='Resume', width=70,
                         command=lambda sid=session.id: self.resume(sid)).pack(side='right', padx=4)
            ctk.CTkButton(row, text='Delete', width=70,
                         command=lambda sid=session.id: self.delete(sid)).pack(side='right')

    def resume(self, sid):
        self.result = sid
        self.destroy()

    def delete(self, sid):
        if ask_yes_no(self, 'Delete coach session?', 'Delete this conversation and its review history?',
                      confirm_text='Delete', cancel_text='Cancel'):
            self.service.delete_session(sid)
            self.rebuild()
