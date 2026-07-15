# bank_islam.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None

from PIL import ImageEnhance, ImageOps


_TESSERACT_READY: Optional[bool] = None


BANK_ISLAM_PREFIX_RE = re.compile(r"^\s*(?:\d+\s+)?\d{4}\s+", re.I)
BANK_ISLAM_TXN_PREFIX = r"(?:\d+\s+)?\d{4}"
BANK_ISLAM_COMPACT_TXN_PREFIX = r"(?:\d+\s+)?\d{4}"

BANK_ISLAM_COMPACT_PARTY_PATTERNS = [
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}HSECHQDEP-CR/DR\s+\d+\s+(?P<party>.+?)\s+\d+$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}IBDuitNow\(ACCNO\)\s+.+\s+(?P<party>[^\s]+)\s+\d{{7}}(?:\s+140N)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}IBDuitNow\(ACCNO\)\s+.+\s+(?P<party>[^\s]+)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}IBFPX\(DEBIT\)-CA\s+.*?\s+(?P<party>[A-Z][A-Z0-9&/.]+)\s+EFPX\d+$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}IBCATRFSA3RDPTY\s+.*?\s+(?P<party>[A-Z][A-Z0-9&/.]+)\s+EFO\d+(?:\s+140N)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}INWDuitNowTransfer\s+TRANSFER(?:\s+BI)?\s+(?P<party>.+?)\s+XRPP\d+$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}ATMCASHWDRWL-CA\s+(?P<party>.+?)(?:\s+140N)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}CANONCHQWDRL\s+\d+(?P<party>Withdrawalwithoutcheque)\s+\d+$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}(?P<party>PROFITPAID)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}(?P<party>SERVICECHARGE)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_COMPACT_TXN_PREFIX}CADR&CRADVICE\(BOC\)\s+.*?\s+(?P<party>[A-Z][A-Z0-9&/.]+)\s+BOC_\d+$",
        re.I,
    ),
]

BANK_ISLAM_PARTY_PATTERNS = [
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+INW\s+DuitNow\s+Transfer\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CA\s+(?:DR&CR|DR|CR)\s+ADVICE\s+\d{{4,6}}\s+"
        r"(?:TRANSFER\s+FUND|TRF\s+FUND|REFUND)\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CA\s+(?:DR&CR|DR|CR)\s+ADVICE\s+(?P<party>.+?)(?:\s+\d{4,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CDB\s+(?:JOMPAY|JPMPAY)\s+(?:ON-US|OFF-US)(?:\s+U\d+\s+\d+)?\s+"
        r"(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+IBG\s+TRANSFER\s+TO\s+CA\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+INW\s+RENTAS\s+CR\s+CA\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+MYC\s+DD\s+CASA\s*-\s*DR\s+(?P<party>.+?)(?:\s+\d{6,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CDB\s+FPX\(DR\)-CA\s+TO\s+GL\s+\d+\s+\d+\s+"
        r"(?P<party>.+?)(?:\s+EFPX[A-Z0-9]+)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CDB\s+CA\s+TRF\s+IBG\s+INTERBANK\s+TRANSFER\s+"
        r"[A-Z0-9]+\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CDB\s+CA\s+TRF\s+IBG\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+CDB\s+CS\s+TO\s+IBFTS3\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        rf"^\s*{BANK_ISLAM_TXN_PREFIX}\s+eSPICK\s+INW\s+(?P<party>.+)$",
        re.I,
    ),
]


def _strip_bank_islam_reference_tail(party: str) -> str:
    previous = None
    while previous != party:
        previous = party
        party = re.sub(r"\s+\d{5,}(?:\s+\d+)?$", "", party)
        party = re.sub(r"\s+[A-Z]{2,}[A-Z0-9]{4,}\s+\d{1,3}$", "", party)
        party = re.sub(r"\s+[A-Z0-9]*\d[A-Z0-9]*(?:\s+[A-Z])?$", "", party)
    return party


def _clean_bank_islam_party_name(party: str) -> str:
    party = re.sub(r"\s+", " ", str(party or "")).strip(" ,.-")
    if not party:
        return "UNKNOWN"
    if not re.search(r"[A-Za-z]", party):
        return "UNKNOWN"

    party = party.split(",", 1)[0].strip(" ,.-")
    if not re.search(r"[A-Za-z]", party):
        return "UNKNOWN"

    party = _strip_bank_islam_reference_tail(party)
    if not re.search(r"[A-Za-z]", party):
        return "UNKNOWN"

    party = re.sub(r"\bSDN\.?\s*BHD\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\s+BH\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bBERH\b", "BERHAD", party, flags=re.I)
    party = re.sub(r"\bCAPITA\b", "CAPITAL", party, flags=re.I)
    party = re.sub(r"\bACCOU\b", "ACCOUNT", party, flags=re.I)
    party = re.sub(r"\bGR\b", "GROUP", party, flags=re.I)

    company_suffix_match = re.search(r"\bSDN\s+BHD\b", party, flags=re.I)
    if company_suffix_match:
        party = party[: company_suffix_match.end()]
    else:
        truncated_sdn_match = re.search(r"\bSDN\.?\b", party, flags=re.I)
        truncated_sd_match = re.search(r"\bSD\b", party, flags=re.I)
        if truncated_sdn_match:
            party = f"{party[: truncated_sdn_match.start()].strip()} SDN BHD"
        elif truncated_sd_match:
            party = f"{party[: truncated_sd_match.start()].strip()} SDN BHD"

    tokens = party.split()
    detail_index = None
    for index, token in enumerate(tokens):
        if index < 2:
            continue
        if re.search(r"[a-z]", token):
            detail_index = index
            break
    if detail_index is not None:
        party = " ".join(tokens[:detail_index])

    party = re.sub(r"\s+\d{5,}(?:\s+\d+)?$", "", party)
    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-")
    return party.upper() if party else "UNKNOWN"


def _strip_bank_islam_transaction_prefix(description: str) -> str:
    return re.sub(r"^\s*(?:\d+\s+)?\d{4}\s+", "", str(description or ""), count=1, flags=re.I).strip()


def _extract_bank_islam_keyword_party(description: str) -> str:
    desc = _strip_bank_islam_transaction_prefix(description)
    if not desc:
        return "UNKNOWN"

    artifact_markers = {
        "ACCOUNT STATEMENT",
        "STATEMENT DATE",
        "TARIKH PENYATA",
        "ACCOUNT NUMBER",
        "NOMBOR AKAUN",
        "CAWANGAN",
        "HALAMAN",
        "DILINDUNGI OLEH PIDM",
    }
    desc_upper = desc.upper()
    if "BANK ISLAM" in desc_upper and any(marker in desc_upper for marker in artifact_markers):
        return "UNKNOWN"

    keyword_patterns = [
        re.compile(
            r"^CA\s+(?:DR&CR|DR|CR)\s+ADVICE\s+(?:\d{4,6}\s+)?"
            r"(?:TRANSFER\s+FUND|TRF\s+FUND|REFUND)\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$",
            re.I,
        ),
        re.compile(
            r"^CDB\s+FPX\(DR\)-CA\s+TO\s+GL\s+\d+\s+\d+\s+(?P<party>.+?)(?:\s+EFPX[A-Z0-9]+)?$",
            re.I,
        ),
        re.compile(
            r"^CDB\s+CA\s+TRF\s+IBG\s+INTERBANK\s+TRANSFER\s+[A-Z0-9]+\s+"
            r"(?P<party>.+?)(?:\s+\d{5,}.*)?$",
            re.I,
        ),
        re.compile(r"^CDB\s+CA\s+TRF\s+IBG\s+(?P<party>.+)$", re.I),
        re.compile(r"^CDB\s+CS\s+TO\s+IBFTS3\s+(?P<party>.+)$", re.I),
        re.compile(r"^IBG\s+TRANSFER\s+TO\s+CA\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$", re.I),
        re.compile(r"^INW\s+RENTAS\s+CR\s+CA\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$", re.I),
        re.compile(r"^MYC\s+DD\s+CASA\s*-\s*DR\s+(?P<party>.+?)(?:\s+\d{6,}.*)?$", re.I),
        re.compile(r"^INW\s+DuitNow\s+Transfer\s+(?P<party>.+)$", re.I),
        re.compile(r"^eSPICK\s+INW\s+(?P<party>.+)$", re.I),
        re.compile(r"^(?:TRANSFER\s+FUND|TRF\s+FUND|REFUND)\s+(?P<party>.+?)(?:\s+\d{5,}.*)?$", re.I),
    ]

    for pattern in keyword_patterns:
        match = pattern.search(desc)
        if not match:
            continue
        party = re.sub(r"\bIBG\s+TRANSACTIO\s*N\b.*$", "", match.group("party"), flags=re.I)
        return _clean_bank_islam_party_name(party)

    if re.search(r"\b(?:SDN\.?\s*(?:BHD\.?|BH|BDH)?|BERHAD|ENTERPRISE|TRADING|SERVICES|HOLDINGS|PLT)\b", desc, re.I):
        return _clean_bank_islam_party_name(desc)

    return "UNKNOWN"


def extract_bank_islam_party_name(description: str) -> str:
    desc = re.sub(r"\s+", " ", str(description or "")).strip()

    if not desc:
        return "UNKNOWN"

    for pattern in BANK_ISLAM_COMPACT_PARTY_PATTERNS:
        match = pattern.search(desc)
        if match:
            return _clean_bank_islam_party_name(match.group("party"))

    if re.search(
        r"\b(?:SERVICE\s+CHARGE|CHQ\s+PRCSG\s+FEE|PROFIT\s+PAID|REVERSE\s+POSTED|GUARANTEE\s+FEE|CAJ\s+PENGESAHAN\s+AKAUN)\b",
        desc,
        re.I,
    ):
        return "BANK / SYSTEM"

    for pattern in BANK_ISLAM_PARTY_PATTERNS:
        match = pattern.search(desc)
        if match:
            party = match.group("party")
            party = re.sub(r"\bIBG\s+TRANSACTIO\s*N\b.*$", "", party, flags=re.I)
            return _clean_bank_islam_party_name(party)

    return _extract_bank_islam_keyword_party(desc)


def _attach_bank_islam_party_names(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for transaction in transactions or []:
        current_party = str(transaction.get("party_name", "") or "").strip().upper()
        if current_party and current_party != "UNKNOWN":
            continue
        transaction["party_name"] = extract_bank_islam_party_name(transaction.get("description", ""))
    return transactions



def _has_tesseract_binary() -> bool:
    global _TESSERACT_READY
    if pytesseract is None:
        _TESSERACT_READY = False
        return False
    if _TESSERACT_READY is not None:
        return _TESSERACT_READY
    try:
        pytesseract.get_tesseract_version()
        _TESSERACT_READY = True
    except Exception:
        _TESSERACT_READY = False
    return _TESSERACT_READY


# =========================================================
# BANK ISLAM – FORMAT 1 (TABLE-BASED)
# =========================================================

# Date pattern that matches both D/MM/YY and DD/MM/YYYY (and variants)
_FORMAT1_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def _parse_format1_date(raw: str) -> Optional[str]:
    """Parse D/MM/YY, DD/MM/YY, D/MM/YYYY, DD/MM/YYYY → ISO date string."""
    m = _FORMAT1_DATE_RE.search(str(raw))
    if not m:
        return None
    date_str = m.group()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_amount(text) -> Optional[float]:
    if not text:
        return None
    s = re.sub(r"\s+", "", str(text))
    m = re.search(r"(-?[\d,]+\.\d{2})", s)
    return float(m.group(1).replace(",", "")) if m else None


def _is_5col_table(table: list) -> bool:
    """
    Return True when the table uses the 5-column Bank Islam layout:
        [date, description, debit, credit, balance]
    Both JAN25 (with spaces) and NOV23 (space-less text) use this layout.
    """
    if not table or not table[0]:
        return False
    if len(table[0]) != 5:
        return False
    # Header cell 0 should contain "TARIKH" or "DATE"
    header = re.sub(r"\s+", "", str(table[0][0] or "")).upper()
    return "TARIKH" in header or "DATE" in header


def parse_bank_islam_format1(pdf, source_file):
    """
    Table-based parser.  Handles two layouts:

    12-column layout (original format 1):
        no | txn_date | customer_eft | txn_code | description | ref_no |
        branch | debit | credit | balance | sender_recipient | payment_details

    5-column layout (JAN25 / NOV23 eStatement formats):
        date | description | debit | credit | balance
    """
    transactions: List[Dict[str, Any]] = []

    for page_num, page in enumerate(pdf.pages, start=1):
        table = page.extract_table()
        if not table:
            continue

        # ── 5-column layout ───────────────────────────────────────────────────
        if _is_5col_table(table):
            for row in table:
                if not row or len(row) < 5:
                    continue
                date_raw, desc_raw, debit_raw, credit_raw, balance_raw = row[:5]

                # Skip header row, BAL B/F row, summary/message rows
                if not date_raw or not _FORMAT1_DATE_RE.search(str(date_raw)):
                    continue

                date = _parse_format1_date(str(date_raw))
                if not date:
                    continue

                debit   = _extract_amount(debit_raw)   or 0.0
                credit  = _extract_amount(credit_raw)  or 0.0
                balance = _extract_amount(balance_raw) or 0.0

                # Collapse embedded newlines in the description cell
                desc_clean = re.sub(r"\s+", " ", str(desc_raw or "")).strip()

                transactions.append({
                    "date":        date,
                    "description": desc_clean,
                    "debit":       round(debit,   2),
                    "credit":      round(credit,  2),
                    "balance":     round(balance, 2),
                    "page":        page_num,
                    "bank":        "Bank Islam",
                    "source_file": source_file,
                    "format":      "format1_5col",
                })
            continue  # done with this page

        # ── 12-column layout (original) ───────────────────────────────────────
        for row in table:
            row = list(row) if row else []
            while len(row) < 12:
                row.append(None)

            (
                no,
                txn_date,
                customer_eft,
                txn_code,
                description,
                ref_no,
                branch,
                debit_raw,
                credit_raw,
                balance_raw,
                sender_recipient,
                payment_details,
            ) = row[:12]

            if not txn_date or not re.search(r"\d{2}/\d{2}/\d{4}", str(txn_date)):
                continue

            try:
                date = datetime.strptime(
                    re.search(r"\d{2}/\d{2}/\d{4}", str(txn_date)).group(),
                    "%d/%m/%Y",
                ).date().isoformat()
            except Exception:
                continue

            debit   = _extract_amount(debit_raw)   or 0.0
            credit  = _extract_amount(credit_raw)  or 0.0
            balance = _extract_amount(balance_raw) or 0.0

            if debit == 0.0 and credit == 0.0:
                recovered = _extract_amount(description)
                if recovered:
                    desc = str(description).upper()
                    if "CR" in desc or "CREDIT" in desc or "IN" in desc:
                        credit = recovered
                    else:
                        debit = recovered

            description_clean = " ".join(
                str(x).replace("\n", " ").strip()
                for x in [no, txn_code, description, sender_recipient, payment_details]
                if x and str(x).lower() != "nan"
            )

            transactions.append({
                "date":        date,
                "description": description_clean,
                "debit":       round(debit,   2),
                "credit":      round(credit,  2),
                "balance":     round(balance, 2),
                "page":        page_num,
                "bank":        "Bank Islam",
                "source_file": source_file,
                "format":      "format1",
            })

    return transactions


# =========================================================
# BANK ISLAM – FORMAT 2 (TEXT / STATEMENT-BASED)
# =========================================================
MONEY_RE = re.compile(r"\(?-?[\d,]+\.\d{2}\)?")
DATE_AT_START_RE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\b")
BAL_BF_RE = re.compile(r"BAL\s+B/F", re.IGNORECASE)


def _to_float(val):
    if not val:
        return None
    neg = val.startswith("(") and val.endswith(")")
    val = val.strip("()").replace(",", "")
    try:
        num = float(val)
        return -num if neg else num
    except ValueError:
        return None


def _parse_date(d: str) -> Optional[str]:
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_bank_islam_format2(pdf, source_file):
    transactions: List[Dict[str, Any]] = []
    prev_balance: Optional[float] = None

    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]

        for line in lines:
            upper = line.upper()

            if BAL_BF_RE.search(upper):
                money = MONEY_RE.findall(line)
                if money:
                    prev_balance = _to_float(money[-1])
                continue

            m_date = DATE_AT_START_RE.match(line)
            if not m_date or prev_balance is None:
                continue

            date = _parse_date(m_date.group(1))
            if not date:
                continue

            money_raw = MONEY_RE.findall(line)
            money_vals = [_to_float(x) for x in money_raw if _to_float(x) is not None]
            if not money_vals:
                continue

            balance = money_vals[-1]

            delta = round(balance - prev_balance, 2)
            credit = delta if delta > 0 else 0.0
            debit = abs(delta) if delta < 0 else 0.0
            prev_balance = balance

            desc = line[len(m_date.group(1)):].strip()
            for tok in money_raw:
                desc = desc.replace(tok, "").strip()

            transactions.append({
                "date":        date,
                "description": desc,
                "debit":       round(debit,   2),
                "credit":      round(credit,  2),
                "balance":     round(balance, 2),
                "page":        page_num,
                "bank":        "Bank Islam",
                "source_file": source_file,
                "format":      "format2_balance_delta",
            })

    return transactions


# =========================================================
# FORMAT 3 – eSTATEMENT
# =========================================================
def parse_bank_islam_format3(pdf, source_file):
    transactions: List[Dict[str, Any]] = []
    prev_balance: Optional[float] = None

    DATE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4})")
    MONEY_RE3 = re.compile(r"(\d{1,3}(?:,\d{3})*\.\d{2})")
    BAL_BF_RE3 = re.compile(r"BAL\s+B/F", re.IGNORECASE)

    def to_float(x):
        return float(x.replace(",", ""))

    def parse_date(d):
        for fmt in ("%d/%m/%y", "%d/%m/%Y"):
            try:
                return datetime.strptime(d, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text(x_tolerance=1) or ""
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]

        for line in lines:
            if BAL_BF_RE3.search(line):
                nums = MONEY_RE3.findall(line)
                if nums:
                    prev_balance = to_float(nums[-1])
                continue

            date_match = DATE_RE.match(line)
            if date_match and prev_balance is not None:
                raw_date = date_match.group(1)
                nums = MONEY_RE3.findall(line)
                if len(nums) >= 2:
                    balance = to_float(nums[-1])
                    desc = line.replace(raw_date, "").strip()
                    for n in nums:
                        desc = desc.replace(n, "").strip()

                    delta = round(balance - prev_balance, 2)

                    transactions.append({
                        "date":        parse_date(raw_date),
                        "description": desc,
                        "debit":       abs(delta) if delta < 0 else 0.0,
                        "credit":      delta if delta > 0 else 0.0,
                        "balance":     balance,
                        "page":        page_num,
                        "bank":        "Bank Islam",
                        "source_file": source_file,
                        "format":      "format3_estatement",
                    })
                    prev_balance = balance

    return transactions


# =========================================================
# FORMAT 4 – eSTATEMENT
# =========================================================
def parse_bank_islam_format4(pdf, source_file):
    transactions: List[Dict[str, Any]] = []
    prev_balance: Optional[float] = None

    DATE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4})")
    MONEY_RE4 = re.compile(r"(\d{1,3}(?:,\d{3})*\.\d{2})")
    BAL_BF_RE4 = re.compile(r"BAL\s+B/IF", re.IGNORECASE)

    def to_float(x):
        return float(x.replace(",", ""))

    def parse_date(d):
        for fmt in ("%d/%m/%y", "%d/%m/%Y"):
            try:
                return datetime.strptime(d, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text(x_tolerance=1) or ""
        lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]

        for line in lines:
            if BAL_BF_RE4.search(line):
                nums = MONEY_RE4.findall(line)
                if nums:
                    prev_balance = to_float(nums[-1])
                continue

            date_match = DATE_RE.match(line)
            if date_match and prev_balance is not None:
                raw_date = date_match.group(1)
                nums = MONEY_RE4.findall(line)
                if nums:
                    current_balance = to_float(nums[-1])
                    delta = round(current_balance - prev_balance, 2)
                    desc = line.replace(raw_date, "").strip()
                    for n in nums:
                        desc = desc.replace(n, "").strip()

                    transactions.append({
                        "date":        parse_date(raw_date),
                        "description": desc,
                        "debit":       abs(delta) if delta < 0 else 0.0,
                        "credit":      delta if delta > 0 else 0.0,
                        "balance":     current_balance,
                        "page":        page_num,
                        "bank":        "Bank Islam",
                        "source_file": source_file,
                        "format":      "format4_normalized",
                    })
                    prev_balance = current_balance

    return transactions


# =========================================================
# OCR PATH – BALANCE DELTA (with February fix)
# =========================================================
_OCR_DATE_RE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\b")
_OCR_MONEY_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}")
_OCR_STATEMENT_DATE_RE = re.compile(
    r"(?:STATEMENT\s*DATE|TARIKH\s*PENYATA)\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})",
    re.IGNORECASE,
)


def _ocr_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _ocr_image(page, resolution: int = 400):
    img = page.to_image(resolution=resolution).original
    img = ImageOps.grayscale(img)
    img = ImageEnhance.Contrast(img).enhance(1.8)
    return img


def _ocr_text_page_multi(page) -> str:
    if not _has_tesseract_binary():
        return ""
    try:
        img = _ocr_image(page, resolution=400)
        t4 = pytesseract.image_to_string(img, config="--psm 4") or ""
        t6 = pytesseract.image_to_string(img, config="--psm 6") or ""
        return t4 + "\n" + t6
    except Exception:
        return ""


def _extract_statement_month_year_via_ocr(pdf) -> Optional[Tuple[int, int]]:
    if pytesseract is None or not getattr(pdf, "pages", None):
        return None
    text = _ocr_text_page_multi(pdf.pages[0]) or ""
    m = _OCR_STATEMENT_DATE_RE.search(text)
    if not m:
        return None
    mm = int(m.group(2))
    yy_raw = m.group(3)
    yy = (2000 + int(yy_raw)) if len(yy_raw) == 2 else int(yy_raw)
    if 1 <= mm <= 12 and 2000 <= yy <= 2100:
        return (yy, mm)
    return None


def _extract_summary_totals_via_ocr(pdf) -> Tuple[Optional[float], Optional[float]]:
    if pytesseract is None or not getattr(pdf, "pages", None):
        return (None, None)

    text = _ocr_text_page_multi(pdf.pages[0])
    text_norm = re.sub(r"\s+", " ", (text or "")).upper()

    def find_total(label: str) -> Optional[float]:
        m = re.search(
            rf"{label}\s+(?:\d+\s+)?((?:\d{{1,3}}(?:,\d{{3}})*)\.\d{{2}})",
            text_norm,
        )
        return _ocr_float(m.group(1)) if m else None

    return find_total("TOTAL DEBIT"), find_total("TOTAL CREDIT")


def _extract_opening_balance_via_ocr(pdf) -> Optional[float]:
    if pytesseract is None or not getattr(pdf, "pages", None):
        return None

    text = _ocr_text_page_multi(pdf.pages[0])
    text_norm = re.sub(r"\s+", " ", (text or "")).upper()

    m = re.search(
        r"BAL\s*B/F\s+((?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2})",
        text_norm,
    )
    return _ocr_float(m.group(1)) if m else None


def _parse_date_dmy(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _collect_date_balance_candidates_from_ocr(
    pdf, stmt_year_month: Optional[Tuple[int, int]]
) -> Dict[str, List[Tuple[float, str, int]]]:
    candidates: Dict[str, List[Tuple[float, str, int]]] = {}

    for page_num, page in enumerate(pdf.pages, start=1):
        text = _ocr_text_page_multi(page)
        lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines() if l.strip()]

        for line in lines:
            dm = _OCR_DATE_RE.match(line)
            if not dm:
                continue

            dt = _parse_date_dmy(dm.group(1))
            if not dt:
                continue

            if stmt_year_month is not None:
                yy, mm = stmt_year_month
                if dt.year != yy or dt.month != mm:
                    continue

            nums = _OCR_MONEY_RE.findall(line)
            if not nums:
                continue

            bal = _ocr_float(nums[-1])
            if bal is None:
                continue

            date_iso = dt.date().isoformat()
            candidates.setdefault(date_iso, []).append((bal, line, page_num))

    out: Dict[str, List[Tuple[float, str, int]]] = {}
    for d, rows in candidates.items():
        seen = set()
        uniq = []
        for bal, line, p in rows:
            key = round(float(bal), 2)
            if key in seen:
                continue
            seen.add(key)
            uniq.append((round(float(bal), 2), line, p))
        uniq.sort(key=lambda x: x[2])
        out[d] = uniq

    return out


def _resolve_one_balance_per_date(
    candidates: Dict[str, List[Tuple[float, str, int]]],
    opening_balance: float,
) -> List[Tuple[str, float, str, int]]:
    dates = sorted(candidates.keys())
    resolved: List[Tuple[str, float, str, int]] = []

    prev = float(opening_balance)

    for d in dates:
        opts = candidates[d]
        if not opts:
            continue

        if len(opts) == 1:
            bal, line, p = opts[0]
            resolved.append((d, bal, line, p))
            prev = bal
            continue

        best = None
        for bal, line, p in opts:
            abs_delta = abs(round(bal - prev, 2))
            cand = (abs_delta, bal, line, p)
            if best is None or cand < best:
                best = cand

        assert best is not None
        _, bal, line, p = best
        resolved.append((d, bal, line, p))
        prev = bal

    return resolved


def _recompute_totals_from_balances(opening: float, rows: List[Tuple[str, float, str, int]]) -> Tuple[float, float]:
    prev = opening
    td = 0.0
    tc = 0.0
    for _, bal, _, _ in rows:
        delta = round(bal - prev, 2)
        if delta > 0:
            tc += delta
        elif delta < 0:
            td += abs(delta)
        prev = bal
    return round(td, 2), round(tc, 2)


def parse_bank_islam_ocr_balance_delta(pdf, source_file) -> List[Dict[str, Any]]:
    if pytesseract is None or not getattr(pdf, "pages", None):
        return []

    opening = _extract_opening_balance_via_ocr(pdf)
    if opening is None:
        return []

    stmt_td, stmt_tc = _extract_summary_totals_via_ocr(pdf)

    stmt_ym = _extract_statement_month_year_via_ocr(pdf)
    cand = _collect_date_balance_candidates_from_ocr(pdf, stmt_ym)
    if not cand:
        return []

    rows = _resolve_one_balance_per_date(cand, opening)
    if not rows:
        return []

    if stmt_td is not None and stmt_tc is not None:
        td, tc = _recompute_totals_from_balances(opening, rows)

    tx: List[Dict[str, Any]] = []
    prev = float(opening)

    for date_iso, bal, line, page_num in rows:
        delta = round(bal - prev, 2)
        credit = delta if delta > 0 else 0.0
        debit = abs(delta) if delta < 0 else 0.0

        desc = line
        m = _OCR_DATE_RE.match(desc)
        if m:
            desc = desc[len(m.group(1)):].strip()

        for n in _OCR_MONEY_RE.findall(line):
            desc = desc.replace(n, "").strip()

        tx.append({
            "date":        date_iso,
            "description": desc,
            "debit":       round(debit,  2),
            "credit":      round(credit, 2),
            "balance":     round(bal,    2),
            "page":        page_num,
            "bank":        "Bank Islam",
            "source_file": source_file,
            "format":      "ocr_balance_delta_v2",
        })

        prev = bal

    return tx



# =========================================================
# SCANNED / GARBLED DETECTION + SUM + WRAPPER
# =========================================================
def _sum_tx(tx: List[Dict[str, Any]]) -> Tuple[float, float]:
    return (
        round(sum(t.get("debit",  0.0) or 0.0 for t in tx), 2),
        round(sum(t.get("credit", 0.0) or 0.0 for t in tx), 2),
    )


def _text_looks_garbled(txt: str) -> bool:
    if not txt:
        return True
    up = txt.upper()
    if up.count("(CID:") >= 20:
        return True
    if len(txt) > 800:
        alnum = sum(ch.isalnum() for ch in txt)
        if (alnum / max(len(txt), 1)) < 0.15:
            return True
    return False


def _looks_like_scanned(source_file: str, pdf) -> bool:
    try:
        if "scan" in (source_file or "").lower() or "scanned" in (source_file or "").lower():
            return True

        pages = getattr(pdf, "pages", []) or []
        if not pages:
            return True

        texts = []
        for p in pages[:3]:
            try:
                texts.append(((p.extract_text() or "").strip()))
            except Exception:
                texts.append("")

        for t in texts:
            if len(t) >= 120 and not _text_looks_garbled(t):
                return False

        if all((len(t) < 80) or _text_looks_garbled(t) for t in texts):
            return True

        return False
    except Exception:
        return True


def parse_bank_islam(pdf, source_file):
    tx = parse_bank_islam_format1(pdf, source_file)
    if not tx:
        tx = parse_bank_islam_format2(pdf, source_file)
    if not tx:
        tx = parse_bank_islam_format4(pdf, source_file)
    if not tx:
        tx = parse_bank_islam_format3(pdf, source_file)

    if _looks_like_scanned(source_file, pdf):
        stmt_td, stmt_tc = _extract_summary_totals_via_ocr(pdf)
        if stmt_td is not None and stmt_tc is not None:
            parsed_td, parsed_tc = _sum_tx(tx)
            if abs(parsed_td - stmt_td) > 0.01 or abs(parsed_tc - stmt_tc) > 0.01:
                tx_ocr = parse_bank_islam_ocr_balance_delta(pdf, source_file)
                if tx_ocr:
                    o_td, o_tc = _sum_tx(tx_ocr)
                    if abs(o_td - stmt_td) <= 0.01 and abs(o_tc - stmt_tc) <= 0.01:
                        return _attach_bank_islam_party_names(tx_ocr)
                    if len(tx_ocr) > len(tx):
                        return _attach_bank_islam_party_names(tx_ocr)

        if not tx:
            tx_ocr = parse_bank_islam_ocr_balance_delta(pdf, source_file)
            if tx_ocr:
                return _attach_bank_islam_party_names(tx_ocr)

    return _attach_bank_islam_party_names(tx)


# ── Smoke-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "statement.pdf"
    with pdfplumber.open(path) as pdf:
        txns = parse_bank_islam(pdf, path)
    print(f"\n{'='*70}\n  {len(txns)} transactions  ·  {path}\n{'='*70}\n")
    for t in txns:
        print(f"  {t['date']}  Dr:{t['debit']:>12.2f}  Cr:{t['credit']:>12.2f}  Bal:{t['balance']:>14.2f}  [{t['format']}]")
        print(f"    {t['description'][:90]}")
    print(f"\n{'='*70}")