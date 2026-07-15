"""uob.py

UOB Malaysia - "Account Activities" PDF export parser.

Observed layout (from provided samples):
  - Header contains:
      Company
      Account <account_no> <company_name> MYR <account_no>
      Statement Date <dd/mm/yyyy> - <dd/mm/yyyy>
  - Transaction table columns:
      Statement Date | Transaction Date | Description | Deposit(MYR) | Withdrawal(MYR) | Ledger Balance(MYR)

Notes:
  - Descriptions are frequently multi-line.
  - Amount columns are consistently present as numeric tokens (e.g. 0.00, 1,090.00, -644,255.96).
  - This parser is resilient to line wraps by "row stitching": we detect a new row when a line
    starts with TWO dates (dd/mm/yyyy dd/mm/yyyy).

Output:
  A list of dicts with canonical keys: date, description, debit, credit, balance, page, bank, source_file.
  Extra metadata (company_name/account_no) may also be included and will be preserved by core_utils.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from core_utils import normalize_date, normalize_text, safe_float


BANK_NAME = "UOB Bank"


_STMT_TIME_RE = re.compile(r"^(?P<stmt>\d{2}/\d{2}/\d{4})\s+(?P<time>\d{2}:\d{2}:\d{2})(?:\s+(?P<rest>.*))?$")
_TRX_LINE_RE = re.compile(r"^(?P<trx>\d{2}/\d{2}/\d{4})\s+(?P<body>.*)$")
_MONEY_RE = re.compile(r"^-?(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$")
# Some UOB rows are packed tightly enough that the first payment rail/detail
# line sits just above the midpoint between two amount rows. Keep a small
# coordinate tolerance so labels such as DuitNow/JomPAY are not clipped.
_ROW_START_LOOKBACK_TOLERANCE = 3.0
_UOB_ROW_DETAIL_PREFIXES = (
    "DUITNOW/INSTANT TRF",
    "DUITNOW/INST TRF",
    "DUITNOW INSTANT TRF",
    "DUITNOW INST TRF",
    "JOMPAY",
    "JOM PAY",
    "FUND TRF",
    "MISC DR",
    "MISC CR",
    "IBG DR",
    "IBG CR",
    "RENTAS",
    "TELEGRAPHIC",
    "CASH",
    "CHEQUE DEPOSIT",
    "CHEQUE",
    "CHQ",
    "DR",
    "OD MAINTENANCE FEE",
    "OD COMMITMENT FEE",
    "OD INT CHARGE",
    "OD INT",
    "OD INTEREST",
    "OVERDRAFT INTEREST",
    "INTEREST",
)
_UOB_ROW_DETAIL_PREFIX_RE = re.compile(
    r"(?<![A-Z0-9])(?:"
    + "|".join(
        re.escape(prefix).replace(r"\ ", r"\s+")
        for prefix in sorted(_UOB_ROW_DETAIL_PREFIXES, key=len, reverse=True)
    )
    + r")\b",
    re.I,
)
_UOB_BANK_CODE_PATTERN = r"PBB|MBB|HLB|CIMB|RHB|ABMB|ABB|AMBG|OCBC|UOB"
UOB_BANK_CODE_PARTY_RE = re.compile(
    rf"\b(?:{_UOB_BANK_CODE_PATTERN})\s+"
    rf"(?P<party>[A-Z0-9&().'\-/ ]+?)"
    rf"(?=\|\||\||$)",
    re.I,
)
UOB_IBG_PARTY_RE = re.compile(
    rf"^IBG\s+CR\s+.*?\b(?:{_UOB_BANK_CODE_PATTERN})\s+"
    rf"(?P<party>[A-Z0-9&().'\-/ ]+)$",
    re.I,
)
UOB_FUND_TRF_RE = re.compile(
    r"^Fund\s+Trf\s+EB\s+(?P<party>.+)$",
    re.I,
)
UOB_DUITNOW_PARTY_RE = re.compile(
    r"^DuitNow/Instant\s+Trf\s+(?:C\d{5,}\s+)*(?P<party>.+)$",
    re.I,
)
UOB_TRADE_BILL_RE = re.compile(
    r"^DR\s+\S+\s+Trade\s+Bill\s+Transfer\b",
    re.I,
)
UOB_CHEQUE_RE = re.compile(
    r"\b(?:CHQ|CHEQUE|CHEQUE\s+DEPOSIT|CHQ\s+WDL|CHQ\s+PROCESSING\s+FEE)\b",
    re.I,
)
UOB_BANK_CHARGE_RE = re.compile(
    r"\b(?:OD\s+INT\s+CHARGE|OD\s+COMMITMENT\s+FEE|OD\s+MAINTENANCE\s+FEE|SERVICE\s+CHARGE)\b",
    re.I,
)


def _extract_header_meta(first_page_text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[float]]:
    """Return (company_name, account_no, statement_end_iso, ledger_balance_header)."""
    if not first_page_text:
        return None, None, None, None

    company_name: Optional[str] = None
    account_no: Optional[str] = None
    statement_end_iso: Optional[str] = None
    ledger_balance_header: Optional[float] = None

    # Company name: UOB export sometimes shows "Company Available Balance" followed by the name.
    m = re.search(
        r"Company\s+Available\s+Balance\s*\n\s*([A-Z0-9 &().,'\/-]{3,})\b",
        first_page_text,
        re.IGNORECASE,
    )
    if m:
        company_name = normalize_text(m.group(1))
        # sometimes the export appends currency + balance after the company name
        company_name = re.split(r"\bMYR\b", company_name, maxsplit=1, flags=re.IGNORECASE)[0].strip() or company_name

    # Fallback: the line after a standalone "Company" label.
    if not company_name:
        m = re.search(r"\bCompany\b\s*\n\s*([A-Z0-9 &().,'\/-]{3,})\s*(?:\n|$)", first_page_text, re.IGNORECASE)
        if m:
            cand = normalize_text(m.group(1))
            if cand and cand.upper() not in {"ACCOUNT", "COMPANY / ACCOUNT"}:
                company_name = cand

    # Account number: commonly shown beneath "Account Ledger Balance".
    m = re.search(r"Account\s+Ledger\s+Balance\s*\n\s*(\d{6,20})\b", first_page_text, re.IGNORECASE)
    if m:
        account_no = m.group(1)

    # Fallback: first long digit group after "Account" label.
    if not account_no:
        m = re.search(r"\bAccount\b\s*(?:\n\s*)?(\d{6,20})\b", first_page_text, re.IGNORECASE)
        if m:
            account_no = m.group(1)

    # Statement period end date.
    m = re.search(
        r"Statement\s+Date\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})",
        first_page_text,
        re.IGNORECASE,
    )
    if m:
        statement_end_iso = normalize_date(m.group(2))

    # Ledger balance header (used for no-transaction fallback)
    # Example: "... MYR -644,255.96" on the same line as the account number.
    m = re.search(r"Account\s+Ledger\s+Balance.*?\bMYR\b\s*([-()\d,]+\.\d{2})", first_page_text, re.IGNORECASE | re.DOTALL)
    if m:
        ledger_balance_header = safe_float(m.group(1))

    return company_name, account_no, statement_end_iso, ledger_balance_header


def _split_amounts_from_tail(body: str) -> Optional[Tuple[str, float, float, float]]:
    """Return (desc, deposit, withdrawal, balance) by taking last 3 money tokens."""
    tokens = (body or "").split()
    money_idx = [i for i, t in enumerate(tokens) if _MONEY_RE.match(t)]
    if len(money_idx) < 3:
        return None

    dep = safe_float(tokens[money_idx[-3]])
    wd = safe_float(tokens[money_idx[-2]])
    bal = safe_float(tokens[money_idx[-1]])

    desc = normalize_text(" ".join(tokens[: money_idx[-3]]))
    return desc, dep, wd, bal


def _is_header_or_export_line(up: str) -> bool:
    return (
        "DATE OF EXPORT" in up
        or up in {"ACCOUNT ACTIVITIES"}
        or up.startswith("STATEMENT DATE TRANSACTION DATE")
        or (
            up.startswith("STATEMENT DATE")
            and "TRANSACTION" in up
            and "DESCRIPTION" in up
        )
    )


def _dedupe_repeated_uob_party(party: str) -> str:
    party = normalize_text(party)
    if not party:
        return ""

    bank_split_parts = [
        normalize_text(part)
        for part in re.split(rf"\b(?:{_UOB_BANK_CODE_PATTERN})\b", party, flags=re.I)
        if normalize_text(part)
    ]
    if len(bank_split_parts) >= 2:
        longest = max(bank_split_parts, key=len)
        if all(
            part == longest
            or part in longest
            or longest in part
            for part in bank_split_parts
        ):
            return longest

    tokens = party.split()
    for size in range(len(tokens) // 2, 0, -1):
        if tokens[:size] == tokens[size : size * 2]:
            return " ".join(tokens[:size] + tokens[size * 2 :])
        if tokens[-size:] == tokens[-size * 2 : -size]:
            return " ".join(tokens[:-size])

    return party


def clean_uob_party_name(party: str) -> str:
    party = normalize_text(str(party or "")).upper()

    # Remove pipe/reference tail.
    party = re.sub(r"\|.*$", "", party)

    # Remove common references.
    party = re.sub(
        r"\b(?:C\d{5,}|INV\s*NO[: ]*\S+|PARTIAL\s+PAYMENT|PAYMENT|BILL\s+PAYMENT|SENT\s+FROM\s+AMONLINE)\b",
        " ",
        party,
        flags=re.I,
    )
    party = re.sub(r"\b\d{10,}\b", " ", party)

    # Normalize company suffixes.
    party = re.sub(r"\bSDN\.?\s*BHD\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BH\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*B\b", "SDN BHD", party, flags=re.I)

    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-|")
    party = _dedupe_repeated_uob_party(party)
    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-|")

    return party or "UNKNOWN"


def extract_uob_party_name(description: str) -> str:
    """Extract a counterparty name from UOB transaction descriptions."""
    desc = normalize_text(str(description or "")).strip()
    desc_up = desc.upper()

    if not desc_up:
        return "UNKNOWN"

    if UOB_CHEQUE_RE.search(desc_up):
        return "CHEQUE"

    if UOB_BANK_CHARGE_RE.search(desc_up):
        return "BANK CHARGES"

    if UOB_TRADE_BILL_RE.search(desc_up):
        return "TRADE BILL TRANSFER"

    matches = list(UOB_BANK_CODE_PARTY_RE.finditer(desc_up))
    if matches:
        return clean_uob_party_name(matches[-1].group("party"))

    match = UOB_DUITNOW_PARTY_RE.search(desc)
    if match:
        party = match.group("party")
        party = re.sub(r"\bC\d{5,}\b", " ", party, flags=re.I)
        return clean_uob_party_name(party)

    match = UOB_IBG_PARTY_RE.search(desc_up)
    if match:
        return clean_uob_party_name(match.group("party"))

    match = UOB_FUND_TRF_RE.search(desc)
    if match:
        party = match.group("party")
        party = re.sub(r"\bC\d{5,}\b", " ", party, flags=re.I)
        party = re.sub(r"\bUPELL\s+CORPORATION.*$", "", party, flags=re.I)
        return clean_uob_party_name(party)

    return "UNKNOWN"


def _make_uob_tx(
    *,
    trx_iso: str,
    post_iso: Optional[str],
    pending_time: Optional[str],
    pending_ampm: Optional[str],
    description: str,
    debit: float,
    credit: float,
    balance: float,
    page_idx: Optional[int],
    source_file: str,
    company_name: Optional[str],
    account_no: Optional[str],
    is_statement_balance: bool = False,
) -> Dict[str, Any]:
    tx = {
        "date": trx_iso,
        "description": description,
        "party_name": extract_uob_party_name(description),
        "debit": round(float(debit), 2),
        "credit": round(float(credit), 2),
        "balance": round(float(balance), 2),
        "page": page_idx,
        "bank": BANK_NAME,
        "source_file": source_file,
        "company_name": company_name,
        "account_no": account_no,
    }
    if post_iso is not None:
        tx.update(
            {
                "posting_date": post_iso,
                "transaction_date": trx_iso,
                "time": (
                    f"{pending_time} {pending_ampm}".strip()
                    if (pending_time and pending_ampm)
                    else pending_time
                ),
            }
        )
    if is_statement_balance:
        tx["is_statement_balance"] = True
        tx["is_balance_marker"] = True
    return tx


def _word_text(words: List[Dict[str, Any]]) -> str:
    sorted_words = sorted(
        words,
        key=lambda w: (float(w.get("x0", 0)), str(w.get("text", ""))),
    )
    return normalize_text(" ".join(str(w.get("text", "")) for w in sorted_words))


def _group_words_by_line(
    words: List[Dict[str, Any]], y_tolerance: float = 3.0
) -> List[Dict[str, Any]]:
    """Group pdfplumber words into visual lines while preserving coordinates.

    UOB's Account Activities export paints the description column above the
    date/amount baseline. Plain ``extract_text`` can therefore place detail
    lines before their row's dates, and the old line-stitcher may attach those
    details to the previous row. Keeping coordinates lets us rebuild each row
    from the table columns instead.
    """
    lines: List[Dict[str, Any]] = []
    for word in sorted(
        words or [], key=lambda w: (float(w.get("top", 0)), float(w.get("x0", 0)))
    ):
        top = float(word.get("top", 0))
        if not lines or abs(top - lines[-1]["top"]) > y_tolerance:
            lines.append({"top": top, "words": [word]})
        else:
            lines[-1]["words"].append(word)
            # Keep a stable top for wrapped words reported with tiny offsets.
            lines[-1]["top"] = min(lines[-1]["top"], top)
    for line in lines:
        line["text"] = _word_text(line["words"])
    return lines


def _line_money_values(line: Dict[str, Any]) -> List[Tuple[float, str]]:
    vals: List[Tuple[float, str]] = []
    for word in sorted(line.get("words", []), key=lambda w: float(w.get("x0", 0))):
        txt = str(word.get("text", ""))
        if _MONEY_RE.match(txt):
            vals.append((float(word.get("x0", 0)), txt))
    return vals


def _line_has_transaction_date(line: Dict[str, Any]) -> Optional[str]:
    for word in line.get("words", []):
        txt = str(word.get("text", ""))
        x0 = float(word.get("x0", 0))
        if 15 <= x0 <= 90 and re.match(r"^\d{2}/\d{2}/\d{4}$", txt):
            return txt
    return None


def _extract_statement_datetime_from_row(
    lines: List[Dict[str, Any]],
    top: float,
    bottom: float,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    stmt_date: Optional[str] = None
    stmt_time: Optional[str] = None
    ampm: Optional[str] = None
    for line in lines:
        y = float(line["top"])
        if y < top or y >= bottom:
            continue
        for word in sorted(line.get("words", []), key=lambda w: float(w.get("x0", 0))):
            txt = str(word.get("text", ""))
            x0 = float(word.get("x0", 0))
            if 95 <= x0 <= 185:
                if stmt_date is None and re.match(r"^\d{2}/\d{2}/\d{4}$", txt):
                    stmt_date = txt
                elif stmt_time is None and re.match(r"^\d{2}:\d{2}:\d{2}$", txt):
                    stmt_time = txt
                elif txt.upper() in {"AM", "PM"}:
                    ampm = txt.upper()
    return normalize_date(stmt_date) if stmt_date else None, stmt_time, ampm


def _extract_description_from_row(
    lines: List[Dict[str, Any]], top: float, bottom: float
) -> str:
    parts: List[str] = []
    for line in lines:
        y = float(line["top"])
        if y < top or y >= bottom:
            continue
        text_up = str(line.get("text", "")).upper().strip()
        if not text_up or _is_header_or_export_line(text_up):
            continue
        if (
            text_up.startswith("DATE OF EXPORT")
            or text_up.startswith("TOTAL DEPOSITS")
            or text_up.startswith("NOTE")
        ):
            continue
        if text_up.startswith("ACCOUNT TRANSACTIONS") or text_up.startswith("ACCOUNT TYPE"):
            continue
        if re.match(r"^\d+\s+OF\s+\d+$", text_up):
            continue

        desc_words = [
            word for word in line.get("words", []) if 185 <= float(word.get("x0", 0)) < 315
        ]
        if desc_words:
            part = _word_text(desc_words)
            if part and not _is_header_or_export_line(part.upper()):
                parts.append(part)
    desc = normalize_text(" ".join(parts))
    return desc or "(NO DESCRIPTION)"


def _is_uob_row_detail_start(text: str) -> bool:
    return bool(_UOB_ROW_DETAIL_PREFIX_RE.match(normalize_text(text)))


def _find_embedded_uob_row_detail_start(text: str) -> Optional[int]:
    desc = normalize_text(text)
    if not desc or _is_uob_row_detail_start(desc):
        return None

    match = _UOB_ROW_DETAIL_PREFIX_RE.search(desc)
    if not match or match.start() <= 0:
        return None
    return match.start()


def _has_double_pipe_boundary(text: str) -> bool:
    return "||" in normalize_text(text)


def _is_uob_prefix_only(text: str) -> bool:
    desc = normalize_text(text)
    match = _UOB_ROW_DETAIL_PREFIX_RE.match(desc)
    return bool(match and not desc[match.end() :].strip())


def _append_uob_description_fragment(tx: Dict[str, Any], fragment: str) -> None:
    fragment = normalize_text(fragment)
    if not fragment:
        return

    tx["description"] = normalize_text(
        f"{tx.get('description', '')} {fragment}".strip()
    )
    tx["party_name"] = extract_uob_party_name(tx.get("description", ""))


def _prepend_uob_description_fragment(tx: Dict[str, Any], fragment: str) -> None:
    fragment = normalize_text(fragment)
    if not fragment:
        return

    tx["description"] = normalize_text(
        f"{fragment} {tx.get('description', '')}".strip()
    )
    tx["party_name"] = extract_uob_party_name(tx.get("description", ""))


def _split_uob_leading_fragment(leading_fragment: str) -> Tuple[str, str]:
    leading_fragment = normalize_text(leading_fragment)
    if not leading_fragment:
        return "", ""

    match = re.match(
        r"^(?P<previous>.+?\|\|)\s+(?P<current>(?:DR|CR)\s+\S+)\s*$",
        leading_fragment,
        re.I,
    )
    if match:
        return normalize_text(match.group("previous")), normalize_text(
            match.group("current")
        )

    return leading_fragment, ""


def _repair_uob_row_description_starts(
    transactions: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    for idx in range(0, len(transactions) - 1):
        tx = transactions[idx]
        next_tx = transactions[idx + 1]
        if tx.get("is_statement_balance") or next_tx.get("is_statement_balance"):
            continue

        desc = normalize_text(tx.get("description", ""))
        next_desc = normalize_text(next_tx.get("description", ""))
        if (
            not desc
            or not next_desc
            or not _is_uob_row_detail_start(desc)
            or _is_uob_row_detail_start(next_desc)
        ):
            continue

        if _is_uob_prefix_only(desc):
            _prepend_uob_description_fragment(next_tx, desc)
            continue

        matches = list(_UOB_ROW_DETAIL_PREFIX_RE.finditer(desc))
        if len(matches) < 2:
            continue

        split_at = matches[-1].start()
        kept_desc = desc[:split_at].strip()
        next_prefix_fragment = desc[split_at:].strip()
        if not kept_desc or not next_prefix_fragment:
            continue

        tx["description"] = kept_desc
        tx["party_name"] = extract_uob_party_name(kept_desc)
        _prepend_uob_description_fragment(next_tx, next_prefix_fragment)

    for idx in range(1, len(transactions)):
        tx = transactions[idx]
        if tx.get("is_statement_balance"):
            continue

        desc = normalize_text(tx.get("description", ""))
        if not desc or desc.startswith("NO TRANSACTIONS"):
            continue

        split_at = _find_embedded_uob_row_detail_start(desc)
        if split_at is None:
            continue

        leading_fragment = desc[:split_at].strip()
        corrected_desc = desc[split_at:].strip()
        if not corrected_desc:
            continue
        if not _has_double_pipe_boundary(leading_fragment):
            continue

        previous_fragment, current_head = _split_uob_leading_fragment(leading_fragment)
        _append_uob_description_fragment(transactions[idx - 1], previous_fragment)
        if current_head:
            corrected_desc = normalize_text(f"{current_head} {corrected_desc}")
        tx["description"] = corrected_desc
        tx["party_name"] = extract_uob_party_name(corrected_desc)

    return transactions



def _find_uob_row_start(
    lines: List[Dict[str, Any]],
    prev_amount_top: Optional[float],
    amount_top: float,
    next_amount_top: Optional[float],
) -> float:
    """Find the visual top of one UOB transaction row.

    Use the EARLIEST transaction prefix between the previous amount row and the
    current amount row. The old code used the latest prefix, which can steal the
    next row prefix into the previous row.
    """
    if prev_amount_top is not None:
        lower = (prev_amount_top + amount_top) / 2.0
    else:
        lookback = ((next_amount_top - amount_top) * 0.55) if next_amount_top else 55.0
        lower = max(0.0, amount_top - min(85.0, lookback))

    lower = max(0.0, lower - _ROW_START_LOOKBACK_TOLERANCE)

    candidates: List[float] = []
    for line in lines:
        y = float(line["top"])
        if y < lower or y > amount_top:
            continue

        desc = _word_text(
            [
                word
                for word in line.get("words", [])
                if 185 <= float(word.get("x0", 0)) < 315
            ]
        )

        if desc and _is_uob_row_detail_start(desc):
            candidates.append(y)

    return min(candidates) if candidates else lower


def _split_leaked_next_prefix_from_tail(description: str) -> Tuple[str, str]:
    """Split an accidental next-row prefix from the tail of a description."""
    desc = normalize_text(description)
    if not desc:
        return "", ""

    matches = list(_UOB_ROW_DETAIL_PREFIX_RE.finditer(desc))
    if len(matches) < 2:
        return desc, ""

    split_at = matches[-1].start()
    left = desc[:split_at].strip()
    right = desc[split_at:].strip()

    if re.match(r"^(CHEQUE|CHQ)\b", right, re.I) and not _has_double_pipe_boundary(left):
        return desc, ""

    if left and right and _is_uob_row_detail_start(right):
        return left, right

    return desc, ""


def _fix_leaked_tail_fragments(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Move leaked prefixes/tails to the correct row.

    Fixes:
      1. "Trade Bill Transfer ... DuitNow/Instant Trf"
      2. "MOTOR|C017886| OD Maintenance Fee 001"
    """
    for idx in range(len(transactions) - 1):
        current_tx = transactions[idx]
        next_tx = transactions[idx + 1]

        current_desc = normalize_text(current_tx.get("description", ""))
        next_desc = normalize_text(next_tx.get("description", ""))

        kept, leaked_prefix = _split_leaked_next_prefix_from_tail(current_desc)
        if leaked_prefix:
            current_tx["description"] = kept
            current_tx["party_name"] = extract_uob_party_name(kept)

            if not next_desc.upper().startswith(leaked_prefix.upper()):
                next_tx["description"] = normalize_text(f"{leaked_prefix} {next_desc}")
                next_tx["party_name"] = extract_uob_party_name(next_tx["description"])

    for idx in range(1, len(transactions)):
        current_tx = transactions[idx]
        prev_tx = transactions[idx - 1]

        desc = normalize_text(current_tx.get("description", ""))
        if not desc:
            continue

        match = _UOB_ROW_DETAIL_PREFIX_RE.search(desc)
        if not match or match.start() <= 0:
            continue

        leading = desc[: match.start()].strip()
        rest = desc[match.start() :].strip()

        if (
            leading
            and rest
            and _has_double_pipe_boundary(leading)
            and not _is_uob_row_detail_start(leading)
        ):
            prev_tx["description"] = normalize_text(
                f"{prev_tx.get('description', '')} {leading}"
            )
            prev_tx["party_name"] = extract_uob_party_name(prev_tx["description"])

            current_tx["description"] = rest
            current_tx["party_name"] = extract_uob_party_name(rest)

    return transactions


def _parse_transactions_uob_words(
    pdf: pdfplumber.PDF,
    source_file: str,
    company_name: Optional[str],
    account_no: Optional[str],
) -> List[Dict[str, Any]]:
    """Parse UOB table rows using word coordinates.

    This is the preferred path for UOB Account Activities PDFs because the
    visual row order is not the same as plain extracted text: payment
    rails/details, such as JomPAY/DuitNow/Fund Transfer labels and references,
    can be printed above the row's date/amount line. Coordinate parsing keeps
    those pre-date details with the same transaction instead of appending them
    to the previous transaction.
    """
    transactions: List[Dict[str, Any]] = []

    for page_idx, page in enumerate(pdf.pages, start=1):
        words = (
            page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
            or []
        )
        if not words:
            continue
        lines = _group_words_by_line(words)

        amount_rows: List[Dict[str, Any]] = []
        for line in lines:
            trx_date = _line_has_transaction_date(line)
            monies = _line_money_values(line)
            if trx_date and len(monies) >= 3:
                amount_rows.append(
                    {"line": line, "trx_date": trx_date, "monies": monies[-3:]}
                )

        row_starts: List[float] = []
        for idx, row in enumerate(amount_rows):
            amount_top = float(row["line"]["top"])
            prev_top = float(amount_rows[idx - 1]["line"]["top"]) if idx else None
            next_top = (
                float(amount_rows[idx + 1]["line"]["top"])
                if idx + 1 < len(amount_rows)
                else None
            )
            row_starts.append(_find_uob_row_start(lines, prev_top, amount_top, next_top))

        for idx, row in enumerate(amount_rows):
            top = row_starts[idx]
            bottom = (
                row_starts[idx + 1]
                if idx + 1 < len(row_starts)
                else float(getattr(page, "height", 9999))
            )

            desc = _extract_description_from_row(lines, top, bottom)
            post_iso, stmt_time, ampm = _extract_statement_datetime_from_row(
                lines, top, bottom
            )
            dep = safe_float(row["monies"][0][1])
            wd = safe_float(row["monies"][1][1])
            bal = safe_float(row["monies"][2][1])
            trx_iso = normalize_date(row["trx_date"]) or row["trx_date"]

            transactions.append(
                _make_uob_tx(
                    trx_iso=trx_iso,
                    post_iso=post_iso,
                    pending_time=stmt_time,
                    pending_ampm=ampm,
                    description=desc,
                    debit=abs(wd) if wd else 0.0,
                    credit=abs(dep) if dep else 0.0,
                    balance=bal,
                    page_idx=page_idx,
                    source_file=source_file,
                    company_name=company_name,
                    account_no=account_no,
                )
            )

    return _fix_leaked_tail_fragments(_repair_uob_row_description_starts(transactions))


def parse_transactions_uob(pdf: pdfplumber.PDF, source_file: str = "") -> List[Dict[str, Any]]:
    transactions: List[Dict[str, Any]] = []

    company_name: Optional[str] = None
    account_no: Optional[str] = None
    statement_end_iso: Optional[str] = None
    ledger_balance_header: Optional[float] = None

    if getattr(pdf, "pages", None):
        first_page_text = pdf.pages[0].extract_text() or ""
        company_name, account_no, statement_end_iso, ledger_balance_header = _extract_header_meta(first_page_text)
        coordinate_transactions = _parse_transactions_uob_words(
            pdf, source_file, company_name, account_no
        )
        if coordinate_transactions:
            return coordinate_transactions

    prev_tx: Optional[Dict[str, Any]] = None

    pending_stmt_date: Optional[str] = None
    pending_time: Optional[str] = None
    pending_ampm: Optional[str] = None
    pending_desc_head: str = ""

    for page_idx, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        if not text:
            continue

        if page_idx == 1 and not (
            company_name
            or account_no
            or statement_end_iso
            or ledger_balance_header is not None
        ):
            company_name, account_no, statement_end_iso, ledger_balance_header = _extract_header_meta(text)

        lines = [normalize_text(ln) for ln in (text.splitlines() or []) if normalize_text(ln)]

        for line in lines:
            up = line.upper().strip()

            # stop at footer/summary sections
            if up.startswith("TOTAL DEPOSITS") or up.startswith("NOTE"):
                break

            if _is_header_or_export_line(up):
                continue

            # AM/PM line wrap (belongs to the pending statement time)
            if up in {"AM", "PM"} and pending_stmt_date and pending_time and not pending_ampm:
                pending_ampm = up
                continue

            # 1) Statement date + time line (sometimes also contains the start of description)
            m1 = _STMT_TIME_RE.match(line)
            if m1:
                pending_stmt_date = normalize_date(m1.group("stmt")) or m1.group("stmt")
                pending_time = m1.group("time")
                pending_ampm = None
                pending_desc_head = normalize_text(m1.group("rest") or "")
                continue

            # 2) Transaction date line (contains amounts/balance)
            m2 = _TRX_LINE_RE.match(line)
            if m2:
                trx_date = m2.group("trx")
                body = m2.group("body") or ""

                split = _split_amounts_from_tail(body)
                if not split:
                    # Some rows have transaction date + no amounts on that line; treat as description continuation
                    if prev_tx is not None and line and not _MONEY_RE.match(line):
                        prev_tx["description"] = normalize_text(prev_tx.get("description", "") + " " + line)
                    continue

                desc_body, dep, wd, bal = split
                desc = normalize_text(" ".join([pending_desc_head, desc_body]).strip()) if pending_desc_head or desc_body else ""

                # If still empty, keep a placeholder
                if not desc:
                    desc = "(NO DESCRIPTION)"

                credit = abs(dep) if dep else 0.0
                debit = abs(wd) if wd else 0.0

                trx_iso = normalize_date(trx_date) or trx_date
                post_iso = pending_stmt_date

                # IMPORTANT: "date" is the transaction date (value date),
                # so monthly summaries align to the period users expect.
                # Posting/statement date is preserved separately as "posting_date".
                tx = _make_uob_tx(
                    trx_iso=trx_iso,
                    post_iso=post_iso,
                    pending_time=pending_time,
                    pending_ampm=pending_ampm,
                    description=desc,
                    debit=debit,
                    credit=credit,
                    balance=bal,
                    page_idx=page_idx,
                    source_file=source_file,
                    company_name=company_name,
                    account_no=account_no,
                )
                transactions.append(tx)
                prev_tx = tx

                # reset pending for next row
                pending_stmt_date = None
                pending_time = None
                pending_ampm = None
                pending_desc_head = ""
                continue

            # 3) Continuation lines for description
            if prev_tx is not None:
                # If AM/PM wraps after the transaction line, attach it to the time field (not description)
                if up in {"AM", "PM"} and prev_tx.get("time") and prev_tx.get("time") not in {"AM", "PM"}:
                    prev_tx["time"] = normalize_text(f"{prev_tx.get('time')} {up}")
                    continue
                # Skip obvious noise like "AM Total 1 Cheque(s)"
                if re.match(r"^(AM|PM)\s+TOTAL\b", up, flags=re.IGNORECASE):
                    continue
                if up.startswith("ACCOUNT ACTIVITIES") or up.startswith("RECORD"):
                    continue
                prev_tx["description"] = normalize_text(prev_tx.get("description", "") + " " + line)
                prev_tx["party_name"] = extract_uob_party_name(prev_tx.get("description", ""))

    # Fallback: no transactions but we still want month to appear.
    if not transactions and ledger_balance_header is not None:
        transactions.append(
            _make_uob_tx(
                trx_iso=statement_end_iso or "2000-01-01",
                post_iso=None,
                pending_time=None,
                pending_ampm=None,
                description="NO TRANSACTIONS (LEDGER BALANCE)",
                debit=0.0,
                credit=0.0,
                balance=ledger_balance_header,
                page_idx=None,
                source_file=source_file,
                company_name=company_name,
                account_no=account_no,
                is_statement_balance=True,
            )
        )

    return _repair_uob_row_description_starts(transactions)