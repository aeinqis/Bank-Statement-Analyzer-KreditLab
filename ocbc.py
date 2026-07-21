# ocbc.py
# OCBC Bank (Malaysia) - Current Account statement parser
#
# Some statements have NO transaction lines (only Balance B/F + Transaction Summary = 0/0).
# In that case, emit a balance-only marker row dated to the statement end date so the month
# can appear in summaries without being counted as a transaction.

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from core_utils import normalize_text, safe_float
from party_utils import normalize_company_suffix


# --- Patterns ---
TX_START_RE = re.compile(
    r"^(?P<day>\d{2})\s+"
    r"(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
    r"(?P<year>\d{4})\s+"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)

BAL_BF_RE = re.compile(r"\bBalance\s+B/F\b\s+(?P<bal>-?[\d,]+\.\d{2})", re.IGNORECASE)

# Statement period line example (from your PDF):
# "Statement Date / Tarikh Penyata : 01 APR 2023 TO 30 APR 2023"
STATEMENT_PERIOD_RE = re.compile(
    r"Statement\s+Date\s*/\s*Tarikh\s+Penyata\s*:\s*"
    r"(?P<d1>\d{2})\s+(?P<m1>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(?P<y1>\d{4})\s+TO\s+"
    r"(?P<d2>\d{2})\s+(?P<m2>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(?P<y2>\d{4})",
    re.IGNORECASE,
)

MONEY_RE = re.compile(r"^-?(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$")
REF_MARKER_RE = re.compile(r"\bREF\s*:?\s*", re.I)
REF_TAIL_MAX_WORDS = 4

STOP_LINES = (
    "TRANSACTION",
    "SUMMARY",
    "NO. OF WITHDRAWALS",
    "NO. OF DEPOSITS",
    "TOTAL WITHDRAWALS",
    "TOTAL DEPOSITS",
    "HOLD AMOUNT",
    "LATE LOCAL CHEQUE",
    "PAGE ",
    "STATEMENT OF CURRENT ACCOUNT",
    "PENYATA AKAUN SEMASA",
    "TRANSACTION DATE",
    "TARIKH TRANSAKSI",
    "TRANSACTION DESCRIPTION",
    "HURAIAN TRANSAKSI",
)

DESCRIPTION_ARTIFACT_MARKERS = (
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
    "PROTECTED BY PIDM",
    "YOUR BANKING QUESTIONS ANSWERED",
    "A MEMBER OF OCBC GROUP",
    "IF THE PROPERTY OR ASSET",
    "LOCAL CHEQUES",
    "CEK-CEK TEMPATAN",
    "WITH EFFECT FROM",
    "BASE LENDING RATE",
    "INSURANCE KADAR PINJAMAN",
)

# classification hints
CREDIT_HINTS = (" CR ", "CR /IB", "CR INWARD", "CREDIT")
DEBIT_HINTS = (" DR ", "DR /IB", "DEBIT", "DUITNOW SC", "DEBIT AS ADVISED")

OCBC_PARTY_PATTERNS = [
    # Captures: PRINCIPAL GAS SDN BHD from "PYMT TO APPROVED PRINCIPAL GAS SDN BHD..."
    re.compile(r"\bPYMT\s+TO\s+APPROVED\s+(?P<party>.+?)(?=\s+DUITNOW|$)", re.I),
    # Captures: LUQMANULHAQEEM BIN from "DUITNOW(INST TRF) DR LUQMANULHAQEEM BIN..."
    re.compile(r"DUITNOW\(INST\s+TRF\)\s+(?:DR|CR)\b\s+(?P<party>.+?)(?=\s+DESC:|\s+REF:|$)", re.I),
    # Captures: LUQMANULHAQEEM BIN from "DUITNOW SC LUQMANULHAQEEM BIN..."
    re.compile(r"\bDUITNOW\s+SC\s+(?P<party>.+?)(?=\s+DESC:|\s+REF:|$)", re.I),
    # Captures the party from inward transfers if a name follows the code.
    re.compile(r"\bCr\s+Inward\s+\(TT\)\s+\d+\s+(?P<party>.+?)$", re.I),
]

OCBC_PARTY_TRAILING_RE = re.compile(
    r"\b(?:DESC|REF|REFERENCE|TRACE|ID|NO|TXN|TRANSACTION|ACC(?:OUNT)?|A/C|BILL|INV(?:OICE)?)\s*:?.*$",
    re.I,
)
OCBC_PARTY_NUMERIC_TAIL_RE = re.compile(r"(?:\s+|[-./])\d{5,}(?:[-./]\d+)*\s*$", re.I)
OCBC_PARTY_LEADING_CHANNEL_RE = re.compile(r"^/?IB\b\s*", re.I)

OCBC_GENERIC_NON_PARTY_TOKENS = {
    "APPROVED",
    "CR",
    "CREDIT",
    "DEBIT",
    "DESC",
    "DR",
    "DUITNOW",
    "INWARD",
    "INST",
    "PAYMENT",
    "PYMT",
    "REF",
    "SC",
    "TO",
    "TRF",
    "TT",
}


def _to_iso_date(day: str, mon: str, year: str) -> str:
    mon = mon.upper()
    month_map = {
        "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
        "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
        "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
    }
    mm = month_map.get(mon, "01")
    return f"{year}-{mm}-{day}"


def _extract_statement_end_date_iso(text: str) -> Optional[str]:
    """Extract the statement end date ISO from the statement period header."""
    if not text:
        return None
    m = STATEMENT_PERIOD_RE.search(text)
    if not m:
        return None
    return _to_iso_date(m.group("d2"), m.group("m2"), m.group("y2"))


def _extract_amount_and_balance_from_line(rest: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    From 'rest' (after date), extract:
      - tx_amount (usually the penultimate money token)
      - balance (last money token)
      - desc_text (rest with trailing numeric columns removed)
    """
    tokens = rest.split()
    money_idx = [i for i, t in enumerate(tokens) if MONEY_RE.match(t)]
    if len(money_idx) < 2:
        return None, None, rest

    balance = safe_float(tokens[money_idx[-1]])
    tx_amount = safe_float(tokens[money_idx[-2]])

    cut = money_idx[-2]
    desc_text = " ".join(tokens[:cut]).strip()
    return tx_amount, balance, desc_text


def _is_noise_line(line: str) -> bool:
    up = line.upper().strip()
    if not up:
        return True
    return any(k in up for k in STOP_LINES) or any(k in up for k in DESCRIPTION_ARTIFACT_MARKERS)


def _is_footer_or_legal_line(line: str) -> bool:
    up = line.upper().strip()
    return any(k in up for k in (
        "YOUR BANKING QUESTIONS ANSWERED",
        "A MEMBER OF OCBC GROUP",
        "HTTP://WWW.BANKINGINFO.COM.MY",
        "HTTPS://WWW.BANKINGINFO.COM.MY",
        "HTTP://WWW.OCBC.COM.MY",
        "HTTPS://WWW.OCBC.COM.MY",
        "IF THE PROPERTY OR ASSET",
        "JOINT MANAGEMENT BODY",
        "CERTIFICATE OF INSURANCE",
        "LOCAL CHEQUES",
        "THE ENTRIES AND BALANCE",
        "PLEASE NOTIFY US",
        "CEK-CEK TEMPATAN",
        "BUTIR-BUTIR TRANSAKSI",
        "SILA MAKLUMKAN",
        "WITH EFFECT FROM",
        "BERKUATKUASA DARI",
        "BASE LENDING RATE",
        "INSURANCE KADAR PINJAMAN",
    ))


def _strip_ocbc_description_artifacts(description: str) -> str:
    cleaned = normalize_text(description)
    if not cleaned:
        return ""

    upper = cleaned.upper()
    cut_index = None
    for marker in DESCRIPTION_ARTIFACT_MARKERS:
        marker_index = upper.find(marker)
        if marker_index >= 0 and (cut_index is None or marker_index < cut_index):
            cut_index = marker_index

    if cut_index is not None:
        cleaned = cleaned[:cut_index]

    ref_matches = list(REF_MARKER_RE.finditer(cleaned))
    if ref_matches:
        ref_match = ref_matches[-1]
        prefix = cleaned[: ref_match.end()]
        ref_tail = cleaned[ref_match.end():].strip()
        ref_tokens = ref_tail.split()
        if len(ref_tokens) > REF_TAIL_MAX_WORDS:
            cleaned = normalize_text(prefix + " ".join(ref_tokens[:REF_TAIL_MAX_WORDS]))

    cleaned = re.sub(r"\s*[-=/,:;|]+\s*$", "", cleaned)
    return normalize_text(cleaned)


def _normalize_ocbc_party_name(name: str) -> str:
    cleaned = normalize_text(name).upper()
    if not cleaned:
        return "UNKNOWN"

    cleaned = OCBC_PARTY_TRAILING_RE.sub("", cleaned)
    cleaned = OCBC_PARTY_NUMERIC_TAIL_RE.sub("", cleaned)
    cleaned = OCBC_PARTY_LEADING_CHANNEL_RE.sub("", cleaned)
    cleaned = re.sub(r"[^A-Z0-9/&().\s-]", " ", cleaned)
    cleaned = normalize_text(cleaned)

    normalized_tokens = []
    for token in cleaned.split():
        token_core = token.strip(" .,-")
        if not token_core:
            continue
        if token_core in {"SND", "SD", "SDN"}:
            token_core = "SDN"
        if token_core in {"BH", "BDH", "B", "BHD"} and any(existing == "SDN" for existing in normalized_tokens):
            token_core = "BHD"
        normalized_tokens.append(token_core)

    cleaned = normalize_company_suffix(" ".join(normalized_tokens))
    return cleaned or "UNKNOWN"


def _is_generic_ocbc_non_party(name: str) -> bool:
    cleaned = normalize_text(name).upper()
    if not cleaned or cleaned == "UNKNOWN":
        return True

    tokens = [
        token
        for token in re.sub(r"[^A-Z0-9/&\s]", " ", cleaned).split()
        if token
    ]
    if not tokens:
        return True

    return all(token in OCBC_GENERIC_NON_PARTY_TOKENS or token.isdigit() for token in tokens)


def extract_ocbc_party_name(description: str) -> str:
    """Extract a counterparty name from OCBC transaction descriptions."""
    desc = normalize_text(description).upper()
    if not desc or desc.startswith("NO TRANSACTIONS"):
        return "UNKNOWN"

    for pattern in OCBC_PARTY_PATTERNS:
        match = pattern.search(desc)
        if not match:
            continue

        candidate = _normalize_ocbc_party_name(match.group("party"))
        if not _is_generic_ocbc_non_party(candidate):
            return candidate

    return "UNKNOWN"


def parse_transactions_ocbc(pdf_input: Any, source_file: str = "") -> List[Dict]:
    """
    Standard interface used by app.py:
      input: pdf bytes (preferred) OR file-like
      output: list of tx dicts with canonical keys
    """
    if hasattr(pdf_input, "pages") and hasattr(pdf_input, "close"):
        pdf = pdf_input
        should_close = False
    elif isinstance(pdf_input, (bytes, bytearray)):
        pdf = pdfplumber.open(BytesIO(bytes(pdf_input)))
        should_close = True
    else:
        pdf = pdfplumber.open(pdf_input)
        should_close = True

    bank_name = "OCBC Bank"
    transactions: List[Dict] = []

    prev_balance: Optional[float] = None
    statement_end_iso: Optional[str] = None
    current_tx: Optional[Dict] = None

    try:
        for page_idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text:
                continue

            # capture statement end date once (needed for "no transactions" months)
            if statement_end_iso is None:
                statement_end_iso = _extract_statement_end_date_iso(text)

            # find Balance B/F once
            if prev_balance is None:
                bf = BAL_BF_RE.search(text)
                if bf:
                    prev_balance = safe_float(bf.group("bal"))

            for raw_line in text.splitlines():
                line = normalize_text(raw_line)
                if not line:
                    continue

                # Stop processing transaction area when summary starts
                if "TRANSACTION" in line.upper() and "SUMMARY" in line.upper():
                    current_tx = None
                    break

                m = TX_START_RE.match(line)
                if m:
                    day, mon, year = m.group("day"), m.group("mon"), m.group("year")
                    rest = m.group("rest")

                    date_iso = _to_iso_date(day, mon, year)
                    tx_amount, balance, desc_head = _extract_amount_and_balance_from_line(rest)

                    if tx_amount is None or balance is None:
                        current_tx = None
                        continue

                    desc_head = _strip_ocbc_description_artifacts(desc_head)
                    desc_upper = desc_head.upper()
                    debit = 0.0
                    credit = 0.0

                    # 1) keyword classification
                    if any(h in f" {desc_upper} " for h in CREDIT_HINTS) and not any(h in f" {desc_upper} " for h in (" DR ", "DR /IB")):
                        credit = abs(tx_amount)
                    elif any(h in f" {desc_upper} " for h in DEBIT_HINTS):
                        debit = abs(tx_amount)
                    # 2) balance-delta fallback
                    elif prev_balance is not None:
                        delta = round(balance - prev_balance, 2)
                        if abs(delta - tx_amount) <= 0.05:
                            credit = abs(tx_amount)
                        elif abs(delta + tx_amount) <= 0.05:
                            debit = abs(tx_amount)
                        else:
                            if delta > 0:
                                credit = abs(delta)
                            elif delta < 0:
                                debit = abs(delta)

                    tx = {
                        "date": date_iso,
                        "description": desc_head,
                        "debit": round(float(debit), 2),
                        "credit": round(float(credit), 2),
                        "balance": round(float(balance), 2),
                        "page": page_idx,
                        "bank": bank_name,
                        "source_file": source_file,
                    }
                    transactions.append(tx)
                    current_tx = tx
                    prev_balance = balance
                    continue

                # Continuation lines (multi-line description)
                if current_tx is not None:
                    up = line.upper()

                    if _is_noise_line(line) or _is_footer_or_legal_line(line):
                        current_tx = None
                        continue

                    is_likely_tx_detail = (
                        up.startswith("DESC:")
                        or up.startswith("REF:")
                        or up.startswith("/IB")
                        or " DUITNOW" in f" {up} "
                        or len(line.split()) <= 6
                    )

                    if is_likely_tx_detail and not MONEY_RE.match(line.replace(",", "")):
                        current_tx["description"] = _strip_ocbc_description_artifacts(
                            normalize_text(current_tx["description"] + " " + line)
                        )

        # ---- FIX: no transactions case (like your April PDF) ----
        if not transactions and prev_balance is not None:
            # Use statement end date so monthly summary buckets correctly
            date_for_row = statement_end_iso or "2000-01-01"
            transactions.append(
                {
                    "date": date_for_row,
                    "description": "NO TRANSACTIONS (BALANCE B/F)",
                    "debit": 0.0,
                    "credit": 0.0,
                    "balance": round(float(prev_balance), 2),
                    "page": None,
                    "bank": bank_name,
                    "source_file": source_file,
                    "is_statement_balance": True,
                    "is_balance_marker": True,
                }
            )

        for transaction in transactions:
            transaction["description"] = _strip_ocbc_description_artifacts(transaction.get("description", ""))
            transaction["party_name"] = extract_ocbc_party_name(transaction.get("description", ""))

        return transactions

    finally:
        if should_close:
            try:
                pdf.close()
            except Exception:
                pass
