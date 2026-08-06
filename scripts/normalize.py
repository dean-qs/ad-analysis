"""Collapse three platform schemas into one ad record, then dedupe.

Field mapping was derived from live payloads, not documentation.
Anything marked UNAVAILABLE below is genuinely absent from the source
rather than merely unmapped, and should surface as such in outputs so a
null never gets read as a zero.
"""
from datetime import datetime, timezone

FIELDS = [
    "source", "brand", "advertiser", "advertiser_id", "creative_id",
    "format", "surfaces", "headline", "body", "cta", "landing_url",
    "first_shown", "last_shown", "served_days", "media_urls",
    "needs_transcription", "dedupe_key", "geo", "spend", "impressions",
]


def _ts(v):
    if v in (None, ""):
        return None
    try:
        n = int(v)
        if n > 10_000_000:
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        pass
    return str(v)[:10]


def _days(a, b):
    try:
        d1 = datetime.fromisoformat(a)
        d2 = datetime.fromisoformat(b)
        return max((d2 - d1).days, 0)
    except Exception:  # noqa: BLE001
        return None


def from_meta(r, brand):
    s = r.get("snapshot") or {}
    body = ((s.get("body") or {}).get("markup") or {}).get("__html") or ""
    vids = [v.get("video_hd_url") or v.get("video_sd_url")
            for v in (s.get("videos") or [])]
    imgs = [i.get("original_image_url") or i.get("resized_image_url")
            for i in (s.get("images") or [])]
    media = [m for m in vids + imgs if m]
    first, last = _ts(r.get("start_date")), _ts(r.get("end_date"))
    return {
        "source": "meta", "brand": brand,
        "advertiser": r.get("page_name"), "advertiser_id": r.get("page_id"),
        "creative_id": r.get("ad_archive_id"),
        "format": (s.get("display_format") or "").upper(),
        "surfaces": r.get("publisher_platform") or [],
        "headline": s.get("title"), "body": body.strip(),
        "cta": s.get("cta_text"), "landing_url": s.get("link_url"),
        "first_shown": first, "last_shown": last,
        "served_days": _days(first, last) if first and last else None,
        "media_urls": media,
        # Meta ships almost no text for video ads. Transcription is not
        # an enhancement here, it is the analysis.
        "needs_transcription": bool(media) and not body.strip(),
        # Meta's own grouping of near-identical placement variants.
        "dedupe_key": r.get("collation_id") or r.get("ad_archive_id"),
        "geo": r.get("targeted_or_reached_countries") or [],   # [] commercial
        "spend": r.get("spend"),                # UNAVAILABLE on commercial
        "impressions": (r.get("impressions_with_index") or {})
                       .get("impressions_text"),  # UNAVAILABLE on commercial
    }


def from_google(r, brand):
    """silva95gustavo/google-ads-scraper payload."""
    out = []
    variations = r.get("variations") or [{}]
    seen = set()
    for v in variations:
        head, desc = v.get("headline"), v.get("description")
        key = (head, desc)
        if key in seen:      # actor repeats identical variations
            continue
        seen.add(key)
        media = [v.get("imageUrl")] if v.get("imageUrl") else []
        out.append({
            "source": "google", "brand": brand,
            "advertiser": r.get("advertiserName"),
            "advertiser_id": r.get("advertiserId"),
            "creative_id": r.get("creativeId"),
            "format": (r.get("format") or "").upper(),
            "surfaces": [], "headline": head, "body": desc,
            "cta": None, "landing_url": v.get("clickUrl"),
            "first_shown": _ts(r.get("firstShown")),
            "last_shown": _ts(r.get("lastShown")),
            "served_days": r.get("numServedDays"),
            "media_urls": media,
            "needs_transcription": (r.get("format") or "").upper() != "TEXT"
                                   and not (head or desc),
            "dedupe_key": f"{r.get('creativeId')}|{head}",
            "geo": r.get("creativeRegions") or [],
            "spend": None, "impressions": None,
        })
    return out


def from_linkedin(r, brand):
    return {
        "source": "linkedin", "brand": brand,
        "advertiser": r.get("advertiser"), "advertiser_id": r.get("advertiser"),
        "creative_id": r.get("id"),
        "format": (r.get("creativeType") or "").upper(),
        "surfaces": ["LINKEDIN"],
        "headline": r.get("headline"), "body": r.get("text"),
        "cta": r.get("ctaLabel"),
        # landingPageUrl is the advertiser's actual click-through target,
        # present only with fetchDetails=True and even then not on every
        # row (e.g. article ads that stay on LinkedIn). detailUrl is
        # LinkedIn's own ad-library detail page, not the ad's landing
        # page -- fall back to it only when there is nothing better.
        "landing_url": r.get("landingPageUrl") or r.get("detailUrl"),
        # Verified against a live fetchDetails=True call on 2026-08-06
        # (fixtures/linkedin_detail_genentech.json): no date field of any
        # kind comes back, on any of 10 sampled rows. LinkedIn ad dates
        # cannot currently be recovered from this actor under any tested
        # parameters. See references/actors.md.
        "first_shown": None, "last_shown": None,
        "served_days": None,
        "media_urls": [r.get("creativeImageUrl")] if r.get("creativeImageUrl") else [],
        "needs_transcription": False,   # LinkedIn ships full copy
        "dedupe_key": r.get("id"),
        "geo": [], "spend": None, "impressions": None,
    }


def normalize(raw: dict) -> list:
    """raw maps source -> {brand -> [records]}."""
    rows = []
    for brand, recs in (raw.get("meta") or {}).items():
        rows += [from_meta(r, brand) for r in recs]
    for brand, recs in (raw.get("google") or {}).items():
        for r in recs:
            rows += from_google(r, brand)
    for brand, recs in (raw.get("linkedin") or {}).items():
        rows += [from_linkedin(r, brand) for r in recs]
    return rows


def dedupe(rows: list) -> list:
    """Collapse placement variants. Keeps the longest-running instance and
    records how many were folded in, since variant count is itself a
    signal of how hard a brand pushed a given creative."""
    best = {}
    for r in rows:
        k = (r["source"], r["brand"], r["dedupe_key"])
        cur = best.get(k)
        if cur is None or (r.get("served_days") or 0) > (cur.get("served_days") or 0):
            r = dict(r, variant_count=(cur or {}).get("variant_count", 0) + 1)
            best[k] = r
        else:
            cur["variant_count"] = cur.get("variant_count", 1) + 1
    return list(best.values())
