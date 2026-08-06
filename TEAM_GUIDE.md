# Ad Transparency Messaging Analysis — Team Guide

Pulls a brand's paid advertising straight from the Meta, Google, and LinkedIn ad transparency libraries, codes it into a theme taxonomy, and produces a comparative messaging analysis you can hand to a client. The taxonomy is portable: apply the same theme definitions to Brandwatch organic data later to compare owned messaging against earned conversation.

Repo: https://github.com/dean-qs/ad-analysis

## One-time setup

```bash
git clone https://github.com/dean-qs/ad-analysis.git
cd ad-analysis
pip install -r requirements.txt
```

You'll need two things before running it:
- `APIFY_TOKEN` — an Apify account token. This is what actually pulls ad data, and Apify bills per result.
- `OPENAI_API_KEY` — used for transcribing video/image ads with no text, deriving the theme codebook, coding ads against it, and writing the final memo.

```bash
export APIFY_TOKEN=...
export OPENAI_API_KEY=...
streamlit run app/streamlit_app.py
```

The app is gated behind a `@quadstrat.com` email check on first load. That's a speed bump against accidental use, not real security — don't rely on it to keep the app private if you ever deploy it somewhere shared.

## The workflow

Six tabs, run in order. Every step that spends real money (Apify or OpenAI) shows an itemized cost estimate before you click the button that runs it — nothing spends without you seeing the number first.

1. **Resolve** — type in brand names, pick platforms and region, click "Run resolution." This probes each platform for candidate pages/advertisers and shows you evidence (name, ID, how many times it showed up) before you confirm which candidates are real. Don't skip this: a Meta keyword search for "Genentech" once returned a random apartment listing, and "Pfizer" returned zero real Pfizer pages. Seed resolution from product names too, not just the company name — pharma DTC ads often name the drug, not the manufacturer.
2. **Collect** — pulls ads from the entities you confirmed. Shows a cost estimate broken out by platform before you run it.
3. **Transcribe** — for ads with no usable text (mostly Meta video), extracts on-screen text and a description of what's shown, and transcribes any spoken audio. This can be the majority of the corpus for some brands — one client's Meta ads were 11 of 12 video with empty copy fields.
4. **Codebook** — derives a two-tier theme taxonomy from a sample across all brands: message-strategy themes (the persuasive angle, e.g. "Access and affordability") as the primary tier, plus more literal topic themes as a secondary cut. Review and edit definitions before coding — this is a client deliverable in its own right, not just an internal step.
5. **Analyze** — brand-by-theme heatmaps under two measures (share of ads, and share weighted by how long a creative ran), plus three derived cuts: table stakes (everyone runs it), distinctiveness (a brand over-indexing on a theme), and platform register (does a brand say different things on different platforms).
6. **Export** — builds the client package: `ads.xlsx` (every coded ad), `codebook.md` (the taxonomy with Brandwatch boolean queries per theme), `messaging_memo.md` (a written analysis), and `explorer.html` (a single self-contained file — no server, no build step — with a filterable creative gallery you can email directly to a client).

## What this can't tell you

- **Meta commercial ads carry no spend, impressions, reach, or geography.** Those fields only populate for political/issue/housing/employment/credit ads. Never read a blank as a zero.
- **No source shows targeting, only delivery.** Sub-national geography isn't available anywhere.
- **LinkedIn doesn't expose usable ad dates at all**, even with detail-level fetches turned on. Persistence-weighted numbers for LinkedIn reflect ad count, not real flight duration.
- Full limitations list lives in `SKILL.md` and gets restated verbatim in every export deliverable, so you're never handing a client a number the data can't actually support.

## Questions

Ping Dean. Issues/bugs can also go on the GitHub repo directly.
