from __future__ import annotations

import shutil
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_user, validate_csrf_token, write_audit, write_security_event
from app.core.templating import render_page
from app.db.session import get_db
from app.models import Case, CaseDocument, User
from app.schemas import ActivityForm, CaseForm
from app.services.cases import (
    ACTIVITY_STATUS_LABELS,
    CASE_PRIORITY_LABELS,
    CASE_STATUS_LABELS,
    CATEGORY_LABELS,
    CaseServiceError,
    DuplicateCaseError,
    add_activity,
    case_directory,
    case_form_values,
    case_snapshot,
    create_case,
    get_case,
    list_cases,
    update_case,
)
from app.services.documents import (
    DocumentServiceError,
    IntegrityError,
    resolve_document_path,
    save_document,
)
from app.services.excel import build_cases_workbook
from app.services.pdf_intake import (
    FIELD_LABELS,
    IntakeDraft,
    PdfIntakeError,
    discard_intake_draft,
    inspect_intake_draft,
    resolve_intake_draft,
    stage_pdf_intake,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
router = APIRouter(prefix="/cases")
CASE_FIELDS = tuple(CaseForm.model_fields)
DETAIL_TABS = {"resumen", "documentos", "actividades"}


def _form_values(form_data) -> dict[str, str]:
    return {field: str(form_data.get(field, "")) for field in CASE_FIELDS}


def _friendly_error(error: dict) -> str:
    error_type = error.get("type")
    if error_type == "missing":
        return "Este campo es obligatorio."
    if error_type == "string_pattern_mismatch":
        return "Solo se permiten letras, números, guiones y guiones bajos."
    if error_type in {"string_too_short", "string_too_long", "value_error"}:
        return str(error.get("msg", "Valor inválido.")).replace("Value error, ", "")
    return "Valor inválido."


def _form_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors():
        location = error.get("loc") or ("__form__",)
        field = str(location[0])
        errors[field] = _friendly_error(error)
    return errors


def _render_case_form(
    request: Request,
    user: User,
    values: dict[str, str],
    *,
    errors: dict[str, str] | None = None,
    error: str | None = None,
    case: Case | None = None,
    pdf_intake: dict | None = None,
    status_code: int = 200,
):
    return render_page(
        request,
        "cases/form.html",
        {
            "user": user,
            "values": values,
            "errors": errors or {},
            "error": error,
            "case": case,
            "pdf_intake": pdf_intake,
            "field_labels": FIELD_LABELS,
            "status_labels": CASE_STATUS_LABELS,
            "priority_labels": CASE_PRIORITY_LABELS,
        },
        status_code=status_code,
    )


def _pdf_intake_view(draft: IntakeDraft, *, extraction=None) -> dict:
    result = extraction or inspect_intake_draft(draft)
    return {
        "token": draft.token,
        "filename": draft.original_name,
        "sha256": draft.sha256,
        "sources": result.sources,
        "text_found": result.text_found,
        "field_count": len(result.values),
    }


def _render_case_detail(
    request: Request,
    db: Session,
    case_id: int,
    user: User,
    *,
    tab: str = "resumen",
    error: str | None = None,
    selected_category: str = "denuncia",
    status_code: int = 200,
):
    case = get_case(db, case_id, details=True)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    if tab not in DETAIL_TABS:
        tab = "resumen"
    return render_page(
        request,
        "cases/detail.html",
        {
            "user": user,
            "case": case,
            "active_tab": tab,
            "error": error,
            "selected_category": selected_category,
            "categories": CATEGORY_LABELS,
            "status_labels": CASE_STATUS_LABELS,
            "priority_labels": CASE_PRIORITY_LABELS,
            "activity_status_labels": ACTIVITY_STATUS_LABELS,
            "created": request.query_params.get("created") == "1",
            "updated": request.query_params.get("updated") == "1",
            "uploaded": request.query_params.get("uploaded") == "1",
            "activity_created": request.query_params.get("activity") == "1",
            "today": date.today(),
        },
        status_code=status_code,
    )


@router.get("")
def cases_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    query = request.query_params.get("q", "")
    return render_page(
        request,
        "cases/list.html",
        {
            "user": user,
            "cases": list_cases(db, query),
            "q": query,
            "today": date.today(),
            "status_labels": CASE_STATUS_LABELS,
            "priority_labels": CASE_PRIORITY_LABELS,
        },
    )


@router.post("/intake/preview")
async def preview_case_pdf(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    file: Annotated[UploadFile, File(...)],
    csrf_token: Annotated[str, Form()] = "",
):
    values = case_form_values()
    if not validate_csrf_token(request, csrf_token):
        return _render_case_form(
            request,
            user,
            values,
            error="Solicitud inválida. Recargue la página.",
            status_code=400,
        )

    values["assignment_date"] = ""
    try:
        draft, extraction = await stage_pdf_intake(db, user, file)
    except PdfIntakeError as exc:
        db.commit()
        return _render_case_form(
            request,
            user,
            values,
            error=str(exc),
            status_code=400,
        )

    for field, value in extraction.values.items():
        values[field] = value
    intake_view = _pdf_intake_view(draft, extraction=extraction)
    db.commit()
    return _render_case_form(
        request,
        user,
        values,
        pdf_intake=intake_view,
    )


@router.get("/export.xlsx")
def export_cases(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    cases = list(
        db.scalars(
            select(Case)
            .options(selectinload(Case.activities))
            .order_by(Case.due_date.is_(None), Case.due_date, Case.created_at.desc())
        ).all()
    )
    content = build_cases_workbook(cases)
    write_audit(
        db,
        action="case.export",
        entity="case",
        username=user.username,
        user_id=user.id,
        after={"count": len(cases)},
    )
    db.commit()
    filename = f"secopsentinel-casos-{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/new")
def new_case_page(
    request: Request,
    user: Annotated[User, Depends(require_user)],
):
    return _render_case_form(request, user, case_form_values())


@router.post("")
async def create_case_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    form_data = await request.form()
    values = _form_values(form_data)
    if not validate_csrf_token(request, str(form_data.get("csrf_token", ""))):
        return _render_case_form(
            request,
            user,
            values,
            error="Solicitud inválida. Recargue la página.",
            status_code=400,
        )
    intake_token = str(form_data.get("intake_token", "")).strip()
    intake_draft = None
    intake_view = None
    if intake_token:
        try:
            intake_draft = resolve_intake_draft(intake_token)
            intake_view = _pdf_intake_view(intake_draft)
        except PdfIntakeError as exc:
            return _render_case_form(
                request,
                user,
                values,
                error=str(exc),
                status_code=400,
            )
    try:
        form = CaseForm(**values)
    except ValidationError as exc:
        return _render_case_form(
            request,
            user,
            values,
            errors=_form_errors(exc),
            error="Revise los campos marcados.",
            pdf_intake=intake_view,
            status_code=400,
        )
    created_nunc = None
    try:
        case = create_case(db, user, form)
        created_nunc = case.nunc
        if intake_draft is not None:
            with intake_draft.file_path.open("rb") as source_file:
                upload = UploadFile(filename=intake_draft.original_name, file=source_file)
                await save_document(db, case, user, upload, "documento_recibido")
    except (DuplicateCaseError, CaseServiceError, DocumentServiceError) as exc:
        db.rollback()
        if created_nunc is not None:
            shutil.rmtree(case_directory(created_nunc), ignore_errors=True)
        if intake_draft is not None and isinstance(exc, DocumentServiceError):
            write_security_event(
                db,
                event_type="case_pdf_intake_save_failed",
                severity="error",
                detail="No se pudo vincular el PDF de origen al caso.",
            )
            db.commit()
        return _render_case_form(
            request,
            user,
            values,
            errors={"nunc": str(exc)},
            error=str(exc),
            pdf_intake=intake_view,
            status_code=400,
        )
    after = case_snapshot(case)
    if intake_draft is not None:
        after["creation_source"] = "pdf"
        after["source_pdf_sha256"] = intake_draft.sha256
        after["pdf_fields_detected"] = sorted((intake_view or {}).get("sources", {}))
    write_audit(
        db,
        action="case.create",
        entity="case",
        username=user.username,
        user_id=user.id,
        entity_id=str(case.id),
        after=after,
    )
    db.commit()
    if intake_draft is not None:
        discard_intake_draft(intake_draft)
    return RedirectResponse(f"/cases/{case.id}?created=1", status_code=303)


@router.get("/{case_id}/edit")
def edit_case_page(
    request: Request,
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    case = get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return _render_case_form(request, user, case_form_values(case), case=case)


@router.post("/{case_id}/edit")
async def update_case_page(
    request: Request,
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    case = get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    form_data = await request.form()
    values = _form_values(form_data)
    if not validate_csrf_token(request, str(form_data.get("csrf_token", ""))):
        return _render_case_form(
            request,
            user,
            values,
            error="Solicitud inválida. Recargue la página.",
            case=case,
            status_code=400,
        )
    try:
        form = CaseForm(**values)
    except ValidationError as exc:
        return _render_case_form(
            request,
            user,
            values,
            errors=_form_errors(exc),
            error="Revise los campos marcados.",
            case=case,
            status_code=400,
        )
    before = case_snapshot(case)
    try:
        update_case(db, case, form)
    except CaseServiceError as exc:
        db.rollback()
        return _render_case_form(
            request,
            user,
            values,
            errors={"nunc": str(exc)},
            error=str(exc),
            case=case,
            status_code=400,
        )
    write_audit(
        db,
        action="case.update",
        entity="case",
        username=user.username,
        user_id=user.id,
        entity_id=str(case.id),
        before=before,
        after=case_snapshot(case),
    )
    db.commit()
    return RedirectResponse(f"/cases/{case.id}?updated=1", status_code=303)


@router.get("/{case_id}")
def case_detail_page(
    request: Request,
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    return _render_case_detail(
        request,
        db,
        case_id,
        user,
        tab=request.query_params.get("tab", "resumen"),
    )


@router.post("/{case_id}/documents")
async def upload_document_page(
    request: Request,
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
    file: Annotated[UploadFile, File(...)],
    category: Annotated[str, Form()] = "denuncia",
    csrf_token: Annotated[str, Form()] = "",
):
    case = get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    if not validate_csrf_token(request, csrf_token):
        return _render_case_detail(
            request,
            db,
            case_id,
            user,
            tab="documentos",
            error="Solicitud inválida. Recargue la página.",
            selected_category=category,
            status_code=400,
        )
    try:
        document = await save_document(db, case, user, file, category)
    except DocumentServiceError as exc:
        db.commit()
        return _render_case_detail(
            request,
            db,
            case_id,
            user,
            tab="documentos",
            error=str(exc),
            selected_category=category,
            status_code=400,
        )
    db.commit()
    return RedirectResponse(
        f"/cases/{case_id}?tab=documentos&uploaded=1&document={document.id}",
        status_code=303,
    )


@router.get("/{case_id}/documents/{document_id}")
def download_document(
    case_id: int,
    document_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    link = db.scalar(
        select(CaseDocument)
        .options(selectinload(CaseDocument.document))
        .where(
            CaseDocument.case_id == case_id,
            CaseDocument.document_id == document_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    try:
        path = resolve_document_path(link.document)
    except IntegrityError as exc:
        write_security_event(
            db,
            event_type="document_integrity_error",
            severity="error",
            detail=f"Caso {case_id}, documento {document_id}: {exc}",
        )
        db.commit()
        raise HTTPException(status_code=404, detail="Archivo no disponible.") from exc
    write_audit(
        db,
        action="document.download",
        entity="document",
        username=user.username,
        user_id=user.id,
        entity_id=str(link.document.id),
        after={"case_id": case_id, "sha256": link.document.sha256},
    )
    db.commit()
    return FileResponse(
        path,
        media_type=link.document.mime_type,
        filename=link.document.original_name,
    )


@router.post("/{case_id}/activities")
async def create_activity_page(
    request: Request,
    case_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_user)],
):
    case = get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    form_data = await request.form()
    if not validate_csrf_token(request, str(form_data.get("csrf_token", ""))):
        return _render_case_detail(
            request,
            db,
            case_id,
            user,
            tab="actividades",
            error="Solicitud inválida. Recargue la página.",
            status_code=400,
        )
    try:
        form = ActivityForm(
            activity=str(form_data.get("activity", "")),
            activity_date=str(form_data.get("activity_date", "")),
            status=str(form_data.get("status", "pendiente")),
            notes=str(form_data.get("notes", "")),
        )
    except ValidationError as exc:
        return _render_case_detail(
            request,
            db,
            case_id,
            user,
            tab="actividades",
            error=_friendly_error(exc.errors()[0]),
            status_code=400,
        )
    activity = add_activity(db, case, user, form)
    write_audit(
        db,
        action="case.activity.create",
        entity="case_activity",
        username=user.username,
        user_id=user.id,
        entity_id=str(activity.id),
        after={
            "case_id": case.id,
            "activity": activity.activity,
            "activity_date": activity.activity_date.isoformat(),
            "status": activity.status,
        },
    )
    db.commit()
    return RedirectResponse(
        f"/cases/{case_id}?tab=actividades&activity=1",
        status_code=303,
    )
