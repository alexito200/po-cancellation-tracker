import os

import pandas as pd
import streamlit as st

import github_storage as ghs
from ingest import process_uploads, load_master, worker_summary, account_summary

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_CSV = os.path.join(HERE, 'master_transactions.csv')

st.set_page_config(page_title="PO Cancellation Tracker", page_icon="📋", layout="wide")
st.title("📋 PO Cancellation Tracker")
st.caption(
    "Upload Daily Order Detail Maintenance Audit Report PDFs. Each one is parsed, "
    "netted against same-transaction relabels/reallocations, deduplicated by Ibm #, "
    "and added to the running history below."
)

github_ready = ghs.is_configured()
current_sha = None
if github_ready:
    content, current_sha = ghs.pull()
    if content is not None:
        with open(MASTER_CSV, 'wb') as f:
            f.write(content)
    st.caption("🟢 Persistent storage: synced with GitHub — survives restarts and redeploys.")
else:
    st.caption("🟡 Persistent storage: local file only — fine for testing, but add GitHub "
               "secrets before relying on this for real history (see README).")

uploaded = st.file_uploader("Drop report PDF(s) here", type="pdf", accept_multiple_files=True)

if uploaded:
    if st.button(f"Process {len(uploaded)} file(s)", type="primary"):
        file_items = [(f.name, f.getvalue()) for f in uploaded]
        with st.spinner("Parsing and netting..."):
            result = process_uploads(file_items, MASTER_CSV)

        if result['new_count']:
            st.success(f"Added {result['new_count']} new transaction(s). "
                       f"Skipped {result['dup_count']} already-seen duplicate(s).")
        else:
            st.warning(f"No new transactions — all {result['dup_count']} were already in the master file.")

        for pf in result['per_file']:
            st.write(f"- **{pf['filename']}** — {pf['new']} new, {pf['duplicate']} duplicate")

        if github_ready and result['new_count']:
            with open(MASTER_CSV, 'rb') as f:
                updated_bytes = f.read()
            try:
                ghs.push(updated_bytes, current_sha,
                         f"Add {result['new_count']} transaction(s) via app upload")
                st.caption("✅ Saved back to GitHub.")
            except Exception as e:
                st.error(f"Processed the file(s) locally, but saving back to GitHub failed: {e}. "
                         f"Your data is NOT yet durable — download the CSV below as a backup, "
                         f"and try processing again (a second person uploading at the same "
                         f"moment is the most likely cause).")

st.divider()
st.subheader("All-time totals")

existing_rows, _ = load_master(MASTER_CSV)

if not existing_rows:
    st.info("No data yet — upload a report above to get started.")
else:
    all_qty = sum(int(r['net_cancelled_qty']) for r in existing_rows)
    all_val = sum(float(r['net_cancelled_value']) for r in existing_rows)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total transactions", len(existing_rows))
    m2.metric("Net units cancelled", f"{all_qty:,}")
    m3.metric("Net $ cancelled", f"${all_val:,.2f}")

    tab_user, tab_account = st.tabs(["By user", "By account"])

    with tab_user:
        summary = worker_summary(existing_rows)
        st.dataframe(
            summary.rename(columns={
                'user': 'User', 'transactions_touched': 'Transactions touched',
                'cancelling_transactions': 'Real cancellations',
                'net_units_cancelled': 'Net units cancelled',
                'net_value_cancelled': 'Net $ cancelled',
                'likely_automated': 'Flagged as automated',
            }),
            use_container_width=True, hide_index=True,
        )
        st.caption(
            "\"Flagged as automated\" users hit that pattern by touching many POs at the exact "
            "same timestamp -- almost certainly a system process, not a person. Worth confirming "
            "before folding them into human productivity numbers."
        )
        st.bar_chart(summary.set_index('user')['net_value_cancelled'])

    with tab_account:
        acct = account_summary(existing_rows)
        st.dataframe(
            acct.rename(columns={
                'account_code': 'Account code', 'account_name': 'Account name',
                'transactions_touched': 'Transactions touched',
                'cancelling_transactions': 'Real cancellations',
                'net_units_cancelled': 'Net units cancelled',
                'net_value_cancelled': 'Net $ cancelled',
            }),
            use_container_width=True, hide_index=True,
        )
        st.bar_chart(acct.set_index('account_name')['net_value_cancelled'])

    with st.expander("Full transaction history"):
        st.dataframe(pd.DataFrame(existing_rows), use_container_width=True, hide_index=True)

    st.download_button(
        "Download master_transactions.csv",
        data=pd.DataFrame(existing_rows).to_csv(index=False),
        file_name="master_transactions.csv",
        mime="text/csv",
    )
