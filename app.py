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
from fraud_logic import (
    analyze_pdf_batch,
    build_display_summary,
    detect_font_anomalies,
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
        padding: 0.8rem 3rem 0.8rem 1rem !important;
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
    div[data-testid="stExpander"] details > summary > div:first-child:has(svg),
    div[data-testid="stExpander"] details > summary svg {
        display: none !important;
    }

    div[data-testid="stExpander"] details > summary::after {
        content: "";
        position: absolute;
        right: 1.2rem;
        top: 50%;
        width: 0.45rem;
        height: 0.40rem;
        border-right: 2px solid #F3F4F6;
        border-bottom: 2px solid #F3F4F6;
        transform: translateY(-60%) rotate(45deg);
    }

    div[data-testid="stExpander"] details[open] > summary::after {
        transform: translateY(-30%) rotate(225deg);
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
def build_counterparty_ledger_from_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build counterparty ledger summary from transaction dataframe by extracting counterparty from description
    """
    if df.empty:
        return pd.DataFrame()
    
    df = df.copy()
    
    # Extract counterparty name from description
    def extract_counterparty(description: str) -> str:
        if not description:
            return "UNKNOWN"
        
        desc = str(description).upper()
        
        # Look for REMITTANCE patterns
        remittance_match = re.search(r'REMITTANCE\s+(?:CR\s+)?([A-Z\s]+?)(?:\s+\d|$)', desc, re.IGNORECASE)
        if remittance_match:
            counterparty = remittance_match.group(1).strip()
            if len(counterparty) > 3:
                return counterparty
        
        # Look for AUTOPAY patterns
        autopay_match = re.search(r'AUTOPAY\s+([A-Z\s]+?)(?:\s+RTB|\s+\d|$)', desc, re.IGNORECASE)
        if autopay_match:
            counterparty = autopay_match.group(1).strip()
            if counterparty:
                return counterparty
        
        # Look for IBG patterns
        ibg_match = re.search(r'IBG\s+(?:CREDIT\s+)?([A-Z\s]+?)(?:\s+[A-Z]|\s+\d|$)', desc, re.IGNORECASE)
        if ibg_match:
            return ibg_match.group(1).strip()
        
        # Clean up and take first few words
        cleaned = re.sub(r'\d{10,}', '', desc)
        cleaned = re.sub(r'CR\d+', '', cleaned)
        cleaned = re.sub(r'RTB\d+', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        words = cleaned.split()
        if len(words) > 5:
            cleaned = ' '.join(words[:5])
        
        return cleaned if len(cleaned) > 3 else "UNKNOWN"
    
    df['counterparty'] = df['description'].apply(extract_counterparty)
    
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
    
    # Sort by absolute net position
    summary_df['abs_net_position'] = summary_df['net_position'].abs()
    summary_df = summary_df.sort_values('abs_net_position', ascending=False)
    summary_df = summary_df.drop('abs_net_position', axis=1)
    
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
    st.markdown("*Top counterparties by absolute net position. Green indicates net inflows; red indicates net outflows.*")
    
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
        options=[''] + counterparty_summary['counterparty_name'].tolist(),
        format_func=lambda x: x if x else "Choose a counterparty..."
    )
    
    # Show transactions for selected counterparty
    if selected_counterparty:
        # Extract counterparty from df
        df_copy = df.copy()
        
        # Extract counterparty for filtering
        def extract_counterparty(description: str) -> str:
            if not description:
                return "UNKNOWN"
            desc = str(description).upper()
            remittance_match = re.search(r'REMITTANCE\s+(?:CR\s+)?([A-Z\s]+?)(?:\s+\d|$)', desc, re.IGNORECASE)
            if remittance_match:
                counterparty = remittance_match.group(1).strip()
                if len(counterparty) > 3:
                    return counterparty
            autopay_match = re.search(r'AUTOPAY\s+([A-Z\s]+?)(?:\s+RTB|\s+\d|$)', desc, re.IGNORECASE)
            if autopay_match:
                counterparty = autopay_match.group(1).strip()
                if counterparty:
                    return counterparty
            ibg_match = re.search(r'IBG\s+(?:CREDIT\s+)?([A-Z\s]+?)(?:\s+[A-Z]|\s+\d|$)', desc, re.IGNORECASE)
            if ibg_match:
                return ibg_match.group(1).strip()
            cleaned = re.sub(r'\d{10,}', '', desc)
            cleaned = re.sub(r'CR\d+', '', cleaned)
            cleaned = re.sub(r'RTB\d+', '', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            words = cleaned.split()
            if len(words) > 5:
                cleaned = ' '.join(words[:5])
            return cleaned if len(cleaned) > 3 else "UNKNOWN"
        
        df_copy['counterparty'] = df_copy['description'].apply(extract_counterparty)
        counterparty_tx = df_copy[df_copy['counterparty'] == selected_counterparty].copy()
        
        if not counterparty_tx.empty:
            # Format for display
            display_tx = counterparty_tx[['date', 'description', 'credit', 'debit', 'balance']].copy()
            display_tx['credit'] = display_tx['credit'].apply(lambda x: f"RM {x:,.2f}" if x and x > 0 else "")
            display_tx['debit'] = display_tx['debit'].apply(lambda x: f"RM {x:,.2f}" if x and x > 0 else "")
            display_tx['balance'] = display_tx['balance'].apply(lambda x: f"RM {x:,.2f}" if x and str(x) != 'nan' else "")
            
            st.markdown(f"### Transaction Details: {selected_counterparty}")
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
            
            # Summary stats for selected counterparty
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Transactions", len(counterparty_tx))
            with col2:
                credits_total = counterparty_tx['credit'].sum()
                st.metric("Total Credits", f"RM {credits_total:,.2f}")
            with col3:
                debits_total = counterparty_tx['debit'].sum()
                st.metric("Total Debits", f"RM {debits_total:,.2f}")
            with col4:
                net = credits_total - debits_total
                st.metric("Net Position", f"RM {net:,.2f}", 
                         delta="Inflow" if net > 0 else "Outflow" if net < 0 else "Neutral")


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
    st.markdown('<h3 class="kl-pattern-details-heading">Pattern Details</h3>', unsafe_allow_html=True)
    
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
                duplicate_columns = [c for c in ["date", "description", "amount", "balance"] if c in duplicate_hits.columns]
                st.dataframe(duplicate_hits[duplicate_columns], use_container_width=True)
    
    # Rapid repeat transactions
    if "is_rapid_repeat_transaction" in df.columns:
        rapid_repeat_hits = df[df["is_rapid_repeat_transaction"] == True].copy()
        if not rapid_repeat_hits.empty:
            with st.expander(f"High freq transactions ({len(rapid_repeat_hits)})"):
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
            with st.expander(f"Round-number transactions ({len(round_hits)})"):
                st.caption("Transactions with round numbers (multiple of 10,000).")
                cols = [c for c in ["date", "description", "amount", "source_file"] if c in round_hits.columns]
                st.dataframe(round_hits[cols], use_container_width=True)
    
    # High value transactions
    if "is_high_value" in df.columns:
        high_hits = df[df["is_high_value"] == True].copy()
        if not high_hits.empty:
            with st.expander(f"High-value transactions (>= RM{high_value_threshold:,.2f}) ({len(high_hits)})"):
                high_value_columns = [c for c in ["date", "description", "credit", "balance"] if c in high_hits.columns]
                st.dataframe(high_hits[high_value_columns], use_container_width=True)
    
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
    
    # Calculate total credits and debits
    total_credits = analysis_df['credit'].sum() if 'credit' in analysis_df.columns else 0
    total_debits = analysis_df['debit'].sum() if 'debit' in analysis_df.columns else 0
    net_position = total_credits - total_debits
    
    # Display Credit/Debit summary cards in a row
    st.markdown("#### Financial Summary")
    credit_col, debit_col, net_col = st.columns(3)
    
    with credit_col:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #1a472a 0%, #0d2818 100%); 
                        border-radius: 12px; 
                        padding: 1rem 1.15rem; 
                        border: 1px solid #2e7d32;
                        text-align: center;">
                <div style="color: #a5d6a7; font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">💰 TOTAL CREDITS</div>
                <div style="color: #69f0ae; font-size: 2rem; font-weight: 800;">RM {total_credits:,.2f}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    with debit_col:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #4a1a1a 0%, #2d1010 100%); 
                        border-radius: 12px; 
                        padding: 1rem 1.15rem; 
                        border: 1px solid #c62828;
                        text-align: center;">
                <div style="color: #ef9a9a; font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">💸 TOTAL DEBITS</div>
                <div style="color: #ff8a80; font-size: 2rem; font-weight: 800;">RM {total_debits:,.2f}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    with net_col:
        net_color = "#69f0ae" if net_position >= 0 else "#ff8a80"
        net_bg = "linear-gradient(135deg, #1a2a3a 0%, #0d1a2a 100%)"
        net_border = "#2e7d32" if net_position >= 0 else "#c62828"
        net_icon = "📈" if net_position >= 0 else "📉"
        net_label = "NET POSITION" if net_position >= 0 else "NET LOSS"
        
        st.markdown(
            f"""
            <div style="background: {net_bg}; 
                        border-radius: 12px; 
                        padding: 1rem 1.15rem; 
                        border: 1px solid {net_border};
                        text-align: center;">
                <div style="color: #b0bec5; font-size: 0.85rem; font-weight: 600; margin-bottom: 0.5rem;">{net_icon} {net_label}</div>
                <div style="color: {net_color}; font-size: 2rem; font-weight: 800;">RM {abs(net_position):,.2f}</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    st.markdown("---")
    
    # Display pattern metrics in two rows
    item_map = dict(pattern_summary.get("items", []))
    statutory_items = pattern_summary.get("statutory_items", [])
    
    # Row 1: Pattern flags
    st.markdown("#### Pattern Detection")
    pattern_col1, pattern_col2, pattern_col3, pattern_col4, pattern_col5 = st.columns(5)
    
    with pattern_col1:
        st.metric(
            "📋 Total Transactions", 
            item_map.get("Total Transactions", 0)
        )
    with pattern_col2:
        st.metric(
            "⚠️ High-Value Flags", 
            item_map.get("High-Value Flags", 0),
            help=f"Transactions >= RM {high_value_threshold:,.2f}"
        )
    with pattern_col3:
        st.metric(
            "🔢 Round-Number", 
            item_map.get("Round-Number", 0),
            help="Transactions with round numbers (multiple of 10,000)"
        )
    with pattern_col4:
        st.metric(
            "🔄 Repeated", 
            item_map.get("Repeated", 0),
            help="Duplicate transactions with same date, description, and amount"
        )
    with pattern_col5:
        st.metric(
            "⚡ High Frequency Flags", 
            item_map.get("High Frequency Flags", 0),
            help="Repeated payments to same merchant within short time window"
        )
    
    # Row 2: Statutory payments
    if statutory_items:
        st.markdown("#### Statutory Payments")
        statutory_cols = st.columns(len(statutory_items))
        
        for idx, (label, count, total) in enumerate(statutory_items):
            icon_map = {
                "EPF / KWSP": "🏦",
                "SOCSO / PERKESO": "🛡️",
                "LHDN / Tax": "📋",
                "HRDF / PSMB": "🎓"
            }
            icon = icon_map.get(label, "📊")
            
            with statutory_cols[idx]:
                st.metric(
                    f"{icon} {label}", 
                    count,
                    delta=total,
                    delta_color="off"
                )
    
    # Render detailed expandable sections
    render_pattern_details(analysis_df, high_value_threshold)


# -----------------------------
# Core Processing Functions
# -----------------------------
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
    progress_text = st.empty()
    progress_bar = st.progress(0)

    total_files = len(uploaded_files)
    parser = PARSERS[bank_choice]
    processing_errors: List[str] = []
    total_extracted = 0
    files_finished = 0
    resolved_pdf_bytes = {}

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

    # Run PDF integrity checks
    analysis_results = {}
    if resolved_pdf_bytes:
        progress_text.write("Running PDF integrity checks...")
        try:
            analysis_results = analyze_pdf_batch(resolved_pdf_bytes)
        except Exception as e:
            st.warning(f"PDF integrity check failed: {e}")

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


# ---------------------------------------------------
# DISPLAY
# ---------------------------------------------------
if st.session_state.results:
    high_value_threshold = get_high_value_threshold()
    
    # Convert results to DataFrame
    df = pd.DataFrame(st.session_state.results) if st.session_state.results else pd.DataFrame()
    
    if not df.empty:
        # Run fraud/pattern checks
        df = run_fraud_checks(df, high_value_threshold)
        
        # Display transaction pattern overview
        render_transaction_overview(df, high_value_threshold)
        
        # Display Counterparty Ledger Table
        st.markdown("---")
        render_counterparty_ledger_table(df)
        
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
        and not st.session_state.pdf_upload_error
    ):
        st.warning("⚠️ No transactions found — click **Start Processing**.")