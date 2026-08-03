"""Attribution row model."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, TimestampsMixin


class Attribution(Base, TimestampsMixin):
    __tablename__ = "attributions"

    attribution_id: Mapped[UUID] = mapped_column(primary_key=True)
    sweep_id: Mapped[UUID] = mapped_column(nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), nullable=False
    )
    solution_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # Which model and which knobs this layer effect was measured under. A layer
    # that helps a 7B may do nothing on a 32B, and without these the two are
    # indistinguishable by the attribution's own columns.
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    config_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suite: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    layer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    methodology: Mapped[str] = mapped_column(String(20), nullable=False, default="LOO")
    baseline_run_id: Mapped[UUID] = mapped_column(nullable=False)
    ablated_run_id: Mapped[UUID] = mapped_column(nullable=False)

    pass_at_k_baseline: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    pass_at_k_ablated: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    delta_pass_at_k: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    pass_hat_k_baseline: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    pass_hat_k_ablated: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    delta_pass_hat_k: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    token_delta_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    runtime_delta_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    mcnemar_p: Mapped[float] = mapped_column(Numeric, nullable=False)
    bh_adjusted_p: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ci_low: Mapped[float] = mapped_column(Numeric, nullable=False)
    ci_high: Mapped[float] = mapped_column(Numeric, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "methodology IN ('LOO', 'shapley')",
            name="ck_attribution_methodology",
        ),
        UniqueConstraint("sweep_id", "layer_name", name="uq_attribution_sweep_layer"),
        Index("idx_attributions_sweep", "sweep_id"),
        Index("idx_attributions_project_solution", "project_id", "solution_id", "suite"),
    )
