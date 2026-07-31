"""LlamaIndexSqlSUT: reference SUT modelled on LlamaIndex's NLSQLTableQueryEngine."""

from __future__ import annotations

from beacon_runner.sut.reference.base import BaseReferenceSqlSUT
from beacon_runner.types import Layer

QUERY_ENGINE_LAYER = Layer(
    name="sql_query_engine",
    description="LlamaIndex NLSQLTableQueryEngine that drives natural-language to SQL.",
    ablation_semantic="When off, the SUT uses the LLM directly without the query-engine wrapper.",
    instrumentation="wrapped",
)
SCHEMA_INTROSPECTION_LAYER = Layer(
    name="schema_introspection",
    description="Programmatic table/column inspection threaded into the prompt context.",
    ablation_semantic="When off, schema metadata is omitted from the prompt.",
    instrumentation="wrapped",
)
ROW_SAMPLING_LAYER = Layer(
    name="row_sampling",
    description="Sampled rows from candidate tables that bias the model toward valid filters.",
    ablation_semantic="When off, no row samples are appended to the prompt.",
    instrumentation="wrapped",
)


class LlamaIndexSqlSUT(BaseReferenceSqlSUT):
    """Reference SUT modelled on LlamaIndex's NLSQLTableQueryEngine pipeline."""

    SOLUTION_ID = "llama-index-sql"
    VERSION = "0.1.0"
    SUMMARY = (
        "Reference SUT scaffolded on LlamaIndex's NLSQLTableQueryEngine with schema "
        "introspection and row sampling."
    )
    LAYERS = (QUERY_ENGINE_LAYER, SCHEMA_INTROSPECTION_LAYER, ROW_SAMPLING_LAYER)
