"""Tests for scripts/analyze.py. Pure computation over rows already in
memory -- no network, no LLM, no mocking boundary needed.
"""
import json

import pytest

import analyze as az


def _row(brand, source, strategy_themes=None, topic_themes=None, served_days=None):
    return {"brand": brand, "source": source,
            "strategy_themes": strategy_themes or [],
            "topic_themes": topic_themes or [],
            "served_days": served_days}


# ------------------------------------------------------------- mix


def test_mix_matrix_normalizes_within_brand_not_raw_count():
    rows = ([_row("A", "meta", ["S01"]), _row("A", "meta", [])]
            + [_row("B", "meta", ["S01"]), _row("B", "meta", ["S01"]),
               _row("B", "meta", []), _row("B", "meta", [])])
    m = az.mix_matrix(rows, "strategy_themes")
    assert m.loc["S01", "A"] == 0.5
    assert m.loc["S01", "B"] == 0.5   # same share despite 2x the raw count


def test_mix_matrix_empty_when_no_themes_anywhere():
    rows = [_row("A", "meta", [])]
    m = az.mix_matrix(rows, "strategy_themes")
    assert m.empty


def test_mix_matrix_multi_label_counts_row_toward_every_theme():
    rows = [_row("A", "meta", ["S01", "S02"]), _row("A", "meta", ["S01"])]
    m = az.mix_matrix(rows, "strategy_themes")
    assert m.loc["S01", "A"] == 1.0    # both rows carry S01
    assert m.loc["S02", "A"] == 0.5    # only one row carries S02


# -------------------------------------------------------- persistence


def test_persistence_matrix_weights_by_served_days():
    rows = [_row("A", "meta", ["S01"], served_days=90),
            _row("A", "meta", ["S02"], served_days=10)]
    m = az.persistence_matrix(rows, "strategy_themes")
    assert m.loc["S01", "A"] == 0.9
    assert m.loc["S02", "A"] == 0.1


def test_persistence_matrix_denominator_includes_untagged_ads():
    """Same bug class as mix_matrix's untagged-ad test: an untagged ad's
    served_days must still count in the brand's total, or persistence
    share overstates every theme that did get tagged."""
    rows = [_row("A", "meta", ["S01"], served_days=10),
            _row("A", "meta", [], served_days=90)]   # untagged, but still ran 90 days
    m = az.persistence_matrix(rows, "strategy_themes")
    assert m.loc["S01", "A"] == 0.1   # 10 / (10 + 90), not 10/10=1.0


def test_persistence_matrix_floors_missing_served_days_to_one():
    """LinkedIn's served_days is always None -- verified in transcribe/
    collect work, no fixture or live call ever returned one. Those rows
    must weigh in at the floor, not vanish or error."""
    rows = [_row("A", "linkedin", ["S01"], served_days=None),
            _row("A", "meta", ["S02"], served_days=9)]
    m = az.persistence_matrix(rows, "strategy_themes")
    assert m.loc["S01", "A"] == 0.1
    assert m.loc["S02", "A"] == 0.9


# ------------------------------------------------------- distinctiveness


def test_distinctiveness_ratio_flags_overindexing_brand():
    rows = ([_row("A", "meta", ["S01"])] * 8 + [_row("A", "meta", [])] * 2
            + [_row("B", "meta", ["S01"])] * 2 + [_row("B", "meta", [])] * 8
            + [_row("C", "meta", ["S01"])] * 2 + [_row("C", "meta", [])] * 8)
    mix = az.mix_matrix(rows, "strategy_themes")
    dist = az.distinctiveness(mix)
    # A=0.8, B=C=0.2, average=0.4 -> A's ratio 2.0, B/C's ratio 0.5
    assert dist.loc["S01", "A"] == pytest.approx(2.0)
    assert dist.loc["S01", "B"] == pytest.approx(0.5)
    assert dist.loc["S01", "A"] >= az.DISTINCTIVE_RATIO
    assert dist.loc["S01", "B"] < az.DISTINCTIVE_RATIO


def test_distinctiveness_empty_input_returns_empty():
    assert az.distinctiveness(az.mix_matrix([], "strategy_themes")).empty


# ---------------------------------------------------------- table stakes


def test_table_stakes_requires_every_brand_above_threshold():
    rows = ([_row("A", "meta", ["S01"])] * 5 + [_row("A", "meta", [])] * 5   # 50%
            + [_row("B", "meta", ["S01"])] * 1 + [_row("B", "meta", [])] * 9  # 10%
            + [_row("C", "meta", ["S01"])] * 0 + [_row("C", "meta", [])] * 10  # 0%
            + [_row("A", "meta", ["S02"])] * 3 + [_row("A", "meta", [])] * 7
            + [_row("B", "meta", ["S02"])] * 4 + [_row("B", "meta", [])] * 6
            + [_row("C", "meta", ["S02"])] * 5 + [_row("C", "meta", [])] * 5)
    mix = az.mix_matrix(rows, "strategy_themes")
    stakes = az.table_stakes(mix)
    assert "S01" not in stakes   # brand C never runs it
    assert "S02" in stakes       # every brand clears 10%


def test_table_stakes_empty_matrix_returns_empty_list():
    assert az.table_stakes(az.mix_matrix([], "strategy_themes")) == []


# ------------------------------------------------------ platform register


def test_platform_register_splits_by_brand_and_platform():
    rows = [_row("A", "meta", ["S01"]), _row("A", "linkedin", ["S02"])]
    reg = az.platform_register(rows, "strategy_themes")
    assert set(reg.keys()) == {"A"}
    table = reg["A"]
    assert table.loc["S01", "meta"] == 1.0
    assert table.loc["S02", "linkedin"] == 1.0


def test_register_divergence_identical_platforms_is_zero():
    rows = [_row("A", "meta", ["S01"]), _row("A", "linkedin", ["S01"])]
    reg = az.platform_register(rows, "strategy_themes")
    scores = az.register_divergence(reg)
    assert scores["A"] == 0.0


def test_register_divergence_disjoint_platforms_is_one():
    """This is HANDOFF's own Genentech example: a brand saying
    completely different things on different platforms."""
    rows = [_row("A", "meta", ["S01"]), _row("A", "linkedin", ["S02"])]
    reg = az.platform_register(rows, "strategy_themes")
    scores = az.register_divergence(reg)
    assert scores["A"] == 1.0


def test_register_divergence_none_when_only_one_platform():
    rows = [_row("A", "meta", ["S01"]), _row("A", "meta", ["S02"])]
    reg = az.platform_register(rows, "strategy_themes")
    scores = az.register_divergence(reg)
    assert scores["A"] is None


# ------------------------------------------------------------ analyze_all


def test_analyze_all_writes_json_and_covers_both_tiers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    rows = [_row("A", "meta", ["S01"], ["T01"], served_days=5)]
    result = az.analyze_all(rows)

    assert (tmp_path / "out" / "analysis.json").exists()
    assert "strategy" in result and "topic" in result
    assert result["strategy"]["mix"]["A"]["S01"] == 1.0
    assert result["topic"]["mix"]["A"]["T01"] == 1.0

    on_disk = json.loads((tmp_path / "out" / "analysis.json").read_text())
    assert on_disk == result


def test_analyze_all_attaches_labels_from_codebook(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    rows = [_row("A", "meta", ["S01"])]
    codebook = {"strategy_themes": [{"id": "S01", "label": "Access"}],
               "topic_themes": [{"id": "T01", "label": "Oncology"}]}
    result = az.analyze_all(rows, codebook)
    assert result["labels"] == {"S01": "Access", "T01": "Oncology"}


def test_analyze_all_handles_completely_uncoded_corpus(monkeypatch, tmp_path):
    """No row has ever been through codebook.apply() -- must not crash."""
    monkeypatch.chdir(tmp_path)
    rows = [{"brand": "A", "source": "meta", "served_days": 3}]
    result = az.analyze_all(rows)
    assert result["strategy"]["mix"] == {}
    assert result["strategy"]["table_stakes"] == []
