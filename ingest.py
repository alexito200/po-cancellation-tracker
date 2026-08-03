"""
Framework-independent core logic for the PO Cancellation Tracker.
Deliberately has zero dependency on Streamlit so it can be unit-tested
with plain Python, and so the same logic could be reused elsewhere
(a script, a different UI, a scheduled job) without change.

app.py is a thin Streamlit layer that only calls these functions.
"""
import csv
import os
import tempfile
from datetime import datetime

import pandas as pd

from maintenance_report_parser import extract_layout_text, parse_records, net_and_categorize

FIELDNAMES = ['po', 'reg', 'ibm', 'account_code', 'account_name', 'user',
              'trn_date', 'trn_time', 'modes', 'lines_in_txn',
              'before_qty_total', 'after_qty_total',
              'net_cancelled_qty', 'net_cancelled_value', 'category',
              'source_file', 'ingested_at']

KNOWN_AUTOMATED_USERS = {'NIBCONSPRC'}  # single-timestamp-many-POs pattern -- see methodology notes


def load_master(master_csv_path: str):
    """Returns (rows, seen_ibm_set). Empty if the file doesn't exist yet."""
    if not os.path.exists(master_csv_path):
        return [], set()
    with open(master_csv_path, newline='') as f:
        rows = list(csv.DictReader(f))
    return rows, {r['ibm'] for r in rows}


def write_master(master_csv_path: str, rows: list[dict]):
    with open(master_csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)


def process_uploads(file_items: list[tuple[str, bytes]], master_csv_path: str) -> dict:
    """
    file_items: list of (filename, raw_pdf_bytes) -- exactly what
    st.file_uploader gives you per file via (uf.name, uf.getvalue()).

    Writes the updated master CSV as a side effect and returns a summary:
    {new_count, dup_count, per_file: [{filename, new, duplicate}], all_rows}
    """
    existing_rows, seen_ibms = load_master(master_csv_path)
    new_rows = []
    dup_count = 0
    per_file = []

    for fname, content in file_items:
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            lines = extract_layout_text(tmp_path)
            records = parse_records(lines)
            rows = net_and_categorize(records)
        finally:
            os.unlink(tmp_path)

        ingested_at = datetime.now().isoformat(timespec='seconds')
        added = 0
        for r in rows:
            if r['ibm'] in seen_ibms:
                dup_count += 1
                continue
            r['source_file'] = fname
            r['ingested_at'] = ingested_at
            new_rows.append(r)
            seen_ibms.add(r['ibm'])
            added += 1
        per_file.append({'filename': fname, 'new': added, 'duplicate': len(rows) - added})

    all_rows = existing_rows + new_rows
    write_master(master_csv_path, all_rows)
    return {'new_count': len(new_rows), 'dup_count': dup_count,
            'per_file': per_file, 'all_rows': all_rows}


def worker_summary(rows: list[dict]) -> pd.DataFrame:
    """One row per user: transactions touched, transactions that were a
    real cancellation, net units/$ cancelled, and a flag for users who
    match the known-automated pattern."""
    if not rows:
        return pd.DataFrame(columns=['user', 'transactions_touched', 'cancelling_transactions',
                                      'net_units_cancelled', 'net_value_cancelled', 'likely_automated'])
    df = pd.DataFrame(rows)
    df['net_cancelled_qty'] = df['net_cancelled_qty'].astype(int)
    df['net_cancelled_value'] = df['net_cancelled_value'].astype(float)

    summary = df.groupby('user').apply(
        lambda g: pd.Series({
            'transactions_touched': len(g),
            'cancelling_transactions': int((g['net_cancelled_qty'] > 0).sum()),
            'net_units_cancelled': int(g['net_cancelled_qty'].sum()),
            'net_value_cancelled': float(g['net_cancelled_value'].sum()),
        }), include_groups=False
    ).reset_index()
    summary['likely_automated'] = summary['user'].isin(KNOWN_AUTOMATED_USERS)
    return summary.sort_values('net_value_cancelled', ascending=False).reset_index(drop=True)


def account_summary(rows: list[dict]) -> pd.DataFrame:
    """Same idea as worker_summary, grouped by account instead of user --
    one row per account_code, with the account_name carried along for
    display (a given account_code always maps to exactly one name, so
    grouping on both together is safe and avoids a separate lookup)."""
    if not rows:
        return pd.DataFrame(columns=['account_code', 'account_name', 'transactions_touched',
                                      'cancelling_transactions', 'net_units_cancelled', 'net_value_cancelled'])
    df = pd.DataFrame(rows)
    df['net_cancelled_qty'] = df['net_cancelled_qty'].astype(int)
    df['net_cancelled_value'] = df['net_cancelled_value'].astype(float)

    summary = df.groupby(['account_code', 'account_name']).apply(
        lambda g: pd.Series({
            'transactions_touched': len(g),
            'cancelling_transactions': int((g['net_cancelled_qty'] > 0).sum()),
            'net_units_cancelled': int(g['net_cancelled_qty'].sum()),
            'net_value_cancelled': float(g['net_cancelled_value'].sum()),
        }), include_groups=False
    ).reset_index()
    return summary.sort_values('net_value_cancelled', ascending=False).reset_index(drop=True)
