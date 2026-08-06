"""Phase 4: brand x theme matrices, two measures, three derived cuts.

HANDOFF.md gives this module no function signature (unlike the other
four), and the Streamlit Analyze tab never actually called into a
module -- it did its own inline pd.crosstab against a flat "themes"
column that codebook.py's two-tier output no longer produces (that's
the KeyError this module exists to fix properly, not route around).

Two-tier handling, per the user: strategy_themes is the primary tier
(full treatment), topic_themes gets the identical matrices as a
supplementary cut, not the headline analysis.

Measures (both normalize within brand -- raw ad count is not
comparable across brands with different budgets):
- mix: share of a brand's own ads carrying a theme
- persistence_weighted: theme share weighted by served_days. LinkedIn's
  served_days is always None (verified in transcribe/collect work --
  the actor exposes no date field at all), so every LinkedIn row weighs
  in at the floor of 1. This is a real, stateable limitation: LinkedIn's
  contribution to persistence-weighted numbers reflects ad count, not
  actual persistence, unlike Meta and Google.

Derived cuts:
- distinctiveness: a brand's mix share for a theme, as a ratio to the
  cross-brand average for that theme. >=1.5x flags real over-indexing.
- table_stakes: themes where every brand's mix share clears 10% --
  nobody can claim this territory alone.
- platform_register + register_divergence: per-brand theme mix broken
  out by platform, plus a single divergence score (average pairwise
  total-variation distance between a brand's platform columns).
  HANDOFF's own worked example is Genentech: patient-story video on
  Meta, HCP clinical language on LinkedIn, disease-education text on
  Google -- a brand saying different things on different platforms.
"""
import json
from pathlib import Path

import pandas as pd

STRATEGY_FIELD = "strategy_themes"
TOPIC_FIELD = "topic_themes"

DISTINCTIVE_RATIO = 1.5
TABLE_STAKES_MIN_SHARE = 0.10


def _exploded(rows, tier_field):
    df = pd.DataFrame(rows)
    if df.empty or tier_field not in df.columns:
        return pd.DataFrame(columns=["brand", "source", "served_days", tier_field])
    # explode() keeps each source row's original index, so a row with
    # 2+ labels produces duplicate index values -- crosstab/pivot_table
    # choke on that ("cannot reindex on an axis with duplicate labels").
    d = df.explode(tier_field).dropna(subset=[tier_field])
    return d.reset_index(drop=True)


def mix_matrix(rows, tier_field):
    """index=theme id, columns=brand, values=share of that brand's own
    ads carrying the theme. Empty DataFrame if nothing to show.

    The denominator is every one of the brand's ads, including ones
    that carry no theme at all -- crosstab's own normalize="columns"
    is wrong for this: it divides by the sum of the *exploded,
    theme-having* rows only, so a brand's untagged ads silently vanish
    from the denominator (inflating every share) and a multi-label ad
    gets double-counted in it (deflating every share). Both bugs were
    caught live by a test with mixed tagged/untagged rows -- compute
    each brand's true total ad count from the un-exploded data instead.
    """
    df = pd.DataFrame(rows)
    if df.empty or tier_field not in df.columns:
        return pd.DataFrame()
    totals = df.groupby("brand").size()
    d = _exploded(rows, tier_field)
    if d.empty:
        return pd.DataFrame()
    counts = pd.crosstab(d[tier_field], d["brand"])
    return counts.div(totals, axis=1)


def persistence_matrix(rows, tier_field):
    """Same shape as mix_matrix, weighted by served_days instead of ad
    count, and normalized the same corrected way: each brand's
    denominator is the served_days sum across ALL of its ads (rows
    with no served_days floor to a weight of 1), not just the ones
    carrying a theme."""
    df = pd.DataFrame(rows)
    if df.empty or tier_field not in df.columns:
        return pd.DataFrame()
    weight = df["served_days"].fillna(1).clip(lower=1)
    totals = weight.groupby(df["brand"]).sum()
    d = _exploded(rows, tier_field)
    if d.empty:
        return pd.DataFrame()
    w = d.assign(w=d["served_days"].fillna(1).clip(lower=1))
    pivot = w.pivot_table(index=tier_field, columns="brand", values="w", aggfunc="sum")
    return pivot.div(totals, axis=1)


def distinctiveness(matrix, ratio=DISTINCTIVE_RATIO):
    """Same shape as the input matrix; values are each brand's share
    divided by the cross-brand average share for that theme. A value
    >= ratio means that brand over-indexes on that theme relative to
    the competitive set. Does not filter by the ratio -- callers decide
    what to do with it; this just computes the index."""
    if matrix.empty:
        return matrix
    avg = matrix.mean(axis=1)
    return matrix.div(avg, axis=0)


def table_stakes(matrix, min_share=TABLE_STAKES_MIN_SHARE):
    """List of theme ids where every brand's share clears min_share."""
    if matrix.empty:
        return []
    return matrix[matrix.ge(min_share).all(axis=1)].index.tolist()


def platform_register(rows, tier_field):
    """brand -> DataFrame(index=theme id, columns=source/platform,
    values=mix share within that brand, computed per platform)."""
    df = pd.DataFrame(rows)
    out = {}
    if df.empty or tier_field not in df.columns:
        return out
    for brand, g in df.groupby("brand"):
        d = g.explode(tier_field).dropna(subset=[tier_field]).reset_index(drop=True)
        if d.empty:
            continue
        pivot = pd.crosstab(d[tier_field], d["source"], normalize="columns")
        out[brand] = pivot
    return out


def register_divergence(platform_tables):
    """brand -> average pairwise total-variation distance between its
    platform theme-mix columns (0 = identical messaging on every
    platform; 1 = completely disjoint). None if a brand has fewer than
    two platforms to compare."""
    scores = {}
    for brand, table in platform_tables.items():
        cols = table.columns.tolist()
        if len(cols) < 2:
            scores[brand] = None
            continue
        dists = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                dist = (table[cols[i]] - table[cols[j]]).abs().sum() / 2
                dists.append(dist)
        scores[brand] = round(sum(dists) / len(dists), 4)
    return scores


def _df_to_json(df):
    if df.empty:
        return {}
    return json.loads(df.round(6).to_json())


def _tier_bundle(rows, tier_field):
    mix = mix_matrix(rows, tier_field)
    reg = platform_register(rows, tier_field)
    return {
        "mix": _df_to_json(mix),
        "persistence_weighted": _df_to_json(persistence_matrix(rows, tier_field)),
        "distinctiveness": _df_to_json(distinctiveness(mix)),
        "table_stakes": table_stakes(mix),
        "platform_register": {b: _df_to_json(t) for b, t in reg.items()},
        "register_divergence": register_divergence(reg),
    }


def analyze_all(rows: list, codebook: dict = None) -> dict:
    """Returns the same JSON-serializable dict written to
    out/analysis.json. codebook is optional and only used to attach a
    theme-id -> label lookup for convenience."""
    result = {"strategy": _tier_bundle(rows, STRATEGY_FIELD),
              "topic": _tier_bundle(rows, TOPIC_FIELD)}

    if codebook:
        labels = {t["id"]: t["label"] for t in
                 codebook.get("strategy_themes", []) + codebook.get("topic_themes", [])}
        result["labels"] = labels

    out_path = Path("out/analysis.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1))
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="out/ads_coded.json")
    ap.add_argument("--codebook", default="out/codebook.json")
    a = ap.parse_args()

    rows = json.loads(Path(a.in_path).read_text())
    codebook = json.loads(Path(a.codebook).read_text()) if Path(a.codebook).exists() else None
    result = analyze_all(rows, codebook)
    print(f"Wrote out/analysis.json. Strategy table_stakes: "
          f"{result['strategy']['table_stakes']}. Topic table_stakes: "
          f"{result['topic']['table_stakes']}.")


if __name__ == "__main__":
    main()
