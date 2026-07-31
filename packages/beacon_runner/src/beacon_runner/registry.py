"""In-process SUT registry."""

from __future__ import annotations

from threading import RLock

from beacon_runner.errors import SutNotFoundError
from beacon_runner.sut import SolutionUnderTest


class SutRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._catalog: dict[tuple[str, str], SolutionUnderTest] = {}

    def register(self, sut: object) -> None:
        """Register a SUT keyed by its ``(solution_id, version)`` identity."""
        if not isinstance(sut, SolutionUnderTest):
            raise TypeError(
                f"object {sut!r} does not satisfy SolutionUnderTest protocol "
                "(must have identity, layers, validate_config, invoke)"
            )
        identity = sut.identity()
        key = (identity.solution_id, identity.version)
        with self._lock:
            self._catalog[key] = sut

    def get(self, solution_id: str, version: str) -> SolutionUnderTest:
        """Look up a SUT by ``(solution_id, version)`` or raise ``SutNotFoundError``."""
        key = (solution_id, version)
        with self._lock:
            try:
                return self._catalog[key]
            except KeyError as exc:
                raise SutNotFoundError(f"no SUT registered for ({solution_id}, {version})") from exc

    def list_ids(self) -> list[tuple[str, str]]:
        """Return all registered ``(solution_id, version)`` pairs, sorted."""
        with self._lock:
            return sorted(self._catalog.keys())


_DEFAULT_REGISTRY = SutRegistry()


def default_registry() -> SutRegistry:
    """Return the process-wide default ``SutRegistry`` singleton."""
    return _DEFAULT_REGISTRY
