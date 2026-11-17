"""cases, activities, documents, case links and document pages

Revision ID: 0002_cases_documents
Revises: 0001_initial_auth
Create Date: 2026-11-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_cases_documents"
down_revision: str | None = "0001_initial_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nunc", sa.String(length=64), nullable=False),
        sa.Column("ot", sa.String(length=64), nullable=False),
        sa.Column("opj", sa.String(length=64), nullable=False),
        sa.Column("spoa", sa.String(length=64), nullable=False),
        sa.Column("assignment_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("prosecutor_office", sa.String(length=160), nullable=False),
        sa.Column("office_number", sa.String(length=64), nullable=False),
        sa.Column("prosecutor_name", sa.String(length=160), nullable=False),
        sa.Column("prosecutor_email", sa.String(length=255), nullable=False),
        sa.Column("prosecutor_phone", sa.String(length=64), nullable=False),
        sa.Column("investigator_name", sa.String(length=160), nullable=False),
        sa.Column("cti_group", sa.String(length=160), nullable=False),
        sa.Column("management_status", sa.String(length=64), nullable=False),
        sa.Column("crime", sa.String(length=255), nullable=False),
        sa.Column("article", sa.String(length=64), nullable=False),
        sa.Column("municipality", sa.String(length=160), nullable=False),
        sa.Column("department", sa.String(length=160), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cases_nunc", "cases", ["nunc"], unique=True)
    op.create_index("ix_cases_ot", "cases", ["ot"])
    op.create_index("ix_cases_due_date", "cases", ["due_date"])
    op.create_index("ix_cases_status", "cases", ["status"])
    op.create_index("ix_cases_priority", "cases", ["priority"])
    op.create_index("ix_cases_created_by_id", "cases", ["created_by_id"])
    op.create_index("ix_cases_created_at", "cases", ["created_at"])

    op.create_table(
        "case_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("activity", sa.String(length=500), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_case_activities_case_id", "case_activities", ["case_id"])
    op.create_index("ix_case_activities_activity_date", "case_activities", ["activity_date"])
    op.create_index("ix_case_activities_status", "case_activities", ["status"])
    op.create_index("ix_case_activities_created_by_id", "case_activities", ["created_by_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_sha256", "documents", ["sha256"], unique=True)
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_uploaded_by_id", "documents", ["uploaded_by_id"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])

    op.create_table(
        "case_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("linked_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "linked_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["linked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "document_id",
            name="uq_case_documents_case_document",
        ),
    )
    op.create_index("ix_case_documents_case_id", "case_documents", ["case_id"])
    op.create_index("ix_case_documents_document_id", "case_documents", ["document_id"])
    op.create_index("ix_case_documents_category", "case_documents", ["category"])
    op.create_index("ix_case_documents_linked_by_id", "case_documents", ["linked_by_id"])

    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "page_number",
            name="uq_document_pages_document_page",
        ),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])


def downgrade() -> None:
    op.drop_table("document_pages")
    op.drop_table("case_documents")
    op.drop_table("documents")
    op.drop_table("case_activities")
    op.drop_table("cases")
