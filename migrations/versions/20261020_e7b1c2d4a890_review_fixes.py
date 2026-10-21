"""review fixes: price basis, immutable flags, replay uniqueness

Revision ID: e7b1c2d4a890
Revises: c3a91e4b6d20
Create Date: 2026-10-20 08:30:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b1c2d4a890"
down_revision = "c3a91e4b6d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("observations", sa.Column("price_basis", sa.String(), nullable=True))
    op.create_table(
        "observation_flags",
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("flag", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.observation_id"],
            name=op.f("fk_observation_flags_observation_id_observations"),
        ),
        sa.PrimaryKeyConstraint("observation_id", "flag", name=op.f("pk_observation_flags")),
    )
    # Replay must not insert a second exclusion or review row for the same fact.
    op.execute(
        "DELETE FROM exclusions WHERE exclusion_id NOT IN ("
        " SELECT MIN(exclusion_id) FROM exclusions"
        " GROUP BY run_id, dealer_id, ifnull(vin, ''), url, reason_code)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_exclusions_replay"
        " ON exclusions(run_id, dealer_id, ifnull(vin, ''), url, reason_code)"
    )
    op.execute(
        "DELETE FROM review_items WHERE item_id NOT IN ("
        " SELECT MIN(item_id) FROM review_items"
        " GROUP BY run_id, dealer_id, ifnull(vin, ''), issue)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_items_replay"
        " ON review_items(run_id, dealer_id, ifnull(vin, ''), issue)"
    )
    op.execute(
        "DELETE FROM fetch_attempts WHERE fetch_attempt_id NOT IN ("
        " SELECT MIN(fetch_attempt_id) FROM fetch_attempts"
        " GROUP BY run_id, dealer_id, url, attempt, renderer)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fetch_attempts_replay"
        " ON fetch_attempts(run_id, dealer_id, url, attempt, renderer)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_fetch_attempts_replay")
    op.execute("DROP INDEX IF EXISTS uq_review_items_replay")
    op.execute("DROP INDEX IF EXISTS uq_exclusions_replay")
    op.drop_table("observation_flags")
    op.drop_column("observations", "price_basis")
