"""GateService -- PR_GATE harness orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from beacon_ablation.metrics import (
    per_task_pass_at_k,
    suite_pass_at_k,
    suite_pass_hat_k,
)
from beacon_ablation.stats import benjamini_hochberg, mcnemar_exact
from beacon_graders.composer import VerdictComposer
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_storage.ids import uuid7
from beacon_storage.models.runs import HarnessMode, Result, Run
from beacon_storage.repository.eval_items import EvalItemRepo
from beacon_storage.repository.projects import ProjectRepo
from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.runs import RunRepo
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from beacon_runner.errors import BeaconRunnerError
from beacon_runner.harness import HarnessRunner
from beacon_runner.registry import SutRegistry, default_registry
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.orm import Session

K_GATE = 3
DEFAULT_PASS_HAT_3_DROP_THRESHOLD = 0.02
DEFAULT_MCNEMAR_ALPHA = 0.05


class BaselineRunNotConfigured(BeaconRunnerError):
    code = "baseline_run_not_configured"


class NotACuratedSubsetError(BeaconRunnerError):
    code = "not_a_curated_subset"


@dataclass(frozen=True)
class GateResult:
    outcome: Literal["PASS", "FAIL"]
    delta_pass_at_3: float
    delta_pass_hat_3: float
    mcnemar_p: float
    bh_adjusted_p: float
    new_run_id: UUID
    baseline_run_id: UUID
    reason: str


class GateService:
    """Orchestrates the PR_GATE flow. Stateless; one instance per request."""

    def __init__(
        self,
        session: Session,
        *,
        registry: SutRegistry | None = None,
        composer: VerdictComposer | None = None,
        max_workers: int = 4,
    ) -> None:
        self.session = session
        self.registry = registry if registry is not None else default_registry()
        self.composer = (
            composer if composer is not None else VerdictComposer(graders=[DabstepAnswerMatcher()])
        )
        self.max_workers = max_workers

    def run_gate(
        self,
        *,
        project_id: UUID,
        sut_id: UUID,
        suite_id: UUID,
        commit_sha: str,
        baseline_run_id: UUID | None = None,
    ) -> GateResult:
        """Execute one PR_GATE cycle and return the gate verdict."""
        project = ProjectRepo(self.session).get(project_id)
        if project is None:
            raise ValueError(f"project {project_id} not found")

        resolved_baseline_id = baseline_run_id or project.baseline_run_id
        if resolved_baseline_id is None:
            raise BaselineRunNotConfigured(f"project {project_id} has no baseline run configured")

        baseline_run = RunRepo(self.session).get(resolved_baseline_id)
        if baseline_run is None:
            raise BaselineRunNotConfigured(f"baseline run {resolved_baseline_id} not found")

        suite = SuiteRepo(self.session).get(suite_id)
        if suite is None or suite.project_id != project_id:
            raise NotACuratedSubsetError(
                f"suite {suite_id} is not attached to project {project_id}"
            )
        self._validate_curated_suite(suite.suite_metadata)

        solution = SolutionRepo(self.session).get(sut_id)
        if solution is None:
            raise ValueError(f"solution {sut_id} not found")
        if solution.team_id != project.team_id:
            raise ValueError(f"solution {sut_id} is not registered for project team")

        item_ids = SuiteRepo(self.session).list_item_ids(suite_id)
        items = self._load_items(item_ids)
        item_id_strings = {str(item_id) for item_id in item_ids}

        new_run_ids = self._run_pr_gate_attempts(
            team_id=project.team_id,
            project_id=project.id,
            user_id=solution.created_by,
            solution_record_id=solution.id,
            model_id=solution.solution_id,
            suite=suite.name,
            dataset_version=baseline_run.dataset_version,
            commit_sha=commit_sha,
            items=items,
        )
        baseline_runs = self._load_run_group(baseline_run, k=K_GATE)

        baseline_results = self._results_for_runs(baseline_runs, item_id_strings)
        new_results = self._results_for_run_ids(new_run_ids, item_id_strings)

        baseline_pass_at_3 = suite_pass_at_k(baseline_results, k=K_GATE)
        new_pass_at_3 = suite_pass_at_k(new_results, k=K_GATE)
        baseline_pass_hat_3 = suite_pass_hat_k(baseline_results, k=K_GATE)
        new_pass_hat_3 = suite_pass_hat_k(new_results, k=K_GATE)

        baseline_outcomes = per_task_pass_at_k(baseline_results, k=K_GATE)
        new_outcomes = per_task_pass_at_k(new_results, k=K_GATE)
        p_value = mcnemar_exact(baseline_outcomes, new_outcomes)
        adjusted_p = benjamini_hochberg([p_value])[0]

        if (
            baseline_pass_at_3 is None
            or new_pass_at_3 is None
            or baseline_pass_hat_3 is None
            or new_pass_hat_3 is None
        ):
            # No gradeable task on one side, so there is nothing to compare. The
            # rates used to come back as 0.0 here, making both deltas 0.0 and
            # every block condition false -- the gate passed a change on zero
            # evaluation data. A gate that cannot judge must not approve.
            return GateResult(
                outcome="FAIL",
                delta_pass_at_3=0.0,
                delta_pass_hat_3=0.0,
                mcnemar_p=p_value,
                bh_adjusted_p=adjusted_p,
                new_run_id=new_run_ids[0],
                baseline_run_id=baseline_run.id,
                reason=(
                    "no gradeable results to compare "
                    f"(baseline pass@{K_GATE}={baseline_pass_at_3}, "
                    f"new pass@{K_GATE}={new_pass_at_3}); refusing to judge"
                ),
            )

        delta_pass_at_3 = new_pass_at_3 - baseline_pass_at_3
        delta_pass_hat_3 = new_pass_hat_3 - baseline_pass_hat_3
        outcome, reason = self._decide(
            project.gate_policy or {},
            delta_pass_at_3=delta_pass_at_3,
            delta_pass_hat_3=delta_pass_hat_3,
            adjusted_p=adjusted_p,
        )
        return GateResult(
            outcome=outcome,
            delta_pass_at_3=delta_pass_at_3,
            delta_pass_hat_3=delta_pass_hat_3,
            mcnemar_p=p_value,
            bh_adjusted_p=adjusted_p,
            new_run_id=new_run_ids[0],
            baseline_run_id=baseline_run.id,
            reason=reason,
        )

    def _load_items(self, item_ids: Sequence[UUID]) -> list[EvalItem]:
        repo = EvalItemRepo(self.session)
        items: list[EvalItem] = []
        for item_id in item_ids:
            item = repo.get_active(item_id)
            if item is None:
                raise NotACuratedSubsetError(f"suite references inactive item {item_id}")
            items.append(
                EvalItem(
                    item_id=str(item.item_id),
                    suite=item.suite,
                    query=item.item_input,
                    ground_truth=item.gold_answer,
                    metadata=item.item_metadata,
                )
            )
        return items

    def _run_pr_gate_attempts(
        self,
        *,
        team_id: UUID,
        project_id: UUID,
        user_id: UUID,
        solution_record_id: UUID,
        model_id: str,
        suite: str,
        dataset_version: str,
        commit_sha: str,
        items: Sequence[EvalItem],
    ) -> list[UUID]:
        bind = self.session.get_bind()
        factory = sessionmaker(bind=bind, expire_on_commit=False, future=True)
        runner = HarnessRunner(
            session_factory=factory,
            registry=self.registry,
            composer=self.composer,
            max_workers=self.max_workers,
        )
        gate_group_id = uuid7()
        run_ids: list[UUID] = []
        for pass_idx in range(K_GATE):
            config = SolutionConfig(
                model_id=model_id,
                prompt_version=commit_sha,
                extras={"commit_sha": commit_sha, "gate_pass_idx": pass_idx},
            )
            run_ids.append(
                runner.run_single(
                    team_id=team_id,
                    project_id=project_id,
                    user_id=user_id,
                    solution_record_id=solution_record_id,
                    items=items,
                    config=config,
                    suite=suite,
                    dataset_version=dataset_version,
                    pass_idx=pass_idx,
                    mode=HarnessMode.PR_GATE,
                    parent_sweep_id=gate_group_id,
                )
            )
        return run_ids

    def _load_run_group(self, baseline_run: Run, *, k: int) -> list[Run]:
        stmt = (
            select(Run)
            .where(
                Run.project_id == baseline_run.project_id,
                Run.solution_id == baseline_run.solution_id,
                Run.suite == baseline_run.suite,
                Run.dataset_version == baseline_run.dataset_version,
                Run.mode == baseline_run.mode,
                Run.pass_idx < k,
            )
            .order_by(Run.pass_idx)
        )
        if baseline_run.parent_sweep_id is None:
            stmt = stmt.where(Run.parent_sweep_id.is_(None))
        else:
            stmt = stmt.where(Run.parent_sweep_id == baseline_run.parent_sweep_id)
        runs_by_pass_idx = {run.pass_idx: run for run in self.session.scalars(stmt)}
        return [runs_by_pass_idx[idx] for idx in range(k) if idx in runs_by_pass_idx]

    def _results_for_runs(self, runs: Sequence[Run], item_ids: set[str]) -> list[Result]:
        return self._results_for_run_ids([run.id for run in runs], item_ids)

    def _results_for_run_ids(self, run_ids: Sequence[UUID], item_ids: set[str]) -> list[Result]:
        results: list[Result] = []
        repo = ResultRepo(self.session)
        for run_id in run_ids:
            results.extend(
                result for result in repo.list_for_run(run_id) if result.item_id in item_ids
            )
        return results

    def _decide(
        self,
        policy: dict[str, object],
        *,
        delta_pass_at_3: float,
        delta_pass_hat_3: float,
        adjusted_p: float,
    ) -> tuple[Literal["PASS", "FAIL"], str]:
        pass_hat_threshold = self._policy_float(
            policy,
            "pass_hat_3_drop_threshold",
            DEFAULT_PASS_HAT_3_DROP_THRESHOLD,
        )
        mcnemar_alpha = self._policy_float(policy, "mcnemar_alpha", DEFAULT_MCNEMAR_ALPHA)
        if delta_pass_hat_3 < -pass_hat_threshold:
            return (
                "FAIL",
                f"pass^3 dropped by {abs(delta_pass_hat_3):.3f}, above threshold "
                f"{pass_hat_threshold:.3f}",
            )
        if adjusted_p < mcnemar_alpha and delta_pass_at_3 < 0.0:
            return (
                "FAIL",
                f"McNemar BH-adjusted p={adjusted_p:.4f} with negative pass@3 delta "
                f"{delta_pass_at_3:.3f}",
            )
        return "PASS", "within PR_GATE policy band"

    def _policy_float(self, policy: dict[str, object], key: str, default: float) -> float:
        value = policy.get(key, default)
        if isinstance(value, int | float):
            return float(value)
        return default

    def _validate_curated_suite(self, suite_metadata: dict[str, object]) -> None:
        subset_tag = suite_metadata.get("subset_tag")
        if not isinstance(subset_tag, str) or not subset_tag.startswith("curated_50_"):
            raise NotACuratedSubsetError("PR_GATE requires a curated_50_* suite")
