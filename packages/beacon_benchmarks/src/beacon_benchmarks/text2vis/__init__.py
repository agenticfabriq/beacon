"""Text2Vis benchmark adapter registration."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.text2vis.adapter import Text2VisAdapter

register_adapter(Text2VisAdapter())
