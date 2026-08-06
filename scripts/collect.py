"""Phase 1: collect ads for confirmed entities across Meta, Google, and LinkedIn.

`confirmed` is `config/resolved_entities.json`'s `brands` value, shaped
`{brand: {platform: [{name, id, seen, auto}, ...]}}` -- the output of
resolve_entities.py after a human has pruned it. Nothing here ever
searches by brand name; every call is scoped to a confirmed entity id.

Every parameter choice below is validated in references/actors.md
against a live run, except where noted otherwise:

- Meta: `activeStatus="all"` or a twelve-month window silently truncates
  to active-only. `fetchDetails=True` for video URLs. Scoped per entity
  via `pageIds`, never `query` -- keyword search returns unrelated pages
  mixed in with the brand (see fixtures/meta_raw_genentech.json, where a
  Facebook Marketplace apartment listing outranks real Genentech ads).
  Rows are also filtered client-side to the confirmed page_id as a
  backstop, since that fixture is the only proof we have of how noisy
  this actor's matching can be and nothing here should trust it blindly.
- Google: startUrls built from advertiserId, against google_collect
  (silva95gustavo) with skipDetails=False and ocr=True. Never
  google_discover (solidcode); that actor returns no ad copy.
- LinkedIn: never pass startDate/endDate, they return zero rows.
  fetchDetails=True is requested anyway because it adds landingPageUrl
  (the real click-through target) and detailPayer (who actually paid,
  sometimes an agency rather than the brand). Verified live against
  fixtures/linkedin_detail_genentech.json on 2026-08-06: NO date field
  of any kind comes back, on any of 10 sampled rows, under any tested
  parameters. LinkedIn results cannot currently be bounded by
  date_from/date_to at all -- this is a real limitation, not a TODO.
  date_from/date_to are still accepted and checked defensively in case
  a future actor version adds a date field; today that check is always
  a no-op and prints a warning saying so, rather than silently dropping
  or silently trusting an unverified field name.
"""
import concurrent.futures
import json
from pathlib import Path

from apify_client import ACTORS, UNIT_COST, run_actor
import normalize

MAX_WORKERS = 6

# Candidate keys for LinkedIn's detail-fetch date field. Checked against
# a live fetchDetails=True call (fixtures/linkedin_detail_genentech.json)
# and NONE of these appear -- the actor exposes no date field at all.
# Kept as a defensive check in case a future actor version adds one; see
# module docstring.
LINKEDIN_DATE_KEYS = ("firstShown", "lastShown", "startedAt", "createdAt",
                      "adStartDate", "adEndDate")


def _clip(rows, cap):
    return rows[:cap] if cap else rows


def _meta_call(entity, region, date_from, date_to, cap):
    payload = {
        "pageIds": [entity["id"]],
        "country": region,
        "activeStatus": "all",
        "fetchDetails": True,
        "minDate": date_from,
        "maxDate": date_to,
        "maxItems": cap,
    }
    rows = run_actor(ACTORS["meta"], payload)
    kept = [r for r in rows if r.get("page_id") == entity["id"]]
    dropped = len(rows) - len(kept)
    if dropped:
        print(f"    ! meta {entity['name']}: dropped {dropped} row(s) not "
              f"matching page_id {entity['id']} (actor returned unrelated pages)")
    return _clip(kept, cap)


def _google_call(entity, region, cap):
    payload = {
        "startUrls": [
            f"https://adstransparency.google.com/advertiser/{entity['id']}?region={region}"
        ],
        "skipDetails": False,
        "ocr": True,
        "maxItems": cap,
    }
    rows = run_actor(ACTORS["google_collect"], payload)
    return _clip(rows, cap)


def _linkedin_in_window(row, date_from, date_to):
    """Returns (keep, was_known). was_known is False when no candidate
    date key was present, so callers can warn instead of silently
    trusting an unverified field name."""
    found = [str(row[k])[:10] for k in LINKEDIN_DATE_KEYS if row.get(k)]
    if not found:
        return True, False
    in_window = any(date_from <= d <= date_to for d in found)
    return in_window, True


def _linkedin_call(entity, region, cap, date_from, date_to):
    payload = {
        "keyword": entity["name"],
        "countries": [region],
        "fetchDetails": True,
        "maxItems": cap,
    }
    rows = run_actor(ACTORS["linkedin"], payload)
    kept, unknown = [], 0
    for r in rows:
        keep, known = _linkedin_in_window(r, date_from, date_to)
        if not known:
            unknown += 1
        if keep:
            kept.append(r)
    if unknown:
        print(f"    ! linkedin {entity['name']}: {unknown} row(s) had no "
              f"recognized date field, kept unfiltered -- verify by eye")
    return _clip(kept, cap)


CALLERS = {"meta": _meta_call, "google": _google_call, "linkedin": _linkedin_call}


def _entity_jobs(confirmed, platforms):
    jobs = []
    for brand, plats in confirmed.items():
        for plat in platforms:
            for entity in plats.get(plat, []):
                jobs.append((brand, plat, entity))
    return jobs


def estimate_run(confirmed, platforms, cap):
    """Itemized worst-case cost per platform, keyed by entity count x cap.
    Google is split into a no-video floor and an all-video ceiling since
    the per-item rate depends on format, which is unknown before the
    call runs. Returns (rows, total_lo, total_hi); rows are
    (platform, entity_count, worst_case_items, cost_lo, cost_hi)."""
    counts = {p: sum(len(v.get(p, [])) for v in confirmed.values())
              for p in platforms}
    rows, total_lo, total_hi = [], 0.0, 0.0
    for p in platforms:
        n = counts.get(p, 0)
        units = n * cap
        if p == "meta":
            lo = hi = units * (UNIT_COST["meta"] + UNIT_COST["meta_detail"])
        elif p == "google":
            lo = units * UNIT_COST["google_collect"]
            hi = units * (UNIT_COST["google_collect"] + UNIT_COST["google_video"])
        elif p == "linkedin":
            lo = hi = units * UNIT_COST["linkedin"]
        else:
            continue
        rows.append((p, n, units, lo, hi))
        total_lo += lo
        total_hi += hi
    return rows, round(total_lo, 4), round(total_hi, 4)


def print_estimate(confirmed, platforms, cap):
    rows, lo, hi = estimate_run(confirmed, platforms, cap)
    print("Cost estimate -- worst case, every entity hits the cap")
    print(f"({cap} ads/entity cap, {len(platforms)} platform(s))\n")
    for p, n, units, plo, phi in rows:
        cap_note = f"{n} entities x {cap} cap = {units} items"
        if abs(plo - phi) < 1e-9:
            print(f"  {p:<9} {cap_note:<32} ~${plo:.2f}")
        else:
            print(f"  {p:<9} {cap_note:<32} ~${plo:.2f} - ${phi:.2f}  "
                  f"(range: no ads video -> all ads video)")
    print()
    if abs(lo - hi) < 1e-9:
        print(f"  total worst case: ~${lo:.2f}")
    else:
        print(f"  total worst case: ~${lo:.2f} - ${hi:.2f}")
    print("  Real cost is usually well below this: most entities will not "
          "hit the cap,\n  and not every Google ad is video.")
    return rows, lo, hi


def collect_all(confirmed: dict, region: str, date_from: str,
                 date_to: str, platforms: list, cap: int) -> list:
    """Runs one actor call per confirmed entity, bounded at MAX_WORKERS
    concurrent calls. Always prints the itemized cost estimate before
    making any call. Writes out/ads_raw.json (raw records grouped
    source -> brand -> records) and out/ads_normalized.json (normalized,
    deduped), and returns the normalized rows.

    This function does not itself block on an interactive confirmation:
    it is called directly by the Streamlit app, where the cost estimate
    shown in the UI and the button press are the confirmation step. Run
    this module as a script for a confirmation prompt on the command
    line.
    """
    print_estimate(confirmed, platforms, cap)

    jobs = _entity_jobs(confirmed, platforms)
    raw = {p: {} for p in platforms}
    errors = []

    def work(job):
        brand, plat, entity = job
        try:
            if plat == "meta":
                rows = _meta_call(entity, region, date_from, date_to, cap)
            elif plat == "google":
                rows = _google_call(entity, region, cap)
            elif plat == "linkedin":
                rows = _linkedin_call(entity, region, cap, date_from, date_to)
            else:
                rows = []
            return job, rows, None
        except Exception as e:  # noqa: BLE001
            return job, [], str(e)[:300]

    print(f"\nRunning {len(jobs)} entity call(s), bounded at {MAX_WORKERS} concurrent\n")
    with concurrent.futures.ThreadPoolExecutor(MAX_WORKERS) as ex:
        for (brand, plat, entity), rows, err in ex.map(work, jobs):
            if err:
                errors.append((brand, plat, entity["name"], err))
                print(f"  ! {brand:<20} {plat:<9} {entity['name']:<25} FAILED: {err}")
                continue
            raw[plat].setdefault(brand, []).extend(rows)
            print(f"  . {brand:<20} {plat:<9} {entity['name']:<25} {len(rows)} rows")

    if errors:
        print(f"\n{len(errors)} of {len(jobs)} entity call(s) failed; keeping "
              f"partial results.")
        for brand, plat, name, err in errors:
            print(f"  {brand} / {plat} / {name}: {err}")

    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "ads_raw.json").write_text(json.dumps(raw, indent=1))

    rows = normalize.normalize(raw)
    rows = normalize.dedupe(rows)
    (out_dir / "ads_normalized.json").write_text(json.dumps(rows, indent=1))

    print(f"\nWrote out/ads_raw.json and out/ads_normalized.json "
          f"({len(rows)} normalized rows)")
    return rows


def main():
    import argparse
    import yaml

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/competitive_set.example.yaml")
    ap.add_argument("--entities", default="config/resolved_entities.json")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    confirmed = json.loads(Path(a.entities).read_text())["brands"]
    platforms = ["meta", "google", "linkedin"]
    cap = cfg.get("limits", {}).get("max_ads_per_brand_per_platform", 400)

    print_estimate(confirmed, platforms, cap)
    ans = input("\nProceed with this live run? [y/N] ").strip().lower()
    if ans != "y":
        print("Aborted, nothing spent.")
        return

    collect_all(confirmed, cfg["region"], str(cfg["date_from"]),
                str(cfg["date_to"]), platforms, cap)


if __name__ == "__main__":
    main()
