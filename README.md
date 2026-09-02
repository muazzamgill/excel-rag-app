# Excel → Query & Reports (multi-file edition)

Upload one or more Excel files. Each works on its own, and you can optionally
link files together on shared key columns (e.g. `Applicant_ID`,
`Country_Code`) for reports and chat that span multiple files — a typical
emigration dataset with Applicants / Applications / Countries tables, for
example. Everything runs locally, no API key required, and reports are
downloadable as PDF or Excel.

## How it works

- **Load**: every uploaded file (and every sheet inside it) becomes a
  separate "table" you can browse independently.
- **Link (optional)**: in the sidebar, define relationships between tables
  (`Table A.column = Table B.column`). The app auto-suggests candidates
  where two tables share a column name. Click "Build joined dataset" to
  merge everything connected by your relationships into one unified table.
- **Report**: pick any individual table, or the joined dataset, and get
  automatic stats (missing values, distributions, correlations, top
  category values) with one-click **PDF** and **Excel** downloads.
- **Chat**: ask natural-language questions about whichever table/joined
  dataset you've selected. Rows are converted to sentences, matched against
  your question with TF-IDF, and the best-matching rows are handed to a
  local LLM (via [Ollama](https://ollama.com)) to answer — grounded, with
  the source rows shown alongside the answer.

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Ollama (free, local LLM — powers the Chat tab)

Download from **https://ollama.com**, then pull a small, fast model:

```bash
ollama pull llama3.2
```

> The Preview and Auto Report tabs work even without Ollama. Only Chat
> needs it.

### 3. Run the app

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`.

### Run with Docker instead

Runs the app **and** a local Ollama server as containers — no local
Python or Ollama install needed:

```bash
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2   # one-time, downloads the model
```

Open `http://localhost:8501`. Stop with `docker compose down` (add `-v`
to also delete the downloaded-model volume).

- To run **only the app** container and point it at an Ollama you run
  elsewhere: `docker build -t excel-rag-app . && docker run -p 8501:8501
  -e OLLAMA_HOST=http://host.docker.internal:11434 excel-rag-app`.
- `OLLAMA_HOST` (base URL) and `OLLAMA_MODEL` env vars override the
  defaults (`http://localhost:11434`, `llama3.2`).

## Example: an emigration dataset with 3 linked files

- **Applicants.xlsx** — `Applicant_ID`, Name, Nationality, DOB
- **Applications.xlsx** — `Application_ID`, `Applicant_ID`, `Country_Code`, Status, Submitted_Date
- **Countries.xlsx** — `Country_Code`, Country_Name, Annual_Quota

1. Upload all three files.
2. In "Link files together", add:
   - `Applicants.xlsx.Applicant_ID = Applications.xlsx.Applicant_ID`
   - `Applications.xlsx.Country_Code = Countries.xlsx.Country_Code`
3. Click **Build joined dataset**.
4. Select **🔗 Joined dataset** as the scope.
5. Ask things like *"how many applicants to Canada are still pending?"* or
   *"what's the approval rate by country?"* — or just browse the Auto
   Report for cross-file stats and download it as PDF/Excel.

You can just as easily skip step 2-4 entirely and use any single file on
its own — nothing requires the files to be linked.

## Notes & next steps

- **Join types**: `inner` keeps only matching rows; `left` keeps every row
  from Table A even without a match in Table B. Pick per relationship.
- **Chains of joins**: relationships don't need to all touch the same
  table — e.g. A↔B and B↔C will chain into one dataset (A joined to B
  joined to C), as long as they're connected. Disconnected tables are
  reported and left out of the joined dataset.
- **Retrieval quality**: TF-IDF is keyword-based. For better handling of
  paraphrased questions on large datasets, consider swapping in
  `sentence-transformers` embeddings + a vector store like ChromaDB.
- **Report exports**: currently include the column summary, numeric
  summary, and all charts. If you want the joined-vs-individual report
  data side-by-side in one file, that's a reasonable next addition.
