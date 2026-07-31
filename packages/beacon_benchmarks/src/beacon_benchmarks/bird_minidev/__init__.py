"""BIRD Mini-Dev V2 adapter; auto-registers at import time."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.bird_minidev.adapter import BirdMinidevAdapter

_adapter = BirdMinidevAdapter()
register_adapter(_adapter)
