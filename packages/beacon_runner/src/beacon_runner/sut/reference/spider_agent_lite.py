"""SpiderAgentLiteSUT: reference SUT modelled on Spider-Agent-Lite."""

from __future__ import annotations

from beacon_runner.sut.reference.base import BaseReferenceSqlSUT
from beacon_runner.types import Layer

PLANNER_LAYER = Layer(
    name="planner",
    description="Spider-Agent-Lite planner that decomposes the user question into SQL sub-steps.",
    ablation_semantic="When off, the SUT skips planning and prompts the LLM end-to-end.",
    instrumentation="wrapped",
)
EXECUTOR_LAYER = Layer(
    name="executor",
    description="Step-by-step SQL executor with intermediate result handling.",
    ablation_semantic=(
        "When off, the executor returns the first candidate SQL without verification."
    ),
    instrumentation="wrapped",
)
VALIDATOR_LAYER = Layer(
    name="validator",
    description="Spider-Agent-Lite validator that confirms result shape against the question.",
    ablation_semantic=(
        "When off, the validator pass is skipped and the executor's output is returned."
    ),
    instrumentation="wrapped",
)


class SpiderAgentLiteSUT(BaseReferenceSqlSUT):
    """Reference SUT modelled on the Spider-Agent-Lite planner/executor/validator loop."""

    SOLUTION_ID = "spider-agent-lite"
    VERSION = "0.1.0"
    SUMMARY = "Reference SUT scaffolded on the Spider-Agent-Lite planner/executor/validator loop."
    LAYERS = (PLANNER_LAYER, EXECUTOR_LAYER, VALIDATOR_LAYER)
