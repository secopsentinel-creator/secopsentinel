import hashlib
import json
import os
import re
import stat
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import func, select

from app.models import AuditLog, Case, CaseDocument, Document, SecurityEvent
from app.services import documents
from app.services.cases import document_link_name
from app.services.documents import ScanResult

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def case_data(nunc: str = "NUNC-001", **overrides) -> dict[str, str]:
    data = {
        "nunc": nunc,
        "ot": "OT-001",
        "opj": "OPJ-001",
        "spoa": "SPOA-001",
        "assignment_date": "2026-11-16",
        "due_date": "2026-11-30",
        "status": "activo",
        "priority": "alta",
        "prosecutor_office": "Fiscalía 1",
        "office_number": "10",
        "prosecutor_name": "Fiscal de prueba",
        "prosecutor_email": "fiscal@example.test",
        "prosecutor_phone": "3000000000",
        "investigator_name": "Investigador de prueba",
        "cti_group": "CTI",
        "management_status": "En curso",
        "crime": "Contratación indebida",
        "article": "408",
        "municipality": "Bogotá",
        "department": "Cundinamarca",
    }
    data.update(overrides)
    return data


def create_case(client, nunc: str = "NUNC-001", **overrides):
    token = csrf_from(client.get("/cases/new").text)
    data = case_data(nunc, **overrides)
    data["csrf_token"] = token
    return client.post("/cases", data=data, follow_redirects=False)


def upload_document(client, case_id: int, *, filename: str = "denuncia.pdf",
                    content: bytes = PDF_BYTES, category: str = "denuncia"):
    token = csrf_from(client.get(f"/cases/{case_id}?tab=documentos").text)
    return client.post(
        f"/cases/{case_id}/documents",
        data={"csrf_token": token, "category": category},
        files={"file": (filename, content, "application/pdf")},
        follow_redirects=False,
    )


def clean_scan(monkeypatch):
    monkeypatch.setattr(documents, "scan_file", lambda path: ScanResult(clean=True))


def test_create_case_creates_folder_manifest_and_dashboard(auth_client, db_session):
    response = create_case(auth_client)

    assert response.status_code == 303
    assert re.match(r"^/cases/\d+\?created=1$", response.headers["location"])

    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None
    assert case.ot == "OT-001"
    assert case.priority == "alta"

    folder = Path(os.environ["CASES_DIR"]) / "NUNC-001"
    assert folder.is_dir()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for name in ("Denuncia", "OPJ", "Oficios", "Informes", "SECOP", "EMP", "EF"):
        assert (folder / name).is_dir()
    manifest_path = folder / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["nunc"] == "NUNC-001"
    assert manifest["documents"] == []
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600

    detail = auth_client.get(response.headers["location"])
    assert detail.status_code == 200
    assert "NUNC-001" in detail.text

    dashboard = auth_client.get("/")
    assert dashboard.status_code == 200
    assert "Casos registrados" in dashboard.text

    audit_count = db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "case.create")
    )
    assert audit_count == 1


def test_cases_list_and_edit_render(auth_client, db_session):
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    listing = auth_client.get("/cases")
    assert listing.status_code == 200
    assert "Noticias criminales" in listing.text
    assert "NUNC-001" in listing.text

    searched = auth_client.get("/cases?q=OT-001")
    assert searched.status_code == 200
    assert "NUNC-001" in searched.text

    edit_page = auth_client.get(f"/cases/{case.id}/edit")
    assert edit_page.status_code == 200
    assert 'value="NUNC-001"' in edit_page.text
    assert "readonly" in edit_page.text


def test_case_rejects_path_traversal_nunc(auth_client, db_session):
    response = create_case(auth_client, nunc="../evil")

    assert response.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(Case)) == 0
    assert not (Path(os.environ["CASES_DIR"]).parent / "evil").exists()


def test_case_rejects_due_before_assignment(auth_client, db_session):
    response = create_case(auth_client, due_date="2026-11-01")

    assert response.status_code == 400
    assert "vencimiento" in response.text.lower()
    assert db_session.scalar(select(func.count()).select_from(Case)) == 0


def test_document_upload_hashes_hardlinks_and_manifest(auth_client, db_session, monkeypatch):
    clean_scan(monkeypatch)
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    response = upload_document(auth_client, case.id)
    assert response.status_code == 303

    document = db_session.scalar(select(Document))
    assert document is not None
    expected_hash = hashlib.sha256(PDF_BYTES).hexdigest()
    assert document.sha256 == expected_hash
    assert document.original_name == "denuncia.pdf"
    assert document.size_bytes == len(PDF_BYTES)
    assert document.mime_type == "application/pdf"

    canonical = (
        Path(os.environ["DATA_DIR"]) / "documents" / expected_hash[:2] / f"{expected_hash}.pdf"
    )
    case_file = (
        Path(os.environ["CASES_DIR"])
        / "NUNC-001"
        / "Denuncia"
        / document_link_name(expected_hash, "denuncia.pdf")
    )
    assert canonical.is_file()
    assert case_file.is_file()
    assert canonical.stat().st_ino == case_file.stat().st_ino
    assert stat.S_IMODE(canonical.stat().st_mode) == 0o600
    assert stat.S_IMODE(case_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(case_file.parent.stat().st_mode) == 0o700

    link = db_session.scalar(select(CaseDocument))
    assert link is not None
    assert link.case_id == case.id
    assert link.category == "denuncia"

    manifest = json.loads(
        (Path(os.environ["CASES_DIR"]) / "NUNC-001" / "_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["documents"][0]["sha256"] == expected_hash
    assert manifest["documents"][0]["file_present"] is True

    detail = auth_client.get(f"/cases/{case.id}?tab=documentos")
    assert detail.status_code == 200
    assert "denuncia.pdf" in detail.text


def test_duplicate_document_in_same_case_is_rejected(auth_client, db_session, monkeypatch):
    clean_scan(monkeypatch)
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    assert upload_document(auth_client, case.id).status_code == 303
    response = upload_document(auth_client, case.id)

    assert response.status_code == 400
    assert "ya está vinculado" in response.text
    assert db_session.scalar(select(func.count()).select_from(Document)) == 1
    assert db_session.scalar(select(func.count()).select_from(CaseDocument)) == 1
    duplicates = db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "document.duplicate")
    )
    assert duplicates == 1


def test_duplicate_document_in_another_case_links_existing(auth_client, db_session, monkeypatch):
    clean_scan(monkeypatch)
    assert create_case(auth_client, nunc="NUNC-001").status_code == 303
    assert create_case(auth_client, nunc="NUNC-002", ot="OT-002").status_code == 303
    first = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    second = db_session.scalar(select(Case).where(Case.nunc == "NUNC-002"))
    assert first is not None and second is not None

    assert upload_document(auth_client, first.id).status_code == 303
    assert upload_document(auth_client, second.id).status_code == 303

    assert db_session.scalar(select(func.count()).select_from(Document)) == 1
    assert db_session.scalar(select(func.count()).select_from(CaseDocument)) == 2
    second_file = (
        Path(os.environ["CASES_DIR"])
        / "NUNC-002"
        / "Denuncia"
        / document_link_name(hashlib.sha256(PDF_BYTES).hexdigest(), "denuncia.pdf")
    )
    assert second_file.is_file()


def test_upload_rejects_mime_mismatch(auth_client, db_session):
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    response = upload_document(auth_client, case.id, content=b"esto no es un pdf")

    assert response.status_code == 400
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    event = db_session.scalar(
        select(SecurityEvent).where(SecurityEvent.event_type == "document_upload_rejected")
    )
    assert event is not None
    assert event.severity == "warning"
    rejected = db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "document.rejected")
    )
    assert rejected == 1


def test_upload_quarantines_infected_file(auth_client, db_session, monkeypatch):
    monkeypatch.setattr(
        documents,
        "scan_file",
        lambda path: ScanResult(
            clean=False,
            infected=True,
            signature="Eicar-Test-Signature",
        ),
    )
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    response = upload_document(auth_client, case.id)

    assert response.status_code == 400
    assert "antivirus" in response.text
    assert db_session.scalar(select(func.count()).select_from(Document)) == 0
    quarantined = list(Path(os.environ["QUARANTINE_DIR"]).iterdir())
    assert len(quarantined) == 1
    assert stat.S_IMODE(quarantined[0].stat().st_mode) == 0o600
    event = db_session.scalar(
        select(SecurityEvent).where(SecurityEvent.event_type == "document_upload_rejected")
    )
    assert event is not None
    assert event.severity == "critical"


def test_document_download_requires_auth_and_audits(auth_client, db_session, monkeypatch):
    clean_scan(monkeypatch)
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None
    assert upload_document(auth_client, case.id).status_code == 303
    document = db_session.scalar(select(Document))
    assert document is not None

    response = auth_client.get(f"/cases/{case.id}/documents/{document.id}")
    assert response.status_code == 200
    assert response.content == PDF_BYTES
    downloads = db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "document.download")
    )
    assert downloads == 1

    auth_client.cookies.clear()
    response = auth_client.get(
        f"/cases/{case.id}/documents/{document.id}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_activity_and_excel_export(auth_client, db_session):
    assert create_case(auth_client).status_code == 303
    case = db_session.scalar(select(Case).where(Case.nunc == "NUNC-001"))
    assert case is not None

    token = csrf_from(auth_client.get(f"/cases/{case.id}?tab=actividades").text)
    response = auth_client.post(
        f"/cases/{case.id}/activities",
        data={
            "csrf_token": token,
            "activity": "Solicitar respuesta al oficio",
            "activity_date": "2026-11-17",
            "status": "pendiente",
            "notes": "Pendiente de respuesta",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Solicitar respuesta al oficio" in auth_client.get(
        f"/cases/{case.id}?tab=actividades"
    ).text

    export = auth_client.get("/cases/export.xlsx")
    assert export.status_code == 200
    workbook = load_workbook(BytesIO(export.content))
    assert workbook.sheetnames == ["Control General de OT", "Actividades"]
    assert workbook["Control General de OT"]["B2"].value == "NUNC-001"
    assert workbook["Actividades"]["C2"].value == "Solicitar respuesta al oficio"
