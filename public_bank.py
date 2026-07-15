# public_bank.py — Public Bank / Public Islamic Bank statement parser
#
# Works for both:
#   Public Bank Berhad  (RM Plus Current Account, etc.)
#   Public Islamic Bank (RM Current Account-i, etc.)
#
# Core algorithm
# ──────────────
# A transaction ANCHOR is any line whose last two tokens are two amounts
# (e.g. "DUITNOW TRSF DR 648617 10,154.16 635,820.72").
# Balance B/F, Balance C/F, and closing-balance lines are excluded.
#
# Every non-skip line that follows an anchor (until the next anchor) is
# appended as continuation description to the CURRENT transaction.
#
# Lines before the first anchor on a normal page are discarded. When a page
# ends with Balance C/F, the last transaction is carried forward and resumes
# collecting continuation lines after the next page's Balance B/F marker.
#
# Debit / credit is resolved from the running balance delta, which is
# always reliable in Public Bank statements.

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pdfplumber


# ── Compiled patterns ──────────────────────────────────────────────────────────

# Two amounts at end of line → transaction anchor
_AMOUNT_BAL = re.compile(
    r"(\d{1,3}(?:,\d{3})*\.\d{2})"   # amount
    r"\s+"
    r"(\d{1,3}(?:,\d{3})*\.\d{2})$"  # balance
)

# Balance carry-forward / page-break markers (NOT real transactions)
_BAL_SKIP = re.compile(
    r"\bBalance\s+(?:B/F|C/F|From Last Statement|In This Statement)\b"
    r"|\bClosing Balance\b",
    re.I,
)
_BAL_BF = re.compile(r"\bBalance\s+B/F\b", re.I)
_BAL_CF = re.compile(r"\bBalance\s+C/F\b", re.I)

# Date prefix "DD/MM " at the start of an anchor line
_DATE_PREFIX = re.compile(r"^(\d{2}/\d{2})\s+")

# Page header / footer lines that must be discarded
_PAGE_HEADER = re.compile(
    r"^(?:"
    # Table column headers
    r"TARIKH|DATE\b|URUS NIAGA|TRANSACTION\b|DEBIT\b|KREDIT\b|CREDIT\b|BAKI(?:\s+BALANCE)?\s*$|BALANCE\s*$|"
    # Pagination
    r"Muka Surat|Page \d|"
    # Footer boilerplate
    r"Penyata ini|This is a computer|"
    r"PUBLIC (?:BANK|ISLAMIC) BERHAD|"
    r"Baki Harian|Daily And Closing|"
    r"Thank You For Banking|Terima Kasih|"
    r"Your banking questions|Kemusykilan|"
    r"Anda boleh melihat|You may view|"
    r"Dimaklumkan bahawa|Please be informed|"
    r"sila layari laman|"
    r"PERHATIAN|ATTENTION|"
    # Branch / contact block
    r"TEL:|"
    r"Dilindungi oleh PIDM|Protected by PIDM|"
    # Account header block
    r"Nombor Akaun|Account Number|"
    r"Tarikh Penyata|Statement Date|"
    r"Jenis Akaun|Account Type|"
    r"RINGKASAN|SUMMARY|TEGASAN|HIGHLIGHTS|"
    r"Baki Penutup|Closing Balance|"
    r"Jumlah (?:Debit|Kredit)|Total (?:Debits|Credits)|"
    r"Bil\. (?:Debit|Kredit)|No\. of (?:Debits|Credits)|"
    # Branch address patterns
    r"KL CITY MAIN|GRD FLOOR MENARA|146 JLN AMPANG|50450 KUALA|"
    r"ALOR SETAR BRANCH|1070 & 1071 JLN|05200 ALOR SETAR|KEDAH DARUL"
    r")",
    re.I,
)

PBB_CHEQUE_RE = re.compile(
    r"\b(?:CHEQ|CHEQUE|CHQ|DEP-LOC\s+CHEQ|LOCAL\s+CHEQUE)\b",
    re.I,
)

PBB_PERSON_MARKER_TOKENS = {"BIN", "BINTI", "BT", "B"}
PBB_PERSON_REMARK_START_TOKENS = {
    "ADV",
    "BALANCE",
    "BAYAR",
    "BAYARAN",
    "BOOKING",
    "CLAIM",
    "CLAIMS",
    "DEPOSIT",
    "FUND",
    "GAJI",
    "INVOICE",
    "INV",
    "KERETA",
    "LOAN",
    "ORDER",
    "PAYMENT",
    "PYMT",
    "REF",
    "REFERENCE",
    "RENTAL",
    "REPAIR",
    "ROADTAX",
    "SALARY",
    "SENT",
    "SEWA",
    "TRANSFER",
}

PBB_PATTERNS = [
    re.compile(
        r"^DUITNOW\s+TRSF\s+(?:DR|CR)\s+\d+\s+"
        r"(?P<party>.+?)"
        r"(?=(?:\s+(?:KONTRA|ADV\s+STAF|STAF|SME\s+BANK\s+LOAN|PVCWS|BALANCE|"
        r"SENT\s+FROM|REFUND|FUND\s+TRANSFER|INTERBANK\s+TRANSFER|CWS-|PR/|"
        r"PBBEMYKL|MBBEMYKL|OCB|ORM|202\d{5,}))|$)",
        re.I,
    ),
    re.compile(
        r"^DEP-ECP\s+\d+\s+\S+\s+"
        r"(?:(?:CIM|CIMB|HSB|HSBC|RHB|MBB|PBB|HLB|AMB|BIMB|BMMB|BSN|UOB|OCBC|CIT|BOT)\s+)?"
        r"(?P<party>.+?)"
        r"(?=(?:\s+(?:CIM|HSB|RHB|MBB|PBB|/GHL/|TNG|GRB|66174|CIT|BOT|"
        r"MTHLY|XREF|APPY|\.|202\d{5,}))|$)",
        re.I,
    ),
    re.compile(
        r"^DR-ECP\s+\d+\s+\S+\s+(?P<party>.+?)"
        r"(?=(?:\s+(?:CP_|A\d+|T\d+|\d{6,}|GD\d+|NO\s+DEFINE|FPX))|$)",
        re.I,
    ),
    re.compile(
        r"^GIRO\s+PYMT-ATM/EFT\s+\d+\s+JOMPAY\s*-\s*[^-]+-\s*(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"^TSFR\s+FUND\s+(?:DR|CR)-ATM/EFT\s+\d+\s+\S+\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"^CR\s+CARD\s+PYMT-ATM/EFT\s+\d+\s+\S+&&\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"^LOAN\s+PYMT-ATM/EFT\s+\d+\s+\S+\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"^RMT\s+DR\s+\S+\s+OUTWARD\s+(?:TT\s+)?(?:EBK\s+)?(?P<party>.+?)"
        r"(?=\s+[A-Z]{3}\s*\d|\s+USD|\s+AED|\s+@|$)",
        re.I,
    ),
    re.compile(
        r"^IBG\s+RTN\s+ITEM\s+\d{8}\s+\S+\s+"
        r"(?P<bank>[A-Z]{3,4})\s+(?P<party>.+?)"
        r"(?=$|\s+(?:NO\s+ACCOUNT|ACCOUNT\s+NOT\s+FOUND|INVALID\s+ACCOUNT|RENTAL))",
        re.I,
    ),
    re.compile(
        r"^RTN\s+ITEM\s+\d+\s+IBG\s+RTN\s+ITEM\s+\d+\s+\S+\s+"
        r"(?P<bank>[A-Z]{3})\s+(?P<party>.+?)"
        r"(?=$|\s+(?:NO\s+ACCOUNT|ACCOUNT\s+NOT\s+FOUND|INVALID\s+ACCOUNT|RENTAL))",
        re.I,
    ),
    re.compile(
        r"^AUTOMATED\s+LOAN\s+PYMT\s+TO\s+(?P<party>\d+)",
        re.I,
    ),
    re.compile(
        r"^HANDLING\s+CHRG\b",
        re.I,
    ),
    re.compile(
        r"^DR-ECP\s+000001\b",
        re.I,
    ),
]


# ── Year extraction ────────────────────────────────────────────────────────────

def clean_pbb_party_name(value: str) -> str:
    party = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-").upper()

    if not party:
        return "UNKNOWN"

    party = _strip_pbb_person_party_remarks(party)

    party = re.sub(
        r"\bSIFAR\s+TOLERANSI\b.*$|\bTOLERANCE\s+APPROACH\b.*$",
        "",
        party,
        flags=re.I,
    )

    party = re.sub(
        r"\b(?:CIM|HSB|RHB|MBB|PBB)\s+(?:TNG|GRB|/GHL/).*?$",
        "",
        party,
        flags=re.I,
    )

    party = re.sub(
        r"\b(?:PVCWS[-/\w]*|PR/\S+|CWS-ALPHA-\S+|"
        r"CWS-\S+|IFTAR|PURCHASE|IMPORT\s+DUTY|SALARY|INTERBANK\s+TRANSFER|"
        r"FUND\s+TRANSFER|MONTHLY|INSTALLMENT|RENTAL|DUTY\s+CHARGES|"
        r"KONTRA|ADV\s+STAF|STAF|SME\s+BANK\s+LOAN|"
        r"PAYMENT\s+BP|PAYMENT|PYMT|LOAN|CLAIMS?|REPAIR|ROADTAX|"
        r"INSURAN|INSURANCE|PRODUCTS|"
        r"BORROW|TRANSFER|RETURNS?|FROM\s+\w+.*|THANKS\s+\w+|CC|INS|"
        r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{1,2}|"
        r"NO\s+DEFINE\s+FPX.*|FPX\s*-\s*\S+|"
        r"202\d{5,}[A-Z0-9]+|OCB\d+|ORM\d+|OCM\d+|OOT\d+|"
        r"TNG\d+|GRB\d+|66174[A-Z0-9]+|[A-Z]{1,3}\d{3,5})\b.*$",
        "",
        party,
        flags=re.I,
    )

    party = re.sub(r"\b\d{8,}\b.*$", "", party)
    party = re.sub(r"\b\d{3,4}\s+(?:LOAN|PAYMENT|PYMT)\b.*$", "", party, flags=re.I)
    party = re.sub(r"\b\d{3,4}\s*$", "", party)
    party = re.sub(r"\bS\s*/\s*B\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BHD\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BH\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*B\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\b(SDN\s+BHD)\b.*$", r"\1", party, flags=re.I)
    party = re.sub(r"\bBERHA\b", "BERHAD", party, flags=re.I)
    party = re.sub(r"\bBERH\b", "BERHAD", party, flags=re.I)

    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-")
    return party or "UNKNOWN"


def _strip_pbb_person_party_remarks(value: str) -> str:
    party = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-").upper()
    tokens = party.split()
    marker_indexes = [
        idx for idx, token in enumerate(tokens)
        if token.strip(" .,-()").upper() in PBB_PERSON_MARKER_TOKENS
    ]
    if not marker_indexes:
        return party

    marker_index = marker_indexes[0]
    if marker_index + 1 >= len(tokens):
        return party

    for idx in range(marker_index + 2, len(tokens)):
        token = tokens[idx].strip(" .,-()").upper()
        if token in PBB_PERSON_REMARK_START_TOKENS:
            return " ".join(tokens[:idx]).strip(" ,.-")

    parenthetical_match = re.search(r"\s+\([^)]*\)", party)
    if parenthetical_match:
        before_parenthetical = party[: parenthetical_match.start()].strip(" ,.-")
        if len(before_parenthetical.split()) > marker_index + 1:
            return before_parenthetical

    return party


def extract_pbb_party_name(description: str) -> str:
    desc = re.sub(r"\s+", " ", str(description or "")).strip()

    if not desc:
        return "UNKNOWN"

    if PBB_CHEQUE_RE.search(desc):
        return "CHEQUE"

    if re.match(r"^HANDLING\s+CHRG\b", desc, re.I):
        return "BANK CHARGES"

    if re.match(r"^DR-ECP\s+000001\b", desc, re.I):
        return "INTERNAL BATCH PAYMENT"

    if re.match(r"^DEP-CASH\s+CDT\b", desc, re.I):
        return "CASH DEPOSIT"

    if re.match(r"^MISC\s+DR\b", desc, re.I):
        return "MISC DEBIT"

    for pattern in PBB_PATTERNS:
        match = pattern.search(desc)
        if not match:
            continue

        if pattern.pattern.startswith("^HANDLING"):
            return "BANK CHARGES"

        if pattern.pattern.startswith("^DR-ECP\\s+000001"):
            return "INTERNAL BATCH PAYMENT"

        party = match.groupdict().get("party", "")
        return clean_pbb_party_name(party)

    return "UNKNOWN"


def _normalize_pbb_description_lines(description_lines: Any) -> list[str]:
    if not isinstance(description_lines, (list, tuple)):
        return []

    lines: list[str] = []
    for line in description_lines:
        cleaned = re.sub(r"\s+", " ", str(line or "")).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _pbb_party_candidate_texts(
    description: str,
    same_row_description: str = "",
    description_lines: Any = None,
) -> list[str]:
    candidates = [same_row_description]
    lines = _normalize_pbb_description_lines(description_lines)

    if lines:
        anchor_line = same_row_description or lines[0]
        for continuation_line in lines[1:4]:
            candidates.append(f"{anchor_line} {continuation_line}")

    candidates.append(description)
    return candidates


def _is_incomplete_pbb_person_party(party: str) -> bool:
    tokens = re.sub(r"\s+", " ", str(party or "")).strip().upper().split()
    if not tokens:
        return True

    for marker in ("BIN", "BINTI", "BT", "B"):
        if marker in tokens:
            marker_index = tokens.index(marker)
            return len(tokens) <= marker_index + 1

    return False


def resolve_pbb_party_name(
    description: str,
    same_row_description: str = "",
    description_lines: Any = None,
) -> str:
    candidates = _pbb_party_candidate_texts(description, same_row_description, description_lines)
    seen: set[str] = set()

    for candidate in candidates:
        text = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if not text or text in seen:
            continue
        seen.add(text)

        party = extract_pbb_party_name(text)
        if party != "UNKNOWN":
            if _is_incomplete_pbb_person_party(party):
                continue
            return party

    return "UNKNOWN"


def extract_year_from_text(text: str) -> str | None:
    """
    Pull the statement year from the header of a Public Bank / Public Islamic
    Bank statement.  Handles the common formats:
        Statement Date  31 Mar 2025
        Statement Date  31/03/2025
        Statement Date  31/03/25
    """
    # "Statement Date  31 Mar 2025"
    m = re.search(
        r"(?:Statement Date|Tarikh Penyata)\s*[:/\s]+\d{1,2}\s+[A-Za-z]{3,}\s+(\d{4})",
        text, re.I,
    )
    if m:
        return m.group(1)

    # "Statement Date  31/03/2025" or "31/03/25"
    m = re.search(
        r"(?:Statement Date|Tarikh Penyata)\s*[:/\s]+\d{1,2}/\d{1,2}/(\d{2,4})",
        text, re.I,
    )
    if m:
        raw = m.group(1)
        return raw if len(raw) == 4 else str(2000 + int(raw))

    return None

# ── Line classifiers ───────────────────────────────────────────────────────────

def _is_page_header(line: str) -> bool:
    return bool(_PAGE_HEADER.match(line))


def _is_balance_marker(line: str) -> bool:
    return bool(_BAL_SKIP.search(line))


def _is_balance_brought_forward(line: str) -> bool:
    return bool(_BAL_BF.search(line))


def _is_balance_carried_forward(line: str) -> bool:
    return bool(_BAL_CF.search(line))


def _anchor_match(line: str):
    """Return the AMOUNT_BAL match if this line is a transaction anchor."""
    if _is_balance_marker(line):
        return None
    return _AMOUNT_BAL.search(line)


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_transactions_pbb(pdf: Any, source_filename: str = "") -> list[dict]:
    """
    Parse a Public Bank or Public Islamic Bank PDF statement.

    Args:
        pdf             – pdfplumber PDF object (already opened).
        source_filename – Label stored on every output row.

    Returns:
        List of dicts with keys:
            date, description, debit, credit, balance, page, bank, source_file
    """
    raw: list[dict] = []   # rows without debit/credit resolved yet
    year: str | None = None
    carried_txn: dict | None = None

    # ── Detect statement year ──────────────────────────────────────────────────
    for page in pdf.pages[:3]:
        text = page.extract_text() or ""
        year = extract_year_from_text(text)
        if year:
            break
    if not year:
        year = str(datetime.now().year)

    # ── Process pages ──────────────────────────────────────────────────────────
    for page_no, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        current_date: str | None = None       # last DD/MM seen
        current_txn:  dict | None = carried_txn  # transaction being built
        carried_txn = None
        waiting_for_balance_bf = current_txn is not None
        found_first_anchor = False            # discard pre-anchor header overflow
        page_ends_with_balance_cf = False

        for line in lines:

            # ── Track date from any dated line (B/F, anchor, etc.) ────────────
            dm = _DATE_PREFIX.match(line)
            if dm:
                current_date = dm.group(1)

            # ── Skip balance markers and page header / footer lines ────────────
            if _is_balance_marker(line):
                if _is_balance_brought_forward(line) and waiting_for_balance_bf:
                    found_first_anchor = True
                    waiting_for_balance_bf = False
                if _is_balance_carried_forward(line):
                    page_ends_with_balance_cf = True
                continue

            if _is_page_header(line):
                continue

            # ── Transaction anchor? ────────────────────────────────────────────
            amt_m = _anchor_match(line)
            if amt_m:
                found_first_anchor = True

                # Emit the previously accumulated transaction
                if current_txn is not None:
                    raw.append(current_txn)

                # Description = line content before the "amount balance" suffix
                anchor_desc = line[: amt_m.start()].strip()
                # Remove the DD/MM date prefix if present
                anchor_desc = _DATE_PREFIX.sub("", anchor_desc).strip()

                amount  = float(amt_m.group(1).replace(",", ""))
                balance = float(amt_m.group(2).replace(",", ""))

                # Build ISO date
                if current_date:
                    dd, mm = current_date.split("/")
                    iso_date = f"{year}-{mm}-{dd}"
                else:
                    iso_date = f"{year}-01-01"

                current_txn = {
                    "date":        iso_date,
                    "description": anchor_desc,
                    "same_row_description": anchor_desc,
                    "description_lines": [anchor_desc] if anchor_desc else [],
                    "party_name":  extract_pbb_party_name(anchor_desc),
                    "amount":      amount,
                    "balance":     balance,
                    "page":        page_no,
                }
                continue

            # ── Continuation / description line ───────────────────────────────
            if not found_first_anchor:
                # Still in the header block or carry-over from previous page
                continue

            if current_txn is not None:
                current_txn["description"] += " " + line
                current_txn.setdefault("description_lines", []).append(line)

        # End of page — emit last accumulated transaction
        if current_txn is not None:
            if page_ends_with_balance_cf:
                carried_txn = current_txn
            else:
                raw.append(current_txn)

    # ── Resolve debit / credit from running balance delta ─────────────────────
    # Balance delta is the most reliable signal in Public Bank statements.
    # The "amount" field from the anchor line is kept for reference but the
    # actual direction is inferred from whether the balance rose or fell.
    if carried_txn is not None:
        raw.append(carried_txn)

    results: list[dict] = []
    prev_balance: float | None = None

    for row in raw:
        balance = row["balance"]
        debit = credit = 0.0

        if prev_balance is not None:
            delta = round(balance - prev_balance, 2)
            if delta > 0:
                credit = delta
            elif delta < 0:
                debit = abs(delta)
        else:
            # First transaction — use description keywords as fallback
            desc_up = row["description"].upper()
            if re.search(r"\bCR\b|^DEP|CREDIT", desc_up):
                credit = row["amount"]
            else:
                debit = row["amount"]

        prev_balance = balance

        party_name = row.get("party_name", "")
        if str(party_name or "").strip().upper() in {"", "UNKNOWN"}:
            party_name = resolve_pbb_party_name(
                row["description"],
                row.get("same_row_description", ""),
                row.get("description_lines"),
            )

        results.append({
            "date":        row["date"],
            "description": row["description"].strip(),
            "same_row_description": row.get("same_row_description", row["description"]).strip(),
            "description_lines": row.get("description_lines", []),
            "party_name":  party_name,
            "debit":       round(debit,  2),
            "credit":      round(credit, 2),
            "balance":     balance,
            "page":        row["page"],
            "bank":        "Public Bank",
            "source_file": source_filename,
        })

    return results


# ── Convenience wrapper (open PDF internally) ──────────────────────────────────

def parse_public_bank(pdf_path: str, source_filename: str = "") -> list[dict]:
    """Open *pdf_path* and return parsed transactions."""
    with pdfplumber.open(pdf_path) as pdf:
        return parse_transactions_pbb(pdf, source_filename=source_filename or pdf_path)


# ── Smoke-test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "statement.pdf"
    txns = parse_public_bank(path)

    print(f"\n{'=' * 72}")
    print(f"  {len(txns)} transactions  ·  {path}")
    print(f"{'=' * 72}\n")

    for t in txns:
        print(
            f"  {t['date']}  Dr:{t['debit']:>12.2f}"
            f"  Cr:{t['credit']:>12.2f}  Bal:{t['balance']:>14.2f}"
        )
        print(f"    {t['description'][:100]}")

    print(f"\n{'=' * 72}")