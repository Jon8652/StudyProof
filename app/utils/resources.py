"""Centralized asset loading helper (Part J).

All GUI code loads images through this module so asset path logic is never
duplicated across UI files. Missing assets must never crash the app; callers
receive ``None`` and fall back to text labels (FR-79, ERR-12).
"""

from __future__ import annotations

from pathlib import Path

from app.utils.paths import get_asset_path

# Cache loaded images so tkinter references stay alive (prevents GC issues)
_IMAGE_CACHE: dict[str, object] = {}

ASSET_FILENAMES = {
    "app_icon": "app_icon.png",
    "home_icon": "home_icon.png",
    "calendar_icon": "calendar_icon.png",
    "stats_icon": "stats_icon.png",
    "settings_icon": "settings_icon.png",
    "notes_icon": "notes_icon.png",
    "bronze_medal": "bronze_medal.png",
    "silver_medal": "silver_medal.png",
    "gold_medal": "gold_medal.png",
    "empty_state": "empty_state.png",
}


def asset_exists(key: str) -> bool:
    """Return True if the asset file for ``key`` exists on disk."""
    filename = ASSET_FILENAMES.get(key)
    if filename is None:
        return False
    return get_asset_path(filename).is_file()


def export_ico(png_filename: str, ico_path) -> bool:
    """Convert a bundled PNG into a multi-size .ico (used at build time).

    User artwork may be any size/mode; Pillow derives all standard icon
    sizes from the source image. Returns True on success.
    """
    from pathlib import Path as _Path

    from PIL import Image

    src = get_asset_path(png_filename)
    if not src.is_file():
        return False
    try:
        with Image.open(src) as img:
            img.load()
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(_Path(ico_path), format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return True
    except Exception:
        return False


def load_photo(key: str, size: int | None = None):
    """Load an asset as a customtkinter-compatible CTkImage, or return None.

    Uses Pillow to build a ``CTkImage`` (proper High-DPI scaling for ctk
    widgets). Falls back gracefully when the asset file is missing or cannot
    be decoded. The returned image object is cached so it stays referenced
    for the lifetime of the process.

    Returns an object usable by customtkinter widgets, or ``None`` when
    unavailable (callers must then use a text fallback).
    """
    filename = ASSET_FILENAMES.get(key)
    if filename is None:
        return None

    cache_name = key if size is None else f"{key}@{size}"
    if cache_name in _IMAGE_CACHE:
        return _IMAGE_CACHE[cache_name]

    path: Path = get_asset_path(filename)
    if not path.is_file():
        return None

    try:
        from PIL import Image

        import customtkinter as ctk

        with Image.open(path) as img:
            img.load()
            # Preserve transparency; convert palette/RGB artwork to RGBA so
            # medals/icons composite cleanly on themed backgrounds.
            if img.mode in ("P", "LA"):
                img = img.convert("RGBA")
            elif img.mode != "RGBA":
                img = img.convert("RGBA")

            # Fit the artwork into a `size` x `size` box (or cap the longest
            # edge at MAX_DISPLAY_EDGE when no size is requested), always
            # preserving the source aspect ratio — user images may be any
            # dimension and need not be square.
            MAX_DISPLAY_EDGE = 512
            box = size if size is not None else MAX_DISPLAY_EDGE
            scale = box / max(img.width, img.height)
            target_w = max(1, round(img.width * scale))
            target_h = max(1, round(img.height * scale))
            if (target_w, target_h) != (img.width, img.height):
                img = img.resize((target_w, target_h), Image.LANCZOS)
            photo = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
            # Keep the resized PIL image accessible for raw-tk uses
            # (e.g. window iconphoto via PIL.ImageTk).
            photo.pil_image = img
    except Exception:
        return None

    _IMAGE_CACHE[cache_name] = photo
    return photo
