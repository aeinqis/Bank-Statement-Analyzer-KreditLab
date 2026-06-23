# cimb.py - CIMB Bank Parser (robust)
#
# CIMB quirks handled:
# - Statement table is usually reverse chronological (latest is #1).
# - "Opening Balance" often appears without a date and is printed on page 1.
# - "Closing Balance / Baki Penutup" appears near end of PDF -> scan full doc text.
# - Extraction can duplicate rows with wrapped descriptions -> dedupe ignoring description.
#
# Output:
# - Standard transaction rows
# - Synthetic OPENING BALANCE (PAGE 1) row if detected
# - Synthetic CLOSING BALANCE / BAKI PENUTUP row if detected
#   (plus optional statement totals metadata on the closing row)

import re
from collections import defaultdict
from datetime import datetime

from party_utils import clean_counterparty_name, deduplicate_counterparty_names


# -----------------------------
# Regex
# -----------------------------

_MONEY_TOKEN_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$")

_STMT_DATE_RE = re.compile(
    r"(?:STATEMENT\s+DATE|TARIKH\s+PENYATA)\s*[:\s]+(\d{1,2})/(\d{1,2})/(\d{2,4})",
    re.IGNORECASE,
)

_CLOSING_RE = re.compile(
    r"CLOSING\s+BALANCE\s*/\s*BAKI\s+PENUTUP\s+(-?[\d,]+\.\d{2})",
    re.IGNORECASE,
)

_OPENING_LINE_RE = re.compile(r"^\s*OPENING\s+BALANCE\b", re.IGNORECASE)

CIMB_PARTY_TRAILING_RE = re.compile(
    r"\b(?:REF(?:ERENCE)?|TRACE|ID|NO|TXN|TRANSACTION|ACC(?:OUNT)?|A/C|PAYMENT|INVOICE|INV)\b.*$",
    re.I,
)

CIMB_SKIP_PARTY_LINES_RE = re.compile(
    r"""(?ix)
    ^(?:IBG\ CREDIT|AUTOPAY\ CR|AUTOPAY\ DR|AUTOPAY\ CHARGES|
       DUITNOW\ TO\ ACCOUNT|TR\ TO\ SAVINGS|TR\ TO\ C/A|TR\ IBG|
       REMITTANCE\ CR|I-PAYMENT|JOMPAY|OTHER\ TRANSFER\ FEE|
       HOUSE\ CHQ\ DR|HSE\ CHQ\ DEPOSIT|2D\ LOCAL\ CHQ|
       CHQ\ PROCESSING\ FEE|ACCOUNT\ STATUS\ CONFIRMATION\ CHARGE|
       IBG\ INWARD\ RETURN|I-FUNDS\ TR\ FROM\ SA)$
    |^\d{4,}$
    |^[A-Z0-9]{8,}$
    |.*\.TXT$
    """
)

CIMB_COUNTERPARTY_RULES = [
    (re.compile(r"\b(?:CHQ|CHEQUE)\b", re.I), "CHEQUE"),
    (re.compile(r"^(?:CDM\s+)?CASH\s+DEPOSIT$", re.I), "CASH DEPOSIT"),
    (re.compile(r"^JOMPAY\s+(?P<counterparty>\S+).*$", re.I), None),
    (
        re.compile(
            r"^TR IBG\s+(?P<counterparty>.+?)(?:\s+(?:CLAIM|GENERAL LABOUR|AC|DET\s+\d+|"
            r"ROADTAX.*|INSURANCE.*|HOUSE\s+RENTAL.*|RENTAL.*|SEWA.*|PAYMENT.*|INVOICE.*|"
            r"PETTY\s+CASH.*|EC\s+EXCEL.*|TRANSFER\s+BACK.*|MONTHLY\s+INSTALMENT.*|"
            r"[A-Z]{0,3}\d{3,}[A-Z0-9]*))?$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^TR TO\s+(?:C/A|SAVINGS)\s+(?P<counterparty>.+?)(?:\s+(?:AC|FROM\s+.+|GENERAL LABOUR|SPONSER.*))?$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^TR FROM\s+(?:CA|SA)\s+.+?\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^DUITNOW TO ACCOUNT\s+\S+\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^IBG CREDIT\s+(?:INTERBANK\s+GIRO\s+){0,2}(?:\S+\s+\S+\s+)?(?P<counterparty>[A-Z][A-Z0-9&()./\- ]+)$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^I-FUNDS TR FROM\s+(?:CA|SA)\s+.+?\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^I-PAYMENT\s+FPXPAY\s+(?P<counterparty>.+?)(?:\s+[A-Z]?\d{6,}.*)?$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^REMITTANCE CR(?:\s+(?P<counterparty>[A-Z][A-Z .&()/-]{3,}))?$",
            re.I,
        ),
        None,
    ),
    (
        re.compile(
            r"^(?P<counterparty>DEBIT ADVICE(?: LETTER SUPPORT FEES?)?|BIZCHANNEL MTHLY FEE|COMMISSION - CO|STAMP DUTY - CO|ACCOUNT STATUS CONFIRMATION CHARGE CHARGES|SERVICE CHARGE CANCELLATION BCQ|CLOSING BALANCE / BAKI PENUTUP)$",
            re.I,
        ),
        None,
    ),
    (re.compile(r"^ATM TRANSFER FROM\s+(?:CA|SA)$", re.I), "ATM TRANSFER"),
    (re.compile(r"^IBG INWARD RETURN .+$", re.I), "IBG RETURN"),
]

CIMB_PERSON_NAME_MARKER_RE = re.compile(r"\b(?:BIN|BINTI|BT|B|ANAK)\b", re.I)
CIMB_PERSON_PURPOSE_SUFFIX_RE = re.compile(
    r"\s+(?:HOUSE\s+RENTAL|GENERAL\s+LABOUR|PETTY\s+CASH|EC\s+EXCEL|"
    r"CLAIM|MILEAGE|LOAN|ROADTAX|INSURANCE|RENTAL|TENDER|FAREWELL|"
    r"TRANSFER\s+BACK(?:\s+TO\b.*)?|MONTHLY\s+INSTALMENT|"
    r"PERUNTUKAN(?:\s+BAJET)?|BAJET)\b.*$",
    re.I,
)
CIMB_TRANSFER_LEADING_CONTEXT_RE = re.compile(
    r"^(?:HOSPITAL\s+\S+|KLINIK\s+\S+|CLINIC\s+\S+|PUSAT\s+\S+|"
    r"SEKOLAH\s+\S+|SCHOOL\s+\S+)\s+",
    re.I,
)
CIMB_COMPANY_HINT_TOKENS = {"SDN", "BHD", "BERHAD", "PLT", "LLP", "PL", "ENTERPRISE", "TRADING"}
CIMB_TRANSFER_FEE_RE = re.compile(r"\bOTHER\s+TRANSFER\s+FEE\b", re.I)


# -----------------------------
# Basic helpers
# -----------------------------

def parse_float(value):
    """Convert string like '1,234.56' or '-1,234.56' to float. Return 0.0 if invalid."""
    if value is None:
        return 0.0
    s = str(value).replace("\n", " ").strip()
    s = s.replace(" ", "").replace(",", "")
    if not s:
        return 0.0
    if not re.match(r"^-?\d+(\.\d+)?$", s):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def clean_text(text):
    if not text:
        return ""
    return str(text).replace("\n", " ").strip()


def normalize_cimb_party_name(name: str) -> str:
    cleaned = clean_text(name).upper()
    cleaned = CIMB_PARTY_TRAILING_RE.sub("", cleaned)
    cleaned = re.sub(r"[^A-Z0-9/&().\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Common OCR/truncation normalisation.
    cleaned = re.sub(r"\bSDN\.?\s*BH?$", "SDN BHD", cleaned)
    cleaned = re.sub(r"\bSDN\.?\s*BHD\.?$", "SDN BHD", cleaned)
    cleaned = strip_cimb_person_purpose_suffix(cleaned)

    return cleaned or "UNKNOWN"


def strip_cimb_person_purpose_suffix(name: str) -> str:
    cleaned = clean_text(name).upper()
    if not CIMB_PERSON_NAME_MARKER_RE.search(cleaned):
        return cleaned

    stripped = CIMB_PERSON_PURPOSE_SUFFIX_RE.sub("", cleaned).strip()
    if stripped and CIMB_PERSON_NAME_MARKER_RE.search(stripped):
        return stripped

    return cleaned


def normalize_cimb_rule_party_name(name: str) -> str:
    cleaned = clean_text(name).upper()
    cleaned = re.sub(r"[^A-Z0-9/&().\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    cleaned = re.sub(r"\bSDN\.?\s*BH?$", "SDN BHD", cleaned)
    cleaned = re.sub(r"\bSDN\.?\s*BHD\.?$", "SDN BHD", cleaned)
    cleaned = strip_cimb_person_purpose_suffix(cleaned)

    return cleaned or "UNKNOWN"


def extract_cimb_trailing_uppercase_party(text: str) -> str:
    raw = clean_text(text)
    if not raw or not re.search(r"[a-z]", raw):
        return ""

    match = re.search(
        r"\b(?P<counterparty>[A-Z][A-Z0-9&()./\-]*(?:\s+[A-Z][A-Z0-9&()./\-]*)*)\s*$",
        raw,
    )
    if not match:
        return ""

    party = normalize_cimb_rule_party_name(match.group("counterparty"))
    if len(party) < 4 or party in {"CA", "SA"}:
        return ""

    return party


def is_cimb_party_candidate_line(part: str) -> bool:
    part = clean_text(part).upper()
    if not part:
        return False
    if CIMB_SKIP_PARTY_LINES_RE.search(part):
        return False
    if re.fullmatch(r"[\d/., -]+", part):
        return False
    if re.search(r"\d{5,}", part):
        return False
    return bool(re.search(r"[A-Z]", part))


def extract_cimb_duitnow_party(line_parts) -> str:
    if not line_parts:
        return ""

    first_line = clean_text(line_parts[0]).upper()
    if not re.match(r"^DUITNOW TO ACCOUNT(?:\s+|$)", first_line, re.I):
        return ""

    if len(line_parts) > 1:
        for part in reversed(line_parts[1:]):
            if is_cimb_party_candidate_line(part):
                return normalize_cimb_rule_party_name(part)

    for ref_pat in [
        r"^DUITNOW TO ACCOUNT\s+\S+\s+\S+\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
        r"^DUITNOW TO ACCOUNT\s+\S+\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
        r"^DUITNOW TO ACCOUNT\s+(?:ONLINE\s+TRANSFER\s+){1,2}(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
    ]:
        match = re.match(ref_pat, first_line, re.I)
        if match:
            return normalize_cimb_rule_party_name(match.group("counterparty"))

    return ""



def extract_cimb_tr_from_party(line_parts, raw_description: str) -> str:
    if not line_parts:
        return ""

    first_line = clean_text(line_parts[0]).upper()
    if not re.match(r"^TR FROM\s+(?:CA|SA)(?:\s+|$)", first_line, re.I):
        return ""

    if len(line_parts) > 1:
        for part in reversed(line_parts[1:]):
            if is_cimb_party_candidate_line(part):
                return normalize_cimb_rule_party_name(part)

    trailing_party = extract_cimb_trailing_uppercase_party(raw_description)
    if trailing_party:
        return trailing_party

    match = re.match(
        r"^TR FROM\s+(?:CA|SA)\s+.+?\s+(?P<counterparty>[A-Z][A-Z0-9&()./\- ]{3,})$",
        first_line,
        re.I,
    )
    if not match:
        return ""

    return normalize_cimb_rule_party_name(match.group("counterparty"))


def strip_cimb_transfer_leading_context(text: str) -> str:
    cleaned = clean_text(text).upper()
    stripped = CIMB_TRANSFER_LEADING_CONTEXT_RE.sub("", cleaned).strip()
    return stripped or cleaned


def extract_cimb_person_transfer_party(body: str) -> str:
    cleaned = normalize_cimb_rule_party_name(body)
    if not cleaned:
        return ""

    without_purpose = CIMB_PERSON_PURPOSE_SUFFIX_RE.sub("", cleaned).strip()
    context_stripped = strip_cimb_transfer_leading_context(without_purpose)
    had_leading_context = context_stripped != without_purpose

    if CIMB_PERSON_NAME_MARKER_RE.search(context_stripped):
        return normalize_cimb_rule_party_name(context_stripped)

    if without_purpose == cleaned:
        return ""

    tokens = context_stripped.split()
    if len(tokens) < 2:
        return ""

    if not had_leading_context:
        if len(tokens) > 3:
            return ""
        if any(token in CIMB_COMPANY_HINT_TOKENS for token in tokens):
            return ""

    if len(tokens) > 3:
        tokens = tokens[-3:]

    party = normalize_cimb_rule_party_name(" ".join(tokens))
    if re.fullmatch(r"(?:PERUNTUKAN|BAJET|HOSPITAL|KLINIK|CLINIC|PUSAT|SEKOLAH|SCHOOL)(?:\s+.*)?", party):
        return ""

    return party


def extract_cimb_tr_to_party(line_parts, raw_description: str) -> str:
    if not line_parts:
        return ""

    flattened = clean_text(re.sub(r"\s*\|\s*|\n+", " ", str(raw_description)))
    match = re.match(
        r"^TR TO\s+(?:C/A|SAVINGS)\s+(?P<body>.+)$",
        flattened,
        re.I,
    )
    if not match:
        return ""

    return extract_cimb_person_transfer_party(match.group("body"))


def extract_cimb_party_name_by_rule(description: str) -> str:
    if not description:
        return ""

    raw = str(description)
    normalized_full = clean_text(re.sub(r"\s*\|\s*", " ", raw)).upper()
    line_parts = [
        clean_text(p)
        for p in re.split(r"\s*\|\s*|\n+", raw)
        if clean_text(p)
    ]

    duitnow_party = extract_cimb_duitnow_party(line_parts)
    if duitnow_party:
        return duitnow_party

    tr_from_party = extract_cimb_tr_from_party(line_parts, raw)
    if tr_from_party:
        return tr_from_party

    tr_to_party = extract_cimb_tr_to_party(line_parts, raw)
    if tr_to_party:
        return tr_to_party

    autopay_party = _extract_autopay_party(raw)
    if autopay_party:
        return autopay_party

    remittance_party = _extract_remittance_party(raw)
    if remittance_party:
        return remittance_party

    candidates = []
    for candidate in [normalized_full, *(part.upper() for part in line_parts)]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        for pattern, fixed_party in CIMB_COUNTERPARTY_RULES:
            match = pattern.search(candidate)
            if not match:
                continue

            party = fixed_party or match.groupdict().get("counterparty")
            if party:
                return normalize_cimb_rule_party_name(party)

    return ""


def extract_cimb_party_name(description: str) -> str:
    """
    CIMB rule:
    party is usually the last non-reference, non-transaction-type line
    in the multiline description.
    """
    if not description:
        return "UNKNOWN"

    # Catch OTHER TRANSFER FEE early — avoids it leaking into the fallback
    if is_cimb_transfer_fee(description):
        return "TRANSFER FEE"

    party_by_rule = extract_cimb_party_name_by_rule(description)
    if party_by_rule:
        return party_by_rule

    parts = [
        clean_text(p).upper()
        for p in re.split(r"\s*\|\s*|\n+", description)
        if clean_text(p)
    ]

    candidates = []
    for part in parts:
        part = clean_text(part).upper()

        if not is_cimb_party_candidate_line(part):
            continue

        candidates.append(part)

    if not candidates:
        return "UNKNOWN"

    return normalize_cimb_party_name(candidates[-1])


def is_cimb_transfer_fee(description: str) -> bool:
    return bool(CIMB_TRANSFER_FEE_RE.search(clean_text(description).upper()))


def _extract_autopay_party(description: str) -> str:
    """Extract counterparty from AUTOPAY CR/DR by stripping the prefix and any
    reference tokens that contain digits. Party name tokens are purely alphabetic."""
    m = re.match(r"^AUTOPAY\s+(?:CR|DR)\s+(.+)$", description, re.I)
    if not m:
        return ""
    tokens = m.group(1).strip().split()
    # Reference codes always contain digits; party name tokens do not
    party_tokens = [t for t in tokens if not re.search(r"\d", t) and len(t) >= 2]
    if not party_tokens:
        return ""
    party = normalize_cimb_rule_party_name(" ".join(party_tokens))
    return party if len(party) >= 4 else ""


def _extract_remittance_party(description: str) -> str:
    """Extract counterparty from REMITTANCE CR — party name is always the final
    alphabetic-only tokens after all the numeric reference segments."""
    m = re.match(r"^REMITTANCE\s+CR\s+(.+)$", description, re.I)
    if not m:
        return ""
    tokens = m.group(1).strip().split()
    party_tokens: list[str] = []
    for token in reversed(tokens):
        if re.search(r"\d", token):
            break          # hit a ref code; stop collecting
        if len(token) >= 2:
            party_tokens.insert(0, token)
    if not party_tokens:
        return ""
    party = normalize_cimb_rule_party_name(" ".join(party_tokens))
    return party if len(party) >= 4 else ""


def annotate_cimb_counterparties(rows):
    """Attach raw + clean counterparty fields and fuzzy-dedupe within a statement."""
    raw_names = []
    for row in rows:
        desc = row.get("description", "")
        if is_cimb_transfer_fee(desc):
            raw = "TRANSFER FEE"
            row["category"] = "TRANSFER FEE"
        else:
            raw = (
                row.get("counterparty_name_raw")
                or row.get("counterparty_name")
                or row.get("party_name")
                or extract_cimb_party_name(desc)
                or "UNKNOWN"
            )
        raw = clean_text(raw).upper() or "UNKNOWN"
        row["counterparty_name_raw"] = raw
        raw_names.append(raw)

    clean_names = deduplicate_counterparty_names(raw_names)
    for row, clean_name in zip(rows, clean_names):
        clean_name = clean_name or "UNKNOWN"
        row["counterparty_name_clean"] = clean_name
        row["counterparty_name"] = clean_name
        row["party_name"] = clean_name

    return rows


def group_cimb_by_party(transactions):
    grouped = defaultdict(lambda: {
        "party_name": "",
        "count": 0,
        "total_debit": 0.0,
        "total_credit": 0.0,
        "transactions": [],
    })

    if any("counterparty_name_clean" not in tx for tx in transactions):
        annotate_cimb_counterparties(transactions)

    for tx in transactions:
        party = tx.get("counterparty_name_clean") or tx.get("party_name") or extract_cimb_party_name(tx.get("description", ""))
        tx["party_name"] = party

        g = grouped[party]
        g["party_name"] = party
        g["count"] += 1
        g["total_debit"] += tx.get("debit") or 0.0
        g["total_credit"] += tx.get("credit") or 0.0
        g["transactions"].append(tx)

    return list(grouped.values())


def format_date(date_str, year):
    """
    Convert 'DD/MM/YYYY' or 'DD/MM' into 'YYYY-MM-DD'.
    """
    if not date_str:
        return None
    s = clean_text(date_str)

    m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        dd, mm, yyyy = m.groups()
        return f"{yyyy}-{mm}-{dd}"

    m = re.match(r"(\d{2})/(\d{2})$", s)
    if m:
        dd, mm = m.groups()
        return f"{year}-{mm}-{dd}"

    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s

    return None


def extract_year_from_text(text):
    if not text:
        return None
    m = re.search(
        r"(?:STATEMENT\s+DATE|TARIKH\s+PENYATA)\s*[:\s]+\d{1,2}/\d{1,2}/(\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    y = m.group(1)
    return y if len(y) == 4 else str(2000 + int(y))


def extract_closing_balance_from_text(text):
    if not text:
        return None
    m = _CLOSING_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _extract_statement_totals_from_text(full_text):
    """
    Extract TOTAL WITHDRAWAL (debit) and TOTAL DEPOSITS (credit) from footer block.
    Layout often includes counts first, then two amounts:
      <no_wd> <no_dep> <total_withdrawal> <total_deposits>
    Returns (td, tc) or (None, None).
    """
    if not full_text:
        return (None, None)

    up = full_text.upper()
    if "TOTAL WITHDRAWAL" not in up or "TOTAL DEPOSITS" not in up:
        return (None, None)

    idx = up.rfind("TOTAL WITHDRAWAL")
    window = full_text[idx: idx + 900] if idx != -1 else full_text

    m = re.search(r"\b\d{1,6}\s+\d{1,6}\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\b", window)
    if m:
        return (parse_float(m.group(1)), parse_float(m.group(2)))

    money = re.findall(r"-?[\d,]+\.\d{2}", window)
    if len(money) >= 2:
        return (parse_float(money[-2]), parse_float(money[-1]))

    return (None, None)


def _prev_month(yyyy: int, mm: int):
    if mm == 1:
        return (yyyy - 1, 12)
    return (yyyy, mm - 1)


def _infer_statement_month_from_statement_date(full_text):
    """
    CIMB statement date is usually next month; statement month = previous month.
    Returns 'YYYY-MM' or None.
    """
    m = _STMT_DATE_RE.search(full_text or "")
    if not m:
        return None
    mm = int(m.group(2))
    yy_raw = m.group(3)
    yy = (2000 + int(yy_raw)) if len(yy_raw) == 2 else int(yy_raw)
    if not (1 <= mm <= 12 and 2000 <= yy <= 2100):
        return None
    py, pm = _prev_month(yy, mm)
    return f"{py:04d}-{pm:02d}"


def _dedupe_cimb(rows):
    """
    CIMB-specific dedupe:
    ignore description differences (wrapping/spacing).
    Key by (date, debit, credit, balance).
    """
    seen = set()
    out = []
    for r in rows:
        key = (
            str(r.get("date") or "").strip(),
            round(parse_float(r.get("debit", 0.0)), 2),
            round(parse_float(r.get("credit", 0.0)), 2),
            None if r.get("balance") is None else round(parse_float(r.get("balance")), 2),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _chronological_sort(rows):
    """
    CIMB table is reverse chronological (latest first).
    Convert to chronological (oldest first):
      sort by (date asc, extracted_index desc)
    so within same date we also reverse the order.
    """
    def key(r):
        return (r.get("date") or "9999-99-99", -int(r.get("__idx", 0)))
    return sorted(rows, key=key)


def _extract_last_balance_token(line):
    """
    Return (balance_float, first_money_index)
    """
    toks = line.split()
    last_idx = None
    for i in range(len(toks) - 1, -1, -1):
        if _MONEY_TOKEN_RE.match(toks[i]):
            last_idx = i
            break
    if last_idx is None:
        return None, None

    bal = parse_float(toks[last_idx])

    first_money_idx = None
    for i, t in enumerate(toks):
        if t == "0" or _MONEY_TOKEN_RE.match(t):
            first_money_idx = i
            break

    return bal, first_money_idx


# -----------------------------
# Text fallback parser (if tables fail)
# -----------------------------

def _parse_transactions_cimb_text(pdf, source_filename, detected_year, bank_name, closing_balance):
    """
    Text parser:
    - collect rows with date/desc/balance (raw order)
    - reorder to chronological
    - infer debit/credit by balance delta (fallback only)
    - capture opening balance line (no date) and emit synthetic opening row
    """
    raw = []
    idx = 0
    prev_balance = None
    latest_tx_date = None

    opening_balance_value = None
    opening_balance_page = None

    cur = None  # {"date":..., "parts":[...], "page":...}

    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        for ln in lines:
            up = ln.upper()

            # Opening balance line (no date)
            if _OPENING_LINE_RE.match(ln):
                bal, _ = _extract_last_balance_token(ln)
                if bal is not None:
                    opening_balance_value = bal
                    opening_balance_page = page_num
                    prev_balance = bal
                continue

            # ignore closing balance line here
            if "CLOSING BALANCE" in up and "BAKI" in up:
                continue

            # Start of transaction
            m = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.*)$", ln)
            if m:
                cur = {"date": m.group(1), "parts": [m.group(2)], "page": page_num}

                # sometimes includes balance same line
                bal, first_money_idx = _extract_last_balance_token(ln)
                if bal is not None:
                    toks = ln.split()
                    desc = " ".join(toks[1:first_money_idx]) if first_money_idx is not None else " ".join(toks[1:])
                    date_iso = format_date(cur["date"], detected_year)
                    if date_iso:
                        idx += 1
                        raw.append({
                            "date": date_iso,
                            "description": clean_text(desc),
                            "party_name": extract_cimb_party_name(desc),
                            "balance": round(bal, 2),
                            "page": page_num,
                            "__idx": idx,
                        })
                        if latest_tx_date is None or date_iso > latest_tx_date:
                            latest_tx_date = date_iso
                    cur = None
                continue

            # Continuation
            if cur is not None:
                bal, first_money_idx = _extract_last_balance_token(ln)
                if bal is not None:
                    toks = ln.split()
                    cur["parts"].append(" ".join(toks[:first_money_idx]) if first_money_idx is not None else ln)
                    desc_source = "\n".join(cur["parts"])
                    date_iso = format_date(cur["date"], detected_year)
                    if date_iso:
                        idx += 1
                        raw.append({
                            "date": date_iso,
                            "description": clean_text(" ".join(cur["parts"])),
                            "party_name": extract_cimb_party_name(desc_source),
                            "balance": round(bal, 2),
                            "page": cur["page"],
                            "__idx": idx,
                        })
                        if latest_tx_date is None or date_iso > latest_tx_date:
                            latest_tx_date = date_iso
                    cur = None
                else:
                    cur["parts"].append(ln)

    # Full-doc closing fallback if needed
    if closing_balance is None:
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        closing_balance = extract_closing_balance_from_text(full_text)

    # reorder before delta inference
    raw = _chronological_sort(raw)

    txs = []
    for r in raw:
        desc = r.get("description")
        bal = parse_float(r.get("balance"))
        debit = credit = 0.0
        if prev_balance is not None:
            delta = round(bal - prev_balance, 2)
            if delta > 0:
                credit = delta
            elif delta < 0:
                debit = -delta

        txs.append({
            "date": r.get("date"),
            "description": desc,
            "party_name": r.get("party_name") or extract_cimb_party_name(desc),
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "balance": round(bal, 2),
            "page": r.get("page"),
            "source_file": source_filename,
            "bank": bank_name,
            "__idx": r.get("__idx", 0),
        })
        prev_balance = bal

    # Emit synthetic opening row (labeled clearly)
    if opening_balance_value is not None:
        anchor = latest_tx_date or (txs[0]["date"] if txs else f"{detected_year}-01-01")
        opening_date = f"{anchor[:8]}01" if re.match(r"^\d{4}-\d{2}-\d{2}$", anchor) else f"{detected_year}-01-01"
        txs.insert(0, {
            "date": opening_date,
            "description": "OPENING BALANCE (PAGE 1)",
            "party_name": "UNKNOWN",
            "debit": 0.0,
            "credit": 0.0,
            "balance": round(float(opening_balance_value), 2),
            "page": opening_balance_page,
            "source_file": source_filename,
            "bank": bank_name,
            "is_opening_balance": True,
            "opening_balance_source": "page_1",
            "__idx": -1,
        })

    # Emit synthetic closing row
    if closing_balance is not None:
        cb_date = latest_tx_date or (txs[-1]["date"] if txs else f"{detected_year}-01-01")
        txs.append({
            "date": cb_date,
            "description": "CLOSING BALANCE / BAKI PENUTUP",
            "party_name": "UNKNOWN",
            "debit": 0.0,
            "credit": 0.0,
            "balance": round(float(closing_balance), 2),
            "page": None,
            "source_file": source_filename,
            "bank": bank_name,
            "is_statement_balance": True,
            "__idx": 10**12,
        })

    txs = _dedupe_cimb(txs)
    txs = annotate_cimb_counterparties(txs)
    for t in txs:
        t.pop("__idx", None)
    return txs


# -----------------------------
# Main parser
# -----------------------------

def parse_transactions_cimb(pdf, source_filename=""):
    """
    Parse CIMB statement using pdfplumber.
    Prefer extract_table; fallback to text parsing if tables missing.
    """
    bank_name = "CIMB Bank"
    detected_year = None

    # quick branding + year
    for page in pdf.pages[:2]:
        text = page.extract_text() or ""
        if "CIMB ISLAMIC BANK" in text.upper():
            bank_name = "CIMB Islamic Bank"
        if not detected_year:
            detected_year = extract_year_from_text(text)

    if not detected_year:
        detected_year = str(datetime.now().year)

    # Full PDF text (critical for closing + totals + statement month)
    full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    closing_balance = extract_closing_balance_from_text(full_text)
    stmt_total_debit, stmt_total_credit = _extract_statement_totals_from_text(full_text)
    stmt_month = _infer_statement_month_from_statement_date(full_text)

    # Extract opening balance if present in table rows (often no date)
    opening_balance_value = None
    opening_balance_page = None

    rows = []
    idx = 0
    latest_tx_date = None

    for page_num, page in enumerate(pdf.pages, start=1):
        table = page.extract_table()
        if not table:
            continue

        for row in table:
            # Expected: [Date, Desc, Ref, Withdrawal, Deposit, Balance]
            if not row or len(row) < 6:
                continue

            first_col = str(row[0]).lower() if row[0] else ""
            if "date" in first_col or "tarikh" in first_col:
                continue

            desc_raw = row[1]
            desc = clean_text(desc_raw)
            desc_l = desc.lower()

            # opening balance row may appear here; capture balance but do not treat as tx
            if "opening balance" in desc_l:
                ob = parse_float(row[5])
                if ob != 0.0:
                    opening_balance_value = ob
                    opening_balance_page = page_num
                continue

            # require balance
            if row[5] is None:
                continue

            date_iso = format_date(row[0], detected_year)
            if not date_iso:
                continue

            debit_val = parse_float(row[3])
            credit_val = parse_float(row[4])

            # skip rows without amounts (continuations)
            if debit_val == 0.0 and credit_val == 0.0:
                continue

            bal = parse_float(row[5])

            if latest_tx_date is None or date_iso > latest_tx_date:
                latest_tx_date = date_iso

            idx += 1
            rows.append({
                "date": date_iso,
                "description": desc,
                "party_name": extract_cimb_party_name(desc_raw),
                "ref_no": clean_text(row[2]),
                "debit": round(debit_val, 2),
                "credit": round(credit_val, 2),
                "balance": round(bal, 2),
                "page": page_num,
                "source_file": source_filename,
                "bank": bank_name,
                "__idx": idx,  # extraction order
            })

    # If table mode failed, fallback to text mode (also labels opening row)
    if not rows:
        return _parse_transactions_cimb_text(
            pdf,
            source_filename=source_filename,
            detected_year=detected_year,
            bank_name=bank_name,
            closing_balance=closing_balance,
        )

    # Deduplicate then reorder to chronological
    rows = _dedupe_cimb(rows)
    rows = _chronological_sort(rows)

    # Emit synthetic opening row if we captured it (labeled clearly)
    if opening_balance_value is not None:
        anchor = latest_tx_date or (rows[0]["date"] if rows else f"{detected_year}-01-01")
        opening_date = f"{anchor[:8]}01" if re.match(r"^\d{4}-\d{2}-\d{2}$", anchor) else f"{detected_year}-01-01"
        rows.insert(0, {
            "date": opening_date,
            "description": "OPENING BALANCE (PAGE 1)",
            "party_name": "UNKNOWN",
            "ref_no": "",
            "debit": 0.0,
            "credit": 0.0,
            "balance": round(float(opening_balance_value), 2),
            "page": opening_balance_page,
            "source_file": source_filename,
            "bank": bank_name,
            "is_opening_balance": True,
            "opening_balance_source": "page_1",
            "__idx": -1,
        })

    # Emit synthetic closing row from footer
    if closing_balance is not None:
        cb_date = latest_tx_date or (rows[-1]["date"] if rows else f"{detected_year}-01-01")
        rows.append({
            "date": cb_date,
            "description": "CLOSING BALANCE / BAKI PENUTUP",
            "party_name": "UNKNOWN",
            "ref_no": "",
            "debit": 0.0,
            "credit": 0.0,
            "balance": round(float(closing_balance), 2),
            "page": None,
            "source_file": source_filename,
            "bank": bank_name,
            "is_statement_balance": True,
            # optional metadata
            "statement_month": stmt_month,
            "statement_total_debit": None if stmt_total_debit is None else round(float(stmt_total_debit), 2),
            "statement_total_credit": None if stmt_total_credit is None else round(float(stmt_total_credit), 2),
            "__idx": 10**12,
        })

    # Final dedupe after adding synthetic rows
    rows = _dedupe_cimb(rows)
    rows = annotate_cimb_counterparties(rows)

    # Remove internal field
    for r in rows:
        r.pop("__idx", None)

    return rows
