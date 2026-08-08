"""In-process mnemiq SUT: one assembled engine per (database, config arm).

mnemiq is deliberately NOT a declared dependency of ``beacon_runner`` -- a
committed path dependency would break standalone clones and CI. It is imported
lazily at first use; install it into this repo's environment out of band:

    uv pip install -e ../mnemiq

(an explicit ``uv sync`` prunes the install; re-run the command afterwards).
Unit tests inject a fake engine builder and need no mnemiq at all.

Each config arm maps onto mnemiq's assembly seams:

- ``enrichment`` off -> structural-only snapshot (as ``run_minidev_pg
  --no-semantic`` does); cached separately from the semantic snapshot because
  mnemiq's enrichment cache key does not encode the semantic flag.
- ``grounding`` off -> glossary definitions stripped from the snapshot before
  retrieval.
- ``certified_records`` off -> Verity's overlay never applied: the settings
  arm carries ``verity_records_url=None``, so ``apply_certified()`` has
  nothing to fetch. Distinct from ``grounding``, which is mnemiq's LOCAL
  glossary -- the two meaning layers ablate separately.
- ``verifier`` off -> no verification cascade assembled.
- ``self_consistency`` off -> single candidate, no selector.
- ``mode_routing`` -> declared for completeness of the product's layer map,
  but a no-op under eval assembly: the in-process engine uses one fixed
  assembly and routing exists only in the product runtime.

Answers execute natively against Postgres (mnemiq's ``PostgresAdapter``), so
the SQL beacon's execution-grounded grader replays is the SQL mnemiq actually
ran, in the same dialect.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beacon_runner.types import (
    EvalItem,
    ExecutionResult,
    ExecutionStep,
    Layer,
    SolutionConfig,
    SolutionIdentity,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

ENRICHMENT_LAYER = Layer(
    name="enrichment",
    description="LLM semantic enrichment of the schema snapshot (card descriptions).",
    ablation_semantic="structural-only snapshot: profiled structure, no LLM descriptions",
    instrumentation="native",
)
GROUNDING_LAYER = Layer(
    name="grounding",
    description="Glossary definitions carried into retrieval (code-meaning grounding).",
    ablation_semantic="definitions stripped from the snapshot before retrieval",
    instrumentation="native",
)
# Distinct from grounding on purpose: grounding is mnemiq's LOCAL glossary,
# certified_records is Verity's overlay (apply_certified via verity_records_url).
# Both are real meaning layers, and an ablation must separate them.
CERTIFIED_RECORDS_LAYER = Layer(
    name="certified_records",
    description="Verity's certified records overlaid onto retrieval (apply_certified).",
    ablation_semantic="no Verity overlay: verity_records_url unset, local snapshot only",
    instrumentation="native",
)
VERIFIER_LAYER = Layer(
    name="verifier",
    description="Post-answer verification cascade before standing behind an answer.",
    ablation_semantic="no verifier assembled; answers returned unverified",
    instrumentation="native",
)
SELF_CONSISTENCY_LAYER = Layer(
    name="self_consistency",
    description="Multi-candidate generation with cross-candidate selection.",
    ablation_semantic="single candidate, no selector",
    instrumentation="native",
)
MODE_ROUTING_LAYER = Layer(
    name="mode_routing",
    description="Per-question mode routing (instant/thinking/deep) in the product runtime.",
    ablation_semantic=(
        "no-op under eval assembly: the in-process engine uses one fixed assembly; "
        "routing exists only in the product runtime"
    ),
    instrumentation="synthetic",
)


def compose_question(query: Mapping[str, Any]) -> str:
    """BIRD leaderboard convention: append the evidence hint when present."""
    question = str(query.get("question", ""))
    evidence = str(query.get("evidence") or "")
    if evidence:
        return f"{question}\n\nHint: {evidence}"
    return question


class MnemiqInProcessSUT:
    """mnemiq assembled in-process via ``mnemiq.eval.engine.build_engine``."""

    SOLUTION_ID = "mnemiq"
    VERSION = "0.1.0.dev0"
    SUMMARY = (
        "mnemiq database-grounded answering engine, assembled in-process per "
        "config arm for layer attribution."
    )
    LAYERS: tuple[Layer, ...] = (
        ENRICHMENT_LAYER,
        GROUNDING_LAYER,
        CERTIFIED_RECORDS_LAYER,
        VERIFIER_LAYER,
        SELF_CONSISTENCY_LAYER,
        MODE_ROUTING_LAYER,
    )

    def __init__(
        self,
        *,
        owner_team_id: UUID,
        minidev_dir: str,
        bird_dsn: str,
        enrich_cache_dir: str,
        candidates: int = 3,
        settings: Any | None = None,
        engine_builder: Callable[[str, Mapping[str, bool]], tuple[Callable[[str], Any], Any]]
        | None = None,
    ) -> None:
        self._owner_team_id = owner_team_id
        self._minidev_dir = minidev_dir
        self._bird_dsn = bird_dsn
        self._enrich_cache_dir = enrich_cache_dir
        self._candidates = candidates
        self._settings = settings
        self._engine_builder = engine_builder or self._build_engine
        # mnemiq engines (DuckDB store + adapter connection) are not
        # thread-safe; one lock serializes both engine builds and asks.
        self._lock = threading.Lock()
        self._engines: dict[
            tuple[str, tuple[tuple[str, bool], ...]], tuple[Callable[[str], Any], Any]
        ] = {}

    def identity(self) -> SolutionIdentity:
        return SolutionIdentity(
            solution_id=self.SOLUTION_ID,
            version=self.VERSION,
            owner_team=self._owner_team_id,
            summary=self.SUMMARY,
            supported_modes=["EVAL", "NIGHTLY_LOO"],
            layers=list(self.LAYERS),
        )

    def layers(self) -> list[Layer]:
        return list(self.LAYERS)

    def validate_config(self, config: SolutionConfig) -> list[str]:
        errors: list[str] = []
        known = {layer.name for layer in self.LAYERS}
        unknown = sorted(set(config.layers_enabled) - known)
        if unknown:
            errors.append(f"Unknown layers: {unknown}")
        if not config.model_id:
            errors.append("model_id is required")
        return errors

    def invoke(self, item: EvalItem, config: SolutionConfig) -> ExecutionResult:
        enabled = {layer.name: config.is_layer_enabled(layer.name) for layer in self.LAYERS}
        db_id = str(item.query.get("db_id", ""))
        question = compose_question(item.query)
        started = time.monotonic()
        with self._lock:
            ask, client = self._engine_for(db_id, enabled)
            tokens_before = int(getattr(client, "total_tokens", 0))
            answer = ask(question)
            tokens_used = int(getattr(client, "total_tokens", 0)) - tokens_before
        runtime_ms = int((time.monotonic() - started) * 1000)
        return self._to_execution_result(
            item=item,
            answer=answer,
            enabled=enabled,
            tokens_used=tokens_used,
            runtime_ms=runtime_ms,
        )

    def _engine_for(
        self, db_id: str, enabled: Mapping[str, bool]
    ) -> tuple[Callable[[str], Any], Any]:
        key = (db_id, tuple(sorted(enabled.items())))
        engine = self._engines.get(key)
        if engine is None:
            engine = self._engine_builder(db_id, enabled)
            self._engines[key] = engine
        return engine

    def _build_engine(
        self, db_id: str, enabled: Mapping[str, bool]
    ) -> tuple[Callable[[str], Any], Any]:
        from mnemiq.adapters.pg import PostgresAdapter
        from mnemiq.eval.bird_runner import enrich_bird_db
        from mnemiq.eval.engine import build_engine

        settings = self._get_settings()
        if not enabled.get("certified_records", True):
            # Verity's overlay is a settings-level knob: with the URL forced to
            # None, apply_certified() never runs and mnemiq answers from its
            # local snapshot alone. verity_records_url is a DECLARED mnemiq
            # Settings field (default None), so forcing it is always valid --
            # and a hasattr guard here can never fire, so none is pretended.
            # The OFF arm is safe by construction; it is the ON arm that can
            # silently equal baseline (fail-soft 401 fetch, incremental-sync
            # watermark returning an empty delta, records reaching the
            # snapshot but not the retrieval packet). Those liveness gates
            # belong per-arm in the SUT's grounded assembly, not here.
            settings = settings.model_copy(update={"verity_records_url": None})
        semantic = enabled.get("enrichment", True)
        # mnemiq's enrichment cache key does not encode the semantic flag;
        # separate directories keep the arms from silently sharing snapshots.
        cache_dir = str(Path(self._enrich_cache_dir) / ("semantic" if semantic else "structural"))
        snapshot = enrich_bird_db(
            self._minidev_dir,
            db_id,
            settings,
            cache_dir=cache_dir,
            semantic=semantic,
        )
        if not enabled.get("grounding", True):
            snapshot = snapshot.model_copy(update={"definitions": []})
        adapter = PostgresAdapter(self._bird_dsn)
        candidates = self._candidates if enabled.get("self_consistency", True) else 1
        ask, client = build_engine(
            snapshot,
            adapter,
            settings,
            candidates=candidates,
            verify=enabled.get("verifier", True),
        )
        return ask, client

    def _get_settings(self) -> Any:
        if self._settings is None:
            from mnemiq.config import Settings

            self._settings = Settings()
        return self._settings

    def _to_execution_result(
        self,
        *,
        item: EvalItem,
        answer: Any,
        enabled: Mapping[str, bool],
        tokens_used: int,
        runtime_ms: int,
    ) -> ExecutionResult:
        failed = bool(getattr(answer, "failed", False))
        deferred = bool(getattr(answer, "deferred", False))
        reason = getattr(answer, "reason_code", None)
        trace_obj = getattr(answer, "trace", None)
        answer_text = str(getattr(answer, "answer", ""))

        children = [
            ExecutionStep(
                uuid=f"{item.item_id}-layer-{layer.name}",
                name=layer.name,
                level=f"layer:{layer.name}",
                status="COMPLETED" if enabled.get(layer.name, True) else "SKIPPED",
            )
            for layer in self.LAYERS
        ]
        root = ExecutionStep(
            uuid=f"root-{item.item_id}",
            name="mnemiq_in_process",
            level="workflow",
            status="FAILED" if failed else "COMPLETED",
            outputs={
                "output_kind": "sql",
                "deferred": deferred,
                "failed": failed,
                "reason": str(reason) if reason is not None else None,
                "cached": getattr(answer, "cached", None),
                "agreement": getattr(answer, "agreement", None),
                "judge_engaged": getattr(answer, "judge_engaged", None),
                "judge_override": getattr(answer, "judge_override", None),
                "candidates_executed": getattr(answer, "candidates_executed", None),
                "enrichment_version": getattr(trace_obj, "enrichment_version", None),
                # mnemiq's client counts total tokens without an in/out split.
                "tokens_total": tokens_used,
            },
            error=answer_text if failed else None,
            children=children,
        )

        if failed:
            # Source outage, not an abstention: surface as a system error so
            # beacon composes ERROR rather than a graded FAIL (mnemiq M6).
            output: dict[str, Any] = {
                "sql": "",
                "answer": answer_text,
                "failed": True,
                "reason": str(reason) if reason is not None else None,
            }
            error: str | None = f"mnemiq_execution_failed: {answer_text[:200]}"
        elif deferred:
            # Abstention: a non-answer beacon grades as not-passing. mnemiq's
            # own harness remains the authority on deferred-correctly/wrongly.
            output = {
                "sql": "",
                "answer": answer_text,
                "deferred": True,
                "reason": str(reason) if reason is not None else None,
            }
            error = None
        else:
            output = {"sql": str(getattr(trace_obj, "target_sql", "")), "item_id": item.item_id}
            error = None

        return ExecutionResult(
            output=output,
            output_kind="sql",
            trace=root,
            tokens_input=0,
            tokens_output=max(tokens_used, 0),
            runtime_ms=runtime_ms,
            error=error,
            deferred=deferred,
        )
