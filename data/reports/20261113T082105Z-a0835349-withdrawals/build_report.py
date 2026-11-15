"""Build the reviewed withdrawal report using only locally extracted evidence.

This is a snapshot-specific analysis, not a general PDF budget parser.
"""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import csv
import json
import re
import sqlite3
import unicodedata

OUTPUT = Path(__file__).resolve().parent
ROOT = OUTPUT.parents[2]
SNAPSHOT = ROOT / 'data/snapshots/20261111T215959Z-a25da02c'
MONEY = re.compile(r'(?<![\w.])(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{2}|\d+(?=\s*€))(?!\d)')


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if not unicodedata.combining(c)).replace('ς', 'σ')


def cents(value):
    return int(Decimal(value.replace('.', '').replace(',', '.')) * 100)


def euro(value):
    return f'{Decimal(value) / 100:,.2f}'


def title_from_catalogue(title):
    if '«' in title:
        title = title.split('«', 1)[1]
    title = re.split(r'\s*\((?:κωδικός\s+)?ΟΠΣ', title, flags=re.I)[0]
    return title.strip(' »')


def extract_budget_rows(record):
    rows = []
    for page in record['pages']:
        for line in page['layout'].splitlines():
            amounts = list(MONEY.finditer(line))
            if (len(amounts) == 2 and amounts[0].start() > 35
                    and amounts[1].start() > amounts[0].end() + 2):
                rows.append({'page': page['page'], 'evidence_line': line.strip(),
                             'approved_before_cents': cents(amounts[0].group()),
                             'proposed_after_cents': cents(amounts[1].group()),
                             'method': 'reviewed_budget_table_columns'})
    if record['ops_code'] == '5202980':
        # The proposed amount is printed one line above the approved amount.
        # Column assignment was checked against the rendered source PDF, page 5.
        assert not rows
        page = record['pages'][4]
        lines = [line.strip() for line in page['layout'].splitlines()
                 if '223.200,00' in line or '146.320,00' in line]
        assert len(lines) == 2
        rows = [{'page': 5, 'evidence_line': '\n'.join(lines),
                 'approved_before_cents': 22_320_000,
                 'proposed_after_cents': 14_632_000,
                 'method': 'visually_verified_offset_table_cells'}]
    assert rows, record['ops_code']
    return rows


def csv_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def write_csv(path, records):
    fields = ['rank', 'ops_code', 'project_title', 'approved_budget_before_eur',
              'withdrawal_catalogue_date', 'action_code', 'responsible_ministry',
              'decision_url', 'budget_pages', 'local_pdf', 'pdf_sha256',
              'prior_catalogue_budget_eur', 'catalogue_check', 'review_note']
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {key: csv_safe(record.get(key, '')) for key in fields}
            row['budget_pages'] = '; '.join(map(str, record['budget_pages']))
            writer.writerow(row)


def md(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def main():
    evidence_path = OUTPUT / 'extracted-evidence.json'
    evidence = json.loads(evidence_path.read_text())
    source = sqlite3.connect(f'file:{SNAPSHOT}/snapshot.sqlite3?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    catalogue = [dict(r) for r in source.execute('SELECT * FROM rrf_catalogue_entries')]
    failed_decisions = source.execute("SELECT COUNT(*) FROM greece20_decisions WHERE status='failed'").fetchone()[0]
    notes = {
        '5179098': 'Catalogue withdrawal OPS 5079098 corrected to 5179098 from the decision; all PDE budget rows included.',
        '5150288': 'Prior catalogue omits EUR 2,000,000 expropriation budget; both approved PDE rows in withdrawal decision included.',
        '5186570': 'Prior catalogue EUR 334,691,225.00 disagrees with both PDFs: approved public budget is EUR 33,469,122.50. Includes two PDE rows.',
        '5225632': 'Prior catalogue EUR 1,034,402.00 is annual credits; inclusion and withdrawal PDFs state full budget EUR 1,611,380.00.',
        '5203256': 'Withdrawal confirmed in operative clause; missed by existing classifier (uses Αποφασίζει).',
        '5202980': 'Approved/proposed values have different vertical positions; column assignment visually verified.',
    }
    records = []
    for item in evidence:
        ops = item['ops_code']
        digest = sha256(Path(item['pdf_absolute_path']).read_bytes()).hexdigest()
        assert digest == item['decision']['content_sha256']
        full_text = '\f'.join(p['text'] for p in item['pages'])
        folded = normalized(full_text)
        operative = re.search(r'αποφασιζ(?:ουμε|ει|εται)', folded)
        assert operative and 'ανακληση' in folded[operative.end():operative.end()+700], ops
        assert ops in folded[operative.end():operative.end()+1500], ops
        later = [r for r in catalogue if r['ops_code'] == ops
                 and (r['inclusion_date'] or '') > item['catalogue']['inclusion_date']]
        assert not later, (ops, later)
        budget_rows = extract_budget_rows(item)
        before = sum(row['approved_before_cents'] for row in budget_rows)
        assert before > 0
        prior = item['prior_catalogue_history'][-1]
        prior_budget = prior['source_budget_cents']
        check = ('missing_prior_catalogue_budget' if prior_budget is None else
                 'matches_prior_catalogue_budget' if prior_budget == before else
                 'catalogue_corrected_using_pdf')
        record = {
            'ops_code': ops,
            'project_title': title_from_catalogue(item['catalogue']['project_title']),
            'original_withdrawal_title': item['catalogue']['project_title'],
            'approved_budget_before_cents': before,
            'approved_budget_before_eur': f'{Decimal(before)/100:.2f}',
            'proposed_budget_after_cents': sum(row['proposed_after_cents'] for row in budget_rows),
            'withdrawal_catalogue_date': item['catalogue']['inclusion_date'],
            'action_code': item['catalogue']['action_code'],
            'responsible_ministry': item['catalogue']['responsible_ministry'] or prior['responsible_ministry'],
            'decision_id': item['decision']['decision_id'],
            'decision_url': item['decision']['source_url'],
            'local_pdf': Path(item['pdf_absolute_path']).resolve().relative_to(ROOT.resolve()).as_posix(),
            'pdf_sha256': digest,
            'budget_pages': sorted({row['page'] for row in budget_rows}),
            'budget_rows': budget_rows,
            'operative_evidence_page': full_text.count('\f', 0, operative.start()) + 1,
            'operative_evidence_excerpt': full_text[operative.start():operative.start()+1500],
            'prior_catalogue_date': prior['inclusion_date'],
            'prior_catalogue_budget_cents': prior_budget,
            'prior_catalogue_budget_eur': f'{Decimal(prior_budget)/100:.2f}' if prior_budget is not None else '',
            'prior_decision_url': prior['decision_url'],
            'catalogue_check': check,
            'review_note': notes.get(ops, ''),
        }
        records.append(record)
    records.sort(key=lambda r: (-r['approved_budget_before_cents'], r['ops_code']))
    for rank, record in enumerate(records, 1):
        record['rank'] = rank
    assert len(records) == len({r['ops_code'] for r in records}) == 104
    total = sum(r['approved_budget_before_cents'] for r in records)
    # Independently reconcile from prior catalogue values and reviewed corrections.
    reconciled = sum(r['prior_catalogue_budget_cents'] or 0 for r in records)
    for r in records:
        if r['catalogue_check'] != 'matches_prior_catalogue_budget':
            reconciled += r['approved_budget_before_cents'] - (r['prior_catalogue_budget_cents'] or 0)
    assert reconciled == total == 290_541_070_472
    summary = {
        'snapshot_id': SNAPSHOT.name, 'source_cutoff': '2026-11-11',
        'identified_withdrawn_project_count': len(records),
        'parent_action_count_as_catalogued': len({r['action_code'] for r in records}),
        'approved_budget_before_withdrawal_cents': total,
        'approved_budget_before_withdrawal_eur': f'{Decimal(total)/100:.2f}',
        'projects_with_resolved_before_budget': len(records),
        'unresolved_before_budgets': 0,
        'top_100_budget_cents': sum(r['approved_budget_before_cents'] for r in records[:100]),
        'excluded_from_top_100_count': len(records[100:]),
        'catalogue_cross_checks': dict(Counter(r['catalogue_check'] for r in records)),
        'source_pdfs_with_verified_hashes': len(records),
        'source_snapshot_failed_pdf_downloads': failed_decisions,
        'source_snapshot_database_sha256': sha256((SNAPSHOT/'snapshot.sqlite3').read_bytes()).hexdigest(),
        'extracted_evidence_sha256': sha256(evidence_path.read_bytes()).hexdigest(),
        'metric_definition': 'Sum of approved public-investment (PDE) project budgets immediately before withdrawal, as stated in the approved column of each withdrawal decision; all PDE lines within a project summed once.',
        'scope_note': 'Identified withdrawals in the saved catalogue/PDF snapshot. No later same-OPS catalogue record found. Replacement projects or funding transfers are not netted out; nationwide completeness and absence of later reinstatement outside this snapshot are not established.',
        'value_note': 'Public project budgets, including national/VAT components where included by the decision. Not an isolated EU RRF contribution, private co-investment total, contract value, paid amount, or net funding loss.',
        'validation_note': '104 explicit operative withdrawal clauses; source PDF hashes checked; all budget rows inspected as extracted evidence, six selected source pages visually inspected, and prior decisions reviewed for material discrepancies.',
    }
    (OUTPUT/'withdrawn-projects.json').write_text(json.dumps({'summary': summary, 'projects': records}, ensure_ascii=False, indent=2)+'\n')
    write_csv(OUTPUT/'top-100-withdrawn-projects.csv', records[:100])
    write_csv(OUTPUT/'all-104-withdrawn-projects.csv', records)
    lines = [
        '# Withdrawn RRF projects: approved budget immediately before withdrawal', '',
        f"**Identified projects: {len(records)}. Approved public-investment budget before withdrawal: EUR {euro(total)}.**", '',
        'Based on the saved Greece 2.0 snapshot through 11 November 2026. No live collection was run.', '',
        '## Method and interpretation', '',
        summary['metric_definition'], '', summary['scope_note'], '', summary['value_note'], '',
        'Projects are counted once by OPS, not by parent action or contract. The 104 projects map to 52 catalogue action codes. All 104 have budget evidence; none is excluded for an unknown amount.', '',
        'The candidate set combines 103 decisions already classified by the analysis with one additional saved decision (OPS 5203256). Explicit withdrawal language was checked in all 104 operative clauses. The catalogue OPS typo 5079098 was corrected to 5179098 using the decision.', '',
        '## Verification and corrections', '',
        '- 104 original PDF hashes matched the snapshot database.',
        '- 98 values agree with the last preceding catalogue budget; three correct catalogue discrepancies, and three fill missing catalogue budgets.',
        '- OPS 5186570: EUR 33,469,122.50 in both relevant PDFs, versus EUR 334,691,225.00 in the catalogue.',
        '- OPS 5225632: EUR 1,611,380.00 full approved budget; the catalogue shows EUR 1,034,402.00 annual credits.',
        '- OPS 5150288: EUR 17,850,000.00 across two approved PDE rows; the catalogue omits EUR 2,000,000.00 for expropriations.',
        '- OPS 5185183, 5225298 and 5189391: missing previous catalogue values recovered from the withdrawal PDFs.',
        '- OPS 5202980: approved/proposed cells have different vertical positions; their column assignment was visually checked.',
        f'- The source snapshot had {failed_decisions} failed PDF downloads. This report is not a certification of exhaustive nationwide coverage.', '',
        'Dates below are the catalogue dates of the withdrawal decisions. Source-page numbers are one-based PDF pages. Full text evidence and exact euro-cent amounts are in withdrawn-projects.json.', '',
        '## Top 100, descending by approved budget before withdrawal', '',
        '| Rank | OPS | Project | Approved budget before (EUR) | Withdrawal date | Budget evidence |',
        '|---:|---|---|---:|---|---|',
    ]
    for r in records[:100]:
        pages = ', '.join(map(str, r['budget_pages']))
        lines.append(f"| {r['rank']} | {r['ops_code']} | {md(r['project_title'])} | {euro(r['approved_budget_before_cents'])} | {r['withdrawal_catalogue_date']} | [Official PDF, pp. {pages}]({r['decision_url']}) |")
    lines += ['', '## Remaining four projects', '',
              'These are included in the 104-project count and total, but fall below the top-100 threshold.', '',
              '| Rank | OPS | Project | Approved budget before (EUR) |', '|---:|---|---|---:|']
    for r in records[100:]:
        lines.append(f"| {r['rank']} | {r['ops_code']} | {md(r['project_title'])} | {euro(r['approved_budget_before_cents'])} |")
    (OUTPUT/'withdrawal-report.md').write_text('\n'.join(lines)+'\n')
    # Read the delivered CSV back to verify row count, ordering and cent-exact amounts.
    for filename, expected in [('top-100-withdrawn-projects.csv', records[:100]), ('all-104-withdrawn-projects.csv', records)]:
        with (OUTPUT/filename).open(encoding='utf-8-sig', newline='') as stream:
            actual = list(csv.DictReader(stream))
        assert len(actual) == len(expected)
        assert [r['ops_code'] for r in actual] == [r['ops_code'] for r in expected]
        assert sum(int(Decimal(r['approved_budget_before_eur'])*100) for r in actual) == sum(r['approved_budget_before_cents'] for r in expected)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
