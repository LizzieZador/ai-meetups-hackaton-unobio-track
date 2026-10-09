"""Streamlit UI — one input form, one interactive report. No auth, no database.

Implements the "Interactive Evidence" Required Deliverable: users can drill from
Recommendation -> Claims (grouped by report section) -> Evidence -> Sources,
instead of reading one flat block of markdown.
"""
import streamlit as st

from pipeline import run_pipeline
from quick_summary import build_quick_summary
from raw_data import SOURCE_ORDER, SOURCE_TITLES, extract_analogs
from schema import SECTION_TITLES

VERDICTS = {"Invest", "Conditional", "Do Not Invest"}

st.set_page_config(page_title="Biotech Investment Committee AI", layout="wide")
st.title("Biotech Investment Committee AI")
st.caption("Disease/Indication + Mechanism/target -> Investment Underwriting Report")

with st.form("intake"):
    indication = st.text_input("Disease / Indication", placeholder="e.g. non-small cell lung cancer")
    mechanism = st.text_input("Mechanism / target", placeholder="e.g. KRAS G12C inhibitor")
    extra_context = st.text_area(
        "Additional context (optional)",
        placeholder="modality, development stage, preclinical results, biomarkers, route of administration...",
    )
    submitted = st.form_submit_button("Generate report")

if submitted:
    if not indication or not mechanism:
        st.error("Both Indication and Mechanism/target are required.")
    else:
        with st.spinner("Collecting sources and running the committee..."):
            try:
                claims, cost_summary, sources = run_pipeline(indication, mechanism, extra_context)
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
                st.stop()

        analogs_preview = extract_analogs(sources)
        st.subheader("At a glance")
        st.caption("Plain-language summary, straight from the connected data sources")
        for row in build_quick_summary(claims, sources, analogs_preview):
            has_detail = row.get("detail_text") or row.get("detail_sources")
            if has_detail:
                with st.expander(f"{row['label']}: {row['text']}"):
                    if row.get("detail_text"):
                        st.markdown(row["detail_text"])
                    if row.get("detail_sources"):
                        cites = ", ".join(
                            f"[{s['source']}]({s['url']})" if s.get("url") else s["source"]
                            for s in row["detail_sources"]
                        )
                        st.caption(f"Source(s): {cites}")
            else:
                st.markdown(f"**{row['label']}:** {row['text']}")
        st.divider()

        st.subheader("Raw source data (no LLM — pulled directly from the connected databases)")
        for key in SOURCE_ORDER:
            items = sources.get(key, [])
            with st.expander(f"{SOURCE_TITLES[key]} — {len(items)} record(s)"):
                if not items:
                    st.write("_No data returned by this source for this query._")
                for item in items:
                    link = f"[{item['source']}]({item.get('url')})" if item.get("url") else item["source"]
                    st.markdown(f"**{link}** (accessed {item.get('date_accessed')})")
                    st.caption(item.get("raw_snippet", ""))

        analogs = extract_analogs(sources)
        with st.expander(f"Analogs / competing drugs — {len(analogs)}"):
            if not analogs:
                st.write("_No known competing drugs on this target were found in Open Targets._")
            for drug in analogs:
                st.markdown(f"- **{drug['name']}** — {drug.get('stage') or 'stage unknown'}")

        st.divider()
        st.subheader("Committee verdict (LLM analysis of the data above)")

        by_section: dict[str, list] = {}
        for c in claims:
            by_section.setdefault(c.section, []).append(c)

        recommendation_claims = by_section.get("recommendation", [])
        verdict = next((c.claim for c in recommendation_claims if c.claim in VERDICTS), "No verdict produced")
        st.header(f"Recommendation: {verdict}")
        for c in recommendation_claims:
            st.markdown(f"- **{c.claim}** — {c.reasoning} _(confidence: {c.confidence})_")

        st.subheader("Claims by section")
        for section, title in SECTION_TITLES.items():
            if section == "recommendation":
                continue
            section_claims = by_section.get(section, [])
            with st.expander(f"{title} — {len(section_claims)} claims"):
                if not section_claims:
                    st.write("_No claims generated for this section._")
                for c in section_claims:
                    badge = "EVIDENCE" if c.type == "evidence" else "INFERENCE"
                    st.markdown(f"**[{badge}] {c.claim}**")
                    if c.type == "evidence":
                        link = f"[{c.source}]({c.url})" if c.url else c.source
                        st.markdown(f"Source: {link} (accessed {c.date_accessed})")
                        if c.exact_snippet:
                            st.caption(c.exact_snippet)
                    else:
                        st.markdown(f"Reasoning: {c.reasoning} _(confidence: {c.confidence})_")
                    st.markdown("---")

        sources = sorted(
            {(c.source, c.url, c.date_accessed) for c in claims if c.type == "evidence" and c.source}
        )
        with st.expander(f"Sources Supporting Major Claims — {len(sources)}"):
            if not sources:
                st.write("_No evidence-backed sources were collected for this run._")
            for source, url, date_accessed in sources:
                link = f"[{source}]({url})" if url else source
                st.markdown(f"- {link} (accessed {date_accessed})")

        st.caption(
            f"Pipeline cost for this run: ~${cost_summary['approx_cost_usd']} "
            f"({cost_summary['calls']} calls, {cost_summary['model']}, "
            f"{cost_summary['input_tokens']} in / {cost_summary['output_tokens']} out tokens)"
        )
