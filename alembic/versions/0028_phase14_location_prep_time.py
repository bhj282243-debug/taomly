"""phase14_location_prep_time

Phase 14: Add preparation_time_minutes to locations table.

Informational field: estimated kitchen preparation time in minutes.
Used for:
  1. ETA display in customer notifications (accepted).
  2. Scheduled order activation formula:
       activation = scheduled_at - preparation_time_minutes
                  - zone.eta_minutes (delivery only)
                  - ACTIVATION_BUFFER_MINUTES

NULL means preparation time is unknown / not configured.
Activation fallback for NULL: 0 minutes (activate at scheduled_at - BUFFER only).
This is the ONLY approved fallback — no arbitrary default.

Revision ID: 0028
Revises: 0027
Create Date: 2026-01-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "locations",
        sa.Column("preparation_time_minutes", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_loc_prep_time_positive",
        "locations",
        "preparation_time_minutes IS NULL OR preparation_time_minutes > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_loc_prep_time_positive", "locations", type_="check")
    op.drop_column("locations", "preparation_time_minutes")
