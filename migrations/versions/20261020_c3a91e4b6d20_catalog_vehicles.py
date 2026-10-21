"""workbook catalog: one row per VIN, features beside it

Revision ID: c3a91e4b6d20
Revises: 8b2f1c0d7e31
Create Date: 2026-10-20 07:10:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3a91e4b6d20"
down_revision = "8b2f1c0d7e31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_vehicles",
        sa.Column("vin", sa.String(), nullable=False),
        sa.Column("dealer_name", sa.String(), nullable=False),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("trim", sa.String(), nullable=True),
        sa.Column("trim_code", sa.String(), nullable=False),
        sa.Column("stock", sa.String(), nullable=True),
        sa.Column("exterior", sa.String(), nullable=True),
        sa.Column("interior", sa.String(), nullable=True),
        sa.Column("mileage", sa.Integer(), nullable=True),
        sa.Column("msrp_cents", sa.Integer(), nullable=True),
        sa.Column("advertised_cents", sa.Integer(), nullable=True),
        sa.Column("listing_url", sa.String(), nullable=True),
        sa.Column("as_of", sa.String(), nullable=False),
        sa.Column("prior_dealer", sa.String(), nullable=True),
        sa.Column("packages_text", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("vin", name=op.f("pk_catalog_vehicles")),
    )
    op.create_index("ix_catalog_vehicles_dealer", "catalog_vehicles", ["dealer_name"])
    op.create_index("ix_catalog_vehicles_trim", "catalog_vehicles", ["trim_code"])
    op.create_table(
        "catalog_features",
        sa.Column("vin", sa.String(), nullable=False),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["vin"], ["catalog_vehicles.vin"], name=op.f("fk_catalog_features_vin_catalog_vehicles")
        ),
        sa.PrimaryKeyConstraint("vin", "feature", name=op.f("pk_catalog_features")),
    )


def downgrade() -> None:
    op.drop_table("catalog_features")
    op.drop_index("ix_catalog_vehicles_trim", table_name="catalog_vehicles")
    op.drop_index("ix_catalog_vehicles_dealer", table_name="catalog_vehicles")
    op.drop_table("catalog_vehicles")
