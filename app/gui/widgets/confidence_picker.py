"""Three fixed confidence choices, read-only once captured."""
import customtkinter as ctk


class ConfidencePicker(ctk.CTkFrame):
    def __init__(self, master, command):
        super().__init__(master, fg_color='transparent')
        self.buttons = {}
        for key, label in [('low', 'Guessed'), ('medium', 'Unsure'), ('high', 'Confident')]:
            button = ctk.CTkButton(self, text=label, width=105, command=lambda k=key: command(k))
            button.pack(side='left', padx=3)
            self.buttons[key] = button
        self.label = ctk.CTkLabel(self, text='')
        self.label.pack(side='left', padx=8)

    def update_question(self, question, busy=False):
        for key, button in self.buttons.items():
            button.configure(state='normal' if question and question.first_answer is not None
                             and question.confidence is None and not busy else 'disabled')
        self.label.configure(text=('Confidence: ' + question.confidence if question and question.confidence
                                   else 'Answer first, then rate confidence.'))
