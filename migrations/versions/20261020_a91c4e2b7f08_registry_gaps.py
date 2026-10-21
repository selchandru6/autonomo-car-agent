"""registry gaps: homepage failure streak, drop center-less dealers

Revision ID: a91c4e2b7f08
Revises: e7b1c2d4a890
Create Date: 2026-10-20 09:10:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a91c4e2b7f08"
down_revision = "e7b1c2d4a890"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dealers",
        sa.Column("discovery_fail_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    # G3: locator rows are keyed by center. The v1.2 Sewell seeds have none and no history.
    from carshop.storage.repos import retire_centerless_dealers

    retire_centerless_dealers(op.get_bind())


def downgrade() -> None:
    op.drop_column("dealers", "discovery_fail_streak")
