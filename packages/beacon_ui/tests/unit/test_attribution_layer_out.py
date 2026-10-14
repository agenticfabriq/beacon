"""B79: an attribution row publishes no number nobody computed, and says which k its statistics
describe.

The engine's headline is min(3, K), so a K=1 sweep -- the default of run_fs_payments_ablation.py --
writes only a "1" entry. The endpoint read `delta_pass_at_k["3"]` through a 0.0 default, so it
published `delta_pass_at_3: 0.0` beside REAL k=1 CI and p-values: a zero inside an interval that
excludes it. The owner's decision (2026-10-14): null the fabricated delta and carry the k.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from beacon_storage.models.attribution import Attribution
from beacon_ui.api.routes.attribution import _layer_out, _layers
from beacon_ui.api.schemas.attribution import AttributionLayerOut


def _row(layer: str, per_k: dict[str, dict[str, float]]) -> Attribution:
    return Attribution(
        layer_name=layer,
        delta_pass_at_k=per_k,
        ci_low=0.1,
        ci_high=0.3,
        mcnemar_p=0.04,
        bh_adjusted_p=0.05,
        token_delta_pct=None,
        n_items_submitted=10,
        n_compared=6,
        n_baseline_excluded=1,
        n_ablated_excluded=3,
    )


def _entry(delta: float) -> dict[str, float]:
    return {"delta": delta, "ci_low": 0.1, "ci_high": 0.3, "p": 0.04}


@pytest.mark.parametrize(("passes", "k"), [(1, 1), (2, 2), (3, 3), (5, 3)])
def test_a_row_says_which_k_its_statistics_describe(passes: int, k: int) -> None:
    per_k = {str(i): _entry(0.2) for i in range(1, passes + 1)}
    assert _layer_out(_row("ontology", per_k)).k == k


@pytest.mark.parametrize("passes", [1, 2])
def test_a_sweep_without_pass_at_3_publishes_no_pass_at_3_delta(passes: int) -> None:
    out = _layer_out(_row("ontology", {str(i): _entry(0.2) for i in range(1, passes + 1)}))
    assert out.delta_pass_at_3 is None
    # The real statistics at the row's k are still published, now labelled by it.
    assert (out.ci_low, out.ci_high, out.mcnemar_p) == (0.1, 0.3, 0.04)


def test_a_sweep_with_pass_at_3_publishes_its_delta() -> None:
    out = _layer_out(_row("ontology", {str(i): _entry(0.25) for i in (1, 2, 3)}))
    assert out.delta_pass_at_3 == 0.25


def test_largest_effect_first_and_a_row_without_a_delta_last() -> None:
    """The sort read `-abs(delta_pass_at_3)` and raised on None; and a None must not sort as 0."""
    rows = [
        _row(name, per_k)
        for name, per_k in [
            ("small", {str(i): _entry(0.05) for i in (1, 2, 3)}),
            ("none", {"1": _entry(0.9)}),
            ("large", {str(i): _entry(-0.4) for i in (1, 2, 3)}),
        ]
    ]
    # `_layers` is what the endpoint calls, so this covers the order it publishes.
    assert [layer.layer for layer in _layers(rows)] == ["large", "small", "none"]


_WEBUI = Path(__file__).parents[2] / "src" / "beacon_ui" / "webui" / "index.html"


def _attribution_table() -> str:
    html = _WEBUI.read_text()
    start = html.index("const attributionBox")
    return html[start : html.index("} catch (error) { attributionBox", start)]


def test_the_webui_table_reads_only_fields_the_api_sends() -> None:
    """B80: the table read `delta_pass_at_k` and `bh_adjusted_p`, which the API never sends, so
    every delta rendered as "" and "p (BH)" showed the unadjusted p. Derived from the schema, so
    a renamed field fails here rather than rendering blank."""
    read = set(re.findall(r"\blayer\.(\w+)", _attribution_table()))
    assert read <= set(AttributionLayerOut.model_fields), read - set(
        AttributionLayerOut.model_fields
    )
    assert {"delta_pass_at_3", "k", "bh_p"} <= read


def test_the_webui_never_shows_a_missing_delta_as_a_number() -> None:
    """Coercing a null to zero would re-fabricate the value B79 removed."""
    table = _attribution_table()
    assert 'layer.delta_pass_at_3 == null ? "—"' in table
    assert not re.search(r"delta_pass_at_3\s*(\?\?|\|\|)\s*0", table)
