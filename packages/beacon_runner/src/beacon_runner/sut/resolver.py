"""Resolve a SUT factory the CLI can instantiate.

Two paths, deliberately:

* a **name** registered under the ``beacon.suts`` entry-point group -- what
  beacon ships, and what ``beacon suts list`` can enumerate;
* an explicit ``module:attr`` **import path** -- for a SUT beacon does not
  ship, so bringing your own agent needs no packaging metadata.

Resolution never instantiates anything. The caller supplies constructor
arguments (see :func:`load_sut_config`) plus ``owner_team_id``, which only it
knows.
"""

from __future__ import annotations

import json
from importlib import import_module
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, cast

from beacon_runner.errors import SutNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ENTRY_POINT_GROUP = "beacon.suts"


def list_available_suts() -> list[str]:
    """Return the names registered under the ``beacon.suts`` entry-point group."""
    return sorted(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))


def resolve_sut_factory(spec: str) -> Callable[..., Any]:
    """Return the callable ``spec`` names, without calling it.

    ``spec`` is either an entry-point name (``mnemiq-inprocess``) or an import
    path (``my_agent.beacon:MySUT``). The colon is what distinguishes them, so
    entry-point names must not contain one.
    """
    if ":" in spec:
        return _resolve_import_path(spec)
    return _resolve_entry_point(spec)


def _resolve_entry_point(name: str) -> Callable[..., Any]:
    matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
    if not matches:
        available = list_available_suts()
        raise SutNotFoundError(
            f"no SUT named {name!r} in the {ENTRY_POINT_GROUP!r} entry-point group; "
            f"available: {available or '(none)'}. "
            "Pass an import path like 'my_pkg.module:MySUT' for a SUT beacon does not ship."
        )
    loaded = cast("object", matches[0].load())
    if not callable(loaded):
        raise SutNotFoundError(f"entry point {name!r} resolved to a non-callable {loaded!r}")
    return cast("Callable[..., Any]", loaded)


def _resolve_import_path(spec: str) -> Callable[..., Any]:
    module_path, _, attr = spec.partition(":")
    if not module_path or not attr:
        raise SutNotFoundError(f"import path {spec!r} must look like 'package.module:Attribute'")
    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise SutNotFoundError(
            f"cannot import module {module_path!r} for SUT {spec!r}: {exc}"
        ) from exc
    try:
        loaded = cast("object", getattr(module, attr))
    except AttributeError as exc:
        raise SutNotFoundError(f"module {module_path!r} has no attribute {attr!r}") from exc
    if not callable(loaded):
        raise SutNotFoundError(f"{spec!r} resolved to a non-callable {loaded!r}")
    return cast("Callable[..., Any]", loaded)


def load_sut_config(path: Path | None) -> dict[str, Any]:
    """Load constructor keyword arguments for a SUT from a JSON object file."""
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SutNotFoundError(f"SUT config {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SutNotFoundError(
            f"SUT config {path} must be a JSON object of constructor keyword "
            f"arguments, got {type(payload).__name__}"
        )
    non_string_keys = [key for key in payload if not isinstance(key, str)]
    if non_string_keys:
        raise SutNotFoundError(f"SUT config {path} has non-string keys: {non_string_keys}")
    return payload
