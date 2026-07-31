"""WatermarkManager delegates to WorkerStateRepo with canonical args."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

from beacon_workers.watermark import WatermarkManager


def test_fetch_since_returns_none_for_new_worker() -> None:
    manager = WatermarkManager(MagicMock(), worker_name="promotion", team_id=uuid4())
    repo = MagicMock()
    repo.get_or_create.return_value = MagicMock(last_processed_id=None)
    manager.repo = cast("Any", repo)

    assert manager.fetch_since() is None


def test_advance_calls_repo_with_correct_args() -> None:
    team_id = uuid4()
    manager = WatermarkManager(MagicMock(), worker_name="promotion", team_id=team_id)
    repo = MagicMock()
    manager.repo = cast("Any", repo)
    new_id = uuid4()

    manager.advance(last_processed_id=new_id)
    kwargs = repo.advance.call_args.kwargs

    assert kwargs["worker_name"] == "promotion"
    assert kwargs["team_id"] == team_id
    assert kwargs["last_processed_id"] == new_id
    assert isinstance(kwargs["last_processed_at"], datetime)
    assert kwargs["last_processed_at"].tzinfo == UTC


def test_record_error_truncates_long_messages() -> None:
    manager = WatermarkManager(MagicMock(), worker_name="x", team_id=None)
    repo = MagicMock()
    manager.repo = cast("Any", repo)

    manager.record_error("A" * 5000)
    message = repo.increment_error.call_args.kwargs["message"]

    assert len(message) == 1000
