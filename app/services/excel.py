from __future__ import annotations

from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models import Case
from app.services.cases import CASE_PRIORITY_LABELS, CASE_STATUS_LABELS

CONTROL_HEADERS = [
    "OT",
    "NUNC",
    "SPOA",
    "OPJ",
    "Delito",
    "Artículo",
    "Fiscal",
    "Despacho",
    "Investigador",
    "Fecha de asignación",
    "Fecha de vencimiento",
    "Días restantes",
    "Estado",
    "Prioridad",
    "Municipio",
    "Departamento",
]
ACTIVITY_HEADERS = ["OT", "NUNC", "Actividad", "Fecha", "Estado"]


def _style_header(worksheet, headers: list[str]) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    for column, header in enumerate(headers, start=1):
        cell = worksheet.cell(row=1, column=column, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        width = max(12, min(42, len(header) + 4))
        worksheet.column_dimensions[get_column_letter(column)].width = width
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def build_cases_workbook(cases: list[Case], *, today: date | None = None) -> bytes:
    today = today or date.today()
    workbook = Workbook()
    control = workbook.active
    control.title = "Control General de OT"
    activities = workbook.create_sheet("Actividades")

    _style_header(control, CONTROL_HEADERS)
    for row, case in enumerate(cases, start=2):
        remaining = (case.due_date - today).days if case.due_date else None
        values = [
            case.ot,
            case.nunc,
            case.spoa,
            case.opj,
            case.crime,
            case.article,
            case.prosecutor_name,
            case.office_number,
            case.investigator_name,
            case.assignment_date,
            case.due_date,
            remaining,
            CASE_STATUS_LABELS.get(case.status, case.status),
            CASE_PRIORITY_LABELS.get(case.priority, case.priority),
            case.municipality,
            case.department,
        ]
        for column, value in enumerate(values, start=1):
            control.cell(row=row, column=column, value=value)

    _style_header(activities, ACTIVITY_HEADERS)
    row = 2
    for case in cases:
        for activity in sorted(case.activities, key=lambda item: item.activity_date):
            values = [
                case.ot,
                case.nunc,
                activity.activity,
                activity.activity_date,
                activity.status,
            ]
            for column, value in enumerate(values, start=1):
                activities.cell(row=row, column=column, value=value)
            row += 1

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
