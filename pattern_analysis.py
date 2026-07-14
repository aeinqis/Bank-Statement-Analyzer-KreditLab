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


def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().upper())


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


__all__ = [
    'bind_app_globals',
    'run_fraud_checks',
    'detect_duplicate_transactions',
    'detect_rapid_repeat_transactions',
    'summarize_transaction_patterns',
    'render_pattern_details',
    'render_transaction_overview',
    'render_metric_cards',
    'is_round_number',
    'normalize_text',
    'compute_epf_payments',
    'compute_socso_payments',
    'compute_lhdn_tax_payments',
    'compute_hrdf_payments',
]
