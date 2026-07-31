"""FDABench benchmark adapter registration."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.fdabench.adapter import FDABenchAdapter

register_adapter(FDABenchAdapter())
