"""Tests for scripts/normalize.py's LinkedIn field mapping, run against
the real fetchDetails=True fixture that caught the landing_url bug.
"""
import normalize


def test_linkedin_landing_url_prefers_landingPageUrl_over_detailUrl(fixtures):
    """detailUrl is LinkedIn's own ad-library detail page, not the ad's
    landing page. landingPageUrl (only present with fetchDetails=True,
    and not on every row) is the real click-through target and must win
    when present."""
    raw = fixtures("linkedin_detail_genentech.json")
    with_landing = next(r for r in raw if r.get("landingPageUrl"))
    row = normalize.from_linkedin(with_landing, "Genentech")
    assert row["landing_url"] == with_landing["landingPageUrl"]
    assert row["landing_url"] != with_landing["detailUrl"]


def test_linkedin_landing_url_falls_back_to_detailUrl_when_absent(fixtures):
    raw = fixtures("linkedin_detail_genentech.json")
    without_landing = next(r for r in raw if not r.get("landingPageUrl"))
    row = normalize.from_linkedin(without_landing, "Genentech")
    assert row["landing_url"] == without_landing["detailUrl"]


def test_linkedin_no_date_fields_populate_from_detail_fetch(fixtures):
    """Documents the verified limitation: even with fetchDetails=True,
    normalize.py has nothing to read a date from."""
    raw = fixtures("linkedin_detail_genentech.json")
    for r in raw:
        row = normalize.from_linkedin(r, "Genentech")
        assert row["first_shown"] is None
        assert row["last_shown"] is None
