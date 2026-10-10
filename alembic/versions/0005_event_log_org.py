"""Event-log tenancy (python-review follow-up).

Adds event_log.org_id so webhook fan-out and GET /webhooks/events are
scoped per organization instead of fleet-global.

Fresh databases get the column via create_all; pre-existing databases are
covered by _bootstrap_tenancy (event_log is in _TENANT_TABLES); alembic
users get this revision.

Revision ID: 0005_event_log_org
Revises: 0004_copilot
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_event_log_org"
down_revision = "0004_copilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE event_log ADD COLUMN IF NOT EXISTS org_id VARCHAR DEFAULT 'org-default'"
    )
    op.execute("UPDATE event_log SET org_id = 'org-default' WHERE org_id IS NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_event_log_org_id ON event_log (org_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_event_log_org_id")
    op.execute("ALTER TABLE event_log DROP COLUMN IF EXISTS org_id")
