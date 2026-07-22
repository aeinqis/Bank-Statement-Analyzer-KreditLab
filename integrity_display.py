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

from app import FRAUD_LAYER_ORDER

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


__all__ = [
    'bind_app_globals',
    'render_integrity_report_styles',
    'render_integrity_metric',
    'render_integrity_overview',
    'render_fraud_summary',
    'file_risk_label',
    'integrity_layer_counts',
    'severity_badge',
    'severity_dot',
    'is_benign_integrity_finding',
]
