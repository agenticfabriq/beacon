"""Build the ``VerdictComposer`` a suite actually needs.

The CLI used to hardcode ``DabstepAnswerMatcher``, which never applies to
``output_kind="sql"``. A registered SQL SUT therefore produced verdicts nothing
could grade and every item composed ERROR. Benchmark adapters already know
which graders their suite needs -- ``register_graders`` is on the adapter
protocol -- and ``BenchmarkMetadata`` carries the suite name, so the run's
suite is enough to look the right graders up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from beacon_graders.composer import VerdictComposer

if TYPE_CHECKING:
    from collections.abc import Sequence

    import sqlalchemy as sa
    from beacon_graders.grader import Grader
    from beacon_graders.llm.provider import JudgeCache

    from beacon_runner.types import EvalItem


class GraderRegistry:
    """Collects graders from an adapter and hands out the engine they grade against."""

    def __init__(
        self,
        *,
        engine: sa.Engine | None = None,
        judge_cache: JudgeCache | None = None,
    ) -> None:
        self._engine = engine
        self.judge_cache = judge_cache
        self.graders: list[Any] = []

    def engine_factory(self, _item: EvalItem) -> sa.Engine:
        """Return the benchmark database engine execution graders run against."""
        if self._engine is None:
            raise ValueError(
                "this suite's graders execute SQL but no benchmark database was "
                "configured; pass --benchmark-db-url"
            )
        return self._engine

    def register(self, grader: Any) -> None:
        self.graders.append(grader)


def graders_for_suite(
    suite: str,
    *,
    engine: sa.Engine | None = None,
    judge_cache: JudgeCache | None = None,
) -> list[Any]:
    """Return the graders the adapter owning ``suite`` registers, or an empty list."""
    from beacon_benchmarks import ADAPTERS  # noqa: PLC0415 - import side effect registers adapters

    registry = GraderRegistry(engine=engine, judge_cache=judge_cache)
    for adapter in ADAPTERS.values():
        if getattr(adapter.metadata, "suite", None) == suite:
            adapter.register_graders(registry)
            return registry.graders
    return []


def composer_for_suite(
    suite: str,
    *,
    engine: sa.Engine | None = None,
    judge_cache: JudgeCache | None = None,
    fallback: Sequence[Grader] | None = None,
) -> VerdictComposer:
    """Build the composer for ``suite``, falling back when no adapter claims it."""
    graders = graders_for_suite(suite, engine=engine, judge_cache=judge_cache)
    if not graders:
        graders = list(fallback or [])
    return VerdictComposer(graders=graders)
