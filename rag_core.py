"""
Core logic for the Excel Query & Reports app: loading files, detecting and
building joins between independent tables, computing auto-reports, exporting
reports to PDF/Excel, and a lightweight RAG pipeline for chat.

Kept separate from app.py (the Streamlit UI) so it can be unit tested without
spinning up a Streamlit session.
"""

from __future__ import annotations

import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

import pandas as pd
import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Endpoint/model are overridable via env vars so the app can run in Docker
# (where Ollama is another container, e.g. OLLAMA_HOST=http://ollama:11434)
# without code changes. Defaults keep the plain `streamlit run app.py` flow.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
TOP_K = 6


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_excel_bytes(file_bytes: bytes) -> dict[str, pd.DataFrame]:
    """Read every sheet of one Excel file into a dict of DataFrames."""
    sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, engine="openpyxl")
    cleaned = {}
    for name, df in sheets.items():
        cleaned[name] = df.dropna(how="all").dropna(axis=1, how="all")
    return cleaned


def load_all_files(files: list[tuple[str, bytes]]) -> dict[str, pd.DataFrame]:
    """files: list of (filename, file_bytes). Returns a dict keyed by a unique
    table name, e.g. 'applicants.xlsx' or 'data.xlsx :: Sheet2' when a file has
    multiple sheets."""
    tables: dict[str, pd.DataFrame] = {}
    for filename, content in files:
        sheets = load_excel_bytes(content)
        if len(sheets) == 1:
            (only_name, df), = sheets.items()
            tables[filename] = df
        else:
            for sheet_name, df in sheets.items():
                tables[f"{filename} :: {sheet_name}"] = df
    return tables


# ---------------------------------------------------------------------------
# Joins between independent files
# ---------------------------------------------------------------------------
def suggest_joins(tables: dict[str, pd.DataFrame]) -> list[tuple[str, str, str, str]]:
    """Suggest candidate (table_a, col_a, table_b, col_b) pairs where two
    tables share a column name (case-insensitive) — likely join keys."""
    suggestions = []
    keys = list(tables.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            t1, t2 = keys[i], keys[j]
            cols1_lower = {c.lower(): c for c in tables[t1].columns}
            cols2_lower = {c.lower(): c for c in tables[t2].columns}
            for lower_name in set(cols1_lower) & set(cols2_lower):
                suggestions.append((t1, cols1_lower[lower_name], t2, cols2_lower[lower_name]))
    return suggestions


def build_joined_dataset(
    tables: dict[str, pd.DataFrame],
    joins: list[dict],
) -> tuple[pd.DataFrame | None, set[str], set[str]]:
    """joins: list of {left_table, left_key, right_table, right_key, how}.
    Sequentially merges tables that are connected by a defined join, starting
    from whichever join's tables aren't yet included. Returns (merged_df,
    included_table_names, tables_left_out)."""
    remaining = list(joins)
    included: set[str] = set()
    merged: pd.DataFrame | None = None

    progress = True
    while remaining and progress:
        progress = False
        for j in list(remaining):
            lt, lk, rt, rk, how = j["left_table"], j["left_key"], j["right_table"], j["right_key"], j["how"]
            if lt not in tables or rt not in tables:
                remaining.remove(j)
                continue
            if merged is None:
                merged = tables[lt].merge(
                    tables[rt], left_on=lk, right_on=rk, how=how,
                    suffixes=(f" ({lt})", f" ({rt})"),
                )
                included.update([lt, rt])
                remaining.remove(j)
                progress = True
            elif lt in included and rt not in included:
                merged = merged.merge(
                    tables[rt], left_on=lk, right_on=rk, how=how,
                    suffixes=("", f" ({rt})"),
                )
                included.add(rt)
                remaining.remove(j)
                progress = True
            elif rt in included and lt not in included:
                merged = merged.merge(
                    tables[lt], left_on=rk, right_on=lk, how=how,
                    suffixes=("", f" ({lt})"),
                )
                included.add(lt)
                remaining.remove(j)
                progress = True
            elif lt in included and rt in included:
                remaining.remove(j)
                progress = True

    left_out = set(tables.keys()) - included
    return merged, included, left_out


# ---------------------------------------------------------------------------
# RAG pipeline (rows -> text -> TF-IDF -> retrieval -> local LLM)
# ---------------------------------------------------------------------------
def rows_to_documents(df: pd.DataFrame) -> list[str]:
    docs = []
    for _, row in df.iterrows():
        parts = [f"{col}: {row[col]}" for col in df.columns if pd.notna(row[col])]
        docs.append(". ".join(parts))
    return docs


def build_index(docs: list[str]):
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(docs)
    return vectorizer, matrix


def retrieve(query: str, vectorizer, matrix, docs: list[str], top_k: int = TOP_K):
    q_vec = vectorizer.transform([query])
    sims = cosine_similarity(q_vec, matrix).flatten()
    top_idx = sims.argsort()[::-1][:top_k]
    return [(docs[i], sims[i]) for i in top_idx if sims[i] > 0]


def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> str:
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return (
            f"⚠️ Couldn't reach Ollama at {OLLAMA_HOST}. Install it from "
            "https://ollama.com, run `ollama pull llama3.2`, then make sure "
            "`ollama serve` is running (or the Ollama container is up), and "
            "try again."
        )
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Ollama error: {e}"


def answer_question(question: str, df: pd.DataFrame) -> tuple[str, list[str]]:
    docs = rows_to_documents(df)
    if not docs:
        return "No data to search.", []
    vectorizer, matrix = build_index(docs)
    hits = retrieve(question, vectorizer, matrix, docs)
    if not hits:
        return "I couldn't find any rows relevant to that question.", []

    context = "\n".join(f"- {doc}" for doc, _score in hits)
    prompt = (
        "You are a helpful data analyst. Answer the question using ONLY the "
        "rows of data below. If the answer isn't in the data, say so.\n\n"
        f"DATA ROWS:\n{context}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )
    answer = call_ollama(prompt)
    return answer, [d for d, _ in hits]


# ---------------------------------------------------------------------------
# Auto report: stats + matplotlib charts (so the same figures can be
# displayed in-app and exported to PDF)
# ---------------------------------------------------------------------------
def compute_report(df: pd.DataFrame) -> dict:
    report: dict = {
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "missing": int(df.isna().sum().sum()),
    }
    report["summary"] = pd.DataFrame({
        "dtype": df.dtypes.astype(str),
        "missing": df.isna().sum(),
        "unique": df.nunique(),
    })

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    categorical_cols = [c for c in df.columns if c not in numeric_cols and df[c].nunique() <= 30]
    report["numeric_cols"] = numeric_cols
    report["categorical_cols"] = categorical_cols

    if numeric_cols:
        report["describe"] = df[numeric_cols].describe().T

        numeric_figs = []
        for col in numeric_cols:
            fig, ax = plt.subplots(figsize=(5, 3))
            df[col].dropna().plot(kind="hist", bins=20, ax=ax, color="#7f77dd")
            ax.set_title(f"Distribution of {col}")
            fig.tight_layout()
            numeric_figs.append((col, fig))
        report["numeric_figs"] = numeric_figs

        if len(numeric_cols) >= 2:
            corr = df[numeric_cols].corr(numeric_only=True)
            fig, ax = plt.subplots(figsize=(5, 4))
            im = ax.imshow(corr, cmap="viridis")
            ax.set_xticks(range(len(numeric_cols)))
            ax.set_xticklabels(numeric_cols, rotation=45, ha="right", fontsize=8)
            ax.set_yticks(range(len(numeric_cols)))
            ax.set_yticklabels(numeric_cols, fontsize=8)
            for i in range(len(numeric_cols)):
                for j in range(len(numeric_cols)):
                    ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center",
                            color="white", fontsize=7)
            ax.set_title("Correlation heatmap")
            fig.colorbar(im, ax=ax, fraction=0.046)
            fig.tight_layout()
            report["corr_fig"] = fig

    if categorical_cols:
        cat_figs = []
        for col in categorical_cols:
            counts = df[col].value_counts().head(15)
            fig, ax = plt.subplots(figsize=(5, 3))
            counts.plot(kind="bar", ax=ax, color="#1d9e75")
            ax.set_title(f"Top values: {col}")
            plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)
            fig.tight_layout()
            cat_figs.append((col, fig))
        report["cat_figs"] = cat_figs

    return report


def export_report_excel(report: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        report["summary"].to_excel(writer, sheet_name="Column summary")
        if "describe" in report:
            report["describe"].to_excel(writer, sheet_name="Numeric summary")
    return buf.getvalue()


def export_report_pdf(report: dict, title: str) -> bytes:
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        fig, ax = plt.subplots(figsize=(8.5, 2.5))
        ax.axis("off")
        ax.text(0.01, 0.75, title, fontsize=16, weight="bold")
        ax.text(
            0.01, 0.4,
            f"Rows: {report['n_rows']}    Columns: {report['n_cols']}    "
            f"Missing values: {report['missing']}",
            fontsize=11,
        )
        pdf.savefig(fig)
        plt.close(fig)

        summary = report["summary"].reset_index().rename(columns={"index": "Column"})
        fig, ax = plt.subplots(figsize=(8.5, min(0.4 * len(summary) + 1, 10)))
        ax.axis("off")
        tbl = ax.table(cellText=summary.values, colLabels=summary.columns, loc="center")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        ax.set_title("Column summary", fontsize=12)
        pdf.savefig(fig)
        plt.close(fig)

        if "describe" in report:
            d = report["describe"].round(2).reset_index().rename(columns={"index": "Column"})
            fig, ax = plt.subplots(figsize=(8.5, min(0.4 * len(d) + 1, 10)))
            ax.axis("off")
            tbl = ax.table(cellText=d.values, colLabels=d.columns, loc="center")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            ax.set_title("Numeric summary", fontsize=12)
            pdf.savefig(fig)
            plt.close(fig)

        for _name, fig in report.get("numeric_figs", []):
            pdf.savefig(fig)
            plt.close(fig)
        if "corr_fig" in report:
            pdf.savefig(report["corr_fig"])
            plt.close(report["corr_fig"])
        for _name, fig in report.get("cat_figs", []):
            pdf.savefig(fig)
            plt.close(fig)

    return buf.getvalue()
