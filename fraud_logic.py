
"""
fraud_logic.py

Unified fraud detection module for bank-statement analysis.

This file combines:
1) Transaction-level fraud checks:
   - high-value transactions
   - round-number pattern detection
   - simple balance delta calculation
2) Typography / font anomaly detection
3) Full PDF integrity analysis (8 layers)
4) Batch comparison helpers for multiple statements

Designed to stay backward-compatible with the current app.py usage:
    from fraud_logic import run_fraud_checks, detect_font_anomalies

Optional new APIs:
    - analyze_pdf(pdf_bytes, filename="")
    - compare_batch(results, pdf_data)
    - build_display_summary(analysis_result)
    - get_priority_findings(analysis_result, limit=10)
    - analyze_pdf_batch(pdf_files)
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

import fitz  # PyMuPDF
import pandas as pd
import pdfplumber
from pypdf import PdfReader

from core_utils import sanitize_transaction_description

# Optional OCR dependencies
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    Image = None
    OCR_AVAILABLE = False


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

_SEVERITY_ORDER = {LOW: 1, MEDIUM: 2, HIGH: 3}


def _worst_severity(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return LOW
    return max(
        (f.get("severity", LOW) for f in findings),
        key=lambda s: _SEVERITY_ORDER.get(s, 0),
    )


def _finding(layer: str, severity: str, message: str, detail: Any = None) -> Dict[str, Any]:
    finding = {"layer": layer, "severity": severity, "message": message}
    if detail is not None:
        finding["detail"] = detail
    return finding


# ---------------------------------------------------------------------------
# OCR / font manipulation helpers
# ---------------------------------------------------------------------------
def detect_font_manipulation(pdf_bytes: bytes) -> str:
    """
    Renders PDF pages and runs OCR to reveal visible text that may differ
    from embedded digital text. Useful for spotting painted-over or hidden layers.

    Returns OCR text only. This function does not raise if OCR dependencies are
    unavailable; it returns an explanatory message instead.
    """
    if not OCR_AVAILABLE:
        return "OCR dependencies are not available. Install pytesseract and Pillow."

    ocr_text = ""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img = Image.open(BytesIO(pix.tobytes("png")))
            ocr_text += pytesseract.image_to_string(img)
    return ocr_text


def clean_font_name(font_name: Optional[str]) -> str:
    """Remove subset prefix such as 'ABCDEF+' from PDF font names."""
    if font_name and "+" in font_name:
        return font_name.split("+", 1)[-1]
    return font_name or "Unknown"


def detect_font_anomalies(pdf_file_like) -> List[Dict[str, Any]]:
    """
    Forensic typography analysis using pdfplumber character extraction.

    Flags text styles that appear in less than 5% of the document body
    (middle 70% of each page), which often indicates post-generation edits.
    """
    all_chars: List[Dict[str, Any]] = []

    pdf_file_like.seek(0)
    with pdfplumber.open(pdf_file_like) as pdf:
        for page in pdf.pages:
            h = page.height
            upper_bound = h * 0.15
            lower_bound = h * 0.85

            for char in page.chars:
                if char.get("text", "").strip() and (upper_bound < char.get("top", 0) < lower_bound):
                    full_name = char.get("fontname", "Unknown")
                    all_chars.append(
                        {
                            "full_name": full_name,
                            "family": clean_font_name(full_name),
                            "size": round(char.get("size", 0)),
                        }
                    )

    if not all_chars:
        return []

    total_char_count = len(all_chars)
    style_counts = Counter((c["full_name"], c["size"], c["family"]) for c in all_chars)
    if not style_counts:
        return []

    dominant_style_tuple = style_counts.most_common(1)[0][0]
    dominant_full_name = dominant_style_tuple[0]
    dominant_size = dominant_style_tuple[1]

    anomalies: List[Dict[str, Any]] = []
    for (full_name, size, family), count in style_counts.items():
        if full_name == dominant_full_name and size == dominant_size:
            continue

        frequency = (count / total_char_count) * 100
        if frequency < 5.0:
            anomalies.append(
                {
                    "font": family,
                    "size": size,
                    "signature": full_name.split("+")[0] if "+" in full_name else "None",
                }
            )

    return anomalies


# ---------------------------------------------------------------------------
# Transaction-level fraud checks
# ---------------------------------------------------------------------------
def _coerce_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def detect_round_numbers(
    df: pd.DataFrame,
    threshold: float = 0.25,
) -> Tuple[pd.DataFrame, bool, float]:
    """
    Flags suspicious concentration of round amounts in credit/debit columns.
    A round number is treated as divisible by 1000.
    """
    if df.empty:
        return df, False, 0.0

    def is_round(val: Any) -> bool:
        if pd.isna(val) or val == 0:
            return False
        try:
            return float(val) % 1000 == 0
        except Exception:
            return False

    if "credit" not in df.columns:
        df["credit"] = 0.0
    if "debit" not in df.columns:
        df["debit"] = 0.0
    if "description" in df.columns:
        df["description"] = df["description"].apply(sanitize_transaction_description)

    df["credit"] = _coerce_numeric_series(df["credit"]).fillna(0.0)
    df["debit"] = _coerce_numeric_series(df["debit"]).fillna(0.0)

    df["is_round_credit"] = df["credit"].apply(is_round)
    df["is_round_debit"] = df["debit"].apply(is_round)
    df["is_round"] = df["is_round_credit"] | df["is_round_debit"]

    total_transactions = len(df)
    round_count = int(df["is_round"].sum())
    round_ratio = round_count / total_transactions if total_transactions > 0 else 0.0
    is_suspicious = round_ratio > threshold

    return df, is_suspicious, round_ratio


def _normalize_merchant_description(value: Any) -> str:
    text = str(value or "").upper()
    text = re.sub(r"\b\d{2,}\b", " ", text)
    text = re.sub(r"[^A-Z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_duplicate_transactions(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    if "description" not in df.columns:
        df["description"] = ""
    if "date" not in df.columns:
        df["date"] = pd.NaT
    if "source_file" not in df.columns:
        df["source_file"] = ""

    df["txn_amount"] = df[["credit", "debit"]].max(axis=1)
    df["txn_direction"] = df.apply(
        lambda row: "credit" if row.get("credit", 0) > 0 else "debit",
        axis=1,
    )
    df["normalized_description"] = df["description"].apply(_normalize_merchant_description)
    df["normalized_date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()

    duplicate_key = [
        "source_file",
        "normalized_date",
        "normalized_description",
        "txn_direction",
        "txn_amount",
    ]
    duplicate_counts = df.groupby(duplicate_key, dropna=False)["normalized_description"].transform("size")
    df["duplicate_count"] = duplicate_counts.fillna(0).astype(int)
    df["is_duplicate_transaction"] = df["duplicate_count"] > 1
    return df


def detect_rapid_repeat_transactions(
    df: pd.DataFrame,
    min_repeat_days: int = 3,
    lookback_days: int = 7,
) -> pd.DataFrame:
    if df.empty:
        return df

    if "normalized_description" not in df.columns:
        df["normalized_description"] = df.get("description", pd.Series(dtype=object)).apply(
            _normalize_merchant_description
        )
    if "normalized_date" not in df.columns:
        df["normalized_date"] = pd.to_datetime(df.get("date"), errors="coerce").dt.normalize()
    if "source_file" not in df.columns:
        df["source_file"] = ""
    if "txn_amount" not in df.columns:
        df["txn_amount"] = df[["credit", "debit"]].max(axis=1)

    df["repeat_days_in_window"] = 0
    df["is_rapid_repeat_transaction"] = False

    valid_mask = df["normalized_date"].notna() & df["normalized_description"].ne("")
    if not valid_mask.any():
        return df

    group_columns = ["source_file", "normalized_description"]
    for _, index_values in df[valid_mask].groupby(group_columns, dropna=False).groups.items():
        group_df = df.loc[list(index_values)].sort_values("normalized_date")
        unique_days = sorted(group_df["normalized_date"].dropna().unique())
        if len(unique_days) < min_repeat_days:
            continue

        flagged_days = set()
        for start_pos, start_day in enumerate(unique_days):
            window_end = start_day + pd.Timedelta(days=lookback_days - 1)
            window_days = [day for day in unique_days[start_pos:] if day <= window_end]
            if len(window_days) >= min_repeat_days:
                flagged_days.update(window_days)

        if not flagged_days:
            continue

        flagged_mask = df.index.isin(group_df.index) & df["normalized_date"].isin(list(flagged_days))
        df.loc[flagged_mask, "is_rapid_repeat_transaction"] = True
        df.loc[flagged_mask, "repeat_days_in_window"] = len(flagged_days)

    return df


def detect_transaction_spikes_and_drops(
    df: pd.DataFrame,
    lookback_count: int = 5,
    spike_multiplier: float = 3.0,
    drop_ratio: float = 0.33,
    min_baseline_amount: float = 100.0,
) -> pd.DataFrame:
    if df.empty:
        return df

    if "source_file" not in df.columns:
        df["source_file"] = ""
    if "date" not in df.columns:
        df["date"] = pd.NaT
    if "txn_amount" not in df.columns:
        df["txn_amount"] = df[["credit", "debit"]].max(axis=1)
    if "txn_direction" not in df.columns:
        df["txn_direction"] = df.apply(
            lambda row: "credit" if row.get("credit", 0) > 0 else "debit",
            axis=1,
        )

    df["transaction_sequence_date"] = pd.to_datetime(df["date"], errors="coerce")
    df["recent_median_amount"] = pd.NA
    df["amount_vs_recent_median"] = pd.NA
    df["is_transaction_spike"] = False
    df["is_transaction_drop"] = False

    group_columns = ["source_file", "txn_direction"]
    for _, index_values in df.groupby(group_columns, dropna=False).groups.items():
        ordered_index = list(
            df.loc[list(index_values)]
            .sort_values(
                by=["transaction_sequence_date", "txn_amount"],
                ascending=[True, False],
                na_position="last",
            )
            .index
        )
        recent_amounts: List[float] = []
        for row_index in ordered_index:
            amount = float(df.at[row_index, "txn_amount"] or 0.0)
            baseline = float(pd.Series(recent_amounts).median()) if recent_amounts else 0.0

            if baseline >= min_baseline_amount:
                df.at[row_index, "recent_median_amount"] = round(baseline, 2)
                df.at[row_index, "amount_vs_recent_median"] = round(amount / baseline, 2) if baseline else pd.NA
                if amount >= baseline * spike_multiplier:
                    df.at[row_index, "is_transaction_spike"] = True
                elif amount <= baseline * drop_ratio:
                    df.at[row_index, "is_transaction_drop"] = True

            if amount > 0:
                recent_amounts.append(amount)
                if len(recent_amounts) > lookback_count:
                    recent_amounts.pop(0)

    return df


def run_fraud_checks(
    transactions: Iterable[Dict[str, Any]],
    high_value_threshold: float = 50000,
    round_threshold: float = 0.25,
) -> Tuple[pd.DataFrame, bool, float]:
    """
    Backward-compatible transaction fraud checks.

    Returns:
        processed_df, is_round_suspicious, round_ratio
    """
    df = pd.DataFrame(list(transactions))
    if df.empty:
        return df, False, 0.0

    if "credit" not in df.columns:
        df["credit"] = 0.0
    if "debit" not in df.columns:
        df["debit"] = 0.0
    if "description" in df.columns:
        df["description"] = df["description"].apply(sanitize_transaction_description)

    df["credit"] = _coerce_numeric_series(df["credit"]).fillna(0.0)
    df["debit"] = _coerce_numeric_series(df["debit"]).fillna(0.0)

    # Transaction delta only (not true running balance reconstruction)
    df["calculated_balance"] = df["credit"] - df["debit"]

    df["is_high_value"] = (
        (df["credit"] > float(high_value_threshold))
        | (df["debit"] > float(high_value_threshold))
    )

    df = detect_duplicate_transactions(df)
    df = detect_rapid_repeat_transactions(df)
    df = detect_transaction_spikes_and_drops(df)
    df, round_suspicious, round_ratio = detect_round_numbers(
        df, threshold=round_threshold
    )

    return df, round_suspicious, round_ratio


# ---------------------------------------------------------------------------
# Known PDF editing software signatures (consumer editors only)
# ---------------------------------------------------------------------------
_EDITOR_SIGNATURES = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"adobe\s*acrobat",
        r"adobe\s*indesign",
        r"foxit",
        r"nitro\s*p",
        r"pdfelement",
        r"wondershare",
        r"phantompdf",
        r"pdf[\-\s]?xchange",
        r"libreoffice",
        r"openoffice",
        r"microsoft\s*word",
        r"canva",
        r"inkscape",
        r"scribus",
        r"smallpdf",
        r"sejda",
        r"pdfsam",
        r"master\s*pdf",
        r"pdf\s*architect",
        r"nuance",
        r"able2extract",
        r"soda\s*pdf",
        r"pdf\s*expert",
        r"preview",
    ]
]


def _is_editor(value: str) -> Optional[str]:
    if not value:
        return None
    for rx in _EDITOR_SIGNATURES:
        match = rx.search(value)
        if match:
            return match.group(0)
    return None


# ---------------------------------------------------------------------------
# Layer 1 — Metadata
# ---------------------------------------------------------------------------
def _layer_metadata(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "metadata"

    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=False)
    except Exception as exc:
        findings.append(_finding(layer, MEDIUM, f"Cannot read PDF metadata: {exc}"))
        return findings

    info = reader.metadata or {}
    creator = str(info.get("/Creator", "") or "").strip()
    producer = str(info.get("/Producer", "") or "").strip()

    for label, value in [("Creator", creator), ("Producer", producer)]:
        editor = _is_editor(value)
        if editor:
            findings.append(
                _finding(
                    layer,
                    HIGH,
                    f"{label} field contains PDF editing software: '{editor}' — banking systems do not produce statements with consumer PDF editors.",
                    {"field": label, "value": value, "matched_editor": editor},
                )
            )

    creation_date = info.get("/CreationDate")
    mod_date = info.get("/ModificationDate")

    def _parse_pdf_date(raw_value: Any) -> Optional[datetime]:
        if not raw_value:
            return None
        s = str(raw_value).strip()
        m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", s)
        if not m:
            return None
        try:
            return datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4) or 0),
                int(m.group(5) or 0),
                int(m.group(6) or 0),
            )
        except Exception:
            return None

    dt_created = _parse_pdf_date(creation_date)
    dt_modified = _parse_pdf_date(mod_date)
    if dt_created and dt_modified:
        gap = abs((dt_modified - dt_created).total_seconds())
        if gap > 86400:
            days = round(gap / 86400, 1)
            severity = HIGH if days > 30 else MEDIUM
            findings.append(
                _finding(
                    layer,
                    severity,
                    f"Creation-to-modification gap: {days} days — genuine bank statements are generated and not later modified.",
                    {
                        "created": str(dt_created),
                        "modified": str(dt_modified),
                        "gap_days": days,
                    },
                )
            )

    try:
        incremental_count = pdf_bytes.count(b"%%EOF")
        if incremental_count > 2:
            severity = HIGH if incremental_count > 3 else MEDIUM
            findings.append(
                _finding(
                    layer,
                    severity,
                    f"PDF has {incremental_count} %%EOF markers (incremental saves) — indicates the document was modified and re-saved multiple times.",
                    {"eof_count": incremental_count},
                )
            )
    except Exception:
        pass

    try:
        raw_creation = str(creation_date or "")
        raw_mod = str(mod_date or "")
        tz_re = re.compile(r"([+-])(\d{2})'(\d{2})'")
        create_tz = tz_re.search(raw_creation)
        mod_tz = tz_re.search(raw_mod)

        if create_tz and mod_tz:
            create_offset = create_tz.group(0)
            mod_offset = mod_tz.group(0)
            if create_offset != mod_offset:
                findings.append(
                    _finding(
                        layer,
                        HIGH,
                        f"Timezone mismatch: CreationDate uses {create_offset} but ModificationDate uses {mod_offset}. Genuine bank PDFs have consistent timezone across both dates.",
                        {"creation_tz": create_offset, "mod_tz": mod_offset},
                    )
                )
            elif create_tz.group(2) != "08":
                findings.append(
                    _finding(
                        layer,
                        MEDIUM,
                        f"CreationDate timezone is {create_offset} — Malaysian bank servers use +08:00. Non-local timezone suggests recreation on a machine outside Malaysia.",
                        {"creation_tz": create_offset},
                    )
                )
    except Exception:
        pass

    try:
        header_chunk = pdf_bytes[:200]
        if b"\r\n" in header_chunk and creator and re.search(r"maybank|itext|jasper|elixir", creator, re.IGNORECASE):
            findings.append(
                _finding(
                    layer,
                    MEDIUM,
                    f"PDF uses Windows CRLF line endings, but the claimed creator '{creator}' normally produces Unix LF endings. This suggests the PDF was re-saved or recreated on Windows.",
                    {"line_endings": "CRLF", "creator": creator},
                )
            )
    except Exception:
        pass

    try:
        keywords = str(info.get("/Keywords", "") or "")
        if keywords.startswith('"') and keywords.endswith('"'):
            findings.append(
                _finding(
                    layer,
                    MEDIUM,
                    "Keywords field is wrapped in double quotes — this is an artifact of PDF re-processing tools. Genuine bank PDFs do not quote keywords.",
                    {"keywords": keywords},
                )
            )
    except Exception:
        pass

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        xmp_raw = doc.metadata or {}
        xmp_creator = (xmp_raw.get("creator") or "").strip()
        xmp_producer = (xmp_raw.get("producer") or "").strip()

        for label, value in [("XMP Creator", xmp_creator), ("XMP Producer", xmp_producer)]:
            editor = _is_editor(value)
            if editor and not any(f.get("detail", {}).get("field") == label for f in findings):
                findings.append(
                    _finding(
                        layer,
                        HIGH,
                        f"{label} contains editing software: '{editor}'.",
                        {"field": label, "value": value},
                    )
                )

        if creator and xmp_creator and creator.lower() != xmp_creator.lower():
            findings.append(
                _finding(
                    layer,
                    MEDIUM,
                    "Metadata inconsistency: /Info Creator differs from XMP Creator.",
                    {"info_creator": creator, "xmp_creator": xmp_creator},
                )
            )
        doc.close()
    except Exception:
        pass

    return findings


# ---------------------------------------------------------------------------
# Layer 2 — Fonts
# ---------------------------------------------------------------------------
_MONEY_RE = re.compile(r"-?[\d,]{1,15}\.\d{2}")


def _layer_fonts(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "fonts"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        findings.append(_finding(layer, MEDIUM, f"Cannot open PDF for font analysis: {exc}"))
        return findings

    all_font_spans: List[Dict[str, Any]] = []
    money_spans: List[Dict[str, Any]] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        except Exception:
            continue

        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue

                    raw_font = span.get("font", "unknown")
                    font_size = round(span.get("size", 0), 1)
                    font_key = f"{raw_font}|{font_size}"
                    font_family = re.sub(
                        r"[-,](Bold|Italic|Light|Medium|Regular|Semibold|Thin|Black|Heavy|Condensed|Oblique|BoldItalic|BoldOblique)+",
                        "",
                        raw_font,
                        flags=re.IGNORECASE,
                    )
                    family_key = f"{font_family}|{font_size}"
                    family_only = font_family

                    span_info = {
                        "page": page_idx + 1,
                        "font": raw_font,
                        "size": font_size,
                        "font_key": font_key,
                        "family_key": family_key,
                        "family_only": family_only,
                        "text": text[:80],
                        "color": span.get("color", 0),
                    }

                    all_font_spans.append(span_info)
                    if _MONEY_RE.search(text):
                        money_spans.append(span_info)

    doc.close()

    if not all_font_spans:
        return findings

    font_counter = Counter(s["font_key"] for s in all_font_spans)

    if money_spans:
        money_family_counter = Counter(s["family_only"] for s in money_spans)
        dominant_money_family = money_family_counter.most_common(1)[0][0]

        money_font_counter = Counter(s["font_key"] for s in money_spans)
        dominant_money_font = money_font_counter.most_common(1)[0][0]

        total_money = len(money_spans)
        dominant_count = money_family_counter[dominant_money_family]
        consistency_pct = round(100.0 * dominant_count / total_money, 1)

        anomalous_font = [s for s in money_spans if s["family_only"] != dominant_money_family]

        money_color_counter = Counter(s["color"] for s in money_spans)
        dominant_money_color = money_color_counter.most_common(1)[0][0]
        anomalous_color = [s for s in money_spans if s["color"] != dominant_money_color]

        anomalous_texts = {s["text"] for s in anomalous_font} | {s["text"] for s in anomalous_color}
        all_anomalous = [s for s in money_spans if s["text"] in anomalous_texts]

        seen = set()
        deduped_anomalous: List[Dict[str, Any]] = []
        for s in all_anomalous:
            key = (s["page"], s["text"])
            if key not in seen:
                seen.add(key)
                deduped_anomalous.append(s)

        if deduped_anomalous and consistency_pct < 85:
            severity = HIGH if len(deduped_anomalous) <= 10 else MEDIUM
            reasons = []
            if anomalous_font:
                reasons.append(f"{len(anomalous_font)} with different font/size")
            if anomalous_color:
                reasons.append(f"{len(anomalous_color)} with different color")

            findings.append(
                _finding(
                    layer,
                    severity,
                    f"SUSPICIOUS AMOUNTS DETECTED: {len(deduped_anomalous)} of {total_money} monetary amounts are anomalous ({', '.join(reasons)}). Font consistency: {consistency_pct}%. Dominant money font: {dominant_money_font}. When someone edits an amount in a PDF editor, the replacement text almost always uses a slightly different font, size, or color.",
                    {
                        "dominant_money_font": dominant_money_font,
                        "dominant_money_color": dominant_money_color,
                        "font_consistency_pct": consistency_pct,
                        "total_money_spans": total_money,
                        "anomalous_count": len(deduped_anomalous),
                        "anomalous_amounts": [
                            {
                                "page": s["page"],
                                "text": s["text"],
                                "font": s["font"],
                                "size": s["size"],
                                "color": s["color"],
                                "font_matches_dominant": s["font_key"] == dominant_money_font,
                                "color_matches_dominant": s["color"] == dominant_money_color,
                            }
                            for s in deduped_anomalous[:20]
                        ],
                    },
                )
            )
        else:
            findings.append(
                _finding(
                    layer,
                    LOW,
                    f"Font consistency: {consistency_pct}% — all {total_money} monetary amounts use the same font ({dominant_money_font}) and color. No anomalies detected.",
                    {
                        "dominant_money_font": dominant_money_font,
                        "font_consistency_pct": consistency_pct,
                        "total_money_spans": total_money,
                        "anomalous_count": 0,
                        "status": "CLEAN",
                    },
                )
            )

    distinct_fonts = len(font_counter)
    if distinct_fonts > 8:
        findings.append(
            _finding(
                layer,
                LOW,
                f"Document uses {distinct_fonts} distinct font/size combinations — bank statements typically use 2-5.",
                {"distinct_fonts": distinct_fonts},
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Layer 3 — Text layers
# ---------------------------------------------------------------------------
def _layer_text_layers(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "text_layers"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        findings.append(_finding(layer, MEDIUM, f"Cannot open PDF for text layer analysis: {exc}"))
        return findings

    pages_with_overlap: List[Dict[str, Any]] = []
    pages_with_invisible: List[int] = []
    pages_with_multi_stream: List[int] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num = page_idx + 1

        try:
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            text_positions: List[Tuple[float, float, float, float, str]] = []

            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text or len(text) < 3:
                            continue
                        bbox = span.get("bbox") or line.get("bbox")
                        if bbox:
                            text_positions.append((bbox[0], bbox[1], bbox[2], bbox[3], text))

            suspicious_overlaps: List[Tuple[str, str]] = []
            for i in range(len(text_positions)):
                for j in range(i + 1, len(text_positions)):
                    ax0, ay0, ax1, ay1, text_a = text_positions[i]
                    bx0, by0, bx1, by1, text_b = text_positions[j]

                    overlap_x = max(0, min(ax1, bx1) - max(ax0, bx0))
                    overlap_y = max(0, min(ay1, by1) - max(ay0, by0))

                    width_a = ax1 - ax0
                    width_b = bx1 - bx0
                    height_a = ay1 - ay0
                    height_b = by1 - by0

                    min_width = min(width_a, width_b) if min(width_a, width_b) > 0 else 1
                    min_height = min(height_a, height_b) if min(height_a, height_b) > 0 else 1

                    if overlap_x / min_width < 0.3 or overlap_y / min_height < 0.3:
                        continue

                    norm_a = re.sub(r"\s+", "", text_a.lower())
                    norm_b = re.sub(r"\s+", "", text_b.lower())
                    if norm_a == norm_b:
                        continue
                    if norm_a in norm_b or norm_b in norm_a:
                        continue

                    suspicious_overlaps.append((text_a[:40], text_b[:40]))
                    if len(suspicious_overlaps) >= 3:
                        break
                if len(suspicious_overlaps) >= 3:
                    break

            if suspicious_overlaps:
                pages_with_overlap.append(
                    {
                        "page": page_num,
                        "examples": [{"text_a": a, "text_b": b} for a, b in suspicious_overlaps[:3]],
                    }
                )
        except Exception:
            pass

        try:
            invisible_money_spans = 0
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                        color = span.get("color", 0)
                        size = span.get("size", 12)
                        if (color == 16777215 or size < 0.5) and _MONEY_RE.search(text) and len(text) < 20:
                            invisible_money_spans += 1
            if invisible_money_spans >= 2 and page_num not in pages_with_invisible:
                pages_with_invisible.append(page_num)
        except Exception:
            pass

        try:
            xref = page.xref
            page_obj = doc.xref_object(xref)
            contents_count = page_obj.count("/Contents")
            if contents_count > 1:
                pages_with_multi_stream.append(page_num)
            elif "/Contents" in page_obj:
                match = re.search(r"/Contents\s*\[([^\]]+)\]", page_obj)
                if match:
                    refs = re.findall(r"\d+\s+\d+\s+R", match.group(1))
                    if len(refs) > 1:
                        pages_with_multi_stream.append(page_num)
        except Exception:
            pass

    doc.close()

    if pages_with_overlap:
        findings.append(
            _finding(
                layer,
                HIGH,
                f"Overlapping DIFFERENT text detected on {len(pages_with_overlap)} page(s) — text with different content placed over existing content is a strong sign of PDF editing.",
                {"pages": pages_with_overlap, "page_numbers": [p['page'] for p in pages_with_overlap]},
            )
        )

    if pages_with_invisible:
        findings.append(
            _finding(
                layer,
                HIGH,
                f"Invisible/white text found on {len(pages_with_invisible)} page(s) — hidden original values beneath edited replacement text.",
                {"pages": pages_with_invisible},
            )
        )

    if pages_with_multi_stream:
        findings.append(
            _finding(
                layer,
                LOW,
                f"Multiple content streams on {len(pages_with_multi_stream)} page(s) — common in bank statements with layered layouts.",
                {"pages": pages_with_multi_stream},
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Layer 4 — Visual
# ---------------------------------------------------------------------------
def _layer_visual(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "visual"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        findings.append(_finding(layer, MEDIUM, f"Cannot open PDF for visual analysis: {exc}"))
        return findings

    if len(doc) == 0:
        doc.close()
        return findings

    dimensions: List[Tuple[int, float, float]] = []
    for page_idx in range(len(doc)):
        rect = doc[page_idx].rect
        dimensions.append((page_idx + 1, round(rect.width, 1), round(rect.height, 1)))

    dim_counter = Counter((d[1], d[2]) for d in dimensions)
    if len(dim_counter) > 1:
        dominant_dim = dim_counter.most_common(1)[0][0]
        mismatched = [d for d in dimensions if (d[1], d[2]) != dominant_dim]
        findings.append(
            _finding(
                layer,
                MEDIUM,
                f"Page dimension inconsistency: {len(mismatched)} page(s) differ from the dominant size ({dominant_dim[0]}x{dominant_dim[1]}pt).",
                {
                    "dominant_dimensions": {"width": dominant_dim[0], "height": dominant_dim[1]},
                    "mismatched_pages": [
                        {"page": d[0], "width": d[1], "height": d[2]} for d in mismatched
                    ],
                },
            )
        )

    page_hashes: List[Dict[str, Any]] = []
    try:
        for page_idx in range(len(doc)):
            pix = doc[page_idx].get_pixmap(dpi=72)
            digest = hashlib.sha256(pix.samples).hexdigest()[:16]
            page_hashes.append({"page": page_idx + 1, "hash": digest})
    except Exception:
        pass

    if page_hashes:
        findings.append(
            _finding(
                layer,
                LOW,
                "Page render hashes computed for cross-document comparison.",
                {"page_hashes": page_hashes},
            )
        )

    doc.close()
    return findings


# ---------------------------------------------------------------------------
# Layer 5 — Cross-validation
# ---------------------------------------------------------------------------
def _normalize_for_comparison(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _layer_cross_validation(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "cross_validation"

    fitz_texts: Dict[int, str] = {}
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for i in range(len(doc)):
            fitz_texts[i + 1] = _normalize_for_comparison(doc[i].get_text())
        doc.close()
    except Exception as exc:
        findings.append(_finding(layer, LOW, f"PyMuPDF extraction failed: {exc}"))
        return findings

    plumber_texts: Dict[int, str] = {}
    try:
        pdf = pdfplumber.open(BytesIO(pdf_bytes))
        for i, page in enumerate(pdf.pages):
            plumber_texts[i + 1] = _normalize_for_comparison(page.extract_text())
        pdf.close()
    except Exception as exc:
        findings.append(_finding(layer, LOW, f"pdfplumber extraction failed: {exc}"))
        return findings

    disagreement_pages: List[Dict[str, Any]] = []
    all_pages = sorted(set(fitz_texts.keys()) | set(plumber_texts.keys()))

    for page_num in all_pages:
        ft = fitz_texts.get(page_num, "")
        pt = plumber_texts.get(page_num, "")
        if not ft and not pt:
            continue
        if ft == pt:
            continue

        len_ft = len(ft)
        len_pt = len(pt)

        if len_ft == 0 or len_pt == 0:
            if abs(len_ft - len_pt) > 20:
                disagreement_pages.append(
                    {
                        "page": page_num,
                        "fitz_len": len_ft,
                        "plumber_len": len_pt,
                        "reason": "One engine extracted text, the other found none",
                    }
                )
            continue

        ratio = min(len_ft, len_pt) / max(len_ft, len_pt)
        if ratio < 0.85:
            disagreement_pages.append(
                {
                    "page": page_num,
                    "fitz_len": len_ft,
                    "plumber_len": len_pt,
                    "length_ratio": round(ratio, 3),
                    "reason": f"Text length ratio {round(ratio, 3)} — significant extraction disagreement",
                }
            )

    if disagreement_pages:
        severity = HIGH if len(disagreement_pages) >= 3 else MEDIUM
        findings.append(
            _finding(
                layer,
                severity,
                f"Text extraction disagreement on {len(disagreement_pages)} page(s) between PyMuPDF and pdfplumber — may indicate hidden layers, non-standard encoding, or injected content.",
                {"pages": disagreement_pages},
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Layer 6 — Bank profile matching
# ---------------------------------------------------------------------------
_BANK_PROFILES: Dict[str, List[Dict[str, Any]]] = {
    "maybank": [
        {
            "name": "Maybank2u.com (conventional/Islamic)",
            "creator_re": re.compile(r"maybank2u\.com", re.IGNORECASE),
            "producer_re": re.compile(r"itext\s*2\.1\.3", re.IGNORECASE),
            "expected_fonts": {"Tahoma", "NSimSun", "MicrosoftSansSerif"},
            "expected_pdf_version": "1.4",
            "expected_font_type": "Type0",
            "max_fonts_per_page": 4,
            "forbidden_fonts": {"Calibri", "TimesNewRomanPSMT"},
        },
        {
            "name": "Maybank Islamic (Elixir/iText)",
            "creator_re": re.compile(r"elixir\s*report", re.IGNORECASE),
            "producer_re": re.compile(r"itext\s*2\.1\.7", re.IGNORECASE),
            "expected_fonts": {"ArialUnicodeMS"},
        },
    ],
    "cimb": [
        {
            "name": "CIMB JasperReports",
            "creator_re": re.compile(r"jasperreports", re.IGNORECASE),
            "producer_re": re.compile(r"itext\s*1\.4", re.IGNORECASE),
            "expected_fonts": {"Helvetica"},
        },
    ],
    "public bank": [
        {
            "name": "Public Bank iTextSharp",
            "creator_re": re.compile(r".*", re.IGNORECASE),
            "producer_re": re.compile(r"itextsharp", re.IGNORECASE),
            "expected_fonts": {"Helvetica"},
        },
    ],
    "rhb": [
        {
            "name": "RHB JasperReports",
            "creator_re": re.compile(r"jasperreports", re.IGNORECASE),
            "producer_re": re.compile(r"itext", re.IGNORECASE),
            "expected_fonts": {"Helvetica"},
        },
        {
            "name": "RHB PDFium",
            "creator_re": re.compile(r".*"),
            "producer_re": re.compile(r"pdfium", re.IGNORECASE),
            "expected_fonts": {"Calibri"},
        },
        {
            "name": "RHB Vault Rendering",
            "creator_re": re.compile(r"vault", re.IGNORECASE),
            "producer_re": re.compile(r"vault", re.IGNORECASE),
            "expected_fonts": set(),
        },
    ],
    "hong leong": [
        {
            "name": "Hong Leong iText (Dax fonts)",
            "creator_re": re.compile(r"5\.7", re.IGNORECASE),
            "producer_re": re.compile(r"itext\s*1\.4", re.IGNORECASE),
            "expected_fonts": {"Dax-Bold", "Dax-Regular"},
        },
    ],
    "bank rakyat": [
        {
            "name": "Bank Rakyat PoDoFo",
            "creator_re": re.compile(r".*"),
            "producer_re": re.compile(r"podofo", re.IGNORECASE),
            "expected_fonts": {"Helvetica"},
        },
        {
            "name": "Bank Rakyat iText",
            "creator_re": re.compile(r".*"),
            "producer_re": re.compile(r"itext\s*2\.1", re.IGNORECASE),
            "expected_fonts": {"Helvetica"},
        },
    ],
    "bank islam": [
        {"name": "Bank Islam openhtmltopdf", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"openhtmltopdf", re.IGNORECASE), "expected_fonts": set()},
        {"name": "Bank Islam iText", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"itext\s*2\.1", re.IGNORECASE), "expected_fonts": set()},
        {"name": "Bank Islam PoDoFo", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"podofo", re.IGNORECASE), "expected_fonts": set()},
        {"name": "Bank Islam PDFium", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"pdfium", re.IGNORECASE), "expected_fonts": set()},
        {"name": "Bank Islam Acrobat Distiller", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"acrobat\s*distiller", re.IGNORECASE), "expected_fonts": set()},
        {"name": "Bank Islam Microsoft Print to PDF", "creator_re": re.compile(r".*"), "producer_re": re.compile(r"microsoft.*print\s*to\s*pdf", re.IGNORECASE), "expected_fonts": set()},
    ],
    "affin": [
        {
            "name": "Affin Bank (image-based)",
            "creator_re": re.compile(r".*"),
            "producer_re": re.compile(r".*"),
            "expected_fonts": set(),
        },
    ],
    "alliance": [
        {
            "name": "Alliance Bank Quadient/Inspire",
            "creator_re": re.compile(r"quadient|inspire", re.IGNORECASE),
            "producer_re": re.compile(r"quadient|inspire", re.IGNORECASE),
            "expected_fonts": {"ArialMT"},
        },
    ],
    "ambank": [
        {
            "name": "AmBank Streamline Pdfgen",
            "creator_re": re.compile(r"streamline\s*pdfgen", re.IGNORECASE),
            "producer_re": re.compile(r"compugr", re.IGNORECASE),
            "expected_fonts": set(),
        },
        {
            "name": "AmBank omsgen",
            "creator_re": re.compile(r"omsgen", re.IGNORECASE),
            "producer_re": re.compile(r".*"),
            "expected_fonts": set(),
        },
    ],
    "agrobank": [
        {
            "name": "AgroBank Microsoft Word",
            "creator_re": re.compile(r"microsoft.*word", re.IGNORECASE),
            "producer_re": re.compile(r".*"),
            "expected_fonts": {"Calibri", "Tahoma"},
        },
        {
            "name": "AgroBank WPS Writer",
            "creator_re": re.compile(r"wps\s*writer", re.IGNORECASE),
            "producer_re": re.compile(r".*"),
            "expected_fonts": set(),
        },
    ],
    "bsn": [
        {
            "name": "BSN e-Statement",
            "creator_re": re.compile(r"bsn|jasper", re.IGNORECASE),
            "producer_re": re.compile(r"itext|jasper", re.IGNORECASE),
            "expected_fonts": set(),
        },
    ],
    "muamalat": [
        {
            "name": "Bank Muamalat PlanetPress",
            "creator_re": re.compile(r".*"),
            "producer_re": re.compile(r"planetpress", re.IGNORECASE),
            "expected_fonts": {"Calibri-Italic", "CourierNewPS-BoldMT", "ArialNarrow"},
        },
    ],
    "ocbc": [
        {
            "name": "OCBC Streamline Pdfgen",
            "creator_re": re.compile(r"streamline\s*pdfgen", re.IGNORECASE),
            "producer_re": re.compile(r"compugr", re.IGNORECASE),
            "expected_fonts": {"Arial"},
        },
    ],
    "uob": [
        {
            "name": "UOB JasperReports",
            "creator_re": re.compile(r"jasperreports", re.IGNORECASE),
            "producer_re": re.compile(r"itext\s*2\.1", re.IGNORECASE),
            "expected_fonts": {"Helvetica", "OpenSans", "ArialUnicodeMS"},
        },
    ],
}

_NON_BANK_PRODUCERS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"adobe\s*scan",
        r"camscanner",
        r"genius\s*scan",
        r"microsoft\s*lens",
        r"google\s*drive",
        r"scanbot",
        r"tiny\s*scanner",
        r"clear\s*scanner",
        r"scanner\s*pro",
        r"adobe\s*photoshop",
        r"gimp",
        r"paint",
        r"canva",
    ]
]


def _detect_bank_from_text(pdf_bytes: bytes) -> Optional[str]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for i in range(min(2, len(doc))):
            text += doc[i].get_text().lower()
        doc.close()
    except Exception:
        return None

    bank_keywords = [
        ("maybank", ["maybank islamic", "maybankislamicberhad", "maybank2u"]),
        ("hong leong", ["hong leong bank", "hong leong islamic"]),
        ("public bank", ["public bank", "public berhad"]),
        ("bank rakyat", ["bank rakyat"]),
        ("bank islam", ["bank islam"]),
        ("alliance", ["alliance bank"]),
        ("agrobank", ["agrobank", "agro bank"]),
        ("bsn", ["bank simpanan nasional"]),
        ("muamalat", ["muamalat"]),
        ("maybank", ["maybank", "malayan banking"]),
        ("affin", ["affin bank", "affin islamic"]),
        ("ambank", ["ambank", "ammb holdings"]),
        ("cimb", ["cimb bank", "cimb islamic", "cimb group"]),
        ("rhb", ["rhb bank", "rhb islamic"]),
        ("ocbc", ["ocbc bank", "ocbc al-amin"]),
        ("uob", ["uob malaysia"]),
    ]

    for bank, keywords in bank_keywords:
        for kw in keywords:
            if kw in text:
                return bank

    short_keywords = [
        ("hong leong", ["hong leong"]),
        ("cimb", ["cimb"]),
        ("rhb", ["rhb "]),
        ("ocbc", ["ocbc"]),
        ("uob", ["uob "]),
        ("bsn", [" bsn "]),
        ("affin", ["affin"]),
    ]
    for bank, keywords in short_keywords:
        for kw in keywords:
            if kw in text:
                return bank

    return None


def _extract_font_names(pdf_bytes: bytes) -> set:
    fonts = set()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            for font in page.get_fonts():
                name = font[3] or ""
                if "+" in name:
                    name = name.split("+", 1)[1]
                if name:
                    fonts.add(name)
        doc.close()
    except Exception:
        pass
    return fonts


def _layer_bank_profile(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "bank_profile"

    bank = _detect_bank_from_text(pdf_bytes)
    if not bank:
        findings.append(_finding(layer, LOW, "Could not identify bank from text content."))
        return findings

    profiles = _BANK_PROFILES.get(bank, [])
    if not profiles:
        findings.append(_finding(layer, LOW, f"No profile database for '{bank}' yet."))
        return findings

    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=False)
        info = reader.metadata or {}
        creator = str(info.get("/Creator", "") or "").strip()
        producer = str(info.get("/Producer", "") or "").strip()
    except Exception:
        creator = ""
        producer = ""

    pdf_fonts = _extract_font_names(pdf_bytes)

    for rx in _NON_BANK_PRODUCERS:
        for label, value in [("Creator", creator), ("Producer", producer)]:
            if rx.search(value):
                findings.append(
                    _finding(
                        layer,
                        HIGH,
                        f"PDF was produced by scanning/consumer software: '{value}' — genuine {bank.title()} statements are server-generated, not scanned or recreated.",
                        {"field": label, "value": value, "detected_bank": bank},
                    )
                )

    matched_profile = None
    for profile in profiles:
        creator_ok = profile["creator_re"].search(creator) if creator else False
        if creator_ok:
            matched_profile = profile
            break
    if not matched_profile:
        for profile in profiles:
            producer_ok = profile["producer_re"].search(producer) if producer else False
            if producer_ok:
                matched_profile = profile
                break

    if matched_profile:
        expected = matched_profile.get("expected_fonts", set())
        if expected and pdf_fonts:
            missing = expected - pdf_fonts
            extra = pdf_fonts - expected
            if missing:
                findings.append(
                    _finding(
                        layer,
                        MEDIUM,
                        f"Font mismatch vs {matched_profile['name']} profile: missing expected fonts {missing}.",
                        {
                            "profile": matched_profile["name"],
                            "missing": sorted(missing),
                            "extra": sorted(extra),
                            "found": sorted(pdf_fonts),
                        },
                    )
                )
            if not missing:
                findings.append(
                    _finding(
                        layer,
                        LOW,
                        f"PDF matches known {matched_profile['name']} profile (creator/producer and fonts consistent).",
                        {"profile": matched_profile["name"], "status": "MATCH"},
                    )
                )

        expected_version = matched_profile.get("expected_pdf_version")
        if expected_version:
            try:
                version_line = pdf_bytes[:20].decode("latin-1", errors="replace")
                match = re.search(r"%PDF-(\d+\.\d+)", version_line)
                if match and match.group(1) != expected_version:
                    findings.append(
                        _finding(
                            layer,
                            HIGH,
                            f"PDF version {match.group(1)} does not match expected {expected_version} for {matched_profile['name']}.",
                            {
                                "expected_version": expected_version,
                                "actual_version": match.group(1),
                                "profile": matched_profile["name"],
                            },
                        )
                    )
            except Exception:
                pass

        max_fonts = matched_profile.get("max_fonts_per_page")
        if max_fonts:
            try:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for page_idx in range(min(2, len(doc))):
                    page_fonts = doc[page_idx].get_fonts()
                    if len(page_fonts) > max_fonts:
                        findings.append(
                            _finding(
                                layer,
                                HIGH,
                                f"Page {page_idx + 1} has {len(page_fonts)} fonts (expected max {max_fonts} for {matched_profile['name']}).",
                                {
                                    "page": page_idx + 1,
                                    "font_count": len(page_fonts),
                                    "expected_max": max_fonts,
                                    "fonts": [f[3] for f in page_fonts],
                                },
                            )
                        )
                        break
                doc.close()
            except Exception:
                pass

        forbidden = matched_profile.get("forbidden_fonts", set())
        if forbidden and pdf_fonts:
            found_forbidden = forbidden & pdf_fonts
            if found_forbidden:
                findings.append(
                    _finding(
                        layer,
                        HIGH,
                        f"Foreign fonts detected: {sorted(found_forbidden)}. Genuine {bank.title()} statements never use these fonts.",
                        {
                            "forbidden_fonts": sorted(found_forbidden),
                            "all_fonts": sorted(pdf_fonts),
                            "profile": matched_profile["name"],
                        },
                    )
                )

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            all_prefixes: List[str] = []
            for page in doc:
                for font in page.get_fonts():
                    name = font[3] or ""
                    if "+" in name:
                        prefix = name.split("+")[0]
                        if len(prefix) == 6 and prefix.isalpha():
                            all_prefixes.append(prefix)
            doc.close()

            if len(all_prefixes) >= 4:
                unique_prefixes = set(all_prefixes)
                if len(unique_prefixes) >= 3:
                    sorted_prefixes = sorted(unique_prefixes)
                    common_root = sorted_prefixes[0][:3]
                    sharing_root = sum(1 for p in sorted_prefixes if p.startswith(common_root))
                    if sharing_root >= 3 and sharing_root == len(unique_prefixes):
                        findings.append(
                            _finding(
                                layer,
                                MEDIUM,
                                f"Font subset prefixes share sequential pattern (all start with '{common_root}'): {sorted_prefixes}.",
                                {"prefixes": sorted_prefixes, "common_root": common_root},
                            )
                        )
        except Exception:
            pass

        try:
            version_line = pdf_bytes[:20].decode("latin-1", errors="replace")
            match = re.search(r"%PDF-(\d+\.\d+)", version_line)
            expected_version = matched_profile.get("expected_pdf_version")
            if match and expected_version and match.group(1) != expected_version:
                reader2 = PdfReader(BytesIO(pdf_bytes), strict=False)
                if reader2.is_encrypted:
                    encrypt_dict = reader2.trailer.get("/Encrypt")
                    if encrypt_dict:
                        v_val = encrypt_dict.get("/V")
                        r_val = encrypt_dict.get("/R")
                        if v_val and int(str(v_val)) >= 5:
                            findings.append(
                                _finding(
                                    layer,
                                    HIGH,
                                    f"Encryption upgraded to V{v_val}R{r_val} but genuine {bank.title()} uses PDF {expected_version}.",
                                    {
                                        "encryption_v": str(v_val),
                                        "encryption_r": str(r_val),
                                        "pdf_version": match.group(1),
                                        "expected_version": expected_version,
                                    },
                                )
                            )
        except Exception:
            pass

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            n_pages = len(doc)
            doc.close()
            if n_pages > 0:
                kb_per_page = len(pdf_bytes) / n_pages / 1024
                if matched_profile.get("expected_pdf_version") == "1.4" and kb_per_page > 45:
                    findings.append(
                        _finding(
                            layer,
                            MEDIUM,
                            f"File size {round(kb_per_page)}KB/page is unusually large for {matched_profile['name']} (expected ~25-30KB/page).",
                            {"kb_per_page": round(kb_per_page, 1), "expected_kb_per_page": "25-30"},
                        )
                    )
        except Exception:
            pass
    else:
        if creator or producer:
            severity = HIGH if (creator and not any(p["creator_re"].search(creator) for p in profiles)) else MEDIUM
            findings.append(
                _finding(
                    layer,
                    severity,
                    f"PDF creator/producer does NOT match any known {bank.title()} generation profile. Creator='{creator}', Producer='{producer}'.",
                    {
                        "detected_bank": bank,
                        "creator": creator,
                        "producer": producer,
                        "known_profiles": [p["name"] for p in profiles],
                        "found_fonts": sorted(pdf_fonts),
                    },
                )
            )
        else:
            findings.append(
                _finding(
                    layer,
                    MEDIUM,
                    f"PDF has no creator/producer metadata — genuine {bank.title()} statements often include generation metadata.",
                    {"detected_bank": bank},
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Layer 7 — Structural anomalies
# ---------------------------------------------------------------------------
def _layer_structural(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "structural"

    file_size = len(pdf_bytes)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        findings.append(_finding(layer, MEDIUM, f"Cannot open PDF: {exc}"))
        return findings

    num_pages = len(doc)
    if num_pages == 0:
        doc.close()
        return findings

    size_per_page = file_size / num_pages
    threshold = 500_000 if num_pages > 1 else 800_000
    if size_per_page > threshold:
        findings.append(
            _finding(
                layer,
                MEDIUM,
                f"Unusually large file: {round(file_size / 1024)}KB for {num_pages} pages ({round(size_per_page / 1024)}KB/page).",
                {
                    "file_size_kb": round(file_size / 1024),
                    "pages": num_pages,
                    "kb_per_page": round(size_per_page / 1024),
                },
            )
        )

    try:
        version_line = pdf_bytes[:20].decode("latin-1", errors="replace")
        version_match = re.search(r"%PDF-(\d+\.\d+)", version_line)
        if version_match:
            findings.append(
                _finding(
                    layer,
                    LOW,
                    f"PDF version: {version_match.group(1)}",
                    {"pdf_version": version_match.group(1)},
                )
            )
    except Exception:
        pass

    structural_min_len = 40
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        page_num = page_idx + 1
        page_width = page.rect.width

        try:
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            text_lines: List[Tuple[str, float, float]] = []
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    bbox = line.get("bbox", [0, 0, 0, 0])
                    line_width = bbox[2] - bbox[0]
                    if len(line_text) >= structural_min_len and line_width > page_width * 0.5:
                        text_lines.append((line_text, bbox[1], line_width))

            text_counter = Counter(t[0] for t in text_lines)
            for text, count in text_counter.items():
                if count >= 2:
                    positions = [t[1] for t in text_lines if t[0] == text]
                    pos_spread = max(positions) - min(positions)
                    if pos_spread > 200:
                        findings.append(
                            _finding(
                                layer,
                                HIGH,
                                f"Duplicate structural text on page {page_num}: '{text[:60]}' appears {count} times at different sections.",
                                {
                                    "page": page_num,
                                    "text": text[:80],
                                    "count": count,
                                    "position_spread_pt": round(pos_spread),
                                },
                            )
                        )
                        break
        except Exception:
            pass

    text_sparse_pages: List[int] = []
    for page_idx in range(len(doc)):
        page = doc[page_idx]
        images = page.get_images()
        text = page.get_text().strip()
        if images and len(text) < 50:
            text_sparse_pages.append(page_idx + 1)

    if text_sparse_pages:
        findings.append(
            _finding(
                layer,
                MEDIUM,
                f"Image-only pages detected (pages {text_sparse_pages[:5]}) — pages contain images but almost no extractable text.",
                {"pages": text_sparse_pages},
            )
        )

    doc.close()
    return findings


# ---------------------------------------------------------------------------
# Layer 8 — Arithmetic validation
# ---------------------------------------------------------------------------
_AMOUNT_PATTERN = r"(?:[\d,]+\.\d{2}|\.\d{2})"

_BALANCE_RE = re.compile(
    rf"(BEGINNING\s+BALANCE|ENDING\s+BALANCE|LEDGER\s+BALANCE|TOTAL\s+DEBIT|TOTAL\s+CREDIT)\s*:?\s*({_AMOUNT_PATTERN})",
    re.IGNORECASE,
)

_TXN_LINE_RE = re.compile(
    rf"(\d{{1,2}}/\d{{2}})\s+.+?\s+({_AMOUNT_PATTERN})([+-])\s+({_AMOUNT_PATTERN})\s*$",
    re.MULTILINE,
)


def _parse_amount(value: str) -> float:
    cleaned = str(value or "").replace(",", "").strip()
    if cleaned.startswith("."):
        cleaned = f"0{cleaned}"
    return float(cleaned)


def _layer_arithmetic(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    layer = "arithmetic"

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        findings.append(_finding(layer, LOW, f"Cannot open PDF: {exc}"))
        return findings

    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    summaries: Dict[str, float] = {}
    for match in _BALANCE_RE.finditer(full_text):
        key = match.group(1).upper().strip()
        summaries[key] = _parse_amount(match.group(2))

    beginning = summaries.get("BEGINNING BALANCE")
    ending = summaries.get("ENDING BALANCE")
    total_debit = summaries.get("TOTAL DEBIT")
    total_credit = summaries.get("TOTAL CREDIT")

    if all(v is not None for v in [beginning, ending, total_debit, total_credit]):
        expected_ending = round(beginning + total_credit - total_debit, 2)
        if abs(expected_ending - ending) > 0.01:
            findings.append(
                _finding(
                    layer,
                    HIGH,
                    f"Balance arithmetic failure: Beginning ({beginning:,.2f}) + Total Credit ({total_credit:,.2f}) - Total Debit ({total_debit:,.2f}) = {expected_ending:,.2f}, but Ending Balance is {ending:,.2f}.",
                    {
                        "beginning": beginning,
                        "total_credit": total_credit,
                        "total_debit": total_debit,
                        "expected_ending": expected_ending,
                        "actual_ending": ending,
                        "difference": round(abs(expected_ending - ending), 2),
                    },
                )
            )
        else:
            findings.append(
                _finding(
                    layer,
                    LOW,
                    "Balance arithmetic verified: Beginning + Credits - Debits = Ending Balance.",
                    {
                        "beginning": beginning,
                        "ending": ending,
                        "total_debit": total_debit,
                        "total_credit": total_credit,
                        "status": "VERIFIED",
                    },
                )
            )

    txn_amounts: List[Tuple[float, str, float]] = []
    for match in _TXN_LINE_RE.finditer(full_text):
        try:
            amount = _parse_amount(match.group(2))
            sign = match.group(3)
            balance = _parse_amount(match.group(4))
            txn_amounts.append((amount, sign, balance))
        except Exception:
            continue

    if len(txn_amounts) >= 2 and beginning is not None:
        running = beginning
        errors: List[Dict[str, Any]] = []
        for i, (amount, sign, expected_balance) in enumerate(txn_amounts):
            running = round(running - amount, 2) if sign == "-" else round(running + amount, 2)
            if abs(running - expected_balance) > 0.01:
                errors.append(
                    {
                        "txn_index": i + 1,
                        "expected": running,
                        "shown": expected_balance,
                        "diff": round(abs(running - expected_balance), 2),
                    }
                )
                running = expected_balance

        if errors:
            findings.append(
                _finding(
                    layer,
                    HIGH,
                    f"Running balance errors in {len(errors)} transaction(s): the shown balance does not match cumulative arithmetic.",
                    {"error_count": len(errors), "first_errors": errors[:5]},
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Public API — full PDF analysis
# ---------------------------------------------------------------------------
def analyze_pdf(pdf_bytes: bytes, filename: str = "") -> Dict[str, Any]:
    """
    Run all 8 detection layers on a PDF.

    Returns:
        {
          "filename": str,
          "overall_risk": "LOW" | "MEDIUM" | "HIGH",
          "layer_results": {...},
          "all_findings": [...],
          "finding_count": int,
          "high_count": int,
          "medium_count": int,
          "low_count": int,
        }
    """
    all_findings: List[Dict[str, Any]] = []
    layer_results: Dict[str, List[Dict[str, Any]]] = {}

    layer_functions = [
        ("metadata", _layer_metadata),
        ("fonts", _layer_fonts),
        ("text_layers", _layer_text_layers),
        ("visual", _layer_visual),
        ("cross_validation", _layer_cross_validation),
        ("bank_profile", _layer_bank_profile),
        ("structural", _layer_structural),
        ("arithmetic", _layer_arithmetic),
    ]

    for layer_name, layer_fn in layer_functions:
        try:
            results = layer_fn(pdf_bytes)
        except Exception as exc:
            results = [_finding(layer_name, LOW, f"Layer failed: {exc}")]
        layer_results[layer_name] = results
        all_findings.extend(results)

    high_count = sum(1 for f in all_findings if f.get("severity") == HIGH)
    medium_count = sum(1 for f in all_findings if f.get("severity") == MEDIUM)
    low_count = sum(1 for f in all_findings if f.get("severity") == LOW)

    return {
        "filename": filename,
        "overall_risk": _worst_severity(all_findings),
        "layer_results": layer_results,
        "all_findings": all_findings,
        "finding_count": len(all_findings),
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
    }


def _extract_profile_fingerprint(pdf_bytes: bytes) -> Dict[str, Any]:
    fp: Dict[str, Any] = {}
    try:
        reader = PdfReader(BytesIO(pdf_bytes), strict=False)
        info = reader.metadata or {}
        fp["creator"] = str(info.get("/Creator", "") or "").strip()
        fp["producer"] = str(info.get("/Producer", "") or "").strip()
    except Exception:
        fp["creator"] = ""
        fp["producer"] = ""

    fp["fonts"] = sorted(_extract_font_names(pdf_bytes))
    fp["bank"] = _detect_bank_from_text(pdf_bytes)

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        fp["pages"] = len(doc)
        if len(doc) > 0:
            rect = doc[0].rect
            fp["page_size"] = f"{round(rect.width, 1)}x{round(rect.height, 1)}"
        doc.close()
    except Exception:
        fp["pages"] = 0
        fp["page_size"] = ""

    fp["file_size"] = len(pdf_bytes)
    return fp


def compare_batch(
    results: Dict[str, Dict[str, Any]],
    pdf_data: Dict[str, bytes],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Cross-file comparison for multi-statement uploads.

    Returns:
        {filename: [extra findings]}
    """
    if len(pdf_data) < 2:
        return {}

    fingerprints: Dict[str, Dict[str, Any]] = {
        fname: _extract_profile_fingerprint(raw)
        for fname, raw in pdf_data.items()
    }

    bank_groups: Dict[Optional[str], List[str]] = {}
    for fname, fp in fingerprints.items():
        bank_groups.setdefault(fp.get("bank"), []).append(fname)

    extra_findings: Dict[str, List[Dict[str, Any]]] = {f: [] for f in pdf_data}

    for bank, filenames in bank_groups.items():
        if bank is None or len(filenames) < 2:
            continue

        creators = Counter(fingerprints[f]["creator"] for f in filenames)
        producers = Counter(fingerprints[f]["producer"] for f in filenames)
        font_sets = Counter(tuple(fingerprints[f]["fonts"]) for f in filenames)

        dominant_creator = creators.most_common(1)[0][0] if creators else ""
        dominant_producer = producers.most_common(1)[0][0] if producers else ""
        dominant_fonts = font_sets.most_common(1)[0][0] if font_sets else ()

        for fname in filenames:
            fp = fingerprints[fname]
            mismatches: List[str] = []

            if fp["creator"] != dominant_creator and dominant_creator:
                mismatches.append(
                    f"Creator '{fp['creator']}' differs from batch norm '{dominant_creator}'"
                )
            if fp["producer"] != dominant_producer and dominant_producer:
                mismatches.append(
                    f"Producer '{fp['producer']}' differs from batch norm '{dominant_producer}'"
                )
            if tuple(fp["fonts"]) != dominant_fonts and dominant_fonts:
                mismatches.append(
                    f"Fonts {fp['fonts']} differ from batch norm {list(dominant_fonts)}"
                )

            sizes_per_page = []
            for f2 in filenames:
                fp2 = fingerprints[f2]
                if fp2["pages"] > 0:
                    sizes_per_page.append(fp2["file_size"] / fp2["pages"])

            if sizes_per_page and fp["pages"] > 0:
                median_spp = sorted(sizes_per_page)[len(sizes_per_page) // 2]
                this_spp = fp["file_size"] / fp["pages"]
                if median_spp > 0 and this_spp / median_spp > 3:
                    mismatches.append(
                        f"File size/page ({round(this_spp / 1024)}KB) is {round(this_spp / median_spp, 1)}x the batch median ({round(median_spp / 1024)}KB)"
                    )

            if mismatches:
                extra_findings[fname].append(
                    _finding(
                        "batch_comparison",
                        HIGH,
                        f"OUTLIER in batch of {len(filenames)} {bank.title()} statements: {'; '.join(mismatches)}.",
                        {
                            "detected_bank": bank,
                            "batch_size": len(filenames),
                            "this_file": {
                                "creator": fp["creator"],
                                "producer": fp["producer"],
                                "fonts": fp["fonts"],
                            },
                            "batch_norm": {
                                "creator": dominant_creator,
                                "producer": dominant_producer,
                                "fonts": list(dominant_fonts),
                            },
                            "mismatches": mismatches,
                        },
                    )
                )

    return extra_findings


# ---------------------------------------------------------------------------
# UI / integration helpers
# ---------------------------------------------------------------------------
def get_priority_findings(analysis_result: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    """
    Returns findings sorted by severity, highest first.
    Useful for Streamlit cards or summary panels.
    """
    findings = list(analysis_result.get("all_findings", []))
    findings.sort(key=lambda x: _SEVERITY_ORDER.get(x.get("severity", LOW), 0), reverse=True)
    return findings[:limit]


def build_display_summary(analysis_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compact summary designed for frontend display.
    """
    priority = get_priority_findings(analysis_result, limit=5)
    return {
        "filename": analysis_result.get("filename", ""),
        "overall_risk": analysis_result.get("overall_risk", LOW),
        "counts": {
            "high": analysis_result.get("high_count", 0),
            "medium": analysis_result.get("medium_count", 0),
            "low": analysis_result.get("low_count", 0),
            "total": analysis_result.get("finding_count", 0),
        },
        "headline": (
            "Critical anomalies detected"
            if analysis_result.get("high_count", 0) > 0
            else "Moderate anomalies detected"
            if analysis_result.get("medium_count", 0) > 0
            else "No critical anomalies detected"
        ),
        "top_findings": priority,
    }


def analyze_pdf_batch(pdf_files: Dict[str, bytes]) -> Dict[str, Dict[str, Any]]:
    """
    Convenience API:
        input:  {"file1.pdf": b"...", "file2.pdf": b"..."}
        output: {"file1.pdf": analysis_dict, ...} with batch-comparison findings merged in
    """
    results = {
        filename: analyze_pdf(pdf_bytes, filename=filename)
        for filename, pdf_bytes in pdf_files.items()
    }

    extra_findings = compare_batch(results, pdf_files)
    for filename, findings in extra_findings.items():
        if not findings:
            continue
        results[filename]["layer_results"].setdefault("batch_comparison", [])
        results[filename]["layer_results"]["batch_comparison"].extend(findings)
        results[filename]["all_findings"].extend(findings)
        results[filename]["finding_count"] = len(results[filename]["all_findings"])
        results[filename]["high_count"] = sum(
            1 for f in results[filename]["all_findings"] if f.get("severity") == HIGH
        )
        results[filename]["medium_count"] = sum(
            1 for f in results[filename]["all_findings"] if f.get("severity") == MEDIUM
        )
        results[filename]["low_count"] = sum(
            1 for f in results[filename]["all_findings"] if f.get("severity") == LOW
        )
        results[filename]["overall_risk"] = _worst_severity(results[filename]["all_findings"])

    return results
