from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_graders.llm.provider import JudgeRequest, JudgeResponse
from beacon_runner.sut import SolutionUnderTest
from beacon_runner.sut.reference import (
    LangchainSqlSUT,
    LlamaIndexSqlSUT,
    SpiderAgentLiteSUT,
)
from beacon_runner.types import EvalItem, SolutionConfig

if TYPE_CHECKING:
    from beacon_runner.sut.reference.base import BaseReferenceSqlSUT


class _RecordingProvider:
    name = "recording"

    def __init__(self, model_version: str = "ref-mock-1", reply: str = "SELECT 1") -> None:
        self.model_version = model_version
        self._reply = reply
        self.requests: list[JudgeRequest] = []

    def generate(self, request: JudgeRequest) -> JudgeResponse:
        self.requests.append(request)
        return JudgeResponse(
            text=self._reply,
            tokens_input=12,
            tokens_output=8,
            model_version=self.model_version,
        )


def _item(item_id: str = "ref-1") -> EvalItem:
    return EvalItem(
        item_id=item_id,
        suite="reference_sut_smoke",
        query={"question": "How many customers signed up last week?", "db_id": "retail"},
        ground_truth={"sql": "SELECT count(*) FROM customers"},
        metadata={},
    )


def _cfg(layers_enabled: dict[str, bool] | None = None) -> SolutionConfig:
    return SolutionConfig(
        model_id="claude-haiku-4-5",
        prompt_version="v0",
        layers_enabled=layers_enabled or {},
    )


@pytest.mark.parametrize(
    "sut_cls",
    [SpiderAgentLiteSUT, LangchainSqlSUT, LlamaIndexSqlSUT],
)
def test_reference_sut_satisfies_protocol(sut_cls: type[BaseReferenceSqlSUT]) -> None:
    sut = sut_cls(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    assert isinstance(sut, SolutionUnderTest)


def test_spider_agent_lite_declares_planner_executor_validator() -> None:
    sut = SpiderAgentLiteSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    names = sorted(layer.name for layer in sut.layers())
    assert names == ["executor", "planner", "validator"]


def test_langchain_sut_declares_sql_chain_fewshot_retry() -> None:
    sut = LangchainSqlSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    names = sorted(layer.name for layer in sut.layers())
    assert names == ["few_shot_examples", "retry_loop", "sql_chain"]


def test_llama_index_sut_declares_query_engine_schema_rows() -> None:
    sut = LlamaIndexSqlSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    names = sorted(layer.name for layer in sut.layers())
    assert names == ["row_sampling", "schema_introspection", "sql_query_engine"]


def test_validate_config_rejects_unknown_layer() -> None:
    sut = SpiderAgentLiteSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    errs = sut.validate_config(_cfg({"bogus_layer": False}))
    assert any("bogus_layer" in err for err in errs)


def test_validate_config_requires_model_id() -> None:
    sut = SpiderAgentLiteSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    errs = sut.validate_config(SolutionConfig(model_id="", prompt_version="v0"))
    assert errs == ["model_id is required"]


def test_invoke_calls_provider_with_layer_aware_prompt() -> None:
    provider = _RecordingProvider(reply="SELECT count(*) FROM customers WHERE signup_date > now()")
    sut = LangchainSqlSUT(owner_team_id=uuid4(), llm_provider=provider)
    result = sut.invoke(_item(), _cfg({"retry_loop": False}))
    assert provider.requests, "provider was not called"
    prompt = provider.requests[0].prompt
    assert "retail" in prompt
    assert "How many customers" in prompt
    assert "retry_loop=off" in prompt
    assert "sql_chain=on" in prompt
    assert result.output_kind == "sql"
    assert "SELECT" in result.output["sql"]


def test_invoke_trace_has_layer_tagged_children() -> None:
    sut = LlamaIndexSqlSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    result = sut.invoke(_item("trace-1"), _cfg())
    levels = {child.level for child in result.trace.children}
    assert "layer:sql_query_engine" in levels
    assert "layer:schema_introspection" in levels
    assert "layer:row_sampling" in levels


def test_disabled_layer_marks_step_skipped() -> None:
    sut = LangchainSqlSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    result = sut.invoke(_item("skip-1"), _cfg({"retry_loop": False}))
    retry_step = next(child for child in result.trace.children if child.level == "layer:retry_loop")
    assert retry_step.status == "SKIPPED"
    other_steps = [child for child in result.trace.children if child.level != "layer:retry_loop"]
    assert all(step.status == "COMPLETED" for step in other_steps)


def test_invoke_propagates_token_usage_from_provider() -> None:
    sut = SpiderAgentLiteSUT(owner_team_id=uuid4(), llm_provider=_RecordingProvider())
    result = sut.invoke(_item("tokens-1"), _cfg())
    assert result.tokens_input == 12
    assert result.tokens_output == 8
