"""Smoke test: StatsScreen Review section with a populated coach log."""
import sys; sys.path.insert(0, "/workspace")
import customtkinter as ctk
from datetime import datetime

from app.core.models import Dataset, CoachSession, QuestionRecord, SourceRef
from app.state.app_state import AppState
from app.gui.screens.stats_screen import StatsScreen

app = ctk.CTk()
state = AppState(Dataset.empty())

ref = SourceRef(kind="note", id="n1", label="Chapter 2 notes")
q = QuestionRecord(
    id="q1", prompt="Explain photosynthesis?",
    first_answer="not sure", confidence="low", hints_seen=2,
    revisions=[{"text": "shorter", "at": None}, {"text": "again", "at": None}],
)
session = CoachSession(id="s1", sources=[ref], questions=[q],
                       updated_at_local=datetime(2026, 10, 19, 12, 0))
state.dataset.coach_sessions.append(session)

screen = StatsScreen(app, state)
cards = screen._cards
assert cards["coach_sessions"].value_label.cget("text") == "1"
assert cards["coach_answered"].value_label.cget("text") == "1/1"
assert cards["coach_hints"].value_label.cget("text") == "2"
assert cards["coach_revisions"].value_label.cget("text") == "2"

def row_labels():
    """Return the text of every weak-topic row (children of container)."""
    out = []
    for child in screen._weak_container.winfo_children():
        if child is screen._weak_header_row or not isinstance(child, ctk.CTkFrame):
            continue
        texts = []
        def walk(w):
            for c in w.winfo_children():
                try: texts.append(str(c.cget("text")))
                except Exception: pass
                walk(c)
        walk(child)
        out.append(" | ".join(texts))
    return out

rows = row_labels()
assert len(rows) == 1, rows
assert "Chapter 2 notes" in rows[0] and "weak points: 4" in rows[0], rows

# Second refresh must not duplicate rows.
screen.refresh()
rows = row_labels()
assert len(rows) == 1, rows

# Empty-state path: remove session -> placeholder label returns.
state.dataset.coach_sessions.clear()
screen.refresh()
placeholders = [c for c in screen._weak_container.winfo_children()
                if isinstance(c, ctk.CTkLabel)]
assert placeholders and "No coaching sessions yet" in placeholders[0].cget("text")
app.destroy()
print("SMOKE OK")
