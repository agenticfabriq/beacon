"""Convergence-worker tick loop."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from beacon_storage.models.eval_items import EvalItemTier
from beacon_storage.models.provenance import ActorType
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.provenance import ProvenanceRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.verdicts import VerdictRepo

from beacon_workers.convergence.voter import ConvergenceVoter, SutVerdict
from beacon_workers.logging import get_logger
from beacon_workers.watermark import WatermarkManager

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


_WORKER_NAME = "convergence"


class ConvergenceWorker:
    """Promote model-proposed eval items when enough SUTs agree."""

    def __init__(
        self,
        *,
        factory: sessionmaker[Session],
        min_suts: int,
        min_confidence: float,
        batch_size: int,
    ) -> None:
        self.factory = factory
        self.voter = ConvergenceVoter(min_suts=min_suts, min_confidence=min_confidence)
        self.batch_size = batch_size
        self.log = get_logger().bind(worker=_WORKER_NAME)

    def tick_all_teams(self) -> None:
        """Run a convergence tick per team, recording per-team errors on failure."""
        with self.factory() as session:
            team_ids = [team.id for team in TeamRepo(session).list()]

        for team_id in team_ids:
            try:
                self.tick_for_team(team_id=team_id)
            except Exception as exc:
                self.log.error(
                    "convergence.tick.team_failed",
                    team_id=str(team_id),
                    error=repr(exc),
                )
                with self.factory() as session:
                    WatermarkManager(
                        session,
                        worker_name=_WORKER_NAME,
                        team_id=team_id,
                    ).record_error(repr(exc))
                    session.commit()

    def tick_for_team(self, *, team_id: UUID) -> None:
        """Promote MODEL_PROPOSED items whose SUT verdicts cleared the convergence vote."""
        with self.factory() as session:
            watermark = WatermarkManager(
                session,
                worker_name=_WORKER_NAME,
                team_id=team_id,
            )
            results = ResultRepo(session).list_after(
                team_id=team_id,
                after_id=watermark.fetch_since(),
                limit=self.batch_size,
            )
            if not results:
                self.log.info("convergence.tick.no_new_results", team_id=str(team_id))
                return

            item_ids = {
                item_id
                for result in results
                if (item_id := _parse_eval_item_id(result.item_id)) is not None
            }
            promoted_count = 0
            for item_id in item_ids:
                item = EvalItemRepo(session).get_active(item_id)
                if item is None or item.tier != EvalItemTier.MODEL_PROPOSED:
                    continue

                vote = self.voter.vote(
                    self._collect_verdicts(
                        session=session,
                        team_id=team_id,
                        eval_item_id=item_id,
                    )
                )
                if not vote.promotable:
                    continue

                updated = EvalItemRepo(session).update_tier_if_unchanged(
                    item_id=item_id,
                    expected_current_tier=EvalItemTier.MODEL_PROPOSED,
                    new_tier=EvalItemTier.EXECUTION_CONFIRMED,
                    gold_answer=vote.winning_canonical_answer,
                )
                if not updated:
                    continue

                ProvenanceRepo(session).append(
                    item_id=item_id,
                    team_id=team_id,
                    prior_tier=EvalItemTier.MODEL_PROPOSED,
                    new_tier=EvalItemTier.EXECUTION_CONFIRMED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=_WORKER_NAME,
                    created_by=None,
                    reason=f"convergence: {vote.reason}",
                    evidence={
                        "winning_answer_hash": vote.winning_answer_hash,
                        "supporting_sut_count": vote.supporting_sut_count,
                    },
                )
                promoted_count += 1

            watermark.advance(last_processed_id=results[-1].id)
            session.commit()

            self.log.info(
                "convergence.tick.completed",
                team_id=str(team_id),
                results=len(results),
                promoted=promoted_count,
                last_processed_id=str(results[-1].id),
            )

    def _collect_verdicts(
        self,
        *,
        session: Session,
        team_id: UUID,
        eval_item_id: UUID,
    ) -> list[SutVerdict]:
        return [
            SutVerdict(
                sut_id=row.solution_id,
                answer_hash=row.answer_hash,
                canonical_answer=row.canonical_answer,
                confidence=row.confidence,
            )
            for row in VerdictRepo(session).list_for_eval_item(
                team_id=team_id,
                eval_item_id=eval_item_id,
            )
        ]


def _parse_eval_item_id(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None
