# agro_bank.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from core_utils import normalize_text

# =========================================================
# Regex / constants
# =========================================================

# Dates in Agrobank statements: 31/05/25
DATE_RE = re.compile(r"^\d{1,2}/\d{2}/\d{2}$")

# Amount tokens:
# - 1,234.56
# - 1,234.56-
# - .92
# - .50-
AMOUNT_RE = re.compile(r"^(?P<num>(?:\d{1,3}(?:,\d{3})*|\d+)?\.\d{2})(?P<sign>-)?$")

# Common zero formats
ZERO_RE = re.compile(r"^(?:0|0?\.00)(?:-)?$")
REFERENCE_RE = re.compile(r"^\d{4,}$")

AGROBANK_FOOTER_KEYWORDS = (
    "BANK PERTANIAN MALAYSIA BERHAD",
    "PETI SURAT",
    "SEMUA CATATAN",
    "SEKIRANYA TERDAPAT SEBARANG PERTANYAAN",
    "DEEMED TO BE CORRECT",
    "PLEASE CALL AT OUR NEAREST BRANCH",
    "AKTIVITI AKAUN ANDA",
    "DATE REFERENCE NO. DETAILS",
    "DEBIT(-)/CREDIT",
    "TARIKH PENYATA",
    "STATEMENT DATE",
    "A/C NO:",
    "PENYATA AKAUN",
    "ACCOUNT STATEMENT",
)

AGROBANK_TRANSACTION_CATEGORY_PATTERNS = [
    ("Cash Deposit (CDM)", re.compile(r"\bCDM\s+CA\s+DEPOSIT\b", re.I)),
    ("DuitNow Debit", re.compile(r"\bDUITNOW/INSTANT\s+DR\b", re.I)),
    ("DuitNow Credit", re.compile(r"\bDUITNOW/INSTANT\s+CR\b", re.I)),
    ("Interbank Transfer", re.compile(r"\b(?:OUTWARD\s+IBG\s+DEBIT|INWARD\s+IBG\s+CREDIT)\b", re.I)),
    ("High Value (RENTAS)", re.compile(r"\bINWARD\s+RENTAS\s+CREDIT\b", re.I)),
    ("Online Payment", re.compile(r"\bFPX\s+PAYMENT\b", re.I)),
    ("Internal/Other", re.compile(r"\b(?:IBFT\s+INEB-INWTRF|FUND\s+RM\s*\.00\s+BRANCH\s+OTC)\b", re.I)),
]

AGROBANK_PARTY_PATTERNS = [
    re.compile(r"\bDUITNOW/INSTANT\s+(?:DR|CR)\b\s+(?P<party>.+)$", re.I),
    re.compile(r"\bOUTWARD\s+IBG\s+DEBIT\b\s+(?P<party>.+)$", re.I),
    re.compile(r"\bINWARD\s+IBG\s+CREDIT\b\s+(?P<party>.+)$", re.I),
    re.compile(r"\bINWARD\s+RENTAS\s+CREDIT\b\s+(?P<party>.+)$", re.I),
    re.compile(r"\bFPX\s+PAYMENT\b\s+(?P<party>.+)$", re.I),
    re.compile(r"\bIBFT\s+INEB-INWTRF\b\s*(?:-\s*\w+)?\s+(?P<party>.+)$", re.I),
]

AGROBANK_CONTINUATION_SKIP_PATTERNS = [
    re.compile(r"^RPP$", re.I),
    re.compile(r"^RM\s*\.00$", re.I),
    re.compile(r"^0$", re.I),
    re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$", re.I),
    re.compile(r"^IBG\s+FROM\s+AGROBANK$", re.I),
    re.compile(r"^BRANCH\s+OTC$", re.I),
    re.compile(r"^[A-Z]{4,}[A-Z0-9]{4,}$", re.I),
    re.compile(r"^BIB\d+$", re.I),
]

AGROBANK_GENERIC_NON_PARTY_TOKENS = {
    "RPP", "DUITNOW", "REFUND", "TOTAL", "DEBIT", "DEBITS", "CREDIT", "CREDITS",
    "GA", "ADVICE", "HIBAH/PROFIT", "HIBAH", "PROFIT",
}

AGROBANK_PARTY_TRAILING_RE = re.compile(
    r"\b(?:REF(?:ERENCE)?|TRACE|ID|NO|TXN|TRANSACTION|ACC(?:OUNT)?|A/C|BANK|BKBK)\b.*$",
    re.I,
)
AGROBANK_PARTY_NUMERIC_TAIL_RE = re.compile(r"(?:\s+|[-./])\d{5,}(?:[-./]\d+)*\s*$", re.I)


def _to_float(amount_token: str) -> float:
    """Parse Agrobank amount tokens, supporting leading-dot (.92) and trailing '-' for negatives."""
    s = (amount_token or "").strip()
    if not s:
        return 0.0
    neg = s.endswith("-")
    if neg:
        s = s[:-1]
    if s.startswith("."):
        s = "0" + s
    s = s.replace(",", "")
    v = float(s)
    return -v if neg else v


def _cluster_lines(words: List[Dict[str, Any]], y_tol: float = 2.0) -> List[List[Dict[str, Any]]]:
    """Cluster Agrobank words into visual lines using y proximity."""
    lines: List[Dict[str, Any]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for line in lines:
            if abs(word["top"] - line["top"]) <= y_tol:
                line["words"].append(word)
                line["top"] = (line["top"] * (len(line["words"]) - 1) + word["top"]) / len(line["words"])
                placed = True
                break
        if not placed:
            lines.append({"top": word["top"], "words": [word]})

    clustered: List[List[Dict[str, Any]]] = []
    for line in sorted(lines, key=lambda item: item["top"]):
        clustered.append(sorted(line["words"], key=lambda w: w["x0"]))
    return clustered


def _line_text(words: List[Dict[str, Any]]) -> str:
    return normalize_text(" ".join((word.get("text") or "").strip() for word in words))


def _is_footer_line(line_text: str) -> bool:
    upper = normalize_text(line_text).upper()
    if not upper:
        return True
    return any(keyword in upper for keyword in AGROBANK_FOOTER_KEYWORDS)


def _extract_non_amount_line_text(words: List[Dict[str, Any]]) -> str:
    return normalize_text(
        " ".join(
            (word.get("text") or "").strip()
            for word in words
            if not AMOUNT_RE.fullmatch((word.get("text") or "").strip())
            and not ZERO_RE.fullmatch((word.get("text") or "").strip())
        )
    )


def extract_agrobank_summary_totals(pdf: pdfplumber.PDF) -> Tuple[Optional[float], Optional[float]]:
    """Extract TOTAL DEBIT / TOTAL CREDIT from the statement footer (most reliable source)."""
    total_debit = None
    total_credit = None

    for page in reversed(pdf.pages):
        text = page.extract_text() or ""
        for line in text.splitlines():
            u = line.upper()

            if "TOTAL DEBIT" in u:
                m = re.search(r"([\d,]*\d?\.\d{2})", line)
                if m:
                    total_debit = _to_float(m.group(1))

            if "TOTAL CREDIT" in u:
                m = re.search(r"([\d,]*\d?\.\d{2})", line)
                if m:
                    total_credit = _to_float(m.group(1))

        if total_debit is not None and total_credit is not None:
            break

    return total_debit, total_credit


def extract_agrobank_account_holder(pdf: pdfplumber.PDF) -> str:
    """Best-effort extraction of the Agrobank account holder name from page 1."""
    if not pdf.pages:
        return ""

    first_page_text = pdf.pages[0].extract_text() or ""
    lines = [normalize_text(line) for line in first_page_text.splitlines() if normalize_text(line)]
    value_line_re = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+AGROBANK\b.*\b\d+$", re.I)
    for index, line in enumerate(lines):
        if not value_line_re.search(line):
            continue
        for candidate in lines[index + 1 :]:
            candidate_upper = candidate.upper()
            if any(keyword in candidate_upper for keyword in AGROBANK_FOOTER_KEYWORDS):
                break
            if re.search(r"\d", candidate):
                continue
            return candidate
    return ""


def extract_agrobank_transaction_category(description: str) -> str:
    """Map Agrobank transaction descriptions to a bank-specific category."""
    desc = normalize_text(description).upper()
    if not desc:
        return "Other"

    for category, pattern in AGROBANK_TRANSACTION_CATEGORY_PATTERNS:
        if pattern.search(desc):
            return category
    return "Other"


def _normalize_agrobank_party_name(name: str) -> str:
    cleaned = normalize_text(name).upper()
    if not cleaned:
        return "UNKNOWN"

    cleaned = re.sub(r"[^A-Z0-9/&\s]", " ", cleaned)
    cleaned = normalize_text(cleaned)

    normalized_tokens = []
    for token in cleaned.split():
        if token in {"SND", "SD"}:
            token = "SDN"
        if token in {"BH", "BDH", "B"} and any(existing == "SDN" for existing in normalized_tokens):
            token = "BHD"
        normalized_tokens.append(token)

    if "SDN" in normalized_tokens and "BHD" not in normalized_tokens:
        normalized_tokens.append("BHD")

    cleaned = " ".join(normalized_tokens).strip()
    return cleaned or "UNKNOWN"


def _is_generic_agrobank_non_party(name: str) -> bool:
    cleaned = normalize_text(name).upper()
    if not cleaned or cleaned == "UNKNOWN":
        return True

    normalized = re.sub(r"[^A-Z0-9/&\s]", " ", cleaned)
    tokens = [token for token in normalize_text(normalized).split() if token]
    if not tokens:
        return True

    return all(token in AGROBANK_GENERIC_NON_PARTY_TOKENS for token in tokens)


def _looks_like_suspicious_agrobank_party(name: str) -> bool:
    cleaned = normalize_text(name).upper()
    if not cleaned or cleaned == "UNKNOWN":
        return True

    tokens = cleaned.split()
    return len(tokens) == 1 and len(tokens[0]) <= 3 and tokens[0].isalpha()


def extract_agrobank_party_name(description: str, account_holder: str = "") -> str:
    """Extract a counterparty-ish name from Agrobank transaction descriptions."""
    desc = normalize_text(description).upper()
    if not desc:
        return "UNKNOWN"

    if re.search(r"\bCDM\s+CA\s+DEPOSIT\b", desc, re.I):
        return "CDM CA DEPOSIT"

    segments = [normalize_text(part).upper() for part in re.split(r"\s+\|\s+", description) if normalize_text(part)]
    account_holder_norm = normalize_text(account_holder).upper()

    if re.search(r"\bMBISM[YK]KL\b", desc, re.I) and account_holder_norm:
        return _normalize_agrobank_party_name(account_holder_norm)

    if segments:
        continuation_segments = segments[1:]
        if continuation_segments:
            meaningful_segments = []
            repeated_self_segments = []
            for segment in continuation_segments:
                if segment == account_holder_norm:
                    repeated_self_segments.append(segment)
                    continue
                if any(pattern.fullmatch(segment) for pattern in AGROBANK_CONTINUATION_SKIP_PATTERNS):
                    continue
                if REFERENCE_RE.fullmatch(segment):
                    continue
                if _is_generic_agrobank_non_party(segment):
                    continue
                meaningful_segments.append(segment)

            if meaningful_segments:
                candidate = _normalize_agrobank_party_name(meaningful_segments[0])
                if not _is_generic_agrobank_non_party(candidate):
                    return candidate
            if account_holder_norm and repeated_self_segments:
                return _normalize_agrobank_party_name(account_holder_norm)
            return "UNKNOWN"

    for pattern in AGROBANK_PARTY_PATTERNS:
        match = pattern.search(desc)
        if not match:
            continue

        party = normalize_text(match.group("party"))
        party = AGROBANK_PARTY_TRAILING_RE.sub("", party)
        party = AGROBANK_PARTY_NUMERIC_TAIL_RE.sub("", party)
        party = re.sub(r"[^A-Z0-9/&\s]", " ", party)
        party = normalize_text(party)

        if party:
            candidate = _normalize_agrobank_party_name(party)
            if not _is_generic_agrobank_non_party(candidate):
                return candidate

    return "UNKNOWN"


def resolve_agrobank_party_name(
    description: str,
    existing_party_name: str = "",
    account_holder: str = "",
) -> str:
    candidate = normalize_text(existing_party_name).upper()
    if candidate and not _looks_like_suspicious_agrobank_party(candidate) and not _is_generic_agrobank_non_party(candidate):
        return _normalize_agrobank_party_name(candidate)

    refined_candidate = extract_agrobank_party_name(description, account_holder=account_holder)
    if refined_candidate and refined_candidate != "UNKNOWN":
        return refined_candidate

    if candidate:
        if _is_generic_agrobank_non_party(candidate):
            return "UNKNOWN"
        return _normalize_agrobank_party_name(candidate)

    return "UNKNOWN"


def parse_agro_bank(pdf: pdfplumber.PDF, source_file: str) -> List[Dict[str, Any]]:
    """
    Agrobank parser (pdfplumber)

    Important behavior:
    - Emit BEGINNING BALANCE and CLOSING BALANCE as synthetic rows.
    - Keep balance-delta inference for debit/credit.
    - Capture continuation lines so Agrobank party names survive parsing.
    """

    transactions: List[Dict[str, Any]] = []
    previous_balance: Optional[float] = None
    last_transaction: Optional[Dict[str, Any]] = None

    summary_debit, summary_credit = extract_agrobank_summary_totals(pdf)
    account_holder = extract_agrobank_account_holder(pdf)

    for page_num, page in enumerate(pdf.pages, start=1):
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
        lines = _cluster_lines(words)

        i = 0
        page_prefixed_to_previous = False
        continuation_zone_active = False
        while i < len(lines):
            line_words = lines[i]
            token = (line_words[0].get("text") or "").strip() if line_words else ""
            line_text = _line_text(line_words)

            if not continuation_zone_active and any(
                marker in line_text.upper()
                for marker in ("AKTIVITI AKAUN ANDA", "DATE REFERENCE NO.", "DEBIT(-)/CREDIT")
            ):
                continuation_zone_active = True

            if (
                not page_prefixed_to_previous
                and continuation_zone_active
                and last_transaction is not None
                and token
                and not DATE_RE.fullmatch(token)
            ):
                first_x0 = float(line_words[0].get("x0", 0.0)) if line_words else 0.0
                if first_x0 >= 180.0 and not _is_footer_line(line_text):
                    cleaned_line = _extract_non_amount_line_text(line_words)
                    if cleaned_line:
                        base_description = normalize_text(last_transaction.get("description"))
                        last_transaction["description"] = (
                            f"{base_description} | {cleaned_line}" if base_description else cleaned_line
                        )
                        last_transaction["party_name"] = extract_agrobank_party_name(
                            last_transaction["description"],
                            account_holder=account_holder,
                        )
                        i += 1
                        continue

            if DATE_RE.fullmatch(token):
                amounts = [
                    (w["x0"], (w["text"] or "").strip())
                    for w in line_words
                    if AMOUNT_RE.fullmatch((w["text"] or "").strip())
                ]
                amounts.sort(key=lambda x: x[0])

                if not amounts:
                    i += 1
                    continue

                page_prefixed_to_previous = True

                balance = _to_float(amounts[-1][1])

                description_parts = [
                    w["text"] for w in line_words
                    if not DATE_RE.fullmatch((w["text"] or "").strip())
                    and not AMOUNT_RE.fullmatch((w["text"] or "").strip())
                    and not ZERO_RE.fullmatch((w["text"] or "").strip())
                ]

                continuation_parts: List[str] = []
                next_index = i + 1
                while next_index < len(lines):
                    next_line_words = lines[next_index]
                    next_line_text = _line_text(next_line_words)
                    next_first_token = (next_line_words[0].get("text") or "").strip() if next_line_words else ""

                    if DATE_RE.fullmatch(next_first_token):
                        break
                    if _is_footer_line(next_line_text):
                        break

                    cleaned_line = _extract_non_amount_line_text(next_line_words)
                    if cleaned_line:
                        continuation_parts.append(cleaned_line)
                    next_index += 1

                description = normalize_text(" ".join(description_parts))
                if continuation_parts:
                    description = " | ".join([description] + continuation_parts)

                iso_date = datetime.strptime(token, "%d/%m/%y").strftime("%Y-%m-%d")
                desc_upper = description.upper()

                if "BEGINNING BALANCE" in desc_upper:
                    transactions.append(
                        {
                            "date": iso_date,
                            "description": "BEGINNING BALANCE",
                            "debit": None,
                            "credit": None,
                            "balance": round(balance, 2),
                            "transaction_category": "Balance Marker",
                            "party_name": "UNKNOWN",
                            "page": page_num,
                            "bank": "Agrobank",
                            "source_file": source_file,
                            "is_balance_marker": True,
                        }
                    )
                    previous_balance = balance
                    i = next_index
                    continue

                if "CLOSING BALANCE" in desc_upper:
                    debit = credit = None
                    if previous_balance is not None:
                        delta = balance - previous_balance
                        if delta > 0.0001:
                            credit = round(delta, 2)
                        elif delta < -0.0001:
                            debit = round(abs(delta), 2)

                    transactions.append(
                        {
                            "date": iso_date,
                            "description": "CLOSING BALANCE",
                            "debit": debit,
                            "credit": credit,
                            "balance": round(balance, 2),
                            "transaction_category": "Balance Marker",
                            "party_name": "UNKNOWN",
                            "page": page_num,
                            "bank": "Agrobank",
                            "source_file": source_file,
                            "is_balance_marker": True,
                        }
                    )
                    previous_balance = balance
                    i = next_index
                    continue

                debit = credit = None
                if previous_balance is not None:
                    delta = balance - previous_balance
                    if delta > 0.0001:
                        credit = round(delta, 2)
                    elif delta < -0.0001:
                        debit = round(abs(delta), 2)

                transactions.append(
                    {
                        "date": iso_date,
                        "description": description,
                        "debit": debit,
                        "credit": credit,
                        "balance": round(balance, 2),
                        "transaction_category": extract_agrobank_transaction_category(description),
                        "party_name": extract_agrobank_party_name(description, account_holder=account_holder),
                        "page": page_num,
                        "bank": "Agrobank",
                        "source_file": source_file,
                    }
                )

                previous_balance = balance
                last_transaction = transactions[-1]
                i = next_index
                continue

            i += 1

    computed_debit = round(sum(t.get("debit") or 0 for t in transactions), 2)
    computed_credit = round(sum(t.get("credit") or 0 for t in transactions), 2)

    mismatch = False
    if summary_debit is not None and abs(computed_debit - summary_debit) > 0.01:
        mismatch = True
    if summary_credit is not None and abs(computed_credit - summary_credit) > 0.01:
        mismatch = True

    for t in transactions:
        t["summary_check"] = "#" if mismatch else ""

    return transactions