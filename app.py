# Add this near the top of your app.py file, after the imports

import copy
import hashlib
import json
import re
import textwrap
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
from party_utils import (
    _merge_counterparty_groups,
    apply_party_aliasing,
    build_transactions_by_party,
    clean_counterparty_name,
    deduplicate_counterparty_names,
    normalise_counterparty_for_ledger,
)

from maybank import parse_transactions_maybank
from public_bank import parse_transactions_pbb
from rhb import parse_transactions_rhb
from cimb import parse_transactions_cimb,extract_cimb_party_name
from bank_islam import parse_bank_islam
from bank_rakyat import parse_bank_rakyat
from hong_leong import parse_hong_leong
from ambank import (
    clean_ambank_company_name,
    parse_ambank,
    extract_ambank_statement_totals,
    extract_ambank_company_name,
)
from bank_muamalat import parse_transactions_bank_muamalat
from affin_bank import parse_affin_bank, extract_affin_statement_totals
from agro_bank import parse_agro_bank
from ocbc import parse_transactions_ocbc
from uob import parse_transactions_uob
from alliance import parse_transactions_alliance
from pdf_security import is_pdf_encrypted, decrypt_pdf_bytes

# Import the extracted functions
from pdf_utils import clean_extracted_company_name, extract_company_name, extract_account_number
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

# Track 2 classifier — import ALL functions needed
try:
    from kredit_lab_classify_track2 import (
        # Core engine functions
        build_track2_result,
        account_meta_from_determinations,
        classify_transactions,
        compute_risk_flags,
        
        # Pattern matching for loans (CRITICAL for loan detection)
        LOAN_DISBURSEMENT_RE,
        LOAN_REPAYMENT_RE,
        
        # Statutory compliance
        compute_statutory_compliance,
        compute_salary_payments,
        compute_epf_payments,
        compute_socso_payments,
        compute_lhdn_tax_payments,
        compute_hrdf_payments,
        
        # FX and other detectors
        compute_fx_totals,
        compute_round_figure_credits,
        compute_high_value_credits,
        compute_returned_cheques,
        
        # Counterparty ledger functions
        scan_related_party_candidates,
        auto_confirmed_related_parties,
        advisory_rp_candidates,
        dedup_counterparty_entries,
        
        # Monthly aggregator
        compute_monthly_aggregates,
        compute_monthly_eod,
        
        # Constants
        CANONICAL_FLAGS,
        LOW_CLOSING_THRESHOLD_CR,
        OD_HIGH_UTILISATION_RATIO,
        SUBTHRESHOLD_TOTAL_SALARY_RM,
        CHANNEL_BLIND_CHEQUE_DR_MIN_RM,
        CHANNEL_BLIND_CHEQUE_DR_MIN_RATIO,
    )
    _TRACK2_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] Track 2 import failed: {e}")
    _TRACK2_AVAILABLE = False

# ============================================================
# EXTRACTED APPLICATION MODULES
# ============================================================
from sidebar_navigation import (
    bind_app_globals as _bind_sidebar_navigation_globals,
    get_main_content_class,
    init_sidebar_navigation,
    render_sidebar_navigation,
    toggle_sidebar,
)
from report_generator import (
    bind_app_globals as _bind_report_generator_globals,
    _average_statutory_ratio_pct,
    _sync_data_quality_status,
    _sync_transaction_pattern_flags,
    _top_parties_from_counterparty_rows,
    adapt_to_v6,
    apply_standard_monthly_summary_to_report,
    build_formula_validation_checks_for_report,
    build_large_transactions,
    build_own_related_party_groups_for_report,
    build_round_transactions,
    generate_interactive_html,
    get_round_transactions_for_report,
    normalize_observations,
    prepare_top_parties_for_report,
)
from excel_exporter import (
    bind_app_globals as _bind_excel_exporter_globals,
    _excel_safe_value,
    _normalise_pdf_integrity_layer_rows,
    _pdf_detail_to_excel_text,
    _records_to_excel_df,
    _write_excel_sections_sheet,
    _write_excel_sheet,
    build_parsing_qc_dataframe_from_parsing_metadata,
    build_risk_signals_dataframe_for_excel,
    generate_excel_report,
)
from counterparty_ledger import (
    bind_app_globals as _bind_counterparty_ledger_globals,
    _build_counterparty_json_payload,
    _canonical_report_counterparty_display_name,
    _copy_counterparty_row_for_report,
    _counterparty_row_matches_report_party_name,
    _counterparty_row_report_match_sources,
    _extract_counterparty_mapping_from_json,
    _merge_report_counterparty_row,
    _report_candidate_contains_party_tokens,
    _report_counterparty_alignment_targets,
    _report_name_matches_own_party,
    _report_party_display_name,
    _report_party_names_equivalent,
    _report_related_party_entries,
    _resolve_transaction_counterparty_details,
    build_report_counterparty_ledger_rows,
    build_track2_counterparty_ledger,
    copy_report_counterparty_rows,
    filter_report_related_parties,
    get_report_counterparty_rows_from_data,
    prepare_counterparty_dataframe,
    render_counterparty_ledger_table,
    resolve_transaction_counterparty,
)
from report_utils import (
    bind_app_globals as _bind_report_utils_globals,
    _finalize_shared_report_data,
    _hash_json_value,
    _report_period_label,
    apply_report_defaults,
    build_report_data_from_analysis,
    build_shared_report_data,
    calculate_report_fingerprint,
    compare_protected_sections,
    convert_legacy_report_to_canonical,
    detect_report_json_schema,
    normalize_report_data_for_export,
    normalize_report_observations,
    prepare_report_for_export,
    prepare_uploaded_report,
    render_imported_report_json_section,
    safe_report_filename,
    validate_canonical_report_data,
)
from pattern_analysis import (
    bind_app_globals as _bind_pattern_analysis_globals,
    compute_epf_payments,
    compute_hrdf_payments,
    compute_lhdn_tax_payments,
    compute_socso_payments,
    detect_duplicate_transactions,
    detect_rapid_repeat_transactions,
    is_round_number,
    normalize_text,
    render_metric_cards,
    render_pattern_details,
    render_transaction_overview,
    run_fraud_checks,
    summarize_transaction_patterns,
)
from integrity_display import (
    bind_app_globals as _bind_integrity_display_globals,
    file_risk_label,
    integrity_layer_counts,
    is_benign_integrity_finding,
    render_fraud_summary,
    render_integrity_metric,
    render_integrity_overview,
    render_integrity_report_styles,
    severity_badge,
    severity_dot,
)
from related_party_manager import (
    bind_app_globals as _bind_related_party_manager_globals,
    _align_related_party_candidates_to_counterparty_rows,
    _counterparty_row_for_related_party_name,
    _related_party_counterparty_row_name,
    detect_related_party_candidates,
    partition_related_party_candidates_for_manager,
    render_related_party_manager,
)

_EXTRACTED_MODULE_BINDERS = (
    _bind_sidebar_navigation_globals,
    _bind_report_generator_globals,
    _bind_excel_exporter_globals,
    _bind_counterparty_ledger_globals,
    _bind_report_utils_globals,
    _bind_pattern_analysis_globals,
    _bind_integrity_display_globals,
    _bind_related_party_manager_globals,
)


def _bind_extracted_module_globals() -> None:
    app_globals = globals()
    for binder in _EXTRACTED_MODULE_BINDERS:
        binder(app_globals)


_bind_extracted_module_globals()

# ============================================================
# SIDEBAR NAVIGATION FOR STREAMLIT APP
# ============================================================








# ============================================================
# END OF SIDEBAR NAVIGATION FUNCTIONS
# ============================================================

# ============================================================
# STREAMLIT PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="Bank Statement Parser", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize sidebar navigation
init_sidebar_navigation()

# Render the sidebar
sidebar_collapsed = render_sidebar_navigation()

# Get the main content class
main_class = get_main_content_class()

# Wrap your main content
st.markdown(textwrap.dedent(f"""
<style>
    .main-content-wrapper {{
        max-width: 1400px;
        margin: 0 auto;
    }}
    
    .section-anchor {{
        scroll-margin-top: 80px;
    }}
</style>
<div class="{main_class}">
"""), unsafe_allow_html=True)

# ============================================================
# SESSION STATE INIT
# ============================================================

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

if "counterparty_name_overrides" not in st.session_state:
    st.session_state.counterparty_name_overrides = {}

if "imported_report_data" not in st.session_state:
    st.session_state.imported_report_data = None

if "imported_report_validation" not in st.session_state:
    st.session_state.imported_report_validation = {}

if "imported_report_upload_sha256" not in st.session_state:
    st.session_state.imported_report_upload_sha256 = ""

if "imported_report_acknowledged" not in st.session_state:
    st.session_state.imported_report_acknowledged = False

# ============================================================
# build_large_transactions FUNCTION
# ============================================================


# Function copy from HTML, def generate_interactive_html(data) - you will replace this with the full function from your original converter file


# ============================================================
# HTML REPORT GENERATION FUNCTIONS (from your JSON converter)
# Copy the entire set of functions here
# ============================================================

def fmt(val, decimals=2):
    """Format number with commas"""
    if val is None:
        return "0.00"
    return f"{val:,.{decimals}f}"



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


def _top_parties_from_transaction_analysis(transaction_analysis: dict) -> dict:
    if not isinstance(transaction_analysis, dict):
        return {"top_payers": [], "top_payees": []}

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


def _has_top_party_rows(top_parties: dict) -> bool:
    if not isinstance(top_parties, dict):
        return False
    payers = top_parties.get("top_payers") or top_parties.get("top_creditors") or []
    payees = top_parties.get("top_payees") or top_parties.get("top_debtors") or []
    return bool(payers or payees)


_REPORT_SPECIAL_COUNTERPARTY_BUCKETS = {
    "UNIDENTIFIED",
    "UNKNOWN",
    "UNKNOWN PARTY",
    "UNCATEGORIZED",
    "CHEQUE",
    "UNIDENTIFIED (CHEQUE)",
    "UNIDENTIFIED (CASH)",
    "CASH DEPOSIT",
    "CASH WITHDRAWAL",
    "BANK FEES",
    "BANK FEE",
    "SYSTEM",
    "BANK SYSTEM",
    "BANK / SYSTEM",
    "SERVICE CHARGE",
    "BULK SALARY",
    "FD/INTEREST",
    "FD INTEREST",
    "INTEREST",
    "PROFIT PAID",
    "PROFIT CHARGED",
    "LOAN REPAYMENT",
    "LOAN DISBURSEMENT",
    "KWSP",
    "KUMPULAN WANG SIMPAN PEKERJA",
    "KUMPULAN WANG SIMPANAN PEKERJA",
    "SOCSO",
    "LHDN",
    "HRDF",
    "TRANSFER FEE",
    "OTHER TRANSFER FEE",
    "REVERSAL",
    "RETURNED CHEQUE",
    "INWARD RETURN",
    "APAYLATER",
    "AUTOPAY CR",
    "AUTOPAY DR",
    "MTH END",
    "MONTH END",
    "OPENING BALANCE",
    "CLOSING BALANCE",
    "SPECIAL BUCKET",
}

_REPORT_RANKABLE_SPECIAL_BUCKETS = {"JANM"}


def _report_counterparty_label(name) -> str:
    text = re.sub(r"[_\s]+", " ", str(name or "").strip().upper())
    return text


def _is_report_special_counterparty_bucket(name) -> bool:
    upper = _report_counterparty_label(name)
    if not upper:
        return True
    if upper in _REPORT_RANKABLE_SPECIAL_BUCKETS:
        return False
    if upper in _REPORT_SPECIAL_COUNTERPARTY_BUCKETS:
        return True
    return bool(
        re.match(r"^UNIDENTIFIED(?:\s.*)?$", upper)
        or re.match(r"^UNNAMED\s+.+?\s+TRANSFER\s*\((?:CR|DR)\)\s*$", upper)
        or re.match(r"^UNNAMED\s+INTERNAL\s+PAYROLL\s*\((?:CR|DR)\)\s*$", upper)
        or re.match(r"^CARD\s+POS\s*\([A-Z]+\)\s*$", upper)
        or re.match(r"^(?:MTH|MONTH)\s+END(?:\s+.*)?$", upper)
    )


def _is_report_unknown_counterparty(name) -> bool:
    upper = _report_counterparty_label(name)
    return upper in {"", "UNKNOWN", "UNKNOWN PARTY", "UNIDENTIFIED", "UNCATEGORIZED"}


def _counterparty_rows_have_stat(counterparty_rows, stat_key: str) -> bool:
    return any(isinstance(row, dict) and stat_key in row for row in counterparty_rows or [])


def _counterparty_row_stat_total(counterparty_rows, stat_key: str, exclude_unknown: bool = False) -> int:
    total = 0
    for row in counterparty_rows or []:
        if not isinstance(row, dict):
            continue
        name = row.get("counterparty_name") or row.get("counterparty") or row.get("party")
        if exclude_unknown and _is_report_unknown_counterparty(name):
            continue
        total += int(safe_float(row.get(stat_key, 0)))
    return total


def _counterparty_unknown_transaction_count(counterparty_rows) -> int:
    total = 0
    for row in counterparty_rows or []:
        if not isinstance(row, dict):
            continue
        name = row.get("counterparty_name") or row.get("counterparty") or row.get("party")
        if not _is_report_unknown_counterparty(name):
            continue
        count = int(safe_float(row.get("transaction_count", 0)))
        if not count:
            count = int(safe_float(row.get("credit_count", 0))) + int(safe_float(row.get("debit_count", 0)))
        if not count:
            count = len(row.get("transactions", []) or [])
        total += count
    return total


def _ledger_monthly_breakdown(transactions: List[dict], side: str) -> List[dict]:
    buckets = {}
    side = side.upper()
    for txn in transactions or []:
        if not isinstance(txn, dict):
            continue

        raw_type = str(txn.get("type") or txn.get("transaction_type") or "").upper()
        credit = safe_float(txn.get("credit", 0))
        debit = safe_float(txn.get("debit", 0))
        amount = 0.0
        if side == "CREDIT":
            if credit > 0:
                amount = credit
            elif raw_type in ("CREDIT", "CR") or "CREDIT" in raw_type:
                amount = abs(safe_float(txn.get("amount", 0)))
        else:
            if debit > 0:
                amount = debit
            elif raw_type in ("DEBIT", "DR") or "DEBIT" in raw_type:
                amount = abs(safe_float(txn.get("amount", 0)))
        if amount <= 0:
            continue

        date_text = str(txn.get("date") or txn.get("transaction_date") or "")
        month_match = re.search(r"\d{4}-\d{2}", date_text)
        if not month_match:
            continue
        month = month_match.group(0)
        bucket = buckets.setdefault(month, {"month": month, "amount": 0.0, "count": 0})
        bucket["amount"] += amount
        bucket["count"] += 1

    return [
        {"month": month, "amount": round(row["amount"], 2), "count": row["count"]}
        for month, row in sorted(buckets.items())
    ]




def _top_parties_from_counterparty_ledger(counterparty_ledger: dict, limit: Optional[int] = 10, company_name: str = "") -> dict:
    if not isinstance(counterparty_ledger, dict):
        return {"top_payers": [], "top_payees": []}
    return _top_parties_from_counterparty_rows(
        build_canonical_counterparty_ledger_rows(counterparty_ledger),
        limit=limit,
        company_name=company_name,
    )


def build_canonical_counterparty_ledger_rows(cp_ledger: dict) -> List[dict]:
    """Return the shared counterparty ledger view used by UI, HTML, and Excel."""
    raw_counterparties = cp_ledger.get("counterparties", []) if isinstance(cp_ledger, dict) else []
    raw_counterparties = [cp for cp in raw_counterparties or [] if isinstance(cp, dict)]
    if not raw_counterparties:
        return []

    raw_names = [str(cp.get("counterparty_name", cp.get("counterparty", "")) or "") for cp in raw_counterparties]
    try:
        clean_names = deduplicate_counterparty_names(raw_names)
    except Exception:
        clean_names = [clean_counterparty_name(name) or name for name in raw_names]

    merged_counterparties = {}
    for cp, clean_name in zip(raw_counterparties, clean_names):
        clean_name = clean_name or clean_counterparty_name(cp.get("counterparty_name", "")) or "UNKNOWN"
        clean_name = re.sub(r"\s+", " ", str(clean_name).strip()).upper() or "UNKNOWN"
        key = clean_name.casefold()
        merged = merged_counterparties.setdefault(
            key,
            {
                "counterparty_name": clean_name,
                "total_credits": 0.0,
                "total_debits": 0.0,
                "net_position": 0.0,
                "transaction_count": 0,
                "credit_count": 0,
                "debit_count": 0,
                "pattern_matched": 0,
                "special_bucket": 0,
                "raw_fallback": 0,
                "transactions": [],
                "raw_names": set(),
                "is_related_party": False,
            },
        )

        raw_name = str(cp.get("counterparty_name", cp.get("counterparty", "")) or "").strip()
        if raw_name:
            merged["raw_names"].add(raw_name)
        merged["total_credits"] += safe_float(cp.get("total_credits", cp.get("total_credit", 0)))
        merged["total_debits"] += safe_float(cp.get("total_debits", cp.get("total_debit", 0)))
        merged["credit_count"] += int(safe_float(cp.get("credit_count", cp.get("credit_tx_count", 0))))
        merged["debit_count"] += int(safe_float(cp.get("debit_count", cp.get("debit_tx_count", 0))))
        merged["transaction_count"] += int(safe_float(cp.get("transaction_count", 0)))
        merged["pattern_matched"] += int(safe_float(cp.get("pattern_matched", 0)))
        merged["special_bucket"] += int(safe_float(cp.get("special_bucket", 0)))
        merged["raw_fallback"] += int(safe_float(cp.get("raw_fallback", 0)))

        related_raw = cp.get("is_related_party", cp.get("related_party", False))
        if bool(related_raw) and str(related_raw).strip().lower() not in {"false", "no", "0"}:
            merged["is_related_party"] = True

        for txn in cp.get("transactions", []) or []:
            if isinstance(txn, dict):
                txn_copy = dict(txn)
                txn_copy["counterparty_name_clean"] = clean_name
                merged["transactions"].append(txn_copy)

    merged_counterparties = _merge_counterparty_groups(
        {
            str(cp.get("counterparty_name") or "UNKNOWN"): cp
            for cp in merged_counterparties.values()
        }
    )

    rows = []
    for cp in merged_counterparties.values():
        cp["total_credits"] = round(cp["total_credits"], 2)
        cp["total_debits"] = round(cp["total_debits"], 2)
        cp["net_position"] = round(cp["total_credits"] - cp["total_debits"], 2)
        if not cp["transaction_count"]:
            cp["transaction_count"] = cp["credit_count"] + cp["debit_count"]
        if not cp["transaction_count"]:
            cp["transaction_count"] = len(cp.get("transactions", []) or [])
        cp["raw_names"] = sorted(name for name in cp["raw_names"] if name)
        cp["transactions"] = sorted(
            cp.get("transactions", []) or [],
            key=lambda tx: (str(tx.get("date") or ""), str(tx.get("description") or "")),
        )
        rows.append(cp)

    rows.sort(key=lambda cp: str(cp.get("counterparty_name", "") or "").casefold())
    return rows




def _report_party_relationship(party) -> str:
    if not isinstance(party, dict):
        return ""
    return re.sub(r"\s+", " ", str(party.get("relationship") or "").strip())


def _matched_report_related_party_name(counterparty_name, description, related_parties) -> str:
    related_party_names = [
        name for name, _relationship in _report_related_party_entries(related_parties)
        if name
    ]
    cp_upper = str(counterparty_name or "").upper()
    desc_upper = str(description or "").upper()
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
        return ""
    return max(matches, key=lambda value: (len(value), value.casefold()))


_REPORT_OWN_PARTY_FALLBACKS = {
    "UNKNOWN",
    "UNKNOWN PARTY",
    "COMPANY",
    "OWN PARTY",
    "OWN PARTY (SELF)",
    "SELF",
}


def _report_clean_party_display_name(value) -> str:
    name = re.sub(
        r"\s*\(\s*OWN[\s\-_]?PARTY\s*\)\s*",
        " ",
        str(value or ""),
        flags=re.I,
    )
    return re.sub(r"\s+", " ", name).strip()


def _strip_report_company_suffix(name: str) -> str:
    cleaned = _report_clean_party_display_name(name)
    stripped = re.sub(
        r"\s+(?:SDN\s+BHD|SDN\s+BH|SDN\s+B|BHD|BERHAD)\.?\s*$",
        "",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", stripped).strip() or cleaned


def _report_flexible_phrase_pattern(name: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", str(name or "").upper())
    if not tokens:
        return ""
    return r"\s+".join(re.escape(token) for token in tokens)


def _own_party_source_text_for_report(txns) -> str:
    parts = []
    for txn in txns or []:
        if not isinstance(txn, dict):
            continue
        party_type = str(txn.get("party_type") or "").upper()
        if party_type and not party_type.startswith("OWN"):
            continue
        for field in (
            "party_name",
            "counterparty_name",
            "counterparty_name_clean",
            "counterparty_name_raw",
            "description",
        ):
            value = txn.get(field)
            if value:
                parts.append(str(value))
    return " ".join(parts).upper()


def _own_party_suffix_visible_in_sources(company_name: str, txns) -> bool:
    company_display = _report_clean_party_display_name(company_name)
    company_base = _strip_report_company_suffix(company_display)
    if not company_display or company_display == company_base:
        return False

    source_text = _own_party_source_text_for_report(txns)
    if not source_text:
        return False

    base_pattern = _report_flexible_phrase_pattern(company_base)
    if not base_pattern:
        return False
    return bool(
        re.search(
            rf"\b{base_pattern}\s+(?:SD|SDN|SND|SN|SB|S/B|SDN\s+B|SDN\s+BH|SDN\s+BHD|BHD|BERHAD)\b",
            source_text,
            flags=re.I,
        )
    )


def _own_party_group_name_for_report(txns, company_name: str = "") -> str:
    company_display = _report_clean_party_display_name(company_name)
    if company_display and company_display.upper() not in _REPORT_OWN_PARTY_FALLBACKS:
        if _own_party_suffix_visible_in_sources(company_display, txns):
            return company_display
        return _strip_report_company_suffix(company_display)

    for txn in txns or []:
        if not isinstance(txn, dict):
            continue
        party_type = str(txn.get("party_type") or "").upper()
        if party_type and not party_type.startswith("OWN"):
            continue
        raw_party_name = _report_clean_party_display_name(txn.get("party_name"))
        if raw_party_name and raw_party_name.upper() not in _REPORT_OWN_PARTY_FALLBACKS:
            return raw_party_name
    return "Own Party (Self)"


def _own_party_display_name_for_report(raw_party_name, company_name: str = "") -> str:
    fallbacks = {"UNKNOWN", "UNKNOWN PARTY", "COMPANY", "OWN PARTY", "OWN PARTY (SELF)", "SELF"}
    for value in (raw_party_name, company_name):
        name = re.sub(
            r"\s*\(\s*OWN[\s\-_]?PARTY\s*\)\s*",
            " ",
            str(value or ""),
            flags=re.I,
        )
        name = re.sub(r"\s+", " ", name).strip()
        if name and name.upper() not in fallbacks:
            return name
    return "Own Party (Self)"






def _report_transaction_amount(txn: dict) -> float:
    amount = safe_float(txn.get("amount", 0))
    if amount:
        return amount
    credit = safe_float(txn.get("credit", 0))
    debit = safe_float(txn.get("debit", 0))
    return credit if credit else debit


def _report_transaction_side(txn: dict) -> str:
    raw_type = str(txn.get("type") or txn.get("transaction_type") or "").upper()
    if "CR" in raw_type or "CREDIT" in raw_type:
        return "CREDIT"
    if "DR" in raw_type or "DEBIT" in raw_type:
        return "DEBIT"
    if safe_float(txn.get("credit", 0)) > 0:
        return "CREDIT"
    if safe_float(txn.get("debit", 0)) > 0:
        return "DEBIT"
    return "DEBIT" if _report_transaction_amount(txn) < 0 else "CREDIT"






def _counterparty_row_matches_related_party(cp: dict, related_party_name: str) -> bool:
    target = str(related_party_name or "").strip().upper()
    if not target:
        return False
    names = [
        cp.get("counterparty_name"),
        cp.get("counterparty"),
        *(cp.get("raw_names", []) or []),
    ]
    for name in names:
        candidate = str(name or "").strip().upper()
        if candidate and (candidate == target or target in candidate or candidate in target):
            return True
    return False


def _merge_counterparty_rows_for_related_party(cp_rows: List[dict], related_party_name: str) -> dict:
    matches = [
        cp for cp in (cp_rows or [])
        if isinstance(cp, dict) and _counterparty_row_matches_related_party(cp, related_party_name)
    ]
    return {
        "total_credits": round(sum(safe_float(cp.get("total_credits", cp.get("total_credit", 0))) for cp in matches), 2),
        "total_debits": round(sum(safe_float(cp.get("total_debits", cp.get("total_debit", 0))) for cp in matches), 2),
        "transaction_count": sum(
            int(safe_float(cp.get("transaction_count") or len(cp.get("transactions", []) or [])))
            for cp in matches
        ),
    }


def build_related_party_summary_rows_for_report(
    related_parties,
    own_related,
    cp_rows: List[dict] | None = None,
    company_name: str = "",
    manual_company_identity_override: bool = False,
    company_account_no: str = "",
) -> List[dict]:
    """Return related-party totals for Excel/HTML exports using C03-C04 rows first."""
    def _normalise_account(value) -> str:
        return re.sub(r"\D+", "", str(value or ""))

    manual_account_key = _normalise_account(company_account_no)
    manual_own_party_active = bool(
        manual_company_identity_override
        and str(company_name or "").strip()
        and manual_account_key
    )

    def _cp_row_has_manual_account(cp: dict) -> bool:
        if not manual_own_party_active or not isinstance(cp, dict):
            return False
        account_keys = [
            _normalise_account(value)
            for value in (cp.get("account_no"), cp.get("account_number"), cp.get("company_account_no"))
            if str(value or "").strip()
        ]
        txn_account_keys = [
            _normalise_account(value)
            for txn in cp.get("transactions", []) or []
            if isinstance(txn, dict)
            for value in (txn.get("account_no"), txn.get("account_number"), txn.get("company_account_no"))
            if str(value or "").strip()
        ]
        if account_keys or txn_account_keys:
            return manual_account_key in account_keys or manual_account_key in txn_account_keys
        return True

    fallback_cp_rows = [
        cp for cp in (cp_rows or [])
        if not _cp_row_has_manual_account(cp)
    ]

    groups = [
        group for group in build_own_related_party_groups_for_report(
            own_related,
            related_parties=related_parties,
            company_name=company_name,
            counterparty_rows=cp_rows,
            manual_company_identity_override=manual_company_identity_override,
            company_account_no=company_account_no,
        )
        if group.get("badge_type") == "RP"
    ]
    related_entries = [
        (name, relationship)
        for name, relationship in _report_related_party_entries(related_parties)
        if not _report_name_matches_own_party(name, company_name)
    ]

    def _matching_group(name: str):
        target = str(name or "").strip().upper()
        if not target:
            return None
        exact = next((g for g in groups if str(g.get("party_name", "")).upper() == target), None)
        if exact:
            return exact
        matches = [
            g for g in groups
            if target in str(g.get("party_name", "")).upper()
            or str(g.get("party_name", "")).upper() in target
        ]
        if not matches:
            return None
        return max(matches, key=lambda g: (len(str(g.get("party_name", ""))), str(g.get("party_name", "")).casefold()))

    rows = []
    seen = set()
    for name, relationship in related_entries:
        group = _matching_group(name)
        group_transactions = group.get("transactions", []) if group else []
        fallback = (
            {}
            if group_transactions
            else _merge_counterparty_rows_for_related_party(fallback_cp_rows, name)
        )
        use_group_totals = bool(group) and (
            bool(group_transactions)
            or not int(safe_float(fallback.get("transaction_count", 0)))
        )
        row = {
            "relationship": relationship,
            "name": name,
            "total_credits": group.get("credits", 0) if use_group_totals else fallback.get("total_credits", 0),
            "total_debits": group.get("debits", 0) if use_group_totals else fallback.get("total_debits", 0),
            "transaction_count": (
                len(group.get("transactions", []) or [])
                if use_group_totals
                else fallback.get("transaction_count", 0)
            ),
            "transactions": group.get("transactions", []) if use_group_totals else [],
        }
        rows.append(row)
        seen.add(name.upper())

    if not related_parties:
        for group in groups:
            group_name = str(group.get("party_name") or "").strip()
            if group_name and group_name.upper() not in seen:
                rows.append(
                    {
                        "relationship": "",
                        "name": group_name,
                        "total_credits": group.get("credits", 0),
                        "total_debits": group.get("debits", 0),
                        "transaction_count": len(group.get("transactions", []) or []),
                        "transactions": group.get("transactions", []) or [],
                    }
                )
                seen.add(group_name.upper())

    return rows


_REPORT_LEGAL_SUFFIX_TOKENS = {
    "SDN", "BHD", "BERHAD", "PLT", "LLP", "LTD", "LIMITED"
}
_REPORT_PERSON_CONNECTOR_TOKENS = {
    "BIN", "BINTI", "BINTE", "BT", "BTE", "B", "A/L", "A/P", "ANAK"
}


def _report_match_tokens(value) -> List[str]:
    text = re.sub(r"[^A-Z0-9/\s]", " ", str(value or "").upper())
    return [token for token in re.sub(r"\s+", " ", text).strip().split() if token]


def _report_party_core_tokens(name) -> List[str]:
    tokens = _report_match_tokens(name)
    core = [token for token in tokens if token not in _REPORT_LEGAL_SUFFIX_TOKENS]
    return core or tokens


def _report_party_alias_core_tokens(name) -> List[str]:
    tokens = _report_party_core_tokens(name)
    core = [token for token in tokens if token not in _REPORT_PERSON_CONNECTOR_TOKENS]
    return core or tokens


def _report_token_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter) and len(longer) - len(shorter) <= 2


def _report_tokens_ordered_match(needle_tokens: List[str], haystack_tokens: List[str]) -> bool:
    if len(needle_tokens) < 2 or len(haystack_tokens) < len(needle_tokens):
        return False

    search_start = 0
    for needle_token in needle_tokens:
        found_at = None
        for idx in range(search_start, len(haystack_tokens)):
            if _report_token_compatible(needle_token, haystack_tokens[idx]):
                found_at = idx
                break
        if found_at is None:
            return False
        search_start = found_at + 1
    return True








def _counterparty_row_matches_report_party(cp: dict, party_name: str) -> bool:
    return any(
        _report_candidate_contains_party_tokens(source, party_name)
        for source in _counterparty_row_report_match_sources(cp)
    )


def _counterparty_row_name_sources(cp: dict) -> List[str]:
    """Return the identity labels for a counterparty ledger row.

    OP/RP inheritance must mirror the main counterparty ledger, so matching is
    intentionally limited to row-level names and raw aliases.
    """
    sources = [
        cp.get("counterparty_name"),
        cp.get("counterparty"),
    ]
    raw_names = cp.get("raw_names", [])
    if isinstance(raw_names, (list, tuple, set)):
        sources.extend(raw_names)
    elif raw_names:
        sources.append(raw_names)

    return [str(source) for source in sources if source]


def _report_party_identity_names_match(left, right) -> bool:
    left_display = _report_party_display_name(left)
    right_display = _report_party_display_name(right)
    if not left_display or not right_display:
        return False
    if left_display.upper() == right_display.upper():
        return True

    left_canonical = _canonical_report_counterparty_display_name(left_display)
    right_canonical = _canonical_report_counterparty_display_name(right_display)
    if left_canonical.upper() == right_canonical.upper():
        return True

    left_tokens = _report_party_alias_core_tokens(left_canonical)
    right_tokens = _report_party_alias_core_tokens(right_canonical)
    if len(left_tokens) != len(right_tokens) or not left_tokens:
        return False
    return all(
        _report_token_compatible(left_token, right_token)
        for left_token, right_token in zip(left_tokens, right_tokens)
    )
















_PARTY_GHOST_STOPWORDS = {
    "TRANSFER", "PAYMENT", "IBG", "IB2G", "IBFT", "IBK", "CR", "DR", "CREDIT", "DEBIT",
    "TO", "FR", "FROM", "A/C", "C/A", "ACCOUNT", "ACCT", "INTER", "BANK", "BANKING", "INTO",
    "ONLINE", "DUITNOW", "DUIT", "NOW", "FPX", "RENTAS", "REMITTANCE", "ELECTRONIC",
    "AUTOPAY", "INSTANT", "FAST", "OUTWARD", "INWARD", "OUTW", "INW",
    "OUT", "IN", "ADVICE", "TRF", "BLKTRF", "NBPS", "TR", "PYMT", "PAY",
    "THE", "AND", "OF", "FOR", "WITH",
    "SA", "CA", "CCARD", "CARD",
    "CHQ", "CHEQUE", "CASH", "DEPOSIT", "WITHDRAWAL", "HSE", "HOUSE",
    "CLRG", "CDM", "2D", "LOCAL", "GIR", "GIRO",
    "HLB", "MBB", "RHB", "ABB", "PBB", "BIMB", "AMB", "AMBANK", "PBE",
    "CIMB", "OCBC", "UOB", "BSN",
    "PMT", "SLRY",
}
_PARTY_CHEQUE_NOISE = {
    "HSE CHQ DEPOSIT", "CDM CASH DEPOSIT", "2D LOCAL CHQ", "CASH CHQ DR",
    "HOUSE CHQ DR", "CLRG CHQ DR", "HSE CHQ", "CHEQUE DEPOSIT", "CHQ DEPOSIT",
}


def _is_ghost_party_bucket(name) -> bool:
    """Return True when a top-party label is only parser/payment-rail noise."""
    if not name:
        return True
    normalised = re.sub(r"[.,]", "", str(name).upper())
    normalised = re.sub(r"\b(SDN|BHD|& CO|\(M\)|PTY|LTD)\b", "", normalised)
    normalised = re.sub(r"\s+", " ", normalised).strip()
    if not normalised:
        return True
    if normalised in _PARTY_CHEQUE_NOISE:
        return True
    tokens = [t for t in re.split(r"[\s/\-]+", normalised) if t]
    real_tokens = [
        t for t in tokens
        if len(t) >= 3 and t not in _PARTY_GHOST_STOPWORDS and re.search(r"[A-Z]", t)
    ]
    return len(real_tokens) == 0


def _normalize_party_for_report(party: dict, is_payer: bool) -> dict:
    if not isinstance(party, dict):
        return {}
    amount = party.get("total_amount")
    if amount is None:
        amount = party.get("total_credits") if is_payer else party.get("total_debits")
    if amount is None:
        amount = party.get("amount", 0)
    return {
        "rank": party.get("rank", ""),
        "party_name": party.get("party_name") or party.get("name") or "",
        "total_amount": safe_float(amount),
        "transaction_count": int(safe_float(party.get("transaction_count") or party.get("txn_count") or 0)),
        "is_related_party": bool(party.get("is_related_party", False)),
        "monthly_breakdown": party.get("monthly_breakdown") or [],
    }




def build_top_party_view_from_counterparty_ledger(
    cp_ledger: dict,
    *,
    limit: int = 10,
    company_name: str = "",
    related_parties=None,
    own_related=None,
) -> dict:
    """Return the shared Top Parties view used by Streamlit, HTML, and Excel."""
    counterparty_rows = build_report_counterparty_ledger_rows(
        cp_ledger,
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )
    top_parties = _top_parties_from_counterparty_rows(
        counterparty_rows,
        limit=None,
        company_name=company_name,
    )
    return prepare_top_parties_for_report(
        top_parties,
        limit=limit,
        company_name=company_name,
    )












def _build_reconciliation_lookup(parsing_metadata: dict) -> dict:
    checks = (
        parsing_metadata.get("account_month_checks", [])
        if isinstance(parsing_metadata, dict)
        else []
    )
    lookup = {}
    if not isinstance(checks, list):
        return lookup

    for chk in checks:
        if not isinstance(chk, dict):
            continue
        month_key = str(chk.get("month", "") or "")
        account_keys = {
            str(chk.get("account_number", "") or ""),
            str(chk.get("account_no", "") or ""),
        }
        for account_key in account_keys:
            lookup[(month_key, account_key)] = chk
        if month_key and not any(account_keys):
            lookup[(month_key, "")] = chk
    return lookup


def _reconciliation_check_for_month_row(row: dict, lookup: dict) -> dict | None:
    if not isinstance(row, dict) or not isinstance(lookup, dict):
        return None
    month_key = str(row.get("month", "") or "")
    account_key = str(row.get("account_number", row.get("account_no", "")) or "")
    return lookup.get((month_key, account_key)) or lookup.get((month_key, ""))


def _effective_reconciliation_values(row: dict, lookup: dict | None = None) -> dict:
    chk = _reconciliation_check_for_month_row(row, lookup or {})
    source = chk if chk is not None else (row if isinstance(row, dict) else {})

    if "passed" in source:
        passed = bool(source.get("passed"))
    else:
        status_text = str(source.get("reconciliation_status") or "").upper()
        if status_text:
            passed = status_text == "PASS"
        else:
            passed = abs(safe_float(source.get("reconciliation_delta", 0))) <= 1.00

    status = "PASS" if passed else "FAIL"
    note = "" if passed else str(
        source.get("data_quality_note")
        or source.get("note")
        or source.get("remarks")
        or (row.get("data_quality_note") if isinstance(row, dict) else "")
        or ""
    )

    return {
        "status": status,
        "delta": safe_float(source.get("reconciliation_delta", 0)),
        "gaps": int(safe_float(source.get("extraction_gaps", source.get("extraction_gaps_count", 0)))),
        "missing_debits": safe_float(source.get("missing_debit_amount", 0)),
        "missing_credits": safe_float(source.get("missing_credit_amount", 0)),
        "note": note,
    }




def standardize_monthly_summary_balance_chain(monthly_summary: List[dict]) -> List[dict]:
    """Use opening + net movement as the standardized closing balance chain."""
    rows = [dict(row) for row in (monthly_summary or []) if isinstance(row, dict)]
    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        account = str(row.get("account_no", row.get("account_number", "")) or "__single_account__")
        grouped.setdefault(account, []).append(row)

    for account_rows in grouped.values():
        account_rows.sort(key=lambda row: str(row.get("month", "") or ""))
        previous_ending = None

        for row in account_rows:
            total_credit = safe_float(row.get("total_credit", row.get("gross_credits", 0)))
            total_debit = safe_float(row.get("total_debit", row.get("gross_debits", 0)))
            net_change = row.get("net_change")
            if net_change is None:
                net_change = round(total_credit - total_debit, 2)
                row["net_change"] = net_change

            opening = row.get("opening_balance")
            if previous_ending is not None:
                opening = previous_ending
                row["opening_balance"] = opening
            elif opening is None or str(opening).strip() == "":
                raw_ending = row.get("ending_balance", row.get("closing_balance"))
                if raw_ending is not None and str(raw_ending).strip() != "":
                    opening = round(safe_float(raw_ending) - safe_float(net_change), 2)
                    row["opening_balance"] = opening

            if opening is None or str(opening).strip() == "":
                raw_ending = row.get("ending_balance", row.get("closing_balance"))
                if raw_ending is not None and str(raw_ending).strip() != "":
                    previous_ending = round(safe_float(raw_ending), 2)
                continue

            expected_ending = round(safe_float(opening) + safe_float(net_change), 2)
            raw_ending = row.get("ending_balance", row.get("closing_balance"))
            if raw_ending is None or abs(expected_ending - safe_float(raw_ending)) > 0.01:
                row["raw_ending_balance"] = raw_ending
            row["ending_balance"] = expected_ending
            row["closing_balance"] = expected_ending
            previous_ending = expected_ending

    return rows









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














def _pdf_finding_is_benign_for_export(finding: dict) -> bool:
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




def _top_counterparty_excel_rows(cp_ledger: dict, amount_column: str, count_column: str) -> List[dict]:
    counterparties = cp_ledger.get("counterparties", []) if isinstance(cp_ledger, dict) else []
    rows = []
    for cp in counterparties or []:
        if not isinstance(cp, dict):
            continue
        amount = safe_float(cp.get(amount_column, 0))
        if amount <= 0:
            continue
        rows.append(
            {
                "Counterparty": cp.get("counterparty_name", cp.get("counterparty", "")),
                "Total Txn": int(safe_float(cp.get(count_column, cp.get("transaction_count", 0)))),
                "Total Amnt of Txn": f"RM {amount:,.2f}",
                "_sort_amount": amount,
            }
        )

    rows.sort(key=lambda row: row["_sort_amount"], reverse=True)
    return [
        {key: value for key, value in row.items() if key != "_sort_amount"}
        for row in rows[:10]
    ]









def generate_html_report_from_data(transactions: List[dict], monthly_summary: List[dict], 
                                   transaction_analysis: dict, high_value_threshold: float) -> str:
    """Generate interactive HTML report from the shared export payload."""
    report_data = build_shared_report_data(
        transactions,
        monthly_summary,
        transaction_analysis,
        high_value_threshold,
    )
    return generate_interactive_html(report_data)

# ============================================================
# HTML REPORT GENERATION FUNCTIONS (ADDED)
# ============================================================

def fmt_basic_unused(val, decimals=2):
    """Format number with commas"""
    if val is None:
        return "0.00"
    return f"{val:,.{decimals}f}"

def normalize_observations_basic_unused(obs):
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


st.markdown('<div id="overview-section" class="section-anchor"></div>', unsafe_allow_html=True)
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

if "related_party_candidates_dismissed" not in st.session_state:
    st.session_state.related_party_candidates_dismissed = set()

if "related_party_manager_expanded" not in st.session_state:
    st.session_state.related_party_manager_expanded = True

if "counterparty_name_overrides" not in st.session_state:
    st.session_state.counterparty_name_overrides = {}

if "imported_report_data" not in st.session_state:
    st.session_state.imported_report_data = None

if "imported_report_validation" not in st.session_state:
    st.session_state.imported_report_validation = {}

if "imported_report_upload_sha256" not in st.session_state:
    st.session_state.imported_report_upload_sha256 = ""

if "imported_report_acknowledged" not in st.session_state:
    st.session_state.imported_report_acknowledged = False


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





















# -----------------------------
# Counterparty Ledger Functions
# -----------------------------
UNKNOWN_COUNTERPARTY_VALUES = {"", "UNKNOWN", "N/A", "NA", "NONE", "NULL", "-"}
IBG_CREDIT_DESCRIPTION_RE = re.compile(r"^\s*IBG\s+CREDIT\b", re.I)
COUNTERPARTY_NAME_FIELDS = (
    "counterparty_name_clean",
    "counterparty_name",
    "counterparty",
    "party_name",
    "counterparty_name_raw",
    "party",
    "merchant",
    "merchant_name",
    "recipient",
    "beneficiary",
    "payer",
    "payee",
)


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




def _counterparty_override_candidates(raw_name: str, clean_name: str = "") -> List[str]:
    candidates = [
        normalize_counterparty_value(raw_name),
        normalize_counterparty_value(clean_name),
    ]
    try:
        candidates.append(normalize_counterparty_value(clean_counterparty_name(raw_name)))
    except Exception:
        pass
    seen = set()
    return [name for name in candidates if name and not (name in seen or seen.add(name))]


def _apply_counterparty_overrides(raw_name: str, clean_name: str) -> str:
    overrides = st.session_state.get("counterparty_name_overrides", {}) or {}
    if not overrides:
        return clean_name

    for candidate in _counterparty_override_candidates(raw_name, clean_name):
        override = normalize_counterparty_value(overrides.get(candidate))
        if override:
            return override
    return clean_name










def build_counterparty_ledger_from_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build counterparty ledger summary using parser-extracted counterparty data.
    """
    if df.empty:
        return pd.DataFrame()
    
    working_df = df.copy()
    if "counterparty_name" not in working_df.columns or "_raw_counterparty" not in working_df.columns:
        working_df = prepare_counterparty_dataframe(working_df)
    party_tables = build_transactions_by_party(working_df)
    
    # Group by counterparty
    summary_data = []
    for party in party_tables:
        table = party.get("table")
        if isinstance(table, pd.DataFrame) and "amount" in table.columns:
            signed_amounts = table["amount"].apply(safe_float)
            credit_count = int((signed_amounts > 0).sum())
            debit_count = int((signed_amounts < 0).sum())
        else:
            credit_count = 0
            debit_count = 0

        total_credits = round(float(party.get("total_credit", 0) or 0), 2)
        total_debits = round(float(party.get("total_debit", 0) or 0), 2)
        net_position = total_credits - total_debits

        summary_data.append({
            'counterparty_name': party.get("party", "UNKNOWN"),
            'transaction_count': int(party.get("count", credit_count + debit_count) or 0),
            'credit_count': credit_count,
            'debit_count': debit_count,
            'total_credits': total_credits,
            'total_debits': total_debits,
            'net_position': round(net_position, 2)
        })
    
    summary_df = pd.DataFrame(summary_data)
    if summary_df.empty:
        return summary_df
    
    summary_df['_sort_counterparty'] = summary_df['counterparty_name'].astype(str).str.casefold()
    summary_df = summary_df.sort_values('_sort_counterparty', ascending=True)
    summary_df = summary_df.drop('_sort_counterparty', axis=1).reset_index(drop=True)
    
    return summary_df





def _counterparty_rows_for_related_party_manager(
    cp_ledger: dict = None,
    shared_report_data: dict = None,
) -> List[dict]:
    rows: List[dict] = []

    def add_rows(candidate_rows) -> None:
        if isinstance(candidate_rows, list):
            rows.extend(row for row in candidate_rows if isinstance(row, dict))

    if isinstance(shared_report_data, dict):
        add_rows(shared_report_data.get("report_counterparty_rows"))
        add_rows(shared_report_data.get("counterparty_ledger_rows"))
        shared_ledger = shared_report_data.get("counterparty_ledger")
        if isinstance(shared_ledger, dict):
            add_rows(shared_ledger.get("counterparties"))

    if isinstance(cp_ledger, dict):
        add_rows(cp_ledger.get("counterparties"))

    return rows











# -----------------------------
# Pattern Analysis Functions
# -----------------------------








# -----------------------------
# Statutory Payment Detection Functions
# -----------------------------


















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
    display_df = df[display_cols].copy()
    for money_col in ("debit", "credit", "balance"):
        if money_col in display_df.columns:
            display_df[money_col] = pd.to_numeric(display_df[money_col], errors="coerce").fillna(0.0)

    st.dataframe(
        display_df,
        use_container_width=True,
        column_config={
            "date": "Transaction Date",
            "description": "Description",
            "debit": st.column_config.NumberColumn("Debit (RM)", format="%.2f"),
            "credit": st.column_config.NumberColumn("Credit (RM)", format="%.2f"),
            "balance": st.column_config.NumberColumn("Running Balance", format="%.2f"),
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
    st.session_state.counterparty_name_overrides = {}


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


AMBANK_COMPANY_NAME_NOISE_RE = re.compile(
    r"^\s*\d{1,2}\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b|"
    r"\b(?:INWARD\s+IBG|IBG|GIRO|DUITNOW|TRANSFER|TRF|CREDIT|DEBIT|PAYMENT|"
    r"AUTO|CHQ|CHEQUE|JOMPAY|FPX|SALARY|FEE|CHARGE)\b",
    re.IGNORECASE,
)


def _clean_ambank_summary_company_name(value) -> Optional[str]:
    raw = normalize_text(value)
    if not raw:
        return None

    cleaned = clean_ambank_company_name(raw)
    if not cleaned:
        return None

    raw_upper = raw.upper()
    cleaned_upper = cleaned.upper()
    if AMBANK_COMPANY_NAME_NOISE_RE.search(raw_upper):
        return None
    if AMBANK_COMPANY_NAME_NOISE_RE.search(cleaned_upper):
        return None

    return cleaned


def _apply_ambank_statement_totals_to_monthly_summary(rows: List[dict], statement_totals: List[dict]) -> List[dict]:
    if not rows or not statement_totals:
        return rows

    totals_by_month: Dict[str, List[dict]] = {}
    for total in statement_totals:
        if not isinstance(total, dict):
            continue
        month = normalize_text(total.get("statement_month"))
        if month:
            totals_by_month.setdefault(month, []).append(total)

    out: List[dict] = []
    for row in rows:
        row_out = dict(row)
        month = normalize_text(row_out.get("month"))
        refs = totals_by_month.get(month, [])
        if not refs:
            out.append(row_out)
            continue

        source_files = normalize_text(row_out.get("source_files"))
        ref = None
        if source_files:
            for candidate in refs:
                source_file = normalize_text(candidate.get("source_file"))
                if source_file and source_file in source_files:
                    ref = candidate
                    break
        if ref is None and len(refs) == 1:
            ref = refs[0]
        if ref is None:
            out.append(row_out)
            continue

        for field in ("opening_balance", "ending_balance", "total_debit", "total_credit"):
            value = ref.get(field)
            if value is not None:
                row_out[field] = round(safe_float(value), 2)
        if row_out.get("total_debit") is not None and row_out.get("total_credit") is not None:
            row_out["net_change"] = round(
                safe_float(row_out.get("total_credit")) - safe_float(row_out.get("total_debit")),
                2,
            )
        out.append(row_out)

    return out


def _ambank_company_candidate_description_hits(rows: pd.DataFrame, candidate: str) -> int:
    if not candidate or "description" not in rows.columns:
        return 0
    candidate_upper = normalize_text(candidate).upper()
    hits = 0
    for desc in rows["description"].dropna().astype(str).tolist():
        if candidate_upper and candidate_upper in normalize_text(desc).upper():
            hits += 1
    return hits


def _choose_ambank_account_company(rows: pd.DataFrame) -> Optional[str]:
    if rows.empty or "company_name" not in rows.columns:
        return None

    scored = []
    for value in rows["company_name"].dropna().astype(str).tolist():
        candidate = _clean_ambank_summary_company_name(value)
        if not candidate:
            continue
        candidate_count = sum(
            1
            for other in rows["company_name"].dropna().astype(str).tolist()
            if _clean_ambank_summary_company_name(other) == candidate
        )
        scored.append(
            (
                _ambank_company_candidate_description_hits(rows, candidate),
                -candidate_count,
                -len(candidate),
                candidate,
            )
        )

    if not scored:
        return None
    return sorted(set(scored))[0][3]


def calculate_monthly_summary(
    transactions: List[dict],
    ambank_statement_totals: Optional[List[dict]] = None,
) -> List[dict]:
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
    is_ambank_summary = False
    if "bank" in df.columns:
        is_ambank_summary = any(
            normalize_text(value).upper() in {"AMBANK", "AM BANK"}
            for value in df["bank"].dropna().tolist()
        )

    fallback_company_name = None
    account_company_names = {}
    if is_ambank_summary and "company_name" in df.columns:
        fallback_company_name = _choose_ambank_account_company(df)
        if "account_no" in df.columns:
            for account_no_value, account_group in df.groupby("account_no", dropna=True):
                account_key = normalize_text(account_no_value)
                candidate = _choose_ambank_account_company(account_group)
                if account_key and candidate:
                    account_company_names[account_key] = candidate
        if not fallback_company_name:
            for value in df["company_name"].dropna().tolist():
                candidate = _clean_ambank_summary_company_name(value)
                if candidate:
                    fallback_company_name = candidate
                    break

    def ambank_group_company_name(group_rows: pd.DataFrame, values: List[str]) -> Optional[str]:
        account_keys = [
            normalize_text(x)
            for x in group_rows.get("account_no", pd.Series([], dtype=object)).dropna().astype(str).unique().tolist()
            if normalize_text(x)
        ]
        for account_key in account_keys:
            if account_key in account_company_names:
                return account_company_names[account_key]

        candidate = _choose_ambank_account_company(group_rows)
        if candidate:
            return candidate

        for value in values:
            candidate = _clean_ambank_summary_company_name(value)
            if candidate:
                return candidate
        return fallback_company_name

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
        if is_ambank_summary:
            company_name = ambank_group_company_name(group_sorted, company_vals)
        else:
            company_name = clean_extracted_company_name(company_vals[0]) if company_vals else None

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

        opening = r.get("opening_balance")
        net_change = r.get("net_change")
        if opening is not None and net_change is not None:
            expected_ending = round(safe_float(opening) + safe_float(net_change), 2)
            raw_ending = r.get("ending_balance")
            if raw_ending is None or abs(expected_ending - safe_float(raw_ending)) > 0.01:
                r["raw_ending_balance"] = raw_ending
                r["ending_balance"] = expected_ending

        if r.get("ending_balance") is not None:
            prev_end = safe_float(r.get("ending_balance"))

    standardized = standardize_monthly_summary_balance_chain(monthly_summary_sorted)
    if is_ambank_summary:
        standardized = _apply_ambank_statement_totals_to_monthly_summary(
            standardized,
            ambank_statement_totals or [],
        )
    return standardized


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


_bind_extracted_module_globals()

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
progress_panel = None
progress_total_steps = 1
progress_export_start_step = 0
progress_final_message = ""
progress_final_variant = "success"

if uploaded_files and st.session_state.status == "running":
    progress_panel = st.empty()

    total_files = len(uploaded_files)
    total_steps = total_files + 4
    progress_total_steps = total_steps
    progress_export_start_step = total_files + 3
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
            if bank_choice == "Ambank":
                ambank_company_name = None
                try:
                    with bytes_to_pdfplumber(pdf_bytes) as meta_pdf:
                        ambank_company_name = extract_ambank_company_name(meta_pdf, max_pages=2)
                except Exception:
                    pass
                company_name = ambank_company_name or _clean_ambank_summary_company_name(company_name)

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
            if company_name:
                company_name = clean_extracted_company_name(company_name)

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
            f"Extracted {total_extracted} transactions. Finalizing report data.",
            total_files + 1,
            variant="warning",
        )
        st.warning(f"⚠️ Completed with {len(processing_errors)} error(s). Check the errors above.")
    else:
        st.session_state.status = "completed"
        update_processing_progress("Finalizing extracted transactions.", total_files + 1)
    
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
        progress_final_variant = "warning"
        progress_final_message = (
            f"Reports ready with {len(processing_errors)} processing error(s). "
            f"Extracted {final_transaction_count} transactions from {total_files} file(s)."
        )
        update_processing_progress(
            f"Preparing report sections and downloads after {len(processing_errors)} error(s). "
            f"Extracted {final_transaction_count} transactions.",
            total_files + 2,
            variant="warning",
        )
    else:
        progress_final_variant = "success"
        progress_final_message = (
            f"All reports are ready. Processed {total_files} file(s) and "
            f"{final_transaction_count} transactions."
        )
        update_processing_progress(
            f"Preparing report sections and downloads for {final_transaction_count} transactions.",
            total_files + 2,
        )


# ---------------------------------------------------
# DISPLAY
# ---------------------------------------------------
analysis_results = st.session_state.get("integrity_analysis_results", {})

if st.session_state.results:
    high_value_threshold = get_high_value_threshold()
    
    # Convert results to DataFrame
    df = pd.DataFrame(st.session_state.results) if st.session_state.results else pd.DataFrame()
    monthly_summary = []
    transaction_analysis_report = {}
    serialized_monthly_summary = []
    serialized_transaction_analysis = {}
    shared_report_data = {}
    
    if not df.empty:
        # Run fraud/pattern checks
        df = run_fraud_checks(df, high_value_threshold)
        
        # Display transaction pattern overview
        render_transaction_overview(df, high_value_threshold)

        # Display Extracted Transaction section
        st.markdown("---")
        render_extracted_transaction_section(df)
        
        # Display Counterparty Ledger Table
        # Display Counterparty Ledger Table
        st.markdown("---")
        counterparty_report_context = render_counterparty_ledger_table(df) or {}

        transaction_analysis_report = parse_top_parties_and_high_value(
            st.session_state.results,
            top_n=10,
            high_value_threshold=high_value_threshold,
        )
        monthly_summary_raw = calculate_monthly_summary(
            st.session_state.results,
            ambank_statement_totals=st.session_state.get("ambank_statement_totals", []),
        )
        monthly_summary = present_monthly_summary_standard(monthly_summary_raw)

        # Build serialized inputs and shared_report_data EARLY so the
        # Related Party Manager and download buttons all use the same object.
        serialized_transactions = make_json_serializable(
            counterparty_report_context.get("prepared_transactions")
            or st.session_state.results
        )
        serialized_monthly_summary = make_json_serializable(monthly_summary)
        serialized_transaction_analysis = make_json_serializable(transaction_analysis_report)
        shared_report_data = build_shared_report_data(
            serialized_transactions,
            serialized_monthly_summary,
            serialized_transaction_analysis,
            high_value_threshold,
        ) if serialized_transactions else {}

        def _attach_counterparty_report_context(report_payload: dict) -> dict:
            if not isinstance(report_payload, dict) or not counterparty_report_context:
                return report_payload

            cp_ledger_override = counterparty_report_context.get("counterparty_ledger")
            if isinstance(cp_ledger_override, dict) and cp_ledger_override.get("counterparties"):
                report_payload["counterparty_ledger"] = cp_ledger_override

            cp_rows = copy_report_counterparty_rows(
                counterparty_report_context.get("report_counterparty_rows")
            )
            if cp_rows:
                report_payload["report_counterparty_rows"] = cp_rows
                report_payload["counterparty_ledger_rows"] = cp_rows
                company_name_for_top = (
                    report_payload.get("report_info", {}).get("company_name")
                    or counterparty_report_context.get("company_name")
                    or ""
                )
                report_payload["top_parties"] = _top_parties_from_counterparty_rows(
                    cp_rows,
                    limit=None,
                    company_name=company_name_for_top,
                )
            return report_payload

        shared_report_data = _attach_counterparty_report_context(shared_report_data)

        # Related Party Manager — runs after shared_report_data is ready
        st.markdown("---")
        rp_changed = render_related_party_manager(
            cp_ledger=shared_report_data.get("counterparty_ledger"),
            shared_report_data=shared_report_data,
        )
        # Rebuild if user made changes so downloads pick up the new RP list
        if rp_changed:
            shared_report_data = build_shared_report_data(
                serialized_transactions,
                serialized_monthly_summary,
                serialized_transaction_analysis,
                high_value_threshold,
            )
            shared_report_data = _attach_counterparty_report_context(shared_report_data)

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
    col1, col2, col3, col4 = st.columns(4)
    export_errors: List[str] = []
    if progress_panel is not None:
        render_processing_progress(
            progress_panel,
            status="Preparing JSON, HTML, and Excel download files.",
            progress=progress_export_start_step / progress_total_steps,
            variant="warning" if progress_final_variant == "warning" else "active",
        )

    df_download = df.copy() if not df.empty else pd.DataFrame([])

    # Convert transactions to JSON-serializable format
    if not df_download.empty:
        json_records = df_download.to_dict(orient="records")
        json_records = make_json_serializable(json_records)
    else:
        json_records = []

    serializable_monthly_summary = make_json_serializable(monthly_summary)
    serializable_transaction_analysis = make_json_serializable(transaction_analysis_report)
    editable_report_data = prepare_report_for_export(shared_report_data)
    editable_report_info = (
        editable_report_data.get("report_info", {})
        if isinstance(editable_report_data.get("report_info"), dict)
        else {}
    )
    safe_company_name = safe_report_filename(
        editable_report_info.get("company_name")
        or st.session_state.get("company_name_override")
        or "report"
    )

    with col1:
        st.download_button(
            "Download Editable Report JSON",
            json.dumps(
                make_json_serializable(editable_report_data),
                indent=2,
                ensure_ascii=False,
            ),
            file_name=f"{safe_company_name}_editable_report.json",
            mime="application/json",
            use_container_width=True,
        )

    with col2:
        try:
            html_content = generate_interactive_html(editable_report_data)
            st.download_button(
                "Download Interactive HTML",
                html_content.encode("utf-8"),
                file_name=f"{safe_company_name}_statement_report.html",
                mime="text/html; charset=utf-8",
                use_container_width=True,
            )
        except Exception as e:
            export_errors.append("HTML")
            st.error(f"Failed to generate HTML report: {e}")

    with col3:
        output = generate_excel_report(
            shared_report_data,
            monthly_summary=serialized_monthly_summary, 
            transaction_analysis=serialized_transaction_analysis
        )

        st.download_button(
            "📊 Download Full Report (XLSX)",
            output.getvalue(),
            "full_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

    with col4:
        st.download_button(
            "Download Raw Transaction JSON",
            json.dumps(json_records, indent=2, ensure_ascii=False),
            file_name=f"{safe_company_name}_raw_transactions.json",
            mime="application/json",
            use_container_width=True,
        )

    if progress_panel is not None:
        final_variant = "warning" if export_errors or progress_final_variant == "warning" else "success"
        final_message = progress_final_message or "All reports are ready."
        if export_errors:
            final_message = (
                f"Report display finished, but {', '.join(export_errors)} export "
                f"{'was' if len(export_errors) == 1 else 'were'} not prepared."
            )
        render_processing_progress(
            progress_panel,
            status=final_message,
            progress=1.0,
            variant=final_variant,
        )

else:
    if (
        uploaded_files
        and st.session_state.status == "idle"
        and not st.session_state.high_value_threshold_error
        and not st.session_state.bank_choice_error
        and not st.session_state.pdf_upload_error
    ):
        st.warning("⚠️ No transactions found — click **Start Processing**.")
        
render_imported_report_json_section()

st.markdown("</div>", unsafe_allow_html=True)
