"""Attribution row model."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from beacon_storage.models.base import Base, TimestampsMixin


class Attribution(Base, TimestampsMixin):
    __tablename__ = "attributions"

    attribution_id: Mapped[UUID] = mapped_column(primary_key=True)
    sweep_id: Mapped[UUID] = mapped_column(nullable=False)
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

    # What the delta was measured over, and what it lost. An ERROR is infra
    # failing rather than the layer, so those attempts leave the comparison --
    # right, but it shrinks the denominator, and an ablation that breaks the
    # endpoint would otherwise report a confident delta over a sample it
    # quietly halved. Asymmetry is the sharper risk and needs both arms: a
    # layer removed from a working config can fail in ways the baseline never
    # does. `n_compared` is the paired intersection the statistics actually
    # used, which is never larger than either arm.
    #
    # NULLABLE because a row written before this existed genuinely does not
    # know its counts. Backfilling 0 would say "nothing was excluded", which
    # is a measurement nobody took -- the same reason `tokens_input` became
    # nullable rather than defaulting.
    #
    # Headline k only, matching `ci_low` and `mcnemar_p`; every k's count is in
    # `delta_pass_at_k` beside the delta it belongs to.
    n_items_submitted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_baseline_excluded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_ablated_excluded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_compared: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
        # Each arm's tasks come from the submitted items and the compared set
        # is their intersection, so neither can exceed the submission. Paired
        # with the non-negative bound below, which is the one an INVERTED
        # subtraction crosses -- that yields a negative, and a negative count
        # of excluded items satisfies "<= submitted" perfectly well.
        CheckConstraint(
            "n_items_submitted IS NULL OR ("
            "  (n_compared IS NULL OR n_compared <= n_items_submitted)"
            "  AND (n_baseline_excluded IS NULL OR n_baseline_excluded <= n_items_submitted)"
            "  AND (n_ablated_excluded IS NULL OR n_ablated_excluded <= n_items_submitted)"
            ")",
            name="ck_attribution_sample_within_submission",
        ),
        CheckConstraint(
            "(n_items_submitted IS NULL OR n_items_submitted >= 0)"
            " AND (n_compared IS NULL OR n_compared >= 0)"
            " AND (n_baseline_excluded IS NULL OR n_baseline_excluded >= 0)"
            " AND (n_ablated_excluded IS NULL OR n_ablated_excluded >= 0)",
            name="ck_attribution_sample_non_negative",
        ),
        UniqueConstraint("sweep_id", "layer_name", name="uq_attribution_sweep_layer"),
        Index("idx_attributions_sweep", "sweep_id"),
        Index("idx_attributions_team_solution", "team_id", "solution_id", "suite"),
    )
