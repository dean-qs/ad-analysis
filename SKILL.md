---
name: ad-transparency-analysis
description: Pull a brand's paid advertising from the Meta Ad Library, Google Ads Transparency Center, and LinkedIn Ad Library, then produce a messaging analysis with a reusable theme codebook. Use whenever someone wants to know what a company is saying in its ads, compare owned messaging across competitors, analyze ad creative themes, or build an owned-versus-earned comparison against social listening data. Trigger on phrases like "what ads is X running", "analyze X's ad messaging", "compare how these brands advertise", "pull the ad library for X", "competitive ad messaging analysis", or any mention of the Meta Ad Library, Google Ads Transparency Center, or LinkedIn Ad Library. Also trigger when someone wants a theme codebook built from advertising that can later be applied to Brandwatch data.
---

# Ad Transparency Messaging Analysis

Produces a comparative messaging analysis of paid advertising across
Meta, Google, and LinkedIn, plus a portable theme codebook that can be
applied to organic conversation later.

## Deliverables

1. `ads.xlsx` — every ad, normalized, with theme codes
2. `codebook.json` and `codebook.md` — the theme taxonomy, with a
   Brandwatch boolean fragment per theme
3. `messaging_memo.md` — the written analysis
4. `explorer.html` — self-contained, shareable, data embedded

The codebook is a first-class deliverable, not an internal artifact.
Brandwatch is a downstream consumer of it, never a prerequisite.

## Running this

The pipeline lives in `scripts/`. A Streamlit operator console wraps it:

```
export APIFY_TOKEN=...
export OPENAI_API_KEY=...        # transcription only
streamlit run app/streamlit_app.py
```

Run it locally, not in a sandboxed container. Ad creative is hosted on
`fbcdn.net` and `tpc.googlesyndication.com`, which are blocked by the
Claude container network allowlist, so transcription cannot complete
in-container. See references/actors.md.

## Phases

Run in order. Each writes to `out/` and can be resumed.

**0. Resolve entities.** `resolve_entities.py --brands ...`
Keyword search is noisy on every platform and unreliable on Meta.
Produces candidates with evidence; a human confirms before any spend.
Never skip this. Never let a later phase search by brand name.

**1. Collect.** `collect.py --config config/competitive_set.yaml`
Runs the three actors against confirmed entity IDs. Prints a cost
estimate and waits for confirmation before spending.

**2. Normalize and dedupe.** `normalize.py`
One schema across sources. Collapses placement variants on Meta's
`collation_id`, keeping the longest-running instance and recording
`variant_count`.

**3. Transcribe.** `transcribe.py`
Only creatives where `needs_transcription` is true and `served_days`
clears the configured threshold. On Genentech's Meta ads this covered
roughly 90% of the corpus, because the copy fields are empty and the
message lives entirely in video.

**4. Derive the codebook.** `codebook.py derive`
Inductive pass over a stratified sample of the **pooled** corpus across
all brands. Deriving from one brand produces brand-shaped themes and
buries whatever competitors are doing differently.

**5. Code and analyze.** `codebook.py apply` then `analyze.py`

## Measurement rules

Ad count is not a comparable measure across brands with different
budgets. Report both:

- **Mix**: share of a brand's own ads carrying a theme
- **Persistence-weighted emphasis**: theme share weighted by
  `served_days`. A creative running 244 days represents more commitment
  than one running three, and count treats them identically.

Derived cuts: distinctiveness (brand over-indexes against set average),
table stakes (all brands run it), whitespace (nobody runs it, but
earned conversation contains it — needs the Brandwatch join).

## Known limitations, state these in any deliverable

- **Meta commercial ads carry no spend, impressions, reach, or
  geography.** Those fields populate only for political, issue, housing,
  employment, and credit ads. A `region` parameter filters Google and
  LinkedIn and does nothing on Meta for most brands.
- **No source exposes targeting**, only delivery. LinkedIn's
  `includedTargetingParameters` is the sole exception. Sub-national
  geography is not available anywhere. Do not promise city-level reads.
- **Unbranded and product-brand advertising is easy to miss.** Pharma
  runs disease-education sites that never name the parent company.
  Seed resolution from products and domains, not company names alone.
- **LinkedIn ad dates cannot be recovered from this actor.** Verified
  live against a real `fetchDetails=True` call: no date field of any
  kind comes back, on any tested row. `first_shown`/`last_shown` are
  always null and `served_days` always floors to 1 for LinkedIn, so
  persistence-weighted numbers reflect LinkedIn ad count, not actual
  flight duration, unlike Meta and Google.
- Co-occurrence between an ad flight and an organic lift is not
  attribution.
