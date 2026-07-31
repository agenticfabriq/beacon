"""DABStep benchmark adapter."""

from beacon_benchmarks.base.registry import register_adapter
from beacon_benchmarks.dabstep.adapter import DabstepAdapter

register_adapter(DabstepAdapter())
