"""Operator console for the advertising analysis pipeline.

Run locally. The container network allowlist blocks ad creative CDNs,
so transcription only completes on a machine without that restriction.

    export APIFY_TOKEN=...
    export OPENAI_API_KEY=...
    streamlit run app/streamlit_app.py
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

st.set_page_config(page_title="Owned advertising analysis", layout="wide")

from access_gate import require_quadstrat_email
require_quadstrat_email(st)


def state(k, default):
    if k not in st.session_state:
        st.session_state[k] = default
    return st.session_state[k]


st.title("Owned advertising messaging analysis")

with st.sidebar:
    st.header("Run configuration")
    brands_raw = st.text_area(
        "Brands, one per line",
        "Genentech\nPfizer\nMerck\nAstraZeneca\nNovartis\nRegeneron")
    brands = [b.strip() for b in brands_raw.splitlines() if b.strip()]
    region = st.selectbox("Region", ["US", "GB", "CA", "AU", "DE", "FR"])
    c1, c2 = st.columns(2)
    date_from = c1.date_input("From", pd.Timestamp.today() - pd.Timedelta(days=365))
    date_to = c2.date_input("To", pd.Timestamp.today())
    platforms = st.multiselect("Platforms", ["meta", "google", "linkedin"],
                               default=["meta", "google", "linkedin"])
    cap = st.number_input("Max ads per brand per platform", 50, 2000, 400, 50)
    st.divider()
    st.caption("Credentials")
    st.text(f"APIFY_TOKEN     {'set' if os.environ.get('APIFY_TOKEN') else 'MISSING'}")
    st.text(f"OPENAI_API_KEY  {'set' if os.environ.get('OPENAI_API_KEY') else 'MISSING'}")

tabs = st.tabs(["1. Resolve", "2. Collect", "3. Transcribe",
                "4. Codebook", "5. Analyze", "6. Export"])

# ---------------------------------------------------------------- resolve
with tabs[0]:
    st.subheader("Resolve brands to platform entity IDs")
    st.info("Keyword search is noisy on every platform and unreliable on "
            "Meta. Confirm entities here before spending on collection.")
    est = len(brands) * len(platforms) * 25 * 0.001
    st.caption(f"Probe cost estimate: about ${est:.2f}")

    if st.button("Run resolution", type="primary"):
        from resolve_entities import resolve
        with st.spinner("Probing each brand on each platform"):
            st.session_state.resolved = resolve(brands, region, platforms)
        st.success("Done. Review and prune below.")

    resolved = state("resolved", None)
    if resolved:
        confirmed = state("confirmed", {})
        for brand in brands:
            with st.expander(brand, expanded=True):
                for plat in platforms:
                    cands = (resolved.get(brand, {})
                             .get(plat, {}).get("candidates", []))
                    if not cands:
                        st.warning(f"{plat}: nothing resolved. "
                                   "Add product names or domains as seeds.")
                        continue
                    labels = [f"{c['name']}  ·  {c['id']}  ·  n={c['seen']}"
                              for c in cands]
                    default = [l for l, c in zip(labels, cands) if c["auto"]]
                    picked = st.multiselect(plat, labels, default=default,
                                            key=f"{brand}:{plat}")
                    confirmed.setdefault(brand, {})[plat] = [
                        cands[labels.index(p)] for p in picked]
        if st.button("Save confirmed entities"):
            p = ROOT / "config" / "resolved_entities.json"
            p.write_text(json.dumps(
                {"region": region, "brands": confirmed}, indent=1))
            st.success(f"Wrote {p}")

# ---------------------------------------------------------------- collect
with tabs[1]:
    st.subheader("Collect ads")
    conf_path = ROOT / "config" / "resolved_entities.json"
    if not conf_path.exists():
        st.warning("Confirm entities first.")
    else:
        conf = json.loads(conf_path.read_text())["brands"]
        from collect import estimate_run
        est_rows, cost_lo, cost_hi = estimate_run(conf, platforms, cap)
        n_ent = sum(r[1] for r in est_rows)
        st.metric("Confirmed entities", n_ent)
        cost_label = (f"${cost_lo:.2f}" if abs(cost_lo - cost_hi) < 1e-9
                      else f"${cost_lo:.2f} - ${cost_hi:.2f}")
        st.metric("Worst-case cost", cost_label)
        st.caption("Worst case assumes every entity hits the cap. Google's "
                   "range spans no ads video to all ads video, since that "
                   "rate depends on ad format. Real runs are usually well "
                   "below this.")
        with st.expander("Itemized by platform"):
            for plat, n, units, plo, phi in est_rows:
                line = (f"~${plo:.2f}" if abs(plo - phi) < 1e-9
                        else f"~${plo:.2f} - ${phi:.2f}")
                st.write(f"**{plat}**: {n} entities x {cap} cap = {units} items, {line}")
        if st.button("Run collection", type="primary"):
            from collect import collect_all
            with st.spinner("Running actors"):
                # collect_all writes out/ads_raw.json (raw, source -> brand
                # -> records) and out/ads_normalized.json itself. Do not
                # re-write ads_raw.json here with `rows` -- rows is the
                # normalized return value, and doing so would clobber the
                # raw dump with the wrong shape.
                rows = collect_all(conf, region, str(date_from),
                                   str(date_to), platforms, cap)
            st.success(f"Collected {len(rows)} ads")

    if (OUT / "ads_normalized.json").exists():
        df = pd.DataFrame(json.loads((OUT / "ads_normalized.json").read_text()))
        st.dataframe(df, use_container_width=True, height=340)

# ------------------------------------------------------------- transcribe
with tabs[2]:
    st.subheader("Transcribe creative")
    st.info("Meta ships almost no text for video ads, so for most brands "
            "this stage is the analysis rather than an enhancement.")
    if not os.environ.get("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY not set. Transcription will not run.")
    min_days = st.slider("Only transcribe creatives running at least N days",
                         0, 90, 14)
    st.caption("Doubles as a relevance filter. A creative the brand kept "
               "running is a better read on intended messaging.")
    if (OUT / "ads_normalized.json").exists():
        rows = json.loads((OUT / "ads_normalized.json").read_text())
        todo = [r for r in rows if r.get("needs_transcription")
                and (r.get("served_days") or 0) >= min_days]
        from transcribe import estimate_rough
        est = estimate_rough(todo)
        c1, c2 = st.columns(2)
        c1.metric("Queued for transcription", len(todo) - est["n_cached"])
        c2.metric("Estimated cost", f"${est['total']:.2f}")
        st.caption(f"{est['n_images']} image(s), {est['n_videos']} video(s) "
                   f"(duration assumed {est['assumed_video_seconds']}s each; "
                   f"the actual run measures real durations before spending), "
                   f"{est['n_cached']} already cached at $0.")
        if st.button("Run transcription", type="primary", disabled=not todo):
            from transcribe import transcribe_all
            with st.spinner(f"Transcribing {len(todo)} creatives"):
                done = transcribe_all(todo)
            # todo's dicts are the same objects as rows' -- transcribe_all
            # mutates them in place, so persist the full set back, not
            # just todo, or every other phase's output goes stale.
            (OUT / "ads_normalized.json").write_text(json.dumps(rows, indent=1))
            st.success(f"Transcribed {done}")

# ---------------------------------------------------------------- codebook
with tabs[3]:
    st.subheader("Theme codebook")
    st.info("Derived from the pooled corpus across all brands. Deriving "
            "from one brand produces brand-shaped themes and buries what "
            "competitors are doing differently.")
    cb_path = OUT / "codebook.json"
    if st.button("Derive codebook", type="primary"):
        from codebook import derive
        with st.spinner("Reading a stratified sample of the pooled corpus"):
            cb = derive(json.loads((OUT / "ads_normalized.json").read_text()))
        cb_path.write_text(json.dumps(cb, indent=1))
        st.success(f"Derived {len(cb['strategy_themes'])} strategy theme(s) "
                   f"and {len(cb['topic_themes'])} topic theme(s)")

    if cb_path.exists():
        cb = json.loads(cb_path.read_text())
        st.caption("Edit definitions before coding. This is the deliverable "
                   "that later travels to Brandwatch. Strategy themes are the "
                   "primary tier: they travel to organic social conversation. "
                   "Topic themes are more literal, ad-level content.")
        for tier_key, tier_title in (("strategy_themes", "Strategy themes"),
                                     ("topic_themes", "Topic themes")):
            st.markdown(f"**{tier_title}**")
            for i, t in enumerate(cb.get(tier_key, [])):
                with st.expander(f"{t['id']} · {t['label']}"):
                    t["label"] = st.text_input("Label", t["label"], key=f"{tier_key}l{i}")
                    t["definition"] = st.text_area("Definition", t["definition"],
                                                   key=f"{tier_key}d{i}")
                    t["brandwatch_boolean_clean"] = st.text_area(
                        "Brandwatch boolean (clean, paste-ready)",
                        t.get("brandwatch_boolean_clean", ""), key=f"{tier_key}bc{i}")
                    st.text_area(
                        "Brandwatch boolean (labelled, for team maintenance)",
                        t.get("brandwatch_boolean_labelled", ""),
                        key=f"{tier_key}bl{i}", disabled=True)
                    st.caption("Examples: " + " | ".join(t.get("examples", [])[:3]))
        if st.button("Save codebook"):
            cb_path.write_text(json.dumps(cb, indent=1))
            st.success("Saved")

# ---------------------------------------------------------------- analyze
with tabs[4]:
    st.subheader("Brand by theme")
    coded = OUT / "ads_coded.json"
    if not coded.exists():
        st.warning("Code the corpus first.")
    else:
        from analyze import (distinctiveness, mix_matrix, persistence_matrix,
                             platform_register, register_divergence, table_stakes)
        rows = json.loads(coded.read_text())
        cb_path = OUT / "codebook.json"
        codebook_obj = json.loads(cb_path.read_text()) if cb_path.exists() else None
        labels = {}
        if codebook_obj:
            labels = {t["id"]: t["label"] for t in
                     codebook_obj.get("strategy_themes", []) + codebook_obj.get("topic_themes", [])}

        tier_choice = st.radio("Tier", ["Strategy (primary)", "Topic (supplementary)"],
                               horizontal=True)
        tier_field = ("strategy_themes" if tier_choice.startswith("Strategy")
                     else "topic_themes")
        measure = st.radio("Measure", ["Mix (share of ads)", "Persistence-weighted"],
                           horizontal=True)
        sources = sorted({r.get("source") for r in rows if r.get("source")})
        plat_filter = st.multiselect("Platform", sources, default=sources)
        filtered = [r for r in rows if r.get("source") in plat_filter]

        matrix = (mix_matrix(filtered, tier_field) if measure.startswith("Mix")
                 else persistence_matrix(filtered, tier_field))
        if matrix.empty:
            st.warning("No coded rows carry a theme in this tier/platform selection yet.")
        else:
            display = matrix.rename(index=labels) if labels else matrix
            st.dataframe((display * 100).round(1).style.background_gradient(axis=None),
                         use_container_width=True)
            st.caption("Ad count is not comparable across brands with different "
                       "budgets. Both measures normalize within brand.")

            full_mix = mix_matrix(rows, tier_field)   # derived cuts ignore the platform filter
            stakes = table_stakes(full_mix)
            st.markdown("**Table stakes** (every brand runs it, >=10% share)")
            st.write(", ".join(labels.get(s, s) for s in stakes) or "None")

            dist = distinctiveness(full_mix)
            if not dist.empty:
                st.markdown("**Distinctiveness** (brand's share vs cross-brand "
                           "average: 1.0 is at par, >=1.5 over-indexes)")
                st.dataframe((dist.rename(index=labels) if labels else dist).round(2),
                            use_container_width=True)

        reg = platform_register(rows, tier_field)
        divergence = register_divergence(reg)
        if divergence:
            st.markdown("**Platform register divergence** (0 = same messaging "
                       "everywhere, 1 = completely different by platform)")
            st.write(divergence)

# ---------------------------------------------------------------- export
with tabs[5]:
    st.subheader("Export")
    st.write("- `ads.xlsx` full coded corpus")
    st.write("- `codebook.json` and `codebook.md` with Brandwatch fragments")
    st.write("- `messaging_memo.md`")
    st.write("- `explorer.html` self-contained, shareable")
    if st.button("Build all outputs", type="primary"):
        from export import build_all
        paths = build_all(OUT)
        for p in paths:
            st.success(f"Wrote {p}")
