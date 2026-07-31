"""TraceSummarizer: collapse a trace payload to a compact summary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


class TraceSummarizer:
    """Build the summary retained after raw trace payload deletion."""

    def summarize(
        self,
        *,
        payload: dict[str, Any],
        verdicts: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a verdict/metrics summary plus original size, used to replace raw payload."""
        try:
            size = len(json.dumps(payload).encode("utf-8"))
        except (TypeError, ValueError):
            size = -1

        return {
            "verdicts": verdicts,
            "metrics": metrics,
            "pass": bool(verdicts)
            and all(verdict.get("outcome") == "PASS" for verdict in verdicts),
            "original_size_bytes": size,
            "summarized_at": datetime.now(UTC).isoformat(),
            "summary_version": "v1",
        }
