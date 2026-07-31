"""Verify download specs, SHA-256 checks, and mirror fallback."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest
import responses
from beacon_benchmarks.base.download import (
    DownloadSpec,
    download,
    sha256_file,
    sha256_verify,
)
from beacon_benchmarks.base.errors import (
    DownloadFailedError,
    LargeAssetSkippedError,
    ShaMismatchError,
)
from beacon_benchmarks.base.layout import benchmark_data_root, benchmark_version_dir

if TYPE_CHECKING:
    from pathlib import Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_benchmark_data_root_honors_env(beacon_data_dir: Path) -> None:
    assert benchmark_data_root() == beacon_data_dir / "benchmarks"


def test_benchmark_version_dir_layout(beacon_data_dir: Path) -> None:
    path = benchmark_version_dir("foo", "v1-2026-01")
    assert path == beacon_data_dir / "benchmarks" / "foo" / "v1-2026-01"


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    assert sha256_file(path) == _sha(b"hello")


def test_sha256_verify_matches(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    sha256_verify(path, _sha(b"abc"))


def test_sha256_verify_mismatch_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    with pytest.raises(ShaMismatchError):
        sha256_verify(path, "0" * 64)


def test_download_streams_from_public_when_no_mirror(tmp_path: Path) -> None:
    body = b"public-payload"
    with responses.RequestsMock() as rsps:
        rsps.add("GET", "https://x.test/data.zip", body=body, status=200)
        spec = DownloadSpec(
            name="foo",
            version="v1",
            public_source="https://x.test/data.zip",
            public_source_sha256=_sha(body),
            internal_mirror=None,
            size_mb=1,
            is_large=False,
        )
        out = download(spec, dest=tmp_path / "foo.zip")
    assert out.read_bytes() == body


def test_download_falls_back_to_mirror_on_public_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"mirror-payload"
    pulled: dict[str, str] = {}

    def fake_pull(uri: str, dest: Path) -> None:
        dest.write_bytes(body)
        pulled["uri"] = uri

    monkeypatch.setattr(
        "beacon_benchmarks.base.mirror.HttpMirror.pull",
        lambda self, uri, dest: fake_pull(uri, dest),
    )
    with responses.RequestsMock() as rsps:
        rsps.add("GET", "https://x.test/data.zip", status=503)
        spec = DownloadSpec(
            name="foo",
            version="v1",
            public_source="https://x.test/data.zip",
            public_source_sha256=_sha(body),
            internal_mirror="https://mirror.test/benchmarks/foo.zip",
            size_mb=1,
            is_large=False,
        )
        out = download(spec, dest=tmp_path / "foo.zip")
    assert out.read_bytes() == body
    assert pulled["uri"] == "https://mirror.test/benchmarks/foo.zip"


def test_download_raises_when_both_sources_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_pull(self: Any, uri: str, dest: Path) -> None:
        raise OSError("mirror unreachable")

    monkeypatch.setattr("beacon_benchmarks.base.mirror.HttpMirror.pull", fail_pull)
    with responses.RequestsMock() as rsps:
        rsps.add("GET", "https://x.test/data.zip", status=503)
        spec = DownloadSpec(
            name="foo",
            version="v1",
            public_source="https://x.test/data.zip",
            public_source_sha256="0" * 64,
            internal_mirror="https://mirror.test/z.zip",
            size_mb=1,
            is_large=False,
        )
        with pytest.raises(DownloadFailedError):
            download(spec, dest=tmp_path / "foo.zip")


def test_download_large_without_flag_raises(tmp_path: Path) -> None:
    spec = DownloadSpec(
        name="big",
        version="v1",
        public_source="https://x.test/data.zip",
        public_source_sha256="0" * 64,
        internal_mirror=None,
        size_mb=512,
        is_large=True,
    )
    with pytest.raises(LargeAssetSkippedError):
        download(spec, dest=tmp_path / "big.zip", include_large=False)
