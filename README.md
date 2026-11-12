# Tax Checklist Dashboard

Internal Streamlit app for a small tax & accounting firm to track **expected vs. received** client documents during tax season.

Staff upload ProSeries Client Checklist PDFs → the app extracts the client name and requested documents → File Request Pro submissions are synced on demand → the dashboard highlights what’s still missing.

**Internal tool only** (no client login). Reliability and clarity over fancy features.

---

## Features

- **PDF ingest** — upload one or many ProSeries Client Checklist PDFs
- **Defensive parsing** — pikepdf normalize → PyMuPDF → pdfplumber → pypdf, then OCR (Tesseract / RapidOCR); ProSeries-aware sections (`Tax Documents:`, `Additional paperwork:`); top-left client name; raw text + engine diagnostics always saved
- **SQLite storage** — clients, checklist items, FRP uploaded-files log, settings
- **File Request Pro sync** — on-demand pull of files/submissions; auto-match by name/email/filename keywords; unmatched queue for manual assign
- **Dashboard** — searchable client table, % complete, missing highlight, per-client checklist with checkboxes, CSV missing report

---

## Quick start

```bash
# 1. Create a virtualenv (recommended)
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) copy env template
cp .env.example .env

# 4. Generate sample PDFs for a dry run
python scripts/make_sample_pdf.py

# 5. (Optional) seed SQLite with those samples
python scripts/seed_demo.py

# 6. Run the app
streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

### Sample PDFs

- Generated demos land in `sample_pdfs/`
- Or drop real ProSeries Client Checklist PDFs into `sample_pdfs/` / upload them in the UI
- Uploaded originals are copied to `data/raw_pdfs/` so you can **re-parse** later

### Streamlit Community Cloud

Text extraction runs **from uploaded bytes in memory** (pypdf → PyPDF2 → pdfminer → PyMuPDF → pdfplumber, then OCR).

Repo root must include:

| File | Purpose |
|------|---------|
| `requirements.txt` | Python deps (`pypdf`, `PyPDF2`, `pdfplumber`, `pymupdf`, …) |
| `packages.txt` | Apt packages for OCR: `tesseract-ocr`, `tesseract-ocr-eng`, `poppler-utils`, `qpdf` |

After deploy, open **Upload PDFs → Environment / library debug** to confirm imports. If a real PDF fails, expand **Cloud extraction debug** and copy the dump (file magic, per-engine errors, previews).

**Password-protected emailed ProSeries PDFs:** unlock once, re-save, upload the unlocked copy.

Local OCR deps (Debian/Ubuntu):

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng poppler-utils qpdf
pip install -r requirements.txt
```

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
# or put it in .env (loaded if you use python-dotenv / your shell)
```

The Settings-page value (stored in SQLite) takes precedence when both are set.

API references:

- [Authentication](https://filerequestpro.com/docs/frp/api-authentication)
- [Files](https://filerequestpro.com/docs/frp/api-file)
- [Submissions](https://filerequestpro.com/docs/frp/api-submissions)

Webhooks can be added later; v1 is **on-demand sync only**.

---

## Project layout

```
app.py                      # Main dashboard
pages/
  1_Upload_PDFs.py          # PDF upload + extraction review
  2_Client_Detail.py        # Checklist, notes, re-parse
  3_Unmatched_Files.py      # Manual FRP file assignment
  4_Missing_Report.py       # Still-missing list + CSV
  5_Settings.py             # FRP API token
taxdash/
  db.py                     # SQLite schema + CRUD
  pdf_parser.py             # pdfplumber / pymupdf extraction
  frp_client.py             # File Request Pro HTTP client
  matching.py               # Name/email/filename matching
  sync.py                   # On-demand sync + auto-match
  ui.py / logging_config.py
data/                       # SQLite DB + raw PDF copies (gitignored contents)
sample_pdfs/                # Demo / reference checklists
scripts/make_sample_pdf.py
tests/
```

### Data model (sample structure)

See comments at the top of `taxdash/db.py`. Summary:

| Table | Purpose |
|-------|---------|
| `clients` | name, email, proseries_id, source filename, notes, **raw_pdf_text** |
| `checklist_items` | description, received flag/date, notes, source_pdf, matched file |
| `uploaded_files` | FRP file/submission ids, filename, submitter, match links, raw JSON |
| `settings` | e.g. `frp_api_token` |

DB file default: `data/taxdash.db` (override with `DATABASE_PATH`).

---

## Typical workflow

1. **Upload** ProSeries checklist PDFs → correct name/items → Save
2. Enter **FRP API token** on Settings → Test connection
3. Click **Sync from File Request Pro**
4. Review **Unmatched files**; assign leftovers manually
5. Open a client to toggle checkboxes / add notes
6. Export **Missing report** (CSV) for follow-ups

---

## Tests

```bash
python tests/test_pdf_parser.py
python tests/test_db_and_matching.py
```

---

## Notes & limits (v1)

- PDF layouts vary; parsing is heuristic. Always review extraction; raw text is kept for later refinement.
- FRP matching is keyword/name based — confirm critical matches.
- No client-facing login, auth, or multi-user permissions (assume trusted staff network).
- Logs: console + `logs/taxdash.log` when writable.
