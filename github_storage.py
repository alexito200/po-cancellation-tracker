"""
Durable storage for master_transactions.csv, using the GitHub repo this
app is deployed from as the backing store.

Why: Streamlit Community Cloud's filesystem is not guaranteed to survive
an app restart or redeploy. The GitHub repo it deploys from IS durable
and fully versioned (every update becomes a commit, so there's a full
history of the master file for free). Rather than stand up a separate
database, this commits the updated CSV back to the repo on every change.

Falls back to doing nothing if the secrets below aren't set, so the exact
same app.py works two ways with no code change:
  - Locally, with no secrets configured: reads/writes the local CSV only.
  - On Community Cloud, with these secrets set: syncs with GitHub, so the
    data survives restarts and redeploys.

Required Streamlit secrets (Community Cloud: Settings -> Secrets; local
dev: a .streamlit/secrets.toml that must NOT be committed to the repo):

    GITHUB_TOKEN = "github_pat_..."   # fine-grained PAT, Contents:
                                        # Read and write, scoped to this repo
    GITHUB_REPO  = "your-username/your-repo-name"
    GITHUB_PATH  = "master_transactions.csv"   # path within the repo

NOTE: this has been validated against GitHub's real Contents API response
shape (fetched a real public file and confirmed the base64 decode round-
trips correctly), but NOT against a real authenticated write -- this
sandbox can't reach api.github.com to test that leg. Worth confirming the
first upload after deploying actually produces a new commit in the repo.
"""
import base64

import requests
import streamlit as st

API_ROOT = "https://api.github.com"


def is_configured() -> bool:
    try:
        return all(k in st.secrets for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_PATH"))
    except Exception:
        return False


def _headers():
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def _url():
    return f"{API_ROOT}/repos/{st.secrets['GITHUB_REPO']}/contents/{st.secrets['GITHUB_PATH']}"


def pull():
    """Returns (content_bytes, sha). (None, None) if the file doesn't exist in the repo yet."""
    resp = requests.get(_url(), headers=_headers(), timeout=15)
    if resp.status_code == 404:
        return None, None
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]), data["sha"]


def push(content_bytes: bytes, sha: str | None, message: str):
    """Commits content_bytes to the repo. Pass the sha from the most recent
    pull() so GitHub can confirm you're updating the version you think
    you are; omit it (None) only when the file has never existed before."""
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    resp = requests.put(_url(), headers=_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()
