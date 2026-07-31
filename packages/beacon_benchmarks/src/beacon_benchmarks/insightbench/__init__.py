"""InsightBench benchmark adapter."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.insightbench.adapter import InsightBenchAdapter

register_adapter(InsightBenchAdapter())
