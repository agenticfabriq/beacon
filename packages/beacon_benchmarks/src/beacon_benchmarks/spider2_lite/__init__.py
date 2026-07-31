"""Spider 2.0 Lite benchmark adapter."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.spider2_lite.adapter import Spider2LiteAdapter

register_adapter(Spider2LiteAdapter())
