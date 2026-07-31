"""Protocol every benchmark adapter implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class BenchmarkMetadata:
    """Static description of a benchmark adapter."""

    name: str
    version: str
    suite: str
    license: str
    public_source: str
    public_source_sha256: str
    internal_mirror: str | None
    size_mb: int
    is_large: bool
    item_metadata_schema: dict[str, str]

    def __post_init__(self) -> None:
        if self.size_mb > 100 and not self.is_large:
            raise ValueError(
                f"BenchmarkMetadata({self.name!r}): size_mb={self.size_mb} > 100 "
                "requires is_large=True"
            )
        if len(self.public_source_sha256) != 64:
            raise ValueError(
                f"BenchmarkMetadata({self.name!r}): public_source_sha256 must be 64 hex chars"
            )


@dataclass
class EvalItemDraft:
    """A preprocessed eval item, ready to be persisted."""

    suite: str
    dataset_version: str
    query: dict[str, Any]
    context: dict[str, Any]
    ground_truth: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    difficulty: str | None = None
    ground_truth_meta: dict[str, Any] = field(default_factory=dict)
    item_external_id: str | None = None


class BenchmarkAdapter(Protocol):
    """Protocol every benchmark subpackage implements."""

    metadata: BenchmarkMetadata

    def download(self, dest: Path, include_large: bool = False) -> Path:
        """Materialize the benchmark's raw payload under ``dest``."""
        ...

    def preprocess(self, raw_root: Path) -> list[EvalItemDraft]:
        """Convert downloaded files in ``raw_root`` into ``EvalItemDraft`` rows."""
        ...

    def register_graders(self, registry: Any) -> list[Any]:
        """Register graders this benchmark relies on and return them."""
        ...

    def ingest(self, raw_root: Path, **kwargs: Any) -> dict[str, Any]:
        """Load databases or files this benchmark needs at grading time."""
        ...


_REQUIRED_ADAPTER_ATTRS = ("metadata", "download", "preprocess", "register_graders", "ingest")


def is_adapter(obj: Any) -> bool:
    """Return true when an object structurally matches ``BenchmarkAdapter``."""
    if not all(hasattr(obj, attr) for attr in _REQUIRED_ADAPTER_ATTRS):
        return False
    return isinstance(getattr(obj, "metadata", None), BenchmarkMetadata)
