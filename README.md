# Tax Checklist Dashboard

Internal Streamlit app for a small tax & accounting firm to track **expected vs. received** client documents during tax season.

Staff paste ProSeries Client Checklist text → the app extracts the client name and requested documents → File Request Pro submissions are synced on demand → the dashboard highlights what’s still missing.

**Internal tool only** (no client login). Reliability and clarity over fancy features.

---

## Features

- **Paste ingest** — copy text from a ProSeries Client Checklist PDF and paste it into the app
- **ProSeries-aware parsing** — client name near the top; items under `Tax Documents:` and `Additional paperwork:`
- **Editable review** — fix name/items before saving; raw pasted text is always kept
- **SQLite storage** — clients, checklist items, FRP uploaded-files log, settings
- **File Request Pro sync** — on-demand pull of files/submissions; auto-match by name/email/filename keywords; unmatched queue for manual assign
- **Dashboard** — searchable client table, % complete, missing highlight, per-client checklist with checkboxes, CSV missing report

---

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional
streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

`requirements.txt` is intentionally minimal for Streamlit Community Cloud:

```
streamlit
requests
python-dotenv
```

### Typical workflow

1. Open a ProSeries Client Checklist PDF on your computer
2. Select all text → Copy
3. In the app, open **Paste checklist text** → paste → **Extract**
4. Review/edit client name + items → **Save client**
5. (Optional) enter FRP API token on Settings → **Sync from File Request Pro**
6. Track missing docs on the dashboard / Missing report

---

## File Request Pro API token

**Never hardcode the token in source.**

### Option A — Settings page (easiest)

1. Sign in to [File Request Pro](https://filerequestpro.com)
2. **Your Settings → API** → create a token for this dashboard
3. In the app, open **Settings / API token**
4. Paste the token → **Save token** → **Test connection**
5. Use **Sync from File Request Pro** on the dashboard or Unmatched Files page

### Option B — Environment variable

```bash
export FRP_API_TOKEN="your_token_here"
```

The Settings-page value (stored in SQLite) takes precedence when both are set.

---

## Project layout

```
app.py                      # Main dashboard
pages/
  1_Paste_Checklist.py      # Paste text → extract → save
  2_Client_Detail.py        # Checklist, notes, edits
  3_Unmatched_Files.py      # Manual FRP file assignment
  4_Missing_Report.py       # Still-missing list + CSV
  5_Settings.py             # FRP API token
taxdash/
  db.py                     # SQLite schema + CRUD
  checklist_parser.py       # Paste-text parsing (ProSeries sections)
  frp_client.py / sync.py / matching.py
data/                       # SQLite DB (gitignored contents)
```

DB file default: `data/taxdash.db` (override with `DATABASE_PATH`).

---

## Tests

```bash
python tests/test_pdf_parser.py
python tests/test_db_and_matching.py
```

Text parsing tests cover client name + Tax Documents / Additional paperwork item extraction.

---

## Notes

- Parsing is heuristic — always review extracted name/items before saving.
- Raw pasted text is stored on the client for later re-extract / debugging.
- No client-facing login (assume trusted staff network).
