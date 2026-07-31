"""Shared benchmark adapter primitives."""

from beacon_benchmarks.base.adapter import (
    BenchmarkAdapter,
    BenchmarkMetadata,
    EvalItemDraft,
    is_adapter,
)
from beacon_benchmarks.base.download import (
    DownloadSpec,
    download,
    sha256_file,
    sha256_verify,
)
from beacon_benchmarks.base.errors import (
    AdapterNotRegisteredError,
    BeaconBenchmarkError,
    DownloadFailedError,
    IngestError,
    LargeAssetSkippedError,
    ShaMismatchError,
    SqlTranslationError,
)
from beacon_benchmarks.base.layout import (
    beacon_data_dir,
    benchmark_data_root,
    benchmark_version_dir,
)
from beacon_benchmarks.base.mirror import HttpMirror, mirror_url

__all__ = [
    "AdapterNotRegisteredError",
    "BeaconBenchmarkError",
    "BenchmarkAdapter",
    "BenchmarkMetadata",
    "DownloadFailedError",
    "DownloadSpec",
    "EvalItemDraft",
    "HttpMirror",
    "IngestError",
    "LargeAssetSkippedError",
    "ShaMismatchError",
    "SqlTranslationError",
    "beacon_data_dir",
    "benchmark_data_root",
    "benchmark_version_dir",
    "download",
    "is_adapter",
    "mirror_url",
    "sha256_file",
    "sha256_verify",
]
