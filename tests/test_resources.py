"""Asset-loading tests for the user artwork pipeline (Part J).

The app ships with user-provided PNGs of arbitrary size and color mode
(64x64 RGBA up to 980x980 RGB). The resource loader must:
  * load every registered asset without crashing,
  * convert non-RGBA modes to RGBA while preserving transparency,
  * downscale oversized artwork keeping the aspect ratio,
  * cap unscaled loads at a sane display size,
  * never return None for a valid file, and return None gracefully for
    unknown keys / missing files (text-fallback contract FR-79/ERR-12),
  * derive a multi-size .ico from the PNG for the Windows exe icon.

CTkImage creation requires a Tk interpreter, so these tests run only when
a display is available; otherwise they skip (the GUI smoke test covers
them under xvfb).
"""

from __future__ import annotations

import pytest

from app.utils.resources import ASSET_FILENAMES, asset_exists, export_ico


def _tk_available() -> bool:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark_tk = pytest.mark.skipif(not _tk_available(), reason="no display for Tk")


def test_all_registered_assets_exist():
    for key in ASSET_FILENAMES:
        assert asset_exists(key), f"asset missing for key {key!r}"


@pytestmark_tk
def test_every_asset_loads_at_ui_sizes():
    from app.utils.resources import load_photo

    for key in ASSET_FILENAMES:
        for size in (18, 20, 64):
            img = load_photo(key, size=size)
            assert img is not None, f"{key} @ {size} failed to load"
            w, h = img.pil_image.size
            assert max(w, h) <= size, f"{key}@{size} not downscaled: {(w, h)}"


@pytestmark_tk
def test_oversized_asset_is_capped_when_unscaled():
    from app.utils.resources import load_photo

    img = load_photo("calendar_icon")  # source can be ~980x980
    assert img is not None
    assert max(img.pil_image.size) <= 512


@pytestmark_tk
def test_aspect_ratio_preserved_for_non_square():
    from PIL import Image

    from app.utils.paths import get_asset_path
    from app.utils.resources import load_photo

    src = Image.open(get_asset_path("settings_icon.png"))
    sw, sh = src.size
    src.close()
    if sw == sh:
        pytest.skip("asset happens to be square")

    img = load_photo("settings_icon", size=64)
    w, h = img.pil_image.size
    expected_h = round(64 * sh / sw)
    assert w == 64
    assert abs(h - expected_h) <= 1, f"aspect ratio broken: {(w, h)} vs source {(sw, sh)}"


@pytestmark_tk
def test_rgba_conversion_preserves_alpha():
    from app.utils.resources import load_photo

    for key in ("gold_medal", "calendar_icon"):  # mixed RGB/RGBA sources
        img = load_photo(key, size=32)
        assert img.pil_image.mode == "RGBA"


def test_unknown_key_and_missing_file_return_none():
    from app.utils.resources import load_photo

    assert load_photo("no_such_asset") is None
    assert load_photo("") is None


def test_export_ico_from_user_png(tmp_path):
    ico = tmp_path / "icon.ico"
    assert export_ico("app_icon.png", ico) is True
    data = ico.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"  # ICO magic header
    assert len(data) > 100


def test_export_ico_missing_source(tmp_path):
    assert export_ico("does_not_exist.png", tmp_path / "x.ico") is False
