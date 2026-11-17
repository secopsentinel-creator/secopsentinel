from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CaseStatus = Literal["activo", "suspendido", "archivado"]
CasePriority = Literal["alta", "media", "baja"]
ActivityStatus = Literal["pendiente", "en_curso", "completada"]


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    csrf_token: str = Field(min_length=1, max_length=256)


class CaseForm(BaseModel):
    nunc: str = Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z_-]+$")
    ot: str = Field(default="", max_length=64)
    opj: str = Field(default="", max_length=64)
    spoa: str = Field(default="", max_length=64)
    assignment_date: date
    due_date: date | None = None
    status: CaseStatus = "activo"
    priority: CasePriority = "media"
    prosecutor_office: str = Field(default="", max_length=160)
    office_number: str = Field(default="", max_length=64)
    prosecutor_name: str = Field(default="", max_length=160)
    prosecutor_email: str = Field(default="", max_length=255)
    prosecutor_phone: str = Field(default="", max_length=64)
    investigator_name: str = Field(default="", max_length=160)
    cti_group: str = Field(default="", max_length=160)
    management_status: str = Field(default="", max_length=64)
    crime: str = Field(default="", max_length=255)
    article: str = Field(default="", max_length=64)
    municipality: str = Field(default="", max_length=160)
    department: str = Field(default="", max_length=160)

    @field_validator("assignment_date", "due_date", mode="before")
    @classmethod
    def _empty_date(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator(
        "nunc",
        "ot",
        "opj",
        "spoa",
        "prosecutor_office",
        "office_number",
        "prosecutor_name",
        "prosecutor_email",
        "prosecutor_phone",
        "investigator_name",
        "cti_group",
        "management_status",
        "crime",
        "article",
        "municipality",
        "department",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("due_date")
    @classmethod
    def _due_after_assignment(cls, value: date | None, info) -> date | None:
        assignment = info.data.get("assignment_date")
        if value is not None and assignment is not None and value < assignment:
            raise ValueError("La fecha de vencimiento no puede ser anterior a la asignación.")
        return value


class ActivityForm(BaseModel):
    activity: str = Field(min_length=1, max_length=500)
    activity_date: date
    status: ActivityStatus = "pendiente"
    notes: str = Field(default="", max_length=5000)

    @field_validator("activity_date", mode="before")
    @classmethod
    def _empty_date(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    @field_validator("activity", "notes", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value
