"""Offline evidence extraction for the 2026-11-11 withdrawal snapshot."""
from pathlib import Path
from hashlib import sha256
import json
import re
import sqlite3
import unicodedata

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / 'data/snapshots/20261111T215959Z-a25da02c'
ANALYSIS = ROOT / 'data/analyses/20261113T082105Z-a0835349'
OUTPUT = Path(__file__).resolve().parent


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if not unicodedata.combining(c)).replace('ς', 'σ')


def main():
    source = sqlite3.connect(f'file:{SNAPSHOT}/snapshot.sqlite3?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    analysis = sqlite3.connect(f'file:{ANALYSIS}/analysis.sqlite3?mode=ro', uri=True)
    classified = {r[0] for r in analysis.execute(
        'SELECT i.source_url FROM greece20_decision_effects e '
        'JOIN greece20_decision_interpretations i USING(decision_id)')}
    entries = [dict(r) for r in source.execute('SELECT * FROM rrf_catalogue_entries')]
    decisions = {r['source_url']: dict(r) for r in source.execute('SELECT * FROM greece20_decisions')}
    results = []
    excerpts = []
    for entry in entries:
        if entry['decision_url'] not in classified and not re.search(
            r'απενταξ|ανακλησ', normalized(entry['project_title'] or '')
        ):
            continue
        decision = decisions[entry['decision_url']]
        path = SNAPSHOT / decision['raw_path']
        digest = sha256(path.read_bytes()).hexdigest()
        assert digest == decision['content_sha256'], path
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages, 1):
            pages.append({'page': i, 'text': page.extract_text() or '',
                          'layout': page.extract_text(extraction_mode='layout') or ''})
        ops = '5179098' if entry['ops_code'] == '5079098' else entry['ops_code']
        history = [r for r in entries if r['ops_code'] == ops
                   and (r['inclusion_date'] or '') < (entry['inclusion_date'] or '')]
        history.sort(key=lambda r: (r['inclusion_date'] or '', r['entry_id']))
        record = {'ops_code': ops, 'catalogue': entry, 'decision': decision,
                  'pdf_absolute_path': str(path), 'pages': pages,
                  'prior_catalogue_history': history}
        results.append(record)
        excerpts.append(f"\n{'=' * 80}\nOPS {ops} / decision {decision['decision_id']} / "
                        f"date {entry['inclusion_date']} / catalogue budget {entry['source_budget_cents']}\n"
                        f"{entry['project_title']}\n"
                        f"PREVIOUS CATALOGUE: {[(r['inclusion_date'],r['source_budget_cents']) for r in history]}\n")
        for page in pages:
            if 'εγκεκριμενοσ' in normalized(page['layout']) or 'προτεινομενοσ' in normalized(page['layout']):
                excerpts.append(f"PAGE {page['page']}\n{page['layout']}\n")
        print(f"{len(results):3} {ops} {len(pages)} pages", flush=True)
    (OUTPUT / 'extracted-evidence.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    (OUTPUT / 'budget-tables.txt').write_text('\n'.join(excerpts))
    print(f'Extracted {len(results)} decisions; all source PDF hashes verified.')


if __name__ == '__main__':
    main()
