"""Data-grounded grader for chart outputs."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from beacon_graders.types import Verdict

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

_SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "range": range,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "sorted": sorted,
    "round": round,
    "float": float,
    "int": int,
    "str": str,
    "enumerate": enumerate,
    "zip": zip,
}

_Row = tuple[tuple[str, Any], ...]


class Text2VisDataGroundedGrader:
    name = "text2vis_data_grounded"
    version = "v1"

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when ``result`` is a chart and the item ships a gold ``data_table``."""
        if result.output_kind != "chart":
            return False
        return "data_table" in (item.ground_truth or {})

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Compare the candidate's tabular data (or sandboxed code) against gold."""
        gold = (item.ground_truth or {}).get("data_table")
        candidate = result.output.get("data_table")
        if candidate is None and "data_code" in result.output:
            try:
                candidate = self._run_code(str(result.output["data_code"]))
            except _SandboxDenied as exc:
                return [
                    self._verdict(
                        False,
                        f"Sandbox denied: {exc}",
                        raw={"reason": str(exc)},
                    )
                ]
            except Exception as exc:
                return [
                    self._verdict(
                        False,
                        f"Code raised {type(exc).__name__}: {exc}",
                        raw={"error_type": type(exc).__name__},
                    )
                ]

        if candidate is None:
            return [
                self._verdict(
                    False,
                    "No data_table or data_code in candidate output.",
                    raw={},
                )
            ]

        try:
            candidate_rows = self._normalise(candidate)
            gold_rows = self._normalise(gold)
        except _NormaliseError as exc:
            return [self._verdict(False, f"Could not normalise table: {exc}", raw={})]

        passed = self._compare(candidate_rows, gold_rows)
        return [
            self._verdict(
                passed,
                (
                    "Data table matches gold."
                    if passed
                    else f"Mismatch: candidate {len(candidate_rows)} rows, gold {len(gold_rows)}."
                ),
                raw={
                    "candidate_row_count": len(candidate_rows),
                    "gold_row_count": len(gold_rows),
                },
            )
        ]

    def _run_code(self, code: str) -> Any:
        for line in code.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                raise _SandboxDenied(f"import statement denied: {stripped!r}")
        namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
        exec(compile(code, "<text2vis-sandbox>", "exec"), namespace, namespace)  # noqa: S102
        if "result" not in namespace:
            raise _SandboxDenied("data_code must bind a `result` variable")
        return namespace["result"]

    def _normalise(self, table: Any) -> list[_Row]:
        if table is None:
            raise _NormaliseError("table is None")
        if isinstance(table, list):
            rows: list[_Row] = []
            for row in table:
                if not isinstance(row, dict):
                    raise _NormaliseError(f"row is not a dict: {row!r}")
                rows.append(tuple(sorted(row.items())))
            return rows
        if isinstance(table, dict):
            columns = list(table.keys())
            lengths = {len(table[column]) for column in columns}
            if len(lengths) > 1:
                raise _NormaliseError("dict-of-lists has unequal column lengths")
            row_count = next(iter(lengths), 0)
            rows = []
            for index in range(row_count):
                row = {column: table[column][index] for column in columns}
                rows.append(tuple(sorted(row.items())))
            return rows
        raise _NormaliseError(f"unsupported table type: {type(table).__name__}")

    def _compare(self, candidate: list[_Row], gold: list[_Row]) -> bool:
        return Counter(candidate) == Counter(gold)

    def _verdict(self, passed: bool, justification: str, *, raw: dict[str, Any]) -> Verdict:
        return Verdict(
            grader=self.name,
            grader_version=self.version,
            criterion="data_correctness",
            bool_value=passed,
            value=1.0 if passed else 0.0,
            justification=justification,
            raw_output=raw,
        )


class _SandboxDenied(Exception):
    pass


class _NormaliseError(Exception):
    pass
