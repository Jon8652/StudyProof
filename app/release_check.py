"""Opt-in release diagnostic, isolated from the user's real study data."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(report_path: str) -> int:
    """Exercise the actual frozen imports, assets, workers, UI and storage.

    Invoked only with --verify-release REPORT.json. A JSON report works with
    the windowed Windows executable, which intentionally has no console.
    """
    from app.utils import paths

    report = {'ok': False, 'frozen': bool(getattr(sys, 'frozen', False)), 'checks': []}
    app = None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)

    def pump(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            app.update()
            time.sleep(.01)
        check('background operation completed', predicate())

    try:
        with tempfile.TemporaryDirectory(prefix='studyproof-release-') as folder:
            paths.set_override_data_dir(folder)
            from app.core import pdfx
            from app.gui.main_window import MainWindow
            from app.persistence import repository
            from app.state.app_state import AppState
            from app.utils.resources import ASSET_FILENAMES, load_photo

            pymupdf = pdfx._load_pymupdf()
            pdf_path = Path(folder) / 'release-fixture.pdf'
            with pymupdf.open() as pdf:
                for _ in range(2):
                    pdf.new_page().insert_textbox((50, 50, 550, 780),
                        'Photosynthesis converts sunlight into chemical energy stored in glucose, '
                        'while chlorophyll absorbs the light needed for this process.')
                pdf.save(str(pdf_path))
            state = AppState()
            app = MainWindow(app_state=state)
            app.withdraw()
            callback_errors = []
            app.report_callback_exception = lambda *args: callback_errors.append(args[1])
            for name in ASSET_FILENAMES:
                check('bundled asset: ' + name, load_photo(name, 18) is not None)

            imported = state.import_document(pdf_path)
            check('PDF binary imported', imported.ok)
            document = imported.value
            app.show_screen('library')
            library = app.screens['library']
            pump(lambda: document.status != 'extracting')
            check('PDF extraction', document.status == 'ready' and document.page_count == 2)
            library.select_document(document.id)
            pump(lambda: library.viewer.image_label.has_image)
            library.viewer.go_to_page(2)
            pump(lambda: (2, 1.0) in library.viewer._cache)
            check('PDF page rendered in Tk', library.viewer.image_label.has_image)

            app.show_screen('coach')
            coach = app.screens['coach']
            started = coach.service.start_session([('document', document.id)])
            check('coach session created', started.ok)
            session = started.value
            coach.resume_session(session.id)
            coach.request('question')
            pump(lambda: not coach.busy)
            check('Mock question generated', len(session.questions) == 1)
            coach.composer.insert('1.0', 'Sunlight supplies energy.')
            coach.answer()
            coach.rate('low')
            coach.request('feedback')
            pump(lambda: not coach.busy)
            check('grounded feedback', bool(session.turns[-1].quotes)
                  and all(q['verified'] for q in session.turns[-1].quotes))
            coach.composer.insert('1.0', 'Chlorophyll absorbs sunlight to produce glucose.')
            coach.revise()
            check('revision appended', len(session.questions[0].revisions) == 1)
            coach.request('followup')
            pump(lambda: not coach.busy)
            check('follow-up linked', session.questions[-1].parent_id == session.questions[0].id)
            app.show_screen('stats')
            check('Review updated', bool(state.get_review_metrics().weak_sources))
            app.show_screen('settings')
            check('AI settings masked', app.screens['settings'].ai_entries['api_key'].cget('show') == '*')
            # Import the optional client without making any network requests.
            from app.core.agent.provider_openai import OpenAICompatProvider
            check('Live client bundled', OpenAICompatProvider is not None)
            check('save succeeded', state.manual_save().ok)
            restored = repository.validate_file_for_manual_load(paths.get_default_save_path()).dataset
            check('restart round-trip', restored.coach_sessions[0].questions[0].first_answer
                  == 'Sunlight supplies energy.' and restored.documents[0].status == 'ready')
            check('no Tk callback errors', not callback_errors)
            app._on_close()
            app = None
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if app is not None:
            try:
                app._on_close()
            except Exception:
                pass
        paths.set_override_data_dir(None)
        Path(report_path).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
