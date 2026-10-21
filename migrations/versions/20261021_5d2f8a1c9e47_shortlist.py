"""shortlist: saved cars, saved searches, search matches, color families

Revision ID: 5d2f8a1c9e47
Revises: a91c4e2b7f08
Create Date: 2026-10-21 09:00:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "5d2f8a1c9e47"
down_revision = "a91c4e2b7f08"
branch_labels = None
depends_on = None

# Frozen copy of reference/data.py families-v1 (requirements-shortlist.md §5.4).
_VERSION = "families-v1"
_FAMILIES = {
    "exterior": {
        "C4W": "Grey", "C4P": "Grey", "C36": "Grey", "C5A": "Grey",
        "300": "White", "A96": "White",
        "416": "Black", "475": "Black",
        "C3Z": "Blue", "C35": "Blue",
        "C3D": "Green",
    },
    "interior": {
        "KPA9": "Light", "VAEW": "Light",
        "KPHF": "Brown", "KPRI": "Brown", "VAHF": "Brown", "VATQ": "Brown",
        "KPSW": "Black", "VASW": "Black",
    },
}  # fmt: skip


def upgrade() -> None:
    op.create_table(
        "saved_cars",
        sa.Column("saved_car_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vin", sa.String(), sa.ForeignKey("units.vin"), nullable=False),
        sa.Column("saved_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column(
            "snapshot_observation_id",
            sa.Integer(),
            sa.ForeignKey("observations.observation_id"),
            nullable=False,
        ),
        sa.Column("removed_at", sa.String()),
    )
    op.create_index("ix_saved_cars_vin", "saved_cars", ["vin"])
    op.create_index(
        "ux_saved_cars_active_vin",
        "saved_cars",
        ["vin"],
        unique=True,
        sqlite_where=sa.text("removed_at IS NULL"),
    )
    op.create_table(
        "saved_searches",
        sa.Column("search_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_vin", sa.String()),
        sa.Column("criteria_json", sa.Text(), nullable=False),
        sa.Column("criteria_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("alerts", sa.Boolean(), nullable=False),
        sa.Column("removed_at", sa.String()),
        sa.Column("baseline_at", sa.String()),
    )
    op.create_table(
        "search_matches",
        sa.Column("search_match_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "search_id", sa.Integer(), sa.ForeignKey("saved_searches.search_id"), nullable=False
        ),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("vin", sa.String(), nullable=False),
        sa.Column("dealer_id", sa.String(), sa.ForeignKey("dealers.dealer_id"), nullable=False),
        sa.Column(
            "observation_id",
            sa.Integer(),
            sa.ForeignKey("observations.observation_id"),
            nullable=False,
        ),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("is_new", sa.Boolean(), nullable=False),
        sa.Column("became_full_match", sa.Boolean(), nullable=False),
        sa.Column("evaluated_at", sa.String(), nullable=False),
        sa.Column("baseline", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("search_id", "run_id", "vin", name="uq_search_matches_search_run_vin"),
    )
    op.create_index("ix_search_matches_search_vin", "search_matches", ["search_id", "vin"])
    op.create_index("ix_search_matches_run", "search_matches", ["run_id"])
    families = op.create_table(
        "color_families",
        sa.Column("kind", sa.String(), primary_key=True),
        sa.Column("code_or_raw", sa.String(), primary_key=True),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("reference_version", sa.String(), nullable=False),
    )
    op.bulk_insert(
        families,
        [
            {"kind": kind, "code_or_raw": code, "family": fam, "reference_version": _VERSION}
            for kind, members in _FAMILIES.items()
            for code, fam in members.items()
        ],
    )


def downgrade() -> None:
    op.drop_table("color_families")
    op.drop_index("ix_search_matches_run", "search_matches")
    op.drop_index("ix_search_matches_search_vin", "search_matches")
    op.drop_table("search_matches")
    op.drop_table("saved_searches")
    op.drop_index("ux_saved_cars_active_vin", "saved_cars")
    op.drop_index("ix_saved_cars_vin", "saved_cars")
    op.drop_table("saved_cars")
