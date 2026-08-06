"""Phase 0: resolve brand names to platform entity IDs.

Keyword search is noisy on every platform. Searching "Genentech" on Meta
returned a Facebook Marketplace apartment listing. This step surfaces
candidates with evidence so a human can confirm before any bulk spend.

Output: config/resolved_entities.json, which the collector reads.
Nothing downstream ever searches by brand name again.
"""
import argparse
import concurrent.futures
import json
import re
from collections import Counter
from pathlib import Path

from apify_client import ACTORS, estimate, run_actor

PROBE_N = 25


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def probe_meta(brand: str, region: str):
    rows = run_actor(ACTORS["meta"], {
        "query": brand, "country": region, "category": "all",
        "activeStatus": "all", "sortBy": "mostRecent",
        "maxItems": PROBE_N, "fetchDetails": False})
    counts = Counter((r.get("page_name"), r.get("page_id")) for r in rows)
    return [{"name": n, "id": i, "seen": c,
             "auto": _norm(brand) in _norm(n)}
            for (n, i), c in counts.most_common() if n]


def probe_google(brand: str, region: str):
    rows = run_actor(ACTORS["google_discover"], {
        "searchQuery": brand, "region": region, "maxResults": PROBE_N})
    counts = Counter((r.get("advertiserName"), r.get("advertiserId"))
                     for r in rows)
    return [{"name": n, "id": i, "seen": c,
             "auto": _norm(brand) in _norm(n)}
            for (n, i), c in counts.most_common() if n]


def probe_linkedin(brand: str, region: str):
    # startDate/endDate break this actor. Filter dates after collection.
    rows = run_actor(ACTORS["linkedin"], {
        "keyword": brand, "countries": [region], "maxItems": PROBE_N,
        "fetchDetails": False})
    counts = Counter(r.get("advertiser") for r in rows)
    return [{"name": n, "id": n, "seen": c, "auto": _norm(brand) in _norm(n)}
            for n, c in counts.most_common() if n]


PROBES = {"meta": probe_meta, "google": probe_google,
          "linkedin": probe_linkedin}


def resolve(brands, region, platforms):
    jobs = [(b, p) for b in brands for p in platforms]
    est = estimate({
        "meta": PROBE_N * len(brands) * ("meta" in platforms),
        "google_discover": PROBE_N * len(brands) * ("google" in platforms),
        "linkedin": PROBE_N * len(brands) * ("linkedin" in platforms)})
    print(f"[estimate] {len(jobs)} probes x {PROBE_N} items = ~${est}\n")

    out = {b: {} for b in brands}

    def work(job):
        brand, plat = job
        try:
            return job, PROBES[plat](brand, region), None
        except Exception as e:  # noqa: BLE001
            return job, [], str(e)[:200]

    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        for (brand, plat), cands, err in ex.map(work, jobs):
            out[brand][plat] = {"candidates": cands, "error": err}
            mark = "!" if err else " "
            hits = sum(1 for c in cands if c["auto"])
            print(f" {mark} {brand:<22} {plat:<9} "
                  f"{len(cands)} candidates, {hits} auto-matched"
                  + (f"  ERROR {err}" if err else ""))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="+", required=True)
    ap.add_argument("--region", default="US")
    ap.add_argument("--platforms", nargs="+",
                    default=["meta", "google", "linkedin"])
    ap.add_argument("--out", default="config/resolved_entities.json")
    a = ap.parse_args()

    res = resolve(a.brands, a.region, a.platforms)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"region": a.region, "brands": res}, indent=1))
    print(f"\nwrote {p}")
    print("Review and prune before running collect.py. "
          "Auto-matched entities are suggestions, not confirmations.")


if __name__ == "__main__":
    main()
