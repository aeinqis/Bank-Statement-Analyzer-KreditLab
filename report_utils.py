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

from report_generator import _sync_data_quality_status, generate_interactive_html, normalize_observations

try:
    from core_utils import safe_float
except Exception:  # pragma: no cover - rebound from app.py during normal use
    safe_float = float


def make_json_serializable(obj):
    """Recursively convert common pandas/numpy/date objects to JSON-safe values."""
    if isinstance(obj, dict):
        return {str(key): make_json_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(item) for item in obj]
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, pd.Period):
        return str(obj)
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return make_json_serializable(obj.item())
        except Exception:
            pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _has_top_party_rows(top_parties: dict) -> bool:
    if not isinstance(top_parties, dict):
        return False
    payers = top_parties.get("top_payers") or top_parties.get("top_creditors") or []
    payees = top_parties.get("top_payees") or top_parties.get("top_debtors") or []
    return bool(payers or payees)


REPORT_JSON_MAX_SIZE_BYTES = 50 * 1024 * 1024

DEFAULT_REPORT_SECTIONS = {
    "top_parties": {
        "top_payers": [],
        "top_payees": [],
    },
    "large_credits": [],
    "large_transactions": [],
    "round_transactions": [],
    "round_figure_credits": [],
    "own_related_transactions": {
        "transactions": [],
        "summary": {},
    },
    "loan_transactions": {
        "transactions": [],
        "disbursements": [],
        "repayments": [],
        "summary": {},
    },
    "flags": {
        "indicators": [],
    },
    "observations": {
        "positive": [],
        "concerns": [],
    },
    "parsing_metadata": {},
    "unclassified_transactions": [],
    "classification_config": {},
    "pdf_integrity": {},
    "counterparty_ledger": {
        "counterparties": [],
        "total_counterparties": 0,
        "extraction_stats": {},
    },
}

AI_EDITING_INSTRUCTIONS = {
    "purpose": "Add analyst observations and concerns without changing calculated report data.",
    "editable_fields": [
        "observations.positive",
        "observations.concerns",
    ],
    "protected_fields": [
        "report_info",
        "accounts",
        "monthly_analysis",
        "consolidated",
        "top_parties",
        "large_credits",
        "round_figure_credits",
        "own_related_transactions",
        "loan_transactions",
        "flags",
        "parsing_metadata",
        "unclassified_transactions",
        "classification_config",
        "pdf_integrity",
        "counterparty_ledger",
    ],
    "rules": [
        "Return valid JSON only.",
        "Preserve all existing keys and values unless explicitly authorised.",
        "Do not modify calculated financial amounts.",
        "Do not delete transactions, flags, accounts, months, or report sections.",
        "Add plain text only to observations.positive and observations.concerns.",
        "Do not include HTML, Markdown scripts, JavaScript, or executable content.",
    ],
}


def _first_present(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def build_protected_report_snapshot(data: dict) -> dict:
    snapshot = copy.deepcopy(make_json_serializable(data if isinstance(data, dict) else {}))
    snapshot.pop("observations", None)
    snapshot.pop("ai_editing_instructions", None)

    report_metadata = snapshot.get("report_metadata")
    if isinstance(report_metadata, dict):
        for volatile_key in (
            "exported_at",
            "imported_at",
            "protected_data_sha256",
            "protected_section_sha256",
        ):
            report_metadata.pop(volatile_key, None)
        if not report_metadata:
            snapshot.pop("report_metadata", None)

    return snapshot


def calculate_protected_section_fingerprints(data: dict) -> dict:
    snapshot = build_protected_report_snapshot(data)
    return {
        key: _hash_json_value(value)
        for key, value in snapshot.items()
    }


def _fingerprint_status_for_report(report_data: dict) -> tuple[str, list[str]]:
    metadata = report_data.get("report_metadata", {}) if isinstance(report_data, dict) else {}
    expected = metadata.get("protected_data_sha256") if isinstance(metadata, dict) else None
    if not expected:
        return "No export fingerprint found.", []

    actual = calculate_report_fingerprint(report_data)
    if actual == expected:
        return "Protected report data is unchanged. Only editable content appears to have been modified.", []

    changed_sections = compare_protected_sections(metadata, report_data)
    return (
        "The uploaded JSON contains changes outside the editable observations fields.",
        changed_sections,
    )


try:
    from counterparty_ledger import (
        build_report_counterparty_ledger_rows,
        _top_parties_from_counterparty_rows,
        get_report_counterparty_rows_from_data,
        filter_report_related_parties,
    )
except Exception:  # pragma: no cover - fallback for standalone use
    build_report_counterparty_ledger_rows = None
    _top_parties_from_counterparty_rows = None
    get_report_counterparty_rows_from_data = None
    filter_report_related_parties = None


try:
    from kredit_lab_classify_track2 import account_meta_from_determinations, build_track2_result
except Exception:  # pragma: no cover - fallback for standalone use
    account_meta_from_determinations = None
    build_track2_result = None


try:
    from report_generator import (
        adapt_to_v6,
        build_large_transactions,
        build_round_transactions,
        get_round_transactions_for_report,
        _sync_transaction_pattern_flags,
        apply_standard_monthly_summary_to_report,
    )
except Exception:  # pragma: no cover - fallback for standalone use
    adapt_to_v6 = None
    build_large_transactions = None
    build_round_transactions = None
    get_round_transactions_for_report = None
    _sync_transaction_pattern_flags = None
    apply_standard_monthly_summary_to_report = None


try:
    from counterparty_ledger import build_track2_counterparty_ledger
except Exception:  # pragma: no cover - fallback for standalone use
    build_track2_counterparty_ledger = None


_TRACK2_AVAILABLE = (
    build_track2_result is not None
    and build_track2_counterparty_ledger is not None
    and build_report_counterparty_ledger_rows is not None
    and _top_parties_from_counterparty_rows is not None
)


def bind_app_globals(app_globals: dict) -> None:
    """Expose app.py helpers/constants that these extracted functions already use."""
    for name, value in app_globals.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = value


def _manual_company_identity_override_info() -> dict:
    company_name = str(st.session_state.get("company_name_override") or "").strip()
    account_no = str(st.session_state.get("company_account_no_override") or "").strip()
    is_manual_identity = bool(company_name and account_no)
    return {
        "manual_company_identity_override": is_manual_identity,
        "manual_company_name": company_name if is_manual_identity else "",
        "manual_company_account_no": account_no if is_manual_identity else "",
    }


def _fallback_filter_report_related_parties(related_parties, company_name: str = "") -> List[dict]:
    """Fallback implementation for related-party filtering when the shared helper is unavailable."""
    if not related_parties:
        return []
    if isinstance(related_parties, str):
        return [{"name": related_parties.strip()}] if related_parties.strip() else []

    normalized: List[dict] = []
    for party in related_parties or []:
        if isinstance(party, dict):
            name = (
                party.get("name")
                or party.get("party_name")
                or party.get("counterparty")
                or party.get("related_party")
                or ""
            )
            if not name:
                continue
            item = dict(party)
            item["name"] = str(name).strip()
            normalized.append(item)
        else:
            name = str(party or "").strip()
            if name:
                normalized.append({"name": name})
    return normalized


if filter_report_related_parties is None:
    filter_report_related_parties = _fallback_filter_report_related_parties


def build_shared_report_data(
    transactions: List[dict],
    monthly_summary: List[dict],
    transaction_analysis: dict,
    high_value_threshold: float,
) -> dict:
    """Build the complete report payload used by both HTML and Excel exports."""
    pdf_integrity = st.session_state.get("integrity_analysis_results") or {}
    threshold = float(high_value_threshold) if high_value_threshold else 100000.0
    transaction_analysis = transaction_analysis or {}

    if _TRACK2_AVAILABLE:
        try:
            cp_ledger = build_track2_counterparty_ledger(transactions)
            company_names = list({
                str(t.get("company_name", "") or "").strip()
                for t in transactions
                if isinstance(t, dict) and t.get("company_name")
            })
            override = (st.session_state.get("company_name_override") or "").strip()
            if override and override not in company_names:
                company_names.insert(0, override)
            manual_identity = _manual_company_identity_override_info()

            determinations = st.session_state.get("account_type_determinations") or []
            account_meta = account_meta_from_determinations(determinations)
            related_parties = filter_report_related_parties(
                st.session_state.get("related_parties_override") or []
            )
            factoring_entities = st.session_state.get("factoring_entities_override") or []

            data = build_track2_result(
                transactions=transactions,
                counterparty_ledger=cp_ledger,
                pdf_integrity=pdf_integrity if pdf_integrity else None,
                company_names=company_names or None,
                related_parties=related_parties or None,
                factoring_entities=factoring_entities or None,
                account_meta=account_meta or None,
            )
            data.setdefault("counterparty_ledger", cp_ledger)
            data.setdefault("report_info", {}).update(manual_identity)
            
            # Build top_parties from the same aligned CP ledger rows rendered in reports.
            # Get company name from override or first transaction
            company_name = override or (company_names[0] if company_names else '')
            report_counterparty_rows = build_report_counterparty_ledger_rows(
                cp_ledger,
                related_parties=filter_report_related_parties(
                    data.get("report_info", {}).get("related_parties", []) or []
                ),
                own_related=data.get("own_related_transactions", {}) or {},
                company_name=company_name,
            )
            data["report_counterparty_rows"] = report_counterparty_rows
            data["top_parties"] = _top_parties_from_counterparty_rows(
                report_counterparty_rows,
                limit=None,
                company_name=company_name,
            )
            
            return _finalize_shared_report_data(
                data,
                transactions,
                monthly_summary,
                threshold,
                pdf_integrity,
            )
        
        except Exception as _track2_err:
            import traceback
            print(f"[Track2] ERROR in build_track2_result: {_track2_err}")
            traceback.print_exc()
            try:
                st.error(f"Track 2 engine failed: {_track2_err}")
            except Exception:
                pass

    print("[Track2] Using legacy fallback (loan detection may be incomplete)")
    report_data = build_report_data_from_analysis(
        transactions,
        monthly_summary,
        transaction_analysis,
        threshold,
    )
    report_data["pdf_integrity"] = pdf_integrity
    report_data.setdefault("report_info", {}).update(_manual_company_identity_override_info())
    
    # Build top_parties from the aligned CP ledger for legacy fallback too
    cp_ledger = build_track2_counterparty_ledger(transactions)
    # Get company name from session state or transactions
    company_name = st.session_state.get("company_name_override", "") or (
        transactions[0].get("company_name", "") if transactions else ""
    )
    report_counterparty_rows = build_report_counterparty_ledger_rows(
        cp_ledger,
        related_parties=filter_report_related_parties(
            report_data.get("report_info", {}).get("related_parties", []) or []
        ),
        own_related=report_data.get("own_related_transactions", {}) or {},
        company_name=company_name,
    )
    report_data["report_counterparty_rows"] = report_counterparty_rows
    report_data["top_parties"] = _top_parties_from_counterparty_rows(
        report_counterparty_rows,
        limit=None,
        company_name=company_name,
    )
    report_data["counterparty_ledger"] = cp_ledger
        
    return _finalize_shared_report_data(
        report_data,
        transactions,
        monthly_summary,
        threshold,
        pdf_integrity,
    )


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
        'counterparty_ledger': transaction_analysis.get('counterparty_ledger', {}) or build_track2_counterparty_ledger(transactions),
        'pdf_integrity': pdf_integrity,
    }

    adapted_data = adapt_to_v6(data)
    adapted_data['transactions'] = transactions
    adapted_data.setdefault('report_info', {}).update(_manual_company_identity_override_info())
    
    # Build top_parties from the same aligned CP ledger rows used by reports.
    cp_ledger = transaction_analysis.get('counterparty_ledger', {}) or build_track2_counterparty_ledger(transactions)
    company_name = st.session_state.get('company_name_override', '') or (transactions[0].get('company_name', '') if transactions else '')
    related_parties = filter_report_related_parties(
        adapted_data.get("report_info", {}).get("related_parties", []) or []
    )
    own_related = adapted_data.get("own_related_transactions", {}) or {}
    report_counterparty_rows = build_report_counterparty_ledger_rows(
        cp_ledger,
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )
    adapted_data["report_counterparty_rows"] = report_counterparty_rows
    adapted_data['top_parties'] = _top_parties_from_counterparty_rows(
        report_counterparty_rows,
        limit=None,
        company_name=company_name,
    )
        
    # IMPORTANT: Build large transactions directly from transactions with correct threshold
    adapted_data['large_transactions'] = build_large_transactions(transactions, threshold)
    adapted_data['large_credits'] = transaction_analysis.get('high_value_credits', [])
    round_transactions = build_round_transactions(transactions)
    
    adapted_data['flags'] = transaction_analysis.get('flags', {'indicators': []})
    adapted_data['observations'] = transaction_analysis.get('observations', {'positive': [], 'concerns': []})
    adapted_data['round_transactions'] = round_transactions
    adapted_data['round_figure_credits'] = round_transactions
    adapted_data['loan_transactions'] = transaction_analysis.get(
        'loan_transactions',
        adapted_data.get('loan_transactions', {'transactions': [], 'summary': {}}),
    )
    adapted_data['own_related_transactions'] = transaction_analysis.get(
        'own_related_transactions',
        adapted_data.get('own_related_transactions', {'transactions': [], 'summary': {}}),
    )
    adapted_data['unclassified_transactions'] = transaction_analysis.get('unclassified_transactions', [])
    adapted_data['classification_config'] = dict(transaction_analysis.get('classification_config', {}) or {})
    adapted_data['classification_config']['large_transaction_threshold'] = threshold
    adapted_data['classification_config']['large_credit_threshold'] = threshold
    adapted_data['parsing_metadata'] = transaction_analysis.get(
        'parsing_metadata',
        adapted_data.get('parsing_metadata', {}),
    )
    adapted_data['pdf_integrity'] = pdf_integrity
    
    # IMPORTANT: Add threshold to consolidated for display
    if 'consolidated' in adapted_data:
        adapted_data['consolidated']['high_value_threshold'] = threshold
        adapted_data['consolidated']['large_transaction_threshold'] = threshold
        # Also store as large_credit_threshold for consistency
        adapted_data['consolidated']['large_credit_threshold'] = threshold

    adapted_data = apply_standard_monthly_summary_to_report(adapted_data, monthly_summary)
    adapted_data = _sync_transaction_pattern_flags(
        adapted_data,
        round_transactions=round_transactions,
        large_transactions=adapted_data.get('large_transactions', []),
        large_threshold=threshold,
    )
    
    return adapted_data


def normalize_report_data_for_export(data: dict) -> dict:
    """Normalize uploaded JSON or v6 payloads for HTML/XLSX report exports."""
    if not isinstance(data, dict):
        return {}

    source = dict(data)
    transaction_analysis = source.get("transaction_analysis", {})
    if isinstance(transaction_analysis, dict):
        source.setdefault("counterparty_ledger", transaction_analysis.get("counterparty_ledger", {}))

    # Build CP ledger if missing
    if not source.get("counterparty_ledger") and source.get("transactions"):
        source["counterparty_ledger"] = build_track2_counterparty_ledger(source.get("transactions", []))
    
    # ALWAYS build top_parties from the aligned CP ledger for consistency with the UI.
    cp_ledger = source.get("counterparty_ledger", {})
    company_name = source.get("report_info", {}).get("company_name", "")
    related_parties = filter_report_related_parties(
        source.get("report_info", {}).get("related_parties", []) or []
    )
    own_related = source.get("own_related_transactions", {}) or {}
    report_counterparty_rows = get_report_counterparty_rows_from_data(
        source,
        cp_ledger,
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )
    if report_counterparty_rows:
        source["report_counterparty_rows"] = report_counterparty_rows
    source["top_parties"] = _top_parties_from_counterparty_rows(
        report_counterparty_rows,
        limit=None,
        company_name=company_name,
    )

    if "monthly_analysis" not in source and "transactions" in source:
        normalized = adapt_to_v6(source)
        normalized["transactions"] = source.get("transactions", [])
        if source.get("report_counterparty_rows"):
            normalized["report_counterparty_rows"] = source.get("report_counterparty_rows")
        if source.get("counterparty_ledger_rows"):
            normalized["counterparty_ledger_rows"] = source.get("counterparty_ledger_rows")
    else:
        normalized = dict(source)

    if isinstance(transaction_analysis, dict):
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
    normalized.setdefault("report_info", {})
    # Ensure top_parties remains from the aligned CP ledger.
    cp_ledger = normalized.get("counterparty_ledger", {})
    company_name = normalized.get("report_info", {}).get("company_name", "")
    related_parties = filter_report_related_parties(
        normalized.get("report_info", {}).get("related_parties", []) or []
    )
    own_related = normalized.get("own_related_transactions", {}) or {}
    report_counterparty_rows = get_report_counterparty_rows_from_data(
        normalized,
        cp_ledger,
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )
    if report_counterparty_rows:
        normalized["report_counterparty_rows"] = report_counterparty_rows
    normalized["top_parties"] = _top_parties_from_counterparty_rows(
        report_counterparty_rows,
        limit=None,
        company_name=company_name,
    )

    threshold = safe_float(
        normalized.get("consolidated", {}).get("high_value_threshold")
        or normalized.get("consolidated", {}).get("large_transaction_threshold")
        or normalized.get("consolidated", {}).get("large_credit_threshold")
        or normalized.get("summary", {}).get("high_value_threshold")
        or normalized.get("classification_config", {}).get("large_transaction_threshold")
        or normalized.get("classification_config", {}).get("large_credit_threshold")
    )
    if threshold <= 0:
        threshold = safe_float(normalized.get("summary", {}).get("high_value_threshold")) or 100000.0
    if normalized.get("transactions") and not normalized.get("large_transactions"):
        normalized["large_transactions"] = build_large_transactions(normalized.get("transactions", []), threshold)
    else:
        normalized.setdefault("large_transactions", [])
    normalized.setdefault("classification_config", {})
    normalized["classification_config"]["large_transaction_threshold"] = threshold
    normalized["classification_config"]["large_credit_threshold"] = threshold
    normalized.setdefault("consolidated", {})
    normalized["consolidated"]["high_value_threshold"] = threshold
    normalized["consolidated"]["large_transaction_threshold"] = threshold
    normalized["consolidated"]["large_credit_threshold"] = threshold
    round_transactions = get_round_transactions_for_report(normalized)
    normalized["round_transactions"] = round_transactions
    normalized["round_figure_credits"] = round_transactions
    if source.get("monthly_summary"):
        normalized = apply_standard_monthly_summary_to_report(normalized, source.get("monthly_summary", []))
    normalized = _sync_transaction_pattern_flags(
        normalized,
        round_transactions=round_transactions,
        large_transactions=normalized.get("large_transactions", []),
        large_threshold=threshold,
    )
    return normalized


def prepare_report_for_export(data: dict) -> dict:
    report_data = copy.deepcopy(data) if isinstance(data, dict) else {}
    report_data = make_json_serializable(apply_report_defaults(report_data))

    report_info = report_data.get("report_info", {}) if isinstance(report_data.get("report_info"), dict) else {}
    metadata = report_data.setdefault("report_metadata", {})
    metadata.update({
        "format": "kredit_lab_interactive_report",
        "schema_version": report_info.get("schema_version", "6.3.5"),
        "exported_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "Kredit Lab",
        "editable_sections": [
            "observations.positive",
            "observations.concerns",
        ],
    })
    report_data["ai_editing_instructions"] = copy.deepcopy(AI_EDITING_INSTRUCTIONS)
    metadata["protected_data_sha256"] = calculate_report_fingerprint(report_data)
    metadata["protected_section_sha256"] = calculate_protected_section_fingerprints(report_data)
    return make_json_serializable(report_data)


def prepare_uploaded_report(data: dict) -> dict:
    schema = detect_report_json_schema(data)
    if schema == "legacy_raw_report":
        report_data = convert_legacy_report_to_canonical(data)
    elif schema == "canonical_report":
        report_data = copy.deepcopy(data)
    else:
        raise ValueError(
            "The file is not a recognised Kredit Lab report. Required canonical fields: "
            "report_info, accounts, monthly_analysis, consolidated."
        )

    report_data = make_json_serializable(apply_report_defaults(report_data))
    metadata = report_data.setdefault("report_metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("format", "kredit_lab_interactive_report")
        metadata.setdefault(
            "schema_version",
            report_data.get("report_info", {}).get("schema_version", "6.3.5")
            if isinstance(report_data.get("report_info"), dict)
            else "6.3.5",
        )
        metadata.setdefault("generator", "Kredit Lab")
        metadata["imported_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return make_json_serializable(report_data)


def convert_legacy_report_to_canonical(data: dict) -> dict:
    source = copy.deepcopy(data) if isinstance(data, dict) else {}
    summary = source.get("summary", {}) if isinstance(source.get("summary"), dict) else {}
    transactions = source.get("transactions", []) if isinstance(source.get("transactions"), list) else []
    monthly_summary = source.get("monthly_summary", []) if isinstance(source.get("monthly_summary"), list) else []
    transaction_analysis = (
        copy.deepcopy(source.get("transaction_analysis"))
        if isinstance(source.get("transaction_analysis"), dict)
        else {}
    )

    for key in (
        "flags",
        "observations",
        "loan_transactions",
        "own_related_transactions",
        "unclassified_transactions",
        "classification_config",
        "parsing_metadata",
        "counterparty_ledger",
        "large_credits",
        "high_value_credits",
    ):
        if key not in transaction_analysis and source.get(key) is not None:
            transaction_analysis[key] = source.get(key)

    threshold = safe_float(
        _first_present(
            summary.get("high_value_threshold"),
            source.get("high_value_threshold"),
            source.get("classification_config", {}).get("large_transaction_threshold")
            if isinstance(source.get("classification_config"), dict)
            else None,
            transaction_analysis.get("classification_config", {}).get("large_transaction_threshold")
            if isinstance(transaction_analysis.get("classification_config"), dict)
            else None,
        )
    )
    if threshold <= 0:
        threshold = 100000.0

    try:
        canonical = build_report_data_from_analysis(
            transactions=transactions,
            monthly_summary=monthly_summary,
            transaction_analysis=transaction_analysis,
            high_value_threshold=threshold,
        )
    except Exception:
        legacy_source = {
            "summary": summary,
            "transactions": transactions,
            "monthly_summary": monthly_summary,
            "counterparty_ledger": transaction_analysis.get("counterparty_ledger", source.get("counterparty_ledger", {})),
            "pdf_integrity": source.get("pdf_integrity", {}),
        }
        canonical = adapt_to_v6(legacy_source)

    canonical["transactions"] = transactions
    canonical["monthly_summary"] = monthly_summary
    canonical["pdf_integrity"] = source.get("pdf_integrity", canonical.get("pdf_integrity", {}))

    restore_fields = (
        "flags",
        "observations",
        "loan_transactions",
        "own_related_transactions",
        "unclassified_transactions",
        "classification_config",
        "parsing_metadata",
        "counterparty_ledger",
        "counterparty_ledger_rows",
        "report_counterparty_rows",
        "top_parties",
    )
    for field in restore_fields:
        value = _first_present(transaction_analysis.get(field), source.get(field))
        if value is not None:
            canonical[field] = value

    large_credits = _first_present(
        transaction_analysis.get("large_credits"),
        transaction_analysis.get("high_value_credits"),
        source.get("large_credits"),
        source.get("high_value_credits"),
    )
    if large_credits is not None:
        canonical["large_credits"] = large_credits

    round_transactions = _first_present(
        source.get("round_transactions"),
        source.get("round_figure_credits"),
        transaction_analysis.get("round_transactions"),
        transaction_analysis.get("round_figure_credits"),
    )
    if not isinstance(round_transactions, list):
        round_transactions = build_round_transactions(transactions)
    canonical["round_transactions"] = round_transactions
    canonical["round_figure_credits"] = round_transactions

    if not canonical.get("counterparty_ledger") and transactions:
        canonical["counterparty_ledger"] = build_track2_counterparty_ledger(transactions)

    if not _has_top_party_rows(canonical.get("top_parties", {})):
        cp_ledger = canonical.get("counterparty_ledger", {})
        company_name = canonical.get("report_info", {}).get("company_name", "")
        related_parties = filter_report_related_parties(
            canonical.get("report_info", {}).get("related_parties", []) or []
        )
        cp_rows = get_report_counterparty_rows_from_data(
            canonical,
            cp_ledger,
            related_parties=related_parties,
            own_related=canonical.get("own_related_transactions", {}) or {},
            company_name=company_name,
        )
        if cp_rows:
            canonical["report_counterparty_rows"] = cp_rows
        canonical["top_parties"] = _top_parties_from_counterparty_rows(
            cp_rows,
            limit=None,
            company_name=company_name,
        )

    canonical.setdefault("summary", {})
    canonical["summary"]["high_value_threshold"] = threshold
    canonical.setdefault("consolidated", {})
    canonical["consolidated"]["high_value_threshold"] = threshold
    canonical["consolidated"]["large_transaction_threshold"] = threshold
    canonical["consolidated"]["large_credit_threshold"] = threshold
    canonical.setdefault("classification_config", {})
    canonical["classification_config"]["large_transaction_threshold"] = threshold
    canonical["classification_config"]["large_credit_threshold"] = threshold

    return make_json_serializable(apply_report_defaults(canonical))


def validate_canonical_report_data(
    data: dict,
) -> tuple[bool, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return False, ["Report JSON root must be an object."], warnings

    report_info = data.get("report_info")
    if not isinstance(report_info, dict):
        errors.append("report_info must be an object.")
    else:
        if not str(report_info.get("company_name", "")).strip():
            errors.append("report_info.company_name is required.")
        if not str(report_info.get("schema_version", "")).strip():
            errors.append("report_info.schema_version is required.")

    required_types = {
        "accounts": (list, "accounts must be a list."),
        "monthly_analysis": (list, "monthly_analysis must be a list."),
        "consolidated": (dict, "consolidated must be an object."),
    }
    for key, (expected_type, message) in required_types.items():
        if key not in data:
            errors.append(f"{key} is required.")
        elif not isinstance(data.get(key), expected_type):
            errors.append(message)

    for key in DEFAULT_REPORT_SECTIONS:
        if key not in data or data.get(key) is None:
            warnings.append(f"Missing optional section '{key}' was filled with a safe default.")

    optional_type_checks = {
        "top_parties": (dict, "top_parties must be an object."),
        "large_credits": (list, "large_credits must be a list."),
        "large_transactions": (list, "large_transactions must be a list."),
        "round_transactions": (list, "round_transactions must be a list."),
        "round_figure_credits": (list, "round_figure_credits must be a list."),
        "loan_transactions": (dict, "loan_transactions must be an object."),
        "flags": (dict, "flags must be an object."),
        "parsing_metadata": (dict, "parsing_metadata must be an object."),
        "unclassified_transactions": (list, "unclassified_transactions must be a list."),
        "classification_config": (dict, "classification_config must be an object."),
        "counterparty_ledger": (dict, "counterparty_ledger must be an object."),
    }
    for key, (expected_type, message) in optional_type_checks.items():
        if key in data and data.get(key) is not None and not isinstance(data.get(key), expected_type):
            errors.append(message)

    observations = data.get("observations")
    if observations is not None and not isinstance(observations, (dict, list)):
        errors.append("observations must be an object or a normalizable list.")

    own_related = data.get("own_related_transactions")
    if own_related is not None and not isinstance(own_related, (dict, list)):
        errors.append("own_related_transactions must be an object or a supported list.")

    pdf_integrity = data.get("pdf_integrity")
    if pdf_integrity is not None and not isinstance(pdf_integrity, (dict, list)):
        errors.append("pdf_integrity must be an object, list, or null.")

    return not errors, errors, warnings


def apply_report_defaults(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}

    for key, default_value in DEFAULT_REPORT_SECTIONS.items():
        if key not in data or data.get(key) is None:
            data[key] = copy.deepcopy(default_value)

    if isinstance(data.get("own_related_transactions"), list):
        data["own_related_transactions"] = {
            "transactions": data.get("own_related_transactions") or [],
            "summary": {},
        }

    if isinstance(data.get("loan_transactions"), list):
        data["loan_transactions"] = {
            "transactions": data.get("loan_transactions") or [],
            "disbursements": [],
            "repayments": [],
            "summary": {},
        }

    normalize_report_observations(data)
    return data


def detect_report_json_schema(data: dict) -> str:
    if not isinstance(data, dict):
        return "unknown"

    canonical_required = {
        "report_info",
        "accounts",
        "monthly_analysis",
        "consolidated",
    }
    if canonical_required.issubset(data.keys()):
        return "canonical_report"

    legacy_indicators = {
        "summary",
        "monthly_summary",
        "transactions",
    }
    if legacy_indicators.issubset(data.keys()):
        return "legacy_raw_report"

    return "unknown"


def normalize_report_observations(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    data["observations"] = normalize_observations(data.get("observations", {}))
    return data


def calculate_report_fingerprint(data: dict) -> str:
    protected_snapshot = build_protected_report_snapshot(data)
    canonical_json = json.dumps(
        protected_snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compare_protected_sections(original_or_export_metadata, uploaded_data) -> list[str]:
    metadata = original_or_export_metadata if isinstance(original_or_export_metadata, dict) else {}
    expected_sections = metadata.get("protected_section_sha256", {})
    if not isinstance(expected_sections, dict) or not expected_sections:
        return []

    current_sections = calculate_protected_section_fingerprints(uploaded_data)
    changed = [
        key
        for key, expected_hash in expected_sections.items()
        if current_sections.get(key) != expected_hash
    ]
    added = [
        key
        for key in current_sections
        if key not in expected_sections
    ]
    return sorted(set(changed + added))


def _finalize_shared_report_data(
    data: dict,
    transactions: List[dict],
    monthly_summary: List[dict],
    threshold: float,
    pdf_integrity: dict | None = None,
) -> dict:
    """Apply report-export fields that both HTML and Excel rely on."""
    if not isinstance(data, dict):
        data = {}

    data.setdefault("transactions", transactions or [])
    data.setdefault("report_info", {}).update(_manual_company_identity_override_info())
    data.setdefault("classification_config", {})
    data["classification_config"]["large_transaction_threshold"] = threshold
    data["classification_config"]["large_credit_threshold"] = threshold
    data.setdefault("consolidated", {})
    data["consolidated"]["high_value_threshold"] = threshold
    data["consolidated"]["large_transaction_threshold"] = threshold
    data["consolidated"]["large_credit_threshold"] = threshold
    data.setdefault("summary", {})
    data["summary"]["high_value_threshold"] = threshold
    if pdf_integrity is not None:
        data["pdf_integrity"] = pdf_integrity

    round_transactions = build_round_transactions(transactions or [])
    data["round_transactions"] = round_transactions
    data["round_figure_credits"] = round_transactions
    data = apply_standard_monthly_summary_to_report(data, monthly_summary or [])
    data = _sync_transaction_pattern_flags(
        data,
        round_transactions=round_transactions,
        large_transactions=data.get("large_transactions", []),
        large_threshold=threshold,
    )
    return _sync_data_quality_status(data)


def _hash_json_value(value) -> str:
    canonical_json = json.dumps(
        make_json_serializable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def safe_report_filename(value: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    safe_value = re.sub(r"_+", "_", safe_value).strip("._-")
    return (safe_value or "report")[:90]


def _report_period_label(report_data: dict) -> str:
    report_info = report_data.get("report_info", {}) if isinstance(report_data, dict) else {}
    start = report_info.get("period_start", "")
    end = report_info.get("period_end", "")
    if start or end:
        return f"{start} to {end}".strip()
    months = [
        str(row.get("month", ""))
        for row in report_data.get("monthly_analysis", [])
        if isinstance(row, dict) and row.get("month")
    ]
    if months:
        return f"{min(months)} to {max(months)}"
    return "Not specified"


def _merge_report_unique_rows(rows: list, key_fields: tuple[str, ...]) -> list:
    merged = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key_parts = tuple(str(row.get(field, "") or "").strip().casefold() for field in key_fields)
        key = key_parts if any(key_parts) else ("row", json.dumps(row, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        merged.append(copy.deepcopy(row))
    return merged


def _merge_related_party_entries_for_reports(reports: list[dict]) -> list:
    merged = []
    seen = set()
    for report in reports or []:
        report_info = report.get("report_info", {}) if isinstance(report, dict) else {}
        parties = report_info.get("related_parties", []) if isinstance(report_info, dict) else []
        for party in parties or []:
            if isinstance(party, dict):
                name = str(party.get("name") or party.get("party_name") or "").strip()
                item = copy.deepcopy(party)
                if name:
                    item["name"] = name
            else:
                name = str(party or "").strip()
                item = {"name": name, "relationship": ""}
            key = name.upper()
            if not key or key in seen:
                continue
            merged.append(item)
            seen.add(key)
    return merged


def _merge_report_info_for_reports(reports: list[dict], source_names: list[str] | None = None) -> dict:
    infos = [
        report.get("report_info", {})
        for report in reports or []
        if isinstance(report, dict) and isinstance(report.get("report_info"), dict)
    ]
    merged = copy.deepcopy(infos[0]) if infos else {}
    company_names = [
        str(info.get("company_name") or "").strip()
        for info in infos
        if str(info.get("company_name") or "").strip()
    ]
    unique_company_names = []
    for name in company_names:
        if name not in unique_company_names:
            unique_company_names.append(name)
    if unique_company_names:
        merged["company_name"] = unique_company_names[0]
        if len(unique_company_names) > 1:
            merged["company_names"] = unique_company_names
    merged["schema_version"] = merged.get("schema_version") or "6.3.5"

    starts = [str(info.get("period_start") or "").strip() for info in infos if str(info.get("period_start") or "").strip()]
    ends = [str(info.get("period_end") or "").strip() for info in infos if str(info.get("period_end") or "").strip()]
    if starts:
        merged["period_start"] = min(starts)
    if ends:
        merged["period_end"] = max(ends)

    merged["related_parties"] = _merge_related_party_entries_for_reports(reports)
    merged["merged_source_files"] = list(source_names or [])
    merged["merged_report_count"] = len(reports or [])
    return merged


def _merge_observations_for_reports(reports: list[dict]) -> dict:
    merged = {"positive": [], "concerns": []}
    for report in reports or []:
        observations = normalize_observations(report.get("observations", {}) if isinstance(report, dict) else {})
        for key in ("positive", "concerns"):
            for item in observations.get(key, []) or []:
                text = str(item)
                if text and text not in merged[key]:
                    merged[key].append(text)
    return merged


def _merge_flags_for_reports(reports: list[dict]) -> dict:
    indicators = []
    seen = set()
    for report in reports or []:
        flags = report.get("flags", {}) if isinstance(report, dict) else {}
        report_indicators = flags.get("indicators", []) if isinstance(flags, dict) else []
        for indicator in report_indicators or []:
            if not isinstance(indicator, dict):
                continue
            key = (
                str(indicator.get("id") or indicator.get("flag_id") or "").strip(),
                str(indicator.get("message") or indicator.get("description") or "").strip(),
            )
            if not any(key):
                key = ("flag", json.dumps(indicator, sort_keys=True, default=str))
            if key in seen:
                continue
            indicators.append(copy.deepcopy(indicator))
            seen.add(key)
    return {"indicators": indicators}


def _merge_transaction_container_for_reports(reports: list[dict], section_key: str) -> dict:
    transactions = []
    disbursements = []
    repayments = []
    for report in reports or []:
        section = report.get(section_key, {}) if isinstance(report, dict) else {}
        if isinstance(section, list):
            transactions.extend(copy.deepcopy(row) for row in section if isinstance(row, dict))
            continue
        if not isinstance(section, dict):
            continue
        transactions.extend(copy.deepcopy(row) for row in section.get("transactions", []) or [] if isinstance(row, dict))
        disbursements.extend(copy.deepcopy(row) for row in section.get("disbursements", []) or [] if isinstance(row, dict))
        repayments.extend(copy.deepcopy(row) for row in section.get("repayments", []) or [] if isinstance(row, dict))
    output = {"transactions": transactions, "summary": {}}
    if disbursements:
        output["disbursements"] = disbursements
    if repayments:
        output["repayments"] = repayments
    return output


def _merge_consolidated_for_reports(reports: list[dict]) -> dict:
    consolidated_rows = [
        report.get("consolidated", {})
        for report in reports or []
        if isinstance(report, dict) and isinstance(report.get("consolidated"), dict)
    ]
    merged: dict = {}
    min_keys = {"eod_lowest", "lowest_balance", "lowest_closing_balance"}
    max_keys = {"eod_highest", "highest_balance", "highest_closing_balance"}
    threshold_keys = {"high_value_threshold", "large_transaction_threshold", "large_credit_threshold"}
    average_values: dict[str, list[float]] = {}

    for row in consolidated_rows:
        for key, value in row.items():
            if isinstance(value, bool):
                continue
            numeric = safe_float(value)
            if isinstance(value, (int, float)) or (isinstance(value, str) and re.search(r"\d", value)):
                if key in threshold_keys:
                    merged[key] = max(safe_float(merged.get(key, 0)), numeric)
                elif key in min_keys:
                    merged[key] = numeric if key not in merged else min(safe_float(merged.get(key)), numeric)
                elif key in max_keys:
                    merged[key] = numeric if key not in merged else max(safe_float(merged.get(key)), numeric)
                elif "average" in key or key.endswith("_avg"):
                    average_values.setdefault(key, []).append(numeric)
                else:
                    merged[key] = safe_float(merged.get(key, 0)) + numeric
            elif key not in merged and value not in (None, ""):
                merged[key] = copy.deepcopy(value)

    for key, values in average_values.items():
        merged[key] = round(sum(values) / len(values), 2) if values else 0.0
    for key, value in list(merged.items()):
        if isinstance(value, float):
            merged[key] = round(value, 2)
    return merged


def _merge_parsing_metadata_for_reports(reports: list[dict]) -> dict:
    merged: dict = {"merged_reports": []}
    for report in reports or []:
        metadata = report.get("parsing_metadata", {}) if isinstance(report, dict) else {}
        if not isinstance(metadata, dict):
            continue
        merged["merged_reports"].append(copy.deepcopy(metadata))
        for key, value in metadata.items():
            if isinstance(value, list):
                merged.setdefault(key, [])
                merged[key].extend(copy.deepcopy(item) for item in value)
            elif isinstance(value, dict):
                merged.setdefault(key, {})
                merged[key].update(copy.deepcopy(value))
            elif key not in merged:
                merged[key] = copy.deepcopy(value)
    return merged


def _is_pdf_integrity_result(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("layer_results"), dict):
        return True
    legacy_layers = value.get("layers", value.get("checks", value.get("findings")))
    if legacy_layers:
        return True
    return bool(value.get("overall_risk") and (value.get("all_findings") is not None or value.get("summary") is not None))


def _pdf_integrity_filename(item: dict, fallback: str) -> str:
    if not isinstance(item, dict):
        return fallback
    for key in ("filename", "file_name", "file", "name", "source_file"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return fallback


def _flatten_pdf_integrity_entries(pdf_integrity: Any, source_label: str = "") -> list[tuple[str, dict]]:
    """Return per-PDF integrity results from canonical, legacy, and merged JSON shapes."""
    entries: list[tuple[str, dict]] = []
    source_label = str(source_label or "").strip()

    if isinstance(pdf_integrity, list):
        for idx, item in enumerate(pdf_integrity, start=1):
            if not isinstance(item, dict):
                continue
            filename = _pdf_integrity_filename(item, f"{source_label or 'pdf'}_{idx}")
            entries.append((filename, copy.deepcopy(item)))
        return entries

    if not isinstance(pdf_integrity, dict):
        return entries

    for list_key in ("files", "results"):
        items = pdf_integrity.get(list_key)
        if isinstance(items, list):
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                filename = _pdf_integrity_filename(item, f"{source_label or list_key}_{idx}")
                entries.append((filename, copy.deepcopy(item)))

    if entries:
        return entries

    skipped_keys = {"summary", "counts", "headline", "overall_risk", "files", "results"}
    for key, value in pdf_integrity.items():
        if key in skipped_keys:
            continue
        if _is_pdf_integrity_result(value):
            filename = _pdf_integrity_filename(value, str(key))
            result = copy.deepcopy(value)
            result.setdefault("filename", filename)
            entries.append((filename, result))
        elif isinstance(value, (dict, list)):
            nested_source = str(key) if not source_label else f"{source_label} :: {key}"
            entries.extend(_flatten_pdf_integrity_entries(value, nested_source))

    return entries


def _merge_pdf_integrity_for_reports(reports: list[dict], source_names: list[str] | None = None) -> dict:
    merged: dict = {}
    seen: set[str] = set()
    for idx, report in enumerate(reports or []):
        if not isinstance(report, dict):
            continue
        source_label = (
            str(source_names[idx]).strip()
            if source_names and idx < len(source_names) and str(source_names[idx]).strip()
            else f"report_{idx + 1}"
        )
        for filename, result in _flatten_pdf_integrity_entries(report.get("pdf_integrity", {}), source_label):
            clean_filename = str(filename or "").strip() or source_label
            output_name = clean_filename
            if output_name in seen:
                output_name = f"{source_label} :: {clean_filename}"
            suffix = 2
            while output_name in seen:
                output_name = f"{source_label} :: {clean_filename} ({suffix})"
                suffix += 1
            seen.add(output_name)
            result_copy = copy.deepcopy(result)
            result_copy["filename"] = output_name
            merged[output_name] = result_copy
    return merged


def merge_report_json_payloads(reports: list[dict], source_names: list[str] | None = None) -> dict:
    """Merge multiple Kredit Lab report JSON payloads into one canonical report."""
    normalized_reports = [
        normalize_report_data_for_export(prepare_uploaded_report(report))
        for report in reports or []
        if isinstance(report, dict)
    ]
    if not normalized_reports:
        raise ValueError("No valid report JSON payloads were provided.")

    transactions = [
        copy.deepcopy(txn)
        for report in normalized_reports
        for txn in (report.get("transactions", []) or [])
        if isinstance(txn, dict)
    ]
    monthly_analysis = [
        copy.deepcopy(row)
        for report in normalized_reports
        for row in (report.get("monthly_analysis", []) or [])
        if isinstance(row, dict)
    ]
    monthly_summary = [
        copy.deepcopy(row)
        for report in normalized_reports
        for row in (report.get("monthly_summary", []) or [])
        if isinstance(row, dict)
    ]

    threshold = max(
        [
            safe_float(
                report.get("classification_config", {}).get("large_transaction_threshold")
                if isinstance(report.get("classification_config"), dict)
                else 0
            )
            or safe_float(
                report.get("consolidated", {}).get("high_value_threshold")
                if isinstance(report.get("consolidated"), dict)
                else 0
            )
            for report in normalized_reports
        ]
        or [100000.0]
    )
    if threshold <= 0:
        threshold = 100000.0

    merged = {
        "report_info": _merge_report_info_for_reports(normalized_reports, source_names),
        "accounts": _merge_report_unique_rows(
            [
                copy.deepcopy(row)
                for report in normalized_reports
                for row in (report.get("accounts", []) or [])
                if isinstance(row, dict)
            ],
            ("bank_name", "bank", "account_no", "account_number"),
        ),
        "transactions": transactions,
        "monthly_analysis": monthly_analysis,
        "monthly_summary": monthly_summary,
        "consolidated": _merge_consolidated_for_reports(normalized_reports),
        "flags": _merge_flags_for_reports(normalized_reports),
        "observations": _merge_observations_for_reports(normalized_reports),
        "own_related_transactions": _merge_transaction_container_for_reports(
            normalized_reports,
            "own_related_transactions",
        ),
        "loan_transactions": _merge_transaction_container_for_reports(
            normalized_reports,
            "loan_transactions",
        ),
        "large_credits": [
            copy.deepcopy(row)
            for report in normalized_reports
            for row in (report.get("large_credits", []) or [])
            if isinstance(row, dict)
        ],
        "large_transactions": [
            copy.deepcopy(row)
            for report in normalized_reports
            for row in (report.get("large_transactions", []) or [])
            if isinstance(row, dict)
        ],
        "round_transactions": [
            copy.deepcopy(row)
            for report in normalized_reports
            for row in (report.get("round_transactions", []) or [])
            if isinstance(row, dict)
        ],
        "unclassified_transactions": [
            copy.deepcopy(row)
            for report in normalized_reports
            for row in (report.get("unclassified_transactions", []) or [])
            if isinstance(row, dict)
        ],
        "classification_config": {
            "large_transaction_threshold": threshold,
            "large_credit_threshold": threshold,
        },
        "parsing_metadata": _merge_parsing_metadata_for_reports(normalized_reports),
        "pdf_integrity": _merge_pdf_integrity_for_reports(normalized_reports, source_names),
    }

    if transactions and build_track2_counterparty_ledger is not None:
        merged["counterparty_ledger"] = build_track2_counterparty_ledger(transactions)
    else:
        merged["counterparty_ledger"] = {
            "counterparties": [
                copy.deepcopy(cp)
                for report in normalized_reports
                for cp in (report.get("counterparty_ledger", {}).get("counterparties", []) if isinstance(report.get("counterparty_ledger"), dict) else [])
                if isinstance(cp, dict)
            ]
        }

    merged["consolidated"].setdefault("high_value_threshold", threshold)
    merged["consolidated"]["large_transaction_threshold"] = threshold
    merged["consolidated"]["large_credit_threshold"] = threshold

    merged = normalize_report_data_for_export(apply_report_defaults(merged))
    return prepare_report_for_export(merged)


def render_merged_report_json_section() -> None:
    st.markdown("---")
    st.subheader("Merge Report JSON Files")

    uploaded_report_jsons = st.file_uploader(
        "Upload Multiple Report JSON Files",
        type=["json"],
        key="merged_report_json_upload",
        accept_multiple_files=True,
        help=(
            "Upload two or more Kredit Lab report JSON files from different banks. "
            "They will be merged into one report and exported using the same HTML format."
        ),
    )

    if not uploaded_report_jsons:
        return

    file_payloads = []
    errors = []
    source_names = []
    hash_builder = hashlib.sha256()
    total_size = 0

    for uploaded_file in uploaded_report_jsons:
        file_bytes = uploaded_file.getvalue()
        total_size += len(file_bytes)
        hash_builder.update(str(uploaded_file.name or "").encode("utf-8"))
        hash_builder.update(file_bytes)
        source_names.append(str(uploaded_file.name or "report.json"))
        if not str(uploaded_file.name or "").lower().endswith(".json"):
            errors.append(f"{uploaded_file.name}: only .json report files are accepted.")
            continue
        try:
            payload = json.loads(file_bytes.decode("utf-8"))
        except UnicodeDecodeError as exc:
            errors.append(f"{uploaded_file.name}: invalid JSON encoding ({exc}).")
        except json.JSONDecodeError as exc:
            errors.append(f"{uploaded_file.name}: invalid JSON ({exc}).")
        else:
            if not isinstance(payload, dict):
                errors.append(f"{uploaded_file.name}: report JSON must be a JSON object.")
            else:
                file_payloads.append(payload)

    upload_hash = hash_builder.hexdigest()
    if st.session_state.get("merged_report_upload_sha256") != upload_hash:
        st.session_state.merged_report_upload_sha256 = upload_hash
        st.session_state.merged_report_data = None
        st.session_state.merged_report_errors = []
        st.session_state.merged_report_warnings = []

        if len(uploaded_report_jsons) < 2:
            errors.append("Upload at least two report JSON files to merge.")
        if total_size > REPORT_JSON_MAX_SIZE_BYTES:
            errors.append("Combined uploaded report JSON size is larger than the 50 MB limit.")

        if not errors:
            try:
                merged_report_data = merge_report_json_payloads(file_payloads, source_names)
            except Exception as exc:
                errors.append(f"Could not merge report JSON files: {exc}")
            else:
                is_valid, validation_errors, warnings = validate_canonical_report_data(merged_report_data)
                errors.extend(validation_errors)
                st.session_state.merged_report_warnings = warnings
                if is_valid:
                    st.session_state.merged_report_data = merged_report_data

        st.session_state.merged_report_errors = errors

    for warning in st.session_state.get("merged_report_warnings", []) or []:
        st.warning(warning)
    for error in st.session_state.get("merged_report_errors", []) or []:
        st.error(error)

    merged_report_data = st.session_state.get("merged_report_data")
    if not merged_report_data:
        return

    report_info = merged_report_data.get("report_info", {}) if isinstance(merged_report_data, dict) else {}
    st.success(f"Merged {len(uploaded_report_jsons)} report JSON files successfully.")

    metric_values = [
        ("Company", report_info.get("company_name", "Unknown")),
        ("Banks / accounts", len(merged_report_data.get("accounts", []) or [])),
        ("Transactions", len(merged_report_data.get("transactions", []) or [])),
        ("Months", len(merged_report_data.get("monthly_analysis", []) or [])),
        ("Counterparties", len(merged_report_data.get("counterparty_ledger", {}).get("counterparties", []) or [])),
    ]
    metric_cols = st.columns(5)
    for idx, (label, value) in enumerate(metric_values):
        metric_cols[idx].metric(label, value)

    safe_company_name = safe_report_filename(report_info.get("company_name", "merged_report"))
    merged_html = generate_interactive_html(merged_report_data)
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Generate HTML from Merged JSON",
            merged_html,
            file_name=f"{safe_company_name}_merged_report.html",
            mime="text/html",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "Download Merged JSON",
            json.dumps(
                make_json_serializable(merged_report_data),
                indent=2,
                ensure_ascii=False,
            ),
            file_name=f"{safe_company_name}_merged_report.json",
            mime="application/json",
            use_container_width=True,
        )


def render_imported_report_json_section() -> None:
    render_merged_report_json_section()

    st.markdown("---")
    st.subheader("Upload Edited Report JSON")

    uploaded_report_json = st.file_uploader(
        "Upload Edited Report JSON",
        type=["json"],
        key="edited_report_json_upload",
        help=(
            "Upload a Kredit Lab editable report JSON, including a version "
            "updated by an AI agent, to regenerate the interactive HTML."
        ),
    )

    if uploaded_report_json is not None:
        file_size = getattr(uploaded_report_json, "size", None)
        file_bytes = uploaded_report_json.getvalue()
        if file_size is None:
            file_size = len(file_bytes)

        upload_hash = hashlib.sha256(file_bytes).hexdigest()
        if st.session_state.get("imported_report_upload_sha256") != upload_hash:
            st.session_state.imported_report_acknowledged = False
            st.session_state.imported_report_data = None
            st.session_state.imported_report_validation = {}
            st.session_state.imported_report_upload_sha256 = upload_hash

            if file_size > REPORT_JSON_MAX_SIZE_BYTES:
                st.error("Uploaded report JSON is larger than the 50 MB limit.")
            elif not str(uploaded_report_json.name or "").lower().endswith(".json"):
                st.error("Only .json report files are accepted.")
            else:
                try:
                    uploaded_data = json.loads(file_bytes.decode("utf-8"))
                except UnicodeDecodeError as exc:
                    st.error(f"Invalid JSON encoding: {exc}")
                except json.JSONDecodeError as exc:
                    st.error(f"Invalid JSON: {exc}")
                else:
                    schema = detect_report_json_schema(uploaded_data)
                    if not isinstance(uploaded_data, dict):
                        st.error("Report JSON root must be an object.")
                    elif schema == "unknown":
                        st.error(
                            "The file is not a recognised Kredit Lab report. Required canonical fields: "
                            "report_info, accounts, monthly_analysis, consolidated."
                        )
                    else:
                        pre_default_warnings = [
                            f"Missing optional section '{key}' was filled with a safe default."
                            for key in DEFAULT_REPORT_SECTIONS
                            if key not in uploaded_data or uploaded_data.get(key) is None
                        ]
                        try:
                            validated_report_data = prepare_uploaded_report(uploaded_data)
                        except Exception as exc:
                            st.error(f"Could not prepare report JSON: {exc}")
                        else:
                            is_valid, errors, warnings = validate_canonical_report_data(validated_report_data)
                            warnings = pre_default_warnings + [
                                warning for warning in warnings if warning not in pre_default_warnings
                            ]
                            fingerprint_status, changed_sections = _fingerprint_status_for_report(validated_report_data)
                            st.session_state.imported_report_validation = {
                                "schema": schema,
                                "is_valid": is_valid,
                                "errors": errors,
                                "warnings": warnings,
                                "fingerprint_status": fingerprint_status,
                                "changed_sections": changed_sections,
                            }
                            if is_valid:
                                st.session_state.imported_report_data = validated_report_data
                            else:
                                st.session_state.imported_report_data = None

    imported_report_data = st.session_state.get("imported_report_data")
    validation = st.session_state.get("imported_report_validation", {}) or {}

    if validation.get("schema") == "legacy_raw_report":
        st.warning(
            "Legacy raw report detected. It was converted to the current canonical report schema. "
            "Some values may be rebuilt from transactions."
        )

    for warning in validation.get("warnings", []) or []:
        st.warning(warning)
    for error in validation.get("errors", []) or []:
        st.error(error)

    if not imported_report_data:
        return

    st.success("Editable report JSON loaded successfully.")

    report_info = imported_report_data.get("report_info", {}) if isinstance(imported_report_data, dict) else {}
    observations = normalize_observations(imported_report_data.get("observations", {}))
    flags = imported_report_data.get("flags", {}) if isinstance(imported_report_data.get("flags"), dict) else {}
    indicators = flags.get("indicators", []) if isinstance(flags.get("indicators"), list) else []
    fingerprint_status = validation.get("fingerprint_status", "No export fingerprint found.")
    changed_sections = validation.get("changed_sections", []) or []
    fingerprint_changed = bool(changed_sections) or fingerprint_status.startswith("The uploaded JSON contains")

    metric_values = [
        ("Company", report_info.get("company_name", "Unknown")),
        ("Period", _report_period_label(imported_report_data)),
        ("Schema version", report_info.get("schema_version", "")),
        ("Accounts", len(imported_report_data.get("accounts", []) or [])),
        ("Months", len(imported_report_data.get("monthly_analysis", []) or [])),
        ("Flags", len(indicators)),
        ("Positive observations", len(observations.get("positive", []))),
        ("Concern observations", len(observations.get("concerns", []))),
        ("Validation", "Valid" if validation.get("is_valid") else "Invalid"),
        ("Fingerprint", "Changed" if fingerprint_changed else "Passed/Unavailable"),
    ]
    preview_cols = st.columns(5)
    for idx, (label, value) in enumerate(metric_values):
        preview_cols[idx % 5].metric(label, value)

    if fingerprint_changed:
        st.warning(fingerprint_status)
        if changed_sections:
            st.write("Changed protected sections: " + ", ".join(changed_sections))
        st.session_state.imported_report_acknowledged = st.checkbox(
            "I acknowledge that this uploaded JSON changes protected report data.",
            key="imported_report_ack_checkbox",
            value=bool(st.session_state.get("imported_report_acknowledged", False)),
        )
    else:
        st.info(fingerprint_status)

    st.markdown("### Positive Observations")
    if observations.get("positive"):
        for item in observations.get("positive", []):
            st.markdown(f"- {escape(str(item))}")
    else:
        st.caption("None")

    st.markdown("### Concerns")
    if observations.get("concerns"):
        for item in observations.get("concerns", []):
            st.markdown(f"- {escape(str(item))}")
    else:
        st.caption("None")

    safe_company_name = safe_report_filename(report_info.get("company_name", "report"))
    downloads_disabled = fingerprint_changed and not st.session_state.get("imported_report_acknowledged", False)
    imported_html = generate_interactive_html(imported_report_data) if not downloads_disabled else ""

    import_col1, import_col2 = st.columns(2)
    with import_col1:
        st.download_button(
            "Generate HTML from Uploaded JSON",
            imported_html,
            file_name=f"{safe_company_name}_updated_report.html",
            mime="text/html",
            use_container_width=True,
            disabled=downloads_disabled,
        )
    with import_col2:
        st.download_button(
            "Download Validated JSON",
            json.dumps(
                make_json_serializable(imported_report_data),
                indent=2,
                ensure_ascii=False,
            ),
            file_name=f"{safe_company_name}_validated_report.json",
            mime="application/json",
            use_container_width=True,
        )


__all__ = [
    'bind_app_globals',
    'build_shared_report_data',
    'build_report_data_from_analysis',
    'normalize_report_data_for_export',
    'merge_report_json_payloads',
    'prepare_report_for_export',
    'prepare_uploaded_report',
    'convert_legacy_report_to_canonical',
    'validate_canonical_report_data',
    'apply_report_defaults',
    'detect_report_json_schema',
    'normalize_report_observations',
    'calculate_report_fingerprint',
    'compare_protected_sections',
    '_finalize_shared_report_data',
    '_hash_json_value',
    'safe_report_filename',
    '_report_period_label',
    'render_merged_report_json_section',
    'render_imported_report_json_section',
]
