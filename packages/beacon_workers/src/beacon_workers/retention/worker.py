"""Retention-worker tick loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from beacon_storage.repository.results import ResultRepo
from beacon_storage.repository.traces import TraceRepo
from beacon_storage.repository.verdicts import VerdictRepo

from beacon_workers.logging import get_logger
from beacon_workers.retention.summarizer import TraceSummarizer
from beacon_workers.watermark import WatermarkManager

if TYPE_CHECKING:
    from uuid import UUID

    from beacon_storage.models.runs import Result, Trace, Verdict
    from beacon_storage.object_storage.adapter import ObjectStorage
    from sqlalchemy.orm import Session, sessionmaker


_WORKER_NAME = "retention"


class RetentionWorker:
    """Summarize and delete raw trace payloads after the retention TTL."""

    def __init__(
        self,
        *,
        factory: sessionmaker[Session],
        storage: ObjectStorage,
        retention_days: int,
        batch_size: int = 500,
    ) -> None:
        self.factory = factory
        self.storage = storage
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.summarizer = TraceSummarizer()
        self.log = get_logger().bind(worker=_WORKER_NAME)

    def tick(self) -> None:
        """Summarize unsummarized traces past the TTL and delete their raw payload blobs."""
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        summarized = 0
        deleted = 0
        delete_failures = 0
        last_id_seen: UUID | None = None

        while True:
            with self.factory() as session:
                trace_repo = TraceRepo(session)
                traces = trace_repo.list_older_than_unsummarized(
                    cutoff=cutoff,
                    limit=self.batch_size,
                )
                if not traces:
                    break

                paths_to_delete: list[str] = []
                for trace in traces:
                    payload_path = trace.object_storage_uri
                    result = ResultRepo(session).get(trace.result_id)
                    verdicts = VerdictRepo(session).list_for_trace(trace.id)
                    payload = self._load_payload(trace)
                    summary = self.summarizer.summarize(
                        payload=payload,
                        verdicts=[_verdict_summary(verdict) for verdict in verdicts],
                        metrics=_result_metrics(result),
                    )
                    trace_repo.write_summary_clear_payload(trace_id=trace.id, summary=summary)
                    if payload_path is not None:
                        paths_to_delete.append(payload_path)
                    summarized += 1
                    last_id_seen = trace.id

                if last_id_seen is not None:
                    WatermarkManager(
                        session,
                        worker_name=_WORKER_NAME,
                        team_id=None,
                    ).advance(last_processed_id=last_id_seen)
                session.commit()

            for path in paths_to_delete:
                try:
                    self.storage.delete(path)
                except Exception as exc:
                    delete_failures += 1
                    self.log.warning(
                        "retention.blob_delete_failed",
                        path=path,
                        error=repr(exc),
                    )
                else:
                    deleted += 1

        self.log.info(
            "retention.tick.completed",
            summarized=summarized,
            blobs_deleted=deleted,
            blob_delete_failures=delete_failures,
            last_processed_id=str(last_id_seen) if last_id_seen else None,
        )

    def _load_payload(self, trace: Trace) -> dict[str, Any]:
        if trace.object_storage_uri is None:
            return trace.step_tree
        try:
            raw = self.storage.get(trace.object_storage_uri).read()
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.log.warning(
                "retention.payload_load_failed",
                trace_id=str(trace.id),
                path=trace.object_storage_uri,
                error=repr(exc),
            )
            return {}
        if isinstance(payload, dict):
            return payload
        return {}


def _result_metrics(result: Result | None) -> dict[str, object]:
    if result is None:
        return {}
    # Cost is nullable, and a total needs both halves: summing a recorded
    # output against an unrecorded prompt would report a number smaller than
    # the truth in a summary that outlives the results it summarises.
    tokens_in, tokens_out = result.tokens_input, result.tokens_output
    total = tokens_in + tokens_out if tokens_in is not None and tokens_out is not None else None
    return {
        "latency_ms": result.runtime_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens": total,
        "outcome": _stored_value(result.outcome) if result.outcome is not None else None,
    }


def _verdict_summary(verdict: Verdict) -> dict[str, object]:
    return {
        "grader": verdict.grader,
        "outcome": _verdict_outcome(verdict),
        "score": verdict.value,
        "answer_hash": verdict.answer_hash,
        "confidence": verdict.confidence,
    }


def _verdict_outcome(verdict: Verdict) -> str:
    if verdict.bool_value is True:
        return "PASS"
    if verdict.bool_value is False:
        return "FAIL"
    return "ERROR"


def _stored_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)
