"""DRBench benchmark adapter registration."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.drbench.adapter import DRBenchAdapter

register_adapter(DRBenchAdapter())
