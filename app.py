"""
Excel -> Query & Reports App (multi-file edition)
--------------------------------------------------
Upload one or more Excel files. Each is usable on its own, and you can
optionally define relationships (joins) between files that share a key
column — the app then builds a unified dataset for reporting and chat too.

Run with:  streamlit run app.py
"""

import streamlit as st

import rag_core as rc

st.set_page_config(page_title="Excel Query & Reports", layout="wide")

if "joins" not in st.session_state:
    st.session_state.joins = []
if "joined_df" not in st.session_state:
    st.session_state.joined_df = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = {}


st.title("📊 Excel → Query & Reports")
st.caption(
    "Works on independent Excel files, and on files that are linked together. "
    "Everything runs locally — no API key required."
)

uploaded_files = st.sidebar.file_uploader(
    "Upload Excel file(s)", type=["xlsx", "xls"], accept_multiple_files=True
)

if not uploaded_files:
    st.info("Upload one or more .xlsx / .xls files to get started.")
    st.stop()

files_payload = [(f.name, f.getvalue()) for f in uploaded_files]
tables = rc.load_all_files(files_payload)

st.sidebar.markdown(f"**{len(tables)} table(s) loaded**")
for name, df in tables.items():
    st.sidebar.caption(f"• {name} — {len(df)} rows, {len(df.columns)} cols")

# ---------------------------------------------------------------------------
# Relationships (optional joins between independent files)
# ---------------------------------------------------------------------------
with st.sidebar.expander("🔗 Link files together (optional)", expanded=False):
    st.caption("Only needed if you want reports/chat across multiple files.")

    if len(tables) < 2:
        st.caption("Upload at least 2 files to define a relationship.")
    else:
        suggestions = rc.suggest_joins(tables)
        if suggestions:
            st.caption("Detected matching column names:")
            for t1, c1, t2, c2 in suggestions:
                st.caption(f"  • {t1}.{c1} ↔ {t2}.{c2}")

        table_names = list(tables.keys())
        left_table = st.selectbox("Table A", table_names, key="jt_left")
        left_key = st.selectbox("Table A key column", list(tables[left_table].columns), key="jk_left")
        right_table = st.selectbox(
            "Table B", [t for t in table_names if t != left_table], key="jt_right"
        )
        right_key = st.selectbox("Table B key column", list(tables[right_table].columns), key="jk_right")
        how = st.radio("Join type", ["inner", "left"], horizontal=True, key="jh")

        if st.button("Add relationship"):
            st.session_state.joins.append({
                "left_table": left_table, "left_key": left_key,
                "right_table": right_table, "right_key": right_key, "how": how,
            })
            st.session_state.joined_df = None  # invalidate, rebuild on demand

        if st.session_state.joins:
            st.markdown("**Current relationships:**")
            for i, j in enumerate(st.session_state.joins):
                c1, c2 = st.columns([5, 1])
                c1.caption(
                    f"{j['left_table']}.{j['left_key']} = "
                    f"{j['right_table']}.{j['right_key']} ({j['how']})"
                )
                if c2.button("✕", key=f"rm_{i}"):
                    st.session_state.joins.pop(i)
                    st.session_state.joined_df = None
                    st.rerun()

        if st.session_state.joins and st.button("Build joined dataset", type="primary"):
            merged, included, left_out = rc.build_joined_dataset(tables, st.session_state.joins)
            st.session_state.joined_df = merged
            if left_out:
                st.warning(f"Not connected to the join graph yet: {', '.join(left_out)}")
            else:
                st.success(f"Joined dataset built from: {', '.join(included)}")

# ---------------------------------------------------------------------------
# Scope selector — any individual file, or the joined dataset if built
# ---------------------------------------------------------------------------
scope_options = list(tables.keys())
if st.session_state.joined_df is not None:
    scope_options = ["🔗 Joined dataset"] + scope_options

scope = st.selectbox("Choose what to look at", scope_options)
active_df = (
    st.session_state.joined_df if scope == "🔗 Joined dataset" else tables[scope]
)

tab_preview, tab_report, tab_chat = st.tabs(["🔍 Preview", "📈 Auto Report", "💬 Chat"])

with tab_preview:
    st.dataframe(active_df, use_container_width=True)

with tab_report:
    report = rc.compute_report(active_df)

    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", report["n_rows"])
    c2.metric("Columns", report["n_cols"])
    c3.metric("Missing values", report["missing"])

    st.subheader("Column summary")
    st.dataframe(report["summary"], use_container_width=True)

    if "describe" in report:
        st.subheader("Numeric summary")
        st.dataframe(report["describe"], use_container_width=True)
        st.subheader("Distributions")
        cols = st.columns(2)
        for i, (name, fig) in enumerate(report.get("numeric_figs", [])):
            cols[i % 2].pyplot(fig, use_container_width=True)
        if "corr_fig" in report:
            st.subheader("Correlation heatmap")
            st.pyplot(report["corr_fig"], use_container_width=True)

    if report.get("cat_figs"):
        st.subheader("Category breakdowns")
        cols = st.columns(2)
        for i, (name, fig) in enumerate(report["cat_figs"]):
            cols[i % 2].pyplot(fig, use_container_width=True)

    st.divider()
    st.subheader("⬇️ Download this report")
    pdf_bytes = rc.export_report_pdf(report, f"Report: {scope}")
    xlsx_bytes = rc.export_report_excel(report)
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "Download as PDF", data=pdf_bytes,
        file_name=f"{scope.replace(' ', '_').replace('/', '_')}_report.pdf",
        mime="application/pdf",
    )
    dl2.download_button(
        "Download as Excel", data=xlsx_bytes,
        file_name=f"{scope.replace(' ', '_').replace('/', '_')}_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

with tab_chat:
    st.caption(
        f"Asking questions about: **{scope}**. Answers come from a local LLM "
        f"(Ollama, model `{rc.OLLAMA_MODEL}`) grounded in retrieved rows."
    )
    if scope not in st.session_state.chat_history:
        st.session_state.chat_history[scope] = []

    for role, msg in st.session_state.chat_history[scope]:
        with st.chat_message(role):
            st.markdown(msg)

    question = st.chat_input(f"Ask a question about {scope}...")
    if question:
        st.session_state.chat_history[scope].append(("user", question))
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Searching and asking the local model..."):
                answer, retrieved_docs = rc.answer_question(question, active_df)
            st.markdown(answer)
            if retrieved_docs:
                with st.expander("Rows used to answer this"):
                    for d in retrieved_docs:
                        st.markdown(f"- {d}")
        st.session_state.chat_history[scope].append(("assistant", answer))
