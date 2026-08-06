"""Phase 5: build every client-facing deliverable.

def build_all(out_dir: Path) -> list[Path]

Produces ads.xlsx, codebook.md (codebook.json already exists from
codebook.py -- this just confirms it and includes it in the manifest),
messaging_memo.md, and explorer.html.

Known limitations are read straight out of SKILL.md's "Known
limitations" section and appended verbatim to every deliverable, per
HANDOFF.md -- never re-typed by hand here, so there is exactly one
place that can go stale, and never trusted to an LLM to reproduce
faithfully.

messaging_memo.md's narrative is the one LLM-generated piece here (the
user's call: a fixed template doesn't need CLAUDE.md's prose-style
rules -- no em dashes, lead with the conclusion -- an LLM writing freely
does). It runs once on gpt-4o over analyze.py's actual numbers, same
model tier as codebook.py's derive() for the same reason: open-ended
synthesis, not classification, and it only runs once.

explorer.html embeds every row's text directly (self-contained per
HANDOFF, no network calls). It does NOT embed media thumbnails or
Apify-relayed media URLs -- those carry expiring signed tokens (an
`oe=<unix timestamp>` param on every fbcdn/licdn URL seen in this
project's fixtures) and would silently 403 by the time a client opens
a "shareable" file days or weeks later. It links to platform ad-library
pages instead (constructed from creative_id/advertiser_id, not
Apify-relayed), which are the platforms' own stable public URLs.
"""
import json
import re
from pathlib import Path

import pandas as pd

import analyze
import codebook as cb_module

MEMO_MODEL = "gpt-4o"
MEMO_PRICING = {"input": 2.50, "output": 10.00}   # per 1M tokens
ASSUMED_MEMO_OUTPUT_TOKENS = 1200

SKILL_MD_PATH = Path(__file__).resolve().parent.parent / "SKILL.md"


def _client():
    import openai
    return openai.OpenAI()


def memo_call(system, user):
    resp = _client().chat.completions.create(
        model=MEMO_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content, resp.usage


def read_limitations(skill_md_path=SKILL_MD_PATH):
    """Verbatim bullet text from SKILL.md's 'Known limitations'
    section. Returns [] if the section or file is missing, rather than
    fabricating limitations text -- callers should treat an empty list
    as a signal something upstream broke, not as 'no limitations'."""
    if not Path(skill_md_path).exists():
        return []
    text = Path(skill_md_path).read_text()
    m = re.search(r"## Known limitations.*?\n(.*?)(?:\n##|\Z)", text, re.S)
    if not m:
        return []
    bullets = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif bullets and line:
            bullets[-1] += " " + line   # bullet continuation line
    return bullets


def ad_library_url(row):
    """The platform's own stable public URL for viewing this ad, not
    an Apify-relayed media URL (those expire)."""
    source, cid = row.get("source"), row.get("creative_id")
    if not cid:
        return None
    if source == "meta":
        return f"https://www.facebook.com/ads/library/?id={cid}"
    if source == "google":
        adv = row.get("advertiser_id")
        return (f"https://adstransparency.google.com/advertiser/{adv}/creative/{cid}"
                if adv else None)
    if source == "linkedin":
        return f"https://www.linkedin.com/ad-library/detail/{cid}"
    return None


# --------------------------------------------------------------- xlsx


def build_ads_xlsx(rows, out_path, limitations):
    df = pd.DataFrame(rows)
    if "strategy_themes" in df.columns:
        df["strategy_themes"] = df["strategy_themes"].apply(
            lambda v: ", ".join(v) if isinstance(v, list) else v)
    if "topic_themes" in df.columns:
        df["topic_themes"] = df["topic_themes"].apply(
            lambda v: ", ".join(v) if isinstance(v, list) else v)
    df["ad_library_url"] = [ad_library_url(r) for r in rows]

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="ads", index=False)
        pd.DataFrame({"Known limitations": limitations}).to_excel(
            writer, sheet_name="limitations", index=False)
    return out_path


# ------------------------------------------------------------ codebook.md


def codebook_md(codebook, limitations):
    lines = ["# Theme codebook", "",
            "Strategy themes are the primary tier: the persuasive angle an ad "
            "takes, portable to organic social conversation. Topic themes are "
            "a supplementary, more literal cut of what the ad is concretely "
            "about.", ""]
    for tier_key, title in (("strategy_themes", "Strategy themes"),
                            ("topic_themes", "Topic themes")):
        lines.append(f"## {title}")
        lines.append("")
        for t in codebook.get(tier_key, []):
            lines.append(f"### {t['id']} · {t['label']}")
            lines.append("")
            lines.append(t.get("definition", ""))
            lines.append("")
            if t.get("inclusion"):
                lines.append("**Inclusion:** " + ", ".join(t["inclusion"]))
            if t.get("exclusion"):
                lines.append("**Exclusion:** " + ", ".join(t["exclusion"]))
            if t.get("examples"):
                lines.append("")
                lines.append("**Examples:**")
                for ex in t["examples"]:
                    lines.append(f"> {ex}")
            lines.append("")
            lines.append("**Brandwatch boolean (labelled):**")
            lines.append("```")
            lines.append(t.get("brandwatch_boolean_labelled", ""))
            lines.append("```")
            lines.append("")

    lines.append("## Known limitations")
    lines.append("")
    for lim in limitations:
        lines.append(f"- {lim}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- messaging memo


def estimate_memo(analysis, codebook):
    system, user = _build_memo_prompt(analysis, codebook)
    in_tokens = cb_module.count_tokens(system) + cb_module.count_tokens(user)
    cost = (in_tokens * MEMO_PRICING["input"]
            + ASSUMED_MEMO_OUTPUT_TOKENS * MEMO_PRICING["output"]) / 1_000_000
    return {"input_tokens": in_tokens, "assumed_output_tokens": ASSUMED_MEMO_OUTPUT_TOKENS,
            "cost": round(cost, 4)}


MEMO_SYSTEM_PROMPT = """You are a research analyst writing a comparative \
advertising messaging memo for a client. Style rules, non-negotiable:
- No em dashes and no double hyphens used as punctuation.
- No "X isn't just A, it's B" constructions.
- Plain declaratives. Lead with the conclusion in each section, not the setup.
- Ground every claim in the numbers given to you. Never invent a figure.
- Round any figure you cite to two decimal places (write "1.65x", not "1.647059").
- Do not discuss spend, impressions, reach, or geography for Meta commercial \
ads. That data does not exist, and inventing it would put a false number in \
a client deliverable.

Write these sections, in this order:
1. Headline finding (2-3 sentences)
2. Table stakes: what every brand in the set is saying
3. Distinctiveness: which brand over-indexes on which theme, and what that \
suggests about their positioning
4. Platform register: which brands say different things on different \
platforms, and what the difference is
5. What to watch (2-3 sentences on the most actionable pattern)

Do not write about limitations, caveats, or disclaimers anywhere in your \
response, not even a single sentence in passing. Do not use the words \
"limitation" or "caveat" at all. A verbatim limitations section is appended \
after your text by a separate process; anything you write about limitations \
yourself will duplicate and contradict it."""


def _build_memo_prompt(analysis, codebook):
    labels = analysis.get("labels", {})
    strategy = analysis.get("strategy", {})

    def _label(theme_id):
        return labels.get(theme_id, theme_id)

    lines = ["MIX MATRIX (share of each brand's own ads carrying the theme):"]
    lines.append(json.dumps(strategy.get("mix", {}), indent=1))
    lines.append("\nTABLE STAKES (every brand clears 10% share):")
    lines.append(", ".join(_label(t) for t in strategy.get("table_stakes", [])) or "none")
    lines.append("\nDISTINCTIVENESS (brand's share / cross-brand average; "
                 ">=1.5 over-indexes):")
    lines.append(json.dumps(strategy.get("distinctiveness", {}), indent=1))
    lines.append("\nPLATFORM REGISTER DIVERGENCE (0=same messaging everywhere, "
                 "1=completely different by platform):")
    lines.append(json.dumps(strategy.get("register_divergence", {}), indent=1))
    lines.append("\nPLATFORM REGISTER DETAIL (brand -> theme -> platform share):")
    lines.append(json.dumps(strategy.get("platform_register", {}), indent=1))
    lines.append("\nTHEME LABELS:")
    lines.append(json.dumps(labels, indent=1))
    return MEMO_SYSTEM_PROMPT, "\n".join(lines)


def messaging_memo(analysis, codebook, limitations):
    system, user = _build_memo_prompt(analysis, codebook)
    est = estimate_memo(analysis, codebook)
    print(f"Estimated cost for messaging_memo.md: ~${est['cost']:.4f} "
          f"({MEMO_MODEL}, {est['input_tokens']} input tokens)")
    body, usage = memo_call(system, user)
    real_cost = (usage.prompt_tokens * MEMO_PRICING["input"]
                 + usage.completion_tokens * MEMO_PRICING["output"]) / 1_000_000
    print(f"Actual cost: ~${real_cost:.4f}")

    if re.search(r"limitation|caveat", body, re.I):
        print("  ! WARNING: the model wrote about limitations/caveats itself "
              "despite being told not to -- check messaging_memo.md for "
              "duplicated or contradictory limitations text")

    out = ["# Messaging analysis", "", body.strip(), "", "## Known limitations", ""]
    out += [f"- {lim}" for lim in limitations]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------- explorer


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def explorer_html(rows, analysis, codebook, limitations):
    labels = analysis.get("labels", {})
    strategy_mix = analysis.get("strategy", {}).get("mix", {})

    creatives = []
    for r in rows:
        creatives.append({
            "creative_id": r.get("creative_id"), "brand": r.get("brand"),
            "source": r.get("source"), "format": r.get("format"),
            "headline": r.get("headline"), "body": r.get("body"),
            "transcript": r.get("transcript"),
            "strategy_themes": r.get("strategy_themes", []),
            "topic_themes": r.get("topic_themes", []),
            "served_days": r.get("served_days"),
            "ad_library_url": ad_library_url(r),
        })

    brands = sorted({c["brand"] for c in creatives if c["brand"]})
    sources = sorted({c["source"] for c in creatives if c["source"]})
    formats = sorted({c["format"] for c in creatives if c["format"]})
    # Both tiers -- the gallery's matches() already checks strategy_themes
    # and topic_themes, but the filter dropdown only ever offered strategy
    # ids, so topic-theme filtering was reachable in code but not in the UI.
    theme_ids = sorted({t for c in creatives for t in c["strategy_themes"] + c["topic_themes"]})

    data = {"creatives": creatives, "labels": labels, "mix": strategy_mix,
            "brands": brands, "sources": sources, "formats": formats,
            "theme_ids": theme_ids, "limitations": limitations}
    data_json = json.dumps(data)

    return _EXPLORER_TEMPLATE.replace("__DATA__", data_json)


_EXPLORER_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Ad messaging explorer</title>
<style>
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 24px;
       color: #1a1a1a; background: #fff; }
h1 { font-size: 20px; }
.limitations { background: #fff3cd; border: 1px solid #ffe69c; padding: 12px;
              border-radius: 6px; font-size: 13px; margin-bottom: 20px; }
.controls { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.controls select { padding: 4px; }
table.heat { border-collapse: collapse; margin-bottom: 24px; font-size: 13px; }
table.heat th, table.heat td { border: 1px solid #ddd; padding: 6px 10px;
                               text-align: right; }
table.heat th:first-child, table.heat td:first-child { text-align: left; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
          gap: 12px; }
.card { border: 1px solid #ddd; border-radius: 6px; padding: 10px; cursor: pointer;
       font-size: 13px; }
.card:hover { border-color: #999; }
.card .meta { color: #666; font-size: 11px; margin-bottom: 4px; }
.card .text { max-height: 80px; overflow: hidden; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: none;
        align-items: center; justify-content: center; }
.modal.open { display: flex; }
.modal .box { background: #fff; padding: 20px; border-radius: 8px; max-width: 600px;
             max-height: 80vh; overflow: auto; }
</style></head>
<body>
<h1>Ad messaging explorer</h1>
<div class="limitations" id="limitations"></div>
<h2>Brand x theme (strategy, mix)</h2>
<table class="heat" id="heat"></table>
<h2>Creative gallery</h2>
<div class="controls">
  <select id="f-brand"><option value="">All brands</option></select>
  <select id="f-source"><option value="">All platforms</option></select>
  <select id="f-theme"><option value="">All themes</option></select>
  <select id="f-format"><option value="">All formats</option></select>
</div>
<div class="gallery" id="gallery"></div>
<div class="modal" id="modal"><div class="box" id="modal-box"></div></div>
<script>
const DATA = __DATA__;

function esc(s) { return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

document.getElementById("limitations").innerHTML =
  "<strong>Known limitations:</strong><ul>" +
  DATA.limitations.map(l => "<li>" + esc(l) + "</li>").join("") + "</ul>";

function renderHeat() {
  // DATA.mix is pandas to_json()'s default shape: {brand: {theme: value}},
  // not {theme: {brand: value}} -- caught live, the first version of this
  // rendered brand names as row labels with every cell at 0%.
  const brands = DATA.brands;
  const themeSet = new Set();
  for (const b of brands) {
    if (DATA.mix[b]) Object.keys(DATA.mix[b]).forEach(t => themeSet.add(t));
  }
  const themes = Array.from(themeSet);
  let html = "<tr><th>theme</th>" + brands.map(b => "<th>" + esc(b) + "</th>").join("") + "</tr>";
  for (const t of themes) {
    html += "<tr><td>" + esc(DATA.labels[t] || t) + "</td>";
    for (const b of brands) {
      const v = (DATA.mix[b] && DATA.mix[b][t]) || 0;
      const pct = Math.round(v * 1000) / 10;
      const alpha = Math.min(v * 2, 1);
      html += `<td style="background: rgba(200,60,60,${alpha})">${pct}%</td>`;
    }
    html += "</tr>";
  }
  document.getElementById("heat").innerHTML = html;
}

function themeOptionLabel(id) {
  const label = DATA.labels[id] || id;
  const tier = id.startsWith("T") ? "topic" : "strategy";
  return label + " (" + tier + ")";
}
function populateSelect(id, values) {
  const el = document.getElementById(id);
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = (id === "f-theme" ? themeOptionLabel(v) : v);
    el.appendChild(opt);
  }
}
populateSelect("f-brand", DATA.brands);
populateSelect("f-source", DATA.sources);
populateSelect("f-theme", DATA.theme_ids);
populateSelect("f-format", DATA.formats);

function matches(c) {
  const brand = document.getElementById("f-brand").value;
  const source = document.getElementById("f-source").value;
  const theme = document.getElementById("f-theme").value;
  const format = document.getElementById("f-format").value;
  if (brand && c.brand !== brand) return false;
  if (source && c.source !== source) return false;
  if (format && c.format !== format) return false;
  if (theme && !c.strategy_themes.includes(theme) && !c.topic_themes.includes(theme)) return false;
  return true;
}

function cardText(c) {
  return c.transcript || [c.headline, c.body].filter(Boolean).join(" -- ") || "(no text)";
}

function renderGallery() {
  const g = document.getElementById("gallery");
  g.innerHTML = "";
  DATA.creatives.filter(matches).forEach((c, i) => {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `<div class="meta">${esc(c.brand)} · ${esc(c.source)} · ${esc(c.format||"")}</div>
      <div class="text">${esc(cardText(c)).slice(0,220)}</div>`;
    div.onclick = () => openModal(c);
    g.appendChild(div);
  });
}

function openModal(c) {
  const box = document.getElementById("modal-box");
  const themes = c.strategy_themes.concat(c.topic_themes).map(t => DATA.labels[t]||t).join(", ") || "none";
  box.innerHTML = `<h3>${esc(c.brand)} · ${esc(c.source)}</h3>
    <p><strong>Themes:</strong> ${esc(themes)}</p>
    <p style="white-space:pre-wrap">${esc(cardText(c))}</p>
    <p>${c.ad_library_url ? '<a href="'+esc(c.ad_library_url)+'" target="_blank" rel="noopener">View on platform ad library</a>' : ''}</p>
    <button onclick="document.getElementById('modal').classList.remove('open')">Close</button>`;
  document.getElementById("modal").classList.add("open");
}
document.getElementById("modal").onclick = (e) => {
  if (e.target.id === "modal") e.currentTarget.classList.remove("open");
};

["f-brand", "f-source", "f-theme", "f-format"].forEach(id =>
  document.getElementById(id).addEventListener("change", renderGallery));

renderHeat();
renderGallery();
</script>
</body></html>
"""


# ------------------------------------------------------------- build_all


def build_all(out_dir: Path) -> list:
    out_dir = Path(out_dir)
    rows = json.loads((out_dir / "ads_coded.json").read_text())
    codebook = json.loads((out_dir / "codebook.json").read_text())
    limitations = read_limitations()
    if not limitations:
        print("! WARNING: no limitations text found in SKILL.md -- "
              "deliverables will ship without the required disclosures")

    analysis = analyze.analyze_all(rows, codebook)

    paths = []

    xlsx_path = out_dir / "ads.xlsx"
    build_ads_xlsx(rows, xlsx_path, limitations)
    paths.append(xlsx_path)

    codebook_path = out_dir / "codebook.json"
    paths.append(codebook_path)   # already written by codebook.py

    codebook_md_path = out_dir / "codebook.md"
    codebook_md_path.write_text(codebook_md(codebook, limitations))
    paths.append(codebook_md_path)

    memo_path = out_dir / "messaging_memo.md"
    memo_path.write_text(messaging_memo(analysis, codebook, limitations))
    paths.append(memo_path)

    explorer_path = out_dir / "explorer.html"
    explorer_path.write_text(explorer_html(rows, analysis, codebook, limitations))
    paths.append(explorer_path)

    return paths


def main():
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    out_dir = Path(a.out)
    rows = json.loads((out_dir / "ads_coded.json").read_text())
    codebook = json.loads((out_dir / "codebook.json").read_text())
    analysis = analyze.analyze_all(rows, codebook)
    est = estimate_memo(analysis, codebook)
    print(f"messaging_memo.md estimate: ~${est['cost']:.4f} ({MEMO_MODEL})")
    if input("\nProceed? [y/N] ").strip().lower() != "y":
        print("Aborted, nothing spent.")
        return
    paths = build_all(out_dir)
    for p in paths:
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
