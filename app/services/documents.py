from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import write_audit, write_security_event
from app.core.config import get_settings
from app.models import Case, CaseDocument, Document, User
from app.services.cases import (
    CATEGORY_FOLDERS,
    case_directory,
    document_link_name,
    refresh_manifest,
)

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
ZIP_REQUIRED_MEMBERS = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
}
CHUNK_SIZE = 1024 * 1024


class DocumentServiceError(ValueError):
    pass


class UploadValidationError(DocumentServiceError):
    pass


class DuplicateDocumentError(DocumentServiceError):
    pass


class IntegrityError(DocumentServiceError):
    pass


@dataclass
class ScanResult:
    clean: bool
    infected: bool = False
    signature: str | None = None
    error: str | None = None


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path.resolve()


def documents_root() -> Path:
    settings = get_settings()
    return _ensure_private_dir(settings.documents_path.expanduser())


def quarantine_root() -> Path:
    settings = get_settings()
    return _ensure_private_dir(settings.quarantine_path.expanduser())


def _tmp_root() -> Path:
    settings = get_settings()
    return _ensure_private_dir(settings.data_dir.expanduser() / "tmp")


def safe_original_name(filename: str | None) -> str:
    name = Path(filename or "").name.replace("\\", "_").replace("/", "_").strip()
    name = "".join(character for character in name if character >= " " and character != "\x7f")
    if not name:
        name = "documento"
    suffix = Path(name).suffix
    stem = Path(name).stem[:180]
    return f"{stem}{suffix}" if stem else f"documento{suffix}"


def extension_for(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            "Formato no permitido. Use PDF, DOCX o XLSX."
        )
    return extension


def scan_file(path: Path) -> ScanResult:
    try:
        result = subprocess.run(
            ["clamdscan", "--fdpass", "--no-summary", "--stdout", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ScanResult(clean=False, error=f"{type(exc).__name__}: {exc}")

    if result.returncode == 0:
        return ScanResult(clean=True)
    if result.returncode == 1:
        output = (result.stdout or result.stderr or "malware detectado").strip()
        return ScanResult(clean=False, infected=True, signature=output[:500])
    output = (result.stderr or result.stdout or "error de clamdscan").strip()
    return ScanResult(clean=False, error=output[:500])


def _validate_content(path: Path, extension: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(8)

    if extension == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise UploadValidationError("El archivo no parece un PDF válido.")
        return

    required_member = ZIP_REQUIRED_MEMBERS.get(extension)
    if required_member is None:
        raise UploadValidationError("Formato no permitido.")
    if not zipfile.is_zipfile(path):
        raise UploadValidationError("El archivo no parece un documento Office válido.")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("El archivo Office está dañado.") from exc
    if required_member not in names:
        raise UploadValidationError("El contenido del archivo no coincide con su extensión.")


async def _write_upload(upload: UploadFile, path: Path, max_bytes: int) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("wb") as output:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise UploadValidationError(
                    f"El archivo supera el límite de {max_bytes // (1024 * 1024)} MB."
                )
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if size == 0:
        raise UploadValidationError("El archivo está vacío.")
    return size, digest.hexdigest()


def _quarantine_file(path: Path, sha256: str, extension: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = quarantine_root() / f"{timestamp}_{sha256[:16]}{extension}"
    shutil.move(str(path), destination)
    destination.chmod(0o600)
    return destination


def _canonical_path(sha256: str, extension: str) -> Path:
    root = documents_root()
    folder = _ensure_private_dir(root / sha256[:2])
    return folder / f"{sha256}{extension}"


def _same_inode(first: Path, second: Path) -> bool:
    try:
        return os.stat(first).st_ino == os.stat(second).st_ino
    except OSError:
        return False


def _link_into_case(case: Case, category: str, document: Document) -> Path:
    folder = _ensure_private_dir(case_directory(case.nunc) / CATEGORY_FOLDERS[category])
    canonical = resolve_document_path(document)
    name = document_link_name(document.sha256, document.original_name)
    destination = folder / name
    if destination.exists() and not _same_inode(destination, canonical):
        stem = Path(name).stem
        suffix = Path(name).suffix
        for index in range(1, 1000):
            candidate = folder / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                destination = candidate
                break
        else:
            raise IntegrityError("No se pudo crear un nombre único para el documento.")
    if not destination.exists():
        try:
            os.link(canonical, destination)
        except OSError:
            shutil.copyfile(canonical, destination)
    destination.chmod(0o600)
    return destination


def resolve_document_path(document: Document) -> Path:
    settings = get_settings()
    data_dir = settings.data_dir.expanduser().resolve()
    root = documents_root()
    path = (data_dir / document.relative_path).resolve()
    if not path.is_relative_to(root):
        raise IntegrityError("La ruta del documento está fuera del almacén permitido.")
    if path.is_symlink() or not path.is_file():
        raise IntegrityError("El archivo del documento no está disponible.")
    return path


def _reject(
    db: Session,
    case: Case,
    user: User,
    sha256: str,
    category: str,
    reason: str,
    *,
    severity: str = "warning",
    original_name: str | None = None,
) -> None:
    write_security_event(
        db,
        event_type="document_upload_rejected",
        severity=severity,
        detail=f"Caso {case.id}, categoría {category}: {reason}",
    )
    write_audit(
        db,
        action="document.rejected",
        entity="document",
        username=user.username,
        user_id=user.id,
        entity_id=sha256 or None,
        after={
            "case_id": case.id,
            "category": category,
            "reason": reason,
            "original_name": original_name,
        },
    )


async def save_document(
    db: Session,
    case: Case,
    user: User,
    upload: UploadFile,
    category: str,
) -> Document:
    if category not in CATEGORY_FOLDERS:
        raise UploadValidationError("Categoría documental inválida.")

    original_name = safe_original_name(upload.filename)
    extension = extension_for(original_name)
    settings = get_settings()
    max_bytes = settings.max_upload_bytes

    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=_tmp_root(),
        prefix="upload-",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        try:
            size, sha256 = await _write_upload(upload, temp_path, max_bytes)
        except UploadValidationError as exc:
            _reject(
                db,
                case,
                user,
                "",
                category,
                str(exc),
                severity="warning",
                original_name=original_name,
            )
            raise

        try:
            _validate_content(temp_path, extension)
        except UploadValidationError as exc:
            _reject(
                db,
                case,
                user,
                sha256,
                category,
                str(exc),
                severity="warning",
                original_name=original_name,
            )
            raise

        scan = scan_file(temp_path)
        if not scan.clean:
            if scan.infected:
                quarantined = _quarantine_file(temp_path, sha256, extension)
                _reject(
                    db,
                    case,
                    user,
                    sha256,
                    category,
                    "malware detectado; archivo en cuarentena",
                    severity="critical",
                    original_name=original_name,
                )
                write_audit(
                    db,
                    action="document.quarantined",
                    entity="document",
                    username=user.username,
                    user_id=user.id,
                    entity_id=sha256,
                    after={"quarantine_file": quarantined.name},
                )
                raise UploadValidationError("El archivo fue rechazado por el antivirus.")
            _reject(
                db,
                case,
                user,
                sha256,
                category,
                "fallo del antivirus; carga rechazada",
                severity="error",
                original_name=original_name,
            )
            raise UploadValidationError("No se pudo verificar el archivo con el antivirus.")

        existing = db.scalar(select(Document).where(Document.sha256 == sha256))
        if existing is not None:
            linked = db.scalar(
                select(CaseDocument.id).where(
                    CaseDocument.case_id == case.id,
                    CaseDocument.document_id == existing.id,
                )
            )
            if linked is not None:
                write_audit(
                    db,
                    action="document.duplicate",
                    entity="document",
                    username=user.username,
                    user_id=user.id,
                    entity_id=str(existing.id),
                    after={"case_id": case.id, "category": category},
                )
                raise DuplicateDocumentError("El documento ya está vinculado a este caso.")

            _link_into_case(case, category, existing)
            db.add(
                CaseDocument(
                    case_id=case.id,
                    document_id=existing.id,
                    category=category,
                    linked_by_id=user.id,
                )
            )
            db.flush()
            refresh_manifest(db, case)
            write_audit(
                db,
                action="document.link",
                entity="document",
                username=user.username,
                user_id=user.id,
                entity_id=str(existing.id),
                after={"case_id": case.id, "category": category, "sha256": sha256},
            )
            temp_path.unlink(missing_ok=True)
            return existing

        canonical = _canonical_path(sha256, extension)
        if canonical.exists():
            if canonical.is_symlink() or not canonical.is_file():
                raise IntegrityError("El archivo canónico del documento no es válido.")
            temp_path.unlink(missing_ok=True)
        else:
            os.replace(temp_path, canonical)
            canonical.chmod(0o600)

        data_dir = settings.data_dir.expanduser().resolve()
        relative_path = (
            canonical.relative_to(data_dir) if canonical.is_relative_to(data_dir) else canonical
        )
        document = Document(
            sha256=sha256,
            original_name=original_name,
            relative_path=str(relative_path),
            mime_type=ALLOWED_EXTENSIONS[extension],
            extension=extension,
            size_bytes=size,
            uploaded_by_id=user.id,
        )
        db.add(document)
        db.flush()
        _link_into_case(case, category, document)
        db.add(
            CaseDocument(
                case_id=case.id,
                document_id=document.id,
                category=category,
                linked_by_id=user.id,
            )
        )
        db.flush()
        refresh_manifest(db, case)
        write_audit(
            db,
            action="document.upload",
            entity="document",
            username=user.username,
            user_id=user.id,
            entity_id=str(document.id),
            after={
                "case_id": case.id,
                "category": category,
                "sha256": sha256,
                "original_name": original_name,
                "size_bytes": size,
            },
        )
        return document
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
