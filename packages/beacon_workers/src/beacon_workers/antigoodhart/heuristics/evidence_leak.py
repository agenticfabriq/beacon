"""Heuristic: gold answer leaking into eval-item evidence."""

from __future__ import annotations

import re
from typing import Any

from beacon_storage.models.antigoodhart import AntigoodhartKind, AntigoodhartSeverity

from beacon_workers.antigoodhart.heuristics.sql_in_question import (
    EvalItemView,
    FindingDraft,
    ScanContext,
)


def scan(item: EvalItemView, _ctx: ScanContext) -> list[FindingDraft]:
    """Flag items whose gold answer literal appears verbatim or numerically in evidence."""
    if item.evidence is None:
        return []

    answer_reprs = _answer_reprs(item.gold_output)
    if not answer_reprs:
        return []

    numeric_target = _numeric_answer(item.gold_output)
    for path, leaf in _walk_strings(item.evidence):
        for answer_repr in answer_reprs:
            if answer_repr in leaf:
                return [
                    _finding(
                        item=item,
                        evidence={
                            "path": path,
                            "matched_repr": answer_repr[:200],
                            "leaf_excerpt": leaf[:200],
                        },
                        description=(f"Gold answer literal appears in evidence at {path}"),
                    )
                ]

        if numeric_target is not None and _numerically_close(leaf, numeric_target):
            return [
                _finding(
                    item=item,
                    evidence={
                        "path": path,
                        "numeric_target": numeric_target,
                        "leaf_excerpt": leaf[:200],
                    },
                    description=f"Numeric gold answer appears in evidence at {path}",
                )
            ]

    return []


def _answer_reprs(gold_output: dict[str, Any]) -> list[str]:
    values: set[str] = set()

    if "answer" in gold_output:
        answer = gold_output["answer"]
        values.add(str(answer))
        if isinstance(answer, int | float):
            values.add(f"{answer:g}")

    list_values = gold_output.get("values")
    if isinstance(list_values, list):
        values.add(", ".join(str(value) for value in list_values))

    rows = gold_output.get("rows")
    if isinstance(rows, list):
        flattened: list[str] = []
        for row in rows:
            if isinstance(row, list | tuple):
                flattened.extend(str(column) for column in row)
            else:
                flattened.append(str(row))
        if flattened:
            values.add(", ".join(flattened))

    return sorted(value for value in values if len(value) >= 3)


def _walk_strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, str):
        return [(path, node)]

    if isinstance(node, dict):
        leaves: list[tuple[str, str]] = []
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            leaves.extend(_walk_strings(value, child_path))
        return leaves

    if isinstance(node, list):
        leaves = []
        for index, value in enumerate(node):
            leaves.extend(_walk_strings(value, f"{path}[{index}]"))
        return leaves

    return []


def _numeric_answer(gold_output: dict[str, Any]) -> float | None:
    answer = gold_output.get("answer")
    return float(answer) if isinstance(answer, int | float) else None


def _numerically_close(text: str, target: float, tolerance_fraction: float = 0.001) -> bool:
    if target == 0:
        return False

    for match in re.finditer(r"-?\d+(?:\.\d+)?", text):
        value = float(match.group(0))
        if abs(value - target) / abs(target) <= tolerance_fraction:
            return True
    return False


def _finding(
    *,
    item: EvalItemView,
    evidence: dict[str, Any],
    description: str,
) -> FindingDraft:
    return FindingDraft(
        kind=AntigoodhartKind.EVIDENCE_LEAK,
        severity=AntigoodhartSeverity.HIGH,
        description=description,
        evidence=evidence,
        item_id=item.id,
        team_id=item.team_id,
        suite_id=item.suite_id,
    )
