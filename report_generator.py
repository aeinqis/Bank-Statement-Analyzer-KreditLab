# Extracted from app.py to keep the Streamlit entrypoint smaller.
from __future__ import annotations

import copy
import hashlib
import json
import re
import textwrap
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from core_utils import safe_float
except Exception:  # pragma: no cover - rebound from app.py during normal use
    safe_float = float


def bind_app_globals(app_globals: dict) -> None:
    """Expose app.py helpers/constants that these extracted functions already use."""
    for name, value in app_globals.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = value


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

    def build_round_figure_credits_internal(transactions):
        """Build the same signed round-number transactions shown in Railway."""
        return build_round_transactions(transactions)
    
    # Extract data
    _fallback_consol = data.get('consolidated') if isinstance(data.get('consolidated'), dict) else {}
    _fallback_summary = data.get('summary') if isinstance(data.get('summary'), dict) else {}
    _fallback_config = data.get('classification_config') if isinstance(data.get('classification_config'), dict) else {}
    fallback_large_threshold = (
        _fallback_consol.get('high_value_threshold')
        or _fallback_summary.get('high_value_threshold')
        or _fallback_config.get('large_transaction_threshold')
        or _fallback_config.get('large_credit_threshold')
        or 100000
    )
    large_txns = data.get('large_transactions', [])
    if not large_txns and data.get('transactions'):
        large_txns = build_large_transactions_internal(data.get('transactions', []), fallback_large_threshold)

    r = data.get('report_info', {})
    accounts = data.get('accounts', [])
    monthly = data.get('monthly_analysis', [])
    consol = data.get('consolidated', {})

 

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
    _sync_data_quality_status(data)
    consol = data.get('consolidated', {})
    flags_data = data.get('flags', {})
    obs = normalize_observations(data.get('observations', {}))
    parsing = data.get('parsing_metadata', {})
    company_name = r.get('company_name', 'Company')
    related_parties = filter_report_related_parties(r.get('related_parties', []), company_name=company_name)
    report_counterparty_rows = copy_report_counterparty_rows(
        data.get('report_counterparty_rows') or data.get('counterparty_ledger_rows')
    )

    # Prepare top parties before feature detection; later rendering depends on it.
    top_parties = data.get('top_parties', {})
    cp_ledger_for_top = data.get('counterparty_ledger', {})
    if isinstance(cp_ledger_for_top, dict) and cp_ledger_for_top.get('counterparties'):
        report_counterparty_rows = get_report_counterparty_rows_from_data(
            data,
            cp_ledger_for_top,
            related_parties=related_parties,
            own_related=own_related,
            company_name=company_name,
        )
        top_parties = _top_parties_from_counterparty_rows(
            report_counterparty_rows,
            limit=None,
            company_name=company_name,
        )
        data['top_parties'] = top_parties
    elif not top_parties or not _has_top_party_rows(top_parties):
        top_parties = {"top_payers": [], "top_payees": []}
    if not isinstance(top_parties, dict):
        top_parties = {}

    # Version detection
    schema_v = r.get('schema_version', '')
    is_v620 = schema_v in ('6.2.0', '6.2.1', '6.2.2', '6.3.0', '6.3.1', '6.3.2', '6.3.3', '6.3.4', '6.3.5') or consol.get('total_fx_credits') is not None
    is_v630 = schema_v in ('6.3.0', '6.3.1', '6.3.2', '6.3.3', '6.3.4', '6.3.5') or consol.get('total_unclassified_cr') is not None
    is_v635 = schema_v in ('6.3.4', '6.3.5')
    has_parsing = bool(parsing)
    has_monthly_bd = any(p.get('monthly_breakdown') for p in (top_parties.get('top_payers') or top_parties.get('top_creditors') or []) + (top_parties.get('top_payees') or top_parties.get('top_debtors') or []))

    # v6.2.1: Data quality detection
    parsing_checks = parsing.get('account_month_checks', []) if isinstance(parsing, dict) else []
    recon_lookup = {}
    for chk in parsing_checks:
        month_key = str(chk.get('month', '') or '')
        account_key = str(chk.get('account_number', '') or '')
        recon_lookup[(month_key, account_key)] = chk

    def _recon_check_for_month_row(row):
        if not isinstance(row, dict):
            return None
        month_key = str(row.get('month', '') or '')
        account_key = str(row.get('account_number', '') or '')
        return recon_lookup.get((month_key, account_key)) or recon_lookup.get((month_key, ''))

    def _recon_status_for_month_row(row):
        chk = _recon_check_for_month_row(row)
        if chk is not None:
            return 'PASS' if chk.get('passed', False) else 'FAIL'
        return row.get('reconciliation_status', '')

    def _recon_gap_count_for_month_row(row):
        chk = _recon_check_for_month_row(row)
        if chk is not None:
            return int(chk.get('extraction_gaps', 0) or 0)
        return int(row.get('extraction_gaps', 0) or 0)

    data_completeness = consol.get('data_completeness', 'COMPLETE')
    has_recon = bool(parsing_checks) or any(m.get('reconciliation_status') for m in monthly)
    if parsing_checks:
        failed_checks = [chk for chk in parsing_checks if not chk.get('passed', False)]
        is_incomplete = bool(failed_checks)
        months_with_gaps = len({str(chk.get('month', '') or '') for chk in failed_checks if chk.get('month')})
        total_gaps = sum(int(chk.get('extraction_gaps', 0) or 0) for chk in parsing_checks)
        if not failed_checks:
            total_missing_dr = 0
            total_missing_cr = 0
        else:
            total_missing_dr = consol.get('total_missing_debits', 0) or 0
            total_missing_cr = consol.get('total_missing_credits', 0) or 0
    else:
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
    related_parties = filter_report_related_parties(r.get('related_parties', []), company_name=company)

    # ── Date format: convert from YYYY-MM-DD to YYYY-MM ──
    def _format_to_year_month(date_str):
        """Convert '2026-06-20' to '2026-06'"""
        if not date_str:
            return ''
        parts = str(date_str).split('-')
        if len(parts) >= 2:
            return f"{parts[0]}-{parts[1]}"
        return date_str

    period_start_display = _format_to_year_month(period_start)
    period_end_display = _format_to_year_month(period_end)

    # Build data quality banner HTML
    dq_banner_html = ''
    if has_recon:
        if is_incomplete:
            if parsing_checks:
                failed_months = [chk for chk in parsing_checks if not chk.get('passed', False)]
            else:
                failed_months = [m for m in monthly if m.get('reconciliation_status') == 'FAIL']
            affected_months = ', '.join(sorted({str(m.get('month', '') or '') for m in failed_months if m.get('month')}))
            has_identified_gaps = total_gaps > 0 or total_missing_dr > 0 or total_missing_cr > 0
            if has_identified_gaps:
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
                largest_delta = max((abs(float(m.get('reconciliation_delta') or 0)) for m in failed_months), default=0)
                dq_banner_html = f'''
                <div class="dq-banner dq-fail">
                    <div class="dq-icon">⚠️</div>
                    <div>
                        <div class="dq-title">Balance Reconciliation Warning — {months_with_gaps} of {total_months} Months Failed</div>
                        <div class="dq-detail">No extraction gaps were identified, but the expected closing balance does not match the statement closing balance for the affected month(s). Review opening balance, closing balance, and parsed transaction totals.</div>
                        <div class="dq-stats">
                            <div><div class="dq-stat-label">Failed Checks</div><div class="dq-stat-val">{len(failed_months)}</div></div>
                            <div><div class="dq-stat-label">Largest Delta</div><div class="dq-stat-val">RM {largest_delta:,.2f}</div></div>
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

    def _related_party_display_name(rp):
        if isinstance(rp, dict):
            raw_name = rp.get('name') or rp.get('party_name') or ''
        else:
            raw_name = str(rp or '')
        return re.sub(r'\s+', ' ', str(raw_name).strip())

    related_party_names = [
        name for name in (_related_party_display_name(rp) for rp in related_parties)
        if name
    ]
    deduped_related_parties = []
    for rp in related_parties:
        name = _related_party_display_name(rp)
        if not name:
            continue
        if any(
            _report_party_names_equivalent(name, _related_party_display_name(existing))
            for existing in deduped_related_parties
        ):
            continue
        deduped_related_parties.append(rp)
    related_parties = deduped_related_parties
    related_party_names = [
        name for name in (_related_party_display_name(rp) for rp in related_parties)
        if name
    ]
    rp_html = ""
    for rp in related_parties:
        name = rp.get('name', rp) if isinstance(rp, dict) else str(rp)
        rel = rp.get('relationship', '') if isinstance(rp, dict) else ''
        rp_html += f'<span class="rp-tag">{name} <small>({rel})</small></span>'

    if isinstance(cp_ledger_for_top, dict) and cp_ledger_for_top.get('counterparties'):
        report_counterparty_rows = get_report_counterparty_rows_from_data(
            data,
            cp_ledger_for_top,
            related_parties=related_parties,
            own_related=own_related,
            company_name=company_name,
        )
        top_parties = _top_parties_from_counterparty_rows(
            report_counterparty_rows,
            limit=None,
            company_name=company_name,
        )
        data['top_parties'] = top_parties

    def _matched_related_party_name(counterparty_name, description):
        cp_upper = str(counterparty_name or '').upper()
        desc_upper = str(description or '').upper()
        matches = []
        for name in related_party_names:
            name_upper = name.upper()
            if (
                name_upper in cp_upper
                or name_upper in desc_upper
                or _report_candidate_contains_party_tokens(counterparty_name, name)
                or _report_candidate_contains_party_tokens(description, name)
            ):
                matches.append(name)
        if not matches:
            return ''
        return max(matches, key=lambda value: (len(value), value.casefold()))

    _own_party_display_fallbacks = {
        'UNKNOWN', 'UNKNOWN PARTY', 'COMPANY', 'OWN PARTY', 'OWN PARTY (SELF)', 'SELF'
    }

    def _own_party_display_name(raw_party_name):
        for value in (raw_party_name, company_name):
            name = re.sub(
                r'\s*\(\s*OWN[\s\-_]?PARTY\s*\)\s*',
                ' ',
                str(value or ''),
                flags=re.I,
            )
            name = re.sub(r'\s+', ' ', name).strip()
            if name and name.upper() not in _own_party_display_fallbacks:
                return name
        return 'Own Party (Self)'

    def _advisory_related_party_candidates_for_report():
        by_name = {}
        confirmed_upper = {name.upper() for name in related_party_names}
        order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}

        def add_candidate(candidate):
            if not isinstance(candidate, dict):
                return
            display_name = re.sub(r'\s+', ' ', str(candidate.get('name') or '').strip())
            key = display_name.upper()
            if (
                not key
                or key in confirmed_upper
                or any(
                    _report_party_names_equivalent(display_name, confirmed_name)
                    for confirmed_name in related_party_names
                )
            ):
                return

            current = {
                'name': display_name,
                'confidence': str(candidate.get('confidence', 'MEDIUM') or 'MEDIUM').upper(),
                'evidence': candidate.get('evidence', 'Flagged by Track 2 engine'),
                'total_cr': safe_float(candidate.get('total_cr', 0)),
                'total_dr': safe_float(candidate.get('total_dr', 0)),
                'score': candidate.get('score'),
                'debit_count': candidate.get('debit_count', 0),
                'credit_count': candidate.get('credit_count', 0),
                'debit_month_count': candidate.get('debit_month_count', 0),
                'signals': candidate.get('signals', []),
            }
            alias_key = next(
                (
                    existing_key for existing_key, existing_candidate in by_name.items()
                    if _report_party_names_equivalent(
                        display_name,
                        existing_candidate.get('name', ''),
                    )
                ),
                None,
            )
            if alias_key is not None:
                key = alias_key
            existing = by_name.get(key)
            if existing is not None:
                previous_total_dr = safe_float(existing.get('total_dr', 0))
                merged_signals = list(existing.get('signals') or [])
                for signal in current.get('signals') or []:
                    if signal not in merged_signals:
                        merged_signals.append(signal)
                existing['signals'] = merged_signals
                existing['total_cr'] = max(safe_float(existing.get('total_cr', 0)), current['total_cr'])
                existing['total_dr'] = max(safe_float(existing.get('total_dr', 0)), current['total_dr'])
                existing['debit_count'] = max(
                    int(safe_float(existing.get('debit_count', 0))),
                    int(safe_float(current.get('debit_count', 0))),
                )
                existing['credit_count'] = max(
                    int(safe_float(existing.get('credit_count', 0))),
                    int(safe_float(current.get('credit_count', 0))),
                )
                existing['debit_month_count'] = max(
                    int(safe_float(existing.get('debit_month_count', 0))),
                    int(safe_float(current.get('debit_month_count', 0))),
                )
                if (
                    order.get(current['confidence'], 9) < order.get(existing.get('confidence'), 9)
                    or current['total_dr'] > previous_total_dr
                ):
                    existing['confidence'] = current['confidence']
                    existing['evidence'] = current['evidence']
                    existing['score'] = current.get('score')
                return

            by_name[key] = current

        for candidate in r.get('related_party_candidates', []) or []:
            add_candidate(candidate)

        if _TRACK2_AVAILABLE:
            candidate_ledgers = []

            def _add_candidate_ledger(ledger):
                if not isinstance(ledger, dict) or not ledger.get('counterparties'):
                    return
                if any(ledger is existing for existing in candidate_ledgers):
                    return
                candidate_ledgers.append(ledger)

            _add_candidate_ledger(data.get('counterparty_ledger'))
            if data.get('transactions'):
                try:
                    _add_candidate_ledger(build_track2_counterparty_ledger(data.get('transactions', [])))
                except Exception:
                    pass
            for cp_ledger_for_candidates in candidate_ledgers:
                try:
                    live_candidates = advisory_rp_candidates(
                        scan_related_party_candidates(cp_ledger_for_candidates),
                        related_party_names,
                        [company],
                    )
                    for candidate in live_candidates:
                        add_candidate(candidate)
                except Exception:
                    pass

        priority_signals = {'monthly_recurrence', 'personal_keyword_sweep'}
        return sorted(
            by_name.values(),
            key=lambda item: (
                0 if any(sig in (item.get('signals') or []) for sig in priority_signals) else 1,
                -int(safe_float(item.get('debit_month_count', 0))),
                order.get(str(item.get('confidence', '')).upper(), 9),
                -safe_float(item.get('total_dr', 0)),
                str(item.get('name', '')).casefold(),
            ),
        )

    # ── Related-party candidates (advisory only; analyst confirms) ──
    # MEDIUM/LOW RP3 near-misses that did NOT auto-confirm and exclude nothing.
    # Surfaced so the analyst sees them instead of hunting the full ledger.
    rp_candidates = _advisory_related_party_candidates_for_report()
    rp_candidates_html = ""
    if rp_candidates:
        _cand_rows = ""
        for c in rp_candidates:
            conf = str(c.get('confidence', '') or '').upper()
            dr = c.get('total_dr', 0) or 0
            cr = c.get('total_cr', 0) or 0
            _cand_rows += (
                '<tr>'
                f'<td>{c.get("name", "")}</td>'
                f'<td><span class="rpc-badge rpc-{conf.lower()}">{conf}</span></td>'
                f'<td style="text-align:right">RM {dr:,.2f}</td>'
                f'<td style="text-align:right">RM {cr:,.2f}</td>'
                f'<td style="font-size:0.8rem;color:var(--text-soft)">{c.get("evidence", "")}</td>'
                '</tr>'
            )
        _total = r.get('related_party_candidates_total', len(rp_candidates)) or len(rp_candidates)
        _shown = len(rp_candidates)
        _cap_note = (
            f' Showing {_shown} of {_total} flagged individuals, prioritising recurring debit-month signals.'
            if _total > _shown else ''
        )
        rp_candidates_html = (
            '<div class="rpc-note">These individuals show some related-party signals but did '
            '<b>not</b> meet the auto-confirm threshold, so they are <b>not</b> excluded from any '
            'figure. Review each and confirm in the analysis step if genuinely related.'
            f'{_cap_note}</div>'
            '<div class="table-wrap"><table>'
            '<thead><tr><th>Party</th><th>Confidence</th><th>Debits</th><th>Credits</th>'
            '<th>Why flagged</th></tr></thead>'
            f'<tbody>{_cand_rows}</tbody></table></div>'
        )

    # ── Monthly analysis table rows ──
    from collections import OrderedDict
    monthly_by_month = OrderedDict()
    for m in monthly:
        mo = m.get('month', '')
        if mo not in monthly_by_month:
            monthly_by_month[mo] = []
        monthly_by_month[mo].append(m)

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
    chart_agg = OrderedDict()
    has_account_col = any(m.get('account_number') for m in monthly)

    for mo, rows in monthly_by_month.items():
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
        agg['opening_balance'] = sum(r.get('opening_balance', 0) or 0 for r in rows)
        agg['closing_balance'] = sum(r.get('closing_balance', 0) or 0 for r in rows)
        chart_agg[mo] = agg

        if has_account_col and len(rows) > 1:
            for m in rows:
                an = m.get('account_number', '')
                bn = m.get('bank_name', '')
                short_bank = bn.split(' ')[0] if bn else ''
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

            a = agg
            month_recon_cell = ''
            if has_recon:
                any_fail = any(_recon_status_for_month_row(r) == 'FAIL' for r in rows)
                if any_fail:
                    total_gaps = sum(_recon_gap_count_for_month_row(r) for r in rows)
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
            m = rows[0] if rows else {}
            recon_status = _recon_status_for_month_row(m)
            row_class = ' class="row-fail"' if recon_status == 'FAIL' else ''
            recon_cell = ''
            if has_recon:
                if recon_status == 'FAIL':
                    gap_count = _recon_gap_count_for_month_row(m)
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

    # ── Top parties with ghost-verb suppression ──
    # Now pass company_name to prepare_top_parties_for_report
    party_view = prepare_top_parties_for_report(top_parties, company_name=company_name)
    _payers = party_view['payers']
    _payees = party_view['payees']
    _payers_suppressed = party_view['payers_suppressed']
    _payees_suppressed = party_view['payees_suppressed']

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
                <span style="background:var(--amber);color:white;padding:1px 6px;border-radius:3px;font-size:0.7rem">VERIFY</span> = high volume — possible real-entity false positive, please cross-check.
            </div>
            <table style="width:100%;font-size:0.78rem"><thead><tr>
                <th style="text-align:left">Bucket (parser artifact)</th>
                <th class="r">Amount (RM)</th><th class="r">Txns</th>
            </tr></thead><tbody>{rows}</tbody></table>
        </div>'''

    payers_suppressed_html = _render_suppressed(_payers_suppressed, 'credit')
    payees_suppressed_html = _render_suppressed(_payees_suppressed, 'debit')

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
        if not month_str:
            return ''
        parts = str(month_str).split('-')
        if len(parts) >= 2 and parts[1] in _MONTH_ABBR:
            return _MONTH_ABBR[parts[1]]
        return str(month_str)[-5:]

    def _render_monthly_bars(monthly_breakdown, color_var):
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

    # Updated payer_rows using related_parties list and is_own_party flag
    payer_rows = ""
    for p in _payers:
        party_name = p.get('party_name', '')
        party_upper = party_name.upper() if party_name else ''
        
        # Check if this is a Related Party
        is_related = False
        for rp in related_parties:
            rp_name = _related_party_display_name(rp)
            if rp_name and rp_name.upper() in party_upper:
                is_related = True
                break
        
        # Determine badge type - use the is_own_party flag from prepare_top_parties_for_report
        badge = ''
        if is_related:
            badge = '<span class="rp-badge">RP</span>'
        elif p.get('is_own_party', False):
            badge = '<span class="op-badge">OP</span>'
        
        mb_html = _render_monthly_bars(p.get('monthly_breakdown'), 'var(--green)')
        payer_rows += f'''<tr>
            <td>{p.get('rank')}</td>
            <td>{party_name} {badge}{mb_html}</td>
            <td class="mono r credit">RM {p.get('total_amount',0):,.2f}</td>
            <td class="mono r">{p.get('transaction_count',0)}</td>
        </tr>'''

    # Updated payee_rows using related_parties list and is_own_party flag
    payee_rows = ""
    for p in _payees:
        party_name = p.get('party_name', '')
        party_upper = party_name.upper() if party_name else ''
        
        # Check if this is a Related Party
        is_related = False
        for rp in related_parties:
            rp_name = _related_party_display_name(rp)
            if rp_name and rp_name.upper() in party_upper:
                is_related = True
                break
        
        # Determine badge type - use the is_own_party flag from prepare_top_parties_for_report
        badge = ''
        if is_related:
            badge = '<span class="rp-badge">RP</span>'
        elif p.get('is_own_party', False):
            badge = '<span class="op-badge">OP</span>'
        
        mb_html = _render_monthly_bars(p.get('monthly_breakdown'), 'var(--red)')
        payee_rows += f'''<tr>
            <td>{p.get('rank')}</td>
            <td>{party_name} {badge}{mb_html}</td>
            <td class="mono r debit">RM {p.get('total_amount',0):,.2f}</td>
            <td class="mono r">{p.get('transaction_count',0)}</td>
        </tr>'''

    # ── Large transactions ──
    large_threshold = safe_float(
        consol.get('high_value_threshold')
        or consol.get('large_credit_threshold')
        or _fallback_summary.get('high_value_threshold')
        or _fallback_config.get('large_transaction_threshold')
        or _fallback_config.get('large_credit_threshold')
        or 100000
    )
    if large_threshold <= 0:
        large_threshold = 100000
    
    large_txns = data.get('large_transactions', [])
    if (not large_txns or len(large_txns) == 0) and data.get('transactions'):
        try:
            large_txns = build_large_transactions(data.get('transactions', []), large_threshold)
        except Exception as e:
            print(f"[DEBUG] Error building large transactions: {e}")
            large_txns = []
    
    large_credits = data.get('large_credits', [])
    if (not large_txns or len(large_txns) == 0) and large_credits:
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
            large_txn_rows += f'''
                <tr>
                    <td>{date_str}</td>
                    <td>{desc_str}</td>
                    <td class="mono r {type_cls}">RM {float(amount):,.2f}</td>
                    <td class="mono r">RM {float(balance):,.2f}</td>
                </tr>'''
    else:
        large_txn_rows = f'''
                <tr>
                    <td colspan="4" class="note">No transactions above RM {large_threshold:,.0f}</td>
                </tr>'''
    
    round_figure_credits = get_round_transactions_for_report(data)
    if not round_figure_credits and data.get('transactions'):
        round_figure_credits = build_round_figure_credits_internal(data.get('transactions', []))
    rf_cr_rows = ""
    if round_figure_credits and isinstance(round_figure_credits, list):
        for t in round_figure_credits:
            if not isinstance(t, dict):
                continue
            amount = safe_float(t.get('amount', t.get('credit', 0)))
            amount_class = 'credit' if amount >= 0 else 'debit'
            amount_text = f"RM {abs(amount):,.2f}"
            balance = safe_float(t.get('balance', 0))
            rf_cr_rows += f'''
                <tr>
                    <td>{escape(str(t.get('date', '')))}</td>
                    <td>{escape(str(t.get('description', ''))[:70])}</td>
                    <td class="mono r {amount_class}">{amount_text}</td>
                    <td class="mono r">RM {balance:,.2f}</td>
                </tr>'''
    if not rf_cr_rows:
        rf_cr_rows = '<tr><td colspan="4" class="note">No round-figure transactions detected.</td></tr>'

    _sync_transaction_pattern_flags(
        data,
        round_transactions=round_figure_credits,
        large_transactions=large_txns,
        large_threshold=large_threshold,
    )

    # ── Related party transactions ──
    rp_summary = own_related.get('summary', {}) or {}
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
    rp_party_rows = ""
    rp_group_list = build_own_related_party_groups_for_report(
        own_related,
        related_parties=related_parties,
        company_name=company_name,
        counterparty_rows=report_counterparty_rows,
        manual_company_identity_override=bool(r.get('manual_company_identity_override')),
        company_account_no=r.get('manual_company_account_no') or r.get('company_account_no') or '',
    )
    if rp_group_list:
        rp_counts = {
            'own_party_cr': sum(int(group.get('credit_count', 0) or 0) for group in rp_group_list if group.get('badge_type') == 'OP'),
            'own_party_dr': sum(int(group.get('debit_count', 0) or 0) for group in rp_group_list if group.get('badge_type') == 'OP'),
            'related_party_cr': sum(int(group.get('credit_count', 0) or 0) for group in rp_group_list if group.get('badge_type') == 'RP'),
            'related_party_dr': sum(int(group.get('debit_count', 0) or 0) for group in rp_group_list if group.get('badge_type') == 'RP'),
        }
    for idx, group in enumerate(rp_group_list):
        badge_cls = 'op-badge' if group['badge_type'] == 'OP' else 'rp-badge'
        detail_rows = ""
        for t in group['transactions']:
            txn_type = str(t.get('type') or '').upper()
            type_cls = 'credit' if txn_type == 'CREDIT' else 'debit'
            amount = safe_float(t.get('amount', 0))
            balance = safe_float(t.get('balance', 0))
            detail_rows += f'''<tr>
                <td>{escape(str(t.get('date','')))}</td>
                <td>{escape(str(t.get('description',''))[:80])}</td>
                <td class="mono r {type_cls}">RM {amount:,.2f}</td>
                <td><span class="badge badge-{escape(txn_type.lower())}">{escape(txn_type)}</span></td>
                <td class="mono r">RM {balance:,.2f}</td>
            </tr>'''

        txn_count = int(group.get('transaction_count') or len(group['transactions']))
        rp_party_rows += f'''
        <tr class="rp-party-row" onclick="toggleRp('rp-detail-{idx}')" style="cursor:pointer">
            <td><span id="rp-caret-{idx}">▶</span> {escape(group['party_name'])} <span class="{badge_cls}">{group['badge_type']}</span></td>
            <td class="mono r credit">{group['credits']:,.2f} <span style="color:var(--text-muted);font-size:0.75rem">({group['credit_count']})</span></td>
            <td class="mono r debit">{group['debits']:,.2f} <span style="color:var(--text-muted);font-size:0.75rem">({group['debit_count']})</span></td>
            <td>{escape(group['party_type'])}</td>
            <td class="mono r">{txn_count}</td>
        </tr>
        <tr id="rp-detail-{idx}" style="display:none"><td colspan="5" style="background:var(--bg);padding:0">
            <div class="table-wrap"><table style="margin:0">
                <thead><tr><th>Date</th><th>Description</th><th class="r">Amount</th><th>Type</th><th class="r">Balance</th></tr></thead>
                <tbody>{detail_rows or '<tr><td colspan="5" class="note">No transactions</td></tr>'}</tbody>
            </table></div>
        </td></tr>'''

    rp_expander_script = '''
                    <script>
                        function toggleRp(id) {
                            var row = document.getElementById(id);
                            if (!row) return;
                            var caret = document.getElementById(id.replace('detail','caret'));
                            if (row.style.display === 'none') { row.style.display = ''; if (caret) caret.textContent = '▼'; }
                            else { row.style.display = 'none'; if (caret) caret.textContent = '▶'; }
                        }
                    </script>'''

    # ── Counterparty Ledger ──
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

        ext_stats = cp_ledger.get('extraction_stats') if isinstance(cp_ledger.get('extraction_stats'), dict) else {}
        ext_pattern = ext_stats.get('pattern_matched', 0)
        ext_bucket = ext_stats.get('special_bucket', 0)

        status_color = {'CLEANED': 'green', 'VALIDATION_FAILED': 'amber', 'SKIPPED': 'amber'}.get(cleaning_status, 'text-muted')
        status_badge = f'<span class="badge" style="background:var(--{status_color}-dim);color:var(--{status_color})">{cleaning_status or "N/A"}</span>' if cleaning_status else ''

        val_fail_warning = ''
        if cleaning_status == 'VALIDATION_FAILED':
            val_fail_warning = '<div style="background:var(--amber-dim);border:1px solid var(--amber);color:var(--amber);margin:0.75rem 0;padding:0.75rem;border-radius:8px;display:flex;gap:0.5rem;align-items:center"><div>⚠️</div><div><div style="font-weight:600">Counterparty ledger cleaning failed validation</div><div style="font-size:0.85rem">Showing original parser output.</div></div></div>'

        raw_counterparties = cp_ledger.get('counterparties', []) or []
        counterparties_sorted = report_counterparty_rows
        if not counterparties_sorted:
            counterparties_sorted = get_report_counterparty_rows_from_data(
                data,
                cp_ledger,
                related_parties=related_parties,
                own_related=own_related,
                company_name=company_name,
            )
        report_counterparty_rows = counterparties_sorted
        total_cp = len(counterparties_sorted)
        row_pattern = _counterparty_row_stat_total(counterparties_sorted, 'pattern_matched')
        row_bucket = _counterparty_row_stat_total(counterparties_sorted, 'special_bucket', exclude_unknown=True)
        ext_pattern = row_pattern if _counterparty_rows_have_stat(counterparties_sorted, 'pattern_matched') else ext_pattern
        ext_bucket = row_bucket if _counterparty_rows_have_stat(counterparties_sorted, 'special_bucket') else ext_bucket
        ext_raw = _counterparty_unknown_transaction_count(counterparties_sorted)

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
                        <div class="summary-card"><div class="val">{int(ext_pattern or 0):,}</div><div class="lbl">Pattern matched</div></div>
                        <div class="summary-card"><div class="val">{int(ext_bucket or 0):,}</div><div class="lbl">Special bucket</div></div>
                        <div class="summary-card"><div class="val">{int(ext_raw or 0):,}</div><div class="lbl">Raw fallback</div></div>
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

    # ── Statutory Compliance ──
    statutory_html = ''
    stat_comp = consol.get('statutory_compliance')
    if stat_comp:
        overall = stat_comp.get('overall_status', 'N/A')
        overall_color = {'COMPLIANT': 'green', 'GAPS_DETECTED': 'amber', 'CRITICAL': 'red'}.get(overall, 'amber')

        def _cov_bar(label, paid, total, missing, paid_list=None, salary_list=None):
            if not total:
                return f'<div class="summary-card"><div class="val">N/A</div><div class="lbl">{label}</div></div>'
            if paid_list is not None and salary_list is not None and salary_list:
                covered = len(set(paid_list) & set(salary_list))
                display_paid = covered
                display_total = len(salary_list)
            else:
                display_total = total
                display_paid = min(paid, total)
            pct = (display_paid / display_total * 100) if display_total else 0
            pct = max(0.0, min(pct, 100.0))
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

        salary_list = stat_comp.get('salary_months_list') or []
        salary_months = _as_int(stat_comp.get('salary_months_active', 0)) or len(salary_list)
        epf_list = stat_comp.get('epf_months_list') or []
        epf_paid = _as_int(stat_comp.get('epf_months_paid', 0)) or len(epf_list)
        epf_missing = stat_comp.get('epf_months_missing', []) or []
        socso_list = stat_comp.get('socso_months_list') or []
        socso_paid = _as_int(stat_comp.get('socso_months_paid', 0)) or len(socso_list)
        socso_missing = stat_comp.get('socso_months_missing', []) or []
        lhdn_det = stat_comp.get('lhdn_detected', False)
        lhdn_list = stat_comp.get('lhdn_months_list') or []
        lhdn_paid = _as_int(stat_comp.get('lhdn_months_paid', 0)) or len(lhdn_list)
        hrdf_det = stat_comp.get('hrdf_detected', False)
        hrdf_list = stat_comp.get('hrdf_months_list') or []
        hrdf_paid = _as_int(stat_comp.get('hrdf_months_paid', 0)) or len(hrdf_list)

        cov_cards = _cov_bar('EPF Coverage', epf_paid, salary_months, epf_missing, epf_list, salary_list)
        cov_cards += _cov_bar('SOCSO Coverage', socso_paid, salary_months, socso_missing, socso_list, salary_list)

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
    def _facility_amount(t):
        try:
            return float(t.get("amount") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _is_real_facility(t, expected_category):
        if not isinstance(t, dict):
            return False
        return t.get("category") == expected_category and _facility_amount(t) > 0

    loan_disbursements = [
        t for t in (loans.get("disbursements") or [])
        if _is_real_facility(t, "loan_disbursement")
    ]

    loan_repayments = [
        t for t in (loans.get("repayments") or [])
        if _is_real_facility(t, "loan_repayment")
    ]

    loan_disb_total = round(sum(_facility_amount(t) for t in loan_disbursements), 2)
    loan_repay_total = round(sum(_facility_amount(t) for t in loan_repayments), 2)

    loan_disb_rows = ""
    for t in loan_disbursements:
        loan_disb_rows += f'''<tr>
            <td>{escape(str(t.get('date','')))}</td>
            <td>{escape(str(t.get('description',''))[:55])}</td>
            <td class="mono r credit">RM {_facility_amount(t):,.2f}</td>
            <td>{escape(str(t.get('category','')))}</td>
        </tr>'''

    loan_repay_rows = ""
    for t in loan_repayments:
        loan_repay_rows += f'''<tr>
            <td>{escape(str(t.get('date','')))}</td>
            <td>{escape(str(t.get('description',''))[:55])}</td>
            <td class="mono r debit">RM {_facility_amount(t):,.2f}</td>
            <td>{escape(str(t.get('category','')))}</td>
        </tr>'''

    # ── Flags ──
    # IMPORTANT: Process flags BEFORE building the HTML to define detected_count and total_flags
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

    total_flags = len(flags_data.get('indicators', []))

    # ── Observations ──
    pos_obs = "".join([
        f'<li class="obs-item positive">{escape(str(o))}</li>'
        for o in obs.get('positive', [])
    ])
    con_obs_items = obs.get('concerns', [])
    con_obs = "".join([
        (
            f'<li class="obs-item '
            f'{"data-warn" if "DATA QUALITY" in str(o).upper() or "INCOMPLETE" in str(o).upper() or "extraction gap" in str(o).lower() else "concern"}'
            f'">{escape(str(o))}</li>'
        )
        for o in con_obs_items
    ])

    # ── Chart data ──
    if chart_agg:
        chart_months = json.dumps(list(chart_agg.keys()))
        chart_net_cr = json.dumps([round(a['net_credits'], 2) for a in chart_agg.values()])
        chart_net_dr = json.dumps([round(a['net_debits'], 2) for a in chart_agg.values()])
        chart_eod_avg = json.dumps([round(a['eod_average'], 2) for a in chart_agg.values()])
        chart_eod_low = json.dumps([round(a['eod_lowest'], 2) for a in chart_agg.values()])
        chart_eod_high = json.dumps([round(a['eod_highest'], 2) for a in chart_agg.values()])
        fx_chart_cr = []
        fx_chart_dr = []
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
        fx_chart_cr = [m.get('fx_credit_amount', 0) for m in monthly] if is_v620 else []
        fx_chart_dr = [m.get('fx_debit_amount', 0) for m in monthly] if is_v620 else []

    fx_chart_cr_json = json.dumps(fx_chart_cr)
    fx_chart_dr_json = json.dumps(fx_chart_dr)

    # ── FX Tab ──
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

    # ── Unclassified Tab ──
    unclassified_tab_html = ''
    if is_v630:
        uncl_txns = data.get('unclassified_transactions', [])
        uncl_cr_total = consol.get('total_unclassified_cr', 0) or 0
        uncl_dr_total = consol.get('total_unclassified_dr', 0) or 0
        uncl_cr_count_total = sum((m.get('unclassified_cr_count', 0) or 0) for m in monthly)
        uncl_dr_count_total = sum((m.get('unclassified_dr_count', 0) or 0) for m in monthly)
        cls_config = data.get('classification_config', {})
        uncl_threshold = cls_config.get('unclassified_listing_threshold', 10000)

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

    # ── Parsing QC Tab ──
    parsing_tab_html = ''
    if has_parsing:
        success_rate = parsing.get('overall_success_rate', 0)
        success_rate_pct = success_rate * 100 if success_rate <= 1 else success_rate
        rate_color = 'green' if success_rate_pct >= 95 else 'amber' if success_rate_pct >= 80 else 'red'
        if 'total_extraction_gaps' in consol:
            p_total_gaps = int(consol.get('total_extraction_gaps') or 0)
        else:
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
                <div class="summary-card"><div class="val" style="color:var(--{rate_color})">{success_rate_pct:.1f}%</div><div class="lbl">Success Rate</div></div>
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

        p_extraction_gaps = (parsing.get('extraction_gaps', []) or []) if is_incomplete else []
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

        cls_config = data.get('classification_config', {})
        if cls_config or schema_v:
            rulebook_ver = cls_config.get('rulebook_version', 'N/A')
            exec_mode = cls_config.get('execution_mode', 'N/A')
            large_txn_threshold = safe_float(
                cls_config.get('large_transaction_threshold')
                or cls_config.get('large_credit_threshold')
                or consol.get('high_value_threshold')
                or consol.get('large_transaction_threshold')
                or consol.get('large_credit_threshold')
                or _fallback_summary.get('high_value_threshold')
                or 100000
            )
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
                        <div class="config-item"><span class="config-label">Large Transaction Threshold</span><span class="config-val">RM {large_txn_threshold:,.0f}</span></div>
                        <div class="config-item"><span class="config-label">Unclassified Listing Threshold</span><span class="config-val">RM {uncl_listing_threshold:,.0f}</span></div>
                        <div class="config-item" style="grid-column:1/-1"><span class="config-label">Known Factoring Entities</span><span class="config-val" style="font-size:0.8rem">{factoring_str}</span></div>
                    </div>
                </div>
            </div>'''

        # Formula Validation Checks
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

        salary = safe_float(consol.get('total_salary_paid', 0))
        epf = safe_float(consol.get('total_statutory_epf', 0))
        socso = safe_float(consol.get('total_statutory_socso', 0))
        stat_comp_for_validation = consol.get('statutory_compliance', {})

        v3_avg_ratio, v3_month_count = _average_statutory_ratio_pct(
            stat_comp_for_validation,
            'epf_per_month_ratios',
            'epf_amount',
        )
        v4_avg_ratio, v4_month_count = _average_statutory_ratio_pct(
            stat_comp_for_validation,
            'socso_per_month_ratios',
            'socso_amount',
        )

        v3_ratio = v3_avg_ratio if v3_avg_ratio is not None else ((epf / salary * 100) if salary > 0 else 0)
        v3_has_data = v3_avg_ratio is not None or salary > 0
        v3_status = 'PASS' if 8 <= v3_ratio <= 16 else ('WARN' if v3_has_data else 'N/A')
        v3_remark = (
            f'Avg: {v3_ratio:.1f}% across {v3_month_count} month{"s" if v3_month_count != 1 else ""}'
            if v3_avg_ratio is not None
            else (f'{v3_ratio:.1f}%' if salary > 0 else 'No salary detected')
        )

        v4_ratio = v4_avg_ratio if v4_avg_ratio is not None else ((socso / salary * 100) if salary > 0 else 0)
        v4_has_data = v4_avg_ratio is not None or salary > 0
        v4_status = 'PASS' if 1 <= v4_ratio <= 5 else ('WARN' if v4_has_data else 'N/A')
        v4_remark = (
            f'Avg: {v4_ratio:.1f}% across {v4_month_count} month{"s" if v4_month_count != 1 else ""}'
            if v4_avg_ratio is not None
            else (f'{v4_ratio:.1f}%' if salary > 0 else 'No salary detected')
        )

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
            ('V3', 'EPF/Salary ratio 8-16%', 'WARNING', v3_status, v3_remark),
            ('V4', 'SOCSO/Salary ratio 1-5%', 'WARNING', v4_status, v4_remark),
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

    # ── Fraud Detector Tab ──
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
        if isinstance(pdf_integrity, dict):
            pdf_files = pdf_integrity.get('files', [])
            if not pdf_files and not isinstance(pdf_integrity.get(next(iter(pdf_integrity), ''), {}), list):
                pdf_files_dict = {k: v for k, v in pdf_integrity.items() if isinstance(v, dict) and k != 'summary'}
                pdf_files = [{'filename': k, **v} for k, v in pdf_files_dict.items()]
            if not pdf_files:
                pdf_files = pdf_integrity.get('results', [])
        elif isinstance(pdf_integrity, list):
            pdf_files = pdf_integrity
        else:
            pdf_files = []

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

    # ── Build HTML ──
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
        .header-grid {{ display:flex; flex-direction:column; align-items:stretch; gap:1.75rem; }}
        .company-info h1 {{ font-size:1.6rem; font-weight:700; margin-bottom:0.25rem; }}
        .company-info .period {{ color:var(--text-soft); font-size:0.88rem; }}
        .schema-badge {{ display:inline-block; padding:0.2rem 0.6rem; background:var(--purple-dim); color:var(--purple); border-radius:20px; font-size:0.72rem; font-weight:600; margin-left:0.75rem; vertical-align:middle; }}
        .header-kpi {{ display:flex; gap:1.75rem; flex-wrap:wrap; width:100%; justify-content:flex-start; }}
        .kpi {{ text-align:center; padding:0 1rem; border-left:2px solid var(--border); }}
        .kpi:first-child {{ border-left:none; }}
        .kpi .val {{ font-size:1.35rem; font-weight:700; font-family:'JetBrains Mono',monospace; }}
        .kpi .lbl {{ font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; }}
        .kpi .val.credit {{ color:var(--green); }}
        .kpi .val.debit {{ color:var(--red); }}

        /* Theme toggle */
        .theme-toggle {{ position:absolute; top:1rem; right:1rem; padding:0.4rem 0.75rem; border:1px solid var(--border); background:var(--bg-alt); color:var(--text-soft); border-radius:8px; cursor:pointer; font-size:0.8rem; }}
        .theme-toggle:hover {{ border-color:var(--border-accent); }}
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
        .rpc-note {{ font-size:0.82rem; color:var(--text-soft); margin-bottom:0.6rem; }}
        .rpc-badge {{ display:inline-block; padding:1px 7px; border-radius:5px; font-size:0.72rem; font-weight:600; }}
        .rpc-badge.rpc-medium {{ background:var(--amber-dim); color:var(--amber); }}
        .rpc-badge.rpc-low {{ background:rgba(148,163,184,0.18); color:var(--text-soft); }}
        .rp-badge {{ display:inline-block; padding:0.1rem 0.35rem; background:var(--amber-dim); color:var(--amber); border-radius:4px; font-size:0.65rem; font-weight:700; margin-left:0.35rem; vertical-align:middle; }}
        .op-badge {{ display:inline-block; padding:0.1rem 0.35rem; background:var(--blue-dim); color:var(--blue); border-radius:4px; font-size:0.65rem; font-weight:700; margin-left:0.35rem; vertical-align:middle; }}

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
        .top-parties-grid {{ display:flex !important; flex-direction:row !important; flex-wrap:nowrap !important; gap:1.5rem; align-items:flex-start; width:100%; overflow-x:auto; }}
        .top-parties-grid > .section {{ flex:1 1 0 !important; min-width:0 !important; width:calc(50% - 0.75rem) !important; margin-bottom:0; }}
        .top-parties-grid .table-wrap {{ overflow-x:auto; }}
        .top-parties-grid table {{ min-width:420px; width:100%; }}
        @media (max-width:900px) {{ .two-col {{ grid-template-columns:1fr; }} }}
        @media (max-width:760px) {{ .top-parties-grid > .section {{ min-width:420px !important; }} }}

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
            .nav, .theme-toggle {{ display:none; }}
            .tab {{ display:block !important; page-break-inside:avoid; }}
            body {{ font-size:11px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <button class="theme-toggle" onclick="toggleTheme()">Dark</button>
            <div class="header-grid">
                <div class="company-info">
                    <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:var(--text-muted);margin-bottom:0.35rem">Kredit Lab &mdash; Statement Intelligence</div>
                    <h1>{company} <span class="schema-badge">Kredit Lab v{r.get('schema_version', '6')}</span></h1>
                    <div class="period">{period_start_display} to {period_end_display} &middot; {total_months} months &middot; {sum(a.get('transaction_count',0) for a in accounts):,} transactions</div>
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
            <button class="nav-btn" onclick="showTab('large')">Large Transactions</button>
            <button class="nav-btn" onclick="showTab('round')">Round Figure</button>
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
            {'<div class="section"><div class="section-head"><h2>Possible Related Parties — Analyst to Confirm</h2></div><div class="section-body">' + rp_candidates_html + '</div></div>' if rp_candidates_html else ''}

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
    # Gap panels
    gap_panels_html = ''
    extraction_gaps = (parsing.get('extraction_gaps', []) if parsing else []) if is_incomplete else []
    if extraction_gaps and has_recon:
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
            <div class="top-parties-grid" style="display:flex !important;flex-direction:row !important;flex-wrap:nowrap !important;gap:1.5rem;align-items:flex-start;width:100%;overflow-x:auto">
                <div class="section" style="flex:1 1 0;min-width:0;width:calc(50% - 0.75rem);margin-bottom:0">
                    <div class="section-head"><h2 style="color:var(--green)">Top 10 Payers (Income)</h2></div>
                    <div class="section-body" style="padding:0">
                        <div class="table-wrap"><table>
                            <thead><tr><th>#</th><th>Party</th><th class="r">Amount (RM)</th><th class="r">Txns</th></tr></thead>
                            <tbody>{payer_rows or '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No data</td></tr>'}</tbody>
                        </table></div>
                        {payers_suppressed_html}
                    </div>
                </div>
                <div class="section" style="flex:1 1 0;min-width:0;width:calc(50% - 0.75rem);margin-bottom:0">
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
                        <thead><tr><th>Party</th><th class="r">Credits (RM)</th><th class="r">Debits (RM)</th><th>Party Type</th><th class="r">Txns</th></tr></thead>
                        <tbody>{rp_party_rows or '<tr><td colspan="5" class="note">No own or related party transactions</td></tr>'}</tbody>
                    </table></div>
                    {rp_expander_script}
                </div>
            </div>
        </div>

        <!-- LOANS TAB -->
        <div id="tab-loans" class="tab">
            <div class="summary-grid">
                <div class="summary-card"><div class="val credit">{loan_disb_total:,.0f}</div><div class="lbl">Total Disbursements</div></div>
                <div class="summary-card"><div class="val debit">{loan_repay_total:,.0f}</div><div class="lbl">Total Repayments</div></div>
                <div class="summary-card"><div class="val">{len(loan_disbursements)}</div><div class="lbl">Disbursement Txns</div></div>
                <div class="summary-card"><div class="val">{len(loan_repayments)}</div><div class="lbl">Repayment Txns</div></div>
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
        </div>

        <!-- ROUND FIGURE TAB -->
        <div id="tab-round" class="tab">
            <div class="section">
                <div class="section-head"><h2>Round Figure Transactions &mdash; Detail</h2><span class="badge badge-current">{len(round_figure_credits)} transactions</span></div>
                <div class="section-body" style="padding:0">
                    <div class="note" style="padding:0.5rem 1.25rem">Credit or debit transactions with round-number amounts (multiple of RM 10,000), matching the round-number flag list.</div>
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
            <p>Kredit Lab &mdash; Statement Intelligence Report | Generated {r.get('generated_at','')} | {period_start_display} &ndash; {period_end_display}</p>
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
            renderCharts();
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
            renderCharts();
        }});
    </script>
</body>
</html>'''
    return html


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
        'classification_config': {
            'large_transaction_threshold': high_value_threshold,
            'large_credit_threshold': high_value_threshold,
        },
        'counterparty_ledger': cp_ledger,
        'pdf_integrity': pdf_integrity,
    }


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


def build_round_transactions(transactions: List[dict], round_thresholds: List[float] | None = None) -> List[dict]:
    """Build the same signed round-number transaction rows shown in Railway."""
    round_rows = []
    for tx in transactions or []:
        if not isinstance(tx, dict):
            continue

        credit = safe_float(tx.get("credit", 0))
        debit = safe_float(tx.get("debit", 0))
        is_round_credit = credit > 0 and is_round_number(credit, round_thresholds)
        is_round_debit = debit > 0 and is_round_number(debit, round_thresholds)
        if not (is_round_credit or is_round_debit):
            continue

        amount = credit if credit > 0 else -debit
        round_rows.append(
            {
                "date": tx.get("date", ""),
                "description": tx.get("description", ""),
                "amount": round(float(amount), 2),
                "balance": tx.get("balance", 0),
            }
        )

    return round_rows


def prepare_top_parties_for_report(top_parties: dict, limit: int = 10, company_name: str = "") -> dict:
    """Prepare the exact top-party view rendered by HTML and Excel."""
    if not isinstance(top_parties, dict):
        top_parties = {}
    raw_payers = top_parties.get("top_payers") or top_parties.get("top_creditors") or []
    raw_payees = top_parties.get("top_payees") or top_parties.get("top_debtors") or []

    payers_all = [_normalize_party_for_report(p, True) for p in raw_payers]
    payees_all = [_normalize_party_for_report(p, False) for p in raw_payees]
    def _top_party_is_suppressed(party: dict) -> bool:
        name = party.get("party_name", "")
        return (
            _is_report_unknown_counterparty(name)
            or _is_report_special_counterparty_bucket(name)
            or _is_ghost_party_bucket(name)
            or _is_bill_or_charge_top_party(name)
        )

    payers_suppressed = [p for p in payers_all if _top_party_is_suppressed(p)]
    payees_suppressed = [p for p in payees_all if _top_party_is_suppressed(p)]
    payers = [p for p in payers_all if not _top_party_is_suppressed(p)][:limit]
    payees = [p for p in payees_all if not _top_party_is_suppressed(p)][:limit]

    for idx, party in enumerate(payers, 1):
        party["rank"] = idx
    for idx, party in enumerate(payees, 1):
        party["rank"] = idx

    # Mark OP (Own Party) relationships - only if company_name is provided
    if company_name:
        company_upper = company_name.upper()
        # Clean the company name for better matching
        company_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', company_upper).strip()
        company_core = re.sub(r'\s+', ' ', company_clean).strip()
        
        for party in payers:
            party_name = party.get('party_name', '').upper()
            party_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', party_name).strip()
            party_core = re.sub(r'\s+', ' ', party_clean).strip()
            
            # Check multiple matching strategies
            is_own = False
            if company_upper and party_name:
                # 1. Direct match (full string)
                if party_name == company_upper:
                    is_own = True
                # 2. Company name contains party name OR party name contains company name
                elif party_name in company_upper or company_upper in party_name:
                    is_own = True
                # 3. Core name match (without SDN BHD etc.)
                elif company_core and party_core and (party_core in company_core or company_core in party_core):
                    is_own = True
                # 4. First few words match (e.g., "MUHAFIZ SECURITY" matches "MUHAFIZ SECURITY SDN BHD")
                elif company_core and party_core:
                    company_words = company_core.split()
                    party_words = party_core.split()
                    if len(company_words) >= 2 and len(party_words) >= 2:
                        if company_words[0] == party_words[0] and company_words[1] == party_words[1]:
                            is_own = True
            
            if is_own:
                party['is_own_party'] = True
                
        for party in payees:
            party_name = party.get('party_name', '').upper()
            party_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', party_name).strip()
            party_core = re.sub(r'\s+', ' ', party_clean).strip()
            
            # Check multiple matching strategies
            is_own = False
            if company_upper and party_name:
                # 1. Direct match (full string)
                if party_name == company_upper:
                    is_own = True
                # 2. Company name contains party name OR party name contains company name
                elif party_name in company_upper or company_upper in party_name:
                    is_own = True
                # 3. Core name match (without SDN BHD etc.)
                elif company_core and party_core and (party_core in company_core or company_core in party_core):
                    is_own = True
                # 4. First few words match (e.g., "MUHAFIZ SECURITY" matches "MUHAFIZ SECURITY SDN BHD")
                elif company_core and party_core:
                    company_words = company_core.split()
                    party_words = party_core.split()
                    if len(company_words) >= 2 and len(party_words) >= 2:
                        if company_words[0] == party_words[0] and company_words[1] == party_words[1]:
                            is_own = True
            
            if is_own:
                party['is_own_party'] = True

    return {
        "payers": payers,
        "payees": payees,
        "payers_suppressed": payers_suppressed,
        "payees_suppressed": payees_suppressed,
    }


def build_own_related_party_groups_for_report(
    own_related,
    related_parties=None,
    company_name: str = "",
    counterparty_rows: List[dict] | None = None,
    manual_company_identity_override: bool = False,
    company_account_no: str = "",
) -> List[dict]:
    """Group C01-C04 rows the same way the HTML Own/RP report presents them."""
    if isinstance(own_related, list):
        txns = own_related
    elif isinstance(own_related, dict):
        txns = own_related.get("transactions", []) or []
    else:
        txns = []

    groups = {}
    related_entries = [
        (name, relationship)
        for name, relationship in _report_related_party_entries(related_parties)
        if not _report_name_matches_own_party(name, company_name)
    ]
    has_confirmed_related_parties = bool(related_entries)
    own_party_name = _own_party_group_name_for_report(txns, company_name)
    manual_own_party_active = bool(
        manual_company_identity_override
        and str(company_name or "").strip()
        and str(company_account_no or "").strip()
    )

    def _normalise_account(value) -> str:
        return re.sub(r"\D+", "", str(value or ""))

    manual_account_key = _normalise_account(company_account_no)

    def _txn_matches_manual_own_account(txn: dict) -> bool:
        if not manual_own_party_active or not isinstance(txn, dict):
            return False
        account_values = (
            txn.get("account_no"),
            txn.get("account_number"),
            txn.get("company_account_no"),
        )
        account_keys = [_normalise_account(value) for value in account_values if str(value or "").strip()]
        if manual_account_key and account_keys:
            return manual_account_key in account_keys
        return not account_keys

    def _txn_matches_own_party(txn: dict, raw_party_name: str) -> bool:
        if _txn_matches_manual_own_account(txn):
            return True
        for value in (
            raw_party_name,
            txn.get("counterparty_name"),
            txn.get("counterparty"),
            txn.get("counterparty_name_clean"),
            txn.get("counterparty_name_raw"),
            txn.get("raw_counterparty"),
        ):
            if value and _report_name_matches_own_party(value, company_name):
                return True
        return False

    def _empty_group(party_name: str, badge_type: str, party_type: str) -> dict:
        return {
            "party_name": party_name,
            "party_type": party_type,
            "badge_type": badge_type,
            "transactions": [],
            "credits": 0.0,
            "debits": 0.0,
            "credit_count": 0,
            "debit_count": 0,
            "transaction_count": 0,
        }

    def _group_key(party_name: str, badge_type: str):
        return (party_name.casefold(), badge_type, party_name)

    for txn in txns:
        if not isinstance(txn, dict):
            continue
        raw_party_name = str(txn.get("party_name") or "Unknown Party").strip() or "Unknown Party"
        party_type = str(txn.get("party_type") or "").strip()
        party_type_upper = party_type.upper()

        if party_type_upper.startswith("OWN") or _txn_matches_own_party(txn, raw_party_name):
            badge_type = "OP"
            party_name = own_party_name
        else:
            badge_type = "RP"
            matched_name = _matched_report_related_party_name(
                raw_party_name,
                txn.get("description"),
                related_parties,
            )
            if has_confirmed_related_parties and not matched_name:
                continue
            party_name = matched_name or raw_party_name

        key = _group_key(party_name, badge_type)
        group = groups.setdefault(
            key,
            _empty_group(
                party_name,
                badge_type,
                party_type or ("Own Party" if badge_type == "OP" else "Related Party"),
            ),
        )

        amount = _report_transaction_amount(txn)
        txn_type = _report_transaction_side(txn)
        if txn_type == "CREDIT":
            group["credits"] += amount
            group["credit_count"] += 1
        elif txn_type == "DEBIT":
            group["debits"] += amount
            group["debit_count"] += 1
        group["transaction_count"] += 1

        txn_copy = dict(txn)
        txn_copy["party_name"] = party_name
        txn_copy["party_type"] = group["party_type"]
        txn_copy["type"] = txn_type
        txn_copy["amount"] = amount
        group["transactions"].append(txn_copy)

    if own_party_name:
        groups.setdefault(
            _group_key(own_party_name, "OP"),
            _empty_group(own_party_name, "OP", "Own Party"),
        )

    for related_name, _relationship in related_entries:
        groups.setdefault(
            _group_key(related_name, "RP"),
            _empty_group(related_name, "RP", "Related Party"),
        )

    if counterparty_rows:
        def _counterparty_row_has_manual_own_account(cp: dict) -> bool:
            if not manual_own_party_active or not isinstance(cp, dict):
                return False
            row_account_values = (
                cp.get("account_no"),
                cp.get("account_number"),
                cp.get("company_account_no"),
            )
            row_account_keys = [
                _normalise_account(value)
                for value in row_account_values
                if str(value or "").strip()
            ]
            if manual_account_key and row_account_keys and manual_account_key in row_account_keys:
                return True

            txns_for_row = [txn for txn in cp.get("transactions", []) or [] if isinstance(txn, dict)]
            txn_account_keys = [
                _normalise_account(value)
                for txn in txns_for_row
                for value in (txn.get("account_no"), txn.get("account_number"), txn.get("company_account_no"))
                if str(value or "").strip()
            ]
            if manual_account_key and txn_account_keys:
                return manual_account_key in txn_account_keys
            return not row_account_keys and not txn_account_keys

        def _replace_group_from_counterparty_rows(group: dict) -> None:
            party_name = str(group.get("party_name") or "").strip()
            if not party_name:
                return
            badge_type = group.get("badge_type")
            if manual_own_party_active and badge_type == "OP":
                matches = [
                    cp for cp in counterparty_rows
                    if isinstance(cp, dict) and _counterparty_row_has_manual_own_account(cp)
                ]
            else:
                matches = [
                    cp for cp in counterparty_rows
                    if (
                        isinstance(cp, dict)
                        and _counterparty_row_matches_report_party_name(cp, party_name)
                        and not (
                            manual_own_party_active
                            and badge_type == "RP"
                            and _counterparty_row_has_manual_own_account(cp)
                        )
                    )
                ]
            if not matches:
                return

            party_type = "OWN" if badge_type == "OP" else "RELATED"
            ledger_display_name = (
                str(matches[0].get("counterparty_name") or matches[0].get("counterparty") or party_name).strip()
                or party_name
            )
            display_name = party_name if manual_own_party_active and badge_type == "OP" else (
                ledger_display_name if badge_type == "OP" else party_name
            )
            transactions = []
            credits = debits = 0.0
            credit_count = debit_count = 0
            ledger_credits = ledger_debits = 0.0
            ledger_credit_count = ledger_debit_count = ledger_transaction_count = 0
            has_credit_total = has_debit_total = False
            has_credit_count = has_debit_count = has_transaction_count = False

            for cp in matches:
                if "total_credits" in cp or "total_credit" in cp:
                    has_credit_total = True
                    ledger_credits += safe_float(cp.get("total_credits", cp.get("total_credit", 0)))
                if "total_debits" in cp or "total_debit" in cp:
                    has_debit_total = True
                    ledger_debits += safe_float(cp.get("total_debits", cp.get("total_debit", 0)))
                if "credit_count" in cp or "credit_tx_count" in cp:
                    has_credit_count = True
                    ledger_credit_count += int(safe_float(cp.get("credit_count", cp.get("credit_tx_count", 0))))
                if "debit_count" in cp or "debit_tx_count" in cp:
                    has_debit_count = True
                    ledger_debit_count += int(safe_float(cp.get("debit_count", cp.get("debit_tx_count", 0))))
                if "transaction_count" in cp:
                    has_transaction_count = True
                    ledger_transaction_count += int(safe_float(cp.get("transaction_count", 0)))

                for txn in cp.get("transactions", []) or []:
                    if not isinstance(txn, dict):
                        continue
                    txn_type = _report_transaction_side(txn)
                    amount = abs(_report_transaction_amount(txn))
                    if amount <= 0:
                        continue
                    if txn_type == "CREDIT":
                        credits += amount
                        credit_count += 1
                    else:
                        debits += amount
                        debit_count += 1

                    txn_copy = dict(txn)
                    txn_copy["party_name"] = display_name
                    txn_copy["party_type"] = party_type
                    txn_copy["type"] = txn_type
                    txn_copy["amount"] = round(amount, 2)
                    transactions.append(txn_copy)

            if not any(
                [
                    transactions,
                    has_credit_total,
                    has_debit_total,
                    has_credit_count,
                    has_debit_count,
                    has_transaction_count,
                ]
            ):
                return
            group["party_name"] = display_name
            group["party_type"] = party_type
            group["transactions"] = sorted(
                transactions,
                key=lambda tx: (str(tx.get("date") or ""), str(tx.get("description") or "")),
            )
            group["credits"] = round(ledger_credits if has_credit_total else credits, 2)
            group["debits"] = round(ledger_debits if has_debit_total else debits, 2)
            group["credit_count"] = ledger_credit_count if has_credit_count else credit_count
            group["debit_count"] = ledger_debit_count if has_debit_count else debit_count
            group["transaction_count"] = (
                ledger_transaction_count
                if has_transaction_count
                else group["credit_count"] + group["debit_count"]
            )

        for group in groups.values():
            _replace_group_from_counterparty_rows(group)

    def _group_sort_key(group):
        if group["badge_type"] == "OP":
            return (0, group["party_name"].casefold())
        return (1, group["party_name"].casefold())

    for group in groups.values():
        group["credits"] = round(group["credits"], 2)
        group["debits"] = round(group["debits"], 2)
        if not group.get("transaction_count"):
            group["transaction_count"] = int(group.get("credit_count", 0) or 0) + int(group.get("debit_count", 0) or 0)

    return sorted(groups.values(), key=_group_sort_key)


def _top_parties_from_counterparty_rows(counterparty_rows: List[dict], limit: Optional[int] = 10, company_name: str = "") -> dict:
    payers = []
    payees = []
    for cp in counterparty_rows or []:
        if not isinstance(cp, dict):
            continue
        name = (
            cp.get("counterparty_name")
            or cp.get("party_name")
            or cp.get("name")
            or ""
        )
        name = str(name).strip()
        if not name:
            continue
        if _is_report_unknown_counterparty(name) or _is_report_special_counterparty_bucket(name):
            continue
        if _is_ghost_party_bucket(name):
            continue
        if _is_bill_or_charge_top_party(name):
            continue

        transactions = cp.get("transactions") or []
        related_raw = cp.get("is_related_party", cp.get("related_party", False))
        is_related = bool(related_raw) and str(related_raw).strip().lower() not in {"false", "no", "0"}

        credit_amount = safe_float(cp.get("total_credits", cp.get("total_credit", 0)))
        debit_amount = safe_float(cp.get("total_debits", cp.get("total_debit", 0)))
        credit_count = int(safe_float(cp.get("credit_count", cp.get("credit_tx_count", 0))))
        debit_count = int(safe_float(cp.get("debit_count", cp.get("debit_tx_count", 0))))

        if credit_amount > 0:
            monthly = _ledger_monthly_breakdown(transactions, "CREDIT")
            payers.append({
                "party_name": name,
                "total_amount": round(credit_amount, 2),
                "transaction_count": credit_count or sum(m.get("count", 0) for m in monthly),
                "is_related_party": is_related,
                "monthly_breakdown": monthly,
            })
        if debit_amount > 0:
            monthly = _ledger_monthly_breakdown(transactions, "DEBIT")
            payees.append({
                "party_name": name,
                "total_amount": round(debit_amount, 2),
                "transaction_count": debit_count or sum(m.get("count", 0) for m in monthly),
                "is_related_party": is_related,
                "monthly_breakdown": monthly,
            })

    payers.sort(key=lambda party: party.get("total_amount", 0), reverse=True)
    payees.sort(key=lambda party: party.get("total_amount", 0), reverse=True)
    
    # Add OP badge detection
    if company_name:
        company_upper = company_name.upper()
        company_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', company_upper).strip()
        company_core = re.sub(r'\s+', ' ', company_clean).strip()
        
        for party in payers:
            party_name = party.get('party_name', '').upper()
            party_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', party_name).strip()
            party_core = re.sub(r'\s+', ' ', party_clean).strip()
            
            is_own = False
            if company_upper and party_name:
                if party_name == company_upper or party_name in company_upper or company_upper in party_name:
                    is_own = True
                elif company_core and party_core and (party_core in company_core or company_core in party_core):
                    is_own = True
                elif company_core and party_core:
                    company_words = company_core.split()
                    party_words = party_core.split()
                    if len(company_words) >= 2 and len(party_words) >= 2:
                        if company_words[0] == party_words[0] and company_words[1] == party_words[1]:
                            is_own = True
            
            if is_own:
                party['is_own_party'] = True
                party['is_related_party'] = False  # OP overrides RP
        
        for party in payees:
            party_name = party.get('party_name', '').upper()
            party_clean = re.sub(r'\s+(SDN|BHD|PTE|LTD|INC|CORP|COMPANY|CO)\s*\.?\s*$', '', party_name).strip()
            party_core = re.sub(r'\s+', ' ', party_clean).strip()
            
            is_own = False
            if company_upper and party_name:
                if party_name == company_upper or party_name in company_upper or company_upper in party_name:
                    is_own = True
                elif company_core and party_core and (party_core in company_core or company_core in party_core):
                    is_own = True
                elif company_core and party_core:
                    company_words = company_core.split()
                    party_words = party_core.split()
                    if len(company_words) >= 2 and len(party_words) >= 2:
                        if company_words[0] == party_words[0] and company_words[1] == party_words[1]:
                            is_own = True
            
            if is_own:
                party['is_own_party'] = True
                party['is_related_party'] = False  # OP overrides RP

    top_limit = limit if isinstance(limit, int) and limit > 0 else None
    top_payers = payers[:top_limit] if top_limit else payers
    top_payees = payees[:top_limit] if top_limit else payees
    return {
        "top_payers": [{**party, "rank": idx} for idx, party in enumerate(top_payers, 1)],
        "top_payees": [{**party, "rank": idx} for idx, party in enumerate(top_payees, 1)],
    }


def _is_bill_or_charge_top_party(name) -> bool:
    """Suppress bill/charge parser buckets from Top Parties only."""
    normalised = re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper())
    tokens = {token for token in normalised.split() if token}
    return bool(tokens & {"BILL", "BILLS", "CHARGE", "CHARGES"})


def normalize_observations(obs):
    """Coerce observations into {'positive': [...], 'concerns': [...]}."""
    def _listify(value):
        if value is None:
            return []
        if isinstance(value, list):
            source = value
        elif isinstance(value, tuple):
            source = list(value)
        else:
            source = [value]
        return [str(item) for item in source if item not in (None, "")]

    if isinstance(obs, dict):
        return {
            'positive': _listify(obs.get('positive', [])),
            'concerns': _listify(obs.get('concerns', [])),
        }

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


def get_round_transactions_for_report(data: dict) -> List[dict]:
    """Return export-ready round transactions, preferring raw transactions when available."""
    if not isinstance(data, dict):
        return []

    transactions = data.get("transactions") or []
    if transactions:
        return build_round_transactions(transactions)

    existing = (
        data.get("round_transactions")
        or data.get("round_figure_transactions")
        or data.get("round_figure_credits")
        or []
    )
    if isinstance(existing, dict):
        existing = existing.get("round_figure_entries", []) or existing.get("transactions", []) or []

    rows = []
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        amount = safe_float(row.get("amount", row.get("credit", 0)))
        if str(row.get("type", "")).upper() == "DEBIT" and amount > 0:
            amount = -amount
        rows.append(
            {
                "date": row.get("date", ""),
                "description": row.get("description", ""),
                "amount": round(float(amount), 2),
                "balance": row.get("balance", 0),
            }
        )
    return rows


def _average_statutory_ratio_pct(stat_comp: dict, ratio_key: str, amount_key: str):
    """Return (average ratio %, row count) from statutory monthly ratio rows."""
    if not isinstance(stat_comp, dict):
        return None, 0

    rows = stat_comp.get(ratio_key) or []
    ratios = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        ratio_value = row.get("ratio_pct")
        if ratio_value is None:
            amount = safe_float(row.get(amount_key))
            salary = safe_float(row.get("salary_amount"))
            if salary > 0 and amount > 0:
                ratio_value = amount / salary * 100.0
        if ratio_value is None and row.get("ratio") is not None:
            ratio_value = safe_float(row.get("ratio"))
            if 0 < ratio_value <= 1:
                ratio_value *= 100.0

        if ratio_value is not None:
            ratios.append(safe_float(ratio_value))

    if not ratios:
        return None, 0
    return sum(ratios) / len(ratios), len(ratios)


def _sync_transaction_pattern_flags(
    data: dict,
    *,
    round_transactions: List[dict] | None = None,
    large_transactions: List[dict] | None = None,
    large_threshold: float | int | str | None = None,
) -> dict:
    """Keep pattern risk-signal remarks aligned with the rendered detail tabs."""
    if not isinstance(data, dict):
        return data

    flags = data.get("flags")
    indicators = flags.get("indicators", []) if isinstance(flags, dict) else []
    if not isinstance(indicators, list):
        return data

    if round_transactions is None:
        round_transactions = get_round_transactions_for_report(data)
    if large_transactions is None:
        threshold = safe_float(large_threshold)
        if threshold <= 0:
            threshold = safe_float((data.get("consolidated") or {}).get("high_value_threshold")) or 100000.0
        large_transactions = data.get("large_transactions") or []
        if not large_transactions and data.get("transactions"):
            large_transactions = build_large_transactions(data.get("transactions", []), threshold)

    threshold = safe_float(large_threshold)
    if threshold <= 0:
        threshold = safe_float((data.get("consolidated") or {}).get("high_value_threshold")) or 100000.0

    round_count = len(round_transactions or [])
    round_total = round(
        sum(abs(safe_float(row.get("amount", row.get("credit", row.get("debit", 0)))))
            for row in (round_transactions or [])
            if isinstance(row, dict)),
        2,
    )
    large_count = len(large_transactions or [])
    large_total = round(
        sum(abs(safe_float(row.get("amount", row.get("credit", row.get("debit", 0)))))
            for row in (large_transactions or [])
            if isinstance(row, dict)),
        2,
    )
    threshold_label = f"RM{threshold:,.0f}"

    for flag in indicators:
        if not isinstance(flag, dict):
            continue
        try:
            flag_id = int(flag.get("id"))
        except (TypeError, ValueError):
            flag_id = None
        flag_name = str(flag.get("name") or "")

        if flag_id == 3 or flag_name.startswith("Round Figure"):
            flag["name"] = "Round Figure Transactions (AML)"
            flag["detected"] = round_count > 0
            flag["remarks"] = (
                f"{round_count} round-figure transactions totalling RM {round_total:,.2f}."
                if round_count > 0
                else "No round-figure transactions detected."
            )
        elif flag_id == 9 or flag_name.startswith("Large Credit") or flag_name.startswith("Large Transaction"):
            flag["name"] = f"Large Transactions (>={threshold_label})"
            flag["detected"] = large_count > 0
            flag["remarks"] = (
                f"{large_count} large transactions (>={threshold_label}) totalling RM {large_total:,.2f}."
                if large_count > 0
                else f"No transactions at or above {threshold_label}."
            )

    return data


def _sync_data_quality_status(data: dict) -> dict:
    """Keep consolidated data quality and the Data Quality flag aligned."""
    if not isinstance(data, dict):
        return data

    consolidated = data.setdefault("consolidated", {})
    parsing = data.get("parsing_metadata", {})
    if not isinstance(parsing, dict):
        parsing = {}
        data["parsing_metadata"] = parsing

    checks = parsing.get("account_month_checks", [])
    if not isinstance(checks, list):
        checks = []

    monthly = data.get("monthly_analysis", [])
    if not isinstance(monthly, list):
        monthly = []

    source_rows = checks or [
        row for row in monthly
        if isinstance(row, dict)
        and (
            row.get("reconciliation_status") is not None
            or row.get("extraction_gaps") is not None
            or row.get("reconciliation_delta") is not None
        )
    ]

    def _passed(row: dict) -> bool:
        if "passed" in row:
            return bool(row.get("passed"))
        status = str(row.get("reconciliation_status") or "").upper()
        if status:
            return status == "PASS"
        if row.get("reconciliation_delta") is not None:
            return abs(safe_float(row.get("reconciliation_delta"))) <= 1.00
        return True

    def _gaps(row: dict) -> int:
        return max(0, int(safe_float(row.get("extraction_gaps", row.get("extraction_gaps_count", 0)))))

    if source_rows:
        failed_rows = [
            row for row in source_rows
            if isinstance(row, dict) and (not _passed(row) or _gaps(row) > 0)
        ]
        failed_months = {
            str(row.get("month") or "")
            for row in failed_rows
            if isinstance(row, dict) and row.get("month")
        }
        total_gaps = sum(_gaps(row) for row in source_rows if isinstance(row, dict))
        total_missing_dr = sum(safe_float(row.get("missing_debit_amount")) for row in source_rows if isinstance(row, dict))
        total_missing_cr = sum(safe_float(row.get("missing_credit_amount")) for row in source_rows if isinstance(row, dict))

        consolidated["data_completeness"] = "INCOMPLETE" if failed_rows else "COMPLETE"
        consolidated["months_with_gaps"] = len(failed_months) if failed_months else len(failed_rows)
        consolidated["total_extraction_gaps"] = total_gaps if failed_rows else 0
        consolidated["total_missing_debits"] = round(total_missing_dr, 2) if failed_rows else 0.0
        consolidated["total_missing_credits"] = round(total_missing_cr, 2) if failed_rows else 0.0

        gap_notes = []
        for row in failed_rows:
            month = row.get("month") or "?"
            details = []
            if not _passed(row):
                status = str(row.get("reconciliation_status") or "FAIL").upper()
                details.append(f"reconciliation {status}")
            gap_count = _gaps(row)
            if gap_count:
                details.append(f"{gap_count} gap(s)")
            missing_dr = safe_float(row.get("missing_debit_amount"))
            missing_cr = safe_float(row.get("missing_credit_amount"))
            if missing_dr:
                details.append(f"missing DR RM {missing_dr:,.2f}")
            if missing_cr:
                details.append(f"missing CR RM {missing_cr:,.2f}")
            gap_notes.append(f"{month} ({', '.join(details)})" if details else str(month))
        consolidated["data_gaps"] = "; ".join(gap_notes)

        if checks:
            pass_count = sum(1 for chk in checks if isinstance(chk, dict) and _passed(chk))
            parsing["total_balance_checks"] = len(checks)
            parsing["total_balance_checks_passed"] = pass_count
            parsing["overall_success_rate"] = round(pass_count / len(checks), 4) if checks else 1.0
            parsing["total_transactions_extracted"] = sum(
                int(safe_float(chk.get("transactions_extracted", 0)))
                for chk in checks
                if isinstance(chk, dict)
            )

    data_completeness = str(consolidated.get("data_completeness") or "COMPLETE").upper()
    data_gaps = str(consolidated.get("data_gaps") or "").strip()
    incomplete = data_completeness == "INCOMPLETE"

    flags = data.get("flags")
    if isinstance(flags, dict):
        indicators = flags.get("indicators", [])
        if isinstance(indicators, list):
            for flag in indicators:
                if not isinstance(flag, dict):
                    continue
                try:
                    flag_id = int(flag.get("id"))
                except (TypeError, ValueError):
                    flag_id = None
                if flag_id == 13 or flag.get("name") == "Data Quality":
                    flag["detected"] = incomplete
                    flag["remarks"] = (
                        f"Statement data INCOMPLETE: {data_gaps}"
                        if incomplete and data_gaps
                        else (
                            "Statement data INCOMPLETE."
                            if incomplete
                            else "Statement data complete across the period."
                        )
                    )
                    break

    observations = data.get("observations")
    if isinstance(observations, dict):
        positives = observations.get("positive", [])
        concerns = observations.get("concerns", [])
        if isinstance(positives, list) and isinstance(concerns, list):
            concerns[:] = [
                item for item in concerns
                if "reconciliation gaps" not in str(item).lower()
                and "statement data incomplete" not in str(item).lower()
            ]
            positives[:] = [
                item for item in positives
                if "all months reconciled" not in str(item).lower()
            ]
            if incomplete:
                concerns.insert(
                    0,
                    f"Statement data incomplete: {data_gaps}" if data_gaps else "Statement data incomplete.",
                )
            else:
                positives.insert(0, "All months reconciled to bank statements within tolerance.")

    return data


def apply_standard_monthly_summary_to_report(data: dict, monthly_summary: List[dict]) -> dict:
    """Align report balances with the Railway standardized monthly summary."""
    if not isinstance(data, dict) or not monthly_summary:
        return data
    monthly_summary = standardize_monthly_summary_balance_chain(monthly_summary)

    def has_value(value) -> bool:
        return value is not None and str(value).strip() != ""

    def num(row: dict, key: str):
        if not has_value(row.get(key)):
            return None
        return round(safe_float(row.get(key)), 2)

    summary_by_key = {}
    summary_by_month = {}
    month_counts = {}
    for row in monthly_summary or []:
        if not isinstance(row, dict):
            continue
        month = str(row.get("month", "") or "")
        account = str(row.get("account_no", row.get("account_number", "")) or "")
        if not month:
            continue
        if account:
            summary_by_key[(month, account)] = row
        month_counts[month] = month_counts.get(month, 0) + 1
        summary_by_month.setdefault(month, row)

    def find_summary(row: dict):
        month = str(row.get("month", "") or "")
        account = str(row.get("account_number", row.get("account_no", "")) or "")
        if (month, account) in summary_by_key:
            return summary_by_key[(month, account)]
        if month_counts.get(month) == 1:
            return summary_by_month.get(month)
        return None

    monthly_rows = data.get("monthly_analysis", [])
    if isinstance(monthly_rows, list):
        for row in monthly_rows:
            if not isinstance(row, dict):
                continue
            ref = find_summary(row)
            if not ref:
                continue
            opening = num(ref, "opening_balance")
            closing = num(ref, "ending_balance")
            total_credit = num(ref, "total_credit")
            total_debit = num(ref, "total_debit")
            if opening is not None:
                row["opening_balance"] = opening
            if closing is not None:
                row["closing_balance"] = closing
            if total_credit is not None:
                row["gross_credits"] = total_credit
            if total_debit is not None:
                row["gross_debits"] = total_debit
            if has_value(ref.get("transaction_count")):
                row["transaction_count"] = int(safe_float(ref.get("transaction_count")))

    account_refs = {}
    for row in monthly_summary or []:
        if not isinstance(row, dict):
            continue
        account = str(row.get("account_no", row.get("account_number", "")) or "")
        if account:
            account_refs.setdefault(account, []).append(row)

    accounts = data.get("accounts", [])
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_number = str(account.get("account_number", account.get("account_no", "")) or "")
            refs = sorted(account_refs.get(account_number, []), key=lambda item: str(item.get("month", "")))
            if not refs and len(account_refs) == 1:
                refs = sorted(next(iter(account_refs.values())), key=lambda item: str(item.get("month", "")))
            if not refs:
                continue
            opening = num(refs[0], "opening_balance")
            closing = num(refs[-1], "ending_balance")
            if opening is not None:
                account["opening_balance"] = opening
            if closing is not None:
                account["closing_balance"] = closing
            account["total_credits"] = round(sum(safe_float(ref.get("total_credit", 0)) for ref in refs), 2)
            account["total_debits"] = round(sum(safe_float(ref.get("total_debit", 0)) for ref in refs), 2)
            account["transaction_count"] = int(sum(safe_float(ref.get("transaction_count", 0)) for ref in refs))

    parsing = data.get("parsing_metadata", {})
    checks = parsing.get("account_month_checks", []) if isinstance(parsing, dict) else []
    if isinstance(checks, list):
        for chk in checks:
            if not isinstance(chk, dict):
                continue
            ref = find_summary(chk)
            if not ref:
                continue
            opening = num(ref, "opening_balance")
            closing = num(ref, "ending_balance")
            total_credit = num(ref, "total_credit")
            total_debit = num(ref, "total_debit")
            if opening is not None:
                chk["opening_balance"] = opening
            if closing is not None:
                chk["closing_balance"] = closing
            if total_credit is not None:
                chk["gross_credits"] = total_credit
            if total_debit is not None:
                chk["gross_debits"] = total_debit
            if has_value(ref.get("transaction_count")):
                chk["transactions_extracted"] = int(safe_float(ref.get("transaction_count")))
            if all(value is not None for value in (opening, closing, total_credit, total_debit)):
                expected = round(opening + total_credit - total_debit, 2)
                delta = round(closing - expected, 2)
                chk["expected_closing"] = expected
                chk["reconciliation_delta"] = delta
                chk["passed"] = abs(delta) <= 0.01
                chk.setdefault("extraction_gaps", 0)

        if checks:
            passed_count = sum(1 for chk in checks if chk.get("passed", False))
            parsing["total_balance_checks"] = len(checks)
            parsing["total_balance_checks_passed"] = passed_count
            parsing["overall_success_rate"] = round(passed_count / len(checks), 4)
            parsing["total_transactions_extracted"] = sum(
                int(safe_float(chk.get("transactions_extracted", 0))) for chk in checks
            )
            data["parsing_metadata"] = parsing

            consolidated = data.setdefault("consolidated", {})
            failed_checks = [chk for chk in checks if not chk.get("passed", False)]
            total_extraction_gaps = sum(int(chk.get("extraction_gaps", 0) or 0) for chk in checks)
            consolidated["data_completeness"] = "INCOMPLETE" if failed_checks else "COMPLETE"
            consolidated["months_with_gaps"] = len(
                {str(chk.get("month", "") or "") for chk in failed_checks if chk.get("month")}
            )
            consolidated["total_extraction_gaps"] = total_extraction_gaps
            if not failed_checks:
                consolidated["total_missing_debits"] = 0.0
                consolidated["total_missing_credits"] = 0.0

    return _sync_data_quality_status(data)


def build_formula_validation_checks_for_report(consolidated: dict, monthly_analysis: List[dict]) -> List[dict]:
    """Build the V1-V6 validation rows displayed in the HTML Parsing QC tab."""
    consolidated = consolidated or {}
    monthly_analysis = monthly_analysis or []

    gross_cr = safe_float(consolidated.get("gross_credits", 0))
    own_cr = safe_float(consolidated.get("total_own_party_cr", 0))
    rp_cr = safe_float(consolidated.get("total_related_party_cr", 0))
    rev_cr = safe_float(consolidated.get("total_reversal_cr", 0))
    loan_disb_cr = safe_float(consolidated.get("total_loan_disbursement_cr", 0))
    fd_int_cr = safe_float(consolidated.get("total_fd_interest_cr", 0))
    inward_ret_cr = safe_float(consolidated.get("total_inward_return_cr", 0))
    net_cr = safe_float(consolidated.get("net_credits", 0))
    expected_net_cr = gross_cr - own_cr - rp_cr - rev_cr - loan_disb_cr - fd_int_cr - inward_ret_cr
    v1_delta = abs(net_cr - expected_net_cr)

    gross_dr = safe_float(consolidated.get("gross_debits", 0))
    own_dr = safe_float(consolidated.get("total_own_party_dr", 0))
    net_dr = safe_float(consolidated.get("net_debits", 0))
    expected_net_dr = gross_dr - own_dr
    v2_delta = abs(net_dr - expected_net_dr)

    salary = safe_float(consolidated.get("total_salary_paid", 0))
    epf = safe_float(consolidated.get("total_statutory_epf", 0))
    socso = safe_float(consolidated.get("total_statutory_socso", 0))
    stat_comp = consolidated.get("statutory_compliance", {}) or {}

    v3_avg_ratio, v3_month_count = _average_statutory_ratio_pct(
        stat_comp,
        "epf_per_month_ratios",
        "epf_amount",
    )
    v4_avg_ratio, v4_month_count = _average_statutory_ratio_pct(
        stat_comp,
        "socso_per_month_ratios",
        "socso_amount",
    )

    v3_ratio = v3_avg_ratio if v3_avg_ratio is not None else ((epf / salary * 100) if salary > 0 else 0)
    v3_has_data = v3_avg_ratio is not None or salary > 0
    v3_status = "PASS" if 8 <= v3_ratio <= 16 else ("WARN" if v3_has_data else "N/A")
    v3_remark = (
        f"Avg: {v3_ratio:.1f}% across {v3_month_count} month{'s' if v3_month_count != 1 else ''}"
        if v3_avg_ratio is not None
        else (f"{v3_ratio:.1f}%" if salary > 0 else "No salary detected")
    )

    v4_ratio = v4_avg_ratio if v4_avg_ratio is not None else ((socso / salary * 100) if salary > 0 else 0)
    v4_has_data = v4_avg_ratio is not None or salary > 0
    v4_status = "PASS" if 1 <= v4_ratio <= 5 else ("WARN" if v4_has_data else "N/A")
    v4_remark = (
        f"Avg: {v4_ratio:.1f}% across {v4_month_count} month{'s' if v4_month_count != 1 else ''}"
        if v4_avg_ratio is not None
        else (f"{v4_ratio:.1f}%" if salary > 0 else "No salary detected")
    )

    monthly_net_cr_sum = sum(safe_float(row.get("net_credits", 0)) for row in monthly_analysis if isinstance(row, dict))
    v6_delta = abs(net_cr - monthly_net_cr_sum)

    return [
        {"ID": "V1", "Check": "Net Credits = Gross - Exclusions", "Severity": "BLOCKING", "Status": "PASS" if v1_delta < 0.02 else "FAIL", "Remarks": f"Delta: RM {v1_delta:,.2f}"},
        {"ID": "V2", "Check": "Net Debits = Gross - Own Party (C02)", "Severity": "BLOCKING", "Status": "PASS" if v2_delta < 0.02 else "FAIL", "Remarks": f"Delta: RM {v2_delta:,.2f}"},
        {"ID": "V3", "Check": "EPF/Salary ratio 8-16%", "Severity": "WARNING", "Status": v3_status, "Remarks": v3_remark},
        {"ID": "V4", "Check": "SOCSO/Salary ratio 1-5%", "Severity": "WARNING", "Status": v4_status, "Remarks": v4_remark},
        {"ID": "V5", "Check": "C02+C11 dual-tag exclusion", "Severity": "BLOCKING", "Status": "PASS", "Remarks": "Single deduction via C02"},
        {"ID": "V6", "Check": "Monthly net_cr sum = consolidated", "Severity": "BLOCKING", "Status": "PASS" if v6_delta < 0.02 else "FAIL", "Remarks": f"Delta: RM {v6_delta:,.2f}"},
    ]


__all__ = [
    'bind_app_globals',
    'generate_interactive_html',
    'adapt_to_v6',
    'build_large_transactions',
    'build_round_transactions',
    'prepare_top_parties_for_report',
    'build_own_related_party_groups_for_report',
    '_top_parties_from_counterparty_rows',
    'normalize_observations',
    'get_round_transactions_for_report',
    '_average_statutory_ratio_pct',
    '_sync_transaction_pattern_flags',
    '_sync_data_quality_status',
    'apply_standard_monthly_summary_to_report',
    'build_formula_validation_checks_for_report',
]
