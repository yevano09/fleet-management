"""Baseline marker (P-ret-1).

Historical schema is owned by create_all + database.py bootstraps; this empty
revision exists so future upgrades have an anchor. Fresh databases: create_all
then `alembic stamp head`. Existing databases: `alembic stamp head` directly
(their schema already matches models.py via bootstraps).

Revision ID: 0001_baseline
Revises: None
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
