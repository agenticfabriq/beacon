"""Shared judge prompts and JSON-extraction helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from beacon_graders.errors import GraderJudgeError

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of LLM output, tolerating fences and preamble."""

    cleaned = _FENCE_RE.sub("", text).strip()
    start = cleaned.find("{")
    if start < 0:
        raise GraderJudgeError(f"No JSON object in judge output: {text[:200]!r}")
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise GraderJudgeError(
            f"Malformed JSON in judge output: {exc!r}; raw={cleaned[start : start + 200]!r}"
        ) from exc
    if not isinstance(value, dict):
        raise GraderJudgeError(f"Judge JSON must be an object: {value!r}")
    return value


HIERARCHICAL_RUBRIC_PROMPT = """\
You are an expert evaluator. Given an evaluation question, a candidate response,
and a hierarchical rubric, score each criterion in the rubric.

QUESTION:
{question}

CANDIDATE RESPONSE:
{candidate}

GOLD REFERENCE (if any):
{gold}

RUBRIC (JSON):
{rubric_json}

For each rubric criterion, return a score in [0.0, 1.0] and a one-sentence
justification. Output ONLY a JSON object with this shape:

{{
  "criteria": {{
    "<criterion_name>": {{"score": 0.0, "justification": "..."}}
  }}
}}
"""


FREE_TEXT_REFERENCE_PROMPT = """\
You are an expert reviewer of analyst narratives. Given the candidate narrative,
a reference narrative containing N target insights, and a list of citations the
candidate produced, score two criteria:

1. insight_recall: what fraction of the reference's insights are present in the
   candidate (entailment, not exact wording).
2. citation_correctness: what fraction of the candidate's citations are
   substantiated by the underlying data sources listed below.

QUESTION:
{question}

CANDIDATE NARRATIVE:
{candidate}

REFERENCE INSIGHTS:
{reference_insights}

CANDIDATE CITATIONS:
{citations}

DATA SOURCES:
{data_sources}

Return ONLY JSON:
{{
  "insight_recall": {{"score": 0.0, "justification": "..."}},
  "citation_correctness": {{"score": 0.0, "justification": "..."}}
}}
"""
