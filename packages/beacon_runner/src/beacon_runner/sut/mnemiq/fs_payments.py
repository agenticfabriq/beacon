"""mnemiq over the fs payments corpus: one DuckDB file, one certified-record snapshot.

``MnemiqInProcessSUT`` is BIRD-shaped — it takes a minidev directory and a Postgres DSN, fans out
per ``db_id``, and calls ``enrich_bird_db``. This corpus is a single DuckDB file with no fan-out and
a semantic layer that comes from Verity rather than from BIRD's own metadata, so only engine
construction differs. Everything else — the layer map, ``invoke``, the result mapping — is
inherited, because a second copy of those would be a second answer to what a layer means.

**The per-arm liveness gates live here**, which is what the base class's ``certified_records``
comment points at. Forcing ``verity_records_url`` to ``None`` makes the OFF arm safe by
construction; the ON arm is the one that can silently equal baseline, and three separate ways of
doing that were measured while building this corpus:

* a **fail-soft 401**. ``fetch_certified_records`` logs a warning and returns ``[]`` so that a
  Verity outage never bricks mnemiq — correct for the product, fatal for an experiment. Both arms
  then produce a byte-identical prompt and the run reports that the semantic layer does not help.
* the **incremental-sync watermark**. A successful pull stamps one; the next run sends ``?since=``
  and receives an empty delta. The second run of an eval grounds on less than the first, silently.
* records reaching the **snapshot but not the retrieval packet**. Certified metrics and dimensions
  sat in ``snapshot.metrics`` read by nothing until mnemiq wired them through; a snapshot-level
  assertion would have passed while the model saw no difference at all.
* a **mid-session outage after a successful pull** (mnemiq M18 confirmed the class in production
  code: an incremental pull plus a from-scratch rebuild emptied the overlay on the SECOND pull,
  38 then 0, silently). mnemiq now serves last-known-good on outage and does periodic full
  re-syncs; the ``expect_records`` gate here guards regardless, because belt and suspenders is
  the correct number of ways to hold up the pants an experiment is wearing.

So the grounded arm asserts it actually grounded, and raises if it did not. A run that cannot detect
its own instrument being disconnected is not a measurement.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from beacon_runner.sut.mnemiq.in_process import MnemiqInProcessSUT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from uuid import UUID

# The one table every question in this suite touches. Used only to check that certified records
# reach the retrieval packet, never to answer.
_PROBE_TABLE = "payment_transaction"
_PROBE_QUESTION = "what was our revenue last quarter"


class CertifiedRecordsNotGrounded(RuntimeError):
    """The grounded arm did not ground. Raised rather than returned: an arm that silently equals
    baseline manufactures a zero effect, which is indistinguishable from a real one."""


class MnemiqFsPaymentsSUT(MnemiqInProcessSUT):
    """mnemiq assembled over a DuckDB corpus, with Verity's records as the ablated layer."""

    SLUG = "mnemiq-fs-payments"
    SUMMARY = (
        "mnemiq over the fs payments corpus, with Verity's certified records as the ablated "
        "layer."
    )

    def __init__(
        self,
        *,
        owner_team_id: UUID,
        database_path: str,
        records_url: str,
        enrich_cache_dir: str,
        source_id: str = "fs_payments",
        expect_records: int,
        candidates: int = 3,
        settings: Any | None = None,
        engine_builder: Callable[[str, Mapping[str, bool]], tuple[Callable[[str], Any], Any]]
        | None = None,
        packet_probe: Callable[[Any], int] | None = None,
    ) -> None:
        super().__init__(
            owner_team_id=owner_team_id,
            # The base class's BIRD inputs are unused here; engine construction is overridden.
            minidev_dir="",
            bird_dsn="",
            enrich_cache_dir=enrich_cache_dir,
            candidates=candidates,
            settings=settings,
            engine_builder=engine_builder,
        )
        self._database_path = database_path
        self._records_url = records_url
        self._source_id = source_id
        self._expect_records = expect_records
        # How many certified objects a real question would pull into the retrieval packet. Injected
        # for the reason the base class injects its engine builder -- beacon's CI has no mnemiq, and
        # a gate that can only be shown to fire on a developer's machine is not demonstrably a gate.
        self._packet_probe = packet_probe or _mnemiq_packet_probe

    def run_config(self) -> dict[str, object]:
        """Provenance for the run, so a result can be tied to the exact inputs that produced it.

        ``records_sha256`` is beacon-main's requirement and it is a real one: the grounded arm reads
        an exported snapshot over ``file://``, and two checkouts carrying the same basename at
        different states is the same trap as two databases with the same name. The digest is of the
        bytes actually read.
        """
        return {
            "database_path": self._database_path,
            "database_sha256": _digest(self._database_path),
            "verity_records_url": self._records_url,
            "records_sha256": _digest(_path_of(self._records_url)),
            "expect_records": self._expect_records,
            "source_id": self._source_id,
        }


    def _to_execution_result(
        self,
        *,
        item: Any,
        answer: Any,
        enabled: Mapping[str, bool],
        tokens_used: int,
        runtime_ms: int,
    ) -> Any:
        """Push the rows the answer's SQL returns, not just the SQL.

        ``ResultSetMatchGrader.applicable()`` requires ``output["rows"]``; the base class emits
        ``{"sql": ..., "item_id": ...}``. The BIRD SUT can do that because beacon executes candidate
        SQL against the benchmark Postgres -- this corpus is a DuckDB file only this SUT holds a
        handle on, so nothing downstream can turn an answer into a result set.

        The first real sweep is what this is written from: 24 of 29 items composed ERROR in BOTH
        arms and the ablation reported delta 0.0 at p = 1.0. The answers were fine -- one of those
        "errors", executed by hand against the corpus, returned 12 rows and had handled the CDC
        revision trap correctly. A grader that never applies produces a null result that looks
        exactly like a real one.
        """
        result = super()._to_execution_result(
            item=item, answer=answer, enabled=enabled,
            tokens_used=tokens_used, runtime_ms=runtime_ms,
        )
        sql = str(result.output.get("sql") or "")
        if result.error is not None or result.deferred or not sql:
            # A deferral is not an answer, and a failure already has its own story. Inventing an
            # empty result set for either would make it gradeable, which is precisely wrong.
            return result

        try:
            columns, rows = self._execute(sql)
        except Exception as exc:  # noqa: BLE001 -- any engine error is the same story here
            # mnemiq already executed this SQL to produce its answer, so a failure here means the
            # two disagree about the same corpus. That has to be loud: quietly pushing no rows is
            # what made the first sweep unreadable.
            return result.model_copy(update={
                "error": f"candidate_sql_failed: {type(exc).__name__}: {str(exc)[:200]}",
            })

        return result.model_copy(update={
            "output": {**result.output, "columns": columns, "rows": rows, "row_count": len(rows)},
        })

    def _execute(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        """Run the chosen SQL read-only against the corpus and return JSONB-safe rows.

        Every row is pushed rather than capped here: ``ResultSetMatchGrader`` owns the cap
        (``MAX_PUSHED_ROWS``) and derives its own truncation signal from what it receives, and a
        second truncation policy in the SUT could only disagree with it.
        """
        import duckdb  # noqa: PLC0415 -- keeps the import cost off engine construction

        con = duckdb.connect(self._database_path, read_only=True)
        try:
            cursor = con.execute(sql)
            columns = [description[0] for description in cursor.description]
            rows = [[_transport_safe(value) for value in row] for row in cursor.fetchall()]
        finally:
            con.close()
        return columns, rows

    def _build_engine(
        self, db_id: str, enabled: Mapping[str, bool]
    ) -> tuple[Callable[[str], Any], Any]:
        from mnemiq.adapters.duckdb import DuckDBAdapter
        from mnemiq.enrichment.certified import apply_certified, fetch_certified_records
        from mnemiq.enrichment.pipeline import enrich_structural
        from mnemiq.eval.engine import build_engine

        grounded = enabled.get("certified_records", True)
        settings = self._settings_for_arm(grounded)

        adapter = DuckDBAdapter.duckdb(self._database_path)
        snapshot = enrich_structural(adapter, self._source_id)

        records = fetch_certified_records(settings) if grounded else []
        before = _standalone_count(snapshot)
        snapshot = apply_certified(snapshot, records)

        if grounded:
            self._assert_grounded(snapshot, records, before)
        if not enabled.get("grounding", True):
            snapshot = snapshot.model_copy(update={"definitions": []})

        return build_engine(
            snapshot,
            adapter,
            settings,
            candidates=self._candidates if enabled.get("self_consistency", True) else 1,
            verify=enabled.get("verifier", True),
        )

    def _watermark_path(self) -> Path:
        """A fresh watermark per build.

        The incremental sync stamps one after a successful pull and sends `?since=` on the next, so
        a deterministic path means the second build of the same arm pulls an empty delta. The gate
        refuses it -- the right failure direction -- but a re-run should not need manual cleanup to
        succeed.
        """
        directory = Path(tempfile.mkdtemp(prefix="fs-payments-watermark-"))
        return directory / "watermark.json"

    def _settings_for_arm(self, grounded: bool) -> Any:
        settings = self._get_settings()
        if not grounded:
            return settings.model_copy(update={"verity_records_url": None})
        return settings.model_copy(
            update={
                "verity_records_url": self._records_url,
                # 0 means "unbounded full dump", which is also what lets a `file://` URL work: the
                # client appends `?limit=` otherwise and a file path has no query string.
                "verity_page_size": 0,
                # A watermark per arm build. Without this the second pull sends `?since=` and gets
                # an empty delta, so a re-run grounds on nothing and says nothing about it.
                "verity_watermark_path": str(self._watermark_path()),
            }
        )

    def _assert_grounded(self, snapshot: Any, records: list[Any], before: int) -> None:
        if len(records) != self._expect_records:
            raise CertifiedRecordsNotGrounded(
                f"grounded arm fetched {len(records)} certified records, expected "
                f"{self._expect_records}. The fetch is fail-soft, so a 401, a wrong URL or an "
                f"empty incremental delta all look like this."
            )
        if _standalone_count(snapshot) <= before:
            raise CertifiedRecordsNotGrounded(
                "apply_certified changed nothing: the records arrived and none was applied."
            )

        if self._packet_probe(snapshot) == 0:
            raise CertifiedRecordsNotGrounded(
                "nothing certified reaches the retrieval packet. The snapshot changed and the "
                "model would see none of it, which is where certified metrics and dimensions sat "
                "for as long as they existed."
            )


def _mnemiq_packet_probe(snapshot: Any) -> int:
    """How many certified objects a real question pulls into the retrieval packet.

    The default probe, and the only part of the gate that needs mnemiq. Selection is the thing being
    checked: records can reach the snapshot and be selected by nothing, which is where certified
    metrics and dimensions sat until mnemiq wired them through.
    """
    from mnemiq.authz.grants import GrantSet
    from mnemiq.semantic.glossary import select_definitions
    from mnemiq.semantic.measures import select_dimensions, select_metrics

    grants = GrantSet(objects=frozenset({_PROBE_TABLE}))
    tables = [_PROBE_TABLE]
    return (
        len(select_definitions(_PROBE_QUESTION, snapshot.definitions, grants, tables))
        + len(select_metrics(tables, snapshot.metrics, grants))
        + len(select_dimensions(tables, snapshot.dimensions, grants))
    )


def _standalone_count(snapshot: Any) -> int:
    return (
        len(snapshot.definitions)
        + len(getattr(snapshot, "metrics", []))
        + len(getattr(snapshot, "dimensions", []))
    )


def _path_of(url: str) -> str | None:
    return url[len("file://") :] if url.startswith("file://") else None


def _digest(path: str | None) -> str | None:
    """sha256 of the bytes actually read, or None when the input is not a local file. A remote
    records endpoint has no stable digest to take, and claiming one would be worse than admitting
    there is none."""
    if not path or not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _transport_safe(value: Any) -> Any:
    """A cell JSONB can hold, coerced the way the GOLD was coerced.

    This must match the loader's `_json_safe`, not the grader package's `jsonable()`. `jsonable()`
    sends anything non-primitive through `str()`, so a money column would arrive as
    `"40505.25"` while the gold holds `40505.25` -- and `canonicalize_cell` strips a string and
    leaves a float alone, so the two never compare equal and every money answer scores wrong while
    looking like a real miss.

    Decimal -> float and temporal -> ISO are the same two rules `canonicalize_cell` applies at
    comparison time; applying them at the transport boundary is what keeps both sides in one
    representation. Nothing else is coerced: a genuinely textual code must stay textual.

    Third home for these two lines (the loader's `_json_safe`, the grader's `canonicalize_cell`,
    here) and it should be one. It cannot be imported from either: `beacon_graders` depends on
    `beacon_runner`, and `beacon_benchmarks` depends on both, so the runner is the bottom of the
    graph. Consolidating means this function moving down here and the other two importing it --
    flagged to beacon-main rather than done unilaterally in a package that is not mine.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value
