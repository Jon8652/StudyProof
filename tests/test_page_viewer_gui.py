"""PDF viewport regression checks using real Tk configure events."""
import time
import tkinter

import customtkinter as ctk
from PIL import Image
import pytest

from app.gui.widgets.page_viewer import PageViewer
from app.gui.workers import UIEventQueue


def test_viewport_resize_and_image_changes_keep_pages_scrollable():
    try:
        root = ctk.CTk()
    except tkinter.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    events = UIEventQueue(root)
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args[1])
    try:
        root.attributes('-alpha', 0)
        root.geometry('640x480')
        viewer = PageViewer(root, events)
        viewer.pack(fill='both', expand=True)

        def settle():
            for _ in range(10):
                root.update()
                time.sleep(.01)

        viewer.clear()
        settle()
        assert int(viewer.image_canvas.itemcget(viewer._image_window, 'width')) == max(
            viewer.image_canvas.winfo_width() - 4, 1)

        viewer._display_image(Image.new('RGB', (1600, 300), 'white'))
        settle()
        assert viewer.image_label.has_image
        assert int(viewer.image_canvas.itemcget(viewer._image_window, 'width')) == 0
        assert viewer.image_canvas.bbox(viewer._image_window)[2] >= 1600

        root.geometry('800x500')
        settle()
        assert int(viewer.image_canvas.itemcget(viewer._image_window, 'width')) == 0
        viewer.clear()
        settle()
        assert not viewer.image_label.has_image
        assert int(viewer.image_canvas.itemcget(viewer._image_window, 'width')) == max(
            viewer.image_canvas.winfo_width() - 4, 1)
        assert not errors
    finally:
        events.stop()
        root.destroy()
