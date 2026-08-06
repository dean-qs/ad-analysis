# Fixtures

Real payloads captured from live actor runs on 2026-08-06 against
Genentech. Use these for all parser development and tests. Hitting Apify
to check a parser change costs money and returns the same shapes.

| File | Source | Records |
|---|---|---|
| `meta_raw_genentech.json` | `igolaizola/facebook-ad-library-scraper` | 30 |
| `google_raw_genentech.json` | `silva95gustavo/google-ads-scraper` | 18 |
| `google_raw_rejected_actor.json` | `lexis-solutions/google-ads-scraper` | 18 |
| `linkedin_raw_genentech.json` | `igolaizola/linkedin-ad-library-scraper`, with `company` | 10 |
| `linkedin_raw_nodate.json` | same actor, keyword and country only | 10 |
| `linkedin_detail_genentech.json` | same actor, `fetchDetails=True` | 10 |

## What each one is good for

**`meta_raw_genentech.json`** is the hard case and the best test of the
normalizer. It contains:

- Only 13 of 30 records belonging to Genentech entities. The rest are a
  Facebook Marketplace apartment listing, a page called "Sharp Eyes
  After 55", BioSpace, and others. Any parser that assumes a keyword
  search returns only the searched brand is wrong.
- Two distinct Genentech pages, `134743529575` and `146718898672043`.
- 11 of 12 Genentech ads in `VIDEO` format with empty body copy. This is
  the case that makes transcription mandatory rather than optional.
- `collation_id` values with real duplicates. Four ads are the same
  "Hidden GeMS" creative resized for different placements. A correct
  deduper collapses these.
- `spend`, `reach_estimate`, and `impressions_with_index` all null, and
  `targeted_or_reached_countries` empty, because these are commercial
  ads. Assert that your code renders these as unavailable and never
  as zero.

**`google_raw_genentech.json`** has `variations[].headline` and
`variations[].description` populated on all 18 records. It also contains
repeated identical `variations` entries within single records, so it
tests the inner dedupe on `(headline, description)`.

**`google_raw_rejected_actor.json`** is the same advertiser pulled with
the actor we did not choose. Only 3 of 18 records carry any text, and
`audienceSelections` and `impressions` are present but empty. Kept so
the actor choice can be re-checked without paying for it again.

**The two LinkedIn files** are the same query with and without the
`company` parameter. Note that neither carries `firstShown` or
`lastShown`.

**`linkedin_detail_genentech.json`** is the same query with
`fetchDetails=True` added, captured live on 2026-08-06 (10 rows, ~$0.005).
It resolved the open question above: **no date field of any kind comes
back**, on any of the 10 rows, under any parameter combination tried so
far. `fetchDetails=True` does add two useful fields instead:

- `landingPageUrl` -- the advertiser's real click-through target. Only
  present on some rows (e.g. article ads that stay on LinkedIn have
  none). `detailUrl` is LinkedIn's own ad-library detail page, not a
  landing page, and was wrongly used as `landing_url`'s source until
  this fixture caught it.
- `detailPayer` -- who actually paid for the ad. Sometimes an agency
  (`WEBER SHANDWICK`, `Compas, Inc.`) rather than the brand named in
  `advertiser`, which matters for resolution/attribution.

LinkedIn ad dates should be treated as unrecoverable from this actor
until proven otherwise. Do not add date-based claims to a LinkedIn
deliverable.

## What is missing

No fixture yet for a political or issue advertiser. Those are the only
ads where Meta populates spend, impressions, demographics, and
region breakdowns, and the normalizer has never been exercised against
a payload where those fields are non-null. Capture one before trusting
any code path that reads them.
