"""
Parses a Daily Order Detail Maintenance Audit Report (PDF, from the EDI
order system) into a clean, one-row-per-transaction CSV that Power BI can
read directly.

Usage:
    python3 maintenance_report_parser.py <path_to_report.pdf> <output_csv_path>

What it does
------------
1. Extracts the PDF's text layer with `pdftotext -layout` (this report is
   printed in a fixed-width font from an iSeries/AS400 system, so layout
   mode preserves the original column spacing).
2. Parses each Before:/After: pair into a record: PO#, Reg#, Ibm#, Line,
   User, Account, Mode, Before Qty/Price, After Qty/Price.
3. Groups records by Ibm# (every line sharing an Ibm# happened in the same
   maintenance transaction) and nets the quantity change across the whole
   group. This step is the reason a script is doing this instead of a
   Power Query filter: a quantity "decrease" on one line is often offset by
   a sibling "New" line in the same transaction (a relabel/reallocation,
   not a real cancellation) and only looking at the group total tells the
   two apart. See the "Methodology Notes" sheet in
   PO_Cancellation_Analysis.xlsx for a worked example and the size of the
   error this avoids.
4. Categorizes each transaction (Full deletion / Partial reduction /
   Relabel-reallocation / No change / Net addition) and writes one clean
   row per transaction to CSV.

Known limitations / things to confirm before running this on every daily
report going forward:
  - Assumes every line within one Ibm# shares one User, PO# and Account
    (true in every transaction seen so far -- worth an assertion, which
    this script raises if violated, rather than silently mis-attributing).
  - Netting is scoped to the whole Ibm# group, not to a narrower
    Style/Color subgroup. In every example checked, one Ibm# only ever
    touched one base style/color, so this hasn't mattered -- flag if you
    see a report where a single Ibm# spans genuinely unrelated styles.
  - "Value" is computed as qty x Before price. When a transaction changes
    price AND quantity in the same move, the price used is the Before
    price, not a blended one.
"""
import os
import re
import subprocess
import sys
import csv
from collections import defaultdict

ROW_PREFIX = re.compile(
    r'^(?P<po>.+?)\s+(?P<reg>\d+)\s+(?P<ibm>\d+)\s+(?P<line>\d+)\s+'
    r'(?P<trndate>\d{1,2}/\d{1,2}/\d{2})\s+(?P<trntime>\d{1,2}:\d{2}:\d{2})\s+'
    r'(?P<user>\S+)\s+Before:\s*(?P<rest>.*)$'
)
AFTER_PREFIX = re.compile(r'^\s+After:\s*(?P<rest>.*)$')
TAIL = re.compile(
    r'(?P<strdate>\d{1,2}/\d{1,2}/\d{2})\s+(?P<cnxdate>\d{1,2}/\d{1,2}/\d{2})\s+'
    r'(?P<price>\d+\.\d{2})\s+(?P<qty>\d+)\s+'
    r'(?:(?P<mode>DLT-\S+|CHG)\s+)?(?P<pty>\d+)\s*'
    r'(?P<value>[\d,]+\.\d{2})?\s*$'
)
ACCOUNT_RE = re.compile(r'Account:\s*(\S+)\s+(.*?)\s{2,}Program:')


def extract_layout_text(pdf_path: str) -> list[str]:
    # If POPPLER_PATH is set (e.g. on a locked-down machine with no PATH
    # access), call pdftotext by its full path instead of relying on PATH.
    # Unset -> unchanged behavior, so this is a no-op on Community Cloud.
    poppler_dir = os.environ.get('POPPLER_PATH')
    if poppler_dir:
        exe_name = 'pdftotext.exe' if os.name == 'nt' else 'pdftotext'
        exe = os.path.join(poppler_dir, exe_name)
    else:
        exe = 'pdftotext'
    out = subprocess.run([exe, '-layout', pdf_path, '-'],
                          capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def parse_records(lines: list[str]) -> list[dict]:
    records = []
    account_code = account_name = None
    i = 0
    while i < len(lines):
        l = lines[i]
        m_acct = ACCOUNT_RE.search(l)
        if m_acct:
            account_code, account_name = m_acct.group(1), re.sub(r'\s+', ' ', m_acct.group(2)).strip()
            i += 1
            continue

        m = ROW_PREFIX.match(l)
        if m:
            rec = m.groupdict()
            rec['account_code'], rec['account_name'] = account_code, account_name
            rest = rec.pop('rest').strip()

            if rest == 'New':
                rec.update(before_qty=None, before_price=None, before_value=None, mode=None)
            else:
                tm = TAIL.search(rest)
                if tm:
                    rec['before_qty'] = int(tm.group('qty'))
                    rec['before_price'] = float(tm.group('price'))
                    rec['before_value'] = (float(tm.group('value').replace(',', ''))
                                            if tm.group('value') else None)
                    rec['mode'] = tm.group('mode')
                else:
                    rec.update(before_qty=None, before_price=None, before_value=None, mode=None)

            after_line = lines[i + 1] if i + 1 < len(lines) else ''
            am = AFTER_PREFIX.match(after_line)
            a_rest = am.group('rest').strip() if am else ''
            atm = TAIL.search(a_rest) if a_rest else None
            if atm:
                rec['after_qty'] = int(atm.group('qty'))
                rec['after_price'] = float(atm.group('price'))
            else:
                rec['after_qty'] = None
                rec['after_price'] = None

            records.append(rec)
            i += 2
            continue
        i += 1
    return records


def net_and_categorize(records: list[dict]) -> list[dict]:
    for r in records:
        b, a = r['before_qty'] or 0, r['after_qty'] or 0
        r['delta_qty'] = b - a
        price = r['before_price'] if r['before_price'] is not None else (r['after_price'] or 0)
        r['delta_value'] = r['delta_qty'] * price

    by_ibm = defaultdict(list)
    for r in records:
        by_ibm[r['ibm']].append(r)

    rows = []
    for ibm, grp in sorted(by_ibm.items()):
        users = {r['user'] for r in grp}
        pos = {r['po'] for r in grp}
        if len(users) > 1 or len(pos) > 1:
            raise ValueError(f"Ibm# {ibm} spans multiple users/POs -- assumption violated, "
                              f"needs manual review: users={users} pos={pos}")

        net_delta_qty = sum(r['delta_qty'] for r in grp)
        net_delta_val = sum(r['delta_value'] for r in grp)
        modes = sorted({r['mode'] for r in grp if r['mode']})
        has_dlt = any(m.startswith('DLT') for m in modes)

        if net_delta_qty > 0 and has_dlt:
            category = 'Full deletion'
        elif net_delta_qty > 0:
            category = 'Partial reduction'
        elif net_delta_qty == 0 and len(grp) > 1:
            category = 'Relabel / reallocation (no net change)'
        elif net_delta_qty == 0:
            category = 'No change'
        else:
            category = 'Net addition (new units, not a cancellation)'

        rep = grp[0]
        rows.append({
            'po': rep['po'], 'reg': rep['reg'], 'ibm': ibm,
            'account_code': rep['account_code'], 'account_name': rep['account_name'],
            'user': rep['user'], 'trn_date': rep['trndate'], 'trn_time': rep['trntime'],
            'modes': ', '.join(modes), 'lines_in_txn': len(grp),
            'before_qty_total': sum((r['before_qty'] or 0) for r in grp),
            'after_qty_total': sum((r['after_qty'] or 0) for r in grp),
            'net_cancelled_qty': max(0, net_delta_qty),
            'net_cancelled_value': round(max(0, net_delta_val), 2) if net_delta_qty > 0 else 0.0,
            'category': category,
        })
    return rows


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    pdf_path, out_csv = sys.argv[1], sys.argv[2]

    lines = extract_layout_text(pdf_path)
    records = parse_records(lines)
    rows = net_and_categorize(records)

    fieldnames = ['po', 'reg', 'ibm', 'account_code', 'account_name', 'user',
                  'trn_date', 'trn_time', 'modes', 'lines_in_txn',
                  'before_qty_total', 'after_qty_total',
                  'net_cancelled_qty', 'net_cancelled_value', 'category']
    with open(out_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    total_qty = sum(r['net_cancelled_qty'] for r in rows)
    total_val = sum(r['net_cancelled_value'] for r in rows)
    print(f"Wrote {len(rows)} transactions to {out_csv}")
    print(f"Net cancelled: {total_qty:,} units / ${total_val:,.2f}")


if __name__ == '__main__':
    main()
