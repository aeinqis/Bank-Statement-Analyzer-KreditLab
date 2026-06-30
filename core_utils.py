"""
core_utils.py

Project-wide utilities used by Streamlit apps and bank parsers.

Goals:
1) Standardize input handling (PDF bytes)
2) Standardize transaction schema and types
3) Make date/amount parsing resilient across banks
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple


# -----------------------------
# PDF INPUT
# -----------------------------
def read_pdf_bytes(pdf_input: Any) -> bytes:
    """Return PDF bytes from:
    - bytes / bytearray
    - Streamlit UploadedFile (has getvalue)
    - file-like objects (has read)
    - filesystem path (str)
    """
    if isinstance(pdf_input, (bytes, bytearray)):
        return bytes(pdf_input)

    # Streamlit UploadedFile
    if hasattr(pdf_input, "getvalue"):
        data = pdf_input.getvalue()
        if data:
            return data

    # file-like
    if hasattr(pdf_input, "read"):
        try:
            pdf_input.seek(0)
        except Exception:
            pass
        data = pdf_input.read()
        if data:
            return data

    # path
    if isinstance(pdf_input, str):
        with open(pdf_input, "rb") as f:
            return f.read()

    raise ValueError("Unable to read PDF bytes from the provided input")


def bytes_to_pdfplumber(pdf_bytes: bytes):
    """Helper to open pdfplumber using bytes."""
    import pdfplumber  # local import to keep utils lightweight
    return pdfplumber.open(BytesIO(pdf_bytes))


# -----------------------------
# NORMALIZATION
# -----------------------------
_WS_RE = re.compile(r"\s+")


def normalize_text(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()


def _chunk_list(items: List[str], chunk_size: int) -> List[List[str]]:
    if chunk_size <= 0:
        chunk_size = 100
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


DESCRIPTION_ARTIFACT_PATTERNS = [
    re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"),
    re.compile(r"\bENDING\s+BALANCE\b", re.I),
    re.compile(r"\bLEDGER\s+BALANCE\b", re.I),
    re.compile(r"\bBAKI\s+AKHIR\b", re.I),
    re.compile(r"\bBAKI\s+LEGAR\b", re.I),
    re.compile(r"\bNOT\s+PROTECTED\s+BY\s+PIDM\b", re.I),
    re.compile(r"\bPROTECTED\s+BY\s+PIDM\b", re.I),
    re.compile(r"\bOVERDRAWN\s+BALANCES\b", re.I),
    re.compile(r"\bPERHATIAN\s*/\s*NOTE\b", re.I),
    re.compile(r"\bPLEASE\s+NOTIFY\b", re.I),
    re.compile(r"\bSEMUA\s+MAKLUMAT\b", re.I),
    re.compile(r"\bSTART\s+SUBMITTING\s+YOUR\s+AUDIT\s+CONFIRMATION\s+REPORT\b", re.I),
    re.compile(r"\bAUDIT\s+CONFIRMATION\s+REPORT\s+REQUEST\b", re.I),
    re.compile(r"\bREQUESTS,\s+PLEASE\s+VISIT\s+OUR\s+WEBSITE\b", re.I),
    re.compile(r"\bTRANSFER\s+FUNDS\s+OVERSEAS\s+QUICKLY\s+AND\s+EASILY\b", re.I),
    re.compile(r"\bBUSINESS\s+MOBILE\s+APP\.?\s*ENJOY\b", re.I),
    re.compile(r"\bOCBC\s+FLASH\b", re.I),
    re.compile(r"\bTHROUGH\s+OCBC\s+VELOCITY\b", re.I),
    re.compile(r"\bOUR\s+SMES,\s+FOR\s+MORE\s+INFORMATION\b", re.I),
    re.compile(r"\bBUILD\s+YOUR\s+BUSINESS\s+STARTING\s+WITH\s+THE\s+OCBC\s+EBIZ\s+ACCOUNT\b", re.I),
    re.compile(r"\bECONFIRM\s+PORTAL\b", re.I),
    re.compile(r"\bSILA\s+HANTAR\s+PERMINTAAN\b", re.I),
    re.compile(r"\bUNTUK\s+BORANG\s+DAN\s+SOALAN\s+LAZIM\b", re.I),
    re.compile(r"\bYOUR\s+BANKING\s+QUESTIONS\s+ANSWERED\b", re.I),
    re.compile(r"\bA\s+MEMBER\s+OF\s+OCBC\s+GROUP\b", re.I),
    re.compile(r"\bIF\s+THE\s+PROPERTY\s+OR\s+ASSET\b", re.I),
    re.compile(r"\bLOCAL\s+CHEQUES\b", re.I),
    re.compile(r"\bCEK-CEK\s+TEMPATAN\b", re.I),
    re.compile(r"\bWITH\s+EFFECT\s+FROM\b", re.I),
    re.compile(r"\bBASE\s+LENDING\s+RATE\b", re.I),
    re.compile(r"\bINSURANCE\s+KADAR\s+PINJAMAN\b", re.I),
    re.compile(r"\bEFFECTIVE\s+30\s+SEPTEMBER\b", re.I),
    re.compile(r"\bPROPRIETORSHIP\s+WILL\s+NO\s+LONGER\b", re.I),
    re.compile(r"\bI-MUAMALAT\.COM\.MY\b", re.I),
    re.compile(r"\bSEGALA\s+BILANGAN\s+DAN\s+BAKI\b", re.I),
    re.compile(r"\bALL\s+ITEMS\s+AND\s+BALANCES\b", re.I),
    re.compile(r"\bTOTAL\s+ENDING\s+BALANCE\b", re.I),
    re.compile(r"\bCURRENT\s+I-MUAMALAT\s+ONLINE\b", re.I),
    re.compile(r"\bANY\s+CHEQUES\s+DEPOSITED\b", re.I),
]

DESCRIPTION_FOOTER_MARKERS = [
    "可應用存餘",
    "截止結餘",
    "未過賬",
    "本欄内誌DR者爲結欠",
    "若银行在21天内未获得书面通知",
    "余额将被视为正确",
    "請通知本行在何地址更换",
    "進支項說明",
    "BALANCE DITANDAKAN DENGAN DR",
    "PERHATIAN / NOTE",
    "SEMUA MAKLUMAT DAN BAKI",
    "OVERDRAWN BALANCES ARE",
    "DENOTED BY DR",
    "PROTECTED BY PIDM",
    "PLEASE NOTIFY US OF ANY CHANGE OF ADDRESS IN WRITING",
    "APPLICABLE FOR PA-I MINOR AND IN-TRUST",
    "START SUBMITTING YOUR AUDIT CONFIRMATION REPORT",
    "AUDIT CONFIRMATION REPORT REQUEST",
    "REQUESTS, PLEASE VISIT OUR WEBSITE",
    "TRANSFER FUNDS OVERSEAS QUICKLY AND EASILY",
    "BUSINESS MOBILE APP.ENJOY",
    "BUSINESS MOBILE APP. ENJOY",
    "OCBC FLASH",
    "THROUGH OCBC VELOCITY",
    "OUR SMES, FOR MORE INFORMATION",
    "BUILD YOUR BUSINESS STARTING WITH THE OCBC EBIZ ACCOUNT",
    "THE SHARIAH-COMPL",
    "ECONFIRM PORTAL",
    "HTTPS://WWW.OCBC.COM.MY/BUSINESS-BANKING/HELP-AND-SUPPORT#FORMS",
    "HTTPS://WWW.OCBC.COM.MY/EBIZ-I",
    "HTTPS://ECONFIRM.MY",
    "SILA HANTAR PERMINTAAN",
    "UNTUK BORANG DAN SOALAN LAZIM",
    "YOUR BANKING QUESTIONS ANSWERED",
    "A MEMBER OF OCBC GROUP",
    "IF THE PROPERTY OR ASSET",
    "LOCAL CHEQUES",
    "CEK-CEK TEMPATAN",
    "WITH EFFECT FROM",
    "BASE LENDING RATE",
    "INSURANCE KADAR PINJAMAN",
    "EFFECTIVE 30 SEPTEMBER",
    "PROPRIETORSHIP WILL NO LONGER",
    "HTTPS:WWW.I-MUAMALAT.COM.MY",
    "HTTPS://WWW.I-MUAMALAT.COM.MY",
    "WWW.I-MUAMALAT.COM.MY",
    "SEGALA BILANGAN DAN BAKI",
    "ALL ITEMS AND BALANCES",
    "TANDA \"-\" PADA BAKI",
    "THE SIGN \"-\" AGAINST",
    "SILA MAKLUMKAN KEPADA",
    "CHANGE OF ADDRESS",
    "SEBARANG CEK DEPOSIT",
    "ANY CHEQUES DEPOSITED",
    "TOTAL ENDING BALANCE",
    "CURRENT I-MUAMALAT ONLINE",
    "ONLINE PORTAL",
    "This is a computer generated statement",
    "For enquiries, please contact",
    "If you have any questions about this statement",
    "Please contact our customer service",
    "This statement was generated by",
    "For more information about your account",
    "This is a system generated statement",
    "If you have any questions regarding this statement",
    "For any inquiries about this statement",
]

def safe_float(value: Any) -> float:
    """Convert numeric strings to float safely.

    Handles:
    - None / empty
    - commas
    - parentheses negatives: (1,234.56)
    - trailing +/-: 123.45- / 123.45+
    - currency symbols and stray text
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s:
        return 0.0

    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    # trailing sign
    trailing_sign = None
    if s.endswith("+"):
        trailing_sign = "+"
        s = s[:-1].strip()
    elif s.endswith("-"):
        trailing_sign = "-"
        s = s[:-1].strip()

    s = s.replace(",", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if s in {"", "-", "."}:
        return 0.0

    try:
        f = float(s)
    except Exception:
        return 0.0

    if neg or trailing_sign == "-":
        f = -abs(f)
    elif trailing_sign == "+":
        f = abs(f)
    return float(f)


def normalize_date(date_value: Any, default_year: Optional[int] = None) -> Optional[str]:
    """Normalize many common bank-statement date formats to ISO YYYY-MM-DD.
    Returns None if parsing fails.
    """
    if date_value is None:
        return None

    s = normalize_text(date_value)
    if not s:
        return None

    # already ISO
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s

    # common patterns (day-first)
    patterns: List[Tuple[str, str]] = [
        (r"^\d{1,2}/\d{1,2}/\d{4}$", "%d/%m/%Y"),
        (r"^\d{1,2}-\d{1,2}-\d{4}$", "%d-%m-%Y"),
        (r"^\d{1,2}/\d{1,2}/\d{2}$", "%d/%m/%y"),
        (r"^\d{1,2}-\d{1,2}-\d{2}$", "%d-%m-%y"),
        (r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{4}$", "%d %b %Y"),
        (r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2}$", "%d %b %y"),
        (r"^\d{1,2}\s+[A-Za-z]{3}$", "%d %b"),
        (r"^\d{1,2}/\d{1,2}$", "%d/%m"),
        (r"^\d{1,2}-\d{1,2}$", "%d-%m"),
    ]

    for rx, fmt in patterns:
        if not re.fullmatch(rx, s):
            continue
        try:
            if fmt in {"%d %b", "%d/%m", "%d-%m"}:
                if default_year is None:
                    return None
                dt = datetime.strptime(f"{s} {default_year}", fmt + " %Y")
            else:
                dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    # last-resort: dateutil
    try:
        from dateutil import parser as dateparser

        dt = dateparser.parse(
            s,
            dayfirst=True,
            yearfirst=False,
            default=datetime(default_year or 2000, 1, 1),
        )
        # if no explicit year and default_year is None, dt will use 2000 and likely be wrong -> reject
        if default_year is None and dt.year == 2000 and not re.search(r"\b\d{4}\b", s):
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_any_date(date_value: Any) -> Optional[datetime]:
    """Parse mixed transaction date formats into a datetime for sorting/display."""
    s = normalize_text(date_value)
    if not s:
        return None

    patterns = [
        "%Y-%m-%d",
        "%d-%b-%y",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %b %y",
    ]

    for fmt in patterns:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass

    try:
        from dateutil import parser as dateparser

        return dateparser.parse(s, dayfirst=True, yearfirst=False)
    except Exception:
        return None


def display_transaction_date(date_value: Any) -> str:
    dt = parse_any_date(date_value)
    if dt is None:
        return normalize_text(date_value)
    return dt.strftime("%d-%b-%y")


def detect_description_artifact(description: Any) -> str:
    """Return a short reason when a description looks polluted by statement/footer text."""
    text = normalize_text(description)
    if not text:
        return ""

    for pattern in DESCRIPTION_ARTIFACT_PATTERNS:
        if pattern.search(text):
            return "Footer/header text leaked into transaction description"
    return ""


def sanitize_transaction_description(description: Any) -> str:
    """Remove statement footer/header sentences accidentally appended to descriptions."""
    text = normalize_text(description)
    if not text:
        return ""

    upper_text = text.upper()
    cut_index: Optional[int] = None
    for marker in DESCRIPTION_FOOTER_MARKERS:
        marker_index = upper_text.find(marker.upper())
        if marker_index >= 0 and (cut_index is None or marker_index < cut_index):
            cut_index = marker_index

    if cut_index is not None:
        text = text[:cut_index]

    text = re.sub(r"\s*[-=/,:;|]+\s*$", "", text)
    return normalize_text(text)


def signed_amount_from_record(record: Dict[str, Any]) -> Optional[float]:
    """Return signed amount with debit as negative and credit as positive."""
    credit = safe_float(record.get("credit", 0) or 0)
    debit = safe_float(record.get("debit", 0) or 0)

    if credit > 0:
        return abs(credit)
    if debit > 0:
        return -abs(debit)

    amount = record.get("amount")
    if amount is None or str(amount).strip() == "":
        return None

    value = safe_float(amount)
    if value == 0:
        return 0.0
    return float(value)


def build_transaction_export_payload(rows: Any) -> List[Dict[str, Any]]:
    """Build a JSON-friendly flat transaction export with core columns only."""
    try:
        import pandas as pd
    except Exception:
        return []

    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return []

    export_df = rows.copy()
    if "description" not in export_df.columns:
        export_df["description"] = ""
    if "date" not in export_df.columns:
        export_df["date"] = ""
    if "credit" not in export_df.columns:
        export_df["credit"] = 0.0
    if "debit" not in export_df.columns:
        export_df["debit"] = 0.0

    export_df["description"] = export_df["description"].apply(normalize_text)
    export_df["credit"] = pd.to_numeric(export_df["credit"], errors="coerce").fillna(0.0)
    export_df["debit"] = pd.to_numeric(export_df["debit"], errors="coerce").fillna(0.0)
    export_df["date_sort_key"] = export_df["date"].apply(parse_any_date)
    export_df["date_sort_key"] = export_df["date_sort_key"].apply(
        lambda x: pd.Timestamp(x) if x is not None else pd.Timestamp.max
    )
    export_df = export_df.sort_values(["date_sort_key", "description"], kind="stable").reset_index(drop=True)

    return [
        {
            "description": row["description"],
            "date": display_transaction_date(row["date"]),
            "credit": round(float(row["credit"]), 2),
            "debit": round(float(row["debit"]), 2),
        }
        for _, row in export_df.iterrows()
    ]


def infer_default_year(transactions: Iterable[Dict[str, Any]]) -> Optional[int]:
    """Infer a reasonable default year from any transaction that already contains a year."""
    for tx in transactions:
        d = normalize_text(tx.get("date"))
        if re.search(r"\b\d{4}\b", d):
            iso = normalize_date(d)
            if iso:
                return int(iso[:4])
    return None


def ensure_transaction_schema(
    tx: Dict[str, Any],
    *,
    default_bank: str,
    default_source_file: str,
    default_year: Optional[int] = None,
) -> Dict[str, Any]:
    """Return a sanitized transaction dict with consistent keys and types."""
    raw_date = tx.get("date")
    date_iso = normalize_date(raw_date, default_year=default_year)

    description = sanitize_transaction_description(tx.get("description"))
    debit = safe_float(tx.get("debit", 0))
    credit = safe_float(tx.get("credit", 0))

    # Some parsers store negative values; normalize to non-negative debit/credit where possible
    if debit < 0 and credit == 0:
        credit = abs(debit)
        debit = 0.0
    if credit < 0 and debit == 0:
        debit = abs(credit)
        credit = 0.0

    balance_raw = tx.get("balance", None)
    balance = safe_float(balance_raw) if balance_raw is not None and str(balance_raw).strip() != "" else None

    page_raw = tx.get("page")
    try:
        page = int(page_raw) if page_raw is not None and str(page_raw).strip() != "" else None
    except Exception:
        page = None

    bank = normalize_text(tx.get("bank")) or default_bank
    source_file = normalize_text(tx.get("source_file")) or default_source_file

    out: Dict[str, Any] = {
        "date": date_iso or normalize_text(raw_date),
        "description": description,
        "debit": round(float(debit), 2),
        "credit": round(float(credit), 2),
        "balance": round(float(balance), 2) if isinstance(balance, (int, float)) else None,
        "page": page,
        "bank": bank,
        "source_file": source_file,
    }

    # retain raw date if normalization changed it
    if date_iso and normalize_text(raw_date) and normalize_text(raw_date) != date_iso:
        out["_raw_date"] = normalize_text(raw_date)

    # Preserve additional parser-provided metadata (scalar JSON-friendly fields only).
    # This prevents accidental loss of useful fields like: seq, account_no, company_name,
    # is_statement_balance, transaction_date, time, etc.
    for k, v in (tx or {}).items():
        if k in out or k.startswith("_"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v

    # Harmonize common metadata keys without changing calculations.
    # Many banks use account_no/account_number inconsistently; keep both when present.
    if out.get("account_no") and not out.get("account_number"):
        out["account_number"] = normalize_text(out.get("account_no"))
    if out.get("account_number") and not out.get("account_no"):
        out["account_no"] = normalize_text(out.get("account_number"))
    if out.get("company_name"):
        out["company_name"] = normalize_text(out.get("company_name"))
    if out.get("is_statement_balance") is True:
        out["is_balance_marker"] = True

    return out


def normalize_transactions(
    transactions: List[Dict[str, Any]],
    *,
    default_bank: str,
    source_file: str,
) -> List[Dict[str, Any]]:
    """Normalize a list of transactions and infer year if needed."""
    year = infer_default_year(transactions)
    return [
        ensure_transaction_schema(
            tx,
            default_bank=default_bank,
            default_source_file=source_file,
            default_year=year,
        )
        for tx in transactions
    ]


def transaction_fingerprint(tx: Dict[str, Any]) -> str:
    """Create a stable fingerprint suitable for de-duplication."""
    parts = [
        normalize_text(tx.get("date")),
        normalize_text(tx.get("description")),
        f"{safe_float(tx.get('debit', 0)):.2f}",
        f"{safe_float(tx.get('credit', 0)):.2f}",
        "" if tx.get("balance") is None else f"{safe_float(tx.get('balance')):.2f}",
        normalize_text(tx.get("bank")),
    ]
    blob = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(blob).hexdigest()


def dedupe_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for tx in transactions:
        fp = transaction_fingerprint(tx)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(tx)
    return out


# =========================================================
# Affin-specific fixes (DO NOT affect other banks unless called)
# =========================================================
def dedupe_transactions_affin(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Affin statements are frequently OCR-based; description strings vary across files.
    De-dupe must NOT depend on description/page/source_file, or overlap PDFs will inflate totals.

    Key:
      (date, debit, credit, balance, bank)
    """
    seen = set()
    out = []
    for tx in transactions:
        date = normalize_text(tx.get("date"))
        bank = normalize_text(tx.get("bank"))
        debit = round(safe_float(tx.get("debit", 0.0)), 2)
        credit = round(safe_float(tx.get("credit", 0.0)), 2)
        bal_raw = tx.get("balance")
        balance = None if bal_raw is None else round(safe_float(bal_raw), 2)

        key = (date, debit, credit, balance, bank)
        if key in seen:
            continue
        seen.add(key)
        out.append(tx)
    return out


def filter_affin_balance_outliers(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Drop rows whose balance is a clear OCR outlier (e.g. extra digit -> millions),
    which causes massive delta-based phantom debits/credits.

    Method:
      - compute median balance
      - keep balances within +/- 1.5M of median
      - keep rows with balance=None unchanged
    """
    bals = [safe_float(t.get("balance")) for t in transactions if t.get("balance") is not None]
    if len(bals) < 10:
        return transactions

    bals_sorted = sorted(bals)
    median = bals_sorted[len(bals_sorted) // 2]

    lo = median - 1_500_000
    hi = median + 1_500_000

    out = []
    for t in transactions:
        b = t.get("balance")
        if b is None:
            out.append(t)
            continue
        bf = safe_float(b)
        if lo <= bf <= hi:
            out.append(t)

    return out


# =========================================================
# Monthly Summary - PRESENTATION STANDARDIZATION ONLY
# =========================================================
def compute_swing(highest_balance: Any, lowest_balance: Any) -> Optional[float]:
    """Compute swing = highest - lowest safely."""
    if highest_balance is None or lowest_balance is None:
        return None
    try:
        return round(float(safe_float(highest_balance) - safe_float(lowest_balance)), 2)
    except Exception:
        return None


def present_monthly_summary_standard(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert an existing monthly summary (any bank-specific schema) into the standard schema:

      opening_balance, total_debit, total_credit, highest_balance, lowest_balance,
      swing, ending_balance, source_files

    This is intentionally "presentation-only":
    - It does NOT recalculate debit/credit/opening/ending.
    - It only maps fields and computes swing from existing high/low.
    """
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        highest = r.get("highest_balance")
        lowest = r.get("lowest_balance")
        out.append(
            {
                "month": r.get("month"),
                "opening_balance": r.get("opening_balance"),
                "total_debit": r.get("total_debit"),
                "total_credit": r.get("total_credit"),
                "highest_balance": highest,
                "lowest_balance": lowest,
                "swing": compute_swing(highest, lowest),
                "ending_balance": r.get("ending_balance"),
                "source_files": r.get("source_files"),
            }
        )
    return out

_COMPANY_SUFFIX_NORM_RE = re.compile(
    r"\b(SB|SDN\s*\.?\s*BHD\.?|SDN\s*\.?\s*BH|SDN\s*\.?\s*B|SDN\.?)\s*$",
    re.IGNORECASE,
)


def normalize_company_suffix(name: Any) -> str:
    """Restore truncated Malaysian "SDN BHD" tails to the canonical form.

    Variants handled at end-of-string only:
      "SB", "SDN", "SDN.", "SDN B", "SDN. B", "SDN B.",
      "SDN BH", "SDN. BH", "SDN BHD", "SDN BHD.",
      "SDN. BHD.", "SDN. BHD"  →  "SDN BHD"

    Returns the original (stripped) string when no tail variant matches.
    Empty / non-string input is coerced to "".
    """
    if not name:
        return ""
    s = str(name)
    return _COMPANY_SUFFIX_NORM_RE.sub("SDN BHD", s).strip()

# =========================================================
# STOP-WORDS — names that should never survive as counterparty
# =========================================================

_MONTH_TOKENS: set = {
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
}

_PURPOSE_STOP_TOKENS: set = {
    "BULK", "SALARY", "PAYMENT", "PAY", "TRANSFER", "TRF",
    "FUND", "SETTLEMENT", "SETTLE",
    "LOAN", "REPAYMENT", "ADVANCE",
    "IBG", "ADVICE", "CREDIT", "DEBIT", "ACCOUNT",
    "HUB", "MISC",
    "DUITNOW", "FPX", "INSTANT",
    "CR", "DR", "TO", "FROM", "FR",
    "TRANSACTION", "TRANSACTIONS", "TRANS",
    "DEPOSIT", "WITHDRAWAL", "CASH",
    "INWARD", "OUTWARD",
}


def should_drop_as_counterparty(name: Any) -> bool:
    """Return True if a cleaned counterparty name is actually just a purpose fragment /
    month / stop word and should be dropped (transaction has no real counterparty).

    Examples that return True:
      "JAN FEB", "BULK", "LOAN REPAYMENT", "CR ADVICE", "PAYMENT TRANSFER",
      "IBG TRANSACTION", "" (empty), "X" (too short), "123" (no letters).
    """
    s = normalize_text(name).upper()
    if not s:
        return True
    if not re.search(r"[A-Z]", s):
        return True
    if len(s) <= 2:
        return True
    toks = s.split()
    # all tokens are months (covers "JAN FEB" / "FEB MAR" etc.)
    if toks and all(t in _MONTH_TOKENS for t in toks):
        return True
    # all tokens are purpose stop-words or months
    if toks and all(t in _PURPOSE_STOP_TOKENS or t in _MONTH_TOKENS for t in toks):
        return True
    return False