#!/usr/bin/env python3
"""Conservative 2026 Russian tax-threshold screening, NOT a tax calculation.

CSV columns: source_id,date,amount_rub,kind
kind: usn_income | psn_income | ausn_income | excluded | review
All amounts are positive RUB decimals. Reversals and corrections must be prepared
in the source register; do NOT mechanically turn bank debits into negative income.

Examples:
  python check_russia_2026.py incomes.csv
  python check_russia_2026.py incomes.csv --complete-years 2025,2026
"""
import argparse
import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

CATEGORIES = {'usn_income','psn_income','ausn_income','excluded','review'}
VAT_USN = Decimal('20000000')
PATENT_LIMIT = Decimal('20000000')
USN_LIMIT = Decimal('490500000')
AUSN_LIMIT = Decimal('60000000')


def analyze(path, complete_years=frozenset()):
    sums = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
    by_month = defaultdict(lambda: defaultdict(lambda: Decimal('0')))
    issues = []
    seen = set()
    row_count = 0
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        need = {'source_id','date','amount_rub','kind'}
        missing = need - set(reader.fieldnames or [])
        if missing:
            raise ValueError('Missing columns: '+', '.join(sorted(missing)))
        for n, line in enumerate(reader, 2):
            sid = (line.get('source_id') or '').strip()
            if not sid or sid in seen:
                issues.append({'line': n, 'reason': 'missing_or_duplicate_source_id', 'id': sid})
                continue
            seen.add(sid)
            try:
                s = (line.get('date') or '').strip()
                d = date.fromisoformat(s)
                if d.isoformat() != s:
                    raise ValueError('invalid ISO date')
                a = Decimal((line.get('amount_rub') or '').strip())
                if not a.is_finite() or a <= 0:
                    raise InvalidOperation()
                k = (line.get('kind') or '').strip()
                if k not in CATEGORIES:
                    raise ValueError('invalid kind')
            except (InvalidOperation, ValueError) as e:
                issues.append({'line': n, 'reason': 'invalid_date_amount_or_kind', 'id': sid})
                continue
            if d.year not in (2025, 2026):
                issues.append({'line': n, 'reason': 'outside_supported_years', 'id': sid})
                continue
            row_count += 1
            sums[d.year][k] += a
            if k in ('usn_income','psn_income'):
                by_month[d.year][d.month] += a
            if k == 'review':
                issues.append({'line': n, 'reason': 'classification_needs_review', 'id': sid})

    y25 = sums[2025]['usn_income']+sums[2025]['psn_income']
    y26 = sums[2026]['usn_income']+sums[2026]['psn_income']
    uncertain = bool(issues)
    reliable25 = 2025 in complete_years and not uncertain
    reliable26 = 2026 in complete_years and not uncertain
    crossing_month = None
    cumulative = Decimal('0')
    for month in range(1, 13):
        cumulative += by_month[2026][month]
        if cumulative > VAT_USN and crossing_month is None:
            crossing_month = month
    next_month = (f'2026-{crossing_month+1:02d}-01' if crossing_month and crossing_month < 12
                  else ('2027-01-01' if crossing_month == 12 else None))

    flags = []
    if y25 > VAT_USN:
        flags.append({'type':'usn_vat_prior_year','status':'review_required' if reliable25 else 'possible_exposure',
                      'note':'2025 taxable income exceeds 2026 VAT exemption threshold; review VAT obligations from 2026-01-01'})
    elif not reliable25:
        flags.append({'type':'usn_vat_prior_year','status':'unknown', 'note':'2025 tax income coverage incomplete; cannot confirm exemption'})
    if crossing_month:
        # If prior-year income already triggered VAT from 1 January, the in-year
        # crossing must not imply a later initial obligation date.
        already_due_from_january = reliable25 and y25 > VAT_USN
        flags.append({'type':'usn_vat_in_year',
                      'status':('already_triggered_prior_year' if already_due_from_january
                                else ('review_required' if reliable26 else 'possible_exposure')),
                      'crossing_month':f'2026-{crossing_month:02d}',
                      'potential_from':('2026-01-01' if already_due_from_january else next_month),
                      'note':('Prior-year threshold already exceeded; 2026 crossing does NOT defer initial VAT obligation'
                              if already_due_from_january else
                              'Cumulative 2026 tax-classified income exceeds RUB 20m; verify liability and exact recognition')})
    elif not reliable26:
        flags.append({'type':'usn_vat_in_year','status':'unknown','note':'2026 year incomplete; threshold may be exceeded later'})
    if y26 > USN_LIMIT:
        flags.append({'type':'usn_regime_limit','status':'review_required',
                      'note':'2026 total classified USN+PSN income exceeds RUB 490.5m; eligibility needs a full legal review'})
    if sums[2025]['psn_income'] or sums[2026]['psn_income']:
        if y25 > PATENT_LIMIT or y26 > PATENT_LIMIT:
            flags.append({'type':'psn_limit','status':'review_required',
                          'note':'Potential 2026 PSN income-limit issue; combined regimes and prior-year income require review'})
    if sums[2026]['ausn_income'] > AUSN_LIMIT:
        flags.append({'type':'ausn_limit','status':'review_required', 'note':'AUSN 2026 classified income exceeds RUB 60m'})
    if uncertain:
        flags.append({'type':'data_quality','status':'unknown','note':'One or more source rows require manual correction'})

    return {
        'tax_year':2026,
        'rule_verified_as_of':'2026-10-26',
        'valid_rows':row_count,
        'complete_years_declared':sorted(complete_years),
        'totals_rub':{
            str(year):{k:str(sums[year][k]) for k in sorted(CATEGORIES)}
            for year in (2025,2026)},
        'usn_psn_combined_income_rub':{'2025':str(y25),'2026':str(y26)},
        'issues':issues,'flags':flags,
        'notice':'Screening only: input must be tax-classified income, NOT raw bank receipts. No definitive VAT/tax status, patent eligibility, returns, or tax payments. Verify applicable FNS rules and region.'
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv_file')
    p.add_argument('--complete-years',default='',help='Only if independently reconciled and complete, e.g. 2025,2026')
    p.add_argument('--output')
    args=p.parse_args()
    yrs=set()
    if args.complete_years:
        yrs={int(y) for y in args.complete_years.split(',')}
        if yrs - {2025,2026}:
            p.error('Only 2025,2026 are supported')
    r=analyze(args.csv_file,yrs)
    s=json.dumps(r,ensure_ascii=False,indent=2)
    if args.output:
        Path(args.output).write_text(s+'\n',encoding='utf-8')
    else:
        print(s)

if __name__=='__main__':
    main()
