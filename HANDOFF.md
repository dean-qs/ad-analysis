# Handoff: build spec for the remaining modules

Five modules are imported by `app/streamlit_app.py` and not yet written.
Signatures below are what the app already calls, so match them.

Read `references/actors.md` first. Every quirk in it cost a live API run
to discover.

---

## 1. `scripts/collect.py`

```python
def collect_all(confirmed: dict, region: str, date_from: str,
                date_to: str, platforms: list, cap: int) -> list
```

`confirmed` is `config/resolved_entities.json` shaped as
`{brand: {platform: [{name, id, seen, auto}, ...]}}`.

Per platform, using the actor IDs in `scripts/apify_client.py`:

- **Meta**: one call per confirmed `page_id`. Pass `activeStatus="all"`,
  or you silently lose most of a twelve-month window. Set
  `fetchDetails=True` so video URLs populate. `minDate` and `maxDate`
  work normally.
- **Google**: build `startUrls` as
  `https://adstransparency.google.com/advertiser/{ADVERTISER_ID}?region={REGION}`
  and call `google_collect` with `skipDetails=False` and `ocr=True`.
  Do not use `google_discover` here; it returns no ad copy.
- **LinkedIn**: pass `keyword` and `countries`. Do **not** pass
  `startDate` or `endDate`, they cause the actor to return zero rows.
  Filter the date window client-side afterwards.

Requirements: itemized cost estimate before running, with confirmation.
Run per-entity calls concurrently, bounded at 6. Never exceed `cap` per
entity. On partial failure, keep what succeeded and report what did not.
Write raw output to `out/ads_raw.json`, then call `normalize.normalize`
and `normalize.dedupe` and write `out/ads_normalized.json`.

---

## 2. `scripts/transcribe.py`

```python
def transcribe_all(rows: list) -> int
```

Only rows where `needs_transcription` is true. Two paths:

- **Image**: download, send to a vision model, extract all on-screen
  text plus a one-sentence description of what is depicted.
- **Video**: download, sample 3 frames evenly, extract audio and run
  Whisper. Combine into one transcript field.

Write results back onto each row as `transcript`, and set
`transcript_source` to `ocr`, `asr`, or `both`. Cache by
`creative_id` on disk so a rerun costs nothing.

This is the expensive stage. It must be resumable, and it must show a
running cost total. Genentech's Meta ads were 11 of 12 video with empty
copy fields, so for some brands this stage produces the entire corpus.

---

## 3. `scripts/codebook.py`

```python
def derive(rows: list) -> dict          # -> {"themes": [...]}
def apply(rows: list, codebook: dict) -> list
```

**Derivation runs on a stratified sample of the pooled corpus across all
brands and all platforms.** Deriving from one brand produces
brand-shaped themes and buries whatever competitors are doing
differently. Stratify by brand and by source so a high-volume advertiser
does not dominate.

Each theme:

```json
{
  "id": "T04",
  "label": "Access and affordability",
  "definition": "Ads whose primary claim concerns cost, insurance "
                "coverage, or patient assistance programs.",
  "inclusion": ["copay support", "coverage eligibility"],
  "exclusion": ["general brand awareness with no cost claim"],
  "examples": ["<verbatim>", "<verbatim>"],
  "example_ids": ["<creative_id>"],
  "brandwatch_boolean": "(copay OR \"out of pocket\" OR ...)"
}
```

The `brandwatch_boolean` field is what makes the codebook portable to
organic data. Follow the conventions in the user's existing
`brandwatch-boolean-creator` skill: prefer NEAR over AND for phrase
proximity, and deliver both a labelled and a clean version.

Application is multi-label; an ad can carry several themes. Use the
OpenAI Batch API rather than a synchronous loop, following the patterns
in the user's `llm-bulk-api` skill. Estimate cost before submitting.

Open question for the user, ask before hard-coding: should themes sit at
the level of **message strategy** (patient empowerment, clinical
authority, access and affordability, innovation narrative) or **content
topic** (specific disease states, trial recruitment, hiring)? Strategy
travels better to Brandwatch. Topic is more concrete in the ad-level
data. A two-tier codebook carrying both is viable and probably right.

---

## 4. `scripts/analyze.py`

Brand by theme matrices under two measures:

- **Mix**: share of a brand's own ads carrying a theme
- **Persistence-weighted**: theme share weighted by `served_days`

Raw ad counts are not comparable across brands with different budgets.
Both measures normalize within brand.

Derived cuts: distinctiveness (brand over-indexes against set average),
table stakes (every brand runs it), and platform register (does a brand
say different things on LinkedIn than on Meta). Genentech was a clean
example: patient-story video on Meta, HCP clinical messaging on
LinkedIn, disease-education text on Google.

---

## 5. `scripts/export.py`

```python
def build_all(out_dir: Path) -> list[Path]
```

Produces `ads.xlsx`, `codebook.json`, `codebook.md`,
`messaging_memo.md`, and `explorer.html`.

`explorer.html` is self-contained with data embedded, no build step and
no network calls, so it can be sent to a client directly. Views: brand
by theme heatmap, creative gallery filterable by brand, platform,
theme, and format, and drill-down to verbatims with live ad links.

Every output states the known limitations verbatim from `SKILL.md`.
A deliverable that implies Meta spend data exists would be wrong.

---

## Also worth doing

`resolve_entities.py` currently auto-matches on company-name substring,
which under-resolves badly. Searching "Pfizer" on Meta returned zero
Pfizer-owned pages across 25 results, because pharma DTC creative names
the product rather than the company. Rework it to seed from the
`names`, `products`, and `domains` lists in
`config/competitive_set.example.yaml`, and match against any of them.

Validation target: a Pfizer resolution run should surface Nurtec and
Paxlovid entities. If it returns zero Meta pages, resolution is still
broken.
