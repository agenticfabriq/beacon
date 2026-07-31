"""LangchainSqlSUT: reference SUT modelled on LangChain's SQL chain."""

from __future__ import annotations

from beacon_runner.sut.reference.base import BaseReferenceSqlSUT
from beacon_runner.types import Layer

SQL_CHAIN_LAYER = Layer(
    name="sql_chain",
    description="LangChain create_sql_query_chain wrapper that produces a candidate SQL string.",
    ablation_semantic=(
        "When off, the SUT falls back to raw LLM prompting without the chain wrapper."
    ),
    instrumentation="wrapped",
)
FEW_SHOT_LAYER = Layer(
    name="few_shot_examples",
    description="Few-shot exemplar selection that conditions the SQL chain on similar questions.",
    ablation_semantic="When off, the chain runs without retrieved exemplars (zero-shot).",
    instrumentation="wrapped",
)
RETRY_LOOP_LAYER = Layer(
    name="retry_loop",
    description="Self-correction retry loop that re-prompts on execution errors.",
    ablation_semantic="When off, the first SQL emitted is returned without a retry pass.",
    instrumentation="wrapped",
)


class LangchainSqlSUT(BaseReferenceSqlSUT):
    """Reference SUT modelled on LangChain's create_sql_query_chain + retry loop."""

    SOLUTION_ID = "langchain-sql"
    VERSION = "0.1.0"
    SUMMARY = (
        "Reference SUT scaffolded on LangChain's create_sql_query_chain with few-shot + retry."
    )
    LAYERS = (SQL_CHAIN_LAYER, FEW_SHOT_LAYER, RETRY_LOOP_LAYER)
