"""registry v1.3: locator provenance on dealers, registry_snapshots

Revision ID: 8b2f1c0d7e31
Revises: 44a0d1b83d4c
Create Date: 2026-10-20 00:10:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "8b2f1c0d7e31"
down_revision = "44a0d1b83d4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dealers") as b:
        b.add_column(sa.Column("locator_url", sa.String(), nullable=True))
        b.add_column(sa.Column("discovery_reason", sa.String(), nullable=True))
        b.add_column(sa.Column("discovered_at", sa.String(), nullable=True))
        b.add_column(sa.Column("source", sa.String(), nullable=False, server_default="locator"))
    op.create_table(
        "registry_snapshots",
        sa.Column("snapshot_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fetched_at", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=True),
        sa.Column("dealer_count", sa.Integer(), nullable=False),
        sa.Column("total_seen", sa.Integer(), nullable=False),
        sa.Column("registry_version", sa.String(), nullable=False),
        sa.Column("applied_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_registry_snapshots")),
    )


def downgrade() -> None:
    op.drop_table("registry_snapshots")
    with op.batch_alter_table("dealers") as b:
        b.drop_column("source")
        b.drop_column("discovered_at")
        b.drop_column("discovery_reason")
        b.drop_column("locator_url")
