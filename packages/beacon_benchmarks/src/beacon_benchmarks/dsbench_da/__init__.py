"""DSBench-DA benchmark adapter registration."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.dsbench_da.adapter import DSBenchDAAdapter

register_adapter(DSBenchDAAdapter())
