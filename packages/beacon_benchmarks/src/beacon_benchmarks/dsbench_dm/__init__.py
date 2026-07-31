"""DSBench-DM benchmark adapter registration."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.dsbench_dm.adapter import DSBenchDMAdapter

register_adapter(DSBenchDMAdapter())
