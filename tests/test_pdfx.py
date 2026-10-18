"""PDF pipeline tests (Phase 1.4/2.2) — headless, no Tk required.

Covers the regressions behind the "Couldn't open PDF" reports:
- permissions-only encrypted PDFs (empty user password) must open;
- user-password-protected PDFs raise PdfOpenError("encrypted");
- junk files raise PdfOpenError("unreadable"), missing paths "missing";
- render_page_pixmap returns a PIL image for a real page.
"""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.core import pdfx  # noqa: E402


def _make_pdf(path, text="Photosynthesis converts light into glucose.",
              pages=2):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _encrypt(src, dst, **kwargs):
    doc = pymupdf.open(str(src))
    doc.save(str(dst), **kwargs)
    doc.close()


def test_extract_and_render_plain_pdf(tmp_path):
    pdf = tmp_path / "plain.pdf"
    _make_pdf(pdf)
    result = pdfx.extract_document(pdf)
    assert result.page_count == 2
    assert any("Photosynthesis" in text for _, text in result.pages)
    image = pdfx.render_page_pixmap(pdf, 1, 1.0)
    assert image.width > 0 and image.height > 0


def test_permissions_only_encryption_opens(tmp_path):
    """Owner-password (permissions-only) PDFs must NOT be refused."""
    src = tmp_path / "src.pdf"
    _make_pdf(src, pages=1)
    pdf = tmp_path / "perm.pdf"
    _encrypt(src, pdf, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="owner", permissions=0)   # empty user password
    assert pdfx.get_page_count(pdf) == 1        # would raise if mislabelled
    image = pdfx.render_page_pixmap(pdf, 1, 1.0)
    assert image.width > 0


def test_user_password_protected_raises_encrypted(tmp_path):
    src = tmp_path / "src.pdf"
    _make_pdf(src, pages=1)
    pdf = tmp_path / "locked.pdf"
    _encrypt(src, pdf, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="secret", owner_pw="owner")
    with pytest.raises(pdfx.PdfOpenError) as excinfo:
        pdfx.get_page_count(pdf)
    assert excinfo.value.kind == "encrypted"


def test_junk_file_raises_unreadable(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.7\nthis is not really a pdf at all\n%%EOF")
    with pytest.raises(pdfx.PdfOpenError) as excinfo:
        pdfx.get_page_count(junk)
    assert excinfo.value.kind == "unreadable"


def test_missing_file_raises_missing(tmp_path):
    with pytest.raises(pdfx.PdfOpenError) as excinfo:
        pdfx.get_page_count(tmp_path / "nope.pdf")
    assert excinfo.value.kind == "missing"


def test_import_refuses_protected_without_record(isolated_data_dir):
    """AppState probe path: unreadable PDF never creates a record."""
    from app.state.app_state import AppState

    junk = isolated_data_dir / "broken.pdf"
    junk.write_bytes(b"not a pdf")
    state = AppState()
    result = state.import_document(junk)
    assert not result.ok
    assert state.dataset.documents == []
