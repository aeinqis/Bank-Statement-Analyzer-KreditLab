import re
import fitz  # PyMuPDF
import pdfplumber
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from core_utils import normalize_text


# ======================================================
# Helper: read PDF bytes safely (Streamlit / file / path)
# ======================================================
def _read_pdf_bytes(pdf_input: Any) -> bytes:
    """Return PDF bytes from bytes, Streamlit UploadedFile, file-like, or filesystem path."""
    if isinstance(pdf_input, (bytes, bytearray)):
        return bytes(pdf_input)

    # Streamlit UploadedFile
    if hasattr(pdf_input, "getvalue"):
        data = pdf_input.getvalue()
        if data:
            return data

    # file-like object
    if hasattr(pdf_input, "read"):
        try:
            pdf_input.seek(0)
        except Exception:
            pass
        data = pdf_input.read()
        if data:
            return data

    # path string
    if isinstance(pdf_input, str):
        with open(pdf_input, "rb") as f:
            return f.read()

    raise ValueError("Unable to read PDF bytes")


# -----------------------------
# Shared parsing helpers
# -----------------------------
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# Money tokens in RHB statements often look like:
#   27,286.00
#   746,858.49-
#   0.00
_MONEY_TOKEN_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})*\.\d{2}[+-]?$|^[+-]?\d+\.\d{2}[+-]?$")
RHB_REFLEX_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
RHB_REFLEX_MONEY_RE = re.compile(
    r"^(?:-|(?:\d{1,3}(?:,\d{3})*|\d+)?\.\d{2}|\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})(?:[+-])?$"
)
RHB_REFLEX_COLUMNS = {
    "description": (86, 140),
    "party_name": (140, 194),
    "reference_1": (194, 248),
    "reference_2": (248, 306),
    "refnum": (306, 362),
    "debit": (362, 438),
    "credit": (438, 515),
    "balance": (515, 590),
}
FOOTER_RE = re.compile(
    r"www\.rhbgroup\.com|For Any Enquiries|please call|603-92068118",
    re.I,
)
RHB_PREFIX_RE = re.compile(r"""
^
(?:
    RFLX\s+INSTANT\s+TRF\s+DR |
    RFLX\s+INST\s+TRF\s+CR\s+REV |
    RPP\s+INWARD\s+INST\s+TRF\s+CR |
    MBK\s+INSTANT\s+TRF\s+DR |
    IBK\s+INSTANT\s+TRF\s+DR |
    MB\s+FUND\s+TRF-(?:CR|DR) |
    DUITNOW\s+QR\s+P2P\s+(?:CR|DR) |
    DUITNOW\s+QR\s+POS\s+(?:CR|DR) |
    REFLEX-FUNDS\s+TFR\s+(?:CR|DR) |
    FPX\s+B2B\s+BUYER\s+DR |
    FPX\s+DD\s+SELLER\s+DR |
    INWARD\s+IBG |
    MB\s+NBPS\s+PYMT\s+DR |
    MYDEBIT\s+- |
    IBK\s+FPX-B2C\s+DR
)
\s+\d{10}\s*
(?P<body>.*)$
""", re.I | re.X)
RHB_RPP_INWARD_PARTY_RE = re.compile(
    r"^RPP\s+INWARD\s+INST\s+TRF(?:\s+CR)?(?:\s+\d{6,})?\s+(?P<body>.+)$",
    re.I,
)
RHB_INDIAN_PARENTAGE_RE = re.compile(r"\bA\s*/\s*([LP])\b", re.I)
RHB_NOTE_TAIL_RE = re.compile(r"""
\s+
(?:
    FUND\s+TRANSFER |
    QR\s+PAYMENT(?:\s+QR\s+\S*)? |
    PAYMENT\s+QR\s+\S* |
    INSTANT\s+TRANSFER |
    TRANSFER |
    REVERSAL |
    REV
)
\s*$
""", re.I | re.X)
RHB_PARTY_ALIASES = {
    "NOOR AZLAN BIN MOHAM": "NOOR AZLAN BIN MOHAMED ISA",
    "TENAGA KK RESOURCES SDN BHD": "TENAGA KK RESOURCES SDN BHD",
}
RHB_QR_PAYMENT_RE = re.compile(r"""
^
(?:
    (?=[A-Z0-9]*\d)[A-Z0-9]{12,} |
    [A-Z]{1,5}\d{8,} |
    \d{8,}
)?
\s*
QR\s+(?:P2P|POS)\s+PAYMENT\s+
(?P<party>.*?)
(?:
    \s+
    (?:
        (?=[A-Z0-9]*\d)[A-Z0-9]{12,} |
        [A-Z]{1,5}\d{8,} |
        \d{8,}
    )
    (?:\s+\S+)*
)?
$
""", re.I | re.X)


def _strip_trailing_description_by_case(party: str) -> str:
    tokens = party.split()
    if len(tokens) < 2:
        return party

    for index, token in enumerate(tokens[1:], start=1):
        if not any(char.islower() for char in token):
            continue

        prefix_tokens = tokens[:index]
        prefix_letters = "".join(char for item in prefix_tokens for char in item if char.isalpha())
        if len(prefix_tokens) >= 2 and prefix_letters and prefix_letters == prefix_letters.upper():
            return " ".join(prefix_tokens).strip(" -/.")
    return party


def _normalize_rhb_indian_parentage_markers(value: str) -> str:
    return RHB_INDIAN_PARENTAGE_RE.sub(lambda match: f"A/{match.group(1).upper()}", str(value or ""))


def _extract_rhb_rpp_inward_party(description: str) -> str:
    match = RHB_RPP_INWARD_PARTY_RE.match(normalize_text(description))
    if not match:
        return ""

    party = _normalize_rhb_indian_parentage_markers(match.group("body"))
    party = re.split(r"\s+/\s+", party, maxsplit=1)[0]
    return normalize_text(party).strip(" -/.")


def _is_incomplete_rhb_party_name(value: str) -> bool:
    party = _normalize_rhb_indian_parentage_markers(normalize_text(value).upper())
    return party in {"A/L", "A/P", "A L", "A P"}


def _strip_leading_description_before_party(party: str) -> str:
    """Drop lowercase free-text notes before the actual uppercase party name."""
    tokens = party.split()
    if len(tokens) < 2:
        return party

    leading_text = ""
    for index, token in enumerate(tokens):
        token_letters = "".join(char for char in token if char.isalpha())
        if not token_letters or token_letters != token_letters.upper():
            continue

        candidate = " ".join(tokens[index:]).strip(" -/.")
        candidate_upper = candidate.upper()
        if not re.search(r"\b(?:SDN\.?\s*BHD\.?|BHD\.?|ENTERPRISE|TRADING|SERVICES|RESOURCES|MARKETING)\b", candidate_upper):
            continue
        if not leading_text:
            leading_text = " ".join(tokens[:index])
        leading_letters = "".join(char for char in leading_text if char.isalpha())
        if leading_letters and leading_letters != leading_letters.upper():
            return candidate

    return party


def _strip_repeated_parenthetical_party(party: str) -> str:
    match = re.match(
        r"^(?P<party>(?P<name>[A-Z0-9/&@().\s-]+?)\s+\([A-Z0-9/&@().\s-]+\))\s+(?P=name)\b.*$",
        party,
        flags=re.I,
    )
    if match:
        return match.group("party").strip(" -/.")
    return party


def _extract_qr_payment_party(party: str) -> str:
    match = RHB_QR_PAYMENT_RE.match(party)
    if not match:
        return party
    return match.group("party").strip()


def _looks_like_reference_token(token: str) -> bool:
    cleaned = token.strip(" -/.")
    return bool(
        re.fullmatch(r"\d{5,}", cleaned)
        or re.fullmatch(r"(?=[A-Z0-9]*\d)[A-Z0-9]{10,}", cleaned, re.I)
    )


def _strip_leading_reference_tokens(party: str) -> str:
    tokens = party.split()
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if _looks_like_reference_token(token):
            index += 1
            continue
        if (
            len(token) == 1
            and token.isalpha()
            and index + 1 < len(tokens)
            and _looks_like_reference_token(tokens[index + 1])
        ):
            index += 1
            continue
        break

    return " ".join(tokens[index:]).strip() or party


def _strip_operational_party_tokens(party: str) -> str:
    party = re.sub(r"\bQR\s+PAYMENT\b.*$", " ", party, flags=re.I)
    party = re.sub(r"\bFUND\s+TRANSFER\b", " ", party, flags=re.I)
    party = re.sub(r"\bQR\b", " ", party, flags=re.I)

    tokens = [
        token
        for token in party.split()
        if not _looks_like_reference_token(token)
    ]
    return " ".join(tokens).strip() or party


def extract_rhb_party_name(description: str) -> str:
    desc = normalize_text(description)

    if re.match(r"^(?:ATM\s+)?CASH WITHDRAWAL\b", desc, re.I):
        return "CASH WITHDRAWAL"
    if re.match(r"^(?:CDT\s+|CDM\s+)?CASH DEPOSIT\b", desc, re.I):
        return "CASH DEPOSIT"
    if re.match(r"^SERVICE CHARGE\b", desc, re.I):
        return "UNKNOWN"
    if re.match(
        r"^(LOCAL CHQ DEP|RFLX INSTANT TRF SC DR|REFLEX INST TRF SC REV|RFLX INSTANT TRF DR \d{10}$|RPP INWARD INST TRF CR \d{10}$)",
        desc,
        re.I,
    ):
        return "UNKNOWN"

    party = _extract_rhb_rpp_inward_party(desc)
    if not party:
        m = RHB_PREFIX_RE.search(desc)
        if not m:
            return "UNKNOWN"
        party = m.group("body").strip()

    # Reflex/FPX/IBG formats often carry operational references before the party.
    party = _normalize_rhb_indian_parentage_markers(party)
    party = re.sub(r"^(?:\d{12,}\s+)+", "", party)
    party = re.sub(r"^invoice\s+T\d+\s+", "", party, flags=re.I)
    party = re.sub(r"^(?:PV\d+\s+)?(?:AP-\d+(?:-\d+)?\s+)?", "", party, flags=re.I)
    party = re.sub(r"^(?:\d{12,}\s+|(?=[A-Z0-9]*\d)[A-Z0-9]{10,}\s+)+", "", party, flags=re.I)

    # Remove channel / QR IDs at the start.
    party = re.sub(r"^(?:IBK\s+)?", "", party, flags=re.I)
    party = re.sub(r"^(?:RHBQR\d+|MAEPP\d+|[0-9]{10,}RHBQR\d+)\s*", "", party, flags=re.I)

    # For old QR format: "... QR P2P/POS Payment NAME ID".
    party = _extract_qr_payment_party(party)
    party = re.sub(r"^QR\s+(?:P2P|POS)\s+PAYMENT\s+", "", party, flags=re.I)

    # FPX/IBG: remove leading payment references before merchant.
    party = _strip_leading_reference_tokens(party)
    party = _strip_operational_party_tokens(party)

    # Bill payments: keep merchant, remove account / bill refs.
    party = re.sub(r"\b(TM UNIFI)\s+\1\b.*$", r"\1", party, flags=re.I)

    # MYDEBIT card suffix.
    party = re.sub(r"\s+(?:[L]?MY\s+)?CARD\s+\d+\b.*$", "", party, flags=re.I)

    # RHB notes are often appended in lower/title/mixed case after an uppercase party name.
    party = _strip_leading_description_before_party(party)
    party = _strip_repeated_parenthetical_party(party)
    party = _strip_trailing_description_by_case(party)

    previous_party = None
    while previous_party != party:
        previous_party = party
        party = RHB_NOTE_TAIL_RE.sub("", party).strip()

    # Remove trailing numeric IDs, QR IDs, and long alphanumeric refs.
    party = re.sub(
        r"\s+(?:\d{5,}|RHBQR\d+|MAEPP\d+|(?=[A-Z0-9]*\d)[A-Z0-9]{12,})$",
        "",
        party,
        flags=re.I,
    )

    # Normalize common company abbreviations.
    party = re.sub(r"\bSDN\.\s*B(?:HD)?\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\s+B\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\b(SDN\s+BHD)\b.*$", r"\1", party, flags=re.I)

    # If exact duplicated name: BOSSTAR WORLD EMPIRE BOSSTAR WORLD EMPIRE.
    words = party.split()
    if len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            party = " ".join(words[:half])

    party = re.sub(r"[^A-Z0-9/&@().\s-]", " ", party.upper())
    party = re.sub(r"\s{2,}", " ", party).strip(" -/.")

    if party in {"IBK", "RHBQR000000", ""} or re.fullmatch(r"\d+", party):
        return "UNKNOWN"

    return RHB_PARTY_ALIASES.get(party, party)


def _money_to_float(token: str) -> Optional[float]:
    if token is None:
        return None
    s = str(token).strip().replace(" ", "")
    if not s:
        return None

    # parenthesis negative
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()

    trailing = None
    if s.endswith("+"):
        trailing = "+"
        s = s[:-1]
    elif s.endswith("-"):
        trailing = "-"
        s = s[:-1]

    s = s.replace(",", "")
    try:
        v = float(s)
    except Exception:
        return None

    if trailing == "-":
        v = -abs(v)
    elif trailing == "+":
        v = abs(v)
    return float(v)


def _clean_cell(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = FOOTER_RE.sub("", value).strip()
    return re.sub(r"\s+", " ", value).strip()


def _money_to_float_rhb(value: str) -> float:
    value = _clean_cell(value)
    match = re.search(
        r"-|(?:\d{1,3}(?:,\d{3})*|\d+)?\.\d{2}[+-]?|\d+\.\d{2}[+-]?",
        value,
    )
    if not match:
        return 0.0

    token = match.group(0)
    if token == "-":
        return 0.0

    sign = -1 if token.endswith("-") else 1
    token = token.rstrip("+-").replace(",", "")
    return round(sign * float(token), 2)


def _extract_first_money(value: str) -> str:
    value = _clean_cell(value)
    match = re.search(
        r"-|(?:\d{1,3}(?:,\d{3})*|\d+)?\.\d{2}[+-]?|\d+\.\d{2}[+-]?",
        value,
    )
    return match.group(0) if match else ""


def _join_rhb_reflex_description_parts(*parts: str) -> str:
    cleaned_parts: List[str] = []
    seen = set()

    for part in parts:
        cleaned = normalize_text(part).strip(" -")
        if not cleaned or cleaned == "-":
            continue
        key = cleaned.upper()
        if key in seen:
            continue
        seen.add(key)
        cleaned_parts.append(cleaned)

    description = " ".join(cleaned_parts)
    description = re.sub(r"\s*/\s*", " / ", description)
    description = re.sub(r"(?:\s+/\s*){2,}", " / ", description)
    return normalize_text(description).strip(" -/").upper()


def _extract_year_from_statement_period(text: str) -> Optional[int]:
    """Extract statement year from common RHB header lines.

    Supports:
      - "Statement Period ... : 1 Jan 25 – 31 Jan 25"
      - "Statement Period" + "01 May 2025 31 May 2025" (sometimes without a dash, often across lines)
      - Any "DD Mon YYYY" occurrence near statement-period headers as fallback

    Returns the *ending* year where available.
    """
    if not text:
        return None

    # Normalize spacing so cross-line patterns work.
    t = re.sub(r"\s+", " ", text).strip()

    # Case 1: explicit range with dash
    m = re.search(
        r"Statement\s+Period.*?:\s*\d{1,2}\s+[A-Za-z]{3,9}\s+(?P<y1>\d{2,4})\s*[-–—]\s*"
        r"\d{1,2}\s+[A-Za-z]{3,9}\s+(?P<y2>\d{2,4})",
        t,
        re.IGNORECASE,
    )
    if m:
        y = m.group("y2") or m.group("y1")
        return int(y) if len(y) == 4 else 2000 + int(y)

    # Case 2: "01 May 2025 31 May 2025" (no dash)
    m = re.search(
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+(?P<y1>\d{4})\s+\d{1,2}\s+[A-Za-z]{3,9}\s+(?P<y2>\d{4})\b",
        t,
        re.IGNORECASE,
    )
    if m:
        return int(m.group("y2"))

    # Case 3: weaker fallback: first year-like token near Statement Period
    m = re.search(
        r"Statement\s+Period.*?\b\d{1,2}\s+[A-Za-z]{3,9}\s+(?P<y>\d{2,4})\b",
        t,
        re.IGNORECASE,
    )
    if m:
        y = m.group("y")
        return int(y) if len(y) == 4 else 2000 + int(y)

    return None


def _guess_bank_name(header_upper: str) -> str:
    if "ISLAMIC" in header_upper:
        return "RHB Islamic Bank"
    return "RHB Bank"


def _is_non_transaction_commodity_page(page_text: str) -> bool:
    """Detect commodity-trading certificate pages that are not account transactions."""
    if not page_text:
        return False
    t = page_text.upper()
    return (
        ("SELLER/PENJUAL" in t and "BUYER/PEMBELI" in t)
        or ("CERTIFICATE NO" in t and "NET DEPOSIT" in t and "SELLING PRICE" in t)
        or ("COMMODITY" in t and "TRADING" in t)
    )


# ======================================================
# 1) RHB ACCOUNT STATEMENT — text based (older layout)
# ======================================================
def _parse_rhb_account_statement_text(pdf_bytes: bytes, source_filename: str) -> List[Dict]:
    transactions: List[Dict] = []

    DATE_START_RE = re.compile(r"^(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\b\s+(?P<rest>.*)$")
    NOISE_LINE_RE = re.compile(
        r"^(?:"
        r"ACCOUNT\s+ACTIVITY|DEPOSIT\s+ACCOUNT|DEPOSIT\s+ACCOUNT\s+SUMMARY|STATEMENT\s+PERIOD|"
        r"IMPORTANT\s+NOTES|IMPORTANT\s+ANNOUNCEMENTS|PAGE\s+NO\.?|RHB\s+BANK|"
        r"MEMBER\s+OF\s+PIDM|PROTECTED\s+BY\s+PIDM|DILINDUNGI\s+OLEH\s+PIDM|"
        r"PRODUCT\s+NAME|ACCOUNT\s+NO\.?|CURRENCY|DATE\s+DESCRIPTION|"
        r"CHEQUE\s+\/\s+SERIAL|DEBIT|CREDIT|BALANCE"
        r")\b",
        re.IGNORECASE,
    )

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        header = pdf.pages[0].extract_text(x_tolerance=1) or ""
        header_up = header.upper()

        # Heuristic: only run this parser if the statement looks like the account-statement format
        if "ACCOUNT STATEMENT" not in header_up and "PENYATA" not in header_up:
            return []

        year = _extract_year_from_statement_period(header) or datetime.now().year
        bank_name = _guess_bank_name(header_up)

        prev_balance: Optional[float] = None
        last_tx: Optional[Dict] = None

        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1) or ""
            if _is_non_transaction_commodity_page(text):
                continue
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]

            for line in lines:
                if NOISE_LINE_RE.match(line):
                    last_tx = None
                    continue

                # Totals / summary counters
                if re.match(r"^Total\s+Count\b", line, re.IGNORECASE):
                    last_tx = None
                    continue

                m = DATE_START_RE.match(line)
                if m:
                    dd = int(m.group("day"))
                    mon = m.group("mon").upper()
                    if mon not in _MONTH_MAP:
                        last_tx = None
                        continue

                    date_iso = f"{year:04d}-{_MONTH_MAP[mon]}-{dd:02d}"

                    tokens = line.split()
                    rest_tokens = tokens[2:]  # drop day + mon

                    money_idx = [i for i, t in enumerate(rest_tokens) if _MONEY_TOKEN_RE.match(t)]
                    if not money_idx:
                        last_tx = None
                        continue

                    bal_token = rest_tokens[money_idx[-1]]
                    balance = _money_to_float(bal_token)
                    if balance is None:
                        last_tx = None
                        continue

                    # Description is everything before the numeric columns start
                    desc_tokens = rest_tokens[:money_idx[0]]
                    description = " ".join(desc_tokens).strip()

                    # Opening/closing balance lines (do not emit as transactions)
                    up_desc = description.upper()
                    if "B/F" in up_desc:
                        prev_balance = balance
                        last_tx = None
                        continue
                    if "C/F" in up_desc:
                        last_tx = None
                        continue

                    # Debit/credit from delta if possible
                    debit = credit = 0.0
                    if prev_balance is not None:
                        delta = round(balance - prev_balance, 2)
                        if delta < 0:
                            debit = abs(delta)
                        elif delta > 0:
                            credit = delta

                    tx = {
                        "date": date_iso,
                        "description": description[:200],
                        "debit": round(debit, 2),
                        "credit": round(credit, 2),
                        "balance": round(balance, 2),
                        "page": page_num,
                        "bank": bank_name,
                        "source_file": source_filename,
                    }
                    transactions.append(tx)
                    prev_balance = balance
                    last_tx = tx
                else:
                    # Continuation line
                    if last_tx is not None:
                        extra = line.strip()
                        if extra and not NOISE_LINE_RE.match(extra):
                            last_tx["description"] = (last_tx["description"] + " " + extra).strip()[:200]

    return transactions


# ======================================================
# 2) RHB ISLAMIC — older text-based format (kept, but guarded)
# ======================================================
def _parse_rhb_islamic_text(pdf_bytes: bytes, source_filename: str) -> List[Dict]:
    transactions: List[Dict] = []
    previous_balance: Optional[float] = None

    balance_re = re.compile(r"(?P<bal>[\d,]+\.\d{2}[+-]?)\s*$")
    date_re = re.compile(r"(?P<d>\d{1,2})\s+(?P<m>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        header = pdf.pages[0].extract_text(x_tolerance=1) or ""
        year = _extract_year_from_statement_period(header) or datetime.now().year

        header_up = header.upper()
        # Reflex Cash Management / Transaction Statement PDFs are handled by the layout-based parser.
        if ("REFLEX" in header_up) or ("CASH MANAGEMENT" in header_up) or ("DEPOSIT ACCOUNT SUMMARY" in header_up) or ("TRANSACTION STATEMENT" in header_up):
            return []

        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text:
                continue
            if _is_non_transaction_commodity_page(text):
                continue

            for line in text.splitlines():
                bal_match = balance_re.search(line.strip())
                date_match = date_re.search(line)
                if not bal_match or not date_match:
                    continue

                balance = _money_to_float(bal_match.group("bal"))
                if balance is None:
                    continue

                if re.search(r"\bB/F\b|\bC/F\b", line):
                    previous_balance = balance
                    continue

                if previous_balance is None:
                    previous_balance = balance
                    continue

                day = int(date_match.group("d"))
                month = date_match.group("m")
                date_iso = datetime.strptime(f"{day:02d} {month} {year}", "%d %b %Y").strftime("%Y-%m-%d")

                delta = round(balance - previous_balance, 2)
                debit = round(abs(delta), 2) if delta < 0 else 0.0
                credit = round(delta, 2) if delta > 0 else 0.0

                desc = balance_re.sub("", line)
                desc = desc.replace(date_match.group(0), "")
                desc = re.sub(r"\s+", " ", desc).strip()

                transactions.append(
                    {
                        "date": date_iso,
                        "description": desc,
                        "debit": debit,
                        "credit": credit,
                        "balance": round(balance, 2),
                        "page": page_index,
                        "bank": "RHB Islamic Bank",
                        "source_file": source_filename,
                    }
                )

                previous_balance = balance

    return transactions


# ======================================================
# 3) RHB CONVENTIONAL — older text-based format (kept, but guarded)
# ======================================================
def _parse_rhb_conventional_text(pdf_bytes: bytes, source_filename: str) -> List[Dict]:
    transactions: List[Dict] = []
    previous_balance: Optional[float] = None

    balance_re = re.compile(r"(?P<bal>[\d,]+\.\d{2}[+-]?)\s*$")
    # supports "05Jan" and "05 Jan"
    date_re = re.compile(r"(?P<d>\d{1,2})\s*(?P<m>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        header = pdf.pages[0].extract_text(x_tolerance=1) or ""
        year = _extract_year_from_statement_period(header) or datetime.now().year

        header_up = header.upper()
        # Reflex Cash Management / Transaction Statement PDFs are handled by the layout-based parser.
        if ("REFLEX" in header_up) or ("CASH MANAGEMENT" in header_up) or ("DEPOSIT ACCOUNT SUMMARY" in header_up) or ("TRANSACTION STATEMENT" in header_up):
            return []

        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text:
                continue
            if _is_non_transaction_commodity_page(text):
                continue

            for line in text.splitlines():
                bal_m = balance_re.search(line.strip())
                date_m = date_re.search(line)
                if not bal_m or not date_m:
                    continue

                balance = _money_to_float(bal_m.group("bal"))
                if balance is None:
                    continue

                if previous_balance is None:
                    previous_balance = balance
                    continue

                day = int(date_m.group("d"))
                month = date_m.group("m")
                date_iso = datetime.strptime(f"{day:02d} {month} {year}", "%d %b %Y").strftime("%Y-%m-%d")

                delta = round(balance - previous_balance, 2)
                debit = round(abs(delta), 2) if delta < 0 else 0.0
                credit = round(delta, 2) if delta > 0 else 0.0

                desc = balance_re.sub("", line)
                desc = desc.replace(date_m.group(0), "")
                desc = re.sub(r"\s+", " ", desc).strip()

                transactions.append(
                    {
                        "date": date_iso,
                        "description": desc,
                        "debit": debit,
                        "credit": credit,
                        "balance": round(balance, 2),
                        "page": page_index,
                        "bank": "RHB Bank",
                        "source_file": source_filename,
                    }
                )

                previous_balance = balance

    return transactions


# ======================================================
# 4) RHB REFLEX — layout based (kept as-is)
# ======================================================
def _parse_rhb_reflex_text_line(line: str, source_filename: str, page_index: int) -> Optional[Dict]:
    match = re.match(
        r"^(?P<date>\d{2}-\d{2}-\d{4})\s+"
        r"(?P<body>.*?)\s+"
        r"(?P<debit>-|[\d,]+\.\d{2})\s+"
        r"(?P<credit>-|[\d,]+\.\d{2})\s+"
        r"(?P<balance>[\d,]+\.\d{2}[+-]?)$",
        re.sub(r"\s+", " ", line).strip(),
    )
    if not match:
        return None

    def money_value(value: str) -> float:
        value = str(value or "").strip()
        if value in {"", "-"}:
            return 0.0
        return float(value.replace(",", ""))

    balance = _money_to_float(match.group("balance"))
    if balance is None:
        return None

    description = match.group("body")
    description = re.sub(r"^\d{3}\s+", "", description)
    description = re.sub(r"\bRPP\s+INWARD\s+INST\s+TRF\b", "RPP", description, flags=re.I)
    description = re.sub(r"\s+\d{7,}\s*$", "", description)
    description = re.sub(r"\s*/\s*", " / ", description)
    description = re.sub(r"\s{2,}", " ", description).strip(" -/")
    if not description:
        return None

    return {
        "date": datetime.strptime(match.group("date"), "%d-%m-%Y").strftime("%Y-%m-%d"),
        "description": description[:200],
        "debit": round(money_value(match.group("debit")), 2),
        "credit": round(money_value(match.group("credit")), 2),
        "balance": round(balance, 2),
        "page": page_index,
        "bank": "RHB Bank",
        "source_file": source_filename,
    }


def _parse_rhb_reflex_text_table(pdf_bytes: bytes, source_filename: str) -> List[Dict]:
    transactions: List[Dict] = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        header = pdf.pages[0].extract_text(x_tolerance=1) if pdf.pages else ""
        header_up = (header or "").upper()
        if not (
            "REFLEX" in header_up
            or "CASH MANAGEMENT" in header_up
            or "TRANSACTION STATEMENT" in header_up
            or "AMOUNT (DR)" in header_up
            or "AMOUNT (CR)" in header_up
        ):
            return []

        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1) or ""
            if not text or _is_non_transaction_commodity_page(text):
                continue

            for raw_line in text.splitlines():
                transaction = _parse_rhb_reflex_text_line(raw_line, source_filename, page_index)
                if transaction:
                    transactions.append(transaction)

    return transactions


def _parse_rhb_reflex_layout(pdf_bytes: bytes, source_filename: str) -> List[Dict]:
    transactions: List[Dict] = []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        for page_index, page in enumerate(doc, start=1):
            raw_words = page.get_text("words")
            words = [
                {
                    "x0": w[0],
                    "x1": w[2],
                    "top": w[1],
                    "text": w[4].strip(),
                }
                for w in raw_words
                if w[4].strip()
            ]
            words.sort(key=lambda word: (word["top"], word["x0"]))

            date_words = [
                word for word in words
                if RHB_REFLEX_DATE_RE.match(word["text"])
            ]

            footer_top = min(
                [
                    word["top"] for word in words
                    if FOOTER_RE.search(word["text"])
                ],
                default=page.rect.height - 10,
            )

            for idx, date_word in enumerate(date_words):
                y_start = date_word["top"] - 1
                y_end = (
                    date_words[idx + 1]["top"] - 1
                    if idx + 1 < len(date_words)
                    else footer_top
                )
                y_end = min(y_end, footer_top)

                block_words = [
                    word for word in words
                    if y_start <= word["top"] < y_end
                    and not FOOTER_RE.search(word["text"])
                ]

                row = {
                    "date": datetime.strptime(date_word["text"], "%d-%m-%Y").strftime("%Y-%m-%d"),
                    "page": page_index,
                    "bank": "RHB Bank",
                    "source_file": source_filename,
                }

                for col_name, (x_min, x_max) in RHB_REFLEX_COLUMNS.items():
                    cell_words = []
                    for word in block_words:
                        x_mid = (word["x0"] + word["x1"]) / 2
                        if x_min <= x_mid < x_max:
                            cell_words.append(word["text"])

                    row[col_name] = _clean_cell(" ".join(cell_words))

                debit_token = _extract_first_money(row.get("debit", ""))
                credit_token = _extract_first_money(row.get("credit", ""))
                balance_token = _extract_first_money(row.get("balance", ""))

                if not balance_token:
                    continue

                row["debit"] = _money_to_float_rhb(debit_token)
                row["credit"] = _money_to_float_rhb(credit_token)
                row["balance"] = _money_to_float_rhb(balance_token)
                row["party_name"] = normalize_text(row.get("party_name", "")).upper()
                row["transaction_type"] = normalize_text(row.get("description", "")).upper()
                row["description"] = _join_rhb_reflex_description_parts(
                    row.get("transaction_type", ""),
                    row.get("party_name", ""),
                    row.get("reference_1", ""),
                    row.get("reference_2", ""),
                )

                transactions.append(row)

    finally:
        doc.close()

    return transactions


def _attach_rhb_party_names(transactions: List[Dict]) -> List[Dict]:
    for transaction in transactions:
        existing_party = normalize_text(transaction.get("party_name", ""))
        if existing_party and not _is_incomplete_rhb_party_name(existing_party):
            transaction["party_name"] = existing_party.upper()
        else:
            extracted_party = extract_rhb_party_name(transaction.get("description", ""))
            transaction["party_name"] = (
                extracted_party
                if extracted_party != "UNKNOWN" or not existing_party
                else existing_party.upper()
            )
    return transactions


def parse_transactions_rhb(pdf_input: Any, source_filename: str) -> List[Dict]:
    """Main entry used by app.py: returns list of canonical tx dicts.

    RHB has multiple PDF layouts. Some Reflex Cash Management PDFs contain month names in header
    summary lines (e.g., "31 May 2025") which can cause the older text-based parsers to emit
    bogus rows. For Reflex PDFs we therefore prefer the layout-based parser.
    """
    pdf_bytes = _read_pdf_bytes(pdf_input)

    header_up = ""
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            header = pdf.pages[0].extract_text(x_tolerance=1) or ""
            header_up = header.upper()
    except Exception:
        header_up = ""

    looks_like_reflex = (
        ("REFLEX" in header_up)
        or ("CASH MANAGEMENT" in header_up)
        or ("DEPOSIT ACCOUNT SUMMARY" in header_up)
        or ("TRANSACTION STATEMENT" in header_up)
    )

    # If it's a Reflex-style statement, try the layout-based parser first.
    if looks_like_reflex:
        for reflex_parser in (_parse_rhb_reflex_layout, _parse_rhb_reflex_text_table):
            try:
                tx = reflex_parser(pdf_bytes, source_filename)
                if tx:
                    return _attach_rhb_party_names(tx)
            except Exception:
                pass

    # Fallback order for other layouts
    for parser in (
        _parse_rhb_account_statement_text,
        _parse_rhb_islamic_text,
        _parse_rhb_conventional_text,
        _parse_rhb_reflex_layout,
        _parse_rhb_reflex_text_table,
    ):
        try:
            tx = parser(pdf_bytes, source_filename)
            if tx:
                return _attach_rhb_party_names(tx)
        except Exception:
            continue

    return []
