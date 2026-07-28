# PO Cancellation Tracker

A Streamlit app: upload a Daily Order Detail Maintenance Audit Report PDF,
it parses the report, nets out same-transaction relabels/reallocations so
only real cancellations count, deduplicates by Ibm # against everything
already seen, and appends to a running `master_transactions.csv` --
the file Power BI will eventually read from.

## Files

- `app.py` -- the Streamlit UI (upload button, totals table, chart, download).
- `ingest.py` -- all the actual logic (parsing, netting, dedup, summary).
  No Streamlit dependency, so it can be tested or reused on its own.
- `maintenance_report_parser.py` -- the PDF parsing/netting functions
  `ingest.py` calls.
- `master_transactions.csv` -- seeded with one sample report (80
  transactions). This is the file that grows every time someone uploads
  a new report, and eventually the file Power BI connects to.
- `requirements.txt` / `packages.txt` -- Python and system dependencies.
  `packages.txt` installs `poppler-utils`, which provides the `pdftotext`
  command the parser shells out to -- needed on Streamlit Community Cloud;
  most Linux systems already have it, and Windows users running locally
  will need it on PATH (see below).

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

This opens in your browser at `http://localhost:8501`. `master_transactions.csv`
lives in this same folder and persists across runs on this machine.

Windows note: `pdftotext` isn't installed by default. Install poppler for
Windows (several prebuilt-binary options exist) and make sure the folder
containing `pdftotext.exe` is on your PATH, or the app will error on upload.

## Deploying to Streamlit Community Cloud (with durable storage)

Community Cloud's own filesystem can reset on restart/redeploy, so this
app is wired to commit `master_transactions.csv` back to its own GitHub
repo on every upload instead (see `github_storage.py`). Requires three
secrets, set in the app's Settings -> Secrets on share.streamlit.io (never
commit these into the repo itself):

```
GITHUB_TOKEN = "your fine-grained PAT, Contents: Read and write, scoped to this repo"
GITHUB_REPO  = "your-username/your-repo-name"
GITHUB_PATH  = "master_transactions.csv"
```

Without these secrets set, the app still runs fine -- it just falls back
to the local file only (that's what running it locally does).

## Not yet tested end-to-end

Every piece of `ingest.py` (parsing, netting, dedup, the summary table) has
been run and verified against the real sample report. `github_storage.py`'s
read path is validated against GitHub's real API (fetched a real public
file and confirmed the decode logic); the authenticated write path is not
-- this sandbox can't reach api.github.com either. `app.py` itself has
only been syntax-checked -- the sandbox couldn't install Streamlit to
click through the actual page. Worth running locally first, then checking
that the first Community Cloud upload actually produces a commit in the
repo, and flagging anything that looks off.
