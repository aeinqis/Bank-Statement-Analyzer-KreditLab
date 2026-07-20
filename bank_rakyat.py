# bank_rakyat.py
# Bank Rakyat – summary-aware word-position parser

from __future__ import annotations

import re
from contextlib import nullcontext
from datetime import datetime
from io import BytesIO
from typing import Any

import pdfplumber


# ── Regex constants ────────────────────────────────────────────────────────────

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
AMOUNT_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
AMOUNT_FIND_RE = re.compile(r"[-]?\d[\d,]*\.\d{2}")
SUMMARY_KW = re.compile(r"baki.{0,3}permulaan|opening.{0,3}balance", re.I)

BANK_RAKYAT_CHEQUE_RE = re.compile(
    r"\b(?:CHEQUE|CHQ|CEK|CHECK|CLEARING|CHEQUE\s*DEPOSIT|CHQ\s*WDL|CHQ\s*PROCESSING)\b",
    re.I,
)

BANK_RAKYAT_PATTERNS = [
    re.compile(
        r"^(?:\d{5}\s+)?DUITNOW\s*TRANSFER\s+(?P<party>.+?)(?:\s+(?:AGROBIZ|TRADING|SIMPANAN|STAFFID\d+|BAYARAN|BAY\d+|PWR|GAJI|REFUND|LEKUPAN|MMB|MMBEKAL|ANGKUT|SEWA|PERUBATAN|TUNTUTAN|INV|KZ\d+|PAKKF|PBWPHG|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?DUITNOW\s*FEE\s+(?P<party>.+?)(?:\s+(?:AGROBIZ|TRADING|STAFFID\d+|BAY\d+|PENDAHULUAN|GAJI|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?CIB\s+(?:DR|CR)\s+(?:ADVICE|CHARGES)\s+(?P<party>.+?)(?:\s+(?:AGROBIZ|STAFFID\d+|GAJI|PINDAHAN|PWR|SEWA|BELIAN|SERVICE|TUGASAN|PERUBATAN|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?CIB\s+SMS\s+FEE\s+(?P<party>.+?)(?:\s+(?:AGROBIZ|TRADING|STAFFID\d+|GAJI|PINDAHAN|PWR|BAYARAN|BAY\d+|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?CIB\s+(?:DR\s+ADVICE|COMMISSION)\s*\(IBG\)\s+(?P<party>.+?)(?:\s+(?:AGROBIZ|SUMBANGAN|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?IBG\s+(?:CREDIT|INWARD\s+RETURN)\s+(?P<party>.+?)(?:\s+(?:KZ|BAY|PBWPHG|INV|STAFFID\d+|AGROBIZ|$).*)?$",
        re.I,
    ),
    re.compile(
        r"^(?:\d{5}\s+)?CASH\s*DEPOSIT\s*(?P<party>.*)$",
        re.I,
    ),
    re.compile(
        r"^DUITNOWTRANSFER\s+(?P<party>.+?)(?:\s+(?:FAROBORNEOFUND|FUNDTRANSFER|PAYMENT|REF).*)?$",
        re.I,
    ),
    re.compile(
        r"^TRTOSAVINGS\s+(?P<party>.+?)(?:\s+\d{6,}.*|\s+TRANSFERFROM.*|$)",
        re.I,
    ),
    re.compile(
        r"^CASHWITHDRAWAL\s+(?P<party>.+?)(?:\s+\d{6,}.*|\s+CASHW?DRAWAL.*|$)",
        re.I,
    ),
]

BANK_RAKYAT_COMPANY_PREFIX_RE = re.compile(
    r"^(?:\d{2,6}\s+)?(?:"
    r"DUITNOW\s*(?:TRANSFER|FEE)|"
    r"CIB\s+(?:DR|CR)\s+(?:ADVICE|CHARGES)|"
    r"CIB\s+SMS\s+FEE|"
    r"CIB\s+(?:DR\s+ADVICE|COMMISSION)\s*\(IBG\)|"
    r"IBG\s+(?:CREDIT|INWARD\s+RETURN)|"
    r"BANK\s+RAKYAT\s+(?:TRANSFER|PAYMENT)|"
    r"TRANSFER\s+(?:TO|FROM)|"
    r"PAYMENT\s+(?:TO|FROM)"
    r")\s+",
    re.I,
)

BANK_RAKYAT_COMPANY_NAME_RE = re.compile(
    r"\b(?P<party>[A-Z0-9][A-Z0-9 &'()./-]*?\b(?:SDN\.?\s*BHD\.?|BHD\.?|BERHAD|PLT)\b)",
    re.I,
)


# ── Amount / date helpers ──────────────────────────────────────────────────────

def clean_amount(val: Any) -> float | None:
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return None


def is_amount(value: str) -> bool:
    return bool(AMOUNT_RE.match(str(value).strip()))


def parse_date(raw: str) -> str | None:
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        return None


def clean_bank_rakyat_party_name(value: str) -> str:
    party = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-").upper()
    if not party:
        return "UNKNOWN"
    party = re.sub(r"^\(?IBG\)?\s+", "", party, flags=re.I)
    party = re.sub(r"\bSDNBHD\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BHD\.?\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*BH\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bSDN\.?\s*B\b", "SDN BHD", party, flags=re.I)
    party = re.sub(r"\bBERHAD\b", "BHD", party, flags=re.I)
    company_match = re.search(r"\b(?:SDN\s+BHD|BHD|PLT)\b", party, re.I)
    if company_match:
        party = party[:company_match.end()]
        party = re.sub(r"\s{2,}", " ", party).strip(" ,.-")
        return party or "UNKNOWN"
    party = re.sub(
        r"\b(?:AGROBIZ|TRADING|SIMPANAN|STAFFID\d+|GAJI\s+\w+\d*|GAJI|BAYARAN|BAY\d*|"
        r"PWR|PINDAHAN|REFUND|LEKUPAN|SUMBANGAN|INV[-/\w]*|KZ[\w/-]*|PBWPHG/[\w/-]*|"
        r"PAKKF[\w/-]*|MMB\w*|MMBEKAL|ANGKUT|SEWA|PERUBATAN|TUNTUTAN|SERVICE|BELIAN|"
        r"TRANSFERFROM.*|FAROBORNEOFUND|FUNDTRANSFER|CASHW?DRAWAL|PAYMENT|REFERENCE|REF)\b.*$",
        "", party, flags=re.I,
    )
    party = re.sub(r"\b\d{6,}\b.*$", "", party)
    party = re.sub(r"\bFAROENGINEERING\b", "FARO ENGINEERING", party, flags=re.I)
    party = re.sub(r"\bROSMANHASHIM\b", "ROSMAN HASHIM", party, flags=re.I)
    party = re.sub(r"\bWANHABIBOTHMANBINWANRAZALI\b", "WAN HABIB OTHMAN BIN WAN RAZALI", party, flags=re.I)
    party = re.sub(r"\s{2,}", " ", party).strip(" ,.-")
    return party or "UNKNOWN"


def extract_bank_rakyat_party_name(description: str) -> str:
    desc = re.sub(r"\s+", " ", str(description or "")).strip()
    if not desc:
        return "UNKNOWN"
    if BANK_RAKYAT_CHEQUE_RE.search(desc):
        return "CHEQUE"
    if re.match(r"^(?:\d{5}\s+)?BILL\s*PAYMENT\s*TO\s*FIN\b", desc, re.I):
        return "BILL PAYMENT TO FIN"
    if re.match(r"^(?:\d{5}\s+)?CREDIT\s*PROFIT\s*/?\s*HIBAH\b", desc, re.I):
        return "CREDIT PROFIT / HIBAH"
    if re.match(r"^CDM\s*CASH\s*DEPOSIT\b", desc, re.I):
        return "CASH DEPOSIT"
    for pattern in BANK_RAKYAT_PATTERNS:
        m = pattern.search(desc)
        if m:
            party = m.groupdict().get("party", "")
            return clean_bank_rakyat_party_name(party)
    company_text = BANK_RAKYAT_COMPANY_PREFIX_RE.sub("", desc)
    company_match = BANK_RAKYAT_COMPANY_NAME_RE.search(company_text)
    if company_match:
        return clean_bank_rakyat_party_name(company_match.group("party"))
    return "UNKNOWN"


# ── Summary extraction ─────────────────────────────────────────────────────────

def _blank_summary() -> dict:
    return {"opening": None, "total_debit": None, "total_credit": None, "closing": None}


def extract_summary_from_text(full_text: str) -> dict:
    nums = [clean_amount(x) for x in AMOUNT_FIND_RE.findall(full_text or "")]
    nums = [n for n in nums if n is not None]
    summary = _blank_summary()

    row_patterns = [
        (
            r"(?:Opening\s+Balance|Baki\s+Permulaan).*?"
            r"(?:Closing\s+Balance|Baki\s+Penutup)\s*"
            r"\n\s*([-]?\d[\d,]*\.\d{2})\s+\d+\s+"
            r"([-]?\d[\d,]*\.\d{2})\s+\d+\s+"
            r"([-]?\d[\d,]*\.\d{2})\s+([-]?\d[\d,]*\.\d{2})"
        ),
        (
            r"(?:Baki\s+Permulaan|Opening\s+Balance).*?"
            r"(?:Baki\s+Penutup|Closing\s+Balance)\s*"
            r"\n\s*([-]?\d[\d,]*\.\d{2})\s+\d+\s+"
            r"([-]?\d[\d,]*\.\d{2})\s+\d+\s+"
            r"([-]?\d[\d,]*\.\d{2})\s+([-]?\d[\d,]*\.\d{2})"
        ),
    ]
    for pattern in row_patterns:
        match = re.search(pattern, full_text or "", re.I | re.S)
        if not match:
            continue
        summary["opening"] = clean_amount(match.group(1))
        summary["total_debit"] = clean_amount(match.group(2))
        summary["total_credit"] = clean_amount(match.group(3))
        summary["closing"] = clean_amount(match.group(4))
        break

    if summary["opening"] is None:
        match = re.search(r"(Opening Balance|Baki Permulaan)[^\d\-]*([-]?\d[\d,]*\.\d{2})", full_text or "", re.I)
        if match:
            summary["opening"] = clean_amount(match.group(2))

    if summary["closing"] is None:
        match = re.search(r"(Closing Balance|Baki Penutup)[^\d\-]*([-]?\d[\d,]*\.\d{2})", full_text or "", re.I)
        if match:
            summary["closing"] = clean_amount(match.group(2))

    if len(nums) >= 4:
        summary["opening"]       = summary["opening"]       if summary["opening"]       is not None else nums[-4]
        summary["total_debit"]   = summary["total_debit"]   if summary["total_debit"]   is not None else nums[-3]
        summary["total_credit"]  = summary["total_credit"]  if summary["total_credit"]  is not None else nums[-2]
        summary["closing"]       = summary["closing"]       if summary["closing"]       is not None else nums[-1]

    return summary


def extract_summary(full_text: str) -> dict:
    return extract_summary_from_text(full_text)


# ── Word → line clustering ─────────────────────────────────────────────────────

def words_to_lines(words: list[dict], y_gap: int = 6) -> dict[float, list[dict]]:
    lines: dict[float, list[dict]] = {}
    for word in words:
        matched = None
        for y in list(lines.keys()):
            if abs(word["top"] - y) <= y_gap:
                matched = y
                break
        if matched is None:
            matched = word["top"]
        lines.setdefault(matched, []).append(word)
    return lines


# ── Page boundary detection ────────────────────────────────────────────────────

def find_boundary_ys(lines: dict[float, list[dict]]) -> tuple[float | None, float | None]:
    header_y = summary_y = None
    for y in sorted(lines):
        texts = " ".join(str(w["text"]) for w in lines[y])
        if header_y is None and re.search(r"\btarikh\b|\bdate\b", texts, re.I):
            header_y = y
        if header_y is not None and SUMMARY_KW.search(texts):
            summary_y = y
            break
    return header_y, summary_y


# ── Dynamic column calibration ─────────────────────────────────────────────────

def cluster_xs(xs: list[float], gap: int = 30) -> list[float]:
    if not xs:
        return []
    sorted_xs = sorted(xs)
    clusters = [[sorted_xs[0]]]
    for x in sorted_xs[1:]:
        if x - clusters[-1][-1] <= gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [min(cluster) for cluster in clusters]


def calibrate_columns(
    lines: dict[float, list[dict]],
    header_y: float,
    summary_y: float | None,
) -> dict[str, tuple[float, float]]:
    amount_xs: list[float] = []
    date_xs: list[float] = []
    code_xs: list[float] = []
    desc_xs: list[float] = []

    for y in sorted(lines):
        if y <= header_y + 5:
            continue
        if summary_y is not None and y >= summary_y - 2:
            break
        row = sorted(lines[y], key=lambda w: w["x0"])
        for word in row:
            text = str(word["text"]).strip()
            x = float(word["x0"])
            if is_amount(text):
                amount_xs.append(x)
            elif DATE_RE.match(text):
                date_xs.append(x)
            elif re.match(r"^\d{3,6}$", text):
                code_xs.append(x)
            elif len(text) > 1 and (
                (date_xs and x > min(date_xs) + 20)
                or (code_xs and x > min(code_xs) + 20)
            ):
                desc_xs.append(x)

    if not amount_xs:
        return {
            "date": (0, 80), "code": (80, 160), "desc": (160, 330),
            "debit": (330, 425), "credit": (425, 515), "balance": (515, 9999),
        }

    amount_clusters = cluster_xs(amount_xs, gap=30)
    if len(amount_clusters) >= 3:
        debit_x, credit_x, balance_x = amount_clusters[-3:]
    elif len(amount_clusters) == 2:
        debit_x = amount_clusters[0]
        balance_x = amount_clusters[1]
        credit_x = debit_x + (balance_x - debit_x) * 0.55
    else:
        debit_x = credit_x = 330
        balance_x = 510

    date_x = min(date_xs) if date_xs else 0
    code_x = min(code_xs) if code_xs else None
    desc_x = min(desc_xs) if desc_xs else None

    if code_x is None:
        code_x = desc_x if desc_x else date_x + 70
    if desc_x is None:
        desc_x = code_x + 40

    return {
        "date":    (date_x  - 8,  code_x  - 2),
        "code":    (code_x  - 2,  desc_x  - 2),
        "desc":    (desc_x  - 2,  debit_x - 5),
        "debit":   (debit_x - 5,  credit_x - 5),
        "credit":  (credit_x - 5, balance_x - 5),
        "balance": (balance_x - 5, 9999),
    }


def in_col(x: float, col: tuple[float, float]) -> bool:
    return col[0] <= x < col[1]


# ── Positioned summary fallback ────────────────────────────────────────────────

def extract_summary_from_lines(lines: dict[float, list[dict]], summary_y: float | None) -> dict:
    summary = _blank_summary()
    if summary_y is None:
        return summary
    for y in sorted(row_y for row_y in lines if row_y >= summary_y):
        row = sorted(lines[y], key=lambda w: w["x0"])
        amounts = [
            (float(w["x0"]), clean_amount(w["text"]))
            for w in row
            if is_amount(str(w["text"])) and clean_amount(w["text"]) is not None
        ]
        if len(amounts) >= 4:
            amounts.sort(key=lambda item: item[0])
            summary["opening"]      = amounts[0][1]
            summary["total_debit"]  = amounts[1][1]
            summary["total_credit"] = amounts[2][1]
            summary["closing"]      = amounts[-1][1]
            break
    return summary


# ── Transaction extraction ─────────────────────────────────────────────────────

def extract_transactions(
    lines: dict[float, list[dict]],
    cols: dict[str, tuple[float, float]],
    header_y: float,
    summary_y: float | None,
    page_no: int,
) -> list[dict]:
    txns: list[dict] = []
    txn_ys = sorted(
        y for y in lines
        if y > header_y + 5 and (summary_y is None or y < summary_y - 2)
    )
    current = None

    for y in txn_ys:
        row = sorted(lines[y], key=lambda w: w["x0"])

        def get(role: str) -> list[str]:
            if role not in cols:
                return []
            # ── SPACE FIX ─────────────────────────────────────────────────────
            # extract_words(x_tolerance=1.5) already splits tokens at the correct
            # word boundaries — the ~1.9pt inter-word gaps in V2 PDFs are wider
            # than x_tolerance, so each word arrives as a separate element.
            # Simply join them with a single space; no secondary gap detection is
            # needed and a secondary gap check with threshold > 2 would silently
            # drop the spaces because those gaps are only ~1.89–1.98 pt.
            words_in_col = [
                str(w["text"]) for w in row
                if in_col(float(w["x0"]), cols[role])
            ]
            return [" ".join(words_in_col)] if words_in_col else []

        date_words   = get("date")
        code_words   = [t for t in get("code")    if not is_amount(t)]
        desc_words   = [t for t in get("desc")    if not is_amount(t)]
        debit_words  = [t for t in get("debit")   if is_amount(t)]
        credit_words = [t for t in get("credit")  if is_amount(t)]
        bal_words    = [t for t in get("balance") if is_amount(t)]

        date_str  = " ".join(date_words).strip()
        has_date  = bool(DATE_RE.match(date_str))
        has_bal   = bool(bal_words)

        if has_bal:
            if current:
                txns.append(current)
            current = {
                "date":             parse_date(date_str) if has_date else None,
                "transaction_code": " ".join(code_words).strip(),
                "description":      " ".join(desc_words).strip(),
                "debit":            clean_amount(" ".join(debit_words)) or 0.0,
                "credit":           clean_amount(" ".join(credit_words)) or 0.0,
                "balance":          clean_amount(bal_words[0]),
                "page":             page_no,
            }
        elif current is not None:
            if has_date and not current["date"]:
                current["date"] = parse_date(date_str)
            if code_words and not current["transaction_code"]:
                current["transaction_code"] = " ".join(code_words).strip()
            if desc_words:
                sep = " " if current["description"] else ""
                current["description"] += sep + " ".join(desc_words).strip()
            if debit_words and not current["debit"]:
                current["debit"] = clean_amount(" ".join(debit_words)) or 0.0
            if credit_words and not current["credit"]:
                current["credit"] = clean_amount(" ".join(credit_words)) or 0.0

    if current:
        txns.append(current)
    return [t for t in txns if t.get("date") and t.get("balance") is not None]


# ── Main entry point ───────────────────────────────────────────────────────────

def _open_bank_rakyat_pdf(pdf_input):
    if hasattr(pdf_input, "pages"):
        return nullcontext(pdf_input)
    if isinstance(pdf_input, (bytes, bytearray)):
        return pdfplumber.open(BytesIO(pdf_input))
    return pdfplumber.open(pdf_input)


def parse_bank_rakyat(pdf_path, source_filename=""):
    all_txns: list[dict] = []
    summary: dict = {}

    with _open_bank_rakyat_pdf(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                keep_blank_chars=True,
                # x_tolerance=1.5 is deliberately below the ~1.9 pt inter-word
                # gap used by V2 (CASA) PDFs, which encode spaces as character
                # spacing rather than actual space characters.  V1 (Cashline)
                # PDFs embed real space chars and are unaffected by this value.
                x_tolerance=1.5,
                y_tolerance=3,
            )

            lines = words_to_lines(words, y_gap=6)
            header_y, summary_y = find_boundary_ys(lines)

            page_summary = extract_summary_from_lines(lines, summary_y)
            if page_summary:
                summary.update({k: v for k, v in page_summary.items() if v is not None})

            if header_y is None:
                continue

            cols = calibrate_columns(lines, header_y, summary_y)
            all_txns.extend(
                extract_transactions(lines, cols, header_y, summary_y, page_no)
            )

    if not all_txns:
        return []

    all_txns.sort(key=lambda x: (x.get("date", ""), x.get("page", 0)))

    for row_order, row in enumerate(all_txns):
        row["_statement_order"] = row_order

    opening = summary.get("opening")
    if opening is None and summary.get("closing") is not None:
        opening = round(
            float(summary.get("closing", 0.0))
            - float(summary.get("total_credit") or 0.0)
            + float(summary.get("total_debit") or 0.0),
            2,
        )

    prev_balance = opening
    results = []

    for row in all_txns:
        debit   = row.get("debit")  or 0.0
        credit  = row.get("credit") or 0.0
        balance = row.get("balance")
        description = row.get("description", "")

        if debit == 0.0 and credit == 0.0 and prev_balance is not None and balance is not None:
            delta = round(balance - prev_balance, 2)
            if delta > 0:
                credit = delta
            elif delta < 0:
                debit = abs(delta)

        prev_balance = balance

        results.append({
            "date":                     row.get("date", ""),
            "transaction_code":         row.get("transaction_code", ""),
            "description":              description,
            "party_name":               extract_bank_rakyat_party_name(description),
            "debit":                    round(float(debit  or 0.0), 2),
            "credit":                   round(float(credit or 0.0), 2),
            "balance":                  balance,
            "page":                     row.get("page"),
            "seq":                      row["_statement_order"],
            "opening_balance":          opening,
            "statement_total_debit":    summary.get("total_debit"),
            "statement_total_credit":   summary.get("total_credit"),
            "statement_closing_balance":summary.get("closing"),
            "bank":                     "Bank Rakyat",
            "source_file":              source_filename,
        })

    return results


# ── Quick smoke-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "statement.pdf"
    txns = parse_bank_rakyat(pdf_path, source_filename=pdf_path)

    print(f"\n{'=' * 72}")
    print(f"  Extracted {len(txns)} rows from: {pdf_path}")
    print(f"{'=' * 72}\n")

    for row in txns:
        print(
            f"  {row['date']}  [{row.get('transaction_code', ''):>5}]"
            f"  Dr:{row['debit']:>12.2f}"
            f"  Cr:{row['credit']:>12.2f}"
            f"  Bal:{row['balance']:>14.2f}"
        )
        print(f"    DESC: {row['description']}")

    print(f"\n{'=' * 72}")
