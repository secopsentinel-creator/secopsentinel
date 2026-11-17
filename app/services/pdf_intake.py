from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import write_audit, write_security_event
from app.core.config import get_settings
from app.models import User
from app.services import documents
from app.services.pdf import PdfPageLimitError, PdfReadError, extract_text_pages

INTAKE_TTL_SECONDS = 30 * 60
INTAKE_DIR_NAME = "intake"
DATE_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b|\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
SPANISH_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)
SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

FIELD_LABELS = {
    "nunc": "NUNC",
    "ot": "OT",
    "opj": "OPJ",
    "spoa": "SPOA",
    "assignment_date": "Fecha de asignación",
    "due_date": "Fecha de vencimiento",
    "status": "Estado",
    "priority": "Prioridad",
    "prosecutor_office": "Fiscalía",
    "office_number": "Número de despacho",
    "prosecutor_name": "Fiscal asignado",
    "prosecutor_email": "Correo del fiscal",
    "prosecutor_phone": "Teléfono del fiscal",
    "investigator_name": "Investigador asignado",
    "cti_group": "Grupo CTI",
    "management_status": "Estado de gestión",
    "crime": "Delito",
    "article": "Artículo",
    "municipality": "Municipio",
    "department": "Departamento",
}

FIELD_ALIASES = {
    "nunc": (r"N\.?\s*U\.?\s*N\.?\s*C\.?", r"N[uú]mero de noticia criminal"),
    "ot": (r"O\.?\s*T\.?", r"Orden de trabajo"),
    "opj": (r"O\.?\s*P\.?\s*J\.?", r"Orden de polic[ií]a judicial"),
    "spoa": (r"SPOA",),
    "assignment_date": (r"Fecha de asignaci[oó]n", r"Fecha de reparto"),
    "due_date": (r"Fecha de vencimiento", r"Vencimiento"),
    "status": (r"Estado del caso",),
    "priority": (r"Prioridad",),
    "prosecutor_office": (r"Fiscal[ií]a",),
    "office_number": (r"N[uú]mero de despacho", r"Despacho n[uú]mero"),
    "prosecutor_name": (r"Fiscal asignado",),
    "prosecutor_email": (r"Correo del fiscal", r"Correo electr[oó]nico"),
    "prosecutor_phone": (r"Tel[eé]fono del fiscal",),
    "investigator_name": (r"Investigador asignado",),
    "cti_group": (r"Grupo CTI",),
    "management_status": (r"Estado de gesti[oó]n",),
    "crime": (r"Delito", r"Conducta punible"),
    "article": (r"Art[ií]culo(?: del C[oó]digo Penal)?",),
    "municipality": (r"Municipio",),
    "department": (r"Departamento",),
}

STATUS_VALUES = {"activo", "suspendido", "archivado"}
PRIORITY_VALUES = {"alta", "media", "baja"}


class PdfIntakeError(ValueError):
    pass


@dataclass(frozen=True)
class IntakeDraft:
    token: str
    file_path: Path
    original_name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PdfCaseExtraction:
    values: dict[str, str]
    sources: dict[str, dict[str, int | str]]
    page_count: int
    text_found: bool


def _intake_root() -> Path:
    root = get_settings().data_dir.expanduser() / "tmp" / INTAKE_DIR_NAME
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root.resolve()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="secopsentinel-pdf-intake")


def _cleanup_expired_drafts() -> None:
    cutoff = datetime.now().timestamp() - INTAKE_TTL_SECONDS
    for path in _intake_root().iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff and not path.is_symlink():
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _parse_date(value: str) -> str | None:
    match = DATE_PATTERN.search(value)
    if match:
        if match.group(1):
            day, month, year = map(int, match.group(1, 2, 3))
        else:
            year, month, day = map(int, match.group(4, 5, 6))
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    match = SPANISH_DATE_PATTERN.search(value)
    if not match:
        return None
    day = int(match.group(1))
    month = SPANISH_MONTHS.get(match.group(2).lower())
    year = int(match.group(3))
    if month is None:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _field_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    labels = "|".join(sorted(aliases, key=len, reverse=True))
    return re.compile(
        rf"^\s*(?:{labels})(?:\s*:\s*|\s*#\s*|\s+[-–]\s+|\s+)(?P<value>.*?)\s*$",
        re.IGNORECASE,
    )


_FIELD_PATTERNS = {
    field: _field_pattern(aliases) for field, aliases in FIELD_ALIASES.items()
}


def _clean_value(field: str, value: str) -> str | None:
    value = " ".join(value.strip().split()).strip(" ;,.")
    if not value:
        return None
    if field in {"assignment_date", "due_date"}:
        return _parse_date(value)
    if field == "nunc":
        value = value.split()[0].strip(".,;:")
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", value):
            return None
    elif field in {"ot", "opj", "spoa", "office_number", "article"}:
        value = value.split()[0].strip(".,;:")
    elif field == "status":
        value = value.casefold()
        if value not in STATUS_VALUES:
            return None
    elif field == "priority":
        value = value.casefold()
        if value not in PRIORITY_VALUES:
            return None
    return value


def extract_case_fields(pages: list[str]) -> PdfCaseExtraction:
    values: dict[str, str] = {}
    sources: dict[str, dict[str, int | str]] = {}
    text_found = any(page.strip() for page in pages)

    for page_number, page in enumerate(pages, start=1):
        for line in page.splitlines():
            for field, pattern in _FIELD_PATTERNS.items():
                if field in values:
                    continue
                match = pattern.match(line)
                if not match:
                    continue
                value = _clean_value(field, match.group("value"))
                if value is None:
                    continue
                values[field] = value
                sources[field] = {"page": page_number, "excerpt": line.strip()[:240]}

    return PdfCaseExtraction(
        values=values,
        sources=sources,
        page_count=len(pages),
        text_found=text_found,
    )


def _record_rejection(db: Session, user: User, reason: str, severity: str) -> None:
    write_security_event(
        db,
        event_type="case_pdf_intake_rejected",
        severity=severity,
        detail=reason,
    )
    write_audit(
        db,
        action="case.pdf_intake.rejected",
        entity="case_intake",
        username=user.username,
        user_id=user.id,
        after={"reason": reason},
    )


async def stage_pdf_intake(
    db: Session,
    user: User,
    upload: UploadFile,
) -> tuple[IntakeDraft, PdfCaseExtraction]:
    filename = documents.safe_original_name(upload.filename)
    if Path(filename).suffix.lower() != ".pdf":
        _record_rejection(db, user, "formato no permitido; se requiere PDF", "warning")
        raise PdfIntakeError("Seleccione un archivo PDF.")

    _cleanup_expired_drafts()
    root = _intake_root()
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=root,
        prefix="intake-",
        suffix=".part",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        try:
            size, sha256 = await documents.write_upload(
                upload, temp_path, get_settings().max_upload_bytes
            )
            documents.validate_content(temp_path, ".pdf")
        except documents.UploadValidationError as exc:
            _record_rejection(db, user, str(exc), "warning")
            raise PdfIntakeError(str(exc)) from exc

        scan = documents.scan_file(temp_path)
        if not scan.clean:
            if scan.infected:
                quarantined = documents._quarantine_file(temp_path, sha256, ".pdf")
                _record_rejection(db, user, "malware detectado; PDF en cuarentena", "critical")
                write_audit(
                    db,
                    action="case.pdf_intake.quarantined",
                    entity="case_intake",
                    username=user.username,
                    user_id=user.id,
                    entity_id=sha256,
                    after={"quarantine_file": quarantined.name},
                )
                raise PdfIntakeError(
                    "El PDF fue rechazado por el antivirus y puesto en cuarentena."
                )
            _record_rejection(db, user, "fallo del antivirus; PDF rechazado", "error")
            raise PdfIntakeError("No se pudo verificar el PDF con el antivirus.")

        intake_id = uuid.uuid4().hex
        staged_path = root / f"{intake_id}.pdf"
        os.replace(temp_path, staged_path)
        staged_path.chmod(0o600)
        token = _serializer().dumps(
            {
                "id": intake_id,
                "name": filename,
                "sha256": sha256,
                "size": size,
            }
        )
        draft = IntakeDraft(token, staged_path, filename, sha256, size)
        try:
            pages = extract_text_pages(staged_path)
        except PdfPageLimitError as exc:
            staged_path.unlink(missing_ok=True)
            _record_rejection(db, user, str(exc), "warning")
            raise PdfIntakeError(str(exc)) from exc
        except PdfReadError:
            pages = []
        extraction = extract_case_fields(pages)
        write_audit(
            db,
            action="case.pdf_intake.preview",
            entity="case_intake",
            username=user.username,
            user_id=user.id,
            entity_id=sha256,
            after={
                "page_count": extraction.page_count,
                "fields_detected": sorted(extraction.values),
                "text_found": extraction.text_found,
            },
        )
        return draft, extraction
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def resolve_intake_draft(token: str) -> IntakeDraft:
    _cleanup_expired_drafts()
    try:
        data = _serializer().loads(token, max_age=INTAKE_TTL_SECONDS)
    except SignatureExpired as exc:
        raise PdfIntakeError(
            "La lectura del PDF venció. Vuelva a cargarlo para continuar."
        ) from exc
    except BadSignature as exc:
        raise PdfIntakeError(
            "El vínculo temporal del PDF no es válido. Vuelva a cargarlo."
        ) from exc

    intake_id = data.get("id", "")
    if not isinstance(intake_id, str) or not re.fullmatch(r"[0-9a-f]{32}", intake_id):
        raise PdfIntakeError("El vínculo temporal del PDF no es válido. Vuelva a cargarlo.")
    name = documents.safe_original_name(data.get("name"))
    sha256 = data.get("sha256", "")
    size = data.get("size", 0)
    if Path(name).suffix.lower() != ".pdf" or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PdfIntakeError("Los datos del PDF no son válidos. Vuelva a cargarlo.")
    if not isinstance(size, int) or size <= 0 or size > get_settings().max_upload_bytes:
        raise PdfIntakeError("El tamaño del PDF no es válido. Vuelva a cargarlo.")

    root = _intake_root()
    path = root / f"{intake_id}.pdf"
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise PdfIntakeError("El PDF temporal ya no está disponible. Vuelva a cargarlo.")
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != sha256:
        raise PdfIntakeError("El PDF temporal no superó la verificación de integridad.")
    return IntakeDraft(token, path, name, sha256, size)


def inspect_intake_draft(draft: IntakeDraft) -> PdfCaseExtraction:
    try:
        pages = extract_text_pages(draft.file_path)
    except PdfReadError:
        pages = []
    return extract_case_fields(pages)


def discard_intake_draft(draft: IntakeDraft) -> None:
    draft.file_path.unlink(missing_ok=True)
