from __future__ import annotations

import sqlalchemy as sa
from beacon_graders import Grader, Verdict, VerdictOutcome
from beacon_graders.graders.dabstep_answer_matcher import DabstepAnswerMatcher
from beacon_graders.graders.execution_grounded_sql import ExecutionGroundedSqlGrader
from beacon_graders.graders.free_text_reference import FreeTextReferenceGrader
from beacon_graders.graders.hierarchical_rubric import HierarchicalRubricGrader
from beacon_graders.graders.text2vis_data_grounded import Text2VisDataGroundedGrader
from beacon_graders.llm.provider import JudgeCache, JudgeRequest, JudgeResponse
from beacon_runner.types import EvalItem, ExecutionResult, ExecutionStep


class _SimpleGrader:
    name = "simple"
    version = "v1"

    def applicable(self, item: EvalItem, result: ExecutionResult) -> bool:  # noqa: ARG002
        return True

    def grade(self, item: EvalItem, result: ExecutionResult) -> list[Verdict]:  # noqa: ARG002
        return [
            Verdict(
                grader=self.name,
                grader_version=self.version,
                criterion="smoke",
                bool_value=True,
            )
        ]


class _StubProvider:
    name = "stub"
    model_version = "stub-1"

    def generate(self, request: JudgeRequest) -> JudgeResponse:  # noqa: ARG002
        return JudgeResponse(
            text="{}",
            tokens_input=0,
            tokens_output=0,
            model_version=self.model_version,
            raw={},
        )


def _item() -> EvalItem:
    return EvalItem(item_id="i-1", suite="suite", query={"q": "?"}, ground_truth={})


def _result() -> ExecutionResult:
    return ExecutionResult(
        output={"answer": "yes"},
        output_kind="answer",
        trace=ExecutionStep(uuid="r", name="root", level="workflow"),
    )


def test_verdict_outcome_values_match_storage_contract() -> None:
    assert [outcome.value for outcome in VerdictOutcome] == ["PASS", "FAIL", "ERROR", "TIMEOUT"]


def test_verdict_defaults_optional_fields() -> None:
    verdict = Verdict(grader="g", grader_version="v1", criterion="c")
    assert verdict.bool_value is None
    assert verdict.justification == ""
    assert verdict.answer_hash is None


def test_simple_grader_satisfies_protocol() -> None:
    grader = _SimpleGrader()
    assert isinstance(grader, Grader)
    verdicts = grader.grade(_item(), _result())
    assert verdicts[0].bool_value is True


def test_shipped_graders_satisfy_protocol() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    cache = JudgeCache(provider=_StubProvider())
    graders = [
        DabstepAnswerMatcher(),
        ExecutionGroundedSqlGrader(engine_factory=lambda _item: engine),
        HierarchicalRubricGrader(judge_cache=cache),
        FreeTextReferenceGrader(judge_cache=cache),
        Text2VisDataGroundedGrader(),
    ]

    assert all(isinstance(grader, Grader) for grader in graders)
