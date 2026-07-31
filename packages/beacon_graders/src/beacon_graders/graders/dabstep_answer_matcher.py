"""DABStep-style answer matcher."""

from __future__ import annotations

import math
import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from beacon_graders.types import Verdict

if TYPE_CHECKING:
    from beacon_runner.types import EvalItem, ExecutionResult

_NOT_APPLICABLE = {"not applicable", "n/a", "na", "none", "[]", ""}
_NUMERIC_WITH_COMMAS_RE = re.compile(r"^\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+[.,]\d+)$")
_NUMBER_RE = re.compile(r"(-?\d*\.\d+|-?\d+\.?\d*)")


class DabstepAnswerMatcher:
    name = "dabstep_answer_matcher"
    version = "v1"

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:
        """Apply when ``result`` is an answer and the item ships an ``answer`` gold."""
        if result.output_kind != "answer":
            return False
        return "answer" in (item.ground_truth or {})

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:
        """Compare candidate vs gold with DABStep numeric/list/fuzzy-string rules."""
        candidate = result.output.get("answer")
        gold = (item.ground_truth or {}).get("answer")
        if candidate is None or gold is None:
            return [
                self._verdict(
                    passed=False,
                    justification="Missing candidate or gold answer.",
                    candidate=candidate,
                    gold=gold,
                )
            ]

        passed = self._compare(str(candidate), str(gold))
        return [
            self._verdict(
                passed=passed,
                justification="Match." if passed else f"Mismatch: '{candidate}' vs '{gold}'",
                candidate=candidate,
                gold=gold,
            )
        ]

    def _compare(self, first: str, second: str) -> bool:
        first_normalized = first.strip().lower()
        second_normalized = second.strip().lower()
        if first_normalized in _NOT_APPLICABLE and second_normalized in _NOT_APPLICABLE:
            return True
        if self._has_list_separator(first_normalized) or self._has_list_separator(
            second_normalized
        ):
            return self._compare_lists(first_normalized, second_normalized)
        if self._is_numeric_like(first_normalized) or self._is_numeric_like(second_normalized):
            return self._compare_numeric(first_normalized, second_normalized)
        return self._compare_strings(first_normalized, second_normalized)

    def _has_list_separator(self, value: str) -> bool:
        if ";" in value:
            return True
        return "," in value and not self._is_numeric_with_commas(value)

    def _is_numeric_with_commas(self, value: str) -> bool:
        return bool(_NUMERIC_WITH_COMMAS_RE.match(value.strip()))

    def _is_numeric_like(self, value: str) -> bool:
        return bool(re.search(r"\d", value))

    def _compare_numeric(self, first: str, second: str) -> bool:
        first_number = self._extract_numeric(first)
        second_number = self._extract_numeric(second)
        if first_number is None or second_number is None:
            return False
        return math.isclose(first_number, second_number, rel_tol=1e-4, abs_tol=1e-4)

    def _extract_numeric(self, value: str) -> float | None:
        cleaned = value.replace(",", "").replace("$", "").replace("%", "").strip()
        match = _NUMBER_RE.search(cleaned)
        if match is None:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _compare_strings(self, first: str, second: str) -> bool:
        clean_first = re.sub(r"\W", "", first)
        clean_second = re.sub(r"\W", "", second)
        if clean_first == clean_second:
            return True
        return SequenceMatcher(None, first, second).ratio() > 0.95

    def _compare_lists(self, first: str, second: str) -> bool:
        first_items = sorted(part.strip() for part in re.split(r"[,;]", first) if part.strip())
        second_items = sorted(part.strip() for part in re.split(r"[,;]", second) if part.strip())
        if len(first_items) != len(second_items):
            return False
        return all(
            self._compare(first_item, second_item)
            for first_item, second_item in zip(first_items, second_items, strict=True)
        )

    def _verdict(
        self,
        *,
        passed: bool,
        justification: str,
        candidate: Any,
        gold: Any,
    ) -> Verdict:
        return Verdict(
            grader=self.name,
            grader_version=self.version,
            criterion="factoid_match",
            bool_value=passed,
            value=1.0 if passed else 0.0,
            justification=justification,
            raw_output={"candidate": candidate, "gold": gold},
        )
