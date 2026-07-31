"""mnemiq SUTs: in-process assembly (ablation) and HTTP client (smoke).

``MnemiqInProcessSUT`` imports the ``mnemiq`` package lazily and builds one
engine per (database, config-arm); it is the primary SUT for layer sweeps.
``MnemiqHttpSqlSUT`` is a thin client for a running ``mnemiq serve --http``
product surface with a fixed configuration; it validates the deployed
contract and is not used for ablation.
"""

from __future__ import annotations

from beacon_runner.sut.mnemiq.http_sql import MnemiqHttpSqlSUT
from beacon_runner.sut.mnemiq.in_process import MnemiqInProcessSUT
from beacon_runner.sut.mnemiq.register import register_mnemiq_solution

__all__ = [
    "MnemiqHttpSqlSUT",
    "MnemiqInProcessSUT",
    "register_mnemiq_solution",
]
