"""Tests for mirror URL resolution and object pull behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import responses
from beacon_benchmarks.base.mirror import HttpMirror, mirror_url

if TYPE_CHECKING:
    from pathlib import Path


def test_mirror_url_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACON_MIRROR_BASE_URL", raising=False)
    assert mirror_url("benchmarks/DABStep.zip") is None


def test_mirror_url_joins_base_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACON_MIRROR_BASE_URL", "https://mirror.test/beacon/")
    assert mirror_url("/benchmarks/DABStep.zip") == (
        "https://mirror.test/beacon/benchmarks/DABStep.zip"
    )


def test_http_mirror_rejects_non_http(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not an http"):
        HttpMirror().pull("ftp://mirror.test/foo.zip", tmp_path / "out.zip")


def test_http_mirror_pull_streams_to_dest(tmp_path: Path) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add("GET", "https://mirror.test/k.zip", body=b"mirror-bytes", status=200)
        dest = tmp_path / "sub" / "out.zip"
        HttpMirror().pull("https://mirror.test/k.zip", dest)
    assert dest.read_bytes() == b"mirror-bytes"


def test_http_mirror_pull_raises_on_http_error(tmp_path: Path) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add("GET", "https://mirror.test/k.zip", status=404)
        with pytest.raises(Exception, match="404"):
            HttpMirror().pull("https://mirror.test/k.zip", tmp_path / "out.zip")
