"""Thin Apify wrapper plus the validated actor registry.

Every actor ID and quirk here was verified empirically against live runs.
See references/actors.md for the evidence behind each note.
"""
import json
import os
import time
import urllib.error
import urllib.request

APIFY_BASE = "https://api.apify.com/v2"

ACTORS = {
    # Meta. Returns snapshot.body/title/cta plus video URLs.
    # Commercial ads carry NO spend, impressions, reach or geography.
    "meta": "bo5X18oGenWEV9vVo",              # igolaizola/facebook-ad-library-scraper
    # Google discovery. Cheap, returns advertiserId, but NO ad copy.
    "google_discover": "iRsL8PTQjmWC1SaPQ",   # solidcode/ads-transparency-scraper
    # Google collection. Returns variations[].headline/description on every
    # record. Takes startUrls, not a keyword. Has built-in ocr.
    "google_collect": "N8vqwV9wL9wpIsLDz",    # silva95gustavo/google-ads-scraper
    # LinkedIn. Richest text of the three. Passing startDate/endDate
    # returns zero rows, so dates are filtered client-side.
    "linkedin": "igolaizola~linkedin-ad-library-scraper",
}


def token() -> str:
    t = os.environ.get("APIFY_TOKEN")
    if not t:
        raise RuntimeError("APIFY_TOKEN not set")
    return t


def run_actor(actor: str, payload: dict, timeout: int = 900, retries: int = 2):
    """Run an actor synchronously and return its dataset items."""
    url = (f"{APIFY_BASE}/acts/{actor}/run-sync-get-dataset-items"
           f"?token={token()}&timeout={timeout}")
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            last = f"HTTP {e.code}: {detail}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
        if attempt < retries:
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"actor {actor} failed after {retries + 1} tries: {last}")


# Bronze-tier per-item pricing, read from the Apify pricing API.
UNIT_COST = {
    "meta": 0.0005,
    "meta_detail": 0.0005,
    "google_discover": 0.001,
    "google_collect": 0.0016,
    "google_video": 0.004,
    "linkedin": 0.0005,
}


def estimate(counts: dict) -> float:
    """counts maps a UNIT_COST key to an item count."""
    return round(sum(UNIT_COST[k] * n for k, n in counts.items()), 4)
