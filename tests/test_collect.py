"""Tests for scripts/collect.py, run entirely against fixtures/. Never
call apify_client.run_actor for real -- every test monkeypatches it so
this suite costs nothing and runs offline.
"""
import json

import pytest

import collect
from apify_client import ACTORS

GENENTECH_META_PAGE = "134743529575"
GENENTECH_META_PAGE_2 = "146718898672043"


def _entity(name, id_):
    return {"name": name, "id": id_, "seen": 1, "auto": True}


# --------------------------------------------------------------- meta


def test_meta_call_payload_shape(monkeypatch, fixtures):
    captured = {}

    def fake_run_actor(actor, payload):
        captured["actor"] = actor
        captured["payload"] = payload
        return []

    monkeypatch.setattr(collect, "run_actor", fake_run_actor)
    collect._meta_call(_entity("Genentech", GENENTECH_META_PAGE), "US",
                        "2025-08-06", "2026-08-06", 400)

    assert captured["actor"] == ACTORS["meta"]
    p = captured["payload"]
    assert p["pageIds"] == [GENENTECH_META_PAGE]
    assert p["activeStatus"] == "all"          # else a 12mo window truncates
    assert p["fetchDetails"] is True           # else no video URLs
    assert p["minDate"] == "2025-08-06"
    assert p["maxDate"] == "2026-08-06"
    assert "query" not in p                    # never keyword-search to collect


def test_meta_call_filters_to_confirmed_page_id(monkeypatch, fixtures):
    """meta_raw_genentech.json is a keyword-search-shaped response mixing
    13 real Genentech records into 30 unrelated ones (a Facebook
    Marketplace listing, etc). Even though collect.py requests a
    pageIds-scoped call, it must not trust the actor to have filtered
    server-side -- this is the regression this fixture exists to catch.
    """
    raw = fixtures("meta_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    kept = collect._meta_call(_entity("Genentech", GENENTECH_META_PAGE),
                               "US", "2025-08-06", "2026-08-06", 400)

    assert len(kept) > 0
    assert all(r["page_id"] == GENENTECH_META_PAGE for r in kept)
    assert not any(r.get("page_name") == "Connor Griffin" for r in kept)


def test_meta_call_respects_cap(monkeypatch, fixtures):
    raw = fixtures("meta_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    kept = collect._meta_call(_entity("Genentech", GENENTECH_META_PAGE),
                               "US", "2025-08-06", "2026-08-06", cap=2)
    assert len(kept) <= 2


def test_meta_call_never_reads_spend_as_zero(monkeypatch, fixtures):
    """Commercial ads carry null spend/impressions/geo. Prove the raw
    record collect.py passes through still has nulls, not zeros --
    normalize.py is what renders them as unavailable, but collect.py
    must not coerce them first."""
    raw = fixtures("meta_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    kept = collect._meta_call(_entity("Genentech", GENENTECH_META_PAGE),
                               "US", "2025-08-06", "2026-08-06", 400)
    assert all(r.get("spend") is None for r in kept)
    assert all(r.get("reach_estimate") is None for r in kept)


# ------------------------------------------------------------- google


def test_google_call_payload_shape(monkeypatch, fixtures):
    captured = {}

    def fake_run_actor(actor, payload):
        captured["actor"] = actor
        captured["payload"] = payload
        return []

    monkeypatch.setattr(collect, "run_actor", fake_run_actor)
    collect._google_call(_entity("Genentech", "AR04097710039484071937"),
                          "US", 400)

    assert captured["actor"] == ACTORS["google_collect"]   # never google_discover
    p = captured["payload"]
    assert p["startUrls"] == [
        "https://adstransparency.google.com/advertiser/AR04097710039484071937?region=US"]
    assert p["skipDetails"] is False           # else no ad copy
    assert p["ocr"] is True


def test_google_call_passes_rows_through_unchanged(monkeypatch, fixtures):
    """Inner-variations dedupe is normalize.py's job, not collect.py's --
    collect.py should not pre-process the records it gets back."""
    raw = fixtures("google_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    rows = collect._google_call(_entity("Genentech", "AR04097710039484071937"),
                                 "US", 400)
    assert rows == raw


def test_google_call_respects_cap(monkeypatch, fixtures):
    raw = fixtures("google_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    rows = collect._google_call(_entity("Genentech", "AR04097710039484071937"),
                                 "US", cap=3)
    assert len(rows) == 3


# ----------------------------------------------------------- linkedin


def test_linkedin_call_never_sends_start_or_end_date(monkeypatch, fixtures):
    captured = {}

    def fake_run_actor(actor, payload):
        captured["payload"] = payload
        return []

    monkeypatch.setattr(collect, "run_actor", fake_run_actor)
    collect._linkedin_call(_entity("Genentech", "Genentech"), "US", 400,
                            "2025-08-06", "2026-08-06")

    p = captured["payload"]
    assert "startDate" not in p
    assert "endDate" not in p
    assert p["keyword"] == "Genentech"
    assert p["countries"] == ["US"]


def test_linkedin_call_keeps_rows_with_no_date_field_and_warns(monkeypatch, fixtures, capsys):
    """Neither linkedin fixture has fetchDetails on, so no date field is
    present. A row with no recognized date key must be kept, not
    dropped, since we cannot yet tell whether it falls in the window --
    see fixtures/README.md."""
    raw = fixtures("linkedin_raw_genentech.json")
    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)

    rows = collect._linkedin_call(_entity("Genentech", "Genentech"), "US", 400,
                                   "2025-08-06", "2026-08-06")
    assert len(rows) == len(raw)
    assert "no recognized date field" in capsys.readouterr().out


def test_linkedin_detail_fetch_has_no_date_field(monkeypatch, fixtures, capsys):
    """Live-verified 2026-08-06 against fixtures/linkedin_detail_genentech.json:
    fetchDetails=True adds landingPageUrl and detailPayer but no date
    field of any kind. All rows must be kept (nothing to filter on), and
    a warning must fire since this is exactly the case the warning
    exists for."""
    raw = fixtures("linkedin_detail_genentech.json")
    assert not any(k in row for row in raw for k in collect.LINKEDIN_DATE_KEYS)

    monkeypatch.setattr(collect, "run_actor", lambda actor, payload: raw)
    rows = collect._linkedin_call(_entity("Genentech", "Genentech"), "US", 400,
                                   "2025-08-06", "2026-08-06")
    assert len(rows) == len(raw)
    assert "no recognized date field" in capsys.readouterr().out


def test_linkedin_in_window_respects_a_recognized_date_key():
    row = {"firstShown": "2026-01-01"}
    keep, known = collect._linkedin_in_window(row, "2025-08-06", "2026-08-06")
    assert (keep, known) == (True, True)

    row_out_of_window = {"firstShown": "2020-01-01"}
    keep, known = collect._linkedin_in_window(row_out_of_window,
                                               "2025-08-06", "2026-08-06")
    assert (keep, known) == (False, True)


# ------------------------------------------------------------ estimate


def test_estimate_run_meta_uses_result_plus_detail_rate():
    confirmed = {"Genentech": {"meta": [_entity("Genentech", "1")]}}
    rows, lo, hi = collect.estimate_run(confirmed, ["meta"], cap=400)
    assert rows[0][0] == "meta"
    assert rows[0][1] == 1              # 1 entity
    assert rows[0][2] == 400            # worst-case items
    assert lo == hi == round(400 * (0.0005 + 0.0005), 4)


def test_estimate_run_google_has_a_video_upcharge_range():
    confirmed = {"Genentech": {"google": [_entity("Genentech", "AR1")]}}
    rows, lo, hi = collect.estimate_run(confirmed, ["google"], cap=400)
    plat, n, units, plo, phi = rows[0]
    assert plo == pytest.approx(400 * 0.0016)
    assert phi == pytest.approx(400 * (0.0016 + 0.004))
    assert phi > plo


def test_estimate_run_zero_entities_is_zero_cost():
    confirmed = {"Genentech": {}}
    rows, lo, hi = collect.estimate_run(confirmed, ["meta", "google", "linkedin"], cap=400)
    assert lo == hi == 0.0


def test_print_estimate_runs_without_error(capsys):
    confirmed = {"Genentech": {"meta": [_entity("Genentech", "1")],
                               "google": [_entity("Genentech", "AR1")],
                               "linkedin": [_entity("Genentech", "Genentech")]}}
    collect.print_estimate(confirmed, ["meta", "google", "linkedin"], 400)
    out = capsys.readouterr().out
    assert "meta" in out and "google" in out and "linkedin" in out
    assert "total worst case" in out


# ---------------------------------------------------------- end to end


def _dispatch_fixture(fixtures):
    by_actor = {
        ACTORS["meta"]: fixtures("meta_raw_genentech.json"),
        ACTORS["google_collect"]: fixtures("google_raw_genentech.json"),
        ACTORS["linkedin"]: fixtures("linkedin_raw_genentech.json"),
    }

    def fake_run_actor(actor, payload):
        return by_actor[actor]
    return fake_run_actor


def test_collect_all_writes_raw_and_normalized_outputs(monkeypatch, fixtures, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(collect, "run_actor", _dispatch_fixture(fixtures))

    confirmed = {
        "Genentech": {
            "meta": [_entity("Genentech", GENENTECH_META_PAGE)],
            "google": [_entity("Genentech USA", "AR04097710039484071937")],
            "linkedin": [_entity("Genentech", "Genentech")],
        }
    }

    rows = collect.collect_all(confirmed, "US", "2025-08-06", "2026-08-06",
                                ["meta", "google", "linkedin"], cap=400)

    raw_path = tmp_path / "out" / "ads_raw.json"
    norm_path = tmp_path / "out" / "ads_normalized.json"
    assert raw_path.exists()
    assert norm_path.exists()

    raw = json.loads(raw_path.read_text())
    assert raw["meta"]["Genentech"]
    assert all(r["page_id"] == GENENTECH_META_PAGE for r in raw["meta"]["Genentech"])

    assert rows == json.loads(norm_path.read_text())
    assert len(rows) > 0
    assert {r["source"] for r in rows} == {"meta", "google", "linkedin"}
    assert all(r["brand"] == "Genentech" for r in rows)


def test_collect_all_keeps_partial_results_on_one_platform_failure(monkeypatch, fixtures, tmp_path):
    monkeypatch.chdir(tmp_path)
    good = _dispatch_fixture(fixtures)

    def flaky_run_actor(actor, payload):
        if actor == ACTORS["google_collect"]:
            raise RuntimeError("HTTP 500: actor timed out")
        return good(actor, payload)

    monkeypatch.setattr(collect, "run_actor", flaky_run_actor)

    confirmed = {
        "Genentech": {
            "meta": [_entity("Genentech", GENENTECH_META_PAGE)],
            "google": [_entity("Genentech USA", "AR04097710039484071937")],
            "linkedin": [_entity("Genentech", "Genentech")],
        }
    }

    rows = collect.collect_all(confirmed, "US", "2025-08-06", "2026-08-06",
                                ["meta", "google", "linkedin"], cap=400)

    assert len(rows) > 0
    assert {r["source"] for r in rows} == {"meta", "linkedin"}   # google failed

    raw = json.loads((tmp_path / "out" / "ads_raw.json").read_text())
    assert raw["google"] == {}   # nothing succeeded for google
