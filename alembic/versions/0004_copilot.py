"""Copilot sessions + RAG-lite service notes (SRS Idea 5, M2.1).

Revision ID: 0004_copilot
Revises: 0003_cargo
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_copilot"
down_revision = "0003_cargo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(), default="runbook"),
        sa.Column("org_id", sa.String(), default="org-default", index=True),
    )
    op.create_table(
        "copilot_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_email", sa.String(), nullable=False, index=True),
        sa.Column("role", sa.String(), default="operator"),
        sa.Column("org_id", sa.String(), default="org-default", index=True),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("copilot_sessions.id"), nullable=False, index=True),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("raw_sha", sa.String(), nullable=True),
        sa.Column("tools_used", sa.Text(), default="[]"),
        sa.Column("provider", sa.String(), default="mock"),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), index=True),
    )


def downgrade() -> None:
    op.drop_table("copilot_messages")
    op.drop_table("copilot_sessions")
    op.drop_table("service_notes")
