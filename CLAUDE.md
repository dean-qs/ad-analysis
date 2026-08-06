# CLAUDE.md

Standing context for this repo. Read `references/actors.md` before
touching any collector.

## What this is

A pipeline that pulls a brand's paid advertising from three ad
transparency libraries, codes it against a derived theme taxonomy, and
produces a comparative messaging analysis. Built for Quadrant
Strategies client work. The theme codebook is a deliverable in its own
right, because it later gets applied to Brandwatch organic data to
compare owned messaging against earned conversation.

## Hard constraints, do not relitigate these

**Never spend money without an explicit confirmation step.** Every
function that calls a paid API prints an itemized cost estimate and
waits. This is a standing requirement, not a nice-to-have. Apify is
billed per result, so an unbounded run is a real financial risk.

**Develop against `fixtures/`, not live APIs.** The fixtures are real
payloads captured from live runs. Use them for every unit test and for
iterating on parsers. Hitting Apify to test a parser change wastes money.

**Ad creative CDNs are blocked in sandboxed containers.** `fbcdn.net`
and `tpc.googlesyndication.com` return `403 host_not_allowed`. This runs
locally. Do not design anything that assumes container network access to
media.

**Meta commercial ads have no spend, impressions, reach, or geography.**
Those fields populate only for political, issue, housing, employment,
and credit ads. Render them as "unavailable", never as zero or blank.
Misreading a null here as a zero would put a false number in a client
deliverable.

## Conventions

- Python 3.11+, standard library where reasonable, pandas for tabular work
- No secrets in code. `APIFY_TOKEN` and `OPENAI_API_KEY` from environment
- Every phase writes to `out/` and is independently resumable
- Prefer small pure functions that take payload dicts, so fixtures work

## Prose style for anything user-facing

Applies to memo templates, UI copy, and docstrings. No em dashes. No
"X isn't just A, it's B" constructions. Plain declaratives. Lead with
the conclusion.

## Where things stand

Built and validated: `apify_client.py`, `resolve_entities.py`,
`normalize.py`, and the Streamlit shell in `app/`.

Stubbed, imported by the app but not written: `collect.py`,
`transcribe.py`, `codebook.py`, `analyze.py`, `export.py`.

See `HANDOFF.md` for the build spec.
