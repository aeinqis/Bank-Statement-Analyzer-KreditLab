
# ambank.py
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pdfplumber

# =========================================================
# Regex patterns (supports multiple AmBank layouts)
# =========================================================

# Transaction date tokens: supports "01Aug", "01-Aug", "01 Aug"
TX_START_RE = re.compile(
    r"^(?P<day>\d{1,2})\s*[-/]?\s*(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

# Money tokens (e.g. 1,234.56)
MONEY_ANYWHERE_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)")

# Statement date range (older layout)
STMT_RANGE_RE = re.compile(
    r"STATEMENT\s+DATE.*?:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

# Statement single date (newer "Deposits Combined Statement" layout)
STMT_SINGLE_DATE_RE = re.compile(
    r"STATEMENT\s+DATE\s*/\s*TARIKH\s+PENYATA\s*:?\s*(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

# Account summary labels (English/Malay) - may not exist in deposits combined statement
OPENING_LBL_RE = re.compile(r"(OPENING\s+BALANCE|BAKI\s+PEMBUKAAN)", re.IGNORECASE)
CLOSING_LBL_RE = re.compile(r"(CLOSING\s+BALANCE|BAKI\s+PENUTUPAN|CLOSING\s+BALANCE\s+BAKI\s+PENUTUPAN)", re.IGNORECASE)
TOTAL_DEBIT_LBL_RE = re.compile(r"(TOTAL\s+DEBITS?|JUMLAH\s+DEBIT)", re.IGNORECASE)
TOTAL_CREDIT_LBL_RE = re.compile(r"(TOTAL\s+CREDITS?|JUMLAH\s+KREDIT)", re.IGNORECASE)

# Deposits Combined Statement "TOTAL / JUMLAH <debit> <credit>" appears at bottom of detailed tx pages
TOTAL_JUMLAH_RE = re.compile(r"\bTOTAL\s*/\s*JUMLAH\b", re.IGNORECASE)

# Balance brought forward line (new layout)
BALANCE_BF_RE = re.compile(r"^\s*Balance\s+Brought\s+Fwd\b", re.IGNORECASE)

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# =========================================================
# Helpers
# =========================================================

def _safe_float_money(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    # allow commas
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}", s):
        return None
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def _normalize_lines_keep_order(text: str) -> List[str]:
    lines: List[str] = []
    for raw in (text or "").splitlines():
        ln = re.sub(r"\s+", " ", raw).strip()
        if ln:
            lines.append(ln)
    return lines


def _find_amount_near_label(lines: List[str], label_re: re.Pattern) -> Optional[float]:
    """
    Robust for layouts where amounts sometimes appear on the line ABOVE the label.

    Strategy:
    - Find first line index where label appears.
    - Try money token on same line.
    - If none, scan upward then downward within small window.
    """
    idxs = [i for i, ln in enumerate(lines) if label_re.search(ln)]
    if not idxs:
        return None

    i = idxs[0]

    # same line
    m = MONEY_ANYWHERE_RE.findall(lines[i])
    if m:
        return _safe_float_money(m[-1])

    for j in range(i - 1, max(-1, i - 6), -1):
        m2 = MONEY_ANYWHERE_RE.findall(lines[j])
        if m2:
            return _safe_float_money(m2[-1])

    for j in range(i + 1, min(len(lines), i + 6)):
        m3 = MONEY_ANYWHERE_RE.findall(lines[j])
        if m3:
            return _safe_float_money(m3[-1])

    return None


def _extract_statement_month(pdf: pdfplumber.PDF) -> Tuple[Optional[str], Optional[int]]:
    """
    Returns (statement_month 'YYYY-MM', detected_year).
    Tries:
      1) date range end date
      2) single statement date
    """
    if not pdf.pages:
        return None, None

    t0 = pdf.pages[0].extract_text(x_tolerance=1) or ""

    m = STMT_RANGE_RE.search(t0)
    if m:
        try:
            end_dt = datetime.strptime(m.group(2), "%d/%m/%Y")
            return end_dt.strftime("%Y-%m"), end_dt.year
        except Exception:
            pass

    m2 = STMT_SINGLE_DATE_RE.search(t0)
    if m2:
        try:
            dt = datetime.strptime(m2.group(1), "%d/%m/%Y")
            return dt.strftime("%Y-%m"), dt.year
        except Exception:
            pass

    # fallback: current year
    now = datetime.utcnow()
    return None, now.year


def extract_ambank_statement_totals(pdf: pdfplumber.PDF, source_file: str = "") -> Dict[str, Optional[float]]:
    """
    Extract monthly totals + balances for AmBank.

    Works across:
    - older "Account Summary" pages with explicit Opening/CLOSING/TOTAL labels
    - newer "Deposits Combined Statement" where:
        * closing balance appears on page 1 (Account Summary / Closing Balance)
        * opening balance appears as "Balance Brought Fwd" on detailed tx page
        * totals appear as "TOTAL / JUMLAH <debit> <credit>" at bottom of detailed tx table

    Returns:
      {
        statement_month: "YYYY-MM" | None,
        opening_balance: float | None,
        ending_balance: float | None,
        total_debit: float | None,
        total_credit: float | None,
        source_file: str
      }
    """
    out: Dict[str, Optional[float]] = {
        "statement_month": None,
        "opening_balance": None,
        "ending_balance": None,
        "total_debit": None,
        "total_credit": None,
        "source_file": source_file,
    }
    if not pdf.pages:
        return out

    stmt_month, detected_year = _extract_statement_month(pdf)
    out["statement_month"] = stmt_month

    # Page 1: try to find closing balance and labels
    text1 = pdf.pages[0].extract_text(x_tolerance=1) or ""
    lines1 = _normalize_lines_keep_order(text1)

    out["opening_balance"] = _find_amount_near_label(lines1, OPENING_LBL_RE)
    out["ending_balance"] = _find_amount_near_label(lines1, CLOSING_LBL_RE)
    out["total_debit"] = _find_amount_near_label(lines1, TOTAL_DEBIT_LBL_RE)
    out["total_credit"] = _find_amount_near_label(lines1, TOTAL_CREDIT_LBL_RE)

    # New layout: opening balance + totals likely on detail pages
    for page in pdf.pages[1:]:
        t = page.extract_text(x_tolerance=1) or ""
        if not t.strip():
            continue
        lines = _normalize_lines_keep_order(t)

        # Opening from "Balance Brought Fwd"
        if out["opening_balance"] is None:
            for ln in lines:
                if BALANCE_BF_RE.search(ln):
                    m = MONEY_ANYWHERE_RE.findall(ln)
                    if m:
                        out["opening_balance"] = _safe_float_money(m[-1])
                        break

        # Totals from "TOTAL / JUMLAH"
        if (out["total_debit"] is None) or (out["total_credit"] is None):
            for ln in lines[::-1]:
                if TOTAL_JUMLAH_RE.search(ln):
                    monies = MONEY_ANYWHERE_RE.findall(ln)
                    # Typically: TOTAL / JUMLAH <debit> <credit>
                    if len(monies) >= 2:
                        out["total_debit"] = out["total_debit"] if out["total_debit"] is not None else _safe_float_money(monies[-2])
                        out["total_credit"] = out["total_credit"] if out["total_credit"] is not None else _safe_float_money(monies[-1])
                    break

    return out


def _to_iso_date(day: str, mon: str, year: int) -> Optional[str]:
    mm = _MONTH_MAP.get((mon or "").upper())
    if not mm:
        return None
    try:
        dd = int(day)
        dt = datetime(year, mm, dd)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _extract_money_tokens(s: str) -> List[str]:
    return MONEY_ANYWHERE_RE.findall(s or "")


def _classify_amount(desc: str) -> str:
    """
    Decide whether the transaction amount belongs to debit or credit.
    """
    up = (desc or "").upper()

    # Strong credit signals
    if " CR " in f" {up} ":
        return "credit"
    if "CREDIT" in up:
        return "credit"
    if "DuitNow CR".upper() in up:
        return "credit"
    if "INW" in up and "CHQ" in up:
        # "INW AMB CHQ PRESENTED" is withdrawal (debit)
        return "debit"

    # Strong debit signals
    if " DEBIT" in up:
        return "debit"
    if "/DEBIT" in up:
        return "debit"
    if "FEE" in up or "CHARGE" in up or "INT" in up:
        return "debit"
    if "TRANSFER" in up and "AUTO DEBIT" in up:
        return "debit"

    # default: unknown
    return "unknown"


def _finalize_tx(
    *,
    date_iso: str,
    buf: List[str],
    page_num: int,
    filename: str,
    prev_balance: Optional[float],
    seq: int,
) -> Tuple[Optional[Dict], Optional[float]]:
    joined = " ".join([b for b in buf if b]).strip()
    if not joined:
        return None, prev_balance

    # Skip totals line (not a transaction)
    if TOTAL_JUMLAH_RE.search(joined):
        return None, prev_balance

    monies = _extract_money_tokens(joined)
    if not monies:
        return None, prev_balance

    # Balance B/F is treated as an anchor row (no debit/credit)
    if BALANCE_BF_RE.search(joined):
        bal = _safe_float_money(monies[-1])
        if bal is None:
            return None, prev_balance
        tx = {
            "date": date_iso,
            "description": "Balance Brought Fwd",
            "debit": 0.0,
            "credit": 0.0,
            "balance": round(float(bal), 2),
            "page": int(page_num),
            "seq": int(seq),
            "bank": "Ambank",
            "source_file": filename,
            "is_balance_bf": True,
        }
        return tx, bal

    # Last money token is running balance, previous is usually txn amount (if present)
    balance_token = monies[-1]
    balance = _safe_float_money(balance_token)
    if balance is None:
        return None, prev_balance

    amount_token = monies[-2] if len(monies) >= 2 else None
    amount = _safe_float_money(amount_token) if amount_token else None

    # Remove one occurrence of balance token anywhere (PDF extraction order can interleave columns)
    desc = joined
    desc = re.sub(rf"\b{re.escape(balance_token)}\b", "", desc, count=1)
    # Remove adjacent DR/CR markers that often stick after balance
    desc = re.sub(r"\b(DR|CR)\b", "", desc, count=1, flags=re.IGNORECASE)

    # Remove one occurrence of amount token if present
    if amount_token:
        desc = re.sub(rf"\b{re.escape(amount_token)}\b", "", desc, count=1)

    # Cleanup spacing
    desc = re.sub(r"\s+", " ", desc).strip()
    if not desc:
        desc = "(NO DESCRIPTION)"

    debit = 0.0
    credit = 0.0

    if amount is not None:
        classification = _classify_amount(desc)
        if classification == "credit":
            credit = float(abs(amount))
        elif classification == "debit":
            debit = float(abs(amount))
        else:
            # fallback: delta sign if we can
            if prev_balance is not None:
                delta = round(balance - prev_balance, 2)
                if delta > 0:
                    credit = float(abs(delta))
                elif delta < 0:
                    debit = float(abs(delta))
            else:
                # no clue: assume debit (safer for fees/charges)
                debit = float(abs(amount))
    else:
        # no amount token; infer from balance change if possible
        if prev_balance is not None:
            delta = round(balance - prev_balance, 2)
            if delta > 0:
                credit = float(abs(delta))
            elif delta < 0:
                debit = float(abs(delta))

    tx = {
        "date": date_iso,
        "description": desc,
        "debit": round(float(debit), 2),
        "credit": round(float(credit), 2),
        "balance": round(float(balance), 2),
        "page": int(page_num),
        "seq": int(seq),
        "bank": "Ambank",
        "source_file": filename,
    }
    return tx, balance


def _parse_transactions_from_lines(
    lines: List[str],
    *,
    page_num: int,
    filename: str,
    detected_year: int,
    statement_month: Optional[str],
    prev_balance: Optional[float],
    seq_start: int,
) -> Tuple[List[Dict], Optional[float], int]:
    txs: List[Dict] = []
    buf: List[str] = []
    cur_date_iso: Optional[str] = None
    seq = seq_start

    # If we see Balance B/F before any tx line, capture it and set prev_balance
    for ln in lines:
        if BALANCE_BF_RE.search(ln):
            monies = _extract_money_tokens(ln)
            if monies:
                bal = _safe_float_money(monies[-1])
                if bal is not None:
                    prev_balance = bal
            break

    # Use first day of statement month for Balance B/F row date if we can
    bf_date_iso = None
    if statement_month:
        try:
            bf_date_iso = f"{statement_month}-01"
        except Exception:
            bf_date_iso = None

    def flush():
        nonlocal prev_balance, seq, buf, cur_date_iso
        if cur_date_iso is None:
            buf = []
            return
        tx, new_prev = _finalize_tx(
            date_iso=cur_date_iso,
            buf=buf,
            page_num=page_num,
            filename=filename,
            prev_balance=prev_balance,
            seq=seq,
        )
        if tx:
            txs.append(tx)
            prev_balance = new_prev
            seq += 1
        buf = []
        cur_date_iso = None

    # If Balance B/F appears as its own "row" (new layout), emit it once at start of first detail page
    emitted_bf = False

    for ln in lines:
        up = ln.upper()

        # stop when leaving transaction table
        if up.startswith("1. PRIVACY NOTICE") or up.startswith("PRIVACY NOTICE"):
            flush()
            break

        # skip headers/noise
        if "DEPOSITS COMBINED STATEMENT" in up:
            continue
        if "DETAILED ACCOUNT TRANSACTION" in up:
            continue
        if up.startswith("DATE ") and "TRANSACTION" in up and "DEBIT" in up:
            continue
        if up.startswith("ACCOUNT NAME") or up.startswith("PRODUCT NAME") or up.startswith("ACCOUNT NO"):
            continue
        if up.startswith("STATEMENT DATE") or up.startswith("PAGE / MUKA SURAT"):
            continue
        if TOTAL_JUMLAH_RE.search(ln):
            flush()
            continue

        if BALANCE_BF_RE.search(ln) and not emitted_bf:
            # emit BF row
            monies = _extract_money_tokens(ln)
            if monies:
                bal = _safe_float_money(monies[-1])
                if bal is not None:
                    tx = {
                        "date": bf_date_iso or (cur_date_iso or f"{detected_year}-01-01"),
                        "description": "Balance Brought Fwd",
                        "debit": 0.0,
                        "credit": 0.0,
                        "balance": round(float(bal), 2),
                        "page": int(page_num),
                        "seq": int(seq),
                        "bank": "Ambank",
                        "source_file": filename,
                        "is_balance_bf": True,
                    }
                    txs.append(tx)
                    prev_balance = bal
                    seq += 1
                    emitted_bf = True
            continue

        m = TX_START_RE.match(ln)
        if m:
            flush()
            cur_date_iso = _to_iso_date(m.group("day"), m.group("mon"), detected_year)
            rest = (m.group("rest") or "").strip()
            buf = [rest] if (cur_date_iso and rest) else []
        else:
            if cur_date_iso is not None:
                buf.append(ln)

    flush()
    return txs, prev_balance, seq


def parse_ambank(pdf: pdfplumber.PDF, filename: str) -> List[Dict]:
    """
    Parse AmBank transactions.

    This parser supports both:
    - older "ddMon" single-line layouts
    - newer "Deposits Combined Statement" layouts with "dd-Mon" and multi-line rows

    Note: Monthly totals should come from extract_ambank_statement_totals() (used in app.py).
    """
    stmt_month, detected_year = _extract_statement_month(pdf)
    detected_year = detected_year or datetime.utcnow().year

    # Use opening anchor if available, else will be updated by Balance B/F
    statement_totals = extract_ambank_statement_totals(pdf, filename)
    prev_balance = statement_totals.get("opening_balance")

    transactions: List[Dict] = []
    seq = 0

    for page_num, page in enumerate(pdf.pages, start=1):
        text = page.extract_text(x_tolerance=1) or ""
        if not text.strip():
            continue
        lines = _normalize_lines_keep_order(text)

        page_txs, prev_balance, seq = _parse_transactions_from_lines(
            lines,
            page_num=page_num,
            filename=filename,
            detected_year=int(detected_year),
            statement_month=stmt_month,
            prev_balance=prev_balance,
            seq_start=seq,
        )
        transactions.extend(page_txs)

    transactions = sorted(
        transactions,
        key=lambda t: (t.get("date") or "", int(t.get("page") or 0), int(t.get("seq") or 0)),
    )
    return transactions
