# Add this near the top of your app.py file, after the imports

import json
import re
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Callable, Dict, List, Tuple, Optional

import pandas as pd
import streamlit as st

from core_utils import (
    bytes_to_pdfplumber,
    dedupe_transactions,
    normalize_transactions,
    safe_float,
)
from transaction_analysis import parse_top_parties_and_high_value

from maybank import parse_transactions_maybank
from public_bank import parse_transactions_pbb
from rhb import parse_transactions_rhb
from cimb import parse_transactions_cimb,extract_cimb_party_name
from bank_islam import parse_bank_islam
from bank_rakyat import parse_bank_rakyat
from hong_leong import parse_hong_leong
from ambank import parse_ambank, extract_ambank_statement_totals
from bank_muamalat import parse_transactions_bank_muamalat
from affin_bank import parse_affin_bank, extract_affin_statement_totals
from agro_bank import parse_agro_bank
from ocbc import parse_transactions_ocbc
from uob import parse_transactions_uob
from alliance import parse_transactions_alliance
from pdf_security import is_pdf_encrypted, decrypt_pdf_bytes

# Import the extracted functions
from pdf_utils import extract_company_name, extract_account_number
from bank_totals import (
    extract_cimb_statement_totals,
    extract_rhb_statement_totals,
    extract_bank_islam_statement_month
)

# Fraud detection logic imports
from fraud_logic import (
    analyze_pdf_batch,
    build_display_summary,
    detect_font_anomalies,
)

# Track 2 classifier — build_track2_result produces the full v6.3.5 schema
# that generate_interactive_html expects (flags, statutory compliance, risk
# indicators, RP detection, etc.)
try:
    from kredit_lab_classify_track2 import (
        build_track2_result,
        account_meta_from_determinations,
    )
    _TRACK2_AVAILABLE = True
except ImportError:
    _TRACK2_AVAILABLE = False

def build_large_transactions(transactions: List[dict], threshold: float) -> List[dict]:
    """Build list of large transactions (both credits and debits) above threshold."""
    large_txns = []
    threshold_float = float(threshold)
    
    for t in transactions:
        credit = safe_float(t.get('credit', 0))
        debit = safe_float(t.get('debit', 0))
        
        # Check both credits and debits
        if credit >= threshold_float:
            large_txns.append({
                'date': t.get('date', ''),
                'description': t.get('description', ''),
                'amount': credit,
                'balance': t.get('balance', 0),
                'type': 'CREDIT'
            })
        elif debit >= threshold_float:
            large_txns.append({
                'date': t.get('date', ''),
                'description': t.get('description', ''),
                'amount': debit,
                'balance': t.get('balance', 0),
                'type': 'DEBIT'
            })
    
    # Sort by amount descending
    large_txns.sort(key=lambda x: x['amount'], reverse=True)
    return large_txns

# Function copy from HTML, def generate_interactive_html(data) - you will replace this with the full function from your original converter file
def generate_interactive_html(data):
    """Generate interactive HTML report for v6.0.0 schema"""

     # Define build_large_transactions inside the function if needed
    def build_large_transactions_internal(transactions, threshold):
        """Build list of large transactions from transaction list"""
        large_txns = []
        threshold_float = float(threshold) if threshold else 100000.0
        
        for t in transactions:
            if not isinstance(t, dict):
                continue
            credit = safe_float(t.get('credit', 0))
            debit = safe_float(t.get('debit', 0))
            
            if credit >= threshold_float:
                large_txns.append({
                    'date': t.get('date', ''),
                    'description': t.get('description', ''),
                    'amount': credit,
                    'balance': t.get('balance', 0),
                    'type': 'CREDIT'
                })
            elif debit >= threshold_float:
                large_txns.append({
                    'date': t.get('date', ''),
                    'description': t.get('description', ''),
                    'amount': debit,
                    'balance': t.get('balance', 0),
                    'type': 'DEBIT'
                })
        
        large_txns.sort(key=lambda x: x.get('amount', 0), reverse=True)
        return large_txns
    
    # Then use it:
    large_txns = data.get('large_transactions', [])
    if not large_txns and data.get('transactions'):
        large_txns = build_large_transactions_internal(data.get('transactions', []), large_threshold)

    r = data.get('report_info', {})
    accounts = data.get('accounts', [])
    monthly = data.get('monthly_analysis', [])
    consol = data.get('consolidated', {})
    top_parties = data.get('top_parties', {})
    large_credits = data.get('large_credits', [])
    own_related = data.get('own_related_transactions', {})
    if isinstance(own_related, list):
        own_related = {'transactions': own_related, 'summary': {}}
    elif not isinstance(own_related, dict):
        own_related = {}
    loans = data.get('loan_transactions', {})
    flags_data = data.get('flags', {})
    obs = normalize_observations(data.get('observations', {}))
    parsing = data.get('parsing_metadata', {})

    # Version detection
    schema_v = r.get('schema_version', '')
    is_v620 = schema_v in ('6.2.0', '6.2.1', '6.2.2', '6.3.0', '6.3.1', '6.3.2', '6.3.3', '6.3.4', '6.3.5') or consol.get('total_fx_credits') is not None
    is_v630 = schema_v in ('6.3.0', '6.3.1', '6.3.2', '6.3.3', '6.3.4', '6.3.5') or consol.get('total_unclassified_cr') is not None
    is_v635 = schema_v in ('6.3.4', '6.3.5')
    has_parsing = bool(parsing)
    has_monthly_bd = any(p.get('monthly_breakdown') for p in (top_parties.get('top_payers') or top_parties.get('top_creditors') or []) + (top_parties.get('top_payees') or top_parties.get('top_debtors') or []))

    # v6.2.1: Data quality detection
    data_completeness = consol.get('data_completeness', 'COMPLETE')
    has_recon = any(m.get('reconciliation_status') for m in monthly)
    is_incomplete = data_completeness == 'INCOMPLETE'
    total_missing_dr = consol.get('total_missing_debits', 0) or 0
    total_missing_cr = consol.get('total_missing_credits', 0) or 0
    months_with_gaps = consol.get('months_with_gaps', 0) or 0
    total_gaps = consol.get('total_extraction_gaps', 0) or 0
    dq_warning = consol.get('data_quality_warning', '')

    company = r.get('company_name', 'Company')
    period_start = r.get('period_start', '')
    period_end = r.get('period_end', '')
    total_months = r.get('total_months', 0)
    related_parties = r.get('related_parties', [])

    # Build data quality banner HTML
    dq_banner_html = ''
    if has_recon:
        if is_incomplete:
            affected_months = ', '.join(m.get('month', '') for m in monthly if m.get('reconciliation_status') == 'FAIL')
            dq_banner_html = f'''
            <div class="dq-banner dq-fail">
                <div class="dq-icon">⚠️</div>
                <div>
                    <div class="dq-title">Incomplete Extraction — {months_with_gaps} of {total_months} Months Affected</div>
                    <div class="dq-detail">Balance trail reconciliation detected {total_gaps} extraction gap(s) where transactions exist in the source PDF but were not captured. Figures marked with ⚠️ are understated.</div>
                    <div class="dq-stats">
                        <div><div class="dq-stat-label">Missing Debits</div><div class="dq-stat-val">RM {total_missing_dr:,.2f}</div></div>
                        <div><div class="dq-stat-label">Missing Credits</div><div class="dq-stat-val" style="color:var(--green)">RM {total_missing_cr:,.2f}</div></div>
                        <div><div class="dq-stat-label">Gaps</div><div class="dq-stat-val">{total_gaps}</div></div>
                        <div><div class="dq-stat-label">Months Affected</div><div class="dq-stat-val">{affected_months}</div></div>
                    </div>
                </div>
            </div>'''
        else:
            dq_banner_html = f'''
            <div class="dq-banner dq-pass">
                <div class="dq-icon">✅</div>
                <div>
                    <div class="dq-title">Extraction Complete — All {total_months} Months Pass Reconciliation</div>
                    <div class="dq-detail">Every transaction's running balance matches the statement balance. No extraction gaps detected.</div>
                </div>
            </div>'''

    # ── Account cards ──
    def _pick_num(d, *keys):
        if not isinstance(d, dict):
            return 0
        for k in keys:
            v = d.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    try:
                        return float(str(v).replace(',', '').replace('RM', '').strip())
                    except (TypeError, ValueError):
                        return 0
        return 0

    acc_cards = ""
    for a in accounts:
        a_summary = a.get('summary', {}) if isinstance(a.get('summary'), dict) else {}
        a_balances = a.get('balances', {}) if isinstance(a.get('balances'), dict) else {}
        opening = _pick_num(a, 'opening_balance', 'balance_open', 'open_balance') or _pick_num(a_summary, 'opening_balance', 'open_balance') or _pick_num(a_balances, 'opening', 'open')
        closing = _pick_num(a, 'closing_balance', 'ending_balance', 'balance_close', 'close_balance') or _pick_num(a_summary, 'closing_balance', 'ending_balance') or _pick_num(a_balances, 'closing', 'ending', 'close')
        credits_v = _pick_num(a, 'total_credits', 'total_credit', 'gross_credits', 'credits') or _pick_num(a_summary, 'total_credits', 'total_credit', 'gross_credits')
        debits_v = _pick_num(a, 'total_debits', 'total_debit', 'gross_debits', 'debits') or _pick_num(a_summary, 'total_debits', 'total_debit', 'gross_debits')
        txn_count = int(_pick_num(a, 'transaction_count', 'txn_count', 'total_transactions', 'transactions_count') or _pick_num(a_summary, 'transaction_count', 'txn_count'))
        acc_cards += f'''
        <div class="account-card">
            <div class="account-header">
                <span class="bank-name">{a.get('bank_name','')}</span>
                <span class="badge badge-{a.get('account_type','Current').lower()}">{a.get('account_type','')}</span>
            </div>
            <div class="account-number">A/C: {a.get('account_number','')}</div>
            <div class="account-holder">{a.get('account_holder','')}</div>
            <div class="account-metrics">
                <div class="metric"><div class="metric-label">Opening</div><div class="metric-value">RM {opening:,.2f}</div></div>
                <div class="metric"><div class="metric-label">Closing</div><div class="metric-value {'debit' if closing < 10000 else ''}">RM {closing:,.2f}</div></div>
                <div class="metric"><div class="metric-label">Credits</div><div class="metric-value credit">RM {credits_v:,.2f}</div></div>
                <div class="metric"><div class="metric-label">Debits</div><div class="metric-value debit">RM {debits_v:,.2f}</div></div>
                <div class="metric"><div class="metric-label">Transactions</div><div class="metric-value">{txn_count:,}</div></div>
            </div>
        </div>'''

    # ── Related parties ──
    rp_html = ""
    for rp in related_parties:
        name = rp.get('name', rp) if isinstance(rp, dict) else str(rp)
        rel = rp.get('relationship', '') if isinstance(rp, dict) else ''
        rp_html += f'<span class="rp-tag">{name} <small>({rel})</small></span>'

    # ── Monthly analysis table rows (per-account with month subtotals) ──
    # Detect if data has per-account rows (v6.1.0) or consolidated (v6.0.0)
    has_account_col = any(m.get('account_number') for m in monthly)

    # Group by month for subtotals and chart aggregation
    from collections import OrderedDict
    monthly_by_month = OrderedDict()
    for m in monthly:
        mo = m.get('month', '')
        if mo not in monthly_by_month:
            monthly_by_month[mo] = []
        monthly_by_month[mo].append(m)

    # Build distinct accounts list for coloring
    acct_list = []
    seen_acct = set()
    for m in monthly:
        an = m.get('account_number', '')
        if an and an not in seen_acct:
            acct_list.append(an)
            seen_acct.add(an)
    acct_colors = {}
    palette = ['var(--blue)', 'var(--purple)', 'var(--green)', 'var(--amber)']
    for i, a in enumerate(acct_list):
        acct_colors[a] = palette[i % len(palette)]

    monthly_rows = ""
    # Aggregated data for charts (per-month consolidated)
    chart_agg = OrderedDict()  # month -> {net_credits, net_debits, eod_lowest, eod_highest, eod_average}

    for mo, rows in monthly_by_month.items():
        # Aggregate for chart
        agg = {}
        sum_fields = ['gross_credits','gross_debits','net_credits','net_debits',
                       'own_party_cr','own_party_dr','related_party_cr','related_party_dr',
                       'reversal_cr','loan_disbursement_cr','fd_interest_cr',
                       'cash_deposits_amount','cash_withdrawals_amount',
                       'cheque_deposits_amount','cheque_issues_amount',
                       'loan_repayment_dr','salary_paid',
                       'statutory_epf','statutory_socso','statutory_tax',
                       'returned_cheques_outward_amount','returned_cheques_outward_count',
                       'round_figure_cr','high_value_cr',
                       'credit_count','debit_count',
                       'own_party_cr_count','own_party_dr_count',
                       'related_party_cr_count','related_party_dr_count',
                       'loan_repayment_count','inward_return_cr',
                       'unclassified_cr_count','unclassified_cr_amount',
                       'unclassified_dr_count','unclassified_dr_amount']
        for fld in sum_fields:
            agg[fld] = sum(r.get(fld, 0) or 0 for r in rows)
        agg['eod_lowest'] = min(r.get('eod_lowest', 0) or 0 for r in rows)
        agg['eod_highest'] = max(r.get('eod_highest', 0) or 0 for r in rows)
        agg['eod_average'] = sum(r.get('eod_average', 0) or 0 for r in rows) / len(rows) if rows else 0
        # For multi-account months, sum opening/closing across accounts; for single account, take directly
        agg['opening_balance'] = sum(r.get('opening_balance', 0) or 0 for r in rows)
        agg['closing_balance'] = sum(r.get('closing_balance', 0) or 0 for r in rows)
        chart_agg[mo] = agg

        if has_account_col and len(rows) > 1:
            # Multiple accounts — show per-account rows then subtotal
            for m in rows:
                an = m.get('account_number', '')
                bn = m.get('bank_name', '')
                short_bank = bn.split(' ')[0] if bn else ''  # e.g. "OCBC" or "CIMB"
                acct_label = f"{short_bank} {an}" if an else mo
                dot_color = acct_colors.get(an, 'var(--text-muted)')
                monthly_rows += f'''<tr style="font-size:0.78rem;">
            <td class="sticky-col" style="padding-left:1.5rem;font-weight:400"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot_color};margin-right:6px;vertical-align:middle"></span>{acct_label}</td>
            {'<td></td>' if has_recon else ''}
            <td class="mono r credit">{m.get('gross_credits',0):,.2f}</td>
            <td class="mono r debit">{m.get('gross_debits',0):,.2f}</td>
            <td class="mono r credit">{m.get('net_credits',0):,.2f}</td>
            <td class="mono r debit">{m.get('net_debits',0):,.2f}</td>
            <td class="mono r">{m.get('credit_count',0)}</td>
            <td class="mono r">{m.get('debit_count',0)}</td>
            <td class="mono r">{m.get('own_party_cr',0):,.2f}</td>
            <td class="mono r">{m.get('own_party_dr',0):,.2f}</td>
            <td class="mono r">{m.get('related_party_cr',0):,.2f}</td>
            <td class="mono r">{m.get('related_party_dr',0):,.2f}</td>
            <td class="mono r">{m.get('reversal_cr',0):,.2f}</td>
            <td class="mono r">{m.get('loan_disbursement_cr',0):,.2f}</td>
            <td class="mono r">{m.get('fd_interest_cr',0):,.2f}</td>
            <td class="mono r">{m.get('cash_deposits_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cash_withdrawals_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cheque_deposits_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cheque_issues_amount',0):,.2f}</td>
            <td class="mono r">{m.get('loan_repayment_dr',0):,.2f}</td>
            <td class="mono r">{m.get('salary_paid',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_epf',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_socso',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_tax',0):,.2f}</td>
            <td class="mono r">{m.get('returned_cheques_outward_count',0)}</td>
            <td class="mono r">{m.get('returned_cheques_outward_amount',0):,.2f}</td>
            <td class="mono r">{m.get('round_figure_cr',0):,.2f}</td>
            <td class="mono r">{m.get('high_value_cr',0):,.2f}</td>
            <td class="mono r">{m.get('eod_lowest',0):,.2f}</td>
            <td class="mono r">{m.get('eod_highest',0):,.2f}</td>
            <td class="mono r">{m.get('eod_average',0):,.2f}</td>
            <td class="mono r">{m.get('opening_balance',0):,.2f}</td>
            <td class="mono r">{m.get('closing_balance',0):,.2f}</td>
            {'<td class="mono r v630-count">' + str(m.get('own_party_cr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('own_party_dr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('related_party_cr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('related_party_dr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('loan_repayment_count',0)) + '</td><td class="mono r v630-amt">' + f"{m.get('inward_return_cr',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(m.get('unclassified_cr_count',0)) + '</td><td class="mono r v630-uncl">' + f"{m.get('unclassified_cr_amount',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(m.get('unclassified_dr_count',0)) + '</td><td class="mono r v630-uncl">' + f"{m.get('unclassified_dr_amount',0):,.2f}" + '</td>' if is_v630 else ''}
        </tr>'''

            # Month subtotal row
            a = agg
            # v6.2.1: aggregate reconciliation for multi-account month
            month_recon_cell = ''
            if has_recon:
                any_fail = any(r.get('reconciliation_status') == 'FAIL' for r in rows)
                if any_fail:
                    total_gaps = sum(r.get('extraction_gaps', 0) for r in rows)
                    total_miss = sum(r.get('missing_debit_amount', 0) for r in rows)
                    month_recon_cell = f'<td><span class="recon-badge fail">✗ FAIL</span> <span class="gap-pill">{total_gaps} gap{"s" if total_gaps > 1 else ""} · RM {total_miss:,.0f}</span></td>'
                else:
                    month_recon_cell = '<td><span class="recon-badge pass">✓ PASS</span></td>'
            monthly_rows += f'''<tr style="background:var(--bg);font-weight:600;border-bottom:2px solid var(--border-accent);{"" if not (has_recon and any_fail) else ""}">
            <td class="sticky-col">{mo}</td>
            {month_recon_cell}
            <td class="mono r credit">{a['gross_credits']:,.2f}</td>
            <td class="mono r debit">{a['gross_debits']:,.2f}</td>
            <td class="mono r credit" style="font-weight:700">{a['net_credits']:,.2f}</td>
            <td class="mono r debit" style="font-weight:700">{a['net_debits']:,.2f}</td>
            <td class="mono r">{int(a['credit_count'])}</td>
            <td class="mono r">{int(a['debit_count'])}</td>
            <td class="mono r">{a['own_party_cr']:,.2f}</td>
            <td class="mono r">{a['own_party_dr']:,.2f}</td>
            <td class="mono r">{a['related_party_cr']:,.2f}</td>
            <td class="mono r">{a['related_party_dr']:,.2f}</td>
            <td class="mono r">{a['reversal_cr']:,.2f}</td>
            <td class="mono r">{a['loan_disbursement_cr']:,.2f}</td>
            <td class="mono r">{a['fd_interest_cr']:,.2f}</td>
            <td class="mono r">{a['cash_deposits_amount']:,.2f}</td>
            <td class="mono r">{a['cash_withdrawals_amount']:,.2f}</td>
            <td class="mono r">{a['cheque_deposits_amount']:,.2f}</td>
            <td class="mono r">{a['cheque_issues_amount']:,.2f}</td>
            <td class="mono r">{a['loan_repayment_dr']:,.2f}</td>
            <td class="mono r">{a['salary_paid']:,.2f}</td>
            <td class="mono r">{a['statutory_epf']:,.2f}</td>
            <td class="mono r">{a['statutory_socso']:,.2f}</td>
            <td class="mono r">{a['statutory_tax']:,.2f}</td>
            <td class="mono r">{int(a['returned_cheques_outward_count'])}</td>
            <td class="mono r">{a['returned_cheques_outward_amount']:,.2f}</td>
            <td class="mono r">{a['round_figure_cr']:,.2f}</td>
            <td class="mono r">{a['high_value_cr']:,.2f}</td>
            <td class="mono r">{a['eod_lowest']:,.2f}</td>
            <td class="mono r">{a['eod_highest']:,.2f}</td>
            <td class="mono r">{a['eod_average']:,.2f}</td>
            <td class="mono r">{a['opening_balance']:,.2f}</td>
            <td class="mono r">{a['closing_balance']:,.2f}</td>
            {'<td class="mono r v630-count">' + str(int(a.get('own_party_cr_count',0))) + '</td><td class="mono r v630-count">' + str(int(a.get('own_party_dr_count',0))) + '</td><td class="mono r v630-count">' + str(int(a.get('related_party_cr_count',0))) + '</td><td class="mono r v630-count">' + str(int(a.get('related_party_dr_count',0))) + '</td><td class="mono r v630-count">' + str(int(a.get('loan_repayment_count',0))) + '</td><td class="mono r v630-amt">' + f"{a.get('inward_return_cr',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(int(a.get('unclassified_cr_count',0))) + '</td><td class="mono r v630-uncl">' + f"{a.get('unclassified_cr_amount',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(int(a.get('unclassified_dr_count',0))) + '</td><td class="mono r v630-uncl">' + f"{a.get('unclassified_dr_amount',0):,.2f}" + '</td>' if is_v630 else ''}
        </tr>'''
        else:
            # Single account or v6.0.0 consolidated — single row per month
            m = rows[0] if rows else {}
            recon_status = m.get('reconciliation_status', '')
            row_class = ' class="row-fail"' if recon_status == 'FAIL' else ''
            recon_cell = ''
            if has_recon:
                if recon_status == 'FAIL':
                    gap_count = m.get('extraction_gaps', 0)
                    miss_dr = m.get('missing_debit_amount', 0)
                    recon_cell = f'<td><span class="recon-badge fail">✗ FAIL</span> <span class="gap-pill">{gap_count} gap{"s" if gap_count > 1 else ""} · RM {miss_dr:,.0f}</span></td>'
                else:
                    recon_cell = '<td><span class="recon-badge pass">✓ PASS</span></td>'
            monthly_rows += f'''<tr{row_class}>
            <td class="sticky-col">{m.get('month','')}</td>
            {recon_cell}
            <td class="mono r credit">{m.get('gross_credits',0):,.2f}</td>
            <td class="mono r debit">{m.get('gross_debits',0):,.2f}</td>
            <td class="mono r credit" style="font-weight:600">{m.get('net_credits',0):,.2f}</td>
            <td class="mono r debit" style="font-weight:600">{m.get('net_debits',0):,.2f}</td>
            <td class="mono r">{m.get('credit_count',0)}</td>
            <td class="mono r">{m.get('debit_count',0)}</td>
            <td class="mono r">{m.get('own_party_cr',0):,.2f}</td>
            <td class="mono r">{m.get('own_party_dr',0):,.2f}</td>
            <td class="mono r">{m.get('related_party_cr',0):,.2f}</td>
            <td class="mono r">{m.get('related_party_dr',0):,.2f}</td>
            <td class="mono r">{m.get('reversal_cr',0):,.2f}</td>
            <td class="mono r">{m.get('loan_disbursement_cr',0):,.2f}</td>
            <td class="mono r">{m.get('fd_interest_cr',0):,.2f}</td>
            <td class="mono r">{m.get('cash_deposits_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cash_withdrawals_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cheque_deposits_amount',0):,.2f}</td>
            <td class="mono r">{m.get('cheque_issues_amount',0):,.2f}</td>
            <td class="mono r">{m.get('loan_repayment_dr',0):,.2f}</td>
            <td class="mono r">{m.get('salary_paid',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_epf',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_socso',0):,.2f}</td>
            <td class="mono r">{m.get('statutory_tax',0):,.2f}</td>
            <td class="mono r">{m.get('returned_cheques_outward_count',0)}</td>
            <td class="mono r">{m.get('returned_cheques_outward_amount',0):,.2f}</td>
            <td class="mono r">{m.get('round_figure_cr',0):,.2f}</td>
            <td class="mono r">{m.get('high_value_cr',0):,.2f}</td>
            <td class="mono r">{m.get('eod_lowest',0):,.2f}</td>
            <td class="mono r">{m.get('eod_highest',0):,.2f}</td>
            <td class="mono r">{m.get('eod_average',0):,.2f}</td>
            <td class="mono r">{m.get('opening_balance',0):,.2f}</td>
            <td class="mono r">{m.get('closing_balance',0):,.2f}</td>
            {'<td class="mono r v630-count">' + str(m.get('own_party_cr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('own_party_dr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('related_party_cr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('related_party_dr_count',0)) + '</td><td class="mono r v630-count">' + str(m.get('loan_repayment_count',0)) + '</td><td class="mono r v630-amt">' + f"{m.get('inward_return_cr',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(m.get('unclassified_cr_count',0)) + '</td><td class="mono r v630-uncl">' + f"{m.get('unclassified_cr_amount',0):,.2f}" + '</td><td class="mono r v630-uncl">' + str(m.get('unclassified_dr_count',0)) + '</td><td class="mono r v630-uncl">' + f"{m.get('unclassified_dr_amount',0):,.2f}" + '</td>' if is_v630 else ''}
        </tr>'''

    # ── Consolidated totals row ──
    total_status_cell = ''
    if has_recon:
        if is_incomplete:
            total_status_cell = f'<td><span class="recon-badge fail">⚠️ {months_with_gaps} FAIL</span></td>'
        else:
            total_status_cell = '<td><span class="recon-badge pass">ALL PASS</span></td>'
    consol_row = f'''<tr class="total-row">
        <td class="sticky-col" style="font-weight:700">TOTAL</td>
        {total_status_cell}
        <td class="mono r credit">{consol.get('gross_credits',0):,.2f}</td>
        <td class="mono r debit">{consol.get('gross_debits',0):,.2f}</td>
        <td class="mono r credit">{consol.get('net_credits',0):,.2f}</td>
        <td class="mono r debit">{consol.get('net_debits',0):,.2f}</td>
        <td class="mono r">-</td><td class="mono r">-</td>
        <td class="mono r">{consol.get('total_own_party_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_own_party_dr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_related_party_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_related_party_dr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_reversal_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_loan_disbursement_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_fd_interest_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_cash_deposits',0):,.2f}</td>
        <td class="mono r">{consol.get('total_cash_withdrawals',0):,.2f}</td>
        <td class="mono r">{consol.get('total_cheque_deposits',0):,.2f}</td>
        <td class="mono r">{consol.get('total_cheque_issues',0):,.2f}</td>
        <td class="mono r">{consol.get('total_loan_repayment_dr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_salary_paid',0):,.2f}</td>
        <td class="mono r">{consol.get('total_statutory_epf',0):,.2f}</td>
        <td class="mono r">{consol.get('total_statutory_socso',0):,.2f}</td>
        <td class="mono r">{consol.get('total_statutory_tax',0):,.2f}</td>
        <td class="mono r">{consol.get('total_returned_cheques_outward',0):,.2f}</td>
        <td class="mono r">{consol.get('total_returned_cheques_outward',0):,.2f}</td>
        <td class="mono r">{consol.get('total_round_figure_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('total_high_value_cr',0):,.2f}</td>
        <td class="mono r">{consol.get('eod_lowest',0):,.2f}</td>
        <td class="mono r">{consol.get('eod_highest',0):,.2f}</td>
        <td class="mono r">{consol.get('eod_average',0):,.2f}</td>
        <td class="mono r">-</td>
        <td class="mono r">-</td>
        {'<td class="mono r v630-count">-</td><td class="mono r v630-count">-</td><td class="mono r v630-count">-</td><td class="mono r v630-count">-</td><td class="mono r v630-count">-</td><td class="mono r v630-amt">' + f"{consol.get('total_inward_return_cr',0):,.2f}" + '</td><td class="mono r v630-uncl">-</td><td class="mono r v630-uncl">' + f"{consol.get('total_unclassified_cr',0):,.2f}" + '</td><td class="mono r v630-uncl">-</td><td class="mono r v630-uncl">' + f"{consol.get('total_unclassified_dr',0):,.2f}" + '</td>' if is_v630 else ''}
    </tr>'''

    # ── Top payers/payees ──
    # Normalize: schema may emit `top_payers`/`top_payees` or `top_creditors`/`top_debtors`
    def _normalize_party(p, is_payer):
        if not isinstance(p, dict):
            return {}
        amt = p.get('total_amount')
        if amt is None:
            amt = p.get('total_credits') if is_payer else p.get('total_debits')
        if amt is None:
            amt = p.get('amount', 0)
        return {
            'rank': p.get('rank', ''),
            'party_name': p.get('party_name') or p.get('name') or '',
            'total_amount': amt or 0,
            'transaction_count': p.get('transaction_count') or p.get('txn_count') or 0,
            'is_related_party': p.get('is_related_party', False),
            'monthly_breakdown': p.get('monthly_breakdown'),
        }

    # v6.3.3.2: ghost-verb suppression (cross-bank). Defensive filter — excludes counterparty
    # entries that are ONLY a payment-rail prefix with no entity name attached. Parser dropouts
    # like bare 'TRANSFER FR A/C', 'TR TO C/A', 'IBG CREDIT', 'Instant Transfer' should not rank
    # as top parties. See CLASSIFICATION_RULES_v3_3.json CN6 for the full spec.
    _GHOST_STOPWORDS = {
        # Generic transfer verbs / rails
        'TRANSFER', 'PAYMENT', 'IBG', 'IB2G', 'IBFT', 'IBK', 'CR', 'DR', 'CREDIT', 'DEBIT',
        'TO', 'FR', 'FROM', 'A/C', 'C/A', 'ACCOUNT', 'ACCT', 'INTER', 'BANK', 'BANKING', 'INTO',
        'ONLINE', 'DUITNOW', 'DUIT', 'NOW', 'FPX', 'RENTAS', 'REMITTANCE', 'ELECTRONIC',
        'AUTOPAY', 'INSTANT', 'FAST', 'OUTWARD', 'INWARD', 'OUTW', 'INW',
        'OUT', 'IN', 'ADVICE', 'TRF', 'BLKTRF', 'NBPS', 'TR', 'PYMT', 'PAY',
        # English connectives
        'THE', 'AND', 'OF', 'FOR', 'WITH',
        # Account-side / card abbreviations
        'SA', 'CA', 'CCARD', 'CARD',
        # Cheque / cash
        'CHQ', 'CHEQUE', 'CASH', 'DEPOSIT', 'WITHDRAWAL', 'HSE', 'HOUSE',
        'CLRG', 'CDM', '2D', 'LOCAL', 'GIR', 'GIRO',
        # Bank name abbreviations (Malaysia) — when parser outputs just "<BANK_ABBR> IBG" with
        # no entity after. Only include abbreviations that are unlikely to appear inside real
        # company names. Excluded words like HONG/LEONG/PUBLIC/ALLIANCE/RAKYAT/BERHAD — those
        # can legitimately be part of a real entity name.
        'HLB', 'MBB', 'RHB', 'ABB', 'PBB', 'BIMB', 'AMB', 'AMBANK', 'PBE',
        'CIMB', 'OCBC', 'UOB', 'BSN',
        # Misc salary/payment abbreviations
        'PMT', 'SLRY',
    }
    _CHEQUE_NOISE = {
        'HSE CHQ DEPOSIT', 'CDM CASH DEPOSIT', '2D LOCAL CHQ', 'CASH CHQ DR',
        'HOUSE CHQ DR', 'CLRG CHQ DR', 'HSE CHQ', 'CHEQUE DEPOSIT', 'CHQ DEPOSIT',
    }

    def _is_ghost_verb(name):
        """Return True if name is a parser-dropout (no real entity)."""
        if not name:
            return True
        import re as _re
        normalised = _re.sub(r'[.,]', '', name.upper())
        # strip common company suffixes so "TRANSFER TO A/C" vs "TRANSFER TO A/C SDN BHD" both normalise
        normalised = _re.sub(r'\b(SDN|BHD|& CO|\(M\)|PTY|LTD)\b', '', normalised)
        normalised = _re.sub(r'\s+', ' ', normalised).strip()
        if not normalised:
            return True
        if normalised in _CHEQUE_NOISE:
            return True
        # Tokenise on whitespace and slashes. A real entity has at least one alphabetic token
        # of ≥3 letters that is NOT in the stopword set.
        tokens = [t for t in _re.split(r'[\s/\-]+', normalised) if t]
        real_tokens = [t for t in tokens if len(t) >= 3 and t not in _GHOST_STOPWORDS and _re.search(r'[A-Z]', t)]
        return len(real_tokens) == 0

    _raw_payers = top_parties.get('top_payers') or top_parties.get('top_creditors') or []
    _raw_payees = top_parties.get('top_payees') or top_parties.get('top_debtors') or []
    # Filter ghost verbs BEFORE slicing to 10, so suppressed entries don't crowd out real ones.
    _payers_all = [_normalize_party(p, True) for p in _raw_payers]
    _payees_all = [_normalize_party(p, False) for p in _raw_payees]
    _payers_suppressed = [p for p in _payers_all if _is_ghost_verb(p.get('party_name', ''))]
    _payees_suppressed = [p for p in _payees_all if _is_ghost_verb(p.get('party_name', ''))]
    _payers = [p for p in _payers_all if not _is_ghost_verb(p.get('party_name', ''))][:10]
    _payees = [p for p in _payees_all if not _is_ghost_verb(p.get('party_name', ''))][:10]
    # Re-rank 1..N after filtering
    for i, p in enumerate(_payers, 1):
        p['rank'] = i
    for i, p in enumerate(_payees, 1):
        p['rank'] = i

    # v6.3.3.2 safeguard: render suppressed buckets in a VISIBLE panel under the Top 10, so
    # analyst never loses sight of what was hidden. If the suppressed bucket has a material
    # amount (>=RM 100,000) OR high transaction count (>=50), flag it with a VERIFY warning so
    # a possible real-entity false-positive gets human review.
    def _render_suppressed(entries, side_css):
        if not entries:
            return ''
        entries_sorted = sorted(entries, key=lambda p: p.get('total_amount', 0) or 0, reverse=True)
        rows = ''
        for p in entries_sorted:
            amt = p.get('total_amount', 0) or 0
            n = p.get('transaction_count', 0) or 0
            warn = ''
            if amt >= 100000 or n >= 50:
                warn = '<span style="background:var(--amber);color:white;padding:1px 6px;border-radius:3px;font-size:0.7rem;margin-left:6px">VERIFY</span>'
            rows += f'''<tr>
                <td style="color:var(--text-muted)">{p.get("party_name","") or "(empty)"}{warn}</td>
                <td class="mono r {side_css}">RM {amt:,.2f}</td>
                <td class="mono r">{n}</td>
            </tr>'''
        return f'''<div style="padding:0.75rem 1.25rem;background:var(--surface-subtle);border-top:1px solid var(--border)">
            <div style="font-size:0.78rem;color:var(--text-soft);margin-bottom:0.5rem">
                <strong>Parser-dropped buckets</strong> — counterparties that were only a transfer verb with no entity name attached.
                Amounts are still counted in gross/net totals; they are hidden from the Top 10 rank to avoid misleading the analyst.
                <span style="background:var(--amber);color:white;padding:1px 6px;border-radius:3px;font-size:0.7rem">VERIFY</span> = high volume \u2014 possible real-entity false positive, please cross-check.
            </div>
            <table style="width:100%;font-size:0.78rem"><thead><tr>
                <th style="text-align:left">Bucket (parser artifact)</th>
                <th class="r">Amount (RM)</th><th class="r">Txns</th>
            </tr></thead><tbody>{rows}</tbody></table>
        </div>'''

    payers_suppressed_html = _render_suppressed(_payers_suppressed, 'credit')
    payees_suppressed_html = _render_suppressed(_payees_suppressed, 'debit')

    # v6.3.4: compact money formatter for inline bar labels (e.g. 162394 -> "162K", 1_245_000 -> "1.2M")
    def _fmt_compact(n):
        try:
            n = float(n or 0)
        except Exception:
            return '0'
        a = abs(n)
        if a >= 1_000_000:
            return f'{n/1_000_000:.1f}M'.replace('.0M', 'M')
        if a >= 1_000:
            return f'{n/1_000:.0f}K'
        return f'{n:.0f}'

    _MONTH_ABBR = {
        '01': 'Jan', '02': 'Feb', '03': 'Mar', '04': 'Apr', '05': 'May', '06': 'Jun',
        '07': 'Jul', '08': 'Aug', '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dec',
    }

    def _fmt_month_label(month_str):
        """Convert '2025-03' -> 'Mar', '2025-03-01' -> 'Mar'; fall back to raw if unparseable."""
        if not month_str:
            return ''
        parts = str(month_str).split('-')
        if len(parts) >= 2 and parts[1] in _MONTH_ABBR:
            return _MONTH_ABBR[parts[1]]
        return str(month_str)[-5:]

    def _render_monthly_bars(monthly_breakdown, color_var):
        """v6.3.4: bars + inline month/amount labels below. Always renders when data is present.
        Previously gated on a global has_monthly_bd flag — now per-party consistent across all banks.
        """
        if not monthly_breakdown:
            return ''
        mb_vals = [mb.get('amount', 0) for mb in monthly_breakdown]
        max_mb = max(mb_vals) if mb_vals and max(mb_vals) > 0 else 1
        bars = ''.join(
            f'<div title="{mb.get("month","")}: RM {mb.get("amount",0):,.0f}" '
            f'style="flex:1;background:{color_var};opacity:0.7;border-radius:2px;'
            f'min-width:4px;height:{max(2, int(mb.get("amount",0)/max_mb*28))}px"></div>'
            for mb in monthly_breakdown
        )
        # Inline label row: "Mar: 162K" per bar. Short form keeps labels readable at narrow widths.
        labels = ''.join(
            f'<span style="flex:1;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
            f'{_fmt_month_label(mb.get("month",""))}: {_fmt_compact(mb.get("amount",0))}'
            f'</span>'
            for mb in monthly_breakdown
        )
        return (
            f'<div style="display:flex;align-items:flex-end;gap:2px;height:30px;margin-top:4px">{bars}</div>'
            f'<div style="display:flex;gap:2px;font-size:0.65rem;color:var(--text-muted);margin-top:2px">{labels}</div>'
        )

    payer_rows = ""
    for p in _payers:
        rp_badge = '<span class="rp-badge">RP</span>' if p.get('is_related_party') else ''
        mb_html = _render_monthly_bars(p.get('monthly_breakdown'), 'var(--green)')
        payer_rows += f'''<tr>
            <td>{p.get('rank')}</td>
            <td>{p.get('party_name','')} {rp_badge}{mb_html}</td>
            <td class="mono r credit">RM {p.get('total_amount',0):,.2f}</td>
            <td class="mono r">{p.get('transaction_count',0)}</td>
        </tr>'''

    payee_rows = ""
    for p in _payees:
        rp_badge = '<span class="rp-badge">RP</span>' if p.get('is_related_party') else ''
        mb_html = _render_monthly_bars(p.get('monthly_breakdown'), 'var(--red)')
        payee_rows += f'''<tr>
            <td>{p.get('rank')}</td>
            <td>{p.get('party_name','')} {rp_badge}{mb_html}</td>
            <td class="mono r debit">RM {p.get('total_amount',0):,.2f}</td>
            <td class="mono r">{p.get('transaction_count',0)}</td>
        </tr>'''


        # ── Large transactions (both credits and debits above threshold) ──
    # Get threshold from consolidated or report_info
    large_threshold = consol.get('high_value_threshold', 100000)
    if large_threshold is None or large_threshold == 0:
        large_threshold = 100000
    
    # Debug: Print to console for troubleshooting
    print(f"[DEBUG] Large threshold: {large_threshold}")
    
    # Get large transactions data - support multiple formats
    large_txns = data.get('large_transactions', [])
    
    # If large_transactions is empty, try to build it from transactions
    if (not large_txns or len(large_txns) == 0) and data.get('transactions'):
        # Use the external build_large_transactions function
        try:
            large_txns = build_large_transactions(data.get('transactions', []), large_threshold)
            print(f"[DEBUG] Built {len(large_txns)} large transactions from transactions")
        except Exception as e:
            print(f"[DEBUG] Error building large transactions: {e}")
            large_txns = []
    
    # Also check for large_credits as fallback
    large_credits = data.get('large_credits', [])
    if (not large_txns or len(large_txns) == 0) and large_credits:
        # Convert old format to new format
        large_txns = []
        for t in large_credits:
            if isinstance(t, dict):
                large_txns.append({
                    'date': t.get('date', ''),
                    'description': t.get('description', ''),
                    'amount': t.get('amount', 0),
                    'balance': t.get('balance', 0),
                    'type': 'CREDIT'
                })
        print(f"[DEBUG] Converted {len(large_txns)} from large_credits")
    
    print(f"[DEBUG] Final large_txns count: {len(large_txns)}")
    
    # Build the large transaction rows HTML as a proper table
    large_txn_rows = ""
    if large_txns and isinstance(large_txns, list) and len(large_txns) > 0:
        for t in large_txns:
            if not isinstance(t, dict):
                continue
            txn_type = t.get('type', 'CREDIT')
            type_cls = 'credit' if txn_type == 'CREDIT' else 'debit'
            amount = t.get('amount', 0)
            if amount is None:
                amount = 0
            balance = t.get('balance', 0)
            if balance is None:
                balance = 0
            date_str = str(t.get('date', ''))
            desc_str = str(t.get('description', ''))[:70]
            # Ensure we're building proper table rows
            large_txn_rows += f'''
                <tr>
                    <td>{date_str}</td>
                    <td>{desc_str}</td>
                    <td class="mono r {type_cls}">RM {float(amount):,.2f} ({txn_type})</td>
                    <td class="mono r">RM {float(balance):,.2f}</td>
                </tr>'''
    else:
        # Provide a meaningful message when no transactions are found
        large_txn_rows = f'''
                <tr>
                    <td colspan="4" class="note">No transactions above RM {large_threshold:,.0f}</td>
                </tr>'''
    
    # Also ensure the round_figure_credits section has proper table structure
    round_figure_credits = data.get('round_figure_credits', []) or []
    rf_cr_rows = ""
    if round_figure_credits and isinstance(round_figure_credits, list):
        for t in round_figure_credits:
            if not isinstance(t, dict):
                continue
            rf_cr_rows += f'''
                <tr>
                    <td>{t.get('date', '')}</td>
                    <td>{t.get('description', '')[:70]}</td>
                    <td class="mono r credit">RM {t.get('amount', 0):,.2f}</td>
                    <td class="mono r">RM {t.get('balance', 0):,.2f}</td>
                </tr>'''
    if not rf_cr_rows:
        rf_cr_rows = '<tr><td colspan="4" class="note">No round-figure credits detected.</td></tr>'

    # ── Related party transactions ──
    rp_summary = own_related.get('summary', {}) or {}
    # Derive counts from transactions when summary fields are missing/zero
    _rp_txns_all = own_related.get('transactions', []) or []
    def _count_txn(party_type_prefix, txn_type):
        c = 0
        for _t in _rp_txns_all:
            if not isinstance(_t, dict):
                continue
            pt = (_t.get('party_type') or '').upper()
            tt = (_t.get('type') or '').upper()
            if pt.startswith(party_type_prefix) and tt == txn_type:
                c += 1
        return c
    rp_counts = {
        'own_party_cr': int(rp_summary.get('own_party_cr_count') or 0) or _count_txn('OWN', 'CREDIT'),
        'own_party_dr': int(rp_summary.get('own_party_dr_count') or 0) or _count_txn('OWN', 'DEBIT'),
        'related_party_cr': int(rp_summary.get('related_party_cr_count') or 0) or _count_txn('RELATED', 'CREDIT'),
        'related_party_dr': int(rp_summary.get('related_party_dr_count') or 0) or _count_txn('RELATED', 'DEBIT'),
    }
    rp_txn_rows = ""
    for t in own_related.get('transactions', [])[:50]:
        type_cls = 'credit' if t.get('type') == 'CREDIT' else 'debit'
        rp_txn_rows += f'''<tr>
            <td>{t.get('date','')}</td>
            <td>{t.get('description','')[:55]}</td>
            <td class="mono r {type_cls}">RM {t.get('amount',0):,.2f}</td>
            <td><span class="badge badge-{t.get('type','').lower()}">{t.get('type','')}</span></td>
            <td>{t.get('party_type','')}</td>
            <td>{t.get('party_name','')}</td>
        </tr>'''
    rp_total = len(own_related.get('transactions', []))
    rp_note = f'<div class="note">Showing first 50 of {rp_total} transactions</div>' if rp_total > 50 else ''

    # ── v6.3.3: Counterparty Ledger ──
    counterparty_ledger_html = ''
    cp_ledger = data.get('counterparty_ledger')
    if cp_ledger:
        rp_name_set = set()
        for _rp in related_parties:
            if isinstance(_rp, dict):
                _nm = _rp.get('name') or _rp.get('party_name') or ''
            else:
                _nm = str(_rp)
            if _nm:
                rp_name_set.add(_nm.strip().upper())

        cleaning_status = cp_ledger.get('ledger_cleaning_status', '')
        cleaning_stats = cp_ledger.get('cleaning_stats', {}) or {}
        total_cp = cp_ledger.get('total_counterparties', 0)
        merges = cleaning_stats.get('merges_performed', 0)
        purpose_strips = cleaning_stats.get('purpose_strips', 0)
        original_cp = cleaning_stats.get('original_counterparties', 0)

        # v6.3.5: counterparty_ledger.extraction_stats — shape-polymorphic.
        # Single-bank engine emits {pattern_matched, special_bucket, raw_fallback, total_transactions};
        # multi-bank consolidated emits {merged_from_banks: [...]}.
        ext_stats = cp_ledger.get('extraction_stats') if isinstance(cp_ledger.get('extraction_stats'), dict) else {}
        merged_banks = ext_stats.get('merged_from_banks') if isinstance(ext_stats.get('merged_from_banks'), list) else None
        ext_pattern = ext_stats.get('pattern_matched')
        ext_bucket = ext_stats.get('special_bucket')
        ext_raw = ext_stats.get('raw_fallback')
        ext_total = ext_stats.get('total_transactions')

        status_color = {'CLEANED': 'green', 'VALIDATION_FAILED': 'amber', 'SKIPPED': 'amber'}.get(cleaning_status, 'text-muted')
        status_badge = f'<span class="badge" style="background:var(--{status_color}-dim);color:var(--{status_color})">{cleaning_status or "N/A"}</span>' if cleaning_status else ''

        val_fail_warning = ''
        if cleaning_status == 'VALIDATION_FAILED':
            val_fail_warning = '<div style="background:var(--amber-dim);border:1px solid var(--amber);color:var(--amber);margin:0.75rem 0;padding:0.75rem;border-radius:8px;display:flex;gap:0.5rem;align-items:center"><div>⚠️</div><div><div style="font-weight:600">Counterparty ledger cleaning failed validation</div><div style="font-size:0.85rem">Showing original parser output.</div></div></div>'

        counterparties = cp_ledger.get('counterparties', []) or []
        counterparties_sorted = sorted(
            counterparties,
            key=lambda c: (c.get('total_credits', 0) or 0) + (c.get('total_debits', 0) or 0),
            reverse=True,
        )

        cp_rows_html = ''
        for idx, cp in enumerate(counterparties_sorted):
            name = cp.get('counterparty_name', '') or ''
            credits = cp.get('total_credits', 0) or 0
            debits = cp.get('total_debits', 0) or 0
            net = cp.get('net_position', 0) or 0
            txn_count = cp.get('transaction_count', 0) or 0
            cr_count = cp.get('credit_count', 0) or 0
            dr_count = cp.get('debit_count', 0) or 0
            net_cls = 'credit' if net >= 0 else 'debit'
            rp_badge = '<span class="rp-badge">RP</span>' if name.strip().upper() in rp_name_set else ''

            sub_rows = ''
            for t in cp.get('transactions', []) or []:
                t_type = t.get('type', '')
                t_cls = 'credit' if t_type == 'CREDIT' else 'debit'
                sub_rows += f'''<tr>
                    <td>{t.get('date','')}</td>
                    <td>{(t.get('description','') or '')[:80]}</td>
                    <td class="mono r {t_cls}">RM {t.get('amount',0):,.2f}</td>
                    <td><span class="badge badge-{t_type.lower()}">{t_type}</span></td>
                    <td class="mono r">{t.get('balance',0):,.2f}</td>
                </tr>'''

            cp_rows_html += f'''
            <tr class="cp-row" onclick="toggleCp('cp-detail-{idx}')" style="cursor:pointer">
                <td><span id="cp-caret-{idx}">▶</span> {name} {rp_badge}</td>
                <td class="mono r credit">{credits:,.2f} <span style="color:var(--text-muted);font-size:0.75rem">({cr_count})</span></td>
                <td class="mono r debit">{debits:,.2f} <span style="color:var(--text-muted);font-size:0.75rem">({dr_count})</span></td>
                <td class="mono r {net_cls}" style="font-weight:600">{net:,.2f}</td>
                <td class="mono r">{txn_count}</td>
            </tr>
            <tr id="cp-detail-{idx}" style="display:none"><td colspan="5" style="background:var(--bg);padding:0">
                <div class="table-wrap"><table style="margin:0">
                    <thead><tr><th>Date</th><th>Description</th><th class="r">Amount</th><th>Type</th><th class="r">Balance</th></tr></thead>
                    <tbody>{sub_rows or '<tr><td colspan="5" class="note">No transactions</td></tr>'}</tbody>
                </table></div>
            </td></tr>'''

        counterparty_ledger_html = f'''
            <div class="section">
                <div class="section-head">
                    <h2>Counterparty Ledger</h2>
                    {status_badge}
                </div>
                <div class="section-body">
                    {val_fail_warning}
                    <div class="summary-grid">
                        <div class="summary-card"><div class="val">{total_cp:,}</div><div class="lbl">Total Counterparties</div></div>
                        {('<div class="summary-card"><div class="val">' + f'{original_cp:,}' + '</div><div class="lbl">Original (pre-clean)</div></div>') if original_cp else ''}
                        {('<div class="summary-card"><div class="val">' + f'{merges:,}' + '</div><div class="lbl">Merges Performed</div></div>') if merges else ''}
                        {('<div class="summary-card"><div class="val">' + f'{purpose_strips:,}' + '</div><div class="lbl">Purpose Strips</div></div>') if purpose_strips else ''}
                        {('<div class="summary-card"><div class="val" style="font-size:1.05rem">' + ', '.join(merged_banks) + '</div><div class="lbl">Merged from banks</div></div>') if merged_banks else ''}
                        {('<div class="summary-card"><div class="val">' + f'{int(ext_pattern):,}' + '</div><div class="lbl">Pattern matched</div></div>') if isinstance(ext_pattern, (int, float)) and ext_pattern else ''}
                        {('<div class="summary-card"><div class="val">' + f'{int(ext_bucket):,}' + '</div><div class="lbl">Special bucket</div></div>') if isinstance(ext_bucket, (int, float)) and ext_bucket else ''}
                        {('<div class="summary-card"><div class="val">' + f'{int(ext_raw):,}' + '</div><div class="lbl">Raw fallback</div></div>') if isinstance(ext_raw, (int, float)) and ext_raw else ''}
                    </div>
                    <div style="margin:0.5rem 0">
                        <input type="text" id="cp-search" placeholder="Filter counterparties..." onkeyup="filterCp()" style="width:100%;padding:0.5rem;border:1px solid var(--border);border-radius:4px;font-size:0.9rem">
                    </div>
                    <div class="table-wrap" style="max-height:600px;overflow:auto"><table id="cp-table">
                        <thead><tr><th>Counterparty</th><th class="r">Credits (RM)</th><th class="r">Debits (RM)</th><th class="r">Net Position</th><th class="r">Txns</th></tr></thead>
                        <tbody>{cp_rows_html or '<tr><td colspan="5" class="note">No counterparties</td></tr>'}</tbody>
                    </table></div>
                </div>
            </div>
            <script>
                function toggleCp(id) {{
                    var row = document.getElementById(id);
                    if (!row) return;
                    var caret = document.getElementById(id.replace('detail','caret'));
                    if (row.style.display === 'none') {{ row.style.display = ''; if (caret) caret.textContent = '▼'; }}
                    else {{ row.style.display = 'none'; if (caret) caret.textContent = '▶'; }}
                }}
                function filterCp() {{
                    var q = document.getElementById('cp-search').value.toLowerCase();
                    var rows = document.querySelectorAll('#cp-table tbody tr.cp-row');
                    rows.forEach(function(r) {{
                        var txt = r.textContent.toLowerCase();
                        var detail = document.getElementById(r.getAttribute('onclick').match(/cp-detail-\\d+/)[0]);
                        if (!q || txt.indexOf(q) >= 0) {{ r.style.display = ''; }}
                        else {{ r.style.display = 'none'; if (detail) detail.style.display = 'none'; }}
                    }});
                }}
            </script>'''

    # ── v6.3.1: Statutory Compliance ──
    statutory_html = ''
    stat_comp = consol.get('statutory_compliance')
    if stat_comp:
        overall = stat_comp.get('overall_status', 'N/A')
        overall_color = {'COMPLIANT': 'green', 'GAPS_DETECTED': 'amber', 'CRITICAL': 'red'}.get(overall, 'amber')

        def _cov_bar(label, paid, total, missing, paid_list=None, salary_list=None):
            # v6.3.3.2 defensive fix: coverage must be bounded [0, 100] and use set intersection
            # when paid_list and salary_list are available. Raw paid/total produces >100% when
            # statutory pays in a non-payroll month (MYTUTOR LHDN 120% bug).
            if not total:
                return f'<div class="summary-card"><div class="val">N/A</div><div class="lbl">{label}</div></div>'
            if paid_list is not None and salary_list is not None and salary_list:
                covered = len(set(paid_list) & set(salary_list))
                display_paid = covered
                display_total = len(salary_list)
            else:
                display_total = total
                display_paid = min(paid, total)  # cap fraction defensively
            pct = (display_paid / display_total * 100) if display_total else 0
            pct = max(0.0, min(pct, 100.0))  # hard cap [0, 100]
            bar_color = 'green' if pct >= 99.5 else ('amber' if pct >= 50 else 'red')
            miss_str = f'<div style="font-size:0.7rem;color:var(--amber);margin-top:0.25rem">Missing: {", ".join(missing[:6])}{"..." if len(missing) > 6 else ""}</div>' if missing else ''
            return f'''<div class="summary-card">
                <div class="val">{display_paid}/{display_total}</div>
                <div class="lbl">{label} ({pct:.0f}%)</div>
                <div style="height:6px;background:var(--border);border-radius:3px;margin-top:0.4rem;overflow:hidden"><div style="width:{pct:.1f}%;height:100%;background:var(--{bar_color})"></div></div>
                {miss_str}
            </div>'''

        def _as_int(v):
            if isinstance(v, list):
                return len(v)
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        # v6.3.3.2: prefer lists where available so we can intersect (covered ∩ salary)
        salary_list = stat_comp.get('salary_months_list') or []
        salary_months = _as_int(stat_comp.get('salary_months_active', 0)) or len(salary_list)
        epf_list = stat_comp.get('epf_months_list') or []
        epf_paid = _as_int(stat_comp.get('epf_months_paid', 0)) or len(epf_list)
        epf_missing = stat_comp.get('epf_months_missing', []) or []
        socso_list = stat_comp.get('socso_months_list') or []
        socso_paid = _as_int(stat_comp.get('socso_months_paid', 0)) or len(socso_list)
        socso_missing = stat_comp.get('socso_months_missing', []) or []
        lhdn_det = stat_comp.get('lhdn_detected', False)
        lhdn_list = stat_comp.get('lhdn_months_list') or []  # may not be in older JSON
        lhdn_paid = _as_int(stat_comp.get('lhdn_months_paid', 0)) or len(lhdn_list)
        hrdf_det = stat_comp.get('hrdf_detected', False)
        hrdf_list = stat_comp.get('hrdf_months_list') or []  # may not be in older JSON
        hrdf_paid = _as_int(stat_comp.get('hrdf_months_paid', 0)) or len(hrdf_list)

        cov_cards = _cov_bar('EPF Coverage', epf_paid, salary_months, epf_missing, epf_list, salary_list)
        cov_cards += _cov_bar('SOCSO Coverage', socso_paid, salary_months, socso_missing, socso_list, salary_list)

        # v6.3.3.2: LHDN and HRDF decoupled from salary-months coverage.
        # Reason: LHDN bucket includes CP204 (corporate tax), SST, stamp duty,
        # etc. in addition to PCB/MTD (salary withholding) — so a paid/salary
        # ratio mixes unrelated payment types (this was the source of the
        # 120% display on MYTUTOR). HRDF is genuinely exempt for small employers
        # so a coverage % is also misleading.
        # Show as informational count + total amount instead.
        total_lhdn = float(consol.get('total_statutory_tax', 0) or 0)
        total_hrdf = float(consol.get('total_statutory_hrdf', 0) or 0)

        def _info_card(label, n_months, total_amt, detected, tooltip=''):
            if not detected and n_months == 0 and total_amt == 0:
                return f'<div class="summary-card" title="{tooltip}"><div class="val" style="color:var(--text-muted)">Not detected</div><div class="lbl">{label}</div></div>'
            amt_str = f"RM {total_amt:,.0f}" if total_amt else ""
            return f'''<div class="summary-card" title="{tooltip}">
                <div class="val">{n_months}</div>
                <div class="lbl">{label} (months paid)</div>
                <div style="font-size:0.75rem;color:var(--text-soft);margin-top:0.2rem">{amt_str}</div>
            </div>'''

        cov_cards += _info_card(
            'LHDN', lhdn_paid, total_lhdn, lhdn_det,
            tooltip='Income tax bucket — includes PCB/MTD salary withholding AND CP204 corporate tax AND SST. Shown as count because payment timing is not strictly payroll-driven.',
        )
        cov_cards += _info_card(
            'HRDF', hrdf_paid, total_hrdf, hrdf_det,
            tooltip='HRDF is exempt for small employers (less than 10 employees in certain industries). Shown as count — absence does not necessarily indicate non-compliance.',
        )

        def _ratio_rows(rows, is_epf=True):
            html = ''
            for row in rows or []:
                if not isinstance(row, dict):
                    html += f'<tr><td class="mono" colspan="5">{row}</td></tr>'
                    continue
                st = row.get('status', 'N/A')
                st_color = {'OK': 'green', 'WARNING': 'amber', 'CATCH_UP': 'red'}.get(st, 'text-muted')
                amt = row.get('epf_amount' if is_epf else 'socso_amount', 0) or 0
                try:
                    amt = float(amt)
                except (TypeError, ValueError):
                    amt = 0.0
                try:
                    sal = float(row.get('salary_amount', 0) or 0)
                except (TypeError, ValueError):
                    sal = 0.0
                try:
                    ratio = float(row.get('ratio_pct', 0) or 0)
                except (TypeError, ValueError):
                    ratio = 0.0
                html += f'''<tr>
                    <td class="mono">{row.get('month','')}</td>
                    <td class="mono r">{amt:,.2f}</td>
                    <td class="mono r">{sal:,.2f}</td>
                    <td class="mono r">{ratio:.2f}%</td>
                    <td style="text-align:center"><span style="color:var(--{st_color});font-weight:600">{st}</span></td>
                </tr>'''
            return html or '<tr><td colspan="5" class="note">No data</td></tr>'

        epf_ratio_rows_html = _ratio_rows(stat_comp.get('epf_per_month_ratios', []), True)
        socso_ratio_rows_html = _ratio_rows(stat_comp.get('socso_per_month_ratios', []), False)

        # v6.3.5: employer-footprint checks (sub-threshold + channel-blind)
        sub_thr = stat_comp.get('subthreshold_employer') if isinstance(stat_comp.get('subthreshold_employer'), dict) else None
        ch_blind = stat_comp.get('channel_blind_employer') if isinstance(stat_comp.get('channel_blind_employer'), dict) else None
        footprint_html = ''
        if sub_thr or ch_blind:
            blocks = ''
            if sub_thr:
                is_sub = bool(sub_thr.get('is_subthreshold'))
                sal_amt = float(sub_thr.get('total_salary_amount', 0) or 0)
                thr_amt = float(sub_thr.get('threshold_amount', 0) or 0)
                reason = sub_thr.get('reason', '') or ''
                badge_color = 'amber' if is_sub else 'green'
                badge_txt = 'SUB-THRESHOLD' if is_sub else 'OK'
                blocks += f'''
                    <div class="summary-card" style="text-align:left">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem">
                            <div class="lbl" style="font-weight:600;text-transform:none;font-size:0.85rem">Sub-threshold employer check</div>
                            <span class="badge" style="background:var(--{badge_color}-dim);color:var(--{badge_color})">{badge_txt}</span>
                        </div>
                        <div style="font-size:0.8rem;color:var(--text-soft)">Salary detected: RM {sal_amt:,.2f} &middot; Threshold: RM {thr_amt:,.0f}</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.4rem;line-height:1.4">{reason}</div>
                    </div>'''
            if ch_blind:
                is_ch = bool(ch_blind.get('is_channel_blind'))
                chq_amt = float(ch_blind.get('cheque_dr_amount', 0) or 0)
                gross_dr = float(ch_blind.get('gross_dr_amount', 0) or 0)
                ratio_pct = float(ch_blind.get('cheque_dr_ratio', 0) or 0) * 100.0
                thr_amt = float(ch_blind.get('threshold_amount', 0) or 0)
                thr_ratio = float(ch_blind.get('threshold_ratio', 0) or 0) * 100.0
                reason = ch_blind.get('reason', '') or ''
                badge_color = 'amber' if is_ch else 'green'
                badge_txt = 'CHANNEL-BLIND' if is_ch else 'OK'
                blocks += f'''
                    <div class="summary-card" style="text-align:left">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.4rem">
                            <div class="lbl" style="font-weight:600;text-transform:none;font-size:0.85rem">Channel-blind employer check</div>
                            <span class="badge" style="background:var(--{badge_color}-dim);color:var(--{badge_color})">{badge_txt}</span>
                        </div>
                        <div style="font-size:0.8rem;color:var(--text-soft)">Cheque DR: RM {chq_amt:,.2f} ({ratio_pct:.1f}% of gross DR RM {gross_dr:,.2f})</div>
                        <div style="font-size:0.8rem;color:var(--text-soft)">Thresholds: &ge; RM {thr_amt:,.0f} AND &ge; {thr_ratio:.0f}%</div>
                        <div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.4rem;line-height:1.4">{reason}</div>
                    </div>'''
            footprint_html = f'''
                    <h3 style="font-size:0.95rem;margin:1.25rem 0 0.5rem">Employer Footprint Checks</h3>
                    <div class="summary-grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">{blocks}</div>'''

        statutory_html = f'''
            <div class="section">
                <div class="section-head">
                    <h2>Statutory Compliance</h2>
                    <span class="badge" style="background:var(--{overall_color}-dim);color:var(--{overall_color});font-weight:700">{overall}</span>
                </div>
                <div class="section-body">
                    <div class="summary-grid">{cov_cards}</div>
                    <div class="two-col" style="margin-top:1rem">
                        <div>
                            <h3 style="font-size:0.95rem;margin:0 0 0.5rem">EPF Monthly Ratios (target 8&ndash;16%)</h3>
                            <div class="table-wrap" style="max-height:320px;overflow:auto"><table>
                                <thead><tr><th>Month</th><th class="r">EPF</th><th class="r">Salary</th><th class="r">Ratio</th><th style="text-align:center">Status</th></tr></thead>
                                <tbody>{epf_ratio_rows_html}</tbody>
                            </table></div>
                        </div>
                        <div>
                            <h3 style="font-size:0.95rem;margin:0 0 0.5rem">SOCSO Monthly Ratios (target 1&ndash;5%)</h3>
                            <div class="table-wrap" style="max-height:320px;overflow:auto"><table>
                                <thead><tr><th>Month</th><th class="r">SOCSO</th><th class="r">Salary</th><th class="r">Ratio</th><th style="text-align:center">Status</th></tr></thead>
                                <tbody>{socso_ratio_rows_html}</tbody>
                            </table></div>
                        </div>
                    </div>{footprint_html}
                </div>
            </div>'''

    # ── Loan transactions ──
    loan_disb_rows = ""
    for t in loans.get('disbursements', []):
        loan_disb_rows += f'''<tr>
            <td>{t.get('date','')}</td><td>{t.get('description','')[:55]}</td>
            <td class="mono r credit">RM {t.get('amount',0):,.2f}</td>
            <td>{t.get('category','')}</td>
        </tr>'''
    loan_repay_rows = ""
    for t in loans.get('repayments', []):
        loan_repay_rows += f'''<tr>
            <td>{t.get('date','')}</td><td>{t.get('description','')[:55]}</td>
            <td class="mono r debit">RM {t.get('amount',0):,.2f}</td>
            <td>{t.get('category','')}</td>
        </tr>'''

    # ── Flags ──
    flag_rows = ""
    detected_count = 0
    for f in flags_data.get('indicators', []):
        detected = f.get('detected', False)
        if detected:
            detected_count += 1
        status_cls = 'flag-yes' if detected else 'flag-no'
        flag_rows += f'''<tr class="{status_cls}">
            <td class="mono">{f.get('id','')}</td>
            <td>{f.get('name','')}</td>
            <td class="mono" style="text-align:center"><span class="flag-dot {'detected' if detected else 'clear'}"></span> {'YES' if detected else 'NO'}</td>
            <td>{f.get('remarks','')}</td>
        </tr>'''

    # ── Observations ──
    pos_obs = "".join([f'<li class="obs-item positive">{o}</li>' for o in obs.get('positive', [])])
    con_obs_items = obs.get('concerns', [])
    con_obs = "".join([
        f'<li class="obs-item {"data-warn" if "DATA QUALITY" in o or "INCOMPLETE" in o.upper() or "extraction gap" in o.lower() else "concern"}">{o}</li>'
        for o in con_obs_items
    ])

    # ── Chart data (JSON for Plotly) — use aggregated monthly if per-account ──
    # v6.2.0: also build FX chart data
    fx_chart_cr = []
    fx_chart_dr = []

    if chart_agg:
        chart_months = json.dumps(list(chart_agg.keys()))
        chart_net_cr = json.dumps([round(a['net_credits'], 2) for a in chart_agg.values()])
        chart_net_dr = json.dumps([round(a['net_debits'], 2) for a in chart_agg.values()])
        chart_eod_avg = json.dumps([round(a['eod_average'], 2) for a in chart_agg.values()])
        chart_eod_low = json.dumps([round(a['eod_lowest'], 2) for a in chart_agg.values()])
        chart_eod_high = json.dumps([round(a['eod_highest'], 2) for a in chart_agg.values()])
        if is_v620:
            for mo, rows in monthly_by_month.items():
                fx_chart_cr.append(sum(r.get('fx_credit_amount', 0) or 0 for r in rows))
                fx_chart_dr.append(sum(r.get('fx_debit_amount', 0) or 0 for r in rows))
    else:
        chart_months = json.dumps([m.get('month', '') for m in monthly])
        chart_net_cr = json.dumps([m.get('net_credits', 0) for m in monthly])
        chart_net_dr = json.dumps([m.get('net_debits', 0) for m in monthly])
        chart_eod_avg = json.dumps([m.get('eod_average', 0) for m in monthly])
        chart_eod_low = json.dumps([m.get('eod_lowest', 0) for m in monthly])
        chart_eod_high = json.dumps([m.get('eod_highest', 0) for m in monthly])
        if is_v620:
            fx_chart_cr = [m.get('fx_credit_amount', 0) for m in monthly]
            fx_chart_dr = [m.get('fx_debit_amount', 0) for m in monthly]

    fx_chart_cr_json = json.dumps(fx_chart_cr)
    fx_chart_dr_json = json.dumps(fx_chart_dr)

    # v6.2.0: Build FX tab HTML
    fx_tab_html = ''
    if is_v620:
        fx_currencies_all = consol.get('fx_currencies_all', [])
        fx_currencies_str = ', '.join(fx_currencies_all) if fx_currencies_all else 'None detected'
        fx_tab_html = f'''
        <div id="tab-fx" class="tab">
            <div class="info-panel">
                <h4>FX Classification Methodology</h4>
                <p>Transactions are classified as FX only when there is clear evidence of foreign currency conversion. Key rules:</p>
                <ul>
                    <li><strong>Default rule:</strong> NOT classified as FX unless clear conversion evidence exists</li>
                    <li><strong>TT CREDIT</strong> = Telegraphic Transfer (payment method), NOT a currency indicator</li>
                    <li><strong>RENTAS / JANM</strong> = Domestic MYR-to-MYR interbank transfers (Real-time Electronic Transfer of Funds and Securities)</li>
                    <li><strong>Voucher codes</strong> (GBPV, USDP) in reference fields = internal bank numbering, not currency denominations</li>
                    <li><strong>True FX requires:</strong> conversion rate, foreign currency amount, SWIFT codes, or foreign beneficiary with non-MYR amounts</li>
                </ul>
            </div>
            <div class="summary-grid">
                <div class="summary-card"><div class="val credit">{consol.get('total_fx_credits',0):,.0f}</div><div class="lbl">FX Credits (Total)</div></div>
                <div class="summary-card"><div class="val debit">{consol.get('total_fx_debits',0):,.0f}</div><div class="lbl">FX Debits (Total)</div></div>
                <div class="summary-card"><div class="val">{consol.get('fx_credit_pct',0):.1f}%</div><div class="lbl">FX Cr % of Gross</div></div>
                <div class="summary-card"><div class="val">{consol.get('fx_debit_pct',0):.1f}%</div><div class="lbl">FX Dr % of Gross</div></div>
                <div class="summary-card"><div class="val" style="font-size:1rem">{fx_currencies_str}</div><div class="lbl">Currencies Detected</div></div>
            </div>
            <div class="section">
                <div class="section-head"><h2>FX / Remittance Trend</h2></div>
                <div class="section-body">
                    <div id="chartFX" style="height:300px"></div>
                </div>
            </div>
            <div class="section">
                <div class="section-head"><h2>Monthly FX Breakdown</h2></div>
                <div class="section-body" style="padding:0">
                    <div class="table-wrap"><table>
                        <thead><tr><th>Month</th><th class="r">FX Cr Count</th><th class="r">FX Cr Amount</th><th class="r">FX Dr Count</th><th class="r">FX Dr Amount</th><th>Currencies</th></tr></thead>
                        <tbody>'''
        for mo, rows in monthly_by_month.items():
            fx_cc = sum(r.get('fx_credit_count', 0) or 0 for r in rows)
            fx_ca = sum(r.get('fx_credit_amount', 0) or 0 for r in rows)
            fx_dc = sum(r.get('fx_debit_count', 0) or 0 for r in rows)
            fx_da = sum(r.get('fx_debit_amount', 0) or 0 for r in rows)
            fx_cur = set()
            for r2 in rows:
                fx_cur.update(r2.get('fx_currencies', []))
            fx_tab_html += f'''<tr><td>{mo}</td><td class="mono r">{fx_cc}</td><td class="mono r credit">RM {fx_ca:,.2f}</td>
                <td class="mono r">{fx_dc}</td><td class="mono r debit">RM {fx_da:,.2f}</td>
                <td>{', '.join(sorted(fx_cur)) if fx_cur else '-'}</td></tr>'''
        fx_tab_html += '</tbody></table></div></div></div></div>'

    # v6.3.0: Build Unclassified Transactions tab HTML
    unclassified_tab_html = ''
    if is_v630:
        uncl_txns = data.get('unclassified_transactions', [])
        uncl_cr_total = consol.get('total_unclassified_cr', 0) or 0
        uncl_dr_total = consol.get('total_unclassified_dr', 0) or 0
        uncl_cr_count_total = sum((m.get('unclassified_cr_count', 0) or 0) for m in monthly)
        uncl_dr_count_total = sum((m.get('unclassified_dr_count', 0) or 0) for m in monthly)
        cls_config = data.get('classification_config', {})
        uncl_threshold = cls_config.get('unclassified_listing_threshold', 10000)

        # Monthly breakdown rows
        uncl_monthly_rows = ''
        for mo, rows in monthly_by_month.items():
            mo_uncl_cr_count = sum(r.get('unclassified_cr_count', 0) or 0 for r in rows)
            mo_uncl_cr_amt = sum(r.get('unclassified_cr_amount', 0) or 0 for r in rows)
            mo_uncl_dr_count = sum(r.get('unclassified_dr_count', 0) or 0 for r in rows)
            mo_uncl_dr_amt = sum(r.get('unclassified_dr_amount', 0) or 0 for r in rows)
            mo_net_cr = sum(r.get('net_credits', 0) or 0 for r in rows)
            pct_of_net = (mo_uncl_cr_amt / mo_net_cr * 100) if mo_net_cr > 0 else 0
            uncl_monthly_rows += f'''<tr>
                <td>{mo}</td>
                <td class="mono r">{mo_uncl_cr_count}</td>
                <td class="mono r credit">RM {mo_uncl_cr_amt:,.2f}</td>
                <td class="mono r">{mo_uncl_dr_count}</td>
                <td class="mono r debit">RM {mo_uncl_dr_amt:,.2f}</td>
                <td class="mono r">{pct_of_net:.1f}%</td>
            </tr>'''

        # Individual transactions rows
        uncl_txn_rows = ''
        for t in uncl_txns:
            type_cls = 'credit' if t.get('type') == 'CREDIT' else 'debit'
            uncl_txn_rows += f'''<tr>
                <td>{t.get('date','')}</td>
                <td>{t.get('description','')[:70]}</td>
                <td class="mono r {type_cls}">RM {t.get('amount',0):,.2f}</td>
                <td><span class="badge badge-{t.get('type','').lower()}">{t.get('type','')}</span></td>
                <td class="mono r">{t.get('balance',0):,.2f}</td>
            </tr>'''

        unclassified_tab_html = f'''
        <div id="tab-unclassified" class="tab">
            <div class="info-panel">
                <h4>What are Unclassified Transactions?</h4>
                <p>These are transactions whose descriptions are too vague or do not match any known classification rule.
                Unclassified credits <strong>remain in Net Credits</strong> and unclassified debits <strong>remain in Net Debits</strong> &mdash;
                they are NOT excluded. This section highlights them for analyst review.</p>
            </div>
            <div class="summary-grid">
                <div class="summary-card"><div class="val credit">RM {uncl_cr_total:,.0f}</div><div class="lbl">Unclassified Credits</div></div>
                <div class="summary-card"><div class="val debit">RM {uncl_dr_total:,.0f}</div><div class="lbl">Unclassified Debits</div></div>
                <div class="summary-card"><div class="val">{uncl_cr_count_total}</div><div class="lbl">Credit Txn Count</div></div>
                <div class="summary-card"><div class="val">{uncl_dr_count_total}</div><div class="lbl">Debit Txn Count</div></div>
            </div>
            <div class="section">
                <div class="section-head"><h2>Monthly Breakdown</h2></div>
                <div class="section-body" style="padding:0"><div class="table-wrap"><table>
                    <thead><tr><th>Month</th><th class="r">Uncl Cr #</th><th class="r">Uncl Cr Amt</th><th class="r">Uncl Dr #</th><th class="r">Uncl Dr Amt</th><th class="r">% of Net Cr</th></tr></thead>
                    <tbody>{uncl_monthly_rows}</tbody>
                </table></div></div>
            </div>
            {'<div class="section"><div class="section-head"><h2>Individual Unclassified Transactions (&ge; RM ' + f"{uncl_threshold:,.0f}" + ')</h2><span class="badge badge-current">' + str(len(uncl_txns)) + ' transactions</span></div><div class="section-body" style="padding:0"><div class="table-wrap" style="max-height:500px;overflow:auto"><table><thead><tr><th>Date</th><th>Description</th><th class="r">Amount</th><th>Type</th><th class="r">Balance</th></tr></thead><tbody>' + uncl_txn_rows + '</tbody></table></div></div></div>' if uncl_txns else ''}
        </div>'''

    # v6.2.0/v6.2.1: Build Parsing QC tab HTML
    parsing_tab_html = ''
    if has_parsing:
        success_rate = parsing.get('overall_success_rate', 0)
        rate_color = 'green' if success_rate >= 95 else 'amber' if success_rate >= 80 else 'red'
        # v6.2.1: Additional gap stats
        p_total_gaps = len(parsing.get('extraction_gaps', []) or [])
        p_missing_dr = consol.get('total_missing_debits', 0) or 0
        p_missing_cr = consol.get('total_missing_credits', 0) or 0
        gap_cards = ''
        if has_recon:
            gap_cards = f'''
                <div class="summary-card"><div class="val" style="color:var(--{'red' if p_total_gaps > 0 else 'green'})">{p_total_gaps}</div><div class="lbl">Extraction Gaps</div></div>
                <div class="summary-card"><div class="val" style="color:var(--{'red' if p_missing_dr > 0 else 'green'})">RM {p_missing_dr:,.0f}</div><div class="lbl">Missing Debits</div></div>
                <div class="summary-card"><div class="val" style="color:var(--{'red' if p_missing_cr > 0 else 'green'})">RM {p_missing_cr:,.0f}</div><div class="lbl">Missing Credits</div></div>'''
        parsing_tab_html = f'''
        <div id="tab-parsing" class="tab">
            {dq_banner_html}
            <div class="summary-grid">
                <div class="summary-card"><div class="val" style="color:var(--{rate_color})">{success_rate:.1f}%</div><div class="lbl">Success Rate</div></div>
                <div class="summary-card"><div class="val">{parsing.get('total_transactions_extracted',0):,}</div><div class="lbl">Txns Extracted</div></div>
                <div class="summary-card"><div class="val">{parsing.get('total_balance_checks_passed',0)}/{parsing.get('total_balance_checks',0)}</div><div class="lbl">Balance Checks Passed</div></div>
                {gap_cards}
            </div>
            <div class="section">
                <div class="section-head"><h2>Balance Reconciliation Detail</h2></div>
                <div class="section-body" style="padding:0"><div class="table-wrap"><table>
                    <thead><tr><th>Month</th><th>Account</th><th class="r">Opening Balance</th><th class="r">Gross Credits</th><th class="r">Gross Debits</th><th class="r">Expected Closing</th><th class="r">Actual Closing</th><th class="r">Delta</th><th style="text-align:center">Status</th><th class="r">Txns</th><th>Gaps</th><th>Notes</th></tr></thead>
                    <tbody>'''
        for chk in parsing.get('account_month_checks', []):
            passed = chk.get('passed', False)
            status_cls = 'flag-no' if passed else 'flag-yes'
            gap_count = chk.get('extraction_gaps', 0) or 0
            gap_cell = f'<span class="gap-pill">{gap_count}</span>' if gap_count > 0 else '—'
            parsing_tab_html += f'''<tr class="{status_cls}">
                <td>{chk.get('month','')}</td><td class="mono">{chk.get('account_number','')}</td>
                <td class="mono r">{chk.get('opening_balance',0):,.2f}</td>
                <td class="mono r credit">{chk.get('gross_credits',0):,.2f}</td>
                <td class="mono r debit">{chk.get('gross_debits',0):,.2f}</td>
                <td class="mono r">{chk.get('expected_closing',0):,.2f}</td>
                <td class="mono r">{chk.get('closing_balance',0):,.2f}</td>
                <td class="mono r" style="font-weight:600;color:var(--{'green' if passed else 'red'})">{chk.get('reconciliation_delta',0):,.2f}</td>
                <td style="text-align:center"><span class="flag-dot {'clear' if passed else 'detected'}"></span>{'PASS' if passed else 'FAIL'}</td>
                <td class="mono r">{chk.get('transactions_extracted',0)}</td>
                <td style="text-align:center">{gap_cell}</td>
                <td style="font-size:0.78rem;color:var(--text-muted)">{chk.get('notes','') or ''}</td>
            </tr>'''
        parsing_tab_html += '''</tbody></table></div></div></div>'''

        # v6.2.1: Extraction gap detail section within Parsing QC tab
        p_extraction_gaps = parsing.get('extraction_gaps', []) or []
        if p_extraction_gaps:
            parsing_tab_html += '''<div class="section"><div class="section-head"><h2 style="color:var(--red)">Extraction Gap Details</h2></div><div class="section-body" style="padding:0"><div class="table-wrap"><table>
                <thead><tr><th>Month</th><th>Date</th><th>Page</th><th>Source File</th><th>Missing</th><th class="r">Amount (RM)</th><th>Before Gap</th><th>After Gap</th></tr></thead><tbody>'''
            for g in p_extraction_gaps:
                parsing_tab_html += f'''<tr class="flag-yes">
                    <td>{g.get('month','')}</td><td>{g.get('date','')}</td><td>{g.get('page','')}</td>
                    <td style="font-size:0.78rem">{g.get('source_file','')}</td>
                    <td><span class="badge badge-debit">{g.get('missing_type','')}</span></td>
                    <td class="mono r" style="font-weight:700;color:var(--red)">{g.get('missing_amount',0):,.2f}</td>
                    <td style="font-size:0.78rem" title="{g.get('prev_description','')}">{g.get('prev_description','')[:40]}... (RM {g.get('balance_before_gap',0):,.2f})</td>
                    <td style="font-size:0.78rem" title="{g.get('next_description','')}">{g.get('next_description','')[:40]}... (RM {g.get('balance_after_gap',0):,.2f})</td>
                </tr>'''
            parsing_tab_html += '</tbody></table></div></div></div>'

        # Gap 4: Classification Config section
        cls_config = data.get('classification_config', {})
        if cls_config or schema_v:
            rulebook_ver = cls_config.get('rulebook_version', 'N/A')
            exec_mode = cls_config.get('execution_mode', 'N/A')
            large_cr_threshold = cls_config.get('large_credit_threshold', 100000)
            uncl_listing_threshold = cls_config.get('unclassified_listing_threshold', 10000)
            factoring_entities = cls_config.get('known_factoring_entities', [])
            factoring_str = ', '.join(factoring_entities) if factoring_entities else 'None configured'

            parsing_tab_html += f'''
            <div class="section">
                <div class="section-head"><h2>Classification Configuration</h2></div>
                <div class="section-body">
                    <div class="config-grid">
                        <div class="config-item"><span class="config-label">Schema Version</span><span class="config-val">{schema_v or 'N/A'}</span></div>
                        <div class="config-item"><span class="config-label">Rulebook Version</span><span class="config-val">{rulebook_ver}</span></div>
                        <div class="config-item"><span class="config-label">Execution Mode</span><span class="config-val">{exec_mode}</span></div>
                        <div class="config-item"><span class="config-label">Large Credit Threshold</span><span class="config-val">RM {large_cr_threshold:,.0f}</span></div>
                        <div class="config-item"><span class="config-label">Unclassified Listing Threshold</span><span class="config-val">RM {uncl_listing_threshold:,.0f}</span></div>
                        <div class="config-item" style="grid-column:1/-1"><span class="config-label">Known Factoring Entities</span><span class="config-val" style="font-size:0.8rem">{factoring_str}</span></div>
                    </div>
                </div>
            </div>'''

        # Gap 6: V1-V6 Formula Validation Checks
        gross_cr = consol.get('gross_credits', 0) or 0
        own_cr = consol.get('total_own_party_cr', 0) or 0
        rp_cr = consol.get('total_related_party_cr', 0) or 0
        rev_cr = consol.get('total_reversal_cr', 0) or 0
        loan_disb_cr = consol.get('total_loan_disbursement_cr', 0) or 0
        fd_int_cr = consol.get('total_fd_interest_cr', 0) or 0
        inward_ret_cr = consol.get('total_inward_return_cr', 0) or 0
        net_cr = consol.get('net_credits', 0) or 0
        expected_net_cr = gross_cr - own_cr - rp_cr - rev_cr - loan_disb_cr - fd_int_cr - inward_ret_cr
        v1_delta = abs(net_cr - expected_net_cr)
        v1_pass = v1_delta < 0.02

        gross_dr = consol.get('gross_debits', 0) or 0
        own_dr = consol.get('total_own_party_dr', 0) or 0
        net_dr = consol.get('net_debits', 0) or 0
        expected_net_dr = gross_dr - own_dr
        v2_delta = abs(net_dr - expected_net_dr)
        v2_pass = v2_delta < 0.02

        salary = consol.get('total_salary_paid', 0) or 0
        epf = consol.get('total_statutory_epf', 0) or 0
        socso = consol.get('total_statutory_socso', 0) or 0
        v3_ratio = (epf / salary * 100) if salary > 0 else 0
        v3_status = 'PASS' if 8 <= v3_ratio <= 16 else ('WARN' if salary > 0 else 'N/A')
        v4_ratio = (socso / salary * 100) if salary > 0 else 0
        v4_status = 'PASS' if 1 <= v4_ratio <= 5 else ('WARN' if salary > 0 else 'N/A')

        # V6: Sum of monthly net_credits = consolidated net_credits
        monthly_net_cr_sum = sum(
            sum(r.get('net_credits', 0) or 0 for r in rows)
            for rows in monthly_by_month.values()
        )
        v6_delta = abs(net_cr - monthly_net_cr_sum)
        v6_pass = v6_delta < 0.02

        def _v_cls(status):
            if status == 'PASS': return 'validation-pass'
            if status == 'WARN': return 'validation-warn'
            if status == 'FAIL': return 'validation-fail'
            return 'validation-pass'

        checks_data = [
            ('V1', 'Net Credits = Gross - Exclusions', 'BLOCKING', 'PASS' if v1_pass else 'FAIL', f'Delta: RM {v1_delta:,.2f}'),
            ('V2', 'Net Debits = Gross - Own Party (C02)', 'BLOCKING', 'PASS' if v2_pass else 'FAIL', f'Delta: RM {v2_delta:,.2f}'),
            ('V3', 'EPF/Salary ratio 8-16%', 'WARNING', v3_status, f'{v3_ratio:.1f}%' if salary > 0 else 'No salary detected'),
            ('V4', 'SOCSO/Salary ratio 1-5%', 'WARNING', v4_status, f'{v4_ratio:.1f}%' if salary > 0 else 'No salary detected'),
            ('V5', 'C02+C11 dual-tag exclusion', 'BLOCKING', 'PASS', 'Single deduction via C02'),
            ('V6', 'Monthly net_cr sum = consolidated', 'BLOCKING', 'PASS' if v6_pass else 'FAIL', f'Delta: RM {v6_delta:,.2f}'),
        ]
        v_rows = ''
        for vid, desc, severity, status, remark in checks_data:
            v_rows += f'''<tr>
                <td class="mono" style="font-weight:600">{vid}</td>
                <td>{desc}</td>
                <td><span class="badge" style="background:var(--{'red-dim' if severity=='BLOCKING' else 'amber-dim'});color:var(--{'red' if severity=='BLOCKING' else 'amber'})">{severity}</span></td>
                <td style="text-align:center"><span class="{_v_cls(status)}">{status}</span></td>
                <td style="font-size:0.82rem;color:var(--text-soft)">{remark}</td>
            </tr>'''

        parsing_tab_html += f'''
            <div class="section">
                <div class="section-head"><h2>Formula Validation Checks (V1&ndash;V6)</h2></div>
                <div class="section-body" style="padding:0"><div class="table-wrap"><table>
                    <thead><tr><th>ID</th><th>Check</th><th>Severity</th><th style="text-align:center">Status</th><th>Remarks</th></tr></thead>
                    <tbody>{v_rows}</tbody>
                </table></div></div>
            </div>'''

        parsing_tab_html += '</div>'

    def _pdf_detail_to_text(value) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, dict):
            return ", ".join(f"{k}: {v}" for k, v in value.items())
        if isinstance(value, list):
            return "; ".join(str(x) for x in value[:5])
        return str(value)

    def _pdf_finding_is_benign(finding: dict) -> bool:
        message = str(finding.get("message", "") or "").lower()
        benign_patterns = [
            "no anomalies detected",
            "verified",
            "matches known",
            "hashes computed",
            "pdf version",
            "font consistency",
        ]
        return any(pattern in message for pattern in benign_patterns)

    def _normalise_pdf_layer_rows(pdf_file: dict) -> list:
        """Normalise Railway analyze_pdf_batch output and legacy HTML shapes."""
        layer_order = [
            ("metadata", "Layer 1: Metadata"),
            ("fonts", "Layer 2: Fonts"),
            ("text_layers", "Layer 3: Text Layers"),
            ("visual", "Layer 4: Visual"),
            ("cross_validation", "Layer 5: Cross Validation"),
            ("bank_profile", "Layer 6: Bank Profile"),
            ("structural", "Layer 7: Structural"),
            ("arithmetic", "Layer 8: Arithmetic"),
        ]
        layer_results = pdf_file.get("layer_results")
        if isinstance(layer_results, dict):
            rows = []
            handled_keys = set()
            for layer_key, layer_label in layer_order:
                handled_keys.add(layer_key)
                findings = layer_results.get(layer_key, []) or []
                findings = findings if isinstance(findings, list) else []
                highest = next(
                    (
                        level
                        for level in ("HIGH", "MEDIUM", "LOW")
                        if any((finding.get("severity") or "").upper() == level for finding in findings if isinstance(finding, dict))
                    ),
                    "LOW",
                )
                anomaly_count = sum(
                    1
                    for finding in findings
                    if isinstance(finding, dict) and not _pdf_finding_is_benign(finding)
                )
                primary = findings[0] if findings and isinstance(findings[0], dict) else {}
                detail_text = _pdf_detail_to_text(primary.get("detail"))
                detail_parts = [f"{anomaly_count} anomalies detected"]
                if detail_text:
                    detail_parts.append(detail_text)
                rows.append(
                    {
                        "layer": layer_label,
                        "severity": highest,
                        "finding": primary.get("message") or "No findings.",
                        "detail": " | ".join(detail_parts),
                    }
                )

            for layer_key, findings in layer_results.items():
                if layer_key in handled_keys:
                    continue
                findings = findings if isinstance(findings, list) else []
                for finding in findings:
                    if not isinstance(finding, dict):
                        continue
                    rows.append(
                        {
                            "layer": str(layer_key),
                            "severity": (finding.get("severity") or "LOW").upper(),
                            "finding": finding.get("message") or finding.get("finding") or "",
                            "detail": _pdf_detail_to_text(finding.get("detail")),
                        }
                    )
            return rows

        legacy_layers = pdf_file.get("layers", pdf_file.get("checks", pdf_file.get("findings", [])))
        if isinstance(legacy_layers, list):
            return [
                {
                    "layer": layer.get("layer", layer.get("name", "")),
                    "severity": (layer.get("severity", "") or layer.get("risk", "") or "LOW").upper(),
                    "finding": layer.get("message", layer.get("finding", layer.get("description", ""))),
                    "detail": _pdf_detail_to_text(layer.get("detail", layer.get("details", ""))),
                }
                for layer in legacy_layers
                if isinstance(layer, dict)
            ]
        if isinstance(legacy_layers, dict):
            rows = []
            for layer_name, layer_data in legacy_layers.items():
                if isinstance(layer_data, dict):
                    rows.append(
                        {
                            "layer": layer_name,
                            "severity": (layer_data.get("severity", "") or layer_data.get("risk", "") or "LOW").upper(),
                            "finding": layer_data.get("message", layer_data.get("finding", layer_data.get("description", ""))),
                            "detail": _pdf_detail_to_text(layer_data.get("detail", layer_data.get("details", ""))),
                        }
                    )
                else:
                    rows.append({"layer": layer_name, "severity": "LOW", "finding": str(layer_data), "detail": ""})
            return rows
        return []

    # v6.3.0: Build Fraud Detector tab HTML
    # v6.3.4: ALWAYS build the tab — when pdf_integrity is missing from the analysis JSON,
    # render a clear placeholder so analysts see the tab every time (consistency across
    # customers / banks). Previously Muhafiz-style runs silently omitted the tab.
    fraud_tab_html = ''
    pdf_integrity = data.get('pdf_integrity')
    if not pdf_integrity:
        fraud_tab_html = '''
        <div id="tab-fraud" class="tab">
            <div class="dq-banner dq-fail" style="background:var(--amber-dim);border-color:var(--amber)">
                <div class="dq-icon">&#x26A0;&#xFE0F;</div>
                <div>
                    <div class="dq-title">PDF Integrity: NOT CAPTURED</div>
                    <div class="dq-detail">
                        This analysis run did not emit <code>pdf_integrity</code> data. The 8-layer
                        fraud detector (<code>pdf_fraud_detector.py</code>) is available in the parser
                        pipeline — re-run through the Streamlit app (<code>streamlit run app.py</code>)
                        to populate this section. No integrity assertion is made for the uploaded PDFs.
                    </div>
                </div>
            </div>
            <div class="summary-grid">
                <div class="summary-card"><div class="val">&#8212;</div><div class="lbl">PDFs Analyzed</div></div>
                <div class="summary-card"><div class="val">&#8212;</div><div class="lbl">Total Checks</div></div>
                <div class="summary-card"><div class="val">&#8212;</div><div class="lbl">HIGH Findings</div></div>
                <div class="summary-card"><div class="val">&#8212;</div><div class="lbl">MEDIUM Findings</div></div>
            </div>
        </div>'''
    elif pdf_integrity:
        # pdf_integrity can be a dict of filename->results or a list
        if isinstance(pdf_integrity, dict):
            pdf_files = pdf_integrity.get('files', [])
            if not pdf_files and not isinstance(pdf_integrity.get(next(iter(pdf_integrity), ''), {}), list):
                # It might be keyed by filename
                pdf_files_dict = {k: v for k, v in pdf_integrity.items() if isinstance(v, dict) and k != 'summary'}
                pdf_files = [{'filename': k, **v} for k, v in pdf_files_dict.items()]
            if not pdf_files:
                pdf_files = pdf_integrity.get('results', [])
        elif isinstance(pdf_integrity, list):
            pdf_files = pdf_integrity
        else:
            pdf_files = []

        # Determine overall risk
        all_severities = []
        total_checks = 0
        high_count = 0
        medium_count = 0
        for pf in pdf_files:
            for layer in _normalise_pdf_layer_rows(pf):
                sev = (layer.get('severity') or 'LOW').upper()
                all_severities.append(sev)
                total_checks += 1
                if sev == 'HIGH':
                    high_count += 1
                elif sev == 'MEDIUM':
                    medium_count += 1

        overall_risk = 'low'
        if high_count > 0:
            overall_risk = 'high'
        elif medium_count > 0:
            overall_risk = 'medium'

        overall_label = {'low': 'ALL CLEAR', 'medium': 'REVIEW NEEDED', 'high': 'HIGH RISK'}[overall_risk]
        overall_icon = {'low': '&#x1F6E1;', 'medium': '&#x26A0;&#xFE0F;', 'high': '&#x1F6A8;'}[overall_risk]

        # Per-file sections
        file_sections = ''
        for pf in pdf_files:
            fname = pf.get('filename', pf.get('file', 'Unknown'))
            file_risk = (pf.get('risk', pf.get('overall_risk', 'LOW'))).upper() if isinstance(pf.get('risk', pf.get('overall_risk', '')), str) else 'LOW'
            if file_risk not in ('LOW', 'MEDIUM', 'HIGH'):
                file_risk = 'LOW'
            risk_cls = file_risk.lower()

            layer_rows = ''
            for layer in _normalise_pdf_layer_rows(pf):
                l_name = escape(str(layer.get('layer', '')))
                l_sev = escape(str((layer.get('severity') or 'LOW').upper()))
                l_msg = escape(str(layer.get('finding', '')))
                l_detail = escape(str(layer.get('detail', '')))
                sev_cls = l_sev.lower()
                layer_rows += f'''<tr>
                    <td>{l_name}</td>
                    <td><span class="fraud-shield {sev_cls}" style="padding:1px 6px;font-size:0.72rem">{l_sev}</span></td>
                    <td>{l_msg}</td>
                    <td style="font-size:0.78rem;color:var(--text-soft)">{l_detail}</td>
                </tr>'''

            file_sections += f'''
            <div class="section">
                <div class="fraud-file-header" onclick="this.nextElementSibling.classList.toggle('collapsed')">
                    <span style="font-weight:600;flex:1">{fname}</span>
                    <span class="fraud-shield {risk_cls}">{file_risk}</span>
                </div>
                <div class="fraud-detail">
                    <div class="table-wrap"><table>
                        <thead><tr><th>Layer</th><th>Severity</th><th>Finding</th><th>Detail</th></tr></thead>
                        <tbody>{layer_rows or '<tr><td colspan="4" class="note">No findings</td></tr>'}</tbody>
                    </table></div>
                </div>
            </div>'''

        fraud_tab_html = f'''
        <div id="tab-fraud" class="tab">
            <div class="{'dq-banner dq-pass' if overall_risk == 'low' else 'dq-banner dq-fail'}">
                <div class="dq-icon">{overall_icon}</div>
                <div>
                    <div class="dq-title">PDF Integrity: {overall_label}</div>
                    <div class="dq-detail">{'All PDF files passed integrity checks. No signs of tampering detected.' if overall_risk == 'low' else f'{high_count} HIGH and {medium_count} MEDIUM findings detected across {len(pdf_files)} PDF file(s). Manual review recommended.'}</div>
                </div>
            </div>
            <div class="summary-grid">
                <div class="summary-card"><div class="val">{len(pdf_files)}</div><div class="lbl">PDFs Analyzed</div></div>
                <div class="summary-card"><div class="val">{total_checks}</div><div class="lbl">Total Checks</div></div>
                <div class="summary-card"><div class="val" style="color:var(--{'red' if high_count > 0 else 'green'})">{high_count}</div><div class="lbl">HIGH Findings</div></div>
                <div class="summary-card"><div class="val" style="color:var(--{'amber' if medium_count > 0 else 'green'})">{medium_count}</div><div class="lbl">MEDIUM Findings</div></div>
            </div>
            {file_sections}
        </div>'''

    # Count flags
    total_flags = len(flags_data.get('indicators', []))

    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kredit Lab — {company}</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.27.0/plotly.min.js"></script>
    <script>if(typeof Plotly==='undefined'){{document.write('<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"><\\/script>')}}</script>
    <style>
        :root, [data-theme="light"] {{
            --bg: #f5f6fa; --bg-alt: #ffffff; --card: #ffffff;
            --border: #e2e8f0; --border-accent: #cbd5e1;
            --green: #059669; --green-dim: rgba(5,150,105,0.08); --green-bg: #ecfdf5;
            --red: #dc2626; --red-dim: rgba(220,38,38,0.08); --red-bg: #fef2f2;
            --amber: #d97706; --amber-dim: rgba(217,119,6,0.08); --amber-bg: #fffbeb;
            --blue: #2563eb; --blue-dim: rgba(37,99,235,0.08);
            --purple: #7c3aed; --purple-dim: rgba(124,58,237,0.08);
            --text: #1e293b; --text-soft: #475569; --text-muted: #94a3b8;
            --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --shadow-lg: 0 4px 6px rgba(0,0,0,0.05), 0 10px 15px rgba(0,0,0,0.03);
        }}
        [data-theme="dark"] {{
            --bg: #0b0f19; --bg-alt: #111827; --card: #1a2235;
            --border: #1e2a42; --border-accent: #2d3f5f;
            --green: #34d399; --green-dim: rgba(52,211,153,0.12); --green-bg: rgba(5,150,105,0.15);
            --red: #f87171; --red-dim: rgba(248,113,113,0.12); --red-bg: rgba(220,38,38,0.15);
            --amber: #fbbf24; --amber-dim: rgba(251,191,36,0.12); --amber-bg: rgba(217,119,6,0.15);
            --blue: #60a5fa; --blue-dim: rgba(96,165,250,0.12);
            --purple: #a78bfa; --purple-dim: rgba(167,139,250,0.12);
            --text: #e2e8f0; --text-soft: #94a3b8; --text-muted: #64748b;
            --shadow: 0 1px 3px rgba(0,0,0,0.3); --shadow-lg: 0 4px 6px rgba(0,0,0,0.4);
        }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family:'Inter',system-ui,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; font-size:14px; }}
        .container {{ max-width:1440px; margin:0 auto; padding:1.5rem; }}

        /* Header */
        .header {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:2rem; margin-bottom:1.5rem; position:relative; overflow:hidden; box-shadow:var(--shadow-lg); }}
        .header::before {{ content:''; position:absolute; top:0; left:0; right:0; height:4px; background:linear-gradient(90deg,#0d9488,#0ea5e9,#6366f1); }}
        .header-grid {{ display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:1.5rem; }}
        .company-info h1 {{ font-size:1.6rem; font-weight:700; margin-bottom:0.25rem; }}
        .company-info .period {{ color:var(--text-soft); font-size:0.88rem; }}
        .schema-badge {{ display:inline-block; padding:0.2rem 0.6rem; background:var(--purple-dim); color:var(--purple); border-radius:20px; font-size:0.72rem; font-weight:600; margin-left:0.75rem; vertical-align:middle; }}
        .header-kpi {{ display:flex; gap:1.75rem; flex-wrap:wrap; }}
        .kpi {{ text-align:center; padding:0 1rem; border-left:2px solid var(--border); }}
        .kpi:first-child {{ border-left:none; }}
        .kpi .val {{ font-size:1.35rem; font-weight:700; font-family:'JetBrains Mono',monospace; }}
        .kpi .lbl {{ font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; }}
        .kpi .val.credit {{ color:var(--green); }}
        .kpi .val.debit {{ color:var(--red); }}

        /* Theme toggle */
        .theme-toggle {{ position:absolute; top:1rem; right:1rem; padding:0.4rem 0.75rem; border:1px solid var(--border); background:var(--bg-alt); color:var(--text-soft); border-radius:8px; cursor:pointer; font-size:0.8rem; }}
        .theme-toggle:hover {{ border-color:var(--border-accent); }}
        .report-actions {{ position:absolute; top:1rem; right:5.5rem; display:flex; gap:0.5rem; }}
        .excel-btn {{ padding:0.4rem 0.75rem; border:1px solid var(--border); background:var(--bg-alt); color:var(--text-soft); border-radius:8px; cursor:pointer; font-size:0.8rem; font-weight:500; }}
        .excel-btn:hover {{ border-color:var(--green); color:var(--green); }}
        .tab-export-bar {{ display:flex; justify-content:flex-end; margin:0 0 1rem; }}

        /* Nav */
        .nav {{ display:flex; gap:0.35rem; margin-bottom:1.5rem; flex-wrap:wrap; background:var(--card); padding:0.4rem; border-radius:12px; border:1px solid var(--border); box-shadow:var(--shadow); }}
        .nav-btn {{ padding:0.6rem 1rem; border:none; background:transparent; color:var(--text-soft); cursor:pointer; border-radius:8px; font-size:0.82rem; font-weight:500; transition:all 0.15s; white-space:nowrap; }}
        .nav-btn:hover {{ background:var(--bg); color:var(--text); }}
        .nav-btn.active {{ background:var(--blue); color:#fff; }}

        /* Tab content */
        .tab {{ display:none; }}
        .tab.active {{ display:block; }}

        /* Cards & Sections */
        .section {{ background:var(--card); border:1px solid var(--border); border-radius:12px; margin-bottom:1.25rem; box-shadow:var(--shadow); overflow:hidden; }}
        .section-head {{ padding:1rem 1.25rem; border-bottom:1px solid var(--border); cursor:pointer; display:flex; justify-content:space-between; align-items:center; }}
        .section-head h2 {{ font-size:1rem; font-weight:600; }}
        .section-body {{ padding:1.25rem; }}
        .section-body.collapsed {{ display:none; }}

        /* Account cards */
        .account-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:1rem; margin-bottom:1rem; }}
        .account-card {{ background:var(--bg-alt); border:1px solid var(--border); border-radius:10px; padding:1.25rem; }}
        .account-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem; }}
        .bank-name {{ font-weight:600; }}
        .account-number {{ font-family:'JetBrains Mono',monospace; font-size:0.85rem; color:var(--text-soft); }}
        .account-holder {{ font-size:0.82rem; color:var(--text-muted); margin-bottom:0.75rem; }}
        .account-metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:0.75rem; }}
        .metric {{ }}
        .metric-label {{ font-size:0.68rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.03em; }}
        .metric-value {{ font-family:'JetBrains Mono',monospace; font-size:0.88rem; font-weight:600; }}

        /* Summary cards */
        .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; margin-bottom:1.5rem; }}
        .summary-card {{ background:var(--bg-alt); border:1px solid var(--border); border-radius:10px; padding:1.25rem; text-align:center; }}
        .summary-card .val {{ font-size:1.4rem; font-weight:700; font-family:'JetBrains Mono',monospace; }}
        .summary-card .lbl {{ font-size:0.72rem; color:var(--text-muted); text-transform:uppercase; margin-top:0.25rem; }}

        /* Tables */
        .table-wrap {{ overflow-x:auto; border-radius:8px; border:1px solid var(--border); }}
        table {{ width:100%; border-collapse:collapse; font-size:0.82rem; }}
        th {{ background:var(--bg); color:var(--text-soft); font-weight:600; text-transform:uppercase; font-size:0.7rem; letter-spacing:0.04em; padding:0.65rem 0.75rem; text-align:left; position:sticky; top:0; white-space:nowrap; border-bottom:2px solid var(--border); }}
        td {{ padding:0.55rem 0.75rem; border-bottom:1px solid var(--border); }}
        tr:hover {{ background:var(--bg); }}
        .total-row {{ background:var(--blue-dim) !important; font-weight:600; }}
        .total-row td {{ border-top:2px solid var(--blue); border-bottom:2px solid var(--blue); }}
        .mono {{ font-family:'JetBrains Mono',monospace; }}
        .r {{ text-align:right; }}
        .credit {{ color:var(--green); }}
        .debit {{ color:var(--red); }}
        .sticky-col {{ position:sticky; left:0; background:inherit; z-index:1; font-weight:600; }}
        th.sticky-col {{ z-index:2; }}

        /* Badges */
        .badge {{ display:inline-block; padding:0.15rem 0.5rem; border-radius:20px; font-size:0.7rem; font-weight:600; }}
        .badge-current {{ background:var(--blue-dim); color:var(--blue); }}
        .badge-savings {{ background:var(--green-dim); color:var(--green); }}
        .badge-od {{ background:var(--red-dim); color:var(--red); }}
        .badge-credit {{ background:var(--green-dim); color:var(--green); }}
        .badge-debit {{ background:var(--red-dim); color:var(--red); }}

        .rp-tag {{ display:inline-block; padding:0.25rem 0.6rem; background:var(--amber-dim); color:var(--amber); border-radius:6px; font-size:0.78rem; margin:0.2rem; }}
        .rp-tag small {{ opacity:0.7; }}
        .rp-badge {{ display:inline-block; padding:0.1rem 0.35rem; background:var(--amber-dim); color:var(--amber); border-radius:4px; font-size:0.65rem; font-weight:700; margin-left:0.35rem; vertical-align:middle; }}

        /* Flags */
        .flag-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:0.35rem; vertical-align:middle; }}
        .flag-dot.detected {{ background:var(--red); }}
        .flag-dot.clear {{ background:var(--green); }}
        .flag-yes {{ background:var(--red-bg); }}

        /* Observations */
        .obs-list {{ list-style:none; padding:0; }}
        .obs-item {{ padding:0.75rem 1rem; margin-bottom:0.5rem; border-radius:8px; font-size:0.88rem; line-height:1.5; }}
        .obs-item.positive {{ background:var(--green-bg); border-left:3px solid var(--green); }}
        .obs-item.concern {{ background:var(--red-bg); border-left:3px solid var(--red); }}

        /* Two column layout */
        .two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:1.5rem; }}
        @media (max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}

        /* Charts */
        .chart-box {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:1rem; margin-bottom:1rem; }}
        .chart-title {{ font-size:0.85rem; font-weight:600; margin-bottom:0.5rem; color:var(--text-soft); }}

        /* Note */
        .note {{ font-size:0.8rem; color:var(--text-muted); padding:0.5rem; font-style:italic; }}

        /* v6.2.1: Data quality banner */
        .dq-banner {{ border-radius:12px; padding:1.25rem 1.5rem; margin-bottom:1.5rem; display:flex; gap:14px; align-items:flex-start; }}
        .dq-banner.dq-fail {{ background:var(--red-bg); border:1px solid var(--red); }}
        .dq-banner.dq-pass {{ background:var(--green-bg); border:1px solid var(--green); }}
        .dq-banner .dq-icon {{ font-size:1.3rem; flex-shrink:0; }}
        .dq-banner .dq-title {{ font-weight:700; font-size:0.92rem; margin-bottom:4px; }}
        .dq-banner.dq-fail .dq-title {{ color:var(--red); }}
        .dq-banner.dq-pass .dq-title {{ color:var(--green); }}
        .dq-banner .dq-detail {{ font-size:0.82rem; color:var(--text-soft); line-height:1.6; }}
        .dq-banner .dq-stats {{ display:flex; gap:2rem; margin-top:0.75rem; font-size:0.82rem; }}
        .dq-banner .dq-stat-label {{ font-size:0.68rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.04em; }}
        .dq-banner .dq-stat-val {{ font-weight:700; font-size:1.05rem; font-family:'JetBrains Mono',monospace; }}
        .dq-banner.dq-fail .dq-stat-val {{ color:var(--red); }}

        /* v6.2.1: Row-level recon status */
        tr.row-fail {{ background:var(--red-bg) !important; }}
        .recon-badge {{ display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px; font-size:0.7rem; font-weight:600; font-family:'JetBrains Mono',monospace; }}
        .recon-badge.pass {{ background:var(--green-dim); color:var(--green); }}
        .recon-badge.fail {{ background:var(--red-dim); color:var(--red); }}
        .gap-pill {{ display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px; font-size:0.7rem; background:var(--red-dim); color:var(--red); font-weight:500; }}
        .dq-gap-panel {{ background:var(--red-bg); border:1px solid rgba(220,38,38,0.2); border-radius:10px; padding:1rem 1.25rem; margin-top:0.75rem; font-size:0.82rem; }}
        .dq-gap-panel strong {{ color:var(--red); }}
        .dq-gap-item {{ padding:0.5rem 0; border-bottom:1px solid rgba(220,38,38,0.1); display:grid; grid-template-columns:100px 1fr; gap:0.5rem; }}
        .dq-gap-item:last-child {{ border-bottom:none; }}
        .obs-item.data-warn {{ background:var(--red-bg); border-left:3px solid var(--red); font-weight:500; }}

        /* v6.3.0 column highlights */
        .v630-count {{ background:var(--purple-dim) !important; }}
        .v630-amt {{ background:var(--blue-dim) !important; }}
        .v630-uncl {{ background:var(--amber-dim) !important; }}
        td.v630-count {{ background:var(--purple-dim); }}
        td.v630-amt {{ background:var(--blue-dim); }}
        td.v630-uncl {{ background:var(--amber-dim); }}

        /* Fraud detector */
        .fraud-shield {{ display:inline-flex; align-items:center; gap:6px; padding:0.3rem 0.8rem; border-radius:20px; font-weight:700; font-size:0.85rem; }}
        .fraud-shield.low {{ background:var(--green-dim); color:var(--green); }}
        .fraud-shield.medium {{ background:var(--amber-dim); color:var(--amber); }}
        .fraud-shield.high {{ background:var(--red-dim); color:var(--red); }}
        .fraud-file-header {{ display:flex; align-items:center; gap:0.75rem; padding:1rem 1.25rem; cursor:pointer; border-bottom:1px solid var(--border); }}
        .fraud-file-header:hover {{ background:var(--bg); }}
        .fraud-detail {{ padding:0; }}
        .info-panel {{ background:var(--blue-dim); border:1px solid rgba(37,99,235,0.2); border-radius:10px; padding:1rem 1.25rem; margin-bottom:1.25rem; font-size:0.84rem; line-height:1.7; }}
        .info-panel h4 {{ color:var(--blue); margin-bottom:0.5rem; }}
        .info-panel ul {{ margin:0.5rem 0 0 1.25rem; }}
        .info-panel li {{ margin-bottom:0.25rem; }}
        .config-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:0.5rem; }}
        .config-item {{ display:flex; justify-content:space-between; padding:0.5rem 0.75rem; border-bottom:1px solid var(--border); font-size:0.84rem; }}
        .config-item .config-label {{ color:var(--text-soft); }}
        .config-item .config-val {{ font-family:'JetBrains Mono',monospace; font-weight:600; }}
        .validation-pass {{ color:var(--green); font-weight:600; }}
        .validation-warn {{ color:var(--amber); font-weight:600; }}
        .validation-fail {{ color:var(--red); font-weight:600; }}

        /* Footer */
        .footer {{ text-align:center; padding:2rem 1rem; color:var(--text-muted); font-size:0.78rem; border-top:1px solid var(--border); margin-top:2rem; }}

        /* Print */
        @media print {{
            .nav, .theme-toggle, .report-actions, .tab-export-bar {{ display:none; }}
            .tab {{ display:block !important; page-break-inside:avoid; }}
            body {{ font-size:11px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="report-actions">
                <button class="excel-btn" onclick="downloadReportExcel()">Download Report Excel</button>
            </div>
            <button class="theme-toggle" onclick="toggleTheme()">Dark</button>
            <div class="header-grid">
                <div class="company-info">
                    <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-muted);margin-bottom:0.35rem">Kredit Lab &mdash; Statement Intelligence</div>
                    <h1>{company} <span class="schema-badge">Kredit Lab v{r.get('schema_version', '6')}</span></h1>
                    <div class="period">{period_start} to {period_end} &middot; {total_months} months &middot; {sum(a.get('transaction_count',0) for a in accounts):,} transactions</div>
                </div>
                <div class="header-kpi">
                    <div class="kpi"><div class="val credit">RM {consol.get('net_credits',0):,.0f}</div><div class="lbl">Net Credits</div></div>
                    <div class="kpi"><div class="val debit">RM {consol.get('net_debits',0):,.0f}</div><div class="lbl">Net Debits{'  ⚠️' if is_incomplete else ''}</div></div>
                    <div class="kpi"><div class="val">RM {consol.get('annualized_net_credits',0):,.0f}</div><div class="lbl">Annualized</div></div>
                    <div class="kpi"><div class="val">RM {consol.get('eod_average',0):,.0f}</div><div class="lbl">Avg EOD</div></div>
                    <div class="kpi"><div class="val" style="color:var(--{'red' if detected_count > total_flags//2 else 'amber'})">{detected_count}/{total_flags}</div><div class="lbl">Flags</div></div>
                </div>
            </div>
        </div>

        <div class="nav">
            <button class="nav-btn active" onclick="showTab('overview')">Overview</button>
            <button class="nav-btn" onclick="showTab('monthly')">Cash Flow</button>
            <button class="nav-btn" onclick="showTab('parties')">Top Parties</button>
            <button class="nav-btn" onclick="showTab('large')">Large Credits</button>
            <button class="nav-btn" onclick="showTab('related')">Counterparty</button>
            <button class="nav-btn" onclick="showTab('loans')">Facilities</button>
            <button class="nav-btn" onclick="showTab('flags')">Risk Signals</button>
            {'<button class="nav-btn" onclick="showTab(&#39;fx&#39;)">FX / Remittance</button>' if is_v620 else ''}
            {'<button class="nav-btn" onclick="showTab(&#39;unclassified&#39;)">Unclassified</button>' if is_v630 else ''}
            {'<button class="nav-btn" onclick="showTab(&#39;parsing&#39;)">Parsing QC</button>' if has_parsing else ''}
            <button class="nav-btn" onclick="showTab('fraud')">Fraud Detector</button>
        </div>

        <!-- OVERVIEW TAB -->
        <div id="tab-overview" class="tab active">
            {dq_banner_html}
            <div class="account-grid">{acc_cards}</div>

            {'<div class="section"><div class="section-head"><h2>Known Related Parties</h2></div><div class="section-body">' + rp_html + '</div></div>' if rp_html else ''}

            <div class="two-col">
                <div class="chart-box"><div class="chart-title">Net Credits vs Debits (Monthly)</div><div id="chartCrDr" style="height:300px"></div></div>
                <div class="chart-box"><div class="chart-title">EOD Balance Range (Monthly)</div><div id="chartEOD" style="height:300px"></div></div>
            </div>

            <div class="section">
                <div class="section-head"><h2>Consolidated Summary</h2></div>
                <div class="section-body">
                    <div class="summary-grid">
                        <div class="summary-card"><div class="val credit">{consol.get('gross_credits',0):,.0f}</div><div class="lbl">Gross Credits</div></div>
                        <div class="summary-card"><div class="val debit">{consol.get('gross_debits',0):,.0f}</div><div class="lbl">Gross Debits</div></div>
                        <div class="summary-card"><div class="val credit">{consol.get('net_credits',0):,.0f}</div><div class="lbl">Net Credits</div></div>
                        <div class="summary-card"><div class="val debit">{consol.get('net_debits',0):,.0f}</div><div class="lbl">Net Debits</div></div>
                        <div class="summary-card"><div class="val">{consol.get('annualized_net_credits',0):,.0f}</div><div class="lbl">Annualized Cr</div></div>
                        <div class="summary-card"><div class="val">{consol.get('annualized_net_debits',0):,.0f}</div><div class="lbl">Annualized Dr</div></div>
                    </div>
                    <div class="two-col">
                        <div>
                            <h4 style="color:var(--green);margin-bottom:0.75rem">Exclusions from Credits</h4>
                            <div class="table-wrap"><table>
                                <tr><td>Own Party</td><td class="mono r">{consol.get('total_own_party_cr',0):,.2f}</td></tr>
                                <tr><td>Related Party</td><td class="mono r">{consol.get('total_related_party_cr',0):,.2f}</td></tr>
                                <tr><td>Reversals</td><td class="mono r">{consol.get('total_reversal_cr',0):,.2f}</td></tr>
                                <tr><td>Loan Disbursements</td><td class="mono r">{consol.get('total_loan_disbursement_cr',0):,.2f}</td></tr>
                                <tr><td>FD/Interest</td><td class="mono r">{consol.get('total_fd_interest_cr',0):,.2f}</td></tr>
                                <tr><td>Inward Return (C16)</td><td class="mono r">{consol.get('total_inward_return_cr',0):,.2f}</td></tr>
                            </table></div>
                        </div>
                        <div>
                            <h4 style="color:var(--red);margin-bottom:0.75rem">Exclusions from Debits</h4>
                            <div class="table-wrap"><table>
                                <tr><td>Own Party</td><td class="mono r">{consol.get('total_own_party_dr',0):,.2f}</td></tr>
                            </table></div>
                        </div>
                    </div>
                    {'<div style="margin-top:1rem"><h4 style="color:var(--purple);margin-bottom:0.75rem">FX / Remittance Summary</h4><div class="summary-grid"><div class="summary-card"><div class="val credit">' + f"{consol.get('total_fx_credits',0):,.0f}" + '</div><div class="lbl">FX Credits (' + f"{consol.get('fx_credit_pct',0):.1f}" + '% of Gross)</div></div><div class="summary-card"><div class="val debit">' + f"{consol.get('total_fx_debits',0):,.0f}" + '</div><div class="lbl">FX Debits (' + f"{consol.get('fx_debit_pct',0):.1f}" + '% of Gross)</div></div><div class="summary-card"><div class="val" style="font-size:0.9rem">' + (', '.join(consol.get('fx_currencies_all', [])) or 'None') + '</div><div class="lbl">Currencies</div></div></div></div>' if is_v620 else ''}
                </div>
            </div>

            <div class="two-col">
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--green)">Positive Observations</h2></div>
                    <div class="section-body"><ul class="obs-list">{pos_obs}</ul></div>
                </div>
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--red)">Concerns</h2></div>
                    <div class="section-body"><ul class="obs-list">{con_obs}</ul></div>
                </div>
            </div>
        </div>

        <!-- MONTHLY ANALYSIS TAB -->
        <div id="tab-monthly" class="tab">
            <div class="section">
                <div class="section-head"><h2>Monthly Cash Flow Breakdown</h2></div>
                {'<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-soft);border-bottom:1px solid var(--border);display:flex;gap:1.25rem;flex-wrap:wrap">' + ''.join(f'<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{acct_colors.get(a,"var(--text-muted)")};margin-right:4px;vertical-align:middle"></span>{next((ac.get("bank_name","") for ac in accounts if ac.get("account_number")==a), "")} ({a})</span>' for a in acct_list) + ' <span style="font-weight:600">Bold rows = month subtotal</span></div>' if has_account_col and len(acct_list) > 1 else ''}
                <div class="section-body" style="padding:0">
                    <div class="table-wrap" style="max-height:600px; overflow:auto">
                        <table>
                            <thead><tr>
                                <th class="sticky-col">Month / Account</th>
                                {'<th>Status</th>' if has_recon else ''}
                                <th class="r">Gross Cr</th><th class="r">Gross Dr</th>
                                <th class="r">Net Cr</th><th class="r">Net Dr</th>
                                <th class="r">Cr #</th><th class="r">Dr #</th>
                                <th class="r">Own Cr</th><th class="r">Own Dr</th>
                                <th class="r">RP Cr</th><th class="r">RP Dr</th>
                                <th class="r">Reversal</th>
                                <th class="r">Loan Disb</th><th class="r">FD/Int</th>
                                <th class="r">Cash Dep</th><th class="r">Cash Wdl</th>
                                <th class="r">Chq Dep</th><th class="r">Chq Issue</th>
                                <th class="r">Loan Repay</th><th class="r">Salary</th>
                                <th class="r">EPF</th><th class="r">SOCSO</th><th class="r">Tax</th>
                                <th class="r">Ret Chq #</th><th class="r">Ret Chq Amt</th>
                                <th class="r">Round Fig</th><th class="r">High Val</th>
                                <th class="r">EOD Low</th><th class="r">EOD High</th><th class="r">EOD Avg</th>
                                <th class="r">Open Bal</th><th class="r">Close Bal</th>
                                {'<th class="r v630-count">Own Cr #</th><th class="r v630-count">Own Dr #</th><th class="r v630-count">RP Cr #</th><th class="r v630-count">RP Dr #</th><th class="r v630-count">Loan #</th><th class="r v630-amt">Inward Ret</th><th class="r v630-uncl">Uncl Cr #</th><th class="r v630-uncl">Uncl Cr Amt</th><th class="r v630-uncl">Uncl Dr #</th><th class="r v630-uncl">Uncl Dr Amt</th>' if is_v630 else ''}
                            </tr></thead>
                            <tbody>
                                {monthly_rows}
                                {consol_row}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            <div class="note">Net Credits = Gross Credits - Own Party - Related Party - Reversals - Loan Disbursements - FD/Interest{' - Inward Return (C16)' if is_v630 else ''} | Net Debits = Gross Debits - Own Party (C02){' | <span style="display:inline-block;width:10px;height:10px;background:var(--purple-dim);border:1px solid var(--purple);border-radius:2px;vertical-align:middle;margin:0 2px"></span> Count columns <span style="display:inline-block;width:10px;height:10px;background:var(--amber-dim);border:1px solid var(--amber);border-radius:2px;vertical-align:middle;margin:0 2px"></span> Unclassified columns (v6.3.0)' if is_v630 else ''}</div>
'''
    # v6.2.1: Build gap detail panels for failed months
    gap_panels_html = ''
    extraction_gaps = parsing.get('extraction_gaps', []) if parsing else []
    if extraction_gaps and has_recon:
        # Group gaps by month
        from collections import defaultdict as _dd
        gaps_by_month = _dd(list)
        for g in extraction_gaps:
            gaps_by_month[g.get('month', '')].append(g)

        for gap_month in sorted(gaps_by_month.keys()):
            month_gaps = gaps_by_month[gap_month]
            total_missing = sum(g.get('missing_amount', 0) for g in month_gaps)
            gap_panels_html += f'''<div class="dq-gap-panel">
                <strong>Extraction Gaps — {gap_month} ({len(month_gaps)} gap{"s" if len(month_gaps) > 1 else ""}, RM {total_missing:,.2f} missing)</strong>'''
            for gi, g in enumerate(month_gaps, 1):
                gap_panels_html += f'''<div class="dq-gap-item">
                    <div><div style="font-size:0.72rem;color:var(--text-muted)">Gap #{gi}</div>
                    <div style="color:var(--red);font-weight:600">RM {g.get('missing_amount',0):,.2f}</div></div>
                    <div><div>Page {g.get('page','')} · {g.get('date','')} · {g.get('missing_type','').lower()}(s) missing</div>
                    <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px">After: <em>{g.get('prev_description','')[:60]}</em> (bal RM {g.get('balance_before_gap',0):,.2f})</div>
                    <div style="font-size:0.78rem;color:var(--text-muted)">Before: <em>{g.get('next_description','')[:60]}</em> (bal RM {g.get('balance_after_gap',0):,.2f})</div></div>
                </div>'''
            gap_panels_html += '</div>'

    html += gap_panels_html
    html += f'''
        </div>

        <!-- TOP PARTIES TAB -->
        <div id="tab-parties" class="tab">
            <div class="two-col">
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--green)">Top 10 Payers (Income)</h2></div>
                    <div class="section-body" style="padding:0">
                        <div class="table-wrap"><table>
                            <thead><tr><th>#</th><th>Party</th><th class="r">Amount (RM)</th><th class="r">Txns</th></tr></thead>
                            <tbody>{payer_rows or '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No data</td></tr>'}</tbody>
                        </table></div>
                        {payers_suppressed_html}
                    </div>
                </div>
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--red)">Top 10 Payees (Outflow)</h2></div>
                    <div class="section-body" style="padding:0">
                        <div class="table-wrap"><table>
                            <thead><tr><th>#</th><th>Party</th><th class="r">Amount (RM)</th><th class="r">Txns</th></tr></thead>
                            <tbody>{payee_rows or '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No data</td></tr>'}</tbody>
                        </table></div>
                        {payees_suppressed_html}
                    </div>
                </div>
            </div>
            <div class="note"><span class="rp-badge">RP</span> = Related Party</div>
        </div>

        <!-- LARGE TRANSACTIONS TAB -->
        <div id="tab-large" class="tab">
            <div class="section">
                <div class="section-head"><h2>Large Transactions (&ge; RM {large_threshold:,.0f})</h2><span class="badge badge-current">{len(large_txns)} transactions</span></div>
                <div class="section-body" style="padding:0">
                    <div class="table-wrap" style="max-height:500px;overflow:auto">
                        <table>
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Description</th>
                                    <th class="r">Amount (RM)</th>
                                    <th class="r">Balance</th>
                                </tr>
                            </thead>
                            <tbody>
                                {large_txn_rows}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- RELATED PARTY TAB -->
        <div id="tab-related" class="tab">
            {counterparty_ledger_html}
            <div class="section">
                <div class="section-head"><h2>Own & Related Party Transactions</h2></div>
                <div class="section-body">
                    <div class="summary-grid">
                        <div class="summary-card"><div class="val credit">{rp_counts['own_party_cr']:,}</div><div class="lbl">Own Party Cr txns</div></div>
                        <div class="summary-card"><div class="val debit">{rp_counts['own_party_dr']:,}</div><div class="lbl">Own Party Dr txns</div></div>
                        <div class="summary-card"><div class="val credit">{rp_counts['related_party_cr']:,}</div><div class="lbl">Related Party Cr txns</div></div>
                        <div class="summary-card"><div class="val debit">{rp_counts['related_party_dr']:,}</div><div class="lbl">Related Party Dr txns</div></div>
                    </div>
                    <div class="table-wrap" style="max-height:500px;overflow:auto"><table>
                        <thead><tr><th>Date</th><th>Description</th><th class="r">Amount (RM)</th><th>Type</th><th>Party Type</th><th>Party Name</th></tr></thead>
                        <tbody>{rp_txn_rows}</tbody>
                    </table></div>
                    {rp_note}
                </div>
            </div>
        </div>

        <!-- LOANS TAB -->
        <div id="tab-loans" class="tab">
            <div class="summary-grid">
                <div class="summary-card"><div class="val credit">{consol.get('total_loan_disbursement_cr',0):,.0f}</div><div class="lbl">Total Disbursements</div></div>
                <div class="summary-card"><div class="val debit">{consol.get('total_loan_repayment_dr',0):,.0f}</div><div class="lbl">Total Repayments</div></div>
                <div class="summary-card"><div class="val">{len(loans.get('disbursements',[])) or loans.get('summary',{}).get('disbursement_count',0)}</div><div class="lbl">Disbursement Txns</div></div>
                <div class="summary-card"><div class="val">{len(loans.get('repayments',[])) or loans.get('summary',{}).get('repayment_count',0)}</div><div class="lbl">Repayment Txns</div></div>
            </div>
            <div class="two-col">
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--green)">Disbursements (Credits)</h2></div>
                    <div class="section-body" style="padding:0"><div class="table-wrap" style="max-height:400px;overflow:auto"><table>
                        <thead><tr><th>Date</th><th>Description</th><th class="r">Amount</th><th>Category</th></tr></thead>
                        <tbody>{loan_disb_rows or '<tr><td colspan="4" class="note">No disbursements</td></tr>'}</tbody>
                    </table></div></div>
                </div>
                <div class="section">
                    <div class="section-head"><h2 style="color:var(--red)">Repayments (Debits)</h2></div>
                    <div class="section-body" style="padding:0"><div class="table-wrap" style="max-height:400px;overflow:auto"><table>
                        <thead><tr><th>Date</th><th>Description</th><th class="r">Amount</th><th>Category</th></tr></thead>
                        <tbody>{loan_repay_rows or '<tr><td colspan="4" class="note">No repayments</td></tr>'}</tbody>
                    </table></div></div>
                </div>
            </div>
        </div>

        <!-- FLAGS TAB -->
        <div id="tab-flags" class="tab">
            {statutory_html}
            <div class="section">
                <div class="section-head"><h2>Risk Signals</h2><span class="badge" style="background:var(--{'red-dim' if detected_count > total_flags//2 else 'amber-dim'});color:var(--{'red' if detected_count > total_flags//2 else 'amber'})">{detected_count} of {total_flags} detected</span></div>
                <div class="section-body" style="padding:0">
                    <div class="table-wrap"><table>
                        <thead><tr><th style="width:40px">#</th><th>Signal</th><th style="width:80px;text-align:center">Status</th><th>Remarks</th></tr></thead>
                        <tbody>{flag_rows}</tbody>
                    </table></div>
                </div>
            </div>
            <div class="section">
                <div class="section-head"><h2>Round Figure Credits (AML) &mdash; Detail</h2><span class="badge badge-current">{len(round_figure_credits)} transactions</span></div>
                <div class="section-body" style="padding:0">
                    <div class="note" style="padding:0.5rem 1.25rem">Credits that are exact round multiples (Flag 3). Listed so the analyst can trace each back to the statement before treating it as anomalous &mdash; round contract payments are common for service operators.</div>
                    <div class="table-wrap" style="max-height:400px;overflow:auto"><table>
                        <thead><tr><th>Date</th><th>Description</th><th class="r">Amount (RM)</th><th class="r">Balance</th></tr></thead>
                        <tbody>{rf_cr_rows}</tbody>
                    </table></div>
                </div>
            </div>
        </div>

        {fx_tab_html}
        {unclassified_tab_html}
        {parsing_tab_html}
        {fraud_tab_html}

        <div class="footer">
            <p>Kredit Lab &mdash; Statement Intelligence Report | Generated {r.get('generated_at','')} | {period_start} &ndash; {period_end}</p>
        </div>
    </div>

    <script>
        function showTab(name) {{
            document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
            const tab = document.getElementById('tab-'+name);
            if(tab) tab.classList.add('active');
            event.target.classList.add('active');
        }}
        function toggleTheme() {{
            const html = document.documentElement;
            const btn = document.querySelector('.theme-toggle');
            const t = html.getAttribute('data-theme')==='dark'?'light':'dark';
            html.setAttribute('data-theme',t);
            btn.textContent = t==='dark'?'Light':'Dark';
            // Re-render charts for theme
            renderCharts();
        }}
        function reportCompanyName() {{
            const heading = document.querySelector('.company-info h1');
            if (!heading) return 'kredit_lab_report';
            return (heading.childNodes[0] && heading.childNodes[0].textContent || 'kredit_lab_report').trim();
        }}
        function filenameSafe(value) {{
            return String(value || 'report')
                .replace(/[^a-z0-9]+/gi, '_')
                .replace(/^_+|_+$/g, '')
                .slice(0, 90) || 'report';
        }}
        function cleanExportTable(table) {{
            const clone = table.cloneNode(true);
            clone.querySelectorAll('button,input,select,textarea,script').forEach(function(el) {{ el.remove(); }});
            clone.querySelectorAll('tr').forEach(function(row) {{
                if (row.style && row.style.display === 'none') row.remove();
            }});
            return clone.outerHTML;
        }}
        function buildExcelHtml(title, tableHtmlParts) {{
            let body = '<h1>' + title + '</h1>';
            if (!tableHtmlParts.length) {{
                body += '<p>No table data available.</p>';
            }}
            tableHtmlParts.forEach(function(tableHtml, idx) {{
                body += '<h2>Table ' + (idx + 1) + '</h2>' + tableHtml;
            }});
            return '\\ufeff<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="UTF-8"><style>body{{font-family:Arial,sans-serif}} table{{border-collapse:collapse;margin-bottom:18px}} th{{background:#1B4F72;color:#fff;font-weight:bold}} th,td{{border:1px solid #d5d8dc;padding:6px 8px;mso-number-format:"\\@"}} .r{{text-align:right}} .credit{{color:#059669}} .debit{{color:#dc2626}}</style></head><body>' + body + '</body></html>';
        }}
        function downloadExcelHtml(filename, title, tableHtmlParts) {{
            const blob = new Blob([buildExcelHtml(title, tableHtmlParts)], {{type:'application/vnd.ms-excel;charset=utf-8;'}});
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = filenameSafe(filename) + '.xls';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(function() {{ URL.revokeObjectURL(link.href); }}, 250);
        }}
        function getTabTitle(tab) {{
            const tabName = tab.id.replace(/^tab-/, '');
            const navButtons = Array.from(document.querySelectorAll('.nav-btn'));
            const navButton = navButtons.find(function(btn) {{
                return (btn.getAttribute('onclick') || '').indexOf("'" + tabName + "'") !== -1;
            }});
            return navButton ? navButton.textContent.trim() : tabName.replace(/-/g, ' ');
        }}
        function downloadTabExcel(tabName) {{
            const tab = document.getElementById('tab-' + tabName);
            if (!tab) return;
            const title = getTabTitle(tab);
            const tables = Array.from(tab.querySelectorAll('table')).map(cleanExportTable);
            downloadExcelHtml(reportCompanyName() + '_' + title, title, tables);
        }}
        function downloadReportExcel() {{
            const tables = [];
            document.querySelectorAll('.tab').forEach(function(tab) {{
                const title = getTabTitle(tab);
                tables.push('<table><tr><th>' + title + '</th></tr></table>');
                tab.querySelectorAll('table').forEach(function(table) {{
                    tables.push(cleanExportTable(table));
                }});
            }});
            downloadExcelHtml(reportCompanyName() + '_full_html_report', 'Kredit Lab Report - ' + reportCompanyName(), tables);
        }}
        function injectExcelButtons() {{
            document.querySelectorAll('.tab').forEach(function(tab) {{
                if (tab.firstElementChild && tab.firstElementChild.classList.contains('tab-export-bar')) return;
                const tabName = tab.id.replace(/^tab-/, '');
                const bar = document.createElement('div');
                bar.className = 'tab-export-bar';
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'excel-btn';
                btn.textContent = 'Download This Tab Excel';
                btn.addEventListener('click', function() {{ downloadTabExcel(tabName); }});
                bar.appendChild(btn);
                tab.insertBefore(bar, tab.firstChild);
            }});
        }}
        function renderCharts() {{
            if(typeof Plotly==='undefined'){{console.warn('Plotly not loaded — charts disabled');return;}}
            const isDark = document.documentElement.getAttribute('data-theme')==='dark';
            const gridColor = isDark?'#1e2a42':'#e2e8f0';
            const textColor = isDark?'#94a3b8':'#475569';
            const bg = 'transparent';

            Plotly.newPlot('chartCrDr', [
                {{x:{chart_months},y:{chart_net_cr},name:'Net Credits',type:'bar',marker:{{color:'rgba(5,150,105,0.7)'}}}},
                {{x:{chart_months},y:{chart_net_dr},name:'Net Debits',type:'bar',marker:{{color:'rgba(220,38,38,0.7)'}}}}
            ], {{
                paper_bgcolor:bg,plot_bgcolor:bg,font:{{color:textColor,size:11}},
                barmode:'group',showlegend:true,legend:{{orientation:'h',y:1.12}},
                margin:{{t:30,b:40,l:60,r:20}},
                yaxis:{{gridcolor:gridColor,tickformat:','}}
            }}, {{responsive:true,displayModeBar:false}});

            Plotly.newPlot('chartEOD', [
                {{x:{chart_months},y:{chart_eod_high},name:'EOD High',type:'scatter',mode:'lines+markers',line:{{color:'#2563eb',width:2}},marker:{{size:6}}}},
                {{x:{chart_months},y:{chart_eod_avg},name:'EOD Average',type:'scatter',mode:'lines+markers',line:{{color:'#7c3aed',width:2}},marker:{{size:6}}}},
                {{x:{chart_months},y:{chart_eod_low},name:'EOD Low',type:'scatter',mode:'lines+markers',line:{{color:'#dc2626',width:2,dash:'dot'}},marker:{{size:6}}}}
            ], {{
                paper_bgcolor:bg,plot_bgcolor:bg,font:{{color:textColor,size:11}},
                showlegend:true,legend:{{orientation:'h',y:1.12}},
                margin:{{t:30,b:40,l:60,r:20}},
                yaxis:{{gridcolor:gridColor,tickformat:','}}
            }}, {{responsive:true,displayModeBar:false}});

            // v6.2.0: FX chart
            var fxEl = document.getElementById('chartFX');
            if (fxEl) {{
                Plotly.newPlot('chartFX', [
                    {{x:{chart_months},y:{fx_chart_cr_json},name:'FX Credits',type:'bar',marker:{{color:'rgba(5,150,105,0.7)'}}}},
                    {{x:{chart_months},y:{fx_chart_dr_json},name:'FX Debits',type:'bar',marker:{{color:'rgba(220,38,38,0.7)'}}}}
                ], {{
                    paper_bgcolor:bg,plot_bgcolor:bg,font:{{color:textColor,size:11}},
                    barmode:'group',showlegend:true,legend:{{orientation:'h',y:1.12}},
                    margin:{{t:30,b:40,l:60,r:20}},
                    yaxis:{{gridcolor:gridColor,tickformat:','}}
                }}, {{responsive:true,displayModeBar:false}});
            }}
        }}
        document.addEventListener('DOMContentLoaded', function() {{
            injectExcelButtons();
            renderCharts();
        }});
    </script>
</body>
</html>'''
    return html


# ============================================================
# HTML REPORT GENERATION FUNCTIONS (from your JSON converter)
# Copy the entire set of functions here
# ============================================================

def fmt(val, decimals=2):
    """Format number with commas"""
    if val is None:
        return "0.00"
    return f"{val:,.{decimals}f}"

def normalize_observations(obs):
    """Coerce observations into {'positive': [...], 'concerns': [...]}."""
    if isinstance(obs, dict):
        return {'positive': list(obs.get('positive', []) or []),
                'concerns': list(obs.get('concerns', []) or [])}
    pos, con = [], []
    if isinstance(obs, list):
        for item in obs:
            if isinstance(item, str):
                con.append(item)
            elif isinstance(item, dict):
                kind = str(item.get('type') or item.get('category') or item.get('sentiment') or '').lower()
                text = item.get('text') or item.get('observation') or item.get('message') or item.get('description') or ''
                if not text:
                    continue
                if kind in ('positive', 'pos', 'good', 'strength'):
                    pos.append(text)
                else:
                    con.append(text)
    return {'positive': pos, 'concerns': con}

def adapt_to_v6(src):
    """Reshape flat extractor output into v6.3.3 renderer schema."""
    from collections import defaultdict

    summary = src.get('summary', {}) or {}
    transactions = src.get('transactions', []) or []
    monthly_summary = src.get('monthly_summary', []) or []
    cp_ledger = src.get('counterparty_ledger', {}) or {}
    pdf_integrity = src.get('pdf_integrity')
    high_value_threshold = summary.get('high_value_threshold', 100000)

    report_info = {
        'company_name': summary.get('company_names', ['Unknown'])[0] if summary.get('company_names') else 'Unknown',
        'schema_version': 'Testv2.0',
        'period_start': '',
        'period_end': '',
        'total_months': len(monthly_summary),
        'related_parties': [],
    }

    # accounts aggregation
    acc_map = defaultdict(lambda: {'credits': 0.0, 'debits': 0.0, 'txn_count': 0, 'bank': '', 'last_bal': None, 'opening_bal': None})
    for t in transactions:
        an = t.get('account_no', '')
        if not an:
            continue
        a = acc_map[an]
        a['txn_count'] += 1
        cr = float(t.get('credit', 0) or 0)
        dr = float(t.get('debit', 0) or 0)
        a['credits'] += cr
        a['debits'] += dr
        if not a['bank']:
            a['bank'] = t.get('bank', '') or ''
        bal = t.get('balance')
        if isinstance(bal, (int, float)):
            if a['opening_bal'] is None:
                a['opening_bal'] = bal - cr + dr
            a['last_bal'] = bal
    
    accounts = []
    for an, a in sorted(acc_map.items()):
        accounts.append({
            'bank_name': a['bank'],
            'account_number': an,
            'account_holder': report_info['company_name'],
            'account_type': 'Current',
            'opening_balance': round(a['opening_bal'] or 0.0, 2),
            'closing_balance': round(a['last_bal'] or 0.0, 2),
            'total_credits': round(a['credits'], 2),
            'total_debits': round(a['debits'], 2),
            'transaction_count': a['txn_count'],
        })

    # Build monthly analysis from monthly_summary
    monthly_analysis = []
    for m in monthly_summary:
        month = m.get('month', '')
        highest = float(m.get('highest_balance', 0) or 0)
        lowest = float(m.get('lowest_balance', 0) or 0)
        monthly_analysis.append({
            'month': month,
            'bank_name': '',
            'account_number': m.get('account_no', ''),
            'gross_credits': float(m.get('total_credit', 0) or 0),
            'gross_debits': float(m.get('total_debit', 0) or 0),
            'net_credits': float(m.get('total_credit', 0) or 0),
            'net_debits': float(m.get('total_debit', 0) or 0),
            'credit_count': m.get('credit_count', 0),
            'debit_count': m.get('debit_count', 0),
            'own_party_cr': float(m.get('own_party_cr', 0) or 0),
            'own_party_dr': float(m.get('own_party_dr', 0) or 0),
            'related_party_cr': float(m.get('related_party_cr', 0) or 0),
            'related_party_dr': float(m.get('related_party_dr', 0) or 0),
            'reversal_cr': float(m.get('reversal_cr', 0) or 0),
            'loan_disbursement_cr': float(m.get('loan_disbursement_cr', 0) or 0),
            'fd_interest_cr': float(m.get('fd_interest_cr', 0) or 0),
            'round_figure_cr': float(m.get('round_figure_cr', 0) or 0),
            'high_value_cr': float(m.get('high_value_cr', 0) or 0),
            'cash_deposits_amount': float(m.get('cash_deposits_amount', 0) or 0),
            'cash_withdrawals_amount': float(m.get('cash_withdrawals_amount', 0) or 0),
            'cheque_deposits_amount': float(m.get('cheque_deposits_amount', 0) or 0),
            'cheque_issues_amount': float(m.get('cheque_issues_amount', 0) or 0),
            'loan_repayment_dr': float(m.get('loan_repayment_dr', 0) or 0),
            'salary_paid': float(m.get('salary_paid', 0) or 0),
            'statutory_epf': float(m.get('statutory_epf', 0) or 0),
            'statutory_socso': float(m.get('statutory_socso', 0) or 0),
            'statutory_tax': float(m.get('statutory_tax', 0) or 0),
            'statutory_hrdf': float(m.get('statutory_hrdf', 0) or 0),
            'returned_cheques_outward_count': m.get('returned_cheques_outward_count', 0),
            'returned_cheques_outward_amount': float(m.get('returned_cheques_outward_amount', 0) or 0),
            'eod_lowest': lowest,
            'eod_highest': highest,
            'eod_average': (highest + lowest) / 2.0 if (highest or lowest) else 0.0,
            'opening_balance': float(m.get('opening_balance', 0) or 0),
            'closing_balance': float(m.get('ending_balance', 0) or 0),
            'transaction_count': m.get('transaction_count', 0),
            'fx_credit_amount': 0,
            'fx_debit_amount': 0,
            'fx_credit_count': 0,
            'fx_debit_count': 0,
            'fx_currencies': [],
        })

    gross_credits = sum(float(t.get('credit', 0) or 0) for t in transactions)
    gross_debits = sum(float(t.get('debit', 0) or 0) for t in transactions)
    
    consolidated = {
    'gross_credits': round(gross_credits, 2),
    'gross_debits': round(gross_debits, 2),
    'net_credits': round(gross_credits, 2),
    'net_debits': round(gross_debits, 2),
    'annualized_net_credits': round(gross_credits * 12 / len(monthly_summary), 2) if monthly_summary else 0,
    'annualized_net_debits': round(gross_debits * 12 / len(monthly_summary), 2) if monthly_summary else 0,
    'eod_lowest': 0,
    'eod_highest': 0,
    'eod_average': 0,
    'data_completeness': 'COMPLETE',
    'high_value_threshold': high_value_threshold,  # Add this line
    'large_credit_threshold': high_value_threshold,  # Add alias for compatibility
    }
    
    return {
        'report_info': report_info,
        'accounts': accounts,
        'monthly_analysis': monthly_analysis,
        'consolidated': consolidated,
        'top_parties': {'top_payers': [], 'top_payees': []},
        'large_credits': [],
        'large_transactions': [],
        'own_related_transactions': {'transactions': [], 'summary': {}},
        'loan_transactions': {'transactions': [], 'summary': {}},
        'flags': {'indicators': []},
        'observations': {'positive': [], 'concerns': []},
        'parsing_metadata': {},
        'counterparty_ledger': cp_ledger,
        'pdf_integrity': pdf_integrity,
    }

def _top_parties_from_transaction_analysis(transaction_analysis: dict) -> dict:
    top_payers = transaction_analysis.get("top_payers") or transaction_analysis.get("top_creditors")
    top_payees = transaction_analysis.get("top_payees") or transaction_analysis.get("top_debtors")

    if not top_payers:
        top_payers = [
            {
                "rank": idx,
                "party_name": row.get("party"),
                "total_amount": row.get("total_credit", 0),
                "transaction_count": row.get("credit_tx_count", 0),
            }
            for idx, row in enumerate(transaction_analysis.get("top_credit_parties", []), start=1)
        ]

    if not top_payees:
        top_payees = [
            {
                "rank": idx,
                "party_name": row.get("party"),
                "total_amount": row.get("total_debit", 0),
                "transaction_count": row.get("debit_tx_count", 0),
            }
            for idx, row in enumerate(transaction_analysis.get("top_debit_parties", []), start=1)
        ]

    return {"top_payers": top_payers or [], "top_payees": top_payees or []}


def build_report_data_from_analysis(
    transactions: List[dict],
    monthly_summary: List[dict],
    transaction_analysis: dict,
    high_value_threshold: float,
) -> dict:
    """Build the v6 report payload shared by HTML and Excel exports."""
    # Get pdf_integrity from session state
    pdf_integrity = st.session_state.get("integrity_analysis_results", {})
    
    # Ensure threshold is a float and has the correct value
    threshold = float(high_value_threshold) if high_value_threshold else 100000.0
    
    data = {
        'transactions': transactions,
        'monthly_summary': monthly_summary,
        'summary': {
            'company_names': list(set(t.get('company_name', '') for t in transactions if t.get('company_name'))),
            'date_range': '',
            'high_value_threshold': threshold,
        },
        'counterparty_ledger': transaction_analysis.get('counterparty_ledger', {}),
        'pdf_integrity': pdf_integrity,
    }

    adapted_data = adapt_to_v6(data)
    adapted_data['transactions'] = transactions
    adapted_data['top_parties'] = _top_parties_from_transaction_analysis(transaction_analysis)
    
    # IMPORTANT: Build large transactions directly from transactions with correct threshold
    adapted_data['large_transactions'] = build_large_transactions(transactions, threshold)
    adapted_data['large_credits'] = transaction_analysis.get('high_value_credits', [])
    
    adapted_data['flags'] = transaction_analysis.get('flags', {'indicators': []})
    adapted_data['observations'] = transaction_analysis.get('observations', {'positive': [], 'concerns': []})
    adapted_data['round_figure_credits'] = transaction_analysis.get('round_figure_credits', [])
    adapted_data['loan_transactions'] = transaction_analysis.get(
        'loan_transactions',
        adapted_data.get('loan_transactions', {'transactions': [], 'summary': {}}),
    )
    adapted_data['own_related_transactions'] = transaction_analysis.get(
        'own_related_transactions',
        adapted_data.get('own_related_transactions', {'transactions': [], 'summary': {}}),
    )
    adapted_data['unclassified_transactions'] = transaction_analysis.get('unclassified_transactions', [])
    adapted_data['classification_config'] = transaction_analysis.get('classification_config', {})
    adapted_data['parsing_metadata'] = transaction_analysis.get(
        'parsing_metadata',
        adapted_data.get('parsing_metadata', {}),
    )
    adapted_data['pdf_integrity'] = pdf_integrity
    
    # IMPORTANT: Add threshold to consolidated for display
    if 'consolidated' in adapted_data:
        adapted_data['consolidated']['high_value_threshold'] = threshold
        # Also store as large_credit_threshold for consistency
        adapted_data['consolidated']['large_credit_threshold'] = threshold
    
    return adapted_data

def normalize_report_data_for_export(data: dict) -> dict:
    """Normalize uploaded JSON or v6 payloads for HTML/XLSX report exports."""
    if not isinstance(data, dict):
        return {}

    source = dict(data)
    transaction_analysis = source.get("transaction_analysis", {})
    if isinstance(transaction_analysis, dict):
        source.setdefault("counterparty_ledger", transaction_analysis.get("counterparty_ledger", {}))

    if "monthly_analysis" not in source and "transactions" in source:
        normalized = adapt_to_v6(source)
        normalized["transactions"] = source.get("transactions", [])
    else:
        normalized = dict(source)

    if isinstance(transaction_analysis, dict):
        normalized["top_parties"] = _top_parties_from_transaction_analysis(transaction_analysis)
        normalized["large_credits"] = transaction_analysis.get(
            "high_value_credits",
            normalized.get("large_credits", []),
        )
        normalized["flags"] = transaction_analysis.get("flags", normalized.get("flags", {"indicators": []}))
        normalized["observations"] = transaction_analysis.get(
            "observations",
            normalized.get("observations", {"positive": [], "concerns": []}),
        )

    normalized.setdefault("accounts", [])
    normalized.setdefault("monthly_analysis", [])
    normalized.setdefault("consolidated", {})
    normalized.setdefault("top_parties", {"top_payers": [], "top_payees": []})
    normalized.setdefault("large_credits", [])
    normalized.setdefault("own_related_transactions", {"transactions": [], "summary": {}})
    normalized.setdefault("loan_transactions", {"transactions": [], "summary": {}})
    normalized.setdefault("flags", {"indicators": []})
    normalized.setdefault("observations", {"positive": [], "concerns": []})
    normalized.setdefault("counterparty_ledger", {})
    normalized.setdefault("parsing_metadata", {})
    return normalized


def _excel_safe_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Period):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _records_to_excel_df(records, columns: List[str] | None = None) -> pd.DataFrame:
    safe_records = [
        {key: _excel_safe_value(val) for key, val in dict(record).items()}
        for record in (records or [])
        if isinstance(record, dict)
    ]
    df = pd.DataFrame(safe_records)
    if columns and df.empty:
        df = pd.DataFrame(columns=columns)
    return df


def _write_excel_sheet(writer, sheet_name: str, df: pd.DataFrame, title: str | None = None) -> None:
    workbook = writer.book
    safe_sheet_name = sheet_name[:31]
    startrow = 2 if title else 0
    
    # Convert DataFrame to have proper string columns for Excel
    df_to_write = df.copy()
    
    # Convert all columns to string for safe processing, but keep numeric ones numeric
    for col in df_to_write.columns:
        # Check if column contains numeric data (float/int)
        if pd.api.types.is_numeric_dtype(df_to_write[col]):
            # Keep numeric columns as is
            continue
        else:
            # Convert non-numeric columns to string, handling None/NaN
            df_to_write[col] = df_to_write[col].apply(
                lambda x: str(x) if x is not None and pd.notna(x) else ""
            )
    
    df_to_write.to_excel(writer, sheet_name=safe_sheet_name, startrow=startrow, index=False)
    worksheet = writer.sheets[safe_sheet_name]

    header_format = workbook.add_format(
        {"bold": True, "font_color": "white", "bg_color": "#1B4F72", "border": 1}
    )
    title_format = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#1B4F72"})
    money_format = workbook.add_format({"num_format": "#,##0.00"})

    if title:
        worksheet.write(0, 0, title, title_format)

    for col_idx, col_name in enumerate(df_to_write.columns):
        worksheet.write(startrow, col_idx, col_name, header_format)
        
        # Safely calculate column width
        try:
            # Get column values as strings safely
            col_values = df_to_write[col_name].astype(str).tolist() if not df_to_write.empty else []
            max_len = len(str(col_name))
            for val in col_values[:200]:  # Limit to first 200 rows for performance
                if val:
                    max_len = max(max_len, len(val))
            # Cap width between 12 and 42
            col_width = min(max(max_len + 2, 12), 42)
        except Exception:
            col_width = 15  # Default fallback width
        
        worksheet.set_column(col_idx, col_idx, col_width)
        
        # Apply money format to amount columns
        if any(token in str(col_name).lower() for token in ("amount", "credit", "debit", "balance", "gross", "net")):
            # Get the column range
            last_row = startrow + len(df_to_write)
            if last_row > startrow:
                worksheet.set_column(col_idx, col_idx, col_width, money_format)

    worksheet.freeze_panes(startrow + 1, 0)
    if not df_to_write.empty:
        worksheet.autofilter(startrow, 0, startrow + len(df_to_write), max(len(df_to_write.columns) - 1, 0))

def generate_excel_report(data: dict) -> BytesIO:
    """Generate a multi-sheet XLSX report matching the HTML report tabs."""
    report_data = normalize_report_data_for_export(data)
    top_parties = report_data.get("top_parties", {}) or {}
    own_related = report_data.get("own_related_transactions", {}) or {}
    if isinstance(own_related, list):
        own_related = {"transactions": own_related, "summary": {}}
    loans = report_data.get("loan_transactions", {}) or {}
    flags = report_data.get("flags", {}) or {}
    cp_ledger = report_data.get("counterparty_ledger", {}) or {}
    parsing = report_data.get("parsing_metadata", {}) or {}
    pdf_integrity = report_data.get("pdf_integrity", {}) or {}

    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        report_info = report_data.get("report_info", {}) or {}
        consolidated = report_data.get("consolidated", {}) or {}
        overview_rows = [
            {"Section": "Report Info", "Metric": key, "Value": _excel_safe_value(value)}
            for key, value in report_info.items()
        ]
        overview_rows.extend(
            {"Section": "Consolidated", "Metric": key, "Value": _excel_safe_value(value)}
            for key, value in consolidated.items()
        )
        _write_excel_sheet(writer, "Overview", pd.DataFrame(overview_rows), "Report Overview")

        _write_excel_sheet(
            writer,
            "Accounts",
            _records_to_excel_df(report_data.get("accounts", [])),
            "Account Details",
        )
        _write_excel_sheet(
            writer,
            "Cash Flow",
            _records_to_excel_df(report_data.get("monthly_analysis", [])),
            "Monthly Cash Flow",
        )

        party_rows = []
        for party_type, parties in (
            ("Top Payer", top_parties.get("top_payers") or top_parties.get("top_creditors") or []),
            ("Top Payee", top_parties.get("top_payees") or top_parties.get("top_debtors") or []),
        ):
            for idx, row in enumerate(parties, start=1):
                party_rows.append({"type": party_type, "rank": row.get("rank", idx), **row})
        _write_excel_sheet(writer, "Top Parties", _records_to_excel_df(party_rows), "Top Parties")

        large_txns = report_data.get("large_transactions", [])
        if not large_txns:
            large_txns = report_data.get("large_credits", [])  # Fallback
            
        _write_excel_sheet(
            writer,
            "Large Transactions",
            _records_to_excel_df(large_txns),
            f"Large Transactions (≥ RM {report_data.get('consolidated', {}).get('high_value_threshold', 100000):,.0f})",
        )

        cp_rows = _records_to_excel_df(cp_ledger.get("counterparties", []))
        if "transactions" in cp_rows.columns:
            cp_rows = cp_rows.drop(columns=["transactions"])
        _write_excel_sheet(writer, "Counterparty", cp_rows, "Counterparty Ledger")

        cp_txn_rows = []
        for cp in cp_ledger.get("counterparties", []) or []:
            for txn in cp.get("transactions", []) or []:
                cp_txn_rows.append({"counterparty": cp.get("counterparty_name", ""), **txn})
        if not cp_txn_rows:
            cp_txn_rows = own_related.get("transactions", []) or []
        _write_excel_sheet(writer, "Counterparty Txns", _records_to_excel_df(cp_txn_rows), "Counterparty Transactions")

        loan_rows = []
        for txn_type, txns in (
            ("Disbursement", loans.get("disbursements", [])),
            ("Repayment", loans.get("repayments", [])),
            ("Transaction", loans.get("transactions", [])),
        ):
            for row in txns or []:
                loan_rows.append({"facility_type": txn_type, **row})
        _write_excel_sheet(writer, "Facilities", _records_to_excel_df(loan_rows), "Facilities")

        _write_excel_sheet(
            writer,
            "Risk Signals",
            _records_to_excel_df(flags.get("indicators", [])),
            "Risk Signals",
        )
        _write_excel_sheet(
            writer,
            "Round Figures",
            _records_to_excel_df(report_data.get("round_figure_credits", [])),
            "Round Figure Credits",
        )

        fx_rows = [
            row for row in report_data.get("monthly_analysis", [])
            if any(row.get(key) for key in ("fx_credit_count", "fx_credit_amount", "fx_debit_count", "fx_debit_amount"))
        ]
        if fx_rows:
            _write_excel_sheet(writer, "FX Remittance", _records_to_excel_df(fx_rows), "FX / Remittance")

        unclassified_rows = report_data.get("unclassified_transactions", []) or []
        if unclassified_rows:
            _write_excel_sheet(writer, "Unclassified", _records_to_excel_df(unclassified_rows), "Unclassified Transactions")

        if parsing:
            _write_excel_sheet(
                writer,
                "Parsing QC",
                _records_to_excel_df(parsing.get("account_month_checks", [])),
                "Parsing Quality Checks",
            )
            if parsing.get("extraction_gaps"):
                _write_excel_sheet(
                    writer,
                    "Extraction Gaps",
                    _records_to_excel_df(parsing.get("extraction_gaps", [])),
                    "Extraction Gaps",
                )

        if pdf_integrity:
            integrity_rows = []
            for file_name, result in pdf_integrity.items():
                if isinstance(result, dict):
                    integrity_rows.append({"file_name": file_name, **result})
            _write_excel_sheet(writer, "Fraud Detector", _records_to_excel_df(integrity_rows), "Fraud Detector")

        if report_data.get("transactions"):
            _write_excel_sheet(
                writer,
                "Transactions",
                _records_to_excel_df(report_data.get("transactions", [])),
                "All Transactions",
            )

    output.seek(0)
    return output


def build_track2_counterparty_ledger(transactions: List[dict]) -> dict:
    """Convert raw transaction rows into the counterparty_ledger dict shape
    that build_track2_result (kredit_lab_classify_track2) expects.

    Schema: {"counterparties": [{"counterparty_name": str,
                                  "transaction_count": int,
                                  "credit_count": int,
                                  "debit_count": int,
                                  "total_credits": float,
                                  "total_debits": float,
                                  "net_position": float,
                                  "transactions": [{"date", "description",
                                                    "amount", "type"}]
                                 }]}
    """
    from collections import defaultdict

    buckets: dict = defaultdict(lambda: {
        "total_credits": 0.0,
        "total_debits": 0.0,
        "credit_count": 0,
        "debit_count": 0,
        "transactions": [],
    })

    for tx in transactions:
        # Resolve counterparty name using the existing helper
        desc = tx.get("description", "")
        bank = str(tx.get("bank", "") or "").upper()
        name = ""
        for col in ("party_name", "counterparty", "counterparty_name",
                    "party", "merchant", "recipient", "beneficiary"):
            v = tx.get(col)
            if v and str(v).strip() and str(v).upper() not in (
                "", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "-"
            ):
                name = str(v).strip().upper()
                break
        if not name and "CIMB" in bank:
            try:
                extracted = extract_cimb_party_name(desc)
                if extracted and str(extracted).strip():
                    name = str(extracted).strip().upper()
            except Exception:
                pass
        if not name:
            name = "UNKNOWN"

        bucket = buckets[name]
        credit = float(tx.get("credit") or 0)
        debit = float(tx.get("debit") or 0)
        if credit > 0:
            bucket["total_credits"] += credit
            bucket["credit_count"] += 1
            bucket["transactions"].append({
                "date": tx.get("date", ""),
                "description": desc,
                "amount": round(credit, 2),
                "type": "CREDIT",
            })
        if debit > 0:
            bucket["total_debits"] += debit
            bucket["debit_count"] += 1
            bucket["transactions"].append({
                "date": tx.get("date", ""),
                "description": desc,
                "amount": round(debit, 2),
                "type": "DEBIT",
            })

    counterparties = []
    for name, b in buckets.items():
        if not b["transactions"]:
            continue
        cr = round(b["total_credits"], 2)
        dr = round(b["total_debits"], 2)
        counterparties.append({
            "counterparty_name": name,
            "transaction_count": b["credit_count"] + b["debit_count"],
            "credit_count": b["credit_count"],
            "debit_count": b["debit_count"],
            "total_credits": cr,
            "total_debits": dr,
            "net_position": round(cr - dr, 2),
            "transactions": b["transactions"],
        })

    counterparties.sort(
        key=lambda c: abs(c["total_credits"] - c["total_debits"]), reverse=True
    )
    return {"counterparties": counterparties, "total_counterparties": len(counterparties)}


def generate_html_report_from_data(transactions: List[dict], monthly_summary: List[dict], 
                                   transaction_analysis: dict, high_value_threshold: float) -> str:
    """Generate interactive HTML report from parsed transactions."""
    # Get pdf_integrity from session state
    pdf_integrity = st.session_state.get("integrity_analysis_results") or {}
    
    # Ensure threshold is a float
    threshold = float(high_value_threshold) if high_value_threshold else 100000.0
    
    if _TRACK2_AVAILABLE:
        try:
            # Build counterparty ledger in the format Track 2 expects
            cp_ledger = build_track2_counterparty_ledger(transactions)

            # Collect company names and account meta from session state
            company_names = list({
                str(t.get("company_name", "") or "").strip()
                for t in transactions
                if t.get("company_name")
            })
            override = (st.session_state.get("company_name_override") or "").strip()
            if override and override not in company_names:
                company_names.insert(0, override)

            # Account-type determinations
            determinations = st.session_state.get("account_type_determinations") or []
            account_meta = account_meta_from_determinations(determinations)

            # Analyst-supplied related parties
            related_parties = st.session_state.get("related_parties_override") or []

            data = build_track2_result(
                transactions,
                counterparty_ledger=cp_ledger,
                pdf_integrity=pdf_integrity if pdf_integrity else None,
                company_names=company_names or None,
                related_parties=related_parties or None,
                account_meta=account_meta or None,
            )
            
            # Ensure the threshold is set in the consolidated data
            if 'consolidated' in data:
                data['consolidated']['high_value_threshold'] = threshold
                data['consolidated']['large_credit_threshold'] = threshold
            
            # Ensure large_transactions is populated
            if 'large_transactions' not in data or not data['large_transactions']:
                data['large_transactions'] = build_large_transactions(transactions, threshold)
            
            return generate_interactive_html(data)
        except Exception as _track2_err:
            import traceback
            print(f"[Track2] build_track2_result failed, falling back: {_track2_err}")
            traceback.print_exc()

    # Legacy fallback path
    report_data = build_report_data_from_analysis(
        transactions,
        monthly_summary,
        transaction_analysis,
        threshold,
    )
    report_data['pdf_integrity'] = pdf_integrity
    return generate_interactive_html(report_data)

# ============================================================
# HTML REPORT GENERATION FUNCTIONS (ADDED)
# ============================================================

def fmt(val, decimals=2):
    """Format number with commas"""
    if val is None:
        return "0.00"
    return f"{val:,.{decimals}f}"

def normalize_observations(obs):
    """Coerce observations into {'positive': [...], 'concerns': [...]}."""
    if isinstance(obs, dict):
        return {'positive': list(obs.get('positive', []) or []),
                'concerns': list(obs.get('concerns', []) or [])}
    pos, con = [], []
    if isinstance(obs, list):
        for item in obs:
            if isinstance(item, str):
                con.append(item)
            elif isinstance(item, dict):
                kind = str(item.get('type') or item.get('category') or item.get('sentiment') or '').lower()
                text = item.get('text') or item.get('observation') or item.get('message') or item.get('description') or ''
                if not text:
                    continue
                if kind in ('positive', 'pos', 'good', 'strength'):
                    pos.append(text)
                else:
                    con.append(text)
    return {'positive': pos, 'concerns': con}

def adapt_to_v6_basic_unused(src):
    """Reshape flat extractor output into v6.3.3 renderer schema."""
    from collections import defaultdict

    summary = src.get('summary', {}) or {}
    transactions = src.get('transactions', []) or []
    monthly_summary = src.get('monthly_summary', []) or []
    cp_ledger = src.get('counterparty_ledger', {}) or {}
    pdf_integrity = src.get('pdf_integrity')

    report_info = {
        'company_name': summary.get('company_names', ['Unknown'])[0] if summary.get('company_names') else 'Unknown',
        'schema_version': '6.3.3',
        'period_start': '',
        'period_end': '',
        'total_months': len(monthly_summary),
        'related_parties': [],
    }

    # accounts aggregation
    acc_map = defaultdict(lambda: {'credits': 0.0, 'debits': 0.0, 'txn_count': 0, 'bank': '', 'last_bal': None, 'opening_bal': None})
    for t in transactions:
        an = t.get('account_no', '')
        if not an:
            continue
        a = acc_map[an]
        a['txn_count'] += 1
        cr = float(t.get('credit', 0) or 0)
        dr = float(t.get('debit', 0) or 0)
        a['credits'] += cr
        a['debits'] += dr
        if not a['bank']:
            a['bank'] = t.get('bank', '') or ''
        bal = t.get('balance')
        if isinstance(bal, (int, float)):
            if a['opening_bal'] is None:
                a['opening_bal'] = bal - cr + dr
            a['last_bal'] = bal
    
    accounts = []
    for an, a in sorted(acc_map.items()):
        accounts.append({
            'bank_name': a['bank'],
            'account_number': an,
            'account_holder': report_info['company_name'],
            'account_type': 'Current',
            'opening_balance': round(a['opening_bal'] or 0.0, 2),
            'closing_balance': round(a['last_bal'] or 0.0, 2),
            'total_credits': round(a['credits'], 2),
            'total_debits': round(a['debits'], 2),
            'transaction_count': a['txn_count'],
        })

    monthly_analysis = []
    for m in monthly_summary:
        monthly_analysis.append({
            'month': m.get('month', ''),
            'bank_name': '',
            'account_number': m.get('account_no', ''),
            'gross_credits': float(m.get('total_credit', 0) or 0),
            'gross_debits': float(m.get('total_debit', 0) or 0),
            'net_credits': float(m.get('total_credit', 0) or 0),
            'net_debits': float(m.get('total_debit', 0) or 0),
            'eod_lowest': float(m.get('lowest_balance', 0) or 0),
            'eod_highest': float(m.get('highest_balance', 0) or 0),
            'eod_average': (float(m.get('highest_balance', 0) or 0) + float(m.get('lowest_balance', 0) or 0)) / 2.0,
            'opening_balance': float(m.get('opening_balance', 0) or 0),
            'closing_balance': float(m.get('ending_balance', 0) or 0),
            'transaction_count': m.get('transaction_count', 0),
        })

    gross_credits = sum(float(t.get('credit', 0) or 0) for t in transactions)
    gross_debits = sum(float(t.get('debit', 0) or 0) for t in transactions)
    total_months = len(monthly_summary) or 1
    
    consolidated = {
        'gross_credits': round(gross_credits, 2),
        'gross_debits': round(gross_debits, 2),
        'net_credits': round(gross_credits, 2),
        'net_debits': round(gross_debits, 2),
        'annualized_net_credits': round(gross_credits * 12 / total_months, 2),
        'annualized_net_debits': round(gross_debits * 12 / total_months, 2),
        'eod_lowest': 0,
        'eod_highest': 0,
        'eod_average': 0,
        'data_completeness': 'COMPLETE',
    }

    return {
        'report_info': report_info,
        'accounts': accounts,
        'monthly_analysis': monthly_analysis,
        'consolidated': consolidated,
        'top_parties': {'top_payers': [], 'top_payees': []},
        'large_credits': [],
        'own_related_transactions': {'transactions': [], 'summary': {}},
        'loan_transactions': {'transactions': [], 'summary': {}},
        'flags': {'indicators': []},
        'observations': {'positive': [], 'concerns': []},
        'parsing_metadata': {},
        'counterparty_ledger': cp_ledger,
        'pdf_integrity': pdf_integrity,
    }

def generate_interactive_html_basic_unused(data):
    """Generate interactive HTML report for v6 schema"""
    # Simplified version - you can import the full function from your other file
    # For now, create a basic HTML report
    r = data.get('report_info', {})
    accounts = data.get('accounts', [])
    consol = data.get('consolidated', {})
    
    company = r.get('company_name', 'Company')
    period_start = r.get('period_start', '')
    period_end = r.get('period_end', '')
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kredit Lab — {company}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }}
        h1 {{ color: #1B4F72; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
        .card {{ background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #1B4F72; }}
        .card .value {{ font-size: 24px; font-weight: bold; }}
        .card .label {{ color: #666; font-size: 12px; text-transform: uppercase; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #1B4F72; color: white; }}
        .credit {{ color: #059669; }}
        .debit {{ color: #dc2626; }}
        .footer {{ text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔬 Kredit Lab — Statement Intelligence Report</h1>
        <p><strong>{company}</strong> | Period: {period_start} to {period_end}</p>
        
        <div class="summary">
            <div class="card"><div class="value">RM {consol.get('net_credits', 0):,.0f}</div><div class="label">Net Credits</div></div>
            <div class="card"><div class="value">RM {consol.get('net_debits', 0):,.0f}</div><div class="label">Net Debits</div></div>
            <div class="card"><div class="value">RM {consol.get('annualized_net_credits', 0):,.0f}</div><div class="label">Annualized</div></div>
            <div class="card"><div class="value">{len(accounts)}</div><div class="label">Accounts</div></div>
        </div>
        
        <h2>Account Summary</h2>
        <table>
            <thead><tr><th>Bank</th><th>Account No</th><th>Opening Balance</th><th>Closing Balance</th><th>Total Credits</th><th>Total Debits</th></tr></thead>
            <tbody>'''
    for acc in accounts:
        html += f'''<tr>
            <td>{acc.get('bank_name', '')}</td>
            <td>{acc.get('account_number', '')}</td>
            <td class="credit">RM {acc.get('opening_balance', 0):,.2f}</td>
            <td class="credit">RM {acc.get('closing_balance', 0):,.2f}</td>
            <td class="credit">RM {acc.get('total_credits', 0):,.2f}</td>
            <td class="debit">RM {acc.get('total_debits', 0):,.2f}</td>
        </tr>'''
    html += f'''</tbody>
        </table>
        <div class="footer">
            <p>Generated by Kredit Lab Statement Intelligence | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div>
</body>
</html>'''
    return html

def generate_html_report_from_data_basic_unused(transactions: List[dict], monthly_summary: List[dict], 
                                                transaction_analysis: dict, high_value_threshold: float) -> str:
    """Generate interactive HTML report from parsed transactions"""
    # Convert transactions to v6.3.3 schema format
    data = adapt_to_v6({
        'transactions': transactions,
        'monthly_summary': monthly_summary,
        'summary': {
            'date_range': f"{transactions[0].get('date', '')} to {transactions[-1].get('date', '')}" if transactions else '',
            'company_names': list(set(t.get('company_name', '') for t in transactions if t.get('company_name')))
        },
        'counterparty_ledger': transaction_analysis.get('counterparty_ledger', {}),
        'pdf_integrity': st.session_state.get('integrity_analysis_results', {})
    })
    
    # Generate HTML report
    return generate_interactive_html(data)

def convert_json_to_html_basic_unused(json_file) -> str:
    """Convert uploaded JSON analysis file to HTML report"""
    data = json.load(json_file)
    
    # Adapt to v6 schema if needed
    if isinstance(data, dict) and 'monthly_analysis' not in data and 'transactions' in data:
        data = adapt_to_v6(data)
    
    return generate_interactive_html(data)


# ============================================================
# END OF ADDED FUNCTIONS
# ============================================================


st.set_page_config(page_title="Bank Statement Parser", layout="wide")
st.markdown(
    '<h1>📄 Bank Statement Parser (Multi-File Support)</h1>',
    unsafe_allow_html=True,
)
st.write("Upload one or more bank statement PDFs to extract transactions.")


st.markdown(
    """
    <style>
    :root {
        --kl-accent: #0078D4;
        --kl-accent-hover: #00A8A8;
        --kl-label: #CCCCCC;
    }

    h1, h2, h3 {
        color: #FFFFFF;
    }

    h1 {
        margin-bottom: 0.35rem;
    }

    [data-testid="stMarkdownContainer"] p {
        margin-bottom: 0.2rem;
    }

    div[data-testid="stSelectbox"] {
        margin-top: -0.25rem;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        min-height: 54px !important;
        align-items: center !important;
        padding-left: 15px !important;
        padding-right: 32px !important;
        border-radius: 8px !important;
        background: #262730 !important;
        border: 1.5px solid #334155 !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] div {
        font-size: 16px !important;
        font-weight: 500 !important;
        line-height: 22px !important;
        -webkit-font-smoothing: antialiased !important;
        -moz-osx-font-smoothing: grayscale !important;
        text-rendering: optimizeLegibility !important;
        color: #F3F4F6 !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] span,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] input {
        font-size: 19px !important;
        font-weight: 600 !important;
        line-height: 22px !important;
        -webkit-font-smoothing: antialiased !important;
        -moz-osx-font-smoothing: grayscale !important;
        text-rendering: optimizeLegibility !important;
        color: #F3F4F6 !important;
    }

    div[data-testid="stSelectbox"] [data-baseweb="tag"] {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }

    div[data-testid="stSelectbox"] svg {
        width: 16px !important;
        height: 16px !important;
        fill: #9CA3AF !important;
    }

    div[role="listbox"],
    ul[role="listbox"] {
        padding: 0 !important;
        border: 0 !important;
        border-radius: 0 !important;
        background: #0B0F16 !important;
        box-shadow: none !important;
    }

    div[role="option"],
    li[role="option"] {
        min-height: 50px !important;
        padding: 0 20px !important;
        border: 0 !important;
        border-radius: 0 !important;
        color: #F3F4F6 !important;
        font-size: 15px !important;
        font-weight: 400 !important;
        line-height: 22px !important;
        -webkit-font-smoothing: antialiased !important;
        -moz-osx-font-smoothing: grayscale !important;
        text-rendering: optimizeLegibility !important;
        display: flex !important;
        align-items: center !important;
    }

    div[role="option"] *,
    li[role="option"] * {
        color: inherit !important;
        font-size: inherit !important;
        font-weight: inherit !important;
        line-height: inherit !important;
        background: transparent !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }

    div[role="option"]:hover,
    li[role="option"]:hover,
    div[role="option"][aria-selected="true"],
    li[role="option"][aria-selected="true"] {
        background: #2A2B34 !important;
        color: #FFFFFF !important;
    }

    [data-testid="stWidgetLabel"] label,
    [data-testid="stWidgetLabel"] p,
    [data-testid="stFileUploader"] label {
        color: var(--kl-label);
        font-weight: 600;
    }

    div.stButton {
        text-align: left;
    }

    div.stButton > button {
        min-height: 3rem;
        width: 100%;
        padding: 0.7rem 1.35rem;
        border-radius: 8px !important;
        font-weight: 600;
        letter-spacing: 0;
        transition: background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease, color 160ms ease;
    }

    div.stButton > button[kind="primary"],
    div.stButton > button[type="primary"] {
        border: 1px solid #0052CC !important;
        background: linear-gradient(135deg, #0078D4 0%, #0052CC 100%) !important;
        color: #FFFFFF !important;
        box-shadow: 0 8px 18px rgba(0, 102, 255, 0.22) !important;
    }

    div.stButton > button[kind="primary"]:hover,
    div.stButton > button[type="primary"]:hover {
        border-color: #1A75FF !important;
        background: linear-gradient(135deg, #1A75FF 0%, #0066FF 100%) !important;
        box-shadow: 0 10px 24px rgba(0, 102, 255, 0.34) !important;
        transform: translateY(-1px);
    }

    div.stButton > button[kind="primary"]:active,
    div.stButton > button[type="primary"]:active {
        box-shadow: 0 4px 10px rgba(0, 102, 255, 0.22) !important;
        transform: translateY(1px);
    }

    div.stButton > button:not([kind="primary"]):not([type="primary"]) {
        border: 1px solid #374151 !important;
        background: #111827 !important;
        color: #D1D5DB !important;
        box-shadow: 0 6px 14px rgba(0, 0, 0, 0.16) !important;
    }

    div.stButton > button:not([kind="primary"]):not([type="primary"]):hover {
        border-color: #9CA3AF !important;
        background: #1F2937 !important;
        color: #FFFFFF !important;
        box-shadow: 0 8px 18px rgba(0, 0, 0, 0.22) !important;
        transform: translateY(-1px);
    }

    div.stButton > button:not([kind="primary"]):not([type="primary"]):active {
        box-shadow: 0 3px 8px rgba(0, 0, 0, 0.2) !important;
        transform: translateY(1px);
    }

    section[data-testid="stFileUploaderDropzone"],
    div[data-testid="stFileUploaderDropzone"],
    div[data-testid="stTextInput"] div[data-baseweb="input"] {
        background: #262730 !important;
        border: 1.5px solid #334155 !important;
        border-radius: 8px !important;
        box-sizing: border-box;
    }

    section[data-testid="stFileUploaderDropzone"]:hover,
    div[data-testid="stFileUploaderDropzone"]:hover,
    div[data-testid="stTextInput"] div[data-baseweb="input"]:hover {
        border-color: #475569 !important;
    }

    div[data-testid="stTextInput"] input {
        background: transparent !important;
        color: #F3F4F6 !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    div[data-testid="stTextInput"] div[data-baseweb="input"] {
        min-height: 3rem;
        box-sizing: border-box;
    }

    div[data-testid="stHorizontalBlock"] > div:nth-child(2) div.stButton > button:hover {
        border-color: #EF4444 !important;
        background: rgba(239, 68, 68, 0.08) !important;
        color: #C4B5FD !important;
        box-shadow: 0 8px 18px rgba(239, 68, 68, 0.16) !important;
    }

    div[data-testid="stHorizontalBlock"] > div:nth-child(2) div.stButton > button:active {
        background: rgba(239, 68, 68, 0.14) !important;
        transform: translateY(1px);
    }

    .kl-progress-panel {
        margin-top: 0.75rem;
        padding: 0.95rem 1rem;
        border: 1px solid rgba(59, 130, 246, 0.36);
        border-radius: 8px;
        background: rgba(15, 23, 42, 0.72);
        box-shadow: 0 0 8px rgba(0, 123, 255, 0.24);
    }

    .kl-progress-panel.success {
        border-color: rgba(34, 197, 94, 0.38);
        background: rgba(20, 83, 45, 0.55);
        box-shadow: 0 0 8px rgba(40, 167, 69, 0.28);
    }

    .kl-progress-panel.warning {
        border-color: rgba(250, 204, 21, 0.38);
        background: rgba(113, 63, 18, 0.42);
        box-shadow: 0 0 8px rgba(250, 204, 21, 0.18);
    }

    .kl-progress-panel.error {
        border-color: rgba(248, 113, 113, 0.42);
        background: rgba(127, 29, 29, 0.45);
        box-shadow: 0 0 8px rgba(248, 113, 113, 0.22);
    }

    .kl-progress-topline {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        margin-bottom: 0.45rem;
    }

    .kl-progress-status {
        min-width: 0;
        color: #CBD5E1;
        font-size: 0.88rem;
        font-weight: 500;
        line-height: 1.3;
    }

    .kl-progress-percent {
        flex: 0 0 auto;
        color: #E5F2FF;
        font-size: 0.82rem;
        font-weight: 600;
        line-height: 1.3;
    }

    .kl-progress-filename {
        color: #94A3B8;
        font-size: 0.8rem;
        line-height: 1.35;
        margin-bottom: 0.5rem;
    }

    .kl-progress-track {
        position: relative;
        height: 0.62rem;
        overflow: hidden;
        border-radius: 6px;
        background: #111827;
        border: 1px solid rgba(51, 65, 85, 0.9);
    }

    .kl-progress-fill {
        position: relative;
        height: 100%;
        border-radius: 6px;
        background: linear-gradient(90deg, #007BFF, #00C6FF);
        box-shadow: 0 0 8px rgba(0, 123, 255, 0.4);
        transition: width 0.4s ease-in-out, background-color 0.3s ease, box-shadow 0.3s ease;
        overflow: hidden;
        animation: kl-progress-pulse 1.3s ease-in-out infinite;
    }

    .kl-progress-fill::after {
        content: "";
        position: absolute;
        inset: 0;
        background-image: linear-gradient(
            45deg,
            rgba(255, 255, 255, 0.18) 25%,
            transparent 25%,
            transparent 50%,
            rgba(255, 255, 255, 0.18) 50%,
            rgba(255, 255, 255, 0.18) 75%,
            transparent 75%,
            transparent
        );
        background-size: 1rem 1rem;
        animation: kl-progress-stripes 0.9s linear infinite;
    }

    .kl-progress-panel.success .kl-progress-fill {
        background: #28A745;
        box-shadow: 0 0 8px rgba(40, 167, 69, 0.4);
        animation: none;
    }

    .kl-progress-panel.success .kl-progress-fill::after,
    .kl-progress-panel.warning .kl-progress-fill::after,
    .kl-progress-panel.error .kl-progress-fill::after {
        display: none;
    }

    .kl-progress-panel.warning .kl-progress-fill {
        background: linear-gradient(90deg, #F59E0B, #FACC15);
        box-shadow: 0 0 8px rgba(250, 204, 21, 0.28);
    }

    .kl-progress-panel.error .kl-progress-fill {
        background: linear-gradient(90deg, #EF4444, #F97316);
        box-shadow: 0 0 8px rgba(248, 113, 113, 0.32);
    }

    @keyframes kl-progress-stripes {
        from { background-position: 1rem 0; }
        to { background-position: 0 0; }
    }

    @keyframes kl-progress-pulse {
        0%, 100% { filter: brightness(1); }
        50% { filter: brightness(1.12); }
    }

    .kl-analysis-title {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        margin: 0.5rem 0 0.75rem;
        color: #FFFFFF;
        font-size: 1.75rem;
        font-weight: 800;
        line-height: 1.2;
    }

    .kl-analysis-subtitle {
        color: #A9C1DD;
        font-size: 1rem;
        margin: 0 0 1.25rem;
    }

    .kl-metric-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.875rem;
        margin-bottom: 0.875rem;
    }

    .kl-metric-card {
        min-height: 6rem;
        padding: 1rem 1.15rem;
        border: 1px solid #334155;
        border-radius: 12px;
        background: #0B111D;
        box-sizing: border-box;
    }

    .kl-metric-card-wide {
        grid-column: 1 / -1;
    }

    .kl-metric-label {
        color: #D7E3F8;
        font-size: 0.92rem;
        font-weight: 600;
        margin-bottom: 0.55rem;
    }

    .kl-metric-value {
        color: #FFFFFF;
        font-size: 2.25rem;
        line-height: 1;
        font-weight: 800;
    }

    .kl-pattern-details-heading {
        color: #FFFFFF;
        font-size: 1.5rem;
        font-weight: 500;
        line-height: 1.2;
        margin: 0.25rem 0 1rem;
    }

    div[data-testid="stExpander"] {
        border: 1px solid #374151 !important;
        border-radius: 8px !important;
        background: #0B0F16 !important;
        overflow: hidden !important;
    }

    div[data-testid="stExpander"] details {
        background: transparent !important;
        border: 0 !important;
    }

    div[data-testid="stExpander"] details > summary {
        min-height: 3.25rem !important;
        padding: 0.8rem 1.25rem !important;
        position: relative !important;
        display: flex !important;
        align-items: center !important;
        list-style: none !important;
        list-style-type: none !important;
        font-weight: 500 !important;
    }

    div[data-testid="stExpander"] details > summary::marker {
        content: "" !important;
        color: transparent !important;
        font-size: 0 !important;
    }

    div[data-testid="stExpander"] details > summary::-webkit-details-marker {
        display: none !important;
        color: transparent !important;
        font-size: 0 !important;
    }

    div[data-testid="stExpander"] details > summary [data-testid="stExpanderToggleIcon"],
    div[data-testid="stExpander"] details > summary > svg:first-child,
    div[data-testid="stExpander"] details > summary > span:first-child:has(svg),
    div[data-testid="stExpander"] details > summary > div:first-child:has(svg),
    div[data-testid="stExpander"] details > summary > div:first-child:has([data-testid="stExpanderToggleIcon"]) {
        display: flex !important;
        order: 2 !important;
        position: static !important;
        width: 1rem !important;
        height: 1rem !important;
        margin: 0 0 0 auto !important;
        padding: 0 !important;
        transform: none !important;
    }

    div[data-testid="stExpander"] details > summary [data-testid="stExpanderToggleIcon"] svg,
    div[data-testid="stExpander"] details > summary > svg:first-child,
    div[data-testid="stExpander"] details > summary > span:first-child:has(svg) svg,
    div[data-testid="stExpander"] details > summary > div:first-child:has(svg) svg,
    div[data-testid="stExpander"] details > summary > div:first-child:has([data-testid="stExpanderToggleIcon"]) svg {
        display: block !important;
        width: 1rem !important;
        height: 1rem !important;
        color: #F3F4F6 !important;
        fill: currentColor !important;
    }

    div[data-testid="stExpander"] details > summary > [data-testid="stMarkdownContainer"],
    div[data-testid="stExpander"] details > summary [data-testid="stExpanderToggleIcon"] + [data-testid="stMarkdownContainer"] {
        order: 1 !important;
        flex: 1 1 auto !important;
        margin-left: 0 !important;
    }

    div[data-testid="stExpander"] details > summary::after {
        content: none !important;
        display: none !important;
    }

    div[data-testid="stExpander"] details > summary [data-testid="stMarkdownContainer"] p {
        color: #FFFFFF;
        font-size: 0.95rem;
        font-weight: 500;
        line-height: 1.2;
        margin: 0;
    }

    @media (max-width: 900px) {
        .kl-metric-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 560px) {
        .kl-metric-grid {
            grid-template-columns: 1fr;
        }
    }

    .integrity-card {
        padding: 1rem 1.1rem;
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 14px;
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.28), rgba(15, 23, 42, 0.16));
        margin-bottom: 0.75rem;
    }
    .integrity-card.low {
        border-color: rgba(74, 222, 128, 0.38);
        background: linear-gradient(180deg, rgba(20, 83, 45, 0.32), rgba(15, 23, 42, 0.18));
    }
    .integrity-card.medium {
        border-color: rgba(250, 204, 21, 0.38);
        background: linear-gradient(180deg, rgba(113, 63, 18, 0.32), rgba(15, 23, 42, 0.18));
    }
    .integrity-card.high {
        border-color: rgba(248, 113, 113, 0.38);
        background: linear-gradient(180deg, rgba(127, 29, 29, 0.34), rgba(15, 23, 42, 0.18));
    }
    .integrity-label {
        font-size: 0.95rem;
        color: #cbd5e1;
        margin-bottom: 0.35rem;
    }
    .integrity-card.low .integrity-value {
        color: #86efac;
    }
    .integrity-card.medium .integrity-value {
        color: #fde68a;
    }
    .integrity-card.high .integrity-value {
        color: #fca5a5;
    }
    .integrity-value {
        font-size: 2.2rem;
        font-weight: 700;
        line-height: 1;
    }
    .integrity-title {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }
    .integrity-subtitle {
        color: #94a3b8;
        margin-bottom: 1rem;
    }

    /* Counterparty table styling */
    .counterparty-net-positive {
        color: #4CAF50;
        font-weight: 600;
    }
    .counterparty-net-negative {
        color: #F44336;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

validation_css_parts = []
if st.session_state.get("bank_choice_error"):
    validation_css_parts.append(
        """
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            border: 1px solid #F04438 !important;
            box-shadow: inset 0 0 0 1px #F04438 !important;
            border-radius: 8px !important;
            box-sizing: border-box !important;
        }

        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
            border-color: #F04438 !important;
        }

        div[data-testid="stSelectbox"] label,
        div[data-testid="stSelectbox"] p {
            color: #F04438 !important;
        }
        """
    )
if st.session_state.get("high_value_threshold_error"):
    validation_css_parts.append(
        """
        div[data-testid="stTextInput"]:has(input[aria-label="High Value Threshold (RM)"]) div[data-baseweb="input"] {
            border: 2px solid #F04438 !important;
            box-shadow: inset 0 0 0 1px #F04438 !important;
            border-radius: 8px !important;
            box-sizing: border-box !important;
        }

        div[data-testid="stTextInput"]:has(input[aria-label="High Value Threshold (RM)"]) label,
        div[data-testid="stTextInput"]:has(input[aria-label="High Value Threshold (RM)"]) p {
            color: #F04438 !important;
        }
        """
    )
if st.session_state.get("pdf_upload_error"):
    validation_css_parts.append(
        """
        section[data-testid="stFileUploaderDropzone"],
        div[data-testid="stFileUploaderDropzone"] {
            border: 1.5px solid #F04438 !important;
            box-shadow: inset 0 0 0 1px #F04438 !important;
            border-radius: 8px !important;
            box-sizing: border-box !important;
        }

        section[data-testid="stFileUploaderDropzone"]:hover,
        div[data-testid="stFileUploaderDropzone"]:hover {
            border-color: #F04438 !important;
        }

        div[data-testid="stFileUploader"] label,
        div[data-testid="stFileUploader"] p {
            color: #F04438 !important;
        }
        """
    )
st.markdown(
    f"""
    <style>
    {''.join(validation_css_parts)}
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# Session state init
# -----------------------------
if "status" not in st.session_state:
    st.session_state.status = "idle"

if "results" not in st.session_state:
    st.session_state.results = []

if "integrity_analysis_results" not in st.session_state:
    st.session_state.integrity_analysis_results = {}

if "affin_statement_totals" not in st.session_state:
    st.session_state.affin_statement_totals = []

if "affin_file_transactions" not in st.session_state:
    st.session_state.affin_file_transactions = {}

if "ambank_statement_totals" not in st.session_state:
    st.session_state.ambank_statement_totals = []

if "ambank_file_transactions" not in st.session_state:
    st.session_state.ambank_file_transactions = {}

if "cimb_statement_totals" not in st.session_state:
    st.session_state.cimb_statement_totals = []

if "cimb_file_transactions" not in st.session_state:
    st.session_state.cimb_file_transactions = {}

if "rhb_statement_totals" not in st.session_state:
    st.session_state.rhb_statement_totals = []

if "rhb_file_transactions" not in st.session_state:
    st.session_state.rhb_file_transactions = {}

if "bank_islam_file_month" not in st.session_state:
    st.session_state.bank_islam_file_month = {}

# password + company name tracking
if "pdf_password" not in st.session_state:
    st.session_state.pdf_password = ""

if "company_name_override" not in st.session_state:
    st.session_state.company_name_override = ""

if "company_account_no_override" not in st.session_state:
    st.session_state.company_account_no_override = ""

if "high_value_threshold_input" not in st.session_state:
    st.session_state.high_value_threshold_input = ""

if "high_value_threshold_error" not in st.session_state:
    st.session_state.high_value_threshold_error = ""

if "bank_choice_error" not in st.session_state:
    st.session_state.bank_choice_error = ""

if "pdf_upload_error" not in st.session_state:
    st.session_state.pdf_upload_error = ""

if "validation_toast_message" not in st.session_state:
    st.session_state.validation_toast_message = ""

if "active_high_value_threshold" not in st.session_state:
    st.session_state.active_high_value_threshold = None

if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False

if "upload_widget_reset_id" not in st.session_state:
    st.session_state.upload_widget_reset_id = 0

if "file_company_name" not in st.session_state:
    st.session_state.file_company_name = {}

if "file_account_no" not in st.session_state:
    st.session_state.file_account_no = {}

# Track 2 — account-type determinations populated by parser hooks
if "account_type_determinations" not in st.session_state:
    st.session_state.account_type_determinations = []

# Analyst-supplied related parties (populated by the analyst form when wired)
if "related_parties_override" not in st.session_state:
    st.session_state.related_parties_override = []


# -----------------------------
# Fraud/Integrity Constants and Functions
# -----------------------------
FRAUD_LAYER_ORDER = [
    ("metadata", "Layer 1: Metadata"),
    ("fonts", "Layer 2: Fonts"),
    ("text_layers", "Layer 3: Text Layers"),
    ("visual", "Layer 4: Visual"),
    ("cross_validation", "Layer 5: Cross Validation"),
    ("bank_profile", "Layer 6: Bank Profile"),
    ("structural", "Layer 7: Structural"),
    ("arithmetic", "Layer 8: Arithmetic"),
]


def severity_badge(severity: str) -> str:
    severity = (severity or "").upper()
    if severity == "HIGH":
        return "🔴 HIGH"
    if severity == "MEDIUM":
        return "🟠 MEDIUM"
    return "🟢 LOW"


def severity_dot(severity: str) -> str:
    severity = (severity or "").upper()
    if severity == "HIGH":
        return "🔴"
    if severity == "MEDIUM":
        return "🟡"
    return "🟢"


def render_integrity_report_styles() -> None:
    st.markdown(
        """
        <style>
        .integrity-card {
            padding: 1rem 1.1rem;
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 14px;
            background: linear-gradient(180deg, rgba(15, 23, 42, 0.28), rgba(15, 23, 42, 0.16));
            margin-bottom: 0.75rem;
        }
        .integrity-card.low {
            border-color: rgba(74, 222, 128, 0.38);
            background: linear-gradient(180deg, rgba(20, 83, 45, 0.32), rgba(15, 23, 42, 0.18));
        }
        .integrity-card.medium {
            border-color: rgba(250, 204, 21, 0.38);
            background: linear-gradient(180deg, rgba(113, 63, 18, 0.32), rgba(15, 23, 42, 0.18));
        }
        .integrity-card.high {
            border-color: rgba(248, 113, 113, 0.38);
            background: linear-gradient(180deg, rgba(127, 29, 29, 0.34), rgba(15, 23, 42, 0.18));
        }
        .integrity-label {
            font-size: 0.95rem;
            color: #cbd5e1;
            margin-bottom: 0.35rem;
        }
        .integrity-card.low .integrity-value {
            color: #86efac;
        }
        .integrity-card.medium .integrity-value {
            color: #fde68a;
        }
        .integrity-card.high .integrity-value {
            color: #fca5a5;
        }
        .integrity-value {
            font-size: 2.2rem;
            font-weight: 700;
            line-height: 1;
        }
        .integrity-title {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.25rem;
        }
        .integrity-subtitle {
            color: #94a3b8;
            margin-bottom: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_integrity_metric(label: str, value, dot: str | None = None, tone: str | None = None) -> None:
    dot_html = f"<span>{dot}</span> " if dot else ""
    tone_class = f" {tone.lower()}" if tone and tone.lower() in {"low", "medium", "high"} else ""
    st.markdown(
        f"""
        <div class="integrity-card{tone_class}">
            <div class="integrity-label">{dot_html}{label}</div>
            <div class="integrity-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_integrity_overview(analysis_results: dict) -> None:
    total_files = len(analysis_results)
    clean_count = sum(1 for result in analysis_results.values() if result.get("overall_risk") == "LOW")
    medium_count = sum(1 for result in analysis_results.values() if result.get("overall_risk") == "MEDIUM")
    high_count = sum(1 for result in analysis_results.values() if result.get("overall_risk") == "HIGH")

    st.markdown('<div class="integrity-title">🛡️ Document Integrity Scan</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="integrity-subtitle">Multi-layer fraud screening across all uploaded statements.</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_integrity_metric("Files Scanned", total_files)
    with c2:
        render_integrity_metric("Clean", clean_count, "🟢")
    with c3:
        render_integrity_metric("Medium Risk", medium_count, "🟡")
    with c4:
        render_integrity_metric("High Risk", high_count, "🔴")


def file_risk_label(file_name: str, result: dict) -> str:
    risk = (result.get("overall_risk") or "LOW").upper()
    counts = integrity_layer_counts(result.get("layer_results", {}))
    return (
        f"{severity_dot(risk)} {file_name} - Risk: {risk} "
        f"({counts.get('high', 0)}H / {counts.get('medium', 0)}M / {counts.get('low', 0)}L)"
    )


def integrity_layer_counts(layer_results: dict) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0, "total": 0}
    for layer_key, _ in FRAUD_LAYER_ORDER:
        findings = (layer_results or {}).get(layer_key, [])
        highest = next(
            (
                level.lower()
                for level in ("HIGH", "MEDIUM", "LOW")
                if any((item.get("severity") or "").upper() == level for item in findings)
            ),
            "low",
        )
        counts[highest] += 1
        counts["total"] += 1
    return counts


def render_fraud_summary(summary: dict, layer_results: dict | None = None):
    risk = summary.get("overall_risk", "LOW")
    counts = summary.get("counts", {})
    headline = summary.get("headline", "Analysis complete")
    if layer_results:
        counts = integrity_layer_counts(layer_results)

    if risk == "HIGH":
        st.error(f"Overall Risk: {risk}")
    elif risk == "MEDIUM":
        st.warning(f"Overall Risk: {risk}")
    else:
        st.success(f"Overall Risk: {risk}")

    st.write(headline)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("High", counts.get("high", 0))
    c2.metric("Medium", counts.get("medium", 0))
    c3.metric("Low", counts.get("low", 0))
    c4.metric("Total Findings", counts.get("total", 0))

    top_findings = summary.get("top_findings", [])
    if top_findings or layer_results:
        st.markdown("**Top Findings**")

    top_finding_by_layer = {
        finding.get("layer", ""): finding
        for finding in top_findings
        if finding.get("layer")
    }

    if not layer_results and top_findings:
        for finding in top_findings:
            st.write(
                f"{severity_badge(finding.get('severity'))} "
                f"**[{finding.get('layer', 'unknown')}]** {finding.get('message', '')}"
            )
    elif not layer_results:
        st.info("No findings returned.")

    if layer_results:
        for layer_key, layer_label in FRAUD_LAYER_ORDER:
            findings = layer_results.get(layer_key, [])
            if findings:
                highest = next(
                    (
                        level
                        for level in ("HIGH", "MEDIUM", "LOW")
                        if any((item.get("severity") or "").upper() == level for item in findings)
                    ),
                    "LOW",
                )
                anomaly_count = sum(1 for finding in findings if not is_benign_integrity_finding(finding))
                summary_finding = top_finding_by_layer.get(layer_key) or (findings[0] if findings else None)
                message = summary_finding.get("message", "") if summary_finding else "No findings."
                st.write(
                    f"{severity_badge(highest)} **{layer_label}** "
                    f"{message} ({anomaly_count} anomalies detected)"
                )
            else:
                st.write(f"{severity_badge('LOW')} **{layer_label}** (0 anomalies detected)")


def is_benign_integrity_finding(finding: dict) -> bool:
    message = str(finding.get("message", "") or "").lower()
    benign_patterns = [
        "no anomalies detected",
        "verified",
        "matches known",
        "hashes computed",
        "pdf version",
        "font consistency",
    ]
    return any(pattern in message for pattern in benign_patterns)



# -----------------------------
# Counterparty Ledger Functions
# -----------------------------
UNKNOWN_COUNTERPARTY_VALUES = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "-"}


def normalize_counterparty_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = re.sub(r"\s+", " ", str(value).strip().upper())
    if text in UNKNOWN_COUNTERPARTY_VALUES:
        return ""
    return text


def resolve_transaction_counterparty(row: pd.Series) -> str:
    """
    Prefer counterparty values extracted by bank parsers. Parser-specific
    helpers may be used, but the UI does not extract counterparties itself.
    """
    for column in (
        "party_name",
        "counterparty",
        "counterparty_name",
        "party",
        "merchant",
        "merchant_name",
        "recipient",
        "beneficiary",
        "payer",
        "payee",
    ):
        if column in row:
            counterparty = normalize_counterparty_value(row.get(column))
            if counterparty:
                return counterparty

    description = row.get("description", "")
    try:
        if pd.isna(description):
            description = ""
    except Exception:
        pass
    bank = normalize_counterparty_value(row.get("bank"))
    if "CIMB" in bank:
        counterparty = normalize_counterparty_value(extract_cimb_party_name(description))
        if counterparty:
            return counterparty

    return "UNKNOWN"


def build_counterparty_ledger_from_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build counterparty ledger summary using parser-extracted counterparty data.
    """
    if df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    df['counterparty'] = df.apply(resolve_transaction_counterparty, axis=1)
    
    # Group by counterparty
    summary_data = []
    for counterparty, group in df.groupby('counterparty'):
        credit_transactions = group[group['credit'] > 0]
        debit_transactions = group[group['debit'] > 0]
        
        total_credits = credit_transactions['credit'].sum() if not credit_transactions.empty else 0
        total_debits = debit_transactions['debit'].sum() if not debit_transactions.empty else 0
        net_position = total_credits - total_debits
        
        summary_data.append({
            'counterparty_name': counterparty,
            'transaction_count': len(group),
            'credit_count': len(credit_transactions),
            'debit_count': len(debit_transactions),
            'total_credits': round(total_credits, 2),
            'total_debits': round(total_debits, 2),
            'net_position': round(net_position, 2)
        })
    
    summary_df = pd.DataFrame(summary_data)
    
    summary_df['_sort_counterparty'] = summary_df['counterparty_name'].astype(str).str.casefold()
    summary_df = summary_df.sort_values('_sort_counterparty', ascending=True)
    summary_df = summary_df.drop('_sort_counterparty', axis=1).reset_index(drop=True)
    
    return summary_df


def render_counterparty_ledger_table(df: pd.DataFrame) -> None:
    """
    Render counterparty ledger as a table with transaction details on selection
    """
    if df.empty:
        st.info("No counterparty data available.")
        return
    
    # Build counterparty summary
    counterparty_summary = build_counterparty_ledger_from_transactions(df)
    
    if counterparty_summary.empty:
        st.info("No counterparty data available.")
        return
    
    st.markdown("## 💼 Counterparty Ledger")
    
    def build_top_counterparty_table(amount_column: str, count_column: str) -> pd.DataFrame:
        top_df = counterparty_summary[
            counterparty_summary[amount_column].fillna(0) > 0
        ].copy()
        if top_df.empty:
            return pd.DataFrame(columns=["Counterparty", "Total Txn", "Total Amnt of Txn"])

        top_df = top_df.sort_values(amount_column, ascending=False).head(10)
        return pd.DataFrame(
            {
                "Counterparty": top_df["counterparty_name"],
                "Total Txn": top_df[count_column].astype(int),
                "Total Amnt of Txn": top_df[amount_column].apply(lambda x: f"RM {x:,.2f}"),
            }
        )


    # Display summary table
    display_df = counterparty_summary.copy()
    display_df['total_credits'] = display_df['total_credits'].apply(lambda x: f"RM {x:,.2f}")
    display_df['total_debits'] = display_df['total_debits'].apply(lambda x: f"RM {x:,.2f}")
    
    # Format net position with color indicators using column config
    def format_net_position(val):
        if val > 0:
            return f"🟢 RM {val:,.2f}"
        elif val < 0:
            return f"🔴 RM {abs(val):,.2f}"
        return f"⚪ RM {val:,.2f}"
    
    display_df['net_position_display'] = counterparty_summary['net_position'].apply(format_net_position)
    display_df = display_df[
        [
            'counterparty_name',
            'transaction_count',
            'credit_count',
            'debit_count',
            'total_credits',
            'total_debits',
            'net_position_display',
        ]
    ]

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            'counterparty_name': 'Counterparty',
            'transaction_count': 'Transactions',
            'credit_count': 'Credits',
            'debit_count': 'Debits',
            'total_credits': 'Total Credits',
            'total_debits': 'Total Debits',
            'net_position_display': 'Net Position'
        }
    )
    
    # Selection dropdown
    selected_counterparty = st.selectbox(
        "Select a counterparty to inspect transaction lines",
        options=counterparty_summary['counterparty_name'].tolist(),
        index=0,
    )
    
    # Show transactions for selected counterparty
    df_copy = df.copy()
    df_copy['counterparty'] = df_copy.apply(resolve_transaction_counterparty, axis=1)
    counterparty_tx = df_copy[df_copy['counterparty'] == selected_counterparty].copy()

    if not counterparty_tx.empty:
        # Format for display
        display_tx = counterparty_tx[['date', 'description', 'credit', 'debit', 'balance']].copy()
        display_tx['credit'] = display_tx['credit'].apply(lambda x: f"RM {x:,.2f}" if x and x > 0 else "")
        display_tx['debit'] = display_tx['debit'].apply(lambda x: f"RM {x:,.2f}" if x and x > 0 else "")
        display_tx['balance'] = display_tx['balance'].apply(lambda x: f"RM {x:,.2f}" if x and str(x) != 'nan' else "")
        
        st.dataframe(
            display_tx,
            use_container_width=True,
            hide_index=True,
            column_config={
                'date': 'Date',
                'description': 'Description',
                'credit': 'Credit (RM)',
                'debit': 'Debit (RM)',
                'balance': 'Balance'
            }
        )

    credit_col, debit_col = st.columns(2)
    with credit_col:
        st.markdown("#### Top 10 Credit Counterparties")
        st.dataframe(
            build_top_counterparty_table("total_credits", "credit_count"),
            use_container_width=True,
            hide_index=True,
        )
    with debit_col:
        st.markdown("#### Top 10 Debit Counterparties")
        st.dataframe(
            build_top_counterparty_table("total_debits", "debit_count"),
            use_container_width=True,
            hide_index=True,
        )

# -----------------------------
# Pattern Analysis Functions
# -----------------------------
def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().upper())


def is_round_number(amount: float, round_thresholds: List[float] = None, tolerance: float = 0.01) -> bool:
    """
    Check if amount is a round number (multiple of significant thresholds like 10,000, 50,000, 100,000, etc.)
    """
    if amount is None or amount == 0:
        return False
    
    if round_thresholds is None:
        round_thresholds = [10000, 50000, 100000, 500000, 1000000, 5000000, 10000000]
    
    abs_amount = abs(amount)
    
    for threshold in round_thresholds:
        if abs_amount >= threshold:
            remainder = abs_amount % threshold
            if remainder < tolerance or (threshold - remainder) < tolerance:
                return True
    
    return False


def detect_duplicate_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Detect duplicate transactions based on date, description, and amount"""
    if df.empty:
        return df
    
    df = df.copy()
    df["duplicate_key"] = df.apply(
        lambda row: (
            row.get("date", ""),
            normalize_text(row.get("description", "")),
            row.get("credit", 0) if row.get("credit", 0) > 0 else row.get("debit", 0)
        ),
        axis=1
    )
    
    duplicate_counts = df.groupby("duplicate_key").size().to_dict()
    df["is_duplicate_transaction"] = df["duplicate_key"].map(lambda x: duplicate_counts.get(x, 0) > 1)
    df["duplicate_count"] = df["duplicate_key"].map(lambda x: duplicate_counts.get(x, 0))
    df.drop("duplicate_key", axis=1, inplace=True)
    
    return df


def detect_rapid_repeat_transactions(df: pd.DataFrame, days_window: int = 30) -> pd.DataFrame:
    """Detect transactions that repeat rapidly to the same party"""
    if df.empty:
        return df
    
    df = df.copy()
    df["parsed_date"] = pd.to_datetime(df["date"], errors="coerce")
    df["normalized_description"] = df["description"].apply(normalize_text)
    
    df["is_rapid_repeat_transaction"] = False
    df["repeat_days_in_window"] = 0
    
    for normalized_desc, group in df.groupby("normalized_description"):
        if len(group) < 2:
            continue
        
        group_sorted = group.sort_values("parsed_date")
        dates = group_sorted["parsed_date"].values
        
        for i in range(len(dates)):
            if i < len(dates) - 1:
                days_diff = (pd.Timestamp(dates[i + 1]) - pd.Timestamp(dates[i])).days
                if days_diff <= days_window:
                    idx = group_sorted.iloc[i].name
                    df.loc[idx, "is_rapid_repeat_transaction"] = True
                    df.loc[idx, "repeat_days_in_window"] = days_diff
    
    return df


# -----------------------------
# Statutory Payment Detection Functions
# -----------------------------
def compute_epf_payments(df: pd.DataFrame) -> Tuple[int, float]:
    """Detect EPF / KWSP contributions"""
    if df.empty:
        return 0, 0.0
    
    epf_patterns = [
        r"\bEPF\b",
        r"\bKWSP\b",
        r"\bKUMPULAN\s+WANG\s+SIMPANAN\s+PEKERJA\b",
        r"\bKARIM\s+JASMINE\s+EPF\b",
        r"\bEPF\s+KARIM\b",
        r"\bKWSP\s+KARIM\b",
    ]
    
    pattern = re.compile("|".join(epf_patterns), re.IGNORECASE)
    
    epf_mask = df["description"].astype(str).apply(lambda x: bool(pattern.search(x)))
    
    # Filter out SOCSO which might match some patterns
    socso_pattern = re.compile(r"\b(SOCSO|PERKESO|EIS|SIP)\b", re.IGNORECASE)
    socso_mask = df["description"].astype(str).apply(lambda x: bool(socso_pattern.search(x)))
    
    # EPF should not be SOCSO
    epf_mask = epf_mask & ~socso_mask
    
    epf_df = df[epf_mask].copy()
    
    # Get amounts (debit/credit - contributions are usually debits/outflows)
    epf_df["amount"] = epf_df.apply(
        lambda row: row.get("debit", 0) if row.get("debit", 0) > 0 else row.get("credit", 0),
        axis=1
    )
    
    total_amount = epf_df["amount"].sum()
    count = len(epf_df)
    
    # Also add flags to dataframe for detailed view
    if "is_epf_payment" not in df.columns:
        df["is_epf_payment"] = False
    df.loc[epf_mask, "is_epf_payment"] = True
    df.loc[epf_mask, "epf_amount"] = epf_df["amount"]
    
    return count, total_amount


def compute_socso_payments(df: pd.DataFrame) -> Tuple[int, float]:
    """Detect SOCSO / PERKESO / EIS contributions"""
    if df.empty:
        return 0, 0.0
    
    socso_patterns = [
        r"\bSOCSO\b",
        r"\bPERKESO\b",
        r"\bEIS\b",
        r"\bSIP\b",
        r"\bPERTUBUHAN\s+KESELAMATAN\s+SOSIAL\b",
        r"\bPERKESO\s+SOCSO\b",
    ]
    
    pattern = re.compile("|".join(socso_patterns), re.IGNORECASE)
    
    socso_mask = df["description"].astype(str).apply(lambda x: bool(pattern.search(x)))
    
    socso_df = df[socso_mask].copy()
    
    # Get amounts (debit/credit - contributions are usually debits/outflows)
    socso_df["amount"] = socso_df.apply(
        lambda row: row.get("debit", 0) if row.get("debit", 0) > 0 else row.get("credit", 0),
        axis=1
    )
    
    total_amount = socso_df["amount"].sum()
    count = len(socso_df)
    
    # Add flags to dataframe for detailed view
    if "is_socso_payment" not in df.columns:
        df["is_socso_payment"] = False
    df.loc[socso_mask, "is_socso_payment"] = True
    df.loc[socso_mask, "socso_amount"] = socso_df["amount"]
    
    return count, total_amount


def compute_lhdn_tax_payments(df: pd.DataFrame) -> Tuple[int, float]:
    """Detect LHDN / tax payments"""
    if df.empty:
        return 0, 0.0
    
    lhdn_patterns = [
        r"\bLHDN\b",
        r"\bLEMBAGA\s+HASIL\s+DALAM\s+NEGERI\b",
        r"\bINLAND\s+REVENUE\b",
        r"\bTAX\s+PAYMENT\b",
        r"\bPCB\b",
        r"\bPOTONGAN\s+CUKAI\s+BULANAN\b",
        r"\bCUKAI\s+PENDAPATAN\b",
        r"\bINCOME\s+TAX\b",
        r"\bHASIL\s+DALAM\s+NEGERI\b",
    ]
    
    pattern = re.compile("|".join(lhdn_patterns), re.IGNORECASE)
    
    lhdn_mask = df["description"].astype(str).apply(lambda x: bool(pattern.search(x)))
    
    lhdn_df = df[lhdn_mask].copy()
    
    # Get amounts (debit/credit - tax payments are usually debits/outflows)
    lhdn_df["amount"] = lhdn_df.apply(
        lambda row: row.get("debit", 0) if row.get("debit", 0) > 0 else row.get("credit", 0),
        axis=1
    )
    
    total_amount = lhdn_df["amount"].sum()
    count = len(lhdn_df)
    
    # Add flags to dataframe for detailed view
    if "is_lhdn_payment" not in df.columns:
        df["is_lhdn_payment"] = False
    df.loc[lhdn_mask, "is_lhdn_payment"] = True
    df.loc[lhdn_mask, "lhdn_amount"] = lhdn_df["amount"]
    
    return count, total_amount


def compute_hrdf_payments(df: pd.DataFrame) -> Tuple[int, float]:
    """Detect HRDF / PSMB levy payments"""
    if df.empty:
        return 0, 0.0
    
    hrdf_patterns = [
        r"\bHRDF\b",
        r"\bPSMB\b",
        r"\bPEMBANGUNAN\s+SUMBER\s+MANUSIA\b",
        r"\bHUMAN\s+RESOURCE\s+DEVELOPMENT\b",
        r"\bLEVY\s+HRDF\b",
        r"\bHRD\s+CORP\b",
        r"\bHRD\s+CORPORATION\b",
    ]
    
    pattern = re.compile("|".join(hrdf_patterns), re.IGNORECASE)
    
    hrdf_mask = df["description"].astype(str).apply(lambda x: bool(pattern.search(x)))
    
    hrdf_df = df[hrdf_mask].copy()
    
    # Get amounts (debit/credit - levies are usually debits/outflows)
    hrdf_df["amount"] = hrdf_df.apply(
        lambda row: row.get("debit", 0) if row.get("debit", 0) > 0 else row.get("credit", 0),
        axis=1
    )
    
    total_amount = hrdf_df["amount"].sum()
    count = len(hrdf_df)
    
    # Add flags to dataframe for detailed view
    if "is_hrdf_payment" not in df.columns:
        df["is_hrdf_payment"] = False
    df.loc[hrdf_mask, "is_hrdf_payment"] = True
    df.loc[hrdf_mask, "hrdf_amount"] = hrdf_df["amount"]
    
    return count, total_amount


def run_fraud_checks(df: pd.DataFrame, high_value_threshold: float, round_thresholds: List[float] = None) -> pd.DataFrame:
    """Run all fraud/pattern detection checks on the transaction dataframe"""
    if df.empty:
        return df
    
    df = df.copy()
    
    # High value detection includes both inflows and outflows.
    df["is_high_value"] = df.apply(
        lambda row: (
            safe_float(row.get("credit", 0)) >= high_value_threshold
            or safe_float(row.get("debit", 0)) >= high_value_threshold
        ),
        axis=1,
    )
    
    # Round number detection - using flexible thresholds
    df["is_round"] = df.apply(
        lambda row: is_round_number(row.get("credit", 0), round_thresholds) or is_round_number(row.get("debit", 0), round_thresholds),
        axis=1
    )
    
    # Duplicate detection
    df = detect_duplicate_transactions(df)
    
    # Rapid repeat detection
    df = detect_rapid_repeat_transactions(df)
    
    # Statutory payment detections
    compute_epf_payments(df)
    compute_socso_payments(df)
    compute_lhdn_tax_payments(df)
    compute_hrdf_payments(df)

    return df


def summarize_transaction_patterns(df: pd.DataFrame) -> dict:
    """Create summary of transaction patterns including statutory payments"""
    if df.empty:
        return {
            "title": "Transactional Pattern Analysis",
            "items": [],
            "headline": "No transactions to analyze."
        }
    
    high_value_count = int(df["is_high_value"].sum()) if "is_high_value" in df.columns else 0
    duplicate_count = int(df["is_duplicate_transaction"].sum()) if "is_duplicate_transaction" in df.columns else 0
    high_freq_count = int(df["is_rapid_repeat_transaction"].sum()) if "is_rapid_repeat_transaction" in df.columns else 0
    round_count = int(df["is_round"].sum()) if "is_round" in df.columns else 0
    
    # Statutory payment counts
    epf_count, epf_total = compute_epf_payments(df)
    socso_count, socso_total = compute_socso_payments(df)
    lhdn_count, lhdn_total = compute_lhdn_tax_payments(df)
    hrdf_count, hrdf_total = compute_hrdf_payments(df)
    
    total_transactions = len(df)
    
    headline = (
        f"Analyzed {total_transactions} transactions. "
        f"Found {high_value_count} high-value, {duplicate_count} duplicates, "
        f"{high_freq_count} high-frequency, and {round_count} round-number transactions. "
        f"Statutory payments: EPF ({epf_count}), SOCSO ({socso_count}), "
        f"LHDN ({lhdn_count}), HRDF ({hrdf_count})."
    )
    
    return {
        "title": "Transactional Pattern Analysis",
        "headline": headline,
        "items": [
            ("Total Transactions", total_transactions),
            ("High-Value Flags", high_value_count),
            ("Round-Number", round_count),
            ("Repeated", duplicate_count),
            ("High Frequency Flags", high_freq_count),
        ],
        "statutory_items": [
            ("EPF / KWSP", epf_count, f"RM {epf_total:,.2f}"),
            ("SOCSO / PERKESO", socso_count, f"RM {socso_total:,.2f}"),
            ("LHDN / Tax", lhdn_count, f"RM {lhdn_total:,.2f}"),
            ("HRDF / PSMB", hrdf_count, f"RM {hrdf_total:,.2f}"),
        ],
    }


def render_pattern_details(df: pd.DataFrame, high_value_threshold: float) -> None:
    """Render expandable sections for each pattern type"""
    st.markdown('<h3 class="kl-pattern-details-heading">Pattern Details</h3>', unsafe_allow_html=True)
    
    # Duplicate transactions
    if "is_duplicate_transaction" in df.columns:
        duplicate_hits = df[df["is_duplicate_transaction"] == True].copy()
        if not duplicate_hits.empty:
            duplicate_hits["amount"] = duplicate_hits.apply(
                lambda row: f"+{row['credit']:,.2f}" if row.get('credit', 0) > 0 else f"-{row['debit']:,.2f}",
                axis=1
            )
            with st.expander("Repeated transaction"):
                st.caption("The following entries share the same date, description, and amount.")
                duplicate_columns = [c for c in ["date", "description", "amount", "balance"] if c in duplicate_hits.columns]
                st.dataframe(duplicate_hits[duplicate_columns], use_container_width=True)
    
    # Rapid repeat transactions
    if "is_rapid_repeat_transaction" in df.columns:
        rapid_repeat_hits = df[df["is_rapid_repeat_transaction"] == True].copy()
        if not rapid_repeat_hits.empty:
            with st.expander("High freq transactions"):
                st.caption(
                    "High frequency transactions are repeated payments to the same merchant "
                    "across multiple days within a short time window."
                )
                display_df = rapid_repeat_hits
                display_columns = [
                    c for c in ["date", "description", "credit", "debit", "repeat_days_in_window"]
                    if c in display_df.columns
                ]
                st.dataframe(display_df[display_columns], use_container_width=True, hide_index=True)
    
    # Round number transactions
    if "is_round" in df.columns:
        round_hits = df[df["is_round"] == True].copy()
        if not round_hits.empty:
            round_hits["amount"] = round_hits.apply(
                lambda row: f"+{row['credit']:,.2f}" if row.get('credit', 0) > 0 else f"-{row['debit']:,.2f}",
                axis=1
            )
            with st.expander("Round-number transactions"):
                st.caption("Transactions with round numbers (multiple of 10,000).")
                cols = [c for c in ["date", "description", "amount", "source_file"] if c in round_hits.columns]
                st.dataframe(round_hits[cols], use_container_width=True)
    
    # High value transactions
    if "is_high_value" in df.columns:
        high_hits = df[df["is_high_value"] == True].copy()
        if not high_hits.empty:
            high_hits["amount"] = high_hits.apply(
                lambda row: (
                    f"+RM {safe_float(row.get('credit', 0)):,.2f}"
                    if safe_float(row.get("credit", 0)) > 0
                    else f"-RM {safe_float(row.get('debit', 0)):,.2f}"
                ),
                axis=1,
            )
            with st.expander("High-value transactions"):
                st.caption(
                    "Transactions flagged when the credit or debit amount is above or equal "
                    f"to the inserted high value threshold: RM {high_value_threshold:,.2f}."
                )
                high_value_columns = [c for c in ["date", "description", "amount", "balance"] if c in high_hits.columns]
                st.dataframe(
                    high_hits[high_value_columns],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "date": "Date Transaction",
                        "description": "Description",
                        "amount": "Amount (RM)",
                        "balance": "Balance",
                    },
                )
    
    # Statutory payments sections
    epf_count, epf_total = compute_epf_payments(df)
    socso_count, socso_total = compute_socso_payments(df)
    lhdn_count, lhdn_total = compute_lhdn_tax_payments(df)
    hrdf_count, hrdf_total = compute_hrdf_payments(df)
    
    # EPF Payments
    if epf_count > 0:
        epf_hits = df[df["is_epf_payment"] == True].copy() if "is_epf_payment" in df.columns else pd.DataFrame()
        if not epf_hits.empty:
            epf_hits["amount"] = epf_hits.apply(
                lambda row: f"-{row['debit']:,.2f}" if row.get('debit', 0) > 0 else f"+{row['credit']:,.2f}",
                axis=1
            )
            with st.expander("EPF / KWSP Contributions"):
                st.caption("Employee Provident Fund (EPF) / KWSP contributions detected.")
                display_cols = [c for c in ["date", "description", "amount"] if c in epf_hits.columns]
                st.dataframe(epf_hits[display_cols], use_container_width=True)
    
    # SOCSO Payments
    if socso_count > 0:
        socso_hits = df[df["is_socso_payment"] == True].copy() if "is_socso_payment" in df.columns else pd.DataFrame()
        if not socso_hits.empty:
            socso_hits["amount"] = socso_hits.apply(
                lambda row: f"-{row['debit']:,.2f}" if row.get('debit', 0) > 0 else f"+{row['credit']:,.2f}",
                axis=1
            )
            with st.expander("SOCSO / PERKESO Contributions"):
                st.caption("Social Security Organization (SOCSO/PERKESO) contributions including EIS/SIP.")
                display_cols = [c for c in ["date", "description", "amount"] if c in socso_hits.columns]
                st.dataframe(socso_hits[display_cols], use_container_width=True)
    
    # LHDN/Tax Payments
    if lhdn_count > 0:
        lhdn_hits = df[df["is_lhdn_payment"] == True].copy() if "is_lhdn_payment" in df.columns else pd.DataFrame()
        if not lhdn_hits.empty:
            lhdn_hits["amount"] = lhdn_hits.apply(
                lambda row: f"-{row['debit']:,.2f}" if row.get('debit', 0) > 0 else f"+{row['credit']:,.2f}",
                axis=1
            )
            with st.expander("LHDN / Tax Payments"):
                st.caption("Inland Revenue Board (LHDN) / Income tax payments detected.")
                display_cols = [c for c in ["date", "description", "amount"] if c in lhdn_hits.columns]
                st.dataframe(lhdn_hits[display_cols], use_container_width=True)
    
    # HRDF Payments
    if hrdf_count > 0:
        hrdf_hits = df[df["is_hrdf_payment"] == True].copy() if "is_hrdf_payment" in df.columns else pd.DataFrame()
        if not hrdf_hits.empty:
            hrdf_hits["amount"] = hrdf_hits.apply(
                lambda row: f"-{row['debit']:,.2f}" if row.get('debit', 0) > 0 else f"+{row['credit']:,.2f}",
                axis=1
            )
            with st.expander("HRDF / PSMB Levies"):
                st.caption("Human Resource Development Fund (HRDF/PSMB) levy payments detected.")
                display_cols = [c for c in ["date", "description", "amount"] if c in hrdf_hits.columns]
                st.dataframe(hrdf_hits[display_cols], use_container_width=True)


def render_metric_cards(metrics: List[Tuple[str, object]], wide_metrics: List[Tuple[str, object]], statutory_metrics: List[Tuple[str, int, str]] = None) -> None:
    cards = []
    for label, value in metrics:
        cards.append(
            '<div class="kl-metric-card">'
            f'<div class="kl-metric-label">{escape(str(label))}</div>'
            f'<div class="kl-metric-value">{escape(str(value))}</div>'
            "</div>"
        )
    for label, value in wide_metrics:
        cards.append(
            '<div class="kl-metric-card kl-metric-card-wide">'
            f'<div class="kl-metric-label">{escape(str(label))}</div>'
            f'<div class="kl-metric-value">{escape(str(value))}</div>'
            "</div>"
        )
    
    # Add statutory payment cards
    if statutory_metrics:
        for label, count, total in statutory_metrics:
            cards.append(
                '<div class="kl-metric-card">'
                f'<div class="kl-metric-label">{escape(str(label))}</div>'
                f'<div class="kl-metric-value">{count}</div>'
                f'<div style="font-size: 0.85rem; color: #A9C1DD; margin-top: 0.35rem;">{escape(str(total))}</div>'
                "</div>"
            )

    st.html(
        f'<div class="kl-metric-grid">{"".join(cards)}</div>',
    )


def render_transaction_overview(df: pd.DataFrame, high_value_threshold: float) -> None:
    """Render the transaction pattern overview dashboard"""
    analysis_df = filter_statement_transactions_df(df)
    pattern_summary = summarize_transaction_patterns(analysis_df)
    
    st.html(
        '<div class="kl-analysis-title">📊 Transactional Pattern Analysis</div>'
        '<div class="kl-analysis-subtitle">The story of the money and whether the financial behavior makes sense.</div>',
    )
    
    # Display pattern metrics only. Financial cards live in the Extracted Transaction section.
    item_map = dict(pattern_summary.get("items", []))
    statutory_items = pattern_summary.get("statutory_items", [])
    
    cards = []

    cards.extend([
        '<div class="kl-metric-card">'
        '<div class="kl-metric-label">High-Value Flags</div>'
        f'<div class="kl-metric-value">{item_map.get("High-Value Flags", 0)}</div>'
        '</div>',
        
        '<div class="kl-metric-card">'
        '<div class="kl-metric-label">Round-Number</div>'
        f'<div class="kl-metric-value">{item_map.get("Round-Number", 0)}</div>'
        '</div>',
        
        '<div class="kl-metric-card">'
        '<div class="kl-metric-label">Repeated</div>'
        f'<div class="kl-metric-value">{item_map.get("Repeated", 0)}</div>'
        '</div>',

        '<div class="kl-metric-card">'
        '<div class="kl-metric-label">High Frequency Flags</div>'
        f'<div class="kl-metric-value">{item_map.get("High Frequency Flags", 0)}</div>'
        '</div>',
    ])

    
    # Add statutory payment cards
    if statutory_items:
        for label, count, total in statutory_items:
            cards.append(
                '<div class="kl-metric-card">'
                f'<div class="kl-metric-label">{label}</div>'
                f'<div class="kl-metric-value">{count}</div>'
                f'<div style="font-size: 0.85rem; color: #A9C1DD; margin-top: 0.35rem;">{total}</div>'
                '</div>'
            )
    
    # Display all cards in the grid
    st.html(
        f'<div class="kl-metric-grid">{"".join(cards)}</div>',
    )
    
    # Render detailed expandable sections
    render_pattern_details(analysis_df, high_value_threshold)


def render_extracted_transaction_section(df: pd.DataFrame) -> None:
    """Render extracted transaction metrics and the transaction table."""
    analysis_df = filter_statement_transactions_df(df)
    total_credits = analysis_df["credit"].sum() if "credit" in analysis_df.columns else 0
    total_debits = analysis_df["debit"].sum() if "debit" in analysis_df.columns else 0
    net_position = total_credits - total_debits
    net_color = "#69f0ae" if net_position >= 0 else "#ff8a80"
    net_border = "#2e7d32" if net_position >= 0 else "#c62828"
    net_label = "Net Position" if net_position >= 0 else "Net Loss"

    cards = [
        '<div class="kl-metric-card">'
        '<div class="kl-metric-label">Transactions</div>'
        f'<div class="kl-metric-value">{len(analysis_df):,}</div>'
        '</div>',
        '<div class="kl-metric-card" style="background: linear-gradient(135deg, #1a472a 0%, #0d2818 100%); border-color: #2e7d32;">'
        '<div class="kl-metric-label">Net Credits</div>'
        f'<div class="kl-metric-value" style="color: #69f0ae;">RM {total_credits:,.2f}</div>'
        '</div>',
        '<div class="kl-metric-card" style="background: linear-gradient(135deg, #4a1a1a 0%, #2d1010 100%); border-color: #c62828;">'
        '<div class="kl-metric-label">Net Debits</div>'
        f'<div class="kl-metric-value" style="color: #ff8a80;">RM {total_debits:,.2f}</div>'
        '</div>',
        f'<div class="kl-metric-card" style="background: linear-gradient(135deg, #1a2a3a 0%, #0d1a2a 100%); border-color: {net_border};">'
        f'<div class="kl-metric-label">{net_label}</div>'
        f'<div class="kl-metric-value" style="color: {net_color};">RM {abs(net_position):,.2f}</div>'
        '</div>',
    ]

    st.html(
        '<div class="kl-analysis-title">&#128202; Extracted Transaction <span style="font-size: 1rem; color: #A9C1DD;">&#128279;</span></div>'
        f'<div class="kl-metric-grid">{"".join(cards)}</div>',
    )

    st.markdown("#### All Transactions")
    requested_cols = ["date", "description", "debit", "credit", "balance"]
    display_cols = [c for c in requested_cols if c in df.columns]
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        column_config={
            "date": "Transaction Date",
            "description": "Description",
            "debit": "Debit (RM)",
            "credit": "Credit (RM)",
            "balance": "Running Balance",
        },
    )

# -----------------------------
# Core Processing Functions
# -----------------------------
def clear_processing_outputs() -> None:
    st.session_state.results = []
    st.session_state.integrity_analysis_results = {}
    st.session_state.affin_statement_totals = []
    st.session_state.affin_file_transactions = {}
    st.session_state.ambank_statement_totals = []
    st.session_state.ambank_file_transactions = {}
    st.session_state.cimb_statement_totals = []
    st.session_state.rhb_statement_totals = []
    st.session_state.cimb_file_transactions = {}
    st.session_state.rhb_file_transactions = {}
    st.session_state.bank_islam_file_month = {}
    st.session_state.file_company_name = {}
    st.session_state.file_account_no = {}
    st.session_state.account_type_determinations = []
    st.session_state.related_parties_override = []


def parse_high_value_threshold() -> Tuple[Optional[float], Optional[str]]:
    raw = str(st.session_state.get("high_value_threshold_input", "") or "").strip()
    if not raw:
        return None, "Please insert the high value threshold."
    if not re.search(r"\d", raw):
        return None, "Please insert a valid high value threshold."

    threshold = safe_float(raw)
    if threshold <= 0:
        return None, "Please insert a high value threshold above 0."
    return threshold, None


def clear_high_value_threshold_error() -> None:
    st.session_state.high_value_threshold_error = ""
    st.session_state.validation_toast_message = ""


def get_upload_widget_key() -> str:
    return f"pdf_upload_{st.session_state.upload_widget_reset_id}"


def validate_pdf_upload() -> Optional[str]:
    uploaded_file_values = st.session_state.get(get_upload_widget_key())
    if not uploaded_file_values:
        return "Please upload at least one PDF file."
    return None


def clear_pdf_upload_error() -> None:
    st.session_state.pdf_upload_error = ""
    st.session_state.validation_toast_message = ""


def validate_bank_choice() -> Optional[str]:
    bank_choice_value = st.session_state.get("bank_choice")
    if not bank_choice_value:
        return "Please select the bank format."
    return None


def clear_bank_choice_error() -> None:
    st.session_state.bank_choice_error = ""
    st.session_state.validation_toast_message = ""


def start_processing() -> None:
    threshold, threshold_error = parse_high_value_threshold()
    bank_choice_error = validate_bank_choice()
    pdf_upload_error = validate_pdf_upload()

    st.session_state.high_value_threshold_error = threshold_error or ""
    st.session_state.bank_choice_error = bank_choice_error or ""
    st.session_state.pdf_upload_error = pdf_upload_error or ""

    if threshold_error or bank_choice_error or pdf_upload_error:
        validation_messages = [
            msg for msg in (bank_choice_error, pdf_upload_error, threshold_error) if msg
        ]
        st.session_state.validation_toast_message = " ".join(validation_messages)
        st.session_state.status = "idle"
        return

    st.session_state.validation_toast_message = ""
    st.session_state.active_high_value_threshold = threshold
    st.session_state.stop_requested = False
    st.session_state.status = "running"
    clear_processing_outputs()


def stop_processing() -> None:
    st.session_state.stop_requested = True
    if st.session_state.status == "running":
        st.session_state.status = "stopped"


def reset_app_inputs() -> None:
    st.session_state.status = "idle"
    st.session_state.stop_requested = False
    clear_processing_outputs()
    st.session_state.pdf_password = ""
    st.session_state.company_name_override = ""
    st.session_state.company_account_no_override = ""
    st.session_state.high_value_threshold_input = ""
    st.session_state.high_value_threshold_error = ""
    st.session_state.bank_choice_error = ""
    st.session_state.pdf_upload_error = ""
    st.session_state.validation_toast_message = ""
    st.session_state.active_high_value_threshold = None
    st.session_state.upload_widget_reset_id += 1
    if "bank_choice" in st.session_state and "PARSERS" in globals():
        st.session_state.bank_choice = None


def get_high_value_threshold() -> float:
    threshold, threshold_error = parse_high_value_threshold()
    if not threshold_error and threshold is not None:
        return threshold

    active_threshold = st.session_state.get("active_high_value_threshold")
    if active_threshold is not None:
        return float(active_threshold)
    return 0.0


def truncate_filename(filename: str, max_chars: int = 34) -> str:
    filename = str(filename or "")
    if len(filename) <= max_chars:
        return filename

    keep_left = max(8, (max_chars - 3) // 2)
    keep_right = max(8, max_chars - keep_left - 3)
    return f"{filename[:keep_left]}...{filename[-keep_right:]}"


def render_processing_progress(
    container,
    *,
    status: str,
    progress: float,
    variant: str = "active",
    file_name: str = "",
) -> None:
    progress = min(max(float(progress or 0), 0.0), 1.0)
    percent = int(round(progress * 100))
    safe_status = escape(str(status or ""))
    safe_file_name = escape(str(file_name or ""), quote=True)
    short_file_name = escape(truncate_filename(str(file_name or "")))
    variant_class = variant if variant in {"active", "success", "warning", "error"} else "active"
    icon = "✅ " if variant_class == "success" else ""

    file_line = ""
    if file_name:
        file_line = (
            '<div class="kl-progress-filename">'
            f'Current file: <span title="{safe_file_name}">{short_file_name}</span>'
            "</div>"
        )

    html = (
        f'<div class="kl-progress-panel {variant_class}">'
        '<div class="kl-progress-topline">'
        f'<div class="kl-progress-status">{icon}{safe_status}</div>'
        f'<div class="kl-progress-percent">{percent}% completed</div>'
        '</div>'
        f'{file_line}'
        '<div class="kl-progress-track" aria-label="Processing progress">'
        f'<div class="kl-progress-fill" style="width: {percent}%;"></div>'
        '</div>'
        '</div>'
    )
    container.markdown(html, unsafe_allow_html=True)


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_any_date_for_summary(x) -> pd.Timestamp:
    if x is None:
        return pd.NaT
    s = str(x).strip()
    if not s:
        return pd.NaT
    if _ISO_RE.match(s):
        return pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _parse_with_pdfplumber(parser_func: Callable, pdf_bytes: bytes, filename: str) -> List[dict]:
    with bytes_to_pdfplumber(pdf_bytes) as pdf:
        return parser_func(pdf, filename)


# -----------------------------
# Bank parsers
# -----------------------------
PARSERS: Dict[str, Callable[[bytes, str], List[dict]]] = {
    "Affin Bank": lambda b, f: _parse_with_pdfplumber(parse_affin_bank, b, f),
    "Agro Bank": lambda b, f: _parse_with_pdfplumber(parse_agro_bank, b, f),
    "Alliance Bank": lambda b, f: _parse_with_pdfplumber(parse_transactions_alliance, b, f),
    "Ambank": lambda b, f: _parse_with_pdfplumber(parse_ambank, b, f),
    "Bank Islam": lambda b, f: _parse_with_pdfplumber(parse_bank_islam, b, f),
    "Bank Muamalat": lambda b, f: _parse_with_pdfplumber(parse_transactions_bank_muamalat, b, f),
    "Bank Rakyat": lambda b, f: _parse_with_pdfplumber(parse_bank_rakyat, b, f),
    "CIMB Bank": lambda b, f: _parse_with_pdfplumber(parse_transactions_cimb, b, f),
    "Hong Leong": lambda b, f: _parse_with_pdfplumber(parse_hong_leong, b, f),
    "Maybank": lambda b, f: parse_transactions_maybank(b, f),
    "Public Bank (PBB)": lambda b, f: _parse_with_pdfplumber(parse_transactions_pbb, b, f),
    "RHB Bank": lambda b, f: parse_transactions_rhb(b, f),
    "OCBC Bank": lambda b, f: parse_transactions_ocbc(b, f),
    "UOB Bank": lambda b, f: _parse_with_pdfplumber(parse_transactions_uob, b, f),
}


def get_supported_banks() -> List[str]:
    return list(PARSERS.keys())


# -----------------------------
# Monthly Summary Functions
# -----------------------------
BALANCE_MARKER_PATTERNS = [
    r"\bOPENING\s+BAL(?:ANCE)?\b",
    r"\bCLOSING\s+BAL(?:ANCE)?\b",
    r"\bBEGINNING\s+BAL(?:ANCE)?\b",
    r"\bENDING\s+BAL(?:ANCE)?\b",
    r"\bBALANCE\s+B\/F\b",
    r"\bBALANCE\s+C\/F\b",
    r"\bB\/F\s+BALANCE\b",
    r"\bC\/F\s+BALANCE\b",
    r"\bBROUGHT\s+FORWARD\b",
    r"\bCARRIED\s+FORWARD\b",
    r"\bBAKI\s+AWAL\b",
    r"\bBAKI\s+AKHIR\b",
    r"\bBAKI\s+PEMBUKA\b",
    r"\bBAKI\s+PENUTUP\b",
    r"\bBAKI\s+B\/B\b",
    r"\bBAKI\s+C\/F\b",
]


def is_balance_marker_transaction(tx: dict) -> bool:
    desc = normalize_text(tx.get("description", ""))
    return any(re.search(pattern, desc, flags=re.IGNORECASE) for pattern in BALANCE_MARKER_PATTERNS)


def count_statement_transactions(transactions: List[dict]) -> int:
    return sum(1 for tx in transactions if not is_balance_marker_transaction(tx))


def filter_statement_transactions_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    mask = [
        not is_balance_marker_transaction(tx)
        for tx in df.to_dict(orient="records")
    ]
    return df.loc[mask].copy()


def calculate_monthly_summary(transactions: List[dict]) -> List[dict]:
    if not transactions:
        return []

    df = pd.DataFrame(transactions)
    if df.empty:
        return []

    df = df.reset_index(drop=True)
    if "__row_order" not in df.columns:
        df["__row_order"] = range(len(df))

    df["date_parsed"] = df.get("date").apply(parse_any_date_for_summary)
    df = df.dropna(subset=["date_parsed"])
    if df.empty:
        st.warning("⚠️ No valid transaction dates found.")
        return []

    df["month_period"] = df["date_parsed"].dt.strftime("%Y-%m")
    df["debit"] = df.get("debit", 0).apply(safe_float)
    df["credit"] = df.get("credit", 0).apply(safe_float)
    df["balance"] = df.get("balance", None).apply(lambda x: safe_float(x) if x is not None else None)

    if "page" in df.columns:
        df["page"] = pd.to_numeric(df["page"], errors="coerce").fillna(0).astype(int)
    else:
        df["page"] = 0

    has_seq = "seq" in df.columns
    if has_seq:
        df["seq"] = pd.to_numeric(df["seq"], errors="coerce").fillna(0).astype(int)

    df["__row_order"] = pd.to_numeric(df["__row_order"], errors="coerce").fillna(0).astype(int)

    monthly_summary: List[dict] = []
    for period, group in df.groupby("month_period", sort=True):
        sort_cols = ["date_parsed", "page"]
        if has_seq:
            sort_cols.append("seq")
        sort_cols.append("__row_order")

        group_sorted = group.sort_values(sort_cols, na_position="last")

        balances = group_sorted["balance"].dropna()
        ending_balance = round(float(balances.iloc[-1]), 2) if not balances.empty else None
        highest_balance = round(float(balances.max()), 2) if not balances.empty else None
        lowest_balance_raw = round(float(balances.min()), 2) if not balances.empty else None
        lowest_balance = lowest_balance_raw
        od_flag = bool(lowest_balance is not None and float(lowest_balance) < 0)

        company_vals = [
            x for x in group_sorted.get("company_name", pd.Series([], dtype=object)).dropna().astype(str).unique().tolist()
            if x.strip()
        ]
        company_name = company_vals[0] if company_vals else None

        acct_vals = [
            x for x in group_sorted.get("account_no", pd.Series([], dtype=object)).dropna().astype(str).unique().tolist() if x.strip()
        ]
        account_no = acct_vals[0] if len(acct_vals) == 1 else (", ".join(acct_vals) if acct_vals else None)

        monthly_summary.append(
            {
                "month": period,
                "company_name": company_name,
                "account_no": account_no,
                "transaction_count": int(
                    sum(
                        1
                        for tx in group_sorted.to_dict(orient="records")
                        if not is_balance_marker_transaction(tx)
                    )
                ),
                "opening_balance": None,
                "total_debit": round(float(group_sorted["debit"].sum()), 2),
                "total_credit": round(float(group_sorted["credit"].sum()), 2),
                "net_change": round(float(group_sorted["credit"].sum() - group_sorted["debit"].sum()), 2),
                "ending_balance": ending_balance,
                "lowest_balance": lowest_balance,
                "lowest_balance_raw": lowest_balance_raw,
                "highest_balance": highest_balance,
                "od_flag": od_flag,
                "source_files": ", ".join(sorted(set(group_sorted.get("source_file", []))))
                if "source_file" in group_sorted.columns
                else "",
            }
        )

    # Fill opening_balance using prior month's ending_balance when possible
    monthly_summary_sorted = sorted(monthly_summary, key=lambda x: x["month"])
    prev_end = None
    for r in monthly_summary_sorted:
        if r.get("opening_balance") is None:
            if prev_end is not None:
                r["opening_balance"] = round(float(prev_end), 2)
            else:
                eb = r.get("ending_balance")
                nc = r.get("net_change")
                if eb is not None and nc is not None:
                    try:
                        r["opening_balance"] = round(float(safe_float(eb) - safe_float(nc)), 2)
                    except Exception:
                        r["opening_balance"] = None

        if r.get("ending_balance") is not None:
            prev_end = safe_float(r.get("ending_balance"))

    return monthly_summary_sorted


def present_monthly_summary_standard(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows or []:
        highest = r.get("highest_balance")
        lowest = r.get("lowest_balance")

        swing = None
        try:
            if highest is not None and lowest is not None:
                swing = round(float(safe_float(highest) - safe_float(lowest)), 2)
        except Exception:
            swing = None

        out.append(
            {
                "month": r.get("month"),
                "company_name": r.get("company_name"),
                "account_no": r.get("account_no"),
                "transaction_count": r.get("transaction_count"),
                "opening_balance": r.get("opening_balance"),
                "total_debit": r.get("total_debit"),
                "total_credit": r.get("total_credit"),
                "highest_balance": highest,
                "lowest_balance": lowest,
                "swing": swing,
                "ending_balance": r.get("ending_balance"),
                "source_files": r.get("source_files"),
            }
        )
    return out


# -----------------------------
# Main UI and Processing
# -----------------------------
if "bank_choice" not in st.session_state:
    st.session_state.bank_choice = None

bank_choice = st.selectbox(
    "Select Bank",
    options=sorted(get_supported_banks(), key=str.lower),
    index=None,
    key="bank_choice",
    placeholder="Choose the bank for the uploaded statement(s)",
    on_change=clear_bank_choice_error,
)

uploaded_files = st.file_uploader(
    "Upload PDF files",
    type=["pdf"],
    accept_multiple_files=True,
    key=get_upload_widget_key(),
    on_change=clear_pdf_upload_error,
)
if uploaded_files:
    uploaded_files = sorted(uploaded_files, key=lambda x: x.name)

input_col1, input_col2, input_col3 = st.columns([1.2, 1.0, 0.8])
with input_col1:
    st.text_input("Company Name (optional override)", key="company_name_override")
with input_col2:
    st.text_input("Company Account No. (optional override)", key="company_account_no_override")
with input_col3:
    st.text_input(
        "High Value Threshold (RM)",
        key="high_value_threshold_input",
        placeholder="e.g. 10,000",
        help="Required. Credits equal to or above this amount are flagged as high value.",
        on_change=clear_high_value_threshold_error,
    )

# Detect encrypted files
encrypted_files: List[str] = []
if uploaded_files:
    for uf in uploaded_files:
        try:
            if is_pdf_encrypted(uf.getvalue()):
                encrypted_files.append(uf.name)
        except Exception:
            encrypted_files.append(uf.name)

    if encrypted_files:
        st.warning(
            "🔒 Encrypted PDF(s) detected. Enter the password once and it will be used for all encrypted files:\n\n"
            + "\n".join([f"- {n}" for n in encrypted_files])
        )
        st.text_input("PDF Password", type="password", key="pdf_password")


button_col1, button_col2, button_col3 = st.columns([2.0, 0.9, 1.0])
with button_col1:
    st.button(
        "Start Processing",
        type="primary",
        use_container_width=True,
        on_click=start_processing,
    )

with button_col2:
    st.button(
        "Stop",
        type="secondary",
        use_container_width=True,
        on_click=stop_processing,
    )

with button_col3:
    st.button(
        "Reset",
        type="secondary",
        use_container_width=True,
        on_click=reset_app_inputs,
    )

if st.session_state.validation_toast_message:
    st.toast(st.session_state.validation_toast_message)
    st.session_state.validation_toast_message = ""

all_tx: List[dict] = []

if uploaded_files and st.session_state.status == "running":
    progress_panel = st.empty()

    total_files = len(uploaded_files)
    total_steps = total_files + 1
    parser = PARSERS[bank_choice]
    processing_errors: List[str] = []
    total_extracted = 0
    files_finished = 0
    resolved_pdf_bytes = {}

    def update_processing_progress(
        status: str,
        completed_steps: int,
        *,
        variant: str = "active",
        file_name: str = "",
    ) -> None:
        render_processing_progress(
            progress_panel,
            status=status,
            progress=completed_steps / total_steps,
            variant=variant,
            file_name=file_name,
        )

    update_processing_progress(f"Preparing {total_files} file(s) for {bank_choice}.", 0)

    for file_idx, uploaded_file in enumerate(uploaded_files):
        if st.session_state.get("stop_requested"):
            st.session_state.status = "stopped"
            update_processing_progress(
                f"Stopped after {files_finished} of {total_files} file(s).",
                files_finished,
                variant="warning",
            )
            break

        current_file = file_idx + 1
        update_processing_progress(
            f"Processing file {current_file} of {total_files}",
            files_finished,
            file_name=uploaded_file.name,
        )

        try:
            pdf_bytes = uploaded_file.getvalue()

            # decrypt if encrypted
            if is_pdf_encrypted(pdf_bytes):
                update_processing_progress(
                    f"Decrypting file {current_file} of {total_files}",
                    files_finished,
                    file_name=uploaded_file.name,
                )
                pdf_bytes = decrypt_pdf_bytes(pdf_bytes, st.session_state.pdf_password)
            
            resolved_pdf_bytes[uploaded_file.name] = pdf_bytes

            # extract company name
            company_name = None
            try:
                with bytes_to_pdfplumber(pdf_bytes) as meta_pdf:
                    company_name = extract_company_name(meta_pdf, max_pages=2)
            except Exception:
                company_name = None

            # extract account number
            account_no = None
            try:
                with bytes_to_pdfplumber(pdf_bytes) as meta_pdf:
                    account_no = extract_account_number(meta_pdf, max_pages=2)
            except Exception:
                account_no = None

            # manual override wins
            if (st.session_state.company_name_override or "").strip():
                company_name = st.session_state.company_name_override.strip()
            if (st.session_state.company_account_no_override or "").strip():
                account_no = st.session_state.company_account_no_override.strip()

            st.session_state.file_company_name[uploaded_file.name] = company_name
            st.session_state.file_account_no[uploaded_file.name] = account_no

            # Parse transactions
            if bank_choice == "Affin Bank":
                with bytes_to_pdfplumber(pdf_bytes) as pdf:
                    totals = extract_affin_statement_totals(pdf, uploaded_file.name)
                    st.session_state.affin_statement_totals.append(totals)
                    tx_raw = parse_affin_bank(pdf, uploaded_file.name) or []

            elif bank_choice == "Ambank":
                with bytes_to_pdfplumber(pdf_bytes) as pdf:
                    totals = extract_ambank_statement_totals(pdf, uploaded_file.name)
                    st.session_state.ambank_statement_totals.append(totals)
                    tx_raw = parse_ambank(pdf, uploaded_file.name) or []

            elif bank_choice == "CIMB Bank":
                with bytes_to_pdfplumber(pdf_bytes) as pdf:
                    totals = extract_cimb_statement_totals(pdf, uploaded_file.name)
                    st.session_state.cimb_statement_totals.append(totals)
                    tx_raw = parse_transactions_cimb(pdf, uploaded_file.name) or []

            elif bank_choice == "RHB Bank":
                with bytes_to_pdfplumber(pdf_bytes) as pdf:
                    totals = extract_rhb_statement_totals(pdf, uploaded_file.name)
                    st.session_state.rhb_statement_totals.append(totals)
                tx_raw = parser(pdf_bytes, uploaded_file.name) or []

            elif bank_choice == "Bank Islam":
                with bytes_to_pdfplumber(pdf_bytes) as pdf:
                    tx_raw = parse_bank_islam(pdf, uploaded_file.name) or []
                    stmt_month = extract_bank_islam_statement_month(pdf)
                    if stmt_month:
                        st.session_state.bank_islam_file_month[uploaded_file.name] = stmt_month

            else:
                tx_raw = parser(pdf_bytes, uploaded_file.name) or []

            # Normalize then attach company_name
            tx_norm = normalize_transactions(
                tx_raw,
                default_bank=bank_choice,
                source_file=uploaded_file.name,
            )
            high_value_threshold = get_high_value_threshold()
            for t in tx_norm:
                t["company_name"] = company_name
                t["account_no"] = account_no
                t["high_value_credit"] = safe_float(t.get("credit", 0)) >= high_value_threshold
                t["high_value_debit"] = safe_float(t.get("debit", 0)) >= high_value_threshold
                t["high_value_transaction"] = t["high_value_credit"] or t["high_value_debit"]

            if bank_choice == "Affin Bank":
                st.session_state.affin_file_transactions[uploaded_file.name] = tx_norm
            if bank_choice == "Ambank":
                st.session_state.ambank_file_transactions[uploaded_file.name] = tx_norm
            if bank_choice == "CIMB Bank":
                st.session_state.cimb_file_transactions[uploaded_file.name] = tx_norm
            if bank_choice == "RHB Bank":
                st.session_state.rhb_file_transactions[uploaded_file.name] = tx_norm

            if tx_norm:
                all_tx.extend(tx_norm)
                total_extracted += len(tx_norm)
                update_processing_progress(
                    f"Processed file {current_file} of {total_files}: "
                    f"{len(tx_norm)} transactions extracted",
                    current_file,
                    file_name=uploaded_file.name,
                )
            else:
                update_processing_progress(
                    f"Processed file {current_file} of {total_files}: no transactions found",
                    current_file,
                    file_name=uploaded_file.name,
                )

        except Exception as e:
            processing_errors.append(uploaded_file.name)
            update_processing_progress(
                f"Error processing file {current_file} of {total_files}: {str(e)[:100]}",
                current_file,
                variant="error",
                file_name=uploaded_file.name,
            )
            st.error(f"❌ Error processing {uploaded_file.name}: {e}")
            st.exception(e)

        files_finished = file_idx + 1

    # Run PDF integrity checks
    analysis_results = {}
    processing_stopped = st.session_state.get("stop_requested")
    if resolved_pdf_bytes and not processing_stopped:
        update_processing_progress(
            f"Running PDF integrity checks for {len(resolved_pdf_bytes)} file(s)",
            total_files,
        )
        try:
            analysis_results = analyze_pdf_batch(resolved_pdf_bytes)
        except Exception as e:
            st.warning(f"PDF integrity check failed: {e}")
    st.session_state.integrity_analysis_results = analysis_results

    # Display final status message
    if processing_stopped:
        st.session_state.status = "stopped"
        update_processing_progress(
            f"Processing stopped at {files_finished} of {total_files} file(s).",
            files_finished,
            variant="warning",
        )
    elif processing_errors:
        st.session_state.status = "completed_with_errors"
        update_processing_progress(
            f"Completed with {len(processing_errors)} error(s). "
            f"Extracted {total_extracted} transactions from {total_files} file(s).",
            total_steps,
            variant="warning",
        )
        st.warning(f"⚠️ Completed with {len(processing_errors)} error(s). Check the errors above.")
    else:
        st.session_state.status = "completed"
        update_processing_progress("Finalizing extracted transactions.", total_steps)
    
    st.markdown("---")
    all_tx = dedupe_transactions(all_tx)

    # Stable ordering
    for idx, t in enumerate(all_tx):
        if "__row_order" not in t:
            t["__row_order"] = idx

    def _sort_key(t: dict) -> Tuple:
        dt = parse_any_date_for_summary(t.get("date"))
        page = t.get("page")
        try:
            page_i = int(page) if page is not None else 10**9
        except Exception:
            page_i = 10**9

        seq = t.get("seq", None)
        try:
            seq_i = int(seq) if seq is not None else 10**9
        except Exception:
            seq_i = 10**9

        row_order = t.get("__row_order", 10**12)
        try:
            row_order_i = int(row_order)
        except Exception:
            row_order_i = 10**12

        return (
            dt if pd.notna(dt) else pd.Timestamp.max,
            page_i,
            seq_i,
            row_order_i,
        )

    all_tx = sorted(all_tx, key=_sort_key)
    st.session_state.results = all_tx
    final_transaction_count = len(all_tx)

    if processing_stopped:
        update_processing_progress(
            f"Processing stopped at {files_finished} of {total_files} file(s).",
            files_finished,
            variant="warning",
        )
    elif processing_errors:
        update_processing_progress(
            f"Completed with {len(processing_errors)} error(s). "
            f"Extracted {final_transaction_count} transactions from {total_files} file(s).",
            total_steps,
            variant="warning",
        )
    else:
        update_processing_progress(
            f"Successfully processed all {total_files} file(s) and completed extraction of "
            f"{final_transaction_count} transactions from {total_files} file(s).",
            total_steps,
            variant="success",
        )


# ---------------------------------------------------
# DISPLAY
# ---------------------------------------------------
analysis_results = st.session_state.get("integrity_analysis_results", {})

if st.session_state.results:
    high_value_threshold = get_high_value_threshold()
    
    # Convert results to DataFrame
    df = pd.DataFrame(st.session_state.results) if st.session_state.results else pd.DataFrame()
    
    if not df.empty:
        # Run fraud/pattern checks
        df = run_fraud_checks(df, high_value_threshold)
        
        # Display transaction pattern overview
        render_transaction_overview(df, high_value_threshold)

        # Display Extracted Transaction section
        st.markdown("---")
        render_extracted_transaction_section(df)
        
        # Display Counterparty Ledger Table
        st.markdown("---")
        render_counterparty_ledger_table(df)
    else:
        st.info("No line-item transactions extracted.")
    
    # Display Document Integrity Scan
    if analysis_results:
        st.markdown("---")
        render_integrity_report_styles()
        render_integrity_overview(analysis_results)

        for file_name, result in analysis_results.items():
            summary = build_display_summary(result)
            layer_results = result.get("layer_results", {})
            with st.expander(file_risk_label(file_name, result)):
                render_fraud_summary(summary, layer_results)

                if layer_results:
                    for layer_name, findings in layer_results.items():
                        st.markdown(f"**{layer_name}**")
                        if not findings:
                            st.write("No findings.")
                            continue

                        for finding in findings:
                            st.write(
                                f"{severity_badge(finding.get('severity'))} "
                                f"{finding.get('message', '')}"
                            )
                            detail = finding.get("detail")
                            if detail:
                                st.json(detail)
    
    # Original transaction analysis from existing code
    transaction_analysis_report = parse_top_parties_and_high_value(
        st.session_state.results,
        high_value_threshold=high_value_threshold,
    )

    monthly_summary_raw = calculate_monthly_summary(st.session_state.results)
    monthly_summary = present_monthly_summary_standard(monthly_summary_raw)

    if monthly_summary:
        st.subheader("📅 Monthly Summary (Standardized)")
        summary_df = pd.DataFrame(monthly_summary)
        desired_cols = [
            "month",
            "company_name",
            "account_no",
            "transaction_count",
            "opening_balance",
            "total_debit",
            "total_credit",
            "highest_balance",
            "lowest_balance",
            "swing",
            "ending_balance",
            "source_files",
        ]
        summary_df = summary_df[[c for c in desired_cols if c in summary_df.columns]]
        
        st.dataframe(
            summary_df, 
            use_container_width=True,
            column_config={
                "month": "Month",
                "company_name": "Company Name",
                "account_no": "Account Number",
                "transaction_count": "No. of Transactions",
                "opening_balance": "Opening Balance",
                "total_debit": "Total Debit",
                "total_credit": "Total Credit",
                "highest_balance": "Highest Balance",
                "lowest_balance": "Lowest Balance",
                "swing": "Swing",
                "ending_balance": "Ending Balance",
                "source_files": "Source Files"
            }
        )
    
    st.subheader("⬇️ Download Options")
    # Updated to 4 columns for HTML report
    col1, col2, col3, col4 = st.columns(4)

    df_download = df.copy() if not df.empty else pd.DataFrame([])

    # Helper function to convert dataframe to JSON-serializable format
    def make_json_serializable(obj):
        """Recursively convert non-serializable objects to JSON-serializable format"""
        if isinstance(obj, dict):
            return {key: make_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [make_json_serializable(item) for item in obj]
        elif isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        elif isinstance(obj, pd.Period):
            return str(obj)
        elif pd.isna(obj):
            return None
        elif hasattr(obj, 'isoformat'):
            return obj.isoformat()
        else:
            return obj

    # Convert transactions to JSON-serializable format
    if not df_download.empty:
        json_records = df_download.to_dict(orient="records")
        json_records = make_json_serializable(json_records)
    else:
        json_records = []

    with col1:
        st.download_button(
            "📄 Download Full Transactions (JSON)",
            json.dumps(json_records, indent=4),
            "transactions.json",
            "application/json",
            use_container_width=True
        )

    with col2:
        # Handle date conversion for display
        if "date" in df_download.columns and not df_download.empty:
            date_min = df_download["date"].min()
            date_max = df_download["date"].max()
            date_min_str = date_min.isoformat() if isinstance(date_min, pd.Timestamp) else str(date_min)
            date_max_str = date_max.isoformat() if isinstance(date_max, pd.Timestamp) else str(date_max)
            date_range_str = f"{date_min_str} to {date_max_str}"
        else:
            date_range_str = None

        total_files_processed = None
        if "source_file" in df_download.columns and not df_download.empty:
            total_files_processed = int(df_download["source_file"].nunique())

        company_names = sorted(
            {x for x in df_download.get("company_name", pd.Series([], dtype=object)).dropna().astype(str).tolist() if x.strip()}
        )

        account_nos = sorted(
            {x for x in df_download.get("account_no", pd.Series([], dtype=object)).dropna().astype(str).tolist() if x.strip()}
        )

        # Convert monthly_summary to JSON-serializable format
        serializable_monthly_summary = make_json_serializable(monthly_summary)
        
        # Convert transaction_analysis_report to JSON-serializable format
        serializable_transaction_analysis = make_json_serializable(transaction_analysis_report)
        serializable_pdf_integrity = make_json_serializable(analysis_results)

        full_report = {
            "summary": {
                "total_transactions": int(len(df_download)),
                "date_range": date_range_str,
                "total_files_processed": total_files_processed,
                "company_names": company_names,
                "account_nos": account_nos,
                "high_value_threshold": high_value_threshold,
                "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "transaction_analysis": serializable_transaction_analysis,
            "monthly_summary": serializable_monthly_summary,
            "pdf_integrity": serializable_pdf_integrity,
            "transactions": json_records,
        }

        st.download_button(
            "📊 Download Full Transaction (JSON)",
            json.dumps(full_report, indent=4),
            "full_report.json",
            "application/json",
            use_container_width=True
        )

    with col3:
        # Make sure data is JSON serializable first
        serialized_transactions = make_json_serializable(st.session_state.results)
        serialized_monthly_summary = make_json_serializable(monthly_summary)
        serialized_transaction_analysis = make_json_serializable(transaction_analysis_report)
    
        report_excel_data = build_report_data_from_analysis(
            serialized_transactions,
            serialized_monthly_summary,
            serialized_transaction_analysis,
            high_value_threshold,
        )
        output = generate_excel_report(report_excel_data)

        st.download_button(
            "📊 Download Full Report (XLSX)",
            output.getvalue(),
            "full_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    with col4:
        # NEW: Generate and download HTML report from current data
        if st.session_state.results:
            try:
                html_content = generate_html_report_from_data(
                    st.session_state.results,
                    monthly_summary,
                    transaction_analysis_report,
                    high_value_threshold
                )
                
                # Get company name for filename
                company_name = st.session_state.company_name_override or "company"
                if company_name == "company" and not st.session_state.results:
                    company_name = "report"
                else:
                    # Try to get from first transaction
                    if st.session_state.results and st.session_state.results[0].get("company_name"):
                        company_name = st.session_state.results[0]["company_name"]
                
                safe_name = company_name.replace(' ', '_').replace('/', '_')
                
                st.download_button(
                    "🌐 Download Interactive HTML Report",
                    html_content.encode('utf-8'),
                    f"{safe_name}_statement_report.html",
                    "text/html; charset=utf-8",
                    use_container_width=True,
                    help="Download an interactive HTML report with charts and analysis"
                )
            except Exception as e:
                st.error(f"Failed to generate HTML report: {e}")

else:
    if (
        uploaded_files
        and st.session_state.status == "idle"
        and not st.session_state.high_value_threshold_error
        and not st.session_state.bank_choice_error
        and not st.session_state.pdf_upload_error
    ):
        st.warning("⚠️ No transactions found — click **Start Processing**.")
