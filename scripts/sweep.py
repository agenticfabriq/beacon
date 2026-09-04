"""Run documented validation sweeps from the command line."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from beacon_ablation.engine import AttributionEngine
from beacon_runner.dummy_sut import DummySUT
from beacon_runner.types import EvalItem, SolutionConfig
from beacon_storage.db import make_engine, make_session_factory
from beacon_storage.ids import uuid7, uuid7_str
from beacon_storage.repository.solutions import SolutionRepo
from beacon_storage.repository.suites import SuiteRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from beacon_runner.sut import SolutionUnderTest


RECOVERY_ITEM_IDS = (
    "recovery-1",
    "recovery-5",
    "recovery-6",
    "recovery-7",
    "recovery-9",
    "recovery-10",
    "recovery-13",
    "recovery-17",
    "recovery-20",
    "recovery-25",
    "recovery-27",
    "recovery-28",
    "recovery-50",
    "recovery-53",
    "recovery-55",
    "recovery-59",
    "recovery-61",
    "recovery-64",
    "recovery-66",
    "recovery-70",
    "recovery-74",
    "recovery-78",
    "recovery-79",
    "recovery-82",
    "recovery-90",
    "recovery-12",
    "recovery-16",
    "recovery-32",
    "recovery-33",
    "recovery-2",
    "recovery-3",
    "recovery-18",
    "recovery-26",
    "recovery-34",
    "recovery-40",
    "recovery-46",
    "recovery-30",
    "recovery-14",
    "recovery-19",
    "recovery-22",
    "recovery-23",
    "recovery-41",
    "recovery-56",
    "recovery-68",
    "recovery-80",
    "recovery-105",
    "recovery-138",
    "recovery-154",
    "recovery-163",
    "recovery-176",
)


@dataclass(frozen=True)
class SweepResult:
    item_id: str
    attempt_idx: int
    outcome: str
    # None where the SUT did not measure cost; median_total_tokens skips those
    # rather than averaging them in as zeros.
    tokens_input: int | None
    tokens_output: int | None
    runtime_ms: int


def _per_k(entry: object, field_name: str) -> float:
    """Read one field out of a JSONB per-k attribution entry."""
    if isinstance(entry, Mapping):
        value = entry.get(field_name)
        if isinstance(value, int | float):
            return float(value)
    return 0.0


def _per_k_count(entry: object, field_name: str) -> int | None:
    """Read a COUNT out of a per-k entry, as an int or as absent.

    Separate from ``_per_k`` because that returns ``float`` and falls back to
    ``0.0``. For a count both halves are wrong: it prints ``2.0`` beside the
    integer columns, and a missing value renders as ``0.0`` -- the same value
    that means "nothing was compared". None keeps absent distinguishable from
    zero, which is the whole point of recording these.
    """
    if isinstance(entry, Mapping):
        value = entry.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


@dataclass
class FakeSweepRunner:
    """Minimal runner adapter for deterministic DummySUT recovery validation.

    Typed against the ablation port rather than beacon's concrete types so
    mypy checks the conformance here -- this fake was the port's only
    implementation, and nothing verified it while scripts/ went unchecked.
    """

    _results_by_run: dict[UUID, list[SweepResult]] = field(default_factory=dict)

    def run_single(
        self,
        *,
        sut: object,
        config: object,
        items: Sequence[object],
        suite: str,
        dataset_version: str,
        mode: str,
        pass_idx: int,
        parent_sweep_id: UUID,
        suite_id: UUID,
        team_id: UUID,
        solution_id: UUID,
        sweep_arm: str,
    ) -> UUID:
        _ = (
            suite,
            dataset_version,
            mode,
            parent_sweep_id,
            suite_id,
            team_id,
            solution_id,
            sweep_arm,
        )
        run_id = uuid7()
        rows: list[SweepResult] = []
        typed_sut = cast("SolutionUnderTest", sut)
        typed_config = cast("SolutionConfig", config)
        for item in cast("Sequence[EvalItem]", items):
            result = typed_sut.invoke(item, typed_config)
            passed = bool(result.output.get("passed"))
            rows.append(
                SweepResult(
                    item_id=item.item_id,
                    attempt_idx=pass_idx,
                    outcome="PASS" if passed else "FAIL",
                    tokens_input=result.tokens_input,
                    tokens_output=result.tokens_output,
                    runtime_ms=result.runtime_ms,
                )
            )
        self._results_by_run[run_id] = rows
        return run_id

    def list_results_for_run(self, run_id: UUID) -> list[SweepResult]:
        return self._results_by_run[run_id]


def _database_url(args: argparse.Namespace) -> str:
    return str(
        args.database_url
        or os.environ.get("DATABASE_URL")
        or "postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon"
    )


def _items(count: int, suite: str) -> list[EvalItem]:
    ids = list(RECOVERY_ITEM_IDS)
    while len(ids) < count:
        ids.append(f"recovery-extra-{len(ids) + 1}")
    return [
        EvalItem(
            item_id=item_id,
            suite=suite,
            query={"question": f"q-{idx}"},
            ground_truth={"answer": "yes"},
            metadata={},
        )
        for idx, item_id in enumerate(ids[:count])
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Beacon validation attribution sweeps.")
    parser.add_argument("--sut", choices=["dummy"], required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--pass-num", type=int, required=True)
    parser.add_argument("--tasks", type=int, required=True)
    parser.add_argument("--mode", choices=["NIGHTLY_LOO"], required=True)
    parser.add_argument("--database-url", default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.pass_num < 1:
        raise SystemExit("--pass-num must be >= 1")
    if args.tasks < 1:
        raise SystemExit("--tasks must be >= 1")

    engine = make_engine(_database_url(args))
    try:
        factory = make_session_factory(engine)
        with factory() as session:
            suffix = uuid7_str()[:8]
            user = UserRepo(session).create(
                email=f"validation-sweep-{suffix}@example.com",
                name="Validation Sweep",
            )
            team = TeamRepo(session).create(name=f"validation-sweep-{suffix}")
            suite_row = SuiteRepo(session).create(
                team_id=team.id,
                name=f"validation-sweep-{suffix}",
                description="",
                method="manual",
                suite_metadata={},
                created_by=user.id,
            )
            sut = DummySUT(owner_team_id=team.id)
            solution = SolutionRepo(session).create(
                team_id=team.id,
                solution_id="dummy",
                version=DummySUT.VERSION,
                owner_team=team.id,
                summary="Validation DummySUT",
                supported_modes=["EVAL", "NIGHTLY_LOO"],
                layers=[layer.model_dump(mode="json") for layer in sut.layers()],
                created_by=user.id,
            )

            attributions = AttributionEngine(session).sweep(
                sut=sut,
                base_config=SolutionConfig(
                    model_id="dummy-m",
                    prompt_version="recovery-v0",
                    layers_enabled={"ontology": True, "retry_loop": True},
                ),
                items=_items(args.tasks, args.suite),
                suite=args.suite,
                dataset_version="validation-v1",
                K=args.pass_num,
                suite_id=suite_row.id,
                team_id=team.id,
                solution_id=solution.id,
                harness_runner=FakeSweepRunner(),
            )
            session.commit()

            headline_key = str(min(3, args.pass_num))
            payload = {
                "sweep_id": str(attributions[0].sweep_id) if attributions else None,
                "attribution_rows": len(attributions),
                "tasks": args.tasks,
                "pass_num": args.pass_num,
                "layers": {
                    row.layer_name: {
                        "delta_pass_at_k": _per_k(row.delta_pass_at_k.get(headline_key), "delta"),
                        "ci_low": _per_k(row.delta_pass_at_k.get(headline_key), "ci_low"),
                        "ci_high": _per_k(row.delta_pass_at_k.get(headline_key), "ci_high"),
                        # The sample the three numbers above came over. Printing a
                        # delta without it is the omission B64 is about.
                        "n_compared": _per_k_count(
                            row.delta_pass_at_k.get(headline_key), "n_compared"
                        ),
                        "n_items_submitted": row.n_items_submitted,
                        "n_baseline_excluded": row.n_baseline_excluded,
                        "n_ablated_excluded": row.n_ablated_excluded,
                        "mcnemar_p": row.mcnemar_p,
                        "bh_adjusted_p": row.bh_adjusted_p,
                    }
                    for row in attributions
                },
            }
            print(json.dumps(payload, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
