"""Centralised visual theme tokens for the Study Tracker UI.

Every colour used by the GUI lives here so screens/dialogs/widgets stay
visually consistent across light *and* dark mode.  Previously several
buttons were hard-coded to ``gray30``/``gray25`` pairs, which are nearly
invisible on a light-mode background; the neutral tokens below are proper
(light, dark) tuples resolved by customtkinter per appearance mode.

Colour conventions
------------------
- ACCENT      – primary blue used for main actions, active nav pills,
                today's calendar tile and the "Today" header button.
- NEUTRAL     – quieter secondary actions (Cancel / Close / Open day …).
- DANGER      – destructive actions (Delete session / Delete note).
- CARD_FG / BORDER – elevated panel look: subtle border around every card
                so panels read as distinct surfaces on both themes.
"""

from __future__ import annotations

import customtkinter as ctk

# --- Accent ------------------------------------------------------------------
ACCENT = "#2b6cb0"            # blue, identical in light + dark modes
ACCENT_HOVER = "#255a94"
ACCENT_TEXT = "white"

# Secondary accents used for coloured stat values on the Home screen.
TIME_BLUE = ("#22589d", "#6ea8e0")
XP_GOLD = ("#a6821f", "#d4af37")

# --- Neutral / semantic buttons ----------------------------------------------
# NOTE: customtkinter accepts either a plain colour string or a *flat* tuple of
# exactly two strings – one per appearance mode (light, dark).  Nesting pairs
# inside the tuple (e.g. (("a","b"),("c","d"))) raises ValueError at widget
# construction time.
NEUTRAL = ("#dfe4ea", "gray28")
NEUTRAL_HOVER = ("#d3d9e0", "gray35")
NEUTRAL_TEXT = ("gray10", "gray92")
DANGER = ("#8b3030", "#b04040")
DANGER_HOVER = ("#a33d3d", "#c44e4e")

# --- Surfaces ------------------------------------------------------------------
# Light mode uses a coherent *blue* palette end-to-end: the window background
# itself is a very pale blue (no neutral greys anywhere), panels are a slightly
# deeper pale blue, and the sidebar/chips step progressively darker.  Dark mode
# mirrors this with deep navy surfaces.
WINDOW_BG = ("#eef4fb", "#15151f")
CARD_FG = ("#f6f9fd", "#242430")   # elevated panel surface (pale blue in light)
BORDER = ("#d6e2ef", "#3f3f50")    # subtle blue-tinted outline that separates cards
NAV_ACTIVE_BG = ("#dbe7f5", "#2b6cb0")
NAV_HOVER = ("#e6eef7", "#33333d")
SIDEBAR_FG = ("#e3ecf6", "#1d1d28")
MUTED_TEXT = ("gray40", "gray65")
# High-contrast body text for content shown on card surfaces (e.g. the session
# rows in the day-details dialog).  Light mode keeps near-black; dark mode uses
# white so the text stays readable instead of the default light grey.
PRIMARY_TEXT = ("gray10", "white")

# --- Screen spacing ------------------------------------------------------------
# Uniform inset between the window/screen edge and everything on a screen
# (titles, cards, buttons, text).  Every screen — Statistics included —
# uses SCREEN_PAD_X so the left gap is identical for all boxes, buttons
# and labels.  The same value is applied on BOTH sides of the content
# area (base_screen packs with padx=SCREEN_PAD_X), so the left and right
# edge gaps are equal.  Reduced to half its original size (was 24 px).
SCREEN_PAD_X = 12

# Inner horizontal padding used inside bordered cards/panes (headings,
# body text, buttons).  Kept equal to SCREEN_PAD_X so the gap between a
# card's border and its contents matches the gap between the screen edge
# and the card itself — uniform within and among all screens.  Cards are
# packed flush against the screen's padded content frame, so their outer
# left/right gaps come from SCREEN_PAD_X automatically.
CARD_PAD_X = SCREEN_PAD_X

# --- Notes screen surfaces -----------------------------------------------------
# The right-hand detail card sits on a soft blue (a slightly deeper tint of the
# sidebar blue) instead of near-white grey, so it harmonises with the light-blue
# sidebar in light mode and stays cohesive in dark mode.
DETAIL_CARD_FG = ("#e7eff9", "#232331")
# Unselected note-row fill: matches the detail card so the two panes read as one
# surface; selection is still conveyed by the accent border + ACCENT title.
NOTE_ROW_BG = ("#e7eff9", "#242430")

# --- "All notes" header chip ---------------------------------------------------
# CTkScrollableFrame's default label chip is grey (#c4c9ce / #3a3d41), which
# clashes with the blue accents used everywhere else.  These tints sit closer to
# the app palette: lighter/softer blue in light mode, darker in dark mode.
ALL_NOTES_CHIP_LIGHT = ("#dbe7f5", "#1e2b3f")
ALL_NOTES_CHIP_TEXT = ("#1a3a5c", "#cfe0f2")


def card(master=None, corner_radius: int = 14, **kwargs):
    """Return a themed CTkFrame styled as an elevated card."""
    return ctk.CTkFrame(
        master,
        corner_radius=corner_radius,
        fg_color=CARD_FG,
        border_width=1,
        border_color=BORDER,
        **kwargs,
    )


def neutral_button(master=None, text: str = "", width: int = 100,
                   height: int = 28, command=None, **kwargs):
    """A quiet secondary action button (Cancel / Close / Open day …)."""
    return ctk.CTkButton(
        master,
        text=text,
        width=width,
        height=height,
        fg_color=NEUTRAL,
        hover_color=NEUTRAL_HOVER,
        text_color=NEUTRAL_TEXT,
        command=command,
        **kwargs,
    )


def danger_button(master=None, text: str = "Delete", width: int = 90,
                  height: int = 28, command=None, **kwargs):
    """A red destructive-action button."""
    return ctk.CTkButton(
        master,
        text=text,
        width=width,
        height=height,
        fg_color=DANGER,
        hover_color=DANGER_HOVER,
        text_color="white",
        command=command,
        **kwargs,
    )
