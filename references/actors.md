# Actor reference

Everything here was verified against live runs on 2026-08-06, not read
from documentation. Apify account is STARTER, which prices at BRONZE.

## Chosen actors

| Source | Actor | ID | Bronze price |
|---|---|---|---|
| Meta | `igolaizola/facebook-ad-library-scraper` | `bo5X18oGenWEV9vVo` | $0.0005/result, $0.0005/detail |
| Google discovery | `solidcode/ads-transparency-scraper` | `iRsL8PTQjmWC1SaPQ` | $0.001/result |
| Google collection | `silva95gustavo/google-ads-scraper` | `N8vqwV9wL9wpIsLDz` | $0.0016/ad, $0.004/video |
| LinkedIn | `igolaizola/linkedin-ad-library-scraper` | — | $0.0005/result |

Two Google actors on purpose: `solidcode` takes a keyword and returns
`advertiserId` cheaply, which is what resolution needs. `silva95gustavo`
takes `startUrls` and returns ad copy, which is what collection needs.

## Why silva95gustavo over the alternatives

Head-to-head on Genentech advertiser `AR04097710039484071937`, 18 ads:

| | silva95gustavo | lexis-solutions | solidcode |
|---|---|---|---|
| Records with ad copy | 18/18 | 3/18 | 0/18 |
| Unique headlines | 19 | — | — |

`lexis-solutions` returns `variants[].textContent` as an empty string on
text ads. It exposes `audienceSelections` and `impressions` fields, but
both were empty for a commercial advertiser.

## Quirks that will bite

**LinkedIn: `startDate` / `endDate` return zero rows.** Passing them
silently yields an empty dataset. Verified: same query returned 0 with
dates, 10 without.

**LinkedIn: `fetchDetails=True` adds no date field at all.** Checked
live on 2026-08-06 against the same query with details on (10/10 rows,
`fixtures/linkedin_detail_genentech.json`, ~$0.005). None of
`firstShown`, `lastShown`, `startedAt`, `createdAt`, or any other date
key appeared on any row. Combined with the startDate/endDate quirk
above, LinkedIn ad dates cannot currently be recovered from this actor
under any tested parameters. Treat LinkedIn results as undated; do not
claim a served-days or date-window figure for this source. `fetchDetails`
is still worth requesting for two other fields it adds: `landingPageUrl`
(the ad's real click-through target -- `detailUrl` is only LinkedIn's
own ad-library detail page, not a landing page) and `detailPayer` (who
actually paid; sometimes an agency, e.g. `WEBER SHANDWICK`, rather than
the brand named in `advertiser`).

**Meta: `activeStatus` defaults to `active`.** Leaving the default drops
most of a twelve-month window. Always set `all`.

**Meta: keyword search is unreliable for resolution.** Searching
"Genentech" returned a Facebook Marketplace apartment listing. Searching
"Pfizer" returned zero Pfizer-owned pages across 25 results. Meta
appears to match ad text rather than page names, and pharma DTC creative
often names the product, not the company. Resolve Meta from product
names and known page IDs.

**Google: `silva95gustavo` repeats identical `variations` entries.**
Dedupe on `(headline, description)` inside each record before counting.

**Meta: `collation_id` is free deduplication.** It is Meta's own grouping
of placement variants of one creative. Four of twelve Genentech ads were
the same creative resized.

## Fields that do not exist

On Meta commercial ads, all of these came back null or empty:
`spend`, `impressions_with_index.impressions_text`, `reach_estimate`,
`targeted_or_reached_countries`, and `categories` was `UNKNOWN`. These
populate only for political, issue, housing, employment, and credit
ads. Treat a null as unavailable, never as zero.

## Network constraints in the Claude container

```
fbcdn.net                  403  x-deny-reason: host_not_allowed
tpc.googlesyndication.com  403  x-deny-reason: host_not_allowed
api.apify.com              reachable
api.openai.com             reachable
```

Creative media cannot be downloaded in-container, so transcription must
run locally. Fallback if it ever must run in-container: Apify actors can
download media into their key-value store, and `api.apify.com` is
allowlisted, so media routed through Apify becomes reachable.
