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
from cimb import parse_transactions_cimb
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
        min-height: 3.35rem !important;
        align-items: center !important;
        padding-left: 0.95rem !important;
        border-radius: 8px !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] div {
        font-size: 18px !important;
        font-weight: 600 !important;
        line-height: 24px !important;
    }

    div[data-testid="stSelectbox"] div[data-baseweb="select"] span,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] input {
        font-size: 18px !important;
        font-weight: 600 !important;
        line-height: 24px !important;
    }

    div[data-testid="stSelectbox"] [data-baseweb="tag"] {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }

    div[data-testid="stSelectbox"] svg {
        width: 1rem;
        height: 1rem;
    }

    div[role="listbox"] {
        padding: 0.4rem !important;
        border: 1px solid #374151 !important;
        border-radius: 10px !important;
        background: #0B0F16 !important;
    }

    div[role="option"] {
        min-height: 2.9rem !important;
        padding: 0.7rem 0.85rem !important;
        border-radius: 7px !important;
        color: #E5E7EB !important;
        font-size: 1.05rem !important;
        font-weight: 700 !important;
        display: flex !important;
        align-items: center !important;
    }

    div[role="option"]:hover,
    div[aria-selected="true"] {
        background: #2D3340 !important;
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

    div[data-testid="stExpander"] {
        border: 1px solid #374151;
        border-radius: 8px;
        background: #0B0F16;
        overflow: hidden;
        margin-bottom: 1rem;
    }

    div[data-testid="stExpander"] details summary {
        min-height: 3.2rem;
        padding: 0.85rem 1rem;
        font-weight: 700;
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

# ✅ password + company name tracking
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


# -----------------------------
# Fraud/Pattern Analysis Functions
# -----------------------------
def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().upper())


def is_round_number(amount: float, round_thresholds: List[float] = None, tolerance: float = 0.01) -> bool:
    """
    Check if amount is a round number (multiple of significant thresholds like 10,000, 50,000, 100,000, etc.)
    
    Args:
        amount: The transaction amount to check
        round_thresholds: List of thresholds to check (default: [10000, 50000, 100000, 500000, 1000000])
        tolerance: Tolerance for floating point precision (default: 0.01)
    
    Returns:
        True if amount is a multiple of any threshold, False otherwise
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
                # Convert to Timestamp to access .days attribute
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
    """
    Detect EPF / KWSP contributions from transaction descriptions.
    Returns: (count, total_amount)
    """
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
    """
    Detect SOCSO / PERKESO / EIS contributions from transaction descriptions.
    Returns: (count, total_amount)
    """
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
    """
    Detect LHDN / tax payments from transaction descriptions.
    Returns: (count, total_amount)
    """
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
    """
    Detect HRDF / PSMB levy payments from transaction descriptions.
    Returns: (count, total_amount)
    """
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
    
    # High value detection
    df["is_high_value"] = df["credit"].apply(lambda x: safe_float(x) >= high_value_threshold if x else False)
    
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
    st.markdown("### Pattern Details")
    
    # Duplicate transactions
    if "is_duplicate_transaction" in df.columns:
        duplicate_hits = df[df["is_duplicate_transaction"] == True].copy()
        if not duplicate_hits.empty:
            duplicate_hits["amount"] = duplicate_hits.apply(
                lambda row: f"+{row['credit']:,.2f}" if row.get('credit', 0) > 0 else f"-{row['debit']:,.2f}",
                axis=1
            )
            with st.expander(f"Repeated transaction ({len(duplicate_hits)})"):
                st.caption("The following entries share the same date, description, and amount.")
                display_cols = [c for c in ["date", "description", "amount", "balance"] if c in duplicate_hits.columns]
                st.dataframe(duplicate_hits[display_cols], use_container_width=True)
    
    # Rapid repeat transactions
    if "is_rapid_repeat_transaction" in df.columns:
        rapid_repeat_hits = df[df["is_rapid_repeat_transaction"] == True].copy()
        if not rapid_repeat_hits.empty:
            with st.expander(f"High freq transactions ({len(rapid_repeat_hits)})"):
                st.caption("Transactions repeated to the same merchant within a short time window.")
                display_cols = [c for c in ["date", "description", "credit", "debit", "repeat_days_in_window"] if c in rapid_repeat_hits.columns]
                st.dataframe(rapid_repeat_hits[display_cols], use_container_width=True)
    
    # Round number transactions
    if "is_round" in df.columns:
        round_hits = df[df["is_round"] == True].copy()
        if not round_hits.empty:
            round_hits["amount"] = round_hits.apply(
                lambda row: f"+{row['credit']:,.2f}" if row.get('credit', 0) > 0 else f"-{row['debit']:,.2f}",
                axis=1
            )
            with st.expander(f"Round-number transactions ({len(round_hits)})"):
                st.caption("Transactions with round numbers (multiple of 10,000).")
                display_cols = [c for c in ["date", "description", "amount", "source_file"] if c in round_hits.columns]
                st.dataframe(round_hits[display_cols], use_container_width=True)
    
    # High value transactions
    if "is_high_value" in df.columns:
        high_hits = df[df["is_high_value"] == True].copy()
        if not high_hits.empty:
            with st.expander(f"High-value transactions (>= RM{high_value_threshold:,.2f}) ({len(high_hits)})"):
                display_cols = [c for c in ["date", "description", "credit", "balance"] if c in high_hits.columns]
                st.dataframe(high_hits[display_cols], use_container_width=True)
    
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
            with st.expander(f"🏦 EPF / KWSP Contributions ({epf_count} payments, RM {epf_total:,.2f})"):
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
            with st.expander(f"🛡️ SOCSO / PERKESO Contributions ({socso_count} payments, RM {socso_total:,.2f})"):
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
            with st.expander(f"📋 LHDN / Tax Payments ({lhdn_count} payments, RM {lhdn_total:,.2f})"):
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
            with st.expander(f"🎓 HRDF / PSMB Levies ({hrdf_count} payments, RM {hrdf_total:,.2f})"):
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
    
    # Display summary metrics
    item_map = dict(pattern_summary.get("items", []))
    statutory_items = pattern_summary.get("statutory_items", [])
    
    render_metric_cards(
        [
            ("Transactions", item_map.get("Total Transactions", 0)),
            ("High-Value Flags", item_map.get("High-Value Flags", 0)),
            ("Round-Number", item_map.get("Round-Number", 0)),
            ("Repeated", item_map.get("Repeated", 0)),
        ],
        [("High Frequency Flags", item_map.get("High Frequency Flags", 0))],
        statutory_metrics=statutory_items if statutory_items else None,
    )
    
    # Render detailed expandable sections
    render_pattern_details(analysis_df, high_value_threshold)


def clear_processing_outputs() -> None:
    st.session_state.results = []
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

    st.session_state.high_value_threshold_error = threshold_error or ""
    st.session_state.bank_choice_error = bank_choice_error or ""

    if threshold_error or bank_choice_error:
        validation_messages = [msg for msg in (bank_choice_error, threshold_error) if msg]
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


if "bank_choice" not in st.session_state:
    st.session_state.bank_choice = None

bank_choice = st.selectbox(
    "Select Bank Format",
    list(PARSERS.keys()),
    index=None,
    key="bank_choice",
    placeholder="Choose the bank for the uploaded statement(s)",
    on_change=clear_bank_choice_error,
)

uploaded_files = st.file_uploader(
    "Upload PDF files",
    type=["pdf"],
    accept_multiple_files=True,
    key=f"pdf_upload_{st.session_state.upload_widget_reset_id}",
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
        placeholder=f"e.g. 10,000",
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
    progress_text = st.empty()
    progress_bar = st.progress(0)

    total_files = len(uploaded_files)
    parser = PARSERS[bank_choice]
    processing_errors: List[str] = []
    total_extracted = 0
    files_finished = 0

    progress_text.write(f"Preparing {total_files} file(s) for {bank_choice}.")

    for file_idx, uploaded_file in enumerate(uploaded_files):
        if st.session_state.get("stop_requested"):
            st.session_state.status = "stopped"
            progress_text.write(f"Stopped after {files_finished} of {total_files} file(s).")
            break

        current_file = file_idx + 1
        progress_bar.progress(file_idx / total_files)
        progress_text.write(f"Processing {current_file} of {total_files}: {uploaded_file.name}")

        try:
            pdf_bytes = uploaded_file.getvalue()

            # decrypt if encrypted
            if is_pdf_encrypted(pdf_bytes):
                progress_text.write(f"Decrypting {uploaded_file.name}...")
                pdf_bytes = decrypt_pdf_bytes(pdf_bytes, st.session_state.pdf_password)

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
                progress_text.write(f"✓ {uploaded_file.name}: {len(tx_norm)} transactions extracted")
            else:
                progress_text.write(f"⚠ {uploaded_file.name}: No transactions found")

        except Exception as e:
            processing_errors.append(uploaded_file.name)
            progress_text.write(f"✗ Error processing {uploaded_file.name}: {str(e)[:100]}")
            st.error(f"❌ Error processing {uploaded_file.name}: {e}")
            st.exception(e)

        progress_bar.progress((file_idx + 1) / total_files)
        files_finished = file_idx + 1

    # After all files are processed - set final progress and show completion
    progress_bar.progress(1.0)
    
    # Display final status message
    if st.session_state.get("stop_requested"):
        st.session_state.status = "stopped"
        progress_text.write(f"✅ Stopped after {files_finished} of {total_files} file(s).")
        st.warning(f"⚠️ Processing stopped at {files_finished} of {total_files} files.")
    elif processing_errors:
        st.session_state.status = "completed_with_errors"
        progress_text.write(
            f"⚠️ Finished with {len(processing_errors)} error(s). Extracted {total_extracted} transactions from {total_files} file(s)."
        )
        st.warning(f"⚠️ Completed with {len(processing_errors)} error(s). Check the errors above.")
    else:
        st.session_state.status = "completed"
        progress_text.write(f"✅ Completed! Extracted {total_extracted} transactions from {total_files} file(s).")
        st.success(f"🎉 Successfully processed all {total_files} file(s)!")
    
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


# =========================================================
# Monthly Summary Calculation
# =========================================================
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
    # Affin-only
    if bank_choice == "Affin Bank" and st.session_state.affin_statement_totals:
        rows: List[dict] = []
        for t in st.session_state.affin_statement_totals:
            month = t.get("statement_month") or "UNKNOWN"
            fname = t.get("source_file", "") or ""
            company_name = st.session_state.file_company_name.get(fname)
            account_no = st.session_state.file_account_no.get(fname)

            opening = t.get("opening_balance")
            ending = t.get("ending_balance")
            total_debit = t.get("total_debit")
            total_credit = t.get("total_credit")

            td = None if total_debit is None else round(float(safe_float(total_debit)), 2)
            tc = None if total_credit is None else round(float(safe_float(total_credit)), 2)

            opening_balance = round(float(safe_float(opening)), 2) if opening is not None else None
            ending_balance = round(float(safe_float(ending)), 2) if ending is not None else None

            txs = st.session_state.affin_file_transactions.get(fname, []) if fname else []
            tx_count = count_statement_transactions(txs) if txs else None

            balances: List[float] = []
            for x in txs:
                b = x.get("balance")
                if b is None:
                    continue
                try:
                    balances.append(float(safe_float(b)))
                except Exception:
                    pass

            if ending_balance is None and balances:
                ending_balance = round(float(balances[-1]), 2)

            lowest_balance = round(min(balances), 2) if balances else None
            highest_balance = round(max(balances), 2) if balances else None

            net_change = None
            if td is not None and tc is not None:
                net_change = round(float(tc - td), 2)

            if opening_balance is None and ending_balance is not None and td is not None and tc is not None:
                opening_balance = round(float(ending_balance - (tc - td)), 2)

            rows.append(
                {
                    "month": month,
                    "company_name": company_name,
                    "account_no": account_no,
                    "transaction_count": tx_count,
                    "opening_balance": opening_balance,
                    "total_debit": td,
                    "total_credit": tc,
                    "net_change": net_change,
                    "ending_balance": ending_balance,
                    "lowest_balance": lowest_balance,
                    "lowest_balance_raw": lowest_balance,
                    "highest_balance": highest_balance,
                    "od_flag": bool(lowest_balance is not None and float(lowest_balance) < 0),
                    "source_files": fname,
                }
            )
        return sorted(rows, key=lambda r: str(r.get("month", "9999-99")))

    # Ambank-only
    if bank_choice == "Ambank" and st.session_state.ambank_statement_totals:
        rows: List[dict] = []
        for t in st.session_state.ambank_statement_totals:
            month = t.get("statement_month") or "UNKNOWN"
            fname = t.get("source_file", "") or ""
            company_name = st.session_state.file_company_name.get(fname)
            account_no = st.session_state.file_account_no.get(fname)

            opening = t.get("opening_balance")
            ending = t.get("ending_balance")
            total_debit = t.get("total_debit")
            total_credit = t.get("total_credit")

            td = None if total_debit is None else round(float(safe_float(total_debit)), 2)
            tc = None if total_credit is None else round(float(safe_float(total_credit)), 2)

            opening_balance = round(float(safe_float(opening)), 2) if opening is not None else None
            ending_balance = round(float(safe_float(ending)), 2) if ending is not None else None

            txs = st.session_state.ambank_file_transactions.get(fname, []) if fname else []
            tx_count = count_statement_transactions(txs) if txs else None

            balances: List[float] = []
            for x in txs:
                b = x.get("balance")
                if b is None:
                    continue
                try:
                    balances.append(float(safe_float(b)))
                except Exception:
                    pass

            lowest_balance = round(min(balances), 2) if balances else None
            highest_balance = round(max(balances), 2) if balances else None

            net_change = None
            if td is not None and tc is not None:
                net_change = round(float(tc - td), 2)

            if opening_balance is None and ending_balance is not None and td is not None and tc is not None:
                opening_balance = round(float(ending_balance - (tc - td)), 2)

            rows.append(
                {
                    "month": month,
                    "company_name": company_name,
                    "account_no": account_no,
                    "transaction_count": tx_count,
                    "opening_balance": opening_balance,
                    "total_debit": td,
                    "total_credit": tc,
                    "net_change": net_change,
                    "ending_balance": ending_balance,
                    "lowest_balance": lowest_balance,
                    "lowest_balance_raw": lowest_balance,
                    "highest_balance": highest_balance,
                    "od_flag": bool(lowest_balance is not None and float(lowest_balance) < 0),
                    "source_files": fname,
                }
            )
        return sorted(rows, key=lambda r: str(r.get("month", "9999-99")))

    # CIMB-only
    if bank_choice == "CIMB Bank" and st.session_state.cimb_statement_totals:
        rows: List[dict] = []
        for t in st.session_state.cimb_statement_totals:
            month = t.get("statement_month") or "UNKNOWN"
            fname = t.get("source_file", "") or ""
            company_name = st.session_state.file_company_name.get(fname)
            account_no = st.session_state.file_account_no.get(fname)

            ending = t.get("ending_balance")
            total_debit = t.get("total_debit")
            total_credit = t.get("total_credit")

            td = None if total_debit is None else round(float(safe_float(total_debit)), 2)
            tc = None if total_credit is None else round(float(safe_float(total_credit)), 2)
            ending_balance = round(float(safe_float(ending)), 2) if ending is not None else None

            net_change = None
            opening_balance = None
            if td is not None and tc is not None:
                net_change = round(float(tc - td), 2)
                if ending_balance is not None:
                    opening_balance = round(float(ending_balance - (tc - td)), 2)

            txs = st.session_state.cimb_file_transactions.get(fname, []) if fname else []
            tx_count = count_statement_transactions(txs) if txs else None

            balances: List[float] = []
            for x in txs:
                desc = str(x.get("description") or "")
                if re.search(r"CLOSING\s+BALANCE\s*/\s*BAKI\s+PENUTUP", desc, flags=re.IGNORECASE):
                    continue
                b = x.get("balance")
                if b is None:
                    continue
                try:
                    balances.append(float(safe_float(b)))
                except Exception:
                    pass

            lowest_balance = round(min(balances), 2) if balances else None
            highest_balance = round(max(balances), 2) if balances else None

            rows.append(
                {
                    "month": month,
                    "company_name": company_name,
                    "account_no": account_no,
                    "transaction_count": tx_count,
                    "opening_balance": opening_balance,
                    "total_debit": td,
                    "total_credit": tc,
                    "net_change": net_change,
                    "ending_balance": ending_balance,
                    "lowest_balance": lowest_balance,
                    "lowest_balance_raw": lowest_balance,
                    "highest_balance": highest_balance,
                    "od_flag": bool(lowest_balance is not None and float(lowest_balance) < 0),
                    "source_files": fname,
                }
            )
        return sorted(rows, key=lambda r: str(r.get("month", "9999-99")))

    # RHB-only
    if bank_choice == "RHB Bank" and st.session_state.rhb_statement_totals:
        rows: List[dict] = []
        for t in st.session_state.rhb_statement_totals:
            month = t.get("statement_month") or "UNKNOWN"
            fname = t.get("source_file", "") or ""
            company_name = st.session_state.file_company_name.get(fname)
            account_no = st.session_state.file_account_no.get(fname)

            opening = t.get("opening_balance")
            ending = t.get("ending_balance")
            total_debit = t.get("total_debit")
            total_credit = t.get("total_credit")

            td = None if total_debit is None else round(float(safe_float(total_debit)), 2)
            tc = None if total_credit is None else round(float(safe_float(total_credit)), 2)
            opening_balance = round(float(safe_float(opening)), 2) if opening is not None else None
            ending_balance = round(float(safe_float(ending)), 2) if ending is not None else None

            txs = st.session_state.rhb_file_transactions.get(fname, []) if fname else []
            tx_count = count_statement_transactions(txs) if txs else None

            balances: List[float] = []
            for x in txs:
                b = x.get("balance")
                if b is None:
                    continue
                try:
                    balances.append(float(safe_float(b)))
                except Exception:
                    pass

            lowest_balance = round(min(balances), 2) if balances else None
            highest_balance = round(max(balances), 2) if balances else None

            net_change = None
            if td is not None and tc is not None:
                net_change = round(float(tc - td), 2)

            if opening_balance is None and ending_balance is not None and td is not None and tc is not None:
                opening_balance = round(float(ending_balance - (tc - td)), 2)

            rows.append(
                {
                    "month": month,
                    "company_name": company_name,
                    "account_no": account_no,
                    "transaction_count": tx_count,
                    "opening_balance": opening_balance,
                    "total_debit": td,
                    "total_credit": tc,
                    "net_change": net_change,
                    "ending_balance": ending_balance,
                    "lowest_balance": lowest_balance,
                    "lowest_balance_raw": lowest_balance,
                    "highest_balance": highest_balance,
                    "od_flag": bool(lowest_balance is not None and float(lowest_balance) < 0),
                    "source_files": fname,
                }
            )
        return sorted(rows, key=lambda r: str(r.get("month", "9999-99")))

    # Default banks
    if not transactions:
        if bank_choice == "Bank Islam" and getattr(st.session_state, "bank_islam_file_month", {}):
            rows: List[dict] = []
            for fname, month in sorted(st.session_state.bank_islam_file_month.items(), key=lambda x: x[1]):
                company_name = st.session_state.file_company_name.get(fname)
                account_no = st.session_state.file_account_no.get(fname)
                rows.append(
                    {
                        "month": month,
                        "company_name": company_name,
                        "account_no": account_no,
                        "transaction_count": 0,
                        "opening_balance": None,
                        "total_debit": 0.0,
                        "total_credit": 0.0,
                        "net_change": 0.0,
                        "ending_balance": None,
                        "lowest_balance": None,
                        "lowest_balance_raw": None,
                        "highest_balance": None,
                        "od_flag": False,
                        "source_files": fname,
                    }
                )
            return rows
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

    # Bank Islam ensure statement months with zero tx still appear
    if bank_choice == "Bank Islam" and getattr(st.session_state, "bank_islam_file_month", {}):
        existing_months = {r.get("month") for r in monthly_summary}
        for fname, month in st.session_state.bank_islam_file_month.items():
            if month in existing_months:
                continue
            company_name = st.session_state.file_company_name.get(fname)
            account_no = st.session_state.file_account_no.get(fname)
            monthly_summary.append(
                {
                    "month": month,
                    "company_name": company_name,
                    "account_no": account_no,
                    "transaction_count": 0,
                    "opening_balance": None,
                    "total_debit": 0.0,
                    "total_credit": 0.0,
                    "net_change": 0.0,
                    "ending_balance": None,
                    "lowest_balance": None,
                    "lowest_balance_raw": None,
                    "highest_balance": None,
                    "od_flag": False,
                    "source_files": fname,
                }
            )

    # Fill opening_balance for default banks using prior month's ending_balance when possible.
    monthly_summary_sorted = sorted(monthly_summary, key=lambda x: x["month"])
    prev_end = None
    for r in monthly_summary_sorted:
        if r.get("opening_balance") is None:
            if prev_end is not None:
                r["opening_balance"] = round(float(prev_end), 2)
            else:
                # best-effort fallback: opening = ending - net_change
                eb = r.get("ending_balance")
                nc = r.get("net_change")
                if eb is not None and nc is not None:
                    try:
                        r["opening_balance"] = round(float(safe_float(eb) - safe_float(nc)), 2)
                    except Exception:
                        r["opening_balance"] = None

        # update prev_end for next month
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


# ---------------------------------------------------
# DISPLAY
# ---------------------------------------------------
if st.session_state.results or (bank_choice == "Affin Bank" and st.session_state.affin_statement_totals) or (
    bank_choice == "Ambank" and st.session_state.ambank_statement_totals
) or (bank_choice == "CIMB Bank" and st.session_state.cimb_statement_totals) or (
    bank_choice == "RHB Bank" and st.session_state.rhb_statement_totals
):
    high_value_threshold = get_high_value_threshold()
    
    # Convert results to DataFrame
    df = pd.DataFrame(st.session_state.results) if st.session_state.results else pd.DataFrame()
    
    if not df.empty:
        # Run fraud/pattern checks
        df = run_fraud_checks(df, high_value_threshold)
        
        # Display transaction pattern overview
        render_transaction_overview(df, high_value_threshold)
        
        # Display basic transaction table
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
                "balance": "Running Balance"
            }
        )
    else:
        st.info("No line-item transactions extracted.")
    
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
    col1, col2, col3 = st.columns(3)

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
        elif hasattr(obj, 'isoformat'):  # For datetime objects
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
            "📄 Download Transactions (JSON)",
            json.dumps(json_records, indent=4),
            "transactions.json",
            "application/json",
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
            date_min_str = None
            date_max_str = None
            date_range_str = None

        total_files_processed = None
        if "source_file" in df_download.columns and not df_download.empty:
            total_files_processed = int(df_download["source_file"].nunique())
        else:
            if bank_choice == "Affin Bank":
                total_files_processed = len(st.session_state.affin_statement_totals)
            elif bank_choice == "Ambank":
                total_files_processed = len(st.session_state.ambank_statement_totals)
            elif bank_choice == "CIMB Bank":
                total_files_processed = len(st.session_state.cimb_statement_totals)
            elif bank_choice == "RHB Bank":
                total_files_processed = len(st.session_state.rhb_statement_totals)

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
            "transactions": json_records,
        }

        st.download_button(
            "📊 Download Full Report (JSON)",
            json.dumps(full_report, indent=4),
            "full_report.json",
            "application/json",
        )

    with col3:
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df_download.to_excel(writer, sheet_name="Transactions", index=False)
            if monthly_summary:
                pd.DataFrame(monthly_summary).to_excel(writer, sheet_name="Monthly Summary", index=False)
            if transaction_analysis_report.get("high_value_credits"):
                pd.DataFrame(transaction_analysis_report["high_value_credits"]).to_excel(
                    writer,
                    sheet_name="High Value Credits",
                    index=False,
                )

        st.download_button(
            "📊 Download Full Report (XLSX)",
            output.getvalue(),
            "full_report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

else:
    if (
        uploaded_files
        and st.session_state.status == "idle"
        and not st.session_state.high_value_threshold_error
        and not st.session_state.bank_choice_error
    ):
        st.warning("⚠️ No transactions found — click **Start Processing**.")

