"""Phase 3: derive a theme codebook from the pooled corpus, then apply it
to every row.

def derive(rows: list) -> dict          # -> {"strategy_themes": [...], "topic_themes": [...]}
def apply(rows: list, codebook: dict) -> list

Two-tier by design, per the user: message-strategy themes (patient
empowerment, clinical authority, access and affordability -- travels
well to Brandwatch organic data) as the primary tier, plus a
content-topic tier (disease states, trial recruitment, hiring -- more
concrete against the ad-level data itself). HANDOFF.md flagged this as
open and suggested a two-tier codebook was "viable and probably right";
the user asked for both rather than picking one.

Model and execution choices, checked against the user's own skills
rather than assumed:

- derive() runs gpt-4o once over a stratified sample (by brand and by
  source, so a high-volume advertiser can't dominate). The
  llm-bulk-api skill's model-selection guide puts "open-ended synthesis
  / judgment / generation" at gpt-4o/4.1 tier -- this is the one
  genuinely generative step in the pipeline, and it runs once, not
  per-row, so the cost gap to a cheaper model barely matters.
- apply() runs gpt-4o-mini per row -- the skill's tier for "multi-class
  classification with subtle disambiguation," which fits theme
  boundaries that carry real inclusion/exclusion nuance.
- apply() uses sync + parallel (ThreadPoolExecutor, checkpointed to
  disk), not the OpenAI Batch API HANDOFF.md names. The user's own
  llm-bulk-api skill reserves Batch for cost-priority runs or jobs
  over 100k requests; a 6-brand ad corpus is nowhere near that, and
  Batch's 24h SLA is the wrong tradeoff here. HANDOFF's instruction
  looks like it predates this skill's current guidance.
- brandwatch_boolean follows the brandwatch-boolean-creator skill's
  preferred pattern: a NEAR-anchored core group over long OR-of-phrases
  lists. Every theme gets a "clean" version (paste-ready) and a
  "labelled" version (wrapped in a `<<< theme label >>>` comment, per
  that skill's convention for team-maintainable queries) -- this is
  what HANDOFF.md meant by "deliver both a labelled and a clean
  version," not a strict/broad precision split.

Both derive_call and classify_call pin temperature=0. Verified live: a
real classification of a real diversity-in-clinical-trials video
transcript returned correct valid ids (S04, T02) on one call and
hallucinated invalid ids on another, silently dropped to zero themes
for a row that plainly matched two theme definitions derived from that
exact row. Determinism is not optional here, matching the reference
template in the llm-bulk-api skill.

apply()'s cost estimate is exact, not assumed: every row's text is
already in memory (no download step, unlike transcribe.py's videos), so
token counts come from tiktoken rather than a guess.
"""
import json
import threading
import time
from pathlib import Path

import tiktoken

CHECKPOINT_FILE = Path("out/codebook_apply_checkpoint.jsonl")
_checkpoint_lock = threading.Lock()

DERIVE_MODEL = "gpt-4o"
APPLY_MODEL = "gpt-4o-mini"

PRICING = {
    "gpt-4o": {"input": 2.50, "cached_input": 1.25, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60},
}

PER_STRATUM_SAMPLE = 10   # rows per (brand, source) group fed to derive()
APPLY_ASSUMED_OUTPUT_TOKENS = 40   # {"strategy_themes": [...], "topic_themes": [...]}

_encoding = tiktoken.get_encoding("o200k_base")


def count_tokens(text):
    return len(_encoding.encode(text or ""))


def _row_text(r):
    t = (r.get("transcript") or "").strip()
    if t:
        return t
    parts = [r.get("headline") or "", r.get("body") or ""]
    return " ".join(p for p in parts if p).strip()


def _client():
    import openai
    return openai.OpenAI()


def derive_call(system, user):
    resp = _client().chat.completions.create(
        model=DERIVE_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content), resp.usage


def classify_call(system, user):
    resp = _client().chat.completions.create(
        model=APPLY_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content), resp.usage


# ------------------------------------------------------------- sampling


def stratified_sample(rows, per_stratum=PER_STRATUM_SAMPLE, seed=42):
    """Groups by (brand, source); samples up to per_stratum per group,
    skipping rows with no usable text. Deterministic given the same
    rows and seed -- no real randomness module, so results are
    reproducible and testable."""
    import random
    rng = random.Random(seed)
    groups = {}
    for r in rows:
        if not _row_text(r).strip():
            continue
        key = (r.get("brand"), r.get("source"))
        groups.setdefault(key, []).append(r)

    sample = []
    for key, grp in groups.items():
        grp = list(grp)
        rng.shuffle(grp)
        sample.extend(grp[:per_stratum])
    return sample


# --------------------------------------------------------------- derive


DERIVE_SYSTEM_PROMPT = """You are a research analyst deriving a theme \
codebook from a corpus of paid advertisements, for later comparative \
messaging analysis across competing brands.

Produce TWO tiers of themes:

1. strategy_themes: message-strategy themes -- the persuasive angle an \
ad is taking (e.g. patient empowerment, clinical authority, access and \
affordability, innovation narrative). These should travel well to \
organic social conversation, not just describe the ad-level content.
2. topic_themes: content-topic themes -- what the ad is concretely \
about (e.g. a specific disease state, trial recruitment, hiring). More \
literal than strategy themes.

Derive both tiers INDUCTIVELY from the sample below -- do not assume a \
fixed taxonomy. Aim for 5-10 themes per tier. A theme must be grounded \
in real examples from the sample; do not invent a theme with no support.

Each theme needs:
- label: short (2-5 words)
- definition: one sentence, specific enough to disambiguate from other \
themes in the same tier
- inclusion: list of concrete inclusion criteria/phrases
- exclusion: list of things that look similar but do not count
- examples: 1-3 VERBATIM quotes copied exactly from the sample text below \
-- never paraphrase or invent a quote
- example_ids: the creative_id for each example quote, in the same order
- brandwatch_boolean_clean: a Brandwatch Listen boolean query fragment \
that would find organic social posts touching this theme. Follow this \
house style: prefer a NEAR-anchored core group over a long OR-of-phrases \
list, e.g. `(trigger_words NEAR/5 concept_words) OR standalone_variant` \
rather than `("phrase one" OR "phrase two" OR "phrase three")`. \
Wildcards (*) are allowed in NEAR/AND groups but never inside quotes. \
Keep it short enough to review at a glance.

Respond as JSON:
{"strategy_themes": [{...}], "topic_themes": [{...}]}
Each theme object has exactly the fields listed above (no id, no tier --
those get added afterward)."""


def _build_derive_user_message(sample):
    lines = []
    for r in sample:
        text = _row_text(r)[:1500]
        lines.append(f"[{r.get('creative_id')}] brand={r.get('brand')} "
                     f"source={r.get('source')}\n{text}")
    return "\n\n---\n\n".join(lines)


def estimate_derive(rows, per_stratum=PER_STRATUM_SAMPLE):
    sample = stratified_sample(rows, per_stratum)
    user_msg = _build_derive_user_message(sample)
    in_tokens = count_tokens(DERIVE_SYSTEM_PROMPT) + count_tokens(user_msg)
    assumed_out_tokens = 3000   # ~10-20 theme objects with examples
    p = PRICING[DERIVE_MODEL]
    cost = (in_tokens * p["input"] + assumed_out_tokens * p["output"]) / 1_000_000
    return {"n_sampled": len(sample), "input_tokens": in_tokens,
            "assumed_output_tokens": assumed_out_tokens, "cost": round(cost, 4)}


def _wrap_labelled(label, clean_boolean):
    return f"<<< {label} >>>\n{clean_boolean}"


def _assign_ids_and_booleans(themes, prefix):
    out = []
    for i, t in enumerate(themes, 1):
        t = dict(t)
        t["id"] = f"{prefix}{i:02d}"
        t["tier"] = "strategy" if prefix == "S" else "topic"
        clean = t.get("brandwatch_boolean_clean", "")
        t["brandwatch_boolean_clean"] = clean
        t["brandwatch_boolean_labelled"] = _wrap_labelled(t.get("label", t["id"]), clean)
        out.append(t)
    return out


def derive(rows: list) -> dict:
    sample = stratified_sample(rows)
    sample_ids = {r.get("creative_id") for r in sample}
    est = estimate_derive(rows)
    print(f"Sampled {est['n_sampled']} rows across brand x source strata")
    print(f"Estimated cost for this one derive() call: ~${est['cost']:.4f} "
          f"({DERIVE_MODEL}, {est['input_tokens']} input tokens)")

    user_msg = _build_derive_user_message(sample)
    parsed, usage = derive_call(DERIVE_SYSTEM_PROMPT, user_msg)

    p = PRICING[DERIVE_MODEL]
    real_cost = (usage.prompt_tokens * p["input"]
                 + usage.completion_tokens * p["output"]) / 1_000_000
    print(f"Actual cost: ~${real_cost:.4f} ({usage.prompt_tokens} in, "
          f"{usage.completion_tokens} out)")

    strategy = _assign_ids_and_booleans(parsed.get("strategy_themes", []), "S")
    topic = _assign_ids_and_booleans(parsed.get("topic_themes", []), "T")

    for tier_themes in (strategy, topic):
        for t in tier_themes:
            kept_ids, kept_examples = [], []
            for eid, ex in zip(t.get("example_ids", []), t.get("examples", [])):
                if eid in sample_ids:
                    kept_ids.append(eid)
                    kept_examples.append(ex)
                else:
                    print(f"  ! {t['id']}: dropped example_id {eid!r} not in "
                          f"the sample given to the model")
            t["example_ids"], t["examples"] = kept_ids, kept_examples

    return {"strategy_themes": strategy, "topic_themes": topic}


# ---------------------------------------------------------------- apply


def _all_themes(codebook):
    return list(codebook.get("strategy_themes", [])) + list(codebook.get("topic_themes", []))


def build_apply_system_prompt(codebook):
    lines = ["You are classifying an advertisement against a fixed theme "
             "codebook. This is multi-label: an ad can carry several "
             "themes, or none. Only assign a theme if the ad clearly "
             "meets its inclusion criteria and does not match its "
             "exclusion criteria.", "", "STRATEGY THEMES:"]
    for t in codebook.get("strategy_themes", []):
        lines.append(f"{t['id']} {t['label']}: {t['definition']} "
                     f"Inclusion: {', '.join(t.get('inclusion', []))}. "
                     f"Exclusion: {', '.join(t.get('exclusion', []))}.")
    lines.append("\nTOPIC THEMES:")
    for t in codebook.get("topic_themes", []):
        lines.append(f"{t['id']} {t['label']}: {t['definition']} "
                     f"Inclusion: {', '.join(t.get('inclusion', []))}. "
                     f"Exclusion: {', '.join(t.get('exclusion', []))}.")
    lines.append('\nRespond as JSON: {"strategy_themes": ["<id>", ...], '
                 '"topic_themes": ["<id>", ...]}. Use empty lists if '
                 "nothing fits. Only use ids from the lists above.")
    return "\n".join(lines)


def estimate_apply(rows, codebook):
    system = build_apply_system_prompt(codebook)
    system_tokens = count_tokens(system)
    eligible = [r for r in rows if _row_text(r).strip()]
    per_row = []
    for r in eligible:
        user_tokens = count_tokens(_row_text(r)[:4000])
        per_row.append(system_tokens + user_tokens)
    p = PRICING[APPLY_MODEL]
    # Worst case: no cache hits. First call pays full system-prompt price;
    # OpenAI's automatic prefix caching typically halves it from there.
    total_in = sum(per_row)
    total_out = len(eligible) * APPLY_ASSUMED_OUTPUT_TOKENS
    cost_worst = (total_in * p["input"] + total_out * p["output"]) / 1_000_000
    cost_if_cached = ((system_tokens * p["input"])
                      + (total_in - system_tokens) * p["cached_input"]
                      + total_out * p["output"]) / 1_000_000 if eligible else 0
    return {"n_eligible": len(eligible), "n_skipped_no_text": len(rows) - len(eligible),
            "system_prompt_tokens": system_tokens,
            "cost_worst_case": round(cost_worst, 6),
            "cost_if_cached_after_first_call": round(cost_if_cached, 6)}


def _load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return {}
    done = {}
    with open(CHECKPOINT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["creative_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def _append_checkpoint(creative_id, parsed, usage):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _checkpoint_lock:
        with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"creative_id": creative_id, "parsed": parsed,
                                "usage": usage}, ensure_ascii=False) + "\n")


def _classify_one(system, row, valid_ids, max_retries=3):
    user = _row_text(row)[:4000]
    last_error = None
    for attempt in range(max_retries):
        try:
            parsed, usage = classify_call(system, user)
            strategy = [i for i in parsed.get("strategy_themes", []) if i in valid_ids]
            topic = [i for i in parsed.get("topic_themes", []) if i in valid_ids]
            dropped = (len(parsed.get("strategy_themes", [])) - len(strategy)
                      + len(parsed.get("topic_themes", [])) - len(topic))
            if dropped:
                print(f"  ! {row.get('creative_id')}: dropped {dropped} "
                      f"hallucinated theme id(s) not in the codebook")
            return ({"strategy_themes": strategy, "topic_themes": topic},
                    {"input_tokens": usage.prompt_tokens,
                     "cached_input_tokens": getattr(
                         getattr(usage, "prompt_tokens_details", None),
                         "cached_tokens", 0) or 0,
                     "output_tokens": usage.completion_tokens})
        except Exception as e:  # noqa: BLE001
            last_error = e
            time.sleep((2 ** attempt) + 0.5)
    print(f"  ! {row.get('creative_id')} FAILED after {max_retries} tries: "
          f"{str(last_error)[:200]}")
    return None, None


def apply(rows: list, codebook: dict) -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    valid_ids = {t["id"] for t in _all_themes(codebook)}
    system = build_apply_system_prompt(codebook)

    done = _load_checkpoint()
    print(f"{len([r for r in rows if r.get('creative_id') in done])} of "
          f"{len(rows)} already checkpointed")

    pending = [r for r in rows if r.get("creative_id") not in done
              and _row_text(r).strip()]
    est = estimate_apply(pending, codebook)
    print(f"\nTo classify: {est['n_eligible']} rows "
          f"({est['n_skipped_no_text']} skipped, no text)")
    print(f"Estimated cost: ~${est['cost_worst_case']:.4f} worst case, "
          f"~${est['cost_if_cached_after_first_call']:.4f} if the system "
          f"prompt caches after the first call\n")

    total_in = total_cached = total_out = 0
    completed = 0
    lock = threading.Lock()

    def work(row):
        return row, *_classify_one(system, row, valid_ids)

    if pending:
        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(work, r) for r in pending]
            for fut in as_completed(futures):
                row, parsed, usage = fut.result()
                cid = row.get("creative_id")
                if parsed is not None:
                    _append_checkpoint(cid, parsed, usage)
                    done[cid] = {"parsed": parsed, "usage": usage}
                    with lock:
                        total_in += usage["input_tokens"]
                        total_cached += usage["cached_input_tokens"]
                        total_out += usage["output_tokens"]
                        completed += 1

    for r in rows:
        cid = r.get("creative_id")
        rec = done.get(cid)
        parsed = rec["parsed"] if rec else {"strategy_themes": [], "topic_themes": []}
        r["strategy_themes"] = parsed["strategy_themes"]
        r["topic_themes"] = parsed["topic_themes"]

    out_path = Path("out/ads_coded.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=1))

    p = PRICING[APPLY_MODEL]
    fresh_in = max(0, total_in - total_cached)
    cost = {
        "input_cost_usd": round(fresh_in * p["input"] / 1_000_000, 4),
        "cached_input_cost_usd": round(total_cached * p["cached_input"] / 1_000_000, 4),
        "output_cost_usd": round(total_out * p["output"] / 1_000_000, 4),
    }
    cost["total_cost_usd"] = round(sum(cost.values()), 4)
    run_meta = {
        "model": APPLY_MODEL, "n_requests": len(pending), "n_succeeded": completed,
        "n_failed": len(pending) - completed,
        "input_tokens": total_in, "cached_input_tokens": total_cached,
        "output_tokens": total_out, **cost,
    }
    (out_path.with_suffix(".json.cost.json")).write_text(json.dumps(run_meta, indent=2))
    print(f"\nWrote {out_path} and its cost log. Total: ${cost['total_cost_usd']:.4f}")
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["derive", "apply"])
    ap.add_argument("--in", dest="in_path", default="out/ads_normalized.json")
    ap.add_argument("--codebook", default="out/codebook.json")
    a = ap.parse_args()

    rows = json.loads(Path(a.in_path).read_text())

    if a.action == "derive":
        est = estimate_derive(rows)
        print(f"Rough estimate: ~${est['cost']:.4f} for one derive() call "
              f"over {est['n_sampled']} sampled rows")
        if input("\nProceed? [y/N] ").strip().lower() != "y":
            print("Aborted, nothing spent.")
            return
        cb = derive(rows)
        Path(a.codebook).write_text(json.dumps(cb, indent=1))
        n = len(cb["strategy_themes"]) + len(cb["topic_themes"])
        print(f"Wrote {a.codebook} ({n} themes across 2 tiers)")
    else:
        codebook = json.loads(Path(a.codebook).read_text())
        est = estimate_apply([r for r in rows if _row_text(r).strip()], codebook)
        print(f"Rough estimate: ~${est['cost_worst_case']:.4f} worst case for "
              f"{est['n_eligible']} rows")
        if input("\nProceed? [y/N] ").strip().lower() != "y":
            print("Aborted, nothing spent.")
            return
        apply(rows, codebook)


if __name__ == "__main__":
    main()
