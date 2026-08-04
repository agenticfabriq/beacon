"""Minimum-viable reference SUTs Beacon ships alongside ``DummySUT``.

These wrappers implement the :class:`~beacon_runner.sut.SolutionUnderTest`
Protocol with a layered prompt-then-invoke loop driven by a pluggable
``LLMProvider``. They are deliberately self-contained: they neither pull in
the heavy LangChain / LlamaIndex / Spider-Agent-Lite frameworks as runtime
dependencies, nor make hidden HTTP calls. Wire a real
:class:`beacon_graders.llm.provider.LLMProvider` (e.g.
:class:`~beacon_graders.llm.openai_provider.OpenAICompatibleProvider`) at
construction time and the SUT will fan out a layered call per item.

To plug an actual framework (LangChain SQLChain, LlamaIndex
``NLSQLTableQueryEngine``, Spider-Agent-Lite agent runner) replace the
``_compose_*`` hooks on the relevant subclass; the rest of the trace shape and
layer accounting stays the same.
"""

from __future__ import annotations

from beacon_runner.sut.reference.base import BaseReferenceSqlSUT
from beacon_runner.sut.reference.langchain_sql import LangchainSqlSUT
from beacon_runner.sut.reference.llama_index_sql import LlamaIndexSqlSUT
from beacon_runner.sut.reference.spider_agent_lite import SpiderAgentLiteSUT

__all__ = [
    "BaseReferenceSqlSUT",
    "LangchainSqlSUT",
    "LlamaIndexSqlSUT",
    "SpiderAgentLiteSUT",
]
