"""Thread-safe background-worker plumbing (Phase 2.2, A-30).

Two pieces:

``UIEventQueue`` — a ``queue.Queue`` wrapper polled from the Tkinter main
loop via ``after()``.  Worker threads only ever *put* callables; the main
loop *drains* them.  This is the single sanctioned channel from any
background thread into the customtkinter UI, which prevents cross-thread
widget crashes.

``ExtractionWorker`` — one daemon thread per document that runs
``app.core.pdfx.extract_document`` and publishes progress / completion /
failure events through a ``UIEventQueue``.  Supports cancel (A-30) and
throttles progress to EXTRACTION_PROGRESS_HZ so a 2,000-page file never
floods the event loop (NFR-12).
"""

from __future__ import annotations

import os
import queue
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable

# ``event_generate`` from a non-main thread used to be suspected of being
# unreliable on Windows, so a self-pipe + ``createfilehandler`` backend was
# tried as a workaround.  That experiment is over: ``createfilehandler`` does
# not exist in the standard Windows Tcl/Tk build, so the pipe setup silently
# failed there anyway — and merely *constructing* the queue with
# ``STUDYPROOF_UIQ_PIPE=1`` raised TclError in every environment (including
# headless CI runs of unrelated tests such as test_stopwatch.py).  Delivery is
# now handled entirely by thread-marshalled ``after_idle`` wakes plus a slow
# ``after()`` safety-net poll (see ``UIEventQueue._wake``), both of which are
# available on every platform.
_PIPE_BACKEND = os.environ.get('STUDYPROOF_UIQ_PIPE') == '1'


def _drain_fd(fd: int) -> None:
    try:
        while os.read(fd, 4096):
            pass
    except (BlockingIOError, OSError, ValueError):
        pass


class UIEventQueue:
    """Callable mailbox handed from worker threads to the Tk main loop.

    Delivery is *event-driven*: ``post()`` schedules an immediate wake-up of
    the main loop, so callbacks run at once even if the app sits idle.  Two
    complementary mechanisms are used because neither is universally
    reliable:

    - ``after_idle`` is registered **from the posting thread itself** against
      the shared Tcl interpreter.  Tkinter's ``_tkinter`` module marshals
      calls made from non-main threads back to the main thread, so this is
      fully thread-safe and fires within milliseconds of the next event-loop
      service (``update()`` / ``mainloop()``).
    - On Windows, where cross-thread ``event_generate`` can be deferred or
      dropped entirely, a self-pipe + ``fileevent`` is armed as well; the
      worker writes one byte and the main loop drains it instantly.

    A slow ``after()`` poll remains purely as a safety net in case a wake-up
    ever gets lost by the windowing system.
    """

    _WAKE_EVENT = "<<UIEventQueueWake>>"

    def __init__(self, widget, poll_ms: int = 50):
        """``widget`` is any Tk-interpolating object used for ``after()``."""
        self._queue: "queue.Queue[Callable[[], Any]]" = queue.Queue()
        # Weak reference: a destroyed widget must never keep the pump alive
        # (and calling ``after``/``bind`` on a dead widget would raise TclError
        # from worker threads after shutdown).
        self._widget_ref = weakref.ref(widget)
        self._poll_ms = max(10, int(poll_ms))
        self._running = False
        self._wake_scheduled = threading.Event()
        try:
            widget.bind(self._WAKE_EVENT, lambda _e: self._drain(), add='+')
        except Exception:
            pass
        self._rfd: int | None = None
        self._wfd: int | None = None
        if _PIPE_BACKEND:
            try:
                rfd, wfd = os.pipe()
                os.set_blocking(rfd, False)
                os.set_blocking(wfd, False)
                self._rfd, self._wfd = rfd, wfd
                # ``createfilehandler`` is the portable wrapper for
                # ``fileevent readable`` and guarantees the callback runs on
                # the main thread.
                widget.createfilehandler(rfd, 'readable', self._on_pipe_readable)
            except Exception:
                self._close_pipes()

    # -- worker side (any thread) ---------------------------------------------

    def post(self, callback: Callable[[], Any]) -> None:
        """Schedule ``callback`` to run on the main thread (thread-safe)."""
        self._queue.put(callback)
        # Coalesce wake-ups: one pending drain is enough for any number of
        # queued callbacks.
        if not self._wake_scheduled.is_set():
            self._wake_scheduled.set()
            self._wake()

    def _wake(self) -> None:
        widget = self._widget_ref()
        if widget is not None:
            try:
                # ``after_idle`` called from *this* (worker) thread goes
                # through tkinter's thread-marshalling and executes on the
                # main thread at the next event-loop service — no polling
                # delay, no reliance on event_generate delivery.
                widget.after_idle(self._idle_wake)
            except Exception:
                pass
        if self._wfd is not None:
            try:
                os.write(self._wfd, b'\x01')
            except Exception:
                pass

    def _idle_wake(self) -> None:
        # Runs on the main thread (marshalled by tkinter).
        self._wake_scheduled.clear()
        self._drain()

    # -- main-thread side -------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._schedule_drain()

    def stop(self) -> None:
        self._running = False
        self._close_pipes()

    def _close_pipes(self) -> None:
        if self._rfd is not None:
            widget = self._widget_ref()
            if widget is not None:
                try:
                    widget.deletefilehandler(self._rfd)
                except Exception:
                    pass
        for fd in (self._rfd, self._wfd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._rfd = self._wfd = None

    def _on_pipe_readable(self, _event=None) -> None:
        """Tk file handler callback (main thread): drain the self-pipe."""
        if self._rfd is not None:
            _drain_fd(self._rfd)
        self._wake_scheduled.clear()
        self._drain()

    def _schedule_drain(self) -> None:
        widget = self._widget_ref()
        if widget is None:
            # Widget garbage-collected between stop and tick — nothing left.
            self._running = False
            return
        try:
            widget.after(self._poll_ms, self._drain)
        except Exception:
            # Widget destroyed between stop and tick — nothing left to do.
            self._running = False

    def _drain(self) -> None:
        if not self._running:
            return
        # Bound each pass so a chatty worker can never starve rendering.
        for _ in range(64):
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                # A broken event callback must never kill the pump (ERR-17).
                pass
        self._schedule_drain()


class ExtractionWorker:
    """Background PDF extraction publishing results via UIEventQueue.

    Callbacks all run on the **main thread** (posted through the queue):
    - ``on_progress(document_id, page_no, total_pages)``
    - ``on_done(document_id, ExtractResult)``
    - ``on_failed(document_id, kind)``   kind ∈ {encrypted, unreadable,
      too_large} (ERR-29/ERR-30) or ``cancelled`` (A-30)
    """

    def __init__(self, events: UIEventQueue,
                 on_progress: Callable[[str, int, int], None] | None = None,
                 on_done: Callable[[str, Any], None] | None = None,
                 on_failed: Callable[[str, str], None] | None = None):
        self._events = events
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_failed = on_failed
        self._cancel_flags: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    # -- public API (main thread) ----------------------------------------------

    def start(self, document_id: str, path: "str | Path") -> None:
        """Begin extracting ``path`` for record ``document_id`` (PDF-4)."""
        flag = threading.Event()
        self._cancel_flags[document_id] = flag
        thread = threading.Thread(
            target=self._run, args=(document_id, Path(path), flag),
            name=f"pdf-extract-{document_id[:8]}", daemon=True)
        self._threads[document_id] = thread
        thread.start()

    def cancel(self, document_id: str) -> None:
        """Request cancellation; the worker polls it once per page (A-30)."""
        flag = self._cancel_flags.get(document_id)
        if flag is not None:
            flag.set()

    def has_active(self) -> bool:
        return any(t.is_alive() for t in self._threads.values())

    def wait_all(self, timeout: float | None = None) -> None:
        """Join every running extraction (tests / shutdown only)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in list(self._threads.values()):
            remaining = None if deadline is None \
                else max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

    # -- worker thread ------------------------------------------------------------

    def _run(self, document_id: str, path: Path,
             cancel_flag: threading.Event) -> None:
        from app.core import pdfx  # heavy import stays off the main thread

        state = {"last_emit": 0.0}
        min_interval = 1.0 / max(1, _hz())

        def publish_progress(page_no: int, total: int) -> None:
            now = time.monotonic()
            # Throttle (NFR-12) but always deliver the final page update.
            if now - state["last_emit"] < min_interval and page_no != total:
                return
            state["last_emit"] = now
            if self._on_progress is not None:
                self._events.post(
                    lambda p=page_no, t=total: self._on_progress(
                        document_id, p, t))

        try:
            result = pdfx.extract_pages(
                path, on_progress=publish_progress,
                should_cancel=cancel_flag.is_set)
        except pdfx.ExtractionCancelled:
            self._post_failure(document_id, "cancelled")
            return
        except pdfx.PdfOpenError as exc:
            self._post_failure(document_id, exc.kind)
            return
        except ImportError:
            # PyMuPDF missing from this environment — report honestly
            # instead of blaming the user's file (ERR-29 must not lie).
            self._post_failure(document_id, "dependency_missing")
            return
        except Exception:
            self._post_failure(document_id, "unreadable")
            return
        if self._on_done is not None:
            self._events.post(
                lambda r=result: self._on_done(document_id, r))

    def _post_failure(self, document_id: str, kind: str) -> None:
        if self._on_failed is not None:
            self._events.post(
                lambda k=kind: self._on_failed(document_id, k))


def _hz() -> int:
    from app.constants import EXTRACTION_PROGRESS_HZ
    return EXTRACTION_PROGRESS_HZ
