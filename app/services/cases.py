from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.models import Case, CaseActivity, CaseDocument, User
from app.schemas import ActivityForm, CaseForm

NUNC_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

CASE_STATUS_LABELS = {
    "activo": "Activo",
    "suspendido": "Suspendido",
    "archivado": "Archivado",
}
CASE_PRIORITY_LABELS = {
    "alta": "Alta",
    "media": "Media",
    "baja": "Baja",
}
ACTIVITY_STATUS_LABELS = {
    "pendiente": "Pendiente",
    "en_curso": "En curso",
    "completada": "Completada",
}

CATEGORY_FOLDERS = {
    "documento_recibido": "Documento recibido",
    "denuncia": "Denuncia",
    "opj": "OPJ",
    "programa_metodologico": "Programa metodológico",
    "oficios": "Oficios",
    "respuestas": "Respuestas",
    "entrevistas": "Entrevistas",
    "informes": "Informes",
    "secop": "SECOP",
    "emp": "EMP",
    "ef": "EF",
}
CATEGORY_LABELS = dict(CATEGORY_FOLDERS)
CASE_FOLDERS = tuple(CATEGORY_FOLDERS.values())


class CaseServiceError(ValueError):
    pass


class DuplicateCaseError(CaseServiceError):
    pass


def normalize_nunc(nunc: str) -> str:
    value = nunc.strip()
    if not NUNC_PATTERN.fullmatch(value):
        raise CaseServiceError(
            "El NUNC solo puede contener letras, números, guiones y guiones bajos."
        )
    return value


def cases_root() -> Path:
    settings = get_settings()
    root = settings.cases_path.expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root.resolve()


def case_directory(nunc: str) -> Path:
    root = cases_root()
    folder = (root / normalize_nunc(nunc)).resolve()
    if not folder.is_relative_to(root):
        raise CaseServiceError("Ruta de expediente inválida.")
    return folder


def ensure_case_folder(nunc: str) -> bool:
    folder = case_directory(nunc)
    created = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    for name in CASE_FOLDERS:
        child = folder / name
        child.mkdir(parents=True, exist_ok=True, mode=0o700)
        child.chmod(0o700)
    return created


def case_manifest_path(nunc: str) -> Path:
    return case_directory(nunc) / "_manifest.json"


def document_link_name(document_sha256: str, original_name: str) -> str:
    safe_name = Path(original_name).name.replace("\\", "_").replace("/", "_").strip()
    if not safe_name:
        safe_name = "documento"
    return f"{document_sha256[:12]}_{safe_name}"


def case_snapshot(case: Case) -> dict[str, object]:
    return {
        "nunc": case.nunc,
        "ot": case.ot,
        "opj": case.opj,
        "spoa": case.spoa,
        "assignment_date": case.assignment_date.isoformat(),
        "due_date": case.due_date.isoformat() if case.due_date else None,
        "status": case.status,
        "priority": case.priority,
        "prosecutor_office": case.prosecutor_office,
        "office_number": case.office_number,
        "prosecutor_name": case.prosecutor_name,
        "prosecutor_email": case.prosecutor_email,
        "prosecutor_phone": case.prosecutor_phone,
        "investigator_name": case.investigator_name,
        "cti_group": case.cti_group,
        "management_status": case.management_status,
        "crime": case.crime,
        "article": case.article,
        "municipality": case.municipality,
        "department": case.department,
    }


def case_form_values(case: Case | None = None) -> dict[str, str]:
    if case is None:
        return {
            "assignment_date": date.today().isoformat(),
            "status": "activo",
            "priority": "media",
        }
    values = case_snapshot(case)
    return {key: "" if value is None else str(value) for key, value in values.items()}


def create_case(db: Session, user: User, form: CaseForm) -> Case:
    nunc = normalize_nunc(form.nunc)
    if db.scalar(select(Case.id).where(Case.nunc == nunc)) is not None:
        raise DuplicateCaseError("Ya existe un caso con ese NUNC.")

    data = form.model_dump()
    data["nunc"] = nunc
    case = Case(**data, created_by_id=user.id)
    db.add(case)
    created_folder = False
    try:
        db.flush()
        created_folder = ensure_case_folder(case.nunc)
        refresh_manifest(db, case)
        return case
    except Exception:
        db.rollback()
        if created_folder:
            shutil.rmtree(case_directory(nunc), ignore_errors=True)
        raise


def update_case(db: Session, case: Case, form: CaseForm) -> Case:
    if form.nunc.strip() != case.nunc:
        raise CaseServiceError("El NUNC no se puede modificar después de crear el caso.")
    data = form.model_dump()
    data.pop("nunc", None)
    for key, value in data.items():
        setattr(case, key, value)
    ensure_case_folder(case.nunc)
    db.flush()
    refresh_manifest(db, case)
    return case


def add_activity(db: Session, case: Case, user: User, form: ActivityForm) -> CaseActivity:
    activity = CaseActivity(
        case_id=case.id,
        activity=form.activity,
        activity_date=form.activity_date,
        status=form.status,
        notes=form.notes,
        created_by_id=user.id,
    )
    db.add(activity)
    db.flush()
    return activity


def get_case(db: Session, case_id: int, *, details: bool = False) -> Case | None:
    statement = select(Case).where(Case.id == case_id)
    if details:
        statement = statement.options(
            selectinload(Case.document_links).selectinload(CaseDocument.document),
            selectinload(Case.activities),
        )
    return db.scalar(statement)


def list_cases(db: Session, query: str = "") -> list[Case]:
    statement = select(Case).order_by(
        Case.due_date.is_(None),
        Case.due_date,
        Case.created_at.desc(),
    )
    query = query.strip()
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                Case.nunc.ilike(pattern),
                Case.ot.ilike(pattern),
                Case.opj.ilike(pattern),
                Case.prosecutor_name.ilike(pattern),
                Case.investigator_name.ilike(pattern),
            )
        )
    return list(db.scalars(statement).all())


def refresh_manifest(db: Session, case: Case) -> Path:
    folder = case_directory(case.nunc)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)

    links = db.scalars(
        select(CaseDocument)
        .options(selectinload(CaseDocument.document))
        .where(CaseDocument.case_id == case.id)
        .order_by(CaseDocument.linked_at)
    ).all()
    documents = []
    for link in links:
        document = link.document
        folder_name = CATEGORY_FOLDERS.get(link.category, "Oficios")
        link_name = document_link_name(document.sha256, document.original_name)
        document_entry = {
            "sha256": document.sha256,
            "original_name": document.original_name,
            "relative_path": document.relative_path,
            "case_path": f"{folder_name}/{link_name}",
            "category": link.category,
            "mime_type": document.mime_type,
            "extension": document.extension,
            "size_bytes": document.size_bytes,
            "linked_at": link.linked_at.isoformat() if link.linked_at else None,
            "file_present": (folder / folder_name / link_name).is_file(),
        }
        documents.append(document_entry)

    manifest = {
        "nunc": case.nunc,
        "ot": case.ot,
        "opj": case.opj,
        "spoa": case.spoa,
        "status": case.status,
        "priority": case.priority,
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": documents,
    }
    manifest_path = folder / "_manifest.json"
    temporary = folder / "_manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(manifest_path)
    manifest_path.chmod(0o600)
    return manifest_path
