# alliance.py
# Alliance Bank Malaysia Berhad statement parser
#
# Interface matches project convention:
#   parse_transactions_alliance(pdf, filename) -> List[dict]
# where pdf is a pdfplumber.PDF instance (from bytes_to_pdfplumber)

import re
from datetime import datetime
from typing import List, Dict, Any, Optional


_TX_START_RE = re.compile(r"^(?P<d>\d{2})(?P<m>\d{2})(?P<y>\d{2})\s+(?P<rest>.+)$")
_MONEY_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*\.\d{2}|\-?\d+\.\d{2}")

_HEADER_SUBSTRS = (
    "STATEMENT OF ACCOUNT",
    "PENYATA AKAUN",
    "PAGE ",
    "HALAMAN ",
    "CURRENT A/C",
    "ACCOUNT NO",
    "NO. AKAUN",
    "CURRENCY",
    "MATAWANG",
    "PROTECTED BY PIDM",
    "DILINDUNGI",
    "CIF NO",
)

_STOP_MARKERS = (
    "THE ITEMS AND BALANCES SHOWN ABOVE WILL BE DEEMED CORRECT",
    "SEGALA BUTIRAN DAN BAKI AKAUN PENYATA DI ATAS DIANGGAP BETUL",
    "ALLIANCE BANK MALAYSIA BERHAD",
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\x00", " ")).strip()


def _is_noise(line: str) -> bool:
    up = _norm(line).upper()
    if not up:
        return True
    if any(k in up for k in _HEADER_SUBSTRS):
        return True
    # common table header
    if (
        "TRANSACTION DETAILS" in up
        and "CHEQUE" in up
        and "DEBIT" in up
        and "CREDIT" in up
        and "BALANCE" in up
    ):
        return True
    if up.startswith("DATE TRANSACTION DETAILS") or up.startswith("TARIKH KETERANGAN"):
        return True
    return False


def _is_stop(line: str) -> bool:
    up = _norm(line).upper()
    return any(m in up for m in _STOP_MARKERS)


def _parse_money_tokens(text: str) -> List[float]:
    out: List[float] = []
    for m in _MONEY_RE.finditer(text):
        try:
            out.append(float(m.group().replace(",", "")))
        except Exception:
            continue
    return out


def _iso_from_ddmmyy(dd: str, mm: str, yy: str) -> Optional[str]:
    y = 2000 + int(yy)
    try:
        dt = datetime(y, int(mm), int(dd))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d")


def _strip_trailing_amounts(s: str) -> str:
    t = _norm(s)
    t = re.sub(r"\s+\b(CR|DR)\b\s*$", "", t, flags=re.I)
    t = re.sub(r"\s+-?\d[\d,]*\.\d{2}\s+-?\d[\d,]*\.\d{2}\s*$", "", t)
    t = re.sub(r"\s+-?\d[\d,]*\.\d{2}\s*$", "", t)
    return _norm(t)


def parse_transactions_alliance(pdf, filename: str) -> List[Dict[str, Any]]:
    """
    Parse Alliance Bank statement into transaction dicts.
    Uses balance-delta to infer debit/credit (robust vs layout variations).
    """
    raw_rows: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for page_no, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = _norm(raw_line)
            if _is_noise(line):
                continue

            if _is_stop(line):
                if current:
                    raw_rows.append(current)
                    current = None
                continue

            m = _TX_START_RE.match(line)
            if m:
                if current:
                    raw_rows.append(current)

                date_iso = _iso_from_ddmmyy(m.group("d"), m.group("m"), m.group("y"))
                if not date_iso:
                    continue
                rest = m.group("rest")

                vals = _parse_money_tokens(line)

                current = {
                    "date": date_iso,
                    "description_parts": [_strip_trailing_amounts(rest)],
                    "amount": None,
                    "balance": None,
                    "page": page_no,
                }

                # typical: ... <amount> <balance>
                if len(vals) >= 2:
                    current["amount"] = vals[-2]
                    current["balance"] = vals[-1]
                elif len(vals) == 1:
                    current["balance"] = vals[-1]
                continue

            # continuation line
            if not current:
                continue

            up = line.upper()
            if "DATE" in up and "TRANSACTION" in up and "DETAILS" in up:
                continue

            current["description_parts"].append(line)

            # sometimes numeric tokens appear on continuation line
            if current.get("balance") is None:
                vals = _parse_money_tokens(line)
                if len(vals) >= 2:
                    current["amount"] = vals[-2]
                    current["balance"] = vals[-1]
                elif len(vals) == 1:
                    current["balance"] = vals[-1]

    if current:
        raw_rows.append(current)

    out: List[Dict[str, Any]] = []
    prev_balance: Optional[float] = None
    seq = 0

    for r in raw_rows:
        seq += 1
        desc = _norm(" ".join(r.get("description_parts") or []))
        desc_up = desc.upper()
        bal = r.get("balance")

        # BEGINNING BALANCE row
        if "BEGINNING BALANCE" in desc_up and isinstance(bal, (int, float)):
            prev_balance = float(bal)
            out.append(
                {
                    "date": r["date"],
                    "description": "BEGINNING BALANCE",
                    "debit": 0.0,
                    "credit": 0.0,
                    "balance": float(bal),
                    "page": int(r.get("page") or 0),
                    "seq": seq,
                    "bank": "Alliance Bank",
                    "source_file": filename,
                }
            )
            continue

        debit = 0.0
        credit = 0.0

        if isinstance(prev_balance, (int, float)) and isinstance(bal, (int, float)):
            delta = round(float(bal) - float(prev_balance), 2)
            if delta >= 0:
                credit = abs(delta)
            else:
                debit = abs(delta)
            prev_balance = float(bal)
        else:
            # fallback if beginning balance wasn't parsed
            amt = r.get("amount")
            if amt is None:
                amt = 0.0
            debit = float(amt)

        if "ENDING BALANCE" in desc_up and isinstance(bal, (int, float)):
            desc = "ENDING BALANCE"

        out.append(
            {
                "date": r["date"],
                "description": desc,
                "debit": float(debit),
                "credit": float(credit),
                "balance": float(bal) if isinstance(bal, (int, float)) else None,
                "page": int(r.get("page") or 0),
                "seq": seq,
                "bank": "Alliance Bank",
                "source_file": filename,
            }
        )

    return out
