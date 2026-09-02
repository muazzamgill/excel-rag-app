# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Not a git repository — there is no commit history to consult, and changes are not version-controlled.

## Project

**Excel → Query & Reports** — a local, free (no API key) Streamlit app for
querying Excel data in plain English and generating downloadable reports.
Primary use case: emigration/immigration datasets spread across multiple
related Excel files (e.g. Applicants, Applications, Countries) that need to
be queried both independently and jointly.

Core capabilities:
- Upload one or more `.xlsx`/`.xls` files (multi-sheet supported)
- Use each file independently, or define relationships (joins) between
  files that share key columns, producing one unified dataset
- Auto-generated report per scope (file or joined dataset): column
  summary, missing values, numeric distributions, correlation heatmap,
  category breakdowns
- Reports downloadable as PDF and Excel
- Chat tab: ask natural-language questions about the selected scope.
  Retrieval-augmented — rows are converted to text, matched via TF-IDF,
  and the top matches are passed to a local LLM (Ollama) to answer

## Tech stack

- **UI**: Streamlit
- **Data**: pandas, openpyxl
- **Retrieval**: scikit-learn `TfidfVectorizer` + cosine similarity (no
  embedding model downloads required — fully offline)
- **Generation**: Ollama (local LLM server, default model `llama3.2`),
  called over HTTP at `localhost:11434`
- **Charts/exports**: matplotlib (`Agg` backend), `matplotlib.backends.backend_pdf.PdfPages`
  for PDF reports, `pandas.ExcelWriter` (openpyxl engine) for Excel reports

No cloud API keys anywhere in this project by design — it must run fully
offline aside from the one-time Ollama model pull.

## File structure

```
app.py          Streamlit UI only — layout, widgets, session state.
                 No data/business logic should live here.
rag_core.py      All business logic: file loading, join detection/building,
                 report computation, PDF/Excel export, TF-IDF retrieval,
                 Ollama calls. Framework-agnostic — must not import streamlit.
requirements.txt Python dependencies.
README.md        End-user setup and usage instructions.
```

**Why the split**: `rag_core.py` has no Streamlit dependency, so its
functions can be tested directly with plain Python/pytest without spinning
up a Streamlit session. Keep it that way — if you add a feature, put the
logic in `rag_core.py` and only the widget/display code in `app.py`.

## Commands

```bash
# Install deps
pip install -r requirements.txt

# Run the app
streamlit run app.py

# Quick manual smoke test of core logic (no pytest suite yet — see TODO)
python3 -c "import rag_core"

# Ollama setup (separate binary, not a pip package)
# https://ollama.com — install, then:
ollama pull llama3.2
ollama serve   # usually auto-starts after install
```

### Docker

```bash
docker compose up -d --build                     # app on :8501 + ollama on :11434
docker compose exec ollama ollama pull llama3.2  # one-time model pull
docker compose down                              # stop (add -v to wipe the model volume)
```

`Dockerfile` builds the app image only; `docker-compose.yml` adds an
`ollama/ollama` service and wires the app to it via `OLLAMA_HOST`.
`compose exec app python smoke_script.py` is the in-container equivalent
of the manual smoke test.

There is currently no test framework wired up. When adding one, prefer
`pytest` with fixtures that build small in-memory DataFrames (see the
manual test pattern used during development: build a DataFrame, write it
to an `io.BytesIO()` via `df.to_excel(...)`, feed the bytes into
`rag_core.load_all_files`).

## Key functions in `rag_core.py`

| Function | Purpose |
|---|---|
| `load_excel_bytes(bytes)` | Read all sheets of one file into `{sheet_name: df}` |
| `load_all_files(list[(filename, bytes)])` | Load multiple files into one `{table_name: df}` dict, disambiguating sheet names when a file has more than one sheet |
| `suggest_joins(tables)` | Find candidate join columns via case-insensitive name matching across tables |
| `build_joined_dataset(tables, joins)` | Sequentially merge tables connected by user-defined joins; returns `(merged_df, included_tables, left_out_tables)` |
| `rows_to_documents(df)` | Turn each row into a `"col: value. col: value..."` sentence for retrieval |
| `build_index(docs)` / `retrieve(query, ...)` | TF-IDF index + cosine similarity search |
| `call_ollama(prompt, model)` | POST to local Ollama server; returns a user-facing error string (never raises) if unreachable |
| `answer_question(question, df)` | Full RAG loop: rows → retrieve → prompt → Ollama answer |
| `compute_report(df)` | Stats + matplotlib figures for the auto report |
| `export_report_pdf(report, title)` / `export_report_excel(report)` | Serialize a report dict to downloadable bytes |

## Architectural subtleties

- **`compute_report` returns a mixed dict** of plain stats (`n_rows`,
  `summary` DataFrame, `describe`) *and* live matplotlib `Figure` objects
  (`numeric_figs`, `corr_fig`, `cat_figs`). `app.py` renders the figures
  with `st.pyplot`, then the same dict is serialized by
  `export_report_pdf` / `export_report_excel`. **`export_report_pdf`
  calls `plt.close()` on every figure it writes** — so it consumes the
  report and must be the last thing to touch those figures. Don't call it
  twice on one report dict, and compute a fresh report if you need to
  re-export.
- **Categorical detection in `compute_report`** is heuristic: a non-numeric
  column counts as categorical only if it has ≤ 30 unique values; charts
  then show the top 15. Widen these if real datasets have higher-cardinality
  categories worth charting.
- **`load_excel_bytes` silently drops all-NA rows and all-NA columns**
  (`dropna(how="all")` on both axes) on every load. Fully blank spacer
  columns/rows in source spreadsheets won't appear downstream.
- **`build_joined_dataset` disambiguates overlapping column names** with
  suffixes: the first merge uses `" (table_name)"` on *both* sides; later
  merges keep the accumulated frame's names bare and suffix only the newly
  joined table's clashes. Report/chat code must tolerate these
  space-and-parens column names.
- **`"🔗 Joined dataset"` is a magic sentinel string** in `app.py` — it's
  the scope-selector option that means "use `st.session_state.joined_df`
  instead of `tables[scope]`", and it's also used to prefix download
  filenames. Changing the literal means changing every comparison against it.
- **`OLLAMA_MODEL` / `OLLAMA_URL` / `TOP_K` are module constants in
  `rag_core.py`** and `app.py` imports `rc.OLLAMA_MODEL` for display.
  `TOP_K` is edit-the-constant only. The Ollama endpoint and model are
  overridable via the `OLLAMA_HOST` (base URL, no `/api/...` suffix) and
  `OLLAMA_MODEL` env vars, defaulting to `http://localhost:11434` /
  `llama3.2` — this is how the Docker setup points the app at the
  `ollama` container without code changes.

## Conventions

- **No API keys, ever.** If a feature needs a paid LLM API, it doesn't
  belong in this project — the whole point is a free/local stack. If a
  future feature genuinely needs better retrieval quality, prefer
  `sentence-transformers` (local embeddings) over a hosted embeddings API.
- **Graceful degradation over hard failures.** `call_ollama` catches
  connection errors and returns an actionable message instead of crashing
  the app — Preview and Auto Report tabs must keep working even if Ollama
  isn't running. Keep this pattern for any new external dependency.
- **Table naming**: a single-sheet file keeps its filename as the table
  key (`applicants.xlsx`); a multi-sheet file uses `filename :: SheetName`.
  Don't change this format without updating the join-relationship UI,
  which stores relationships by table-key string.
- **Joins are user-defined, not inferred automatically.** `suggest_joins`
  only *suggests* candidates by column-name match; it never silently
  joins data. Don't change this — silently joining on a guessed key risks
  producing wrong report numbers without the user realizing it.
- Chat history in the UI is keyed per-scope (`st.session_state.chat_history[scope]`)
  so switching between files/joined dataset doesn't mix up conversations.

## Known limitations / TODO

- **Retrieval is keyword-based (TF-IDF)**, not semantic. Paraphrased
  questions may miss relevant rows. Upgrade path: swap in
  `sentence-transformers` (e.g. `all-MiniLM-L6-v2`) + a local vector
  store (ChromaDB) — this needs internet access on first run to download
  the model, so keep it as an opt-in, not the default.
- **Row-per-document chunking** doesn't scale well past tens of thousands
  of rows (TF-IDF matrix gets large, and Ollama's context window limits
  how many retrieved rows can be included). For big datasets, consider
  batching multiple rows per chunk, or pre-aggregating before retrieval.
- **No test suite yet.** Core logic was validated manually during
  development (see commands above) but has no automated regression tests.
- **PDF/Excel exports** currently include column summary + numeric
  summary + charts, but not category value-count tables as data (only as
  charts). Consider adding a data tab to the Excel export.
- **No persistence** — uploaded files and joins live only in the browser
  session; refreshing loses everything. Consider `window.storage`-style
  persistence or a "save session" export if this becomes a pain point.
- **Real schema not yet incorporated.** This was built against a
  synthetic Applicants/Applications/Countries example. Once real
  emigration file structures are available, revisit `suggest_joins` and
  `compute_report` for domain-specific additions (e.g. approval-rate
  calculations, quota-breach flags, processing-time metrics).
