from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_adapter_registered() -> None:
    from beacon_benchmarks import ADAPTERS

    assert "spider2_lite" in ADAPTERS
    metadata = ADAPTERS["spider2_lite"].metadata
    assert metadata.suite == "spider2_lite_v1"


def test_preprocess_emits_drafts_with_dialect_metadata(tmp_path: Path) -> None:
    from beacon_benchmarks.spider2_lite.adapter import Spider2LiteAdapter

    root = tmp_path / "spider2"
    (root / "spider2-lite").mkdir(parents=True)
    manifest = [
        {
            "instance_id": "spider2_001",
            "db_id": "TPCH_SF1",
            "db_type": "snowflake",
            "question": "Top 5 customers?",
            "external_knowledge": "",
            "gold_sql": "SELECT c_name FROM customer LIMIT 5",
        },
        {
            "instance_id": "spider2_002",
            "db_id": "Bike_Store",
            "db_type": "sqlite",
            "question": "Number of stores",
            "external_knowledge": "",
            "gold_sql": "SELECT COUNT(*) FROM stores",
        },
        {
            "instance_id": "spider2_003",
            "db_id": "ga_sample",
            "db_type": "bigquery",
            "question": "Bounce rate?",
            "external_knowledge": "",
            "gold_sql": "SELECT AVG(bounces) FROM sessions",
        },
    ]
    with (root / "spider2-lite" / "spider2-lite.jsonl").open("w") as file:
        for item in manifest:
            file.write(json.dumps(item) + "\n")

    drafts = Spider2LiteAdapter().preprocess(root)

    assert len(drafts) == 3
    by_dialect = {draft.metadata["dialect"]: draft for draft in drafts}
    assert set(by_dialect) == {"snowflake", "sqlite", "bigquery"}
    assert by_dialect["snowflake"].context["secret_refs"] == [
        "spider2.snowflake.account",
        "spider2.snowflake.user",
        "spider2.snowflake.password",
        "spider2.snowflake.warehouse",
    ]
    assert by_dialect["sqlite"].context.get("secret_refs", []) == []
