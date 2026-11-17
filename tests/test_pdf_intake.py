import pytest
from pypdf import PdfWriter

from app.services import pdf
from app.services.pdf import PdfPageLimitError
from app.services.pdf_intake import extract_case_fields


def test_extract_case_fields_keeps_page_sources_and_does_not_invent_due_date():
    extraction = extract_case_fields(
        [
            "NUNC: NUNC-2026-01\n"
            "OT: OT-44\n"
            "OPJ: OPJ-17\n"
            "Fecha de asignación: 16 de noviembre de 2026",
            "Fiscal asignado: Fiscal de prueba\nMunicipio: Cali",
        ]
    )

    assert extraction.values == {
        "nunc": "NUNC-2026-01",
        "ot": "OT-44",
        "opj": "OPJ-17",
        "assignment_date": "2026-11-16",
        "prosecutor_name": "Fiscal de prueba",
        "municipality": "Cali",
    }
    assert extraction.sources["assignment_date"]["page"] == 1
    assert extraction.sources["municipality"]["page"] == 2
    assert "due_date" not in extraction.values


def test_extract_case_fields_reads_only_explicit_due_date():
    extraction = extract_case_fields(["Fecha de vencimiento: 30/11/2026"])

    assert extraction.values["due_date"] == "2026-11-30"
    assert extraction.sources["due_date"]["page"] == 1


def test_pypdf_fallback_runs_in_limited_subprocess(tmp_path, monkeypatch):
    path = tmp_path / "digital.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    monkeypatch.setattr(pdf, "extract_text_with_pdftotext", lambda path, timeout: "")

    assert pdf.extract_text_pages(path) == [""]


def test_pdf_page_limit_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pdf,
        "extract_text_with_pdftotext",
        lambda path, timeout: "\f".join(["text"] * 201),
    )

    with pytest.raises(PdfPageLimitError):
        pdf.extract_text_pages(tmp_path / "large.pdf")
