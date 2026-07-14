import re
import fitz
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core_utils import (
    display_transaction_date,
    normalize_text,
    parse_any_date,
    safe_float,
    signed_amount_from_record,
)
from party_utils import (
    apply_party_aliasing,
    build_transactions_by_party,
    deduplicate_counterparty_names,
    looks_like_suspicious_short_party,
)

# -----------------------------
# Regex patterns
# -----------------------------
DATE_DMY_SLASH_RE = re.compile(r"^(?P<d>\d{2})/(?P<m>\d{2})(?:/(?P<y>\d{2,4}))?$")
DATE_DMY_DASH_RE  = re.compile(r"^(?P<d>\d{2})-(?P<m>\d{2})(?:-(?P<y>\d{2,4}))?$")
STATEMENT_DATE_RE = re.compile(r"STATEMENT\s+DATE\s*:?\s*(\d{2})/(\d{2})/(\d{2,4})", re.I)

# Amount tokens usually look like: 1,630.00-  or  9,576.40+
MONEY_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}[+-]?$")
BALANCE_CENTS_ONLY_RE = re.compile(r"^\.\d{2}[+-]?$")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

FOOTER_KEYWORDS = (
    "ENDING BALANCE",
    "LEDGER BALANCE",
    "TOTAL DEBIT",
    "TOTAL CREDIT",
    "TOTAL DEBITS",
    "TOTAL CREDITS",
    "END OF STATEMENT",
    "PROFIT OUTSTANDING",
    "BAKI LEGAR",
    "BAKI AKHIR",
    "MUKA/",
    "PAGE",
    "NOMBOR AKAUN",
    "NOT PROTECTED BY PIDM",
    "PLEASE BE REMINDED",
    "NOTICE:",
    "NOTIS",
)

TRANSACTION_SECTION_KEYWORDS = (
    "ACCOUNT TRANSACTIONS",
    "TRANSACTION DESCRIPTION",
    "BUTIR URUSNIAGA",
    "ENTRY DATE",
    "TARIKH MASUK",
)

CONTINUATION_BLOCKLIST = (
    "BAKI LEGAR",
    "BAKI AKHIR",
    "ENDING BALANCE",
    "LEDGER BALANCE",
    "NOT PROTECTED BY PIDM",
    "PLEASE NOTIFY",
    "PROTECTED BY PIDM",
    "OVERDRAWN BALANCES",
)

MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

PARTY_PREFIX_RE = re.compile(
    r"^(?:TRANSFER\s+(?:TO|FR)\s+A/C|DUITNOW\s+TO\s+A/C|IBG\s+TO\s+A/C)\s+",
    re.I,
)
PARTY_SPLIT_RE = re.compile(
    r"\b(?:REFUND|PAYMENT|PAY|TRANSFER|FUND|MBB|CT)\b",
    re.I,
)
PARTY_STOP_WORDS = {
    "REFUND", "PAYMENT", "PAY", "TRANSFER", "FUND", "MBB", "CT",
    "ADVANCE", "BAS", "HST", "REPAYMENT", "TRAIN",
    "ENCIK", "CIK",
}

SPECIAL_PARTY_PATTERNS = [
    (re.compile(r"\bCDM\s+CASH\s+DEPOSIT\b", re.I), "CASH DEPOSIT"),
    (re.compile(r"\bCASH\s+DEPOSIT\b", re.I), "CASH DEPOSIT"),
    (re.compile(r"\bCASH\s+WITHDRAWAL\b", re.I), "CASH WITHDRAWAL"),
]

PARTY_CAPTURE_PATTERNS = [
    re.compile(
        r"\bINTER-BANK\s+PAYMENT\s+INTO\s+A/C\b\s+(?P<party>[A-Z][A-Z\s]+?\b(?:BIN|BINTI|BT|A/L|A/P)\b\s+[A-Z]+)\b",
        re.I,
    ),
    re.compile(
        r"\bCMS\s*-\s*CR\s+PYMT\s+MARS\b.*?\b(?P<party>AEON\s+CREDIT\s+SERVICE)\b",
        re.I,
    ),
    re.compile(
        r"\bCMS\s*-\s*CR\s+PYMT\s+MARS\b\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"\bCREDIT\s+INWARD\s+RENTAS\b\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"\bESI\s+PAYMENT\s+DEBIT\b\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        # Only treat the token after A/C as an account reference when it contains digits.
        # This preserves names like "EPF DPE" in descriptions such as:
        # "PAYMENT FR A/C EPF DPE * 000000..."
        r"\bPAYMENT\s+FR\s+A/C\b(?:\s+(?=[A-Z0-9]*\d)[A-Z0-9]+)?\s*\*?\s*(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"\bELECTRONIC\s+REMITTANCE\s*-\s*GIR\b\s+(?P<party>.+)$",
        re.I,
    ),
    re.compile(
        r"\bA/C\b\s+(?P<party>.+)$",
        re.I,
    ),
]

PARTY_TRAILING_NOISE_RE = re.compile(
    r"\b(?:OR\s+CALL\s+US\s+AT|REF(?:ERENCE)?|ID|NO|INV(?:OICE)?|BILL)\b.*$",
    re.I,
)
PARTY_TRAILING_CHANNEL_RE = re.compile(
    r"\bIBG\b(?:\s+(?:TRANSACTION|THROUGH)(?:\b.*)?)?$",
    re.I,
)
PARTY_TRAILING_REF_RE = re.compile(
    r"(?:\s+|[-./])\d{6,}(?:[-./]\d+)*\s*$",
    re.I,
)
PARTY_LEADING_LABEL_RE = re.compile(
    r"^(?:TM\s+)?(?P<party>UNIFI)\b",
    re.I,
)
PARTY_ABBREVIATION_NORMALIZATIONS = {
    "ENTERPR": "ENTERPRISE",
}


def _norm_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _extract_payment_fr_a_c_party(desc: str) -> str:
    """Handle PAYMENT FR A/C rows where either side of '*' may be the real party."""
    match = re.search(r"\bPAYMENT\s+FR\s+A/C\b\s+(?P<body>.+)$", desc, re.I)
    if not match:
        return ""

    body = normalize_text(match.group("body"))
    if not body:
        return ""

    if "*" not in body:
        return body

    left_side, right_side = [normalize_text(part) for part in body.split("*", 1)]
    if not left_side and not right_side:
        return ""
    if not right_side:
        return left_side

    left_tokens = left_side.split()
    if left_tokens:
        first_left = left_tokens[0]
        remaining_left = " ".join(left_tokens[1:]).strip()
        if re.search(r"[_\d]", first_left) and remaining_left:
            return remaining_left

    # If the left side looks like a payment reference (mixed digits/underscore),
    # prefer the right side as the actual counterparty.
    if re.search(r"[_\d]", left_side):
        return right_side

    # Otherwise the party usually appears before the asterisk and the right side
    # is only a trailing reference number.
    return left_side


def extract_maybank_party_name(description: str) -> str:
    """Extract and standardize the counterparty name from a Maybank transaction description."""
    desc = normalize_text(description).upper()
    if not desc:
        return "UNKNOWN"

    for pattern, label in SPECIAL_PARTY_PATTERNS:
        if pattern.search(desc):
            return label

    party = _extract_payment_fr_a_c_party(desc)
    if not party:
        for pattern in PARTY_CAPTURE_PATTERNS:
            match = pattern.search(desc)
            if match:
                party = normalize_text(match.group("party"))
                break

    if not party:
        return "UNKNOWN"

    party = PARTY_PREFIX_RE.sub("", party)
    leading_label_match = PARTY_LEADING_LABEL_RE.match(party)
    if leading_label_match:
        party = leading_label_match.group("party")
    party = party.split("*", 1)[0]
    party = PARTY_SPLIT_RE.split(party, maxsplit=1)[0]
    party = PARTY_TRAILING_NOISE_RE.sub("", party)
    party = PARTY_TRAILING_CHANNEL_RE.sub("", party)
    party = PARTY_TRAILING_REF_RE.sub("", party)
    party = re.sub(r"[^A-Z0-9/&()\s]", " ", party)
    party = normalize_text(party)

    raw_tokens = party.split()
    tokens = []
    for index, token in enumerate(raw_tokens):
        if token in PARTY_STOP_WORDS:
            continue
        if token.isdigit():
            continue
        if any(ch.isdigit() for ch in token):
            alpha_count = sum(ch.isalpha() for ch in token)
            digit_count = sum(ch.isdigit() for ch in token)
            if alpha_count == 0:
                continue
            if alpha_count <= 2 and digit_count >= 3:
                continue
        if re.fullmatch(r"[A-Z]\d+", token):
            continue
        if len(token) == 1:
            prev_token = raw_tokens[index - 1] if index > 0 else ""
            next_token = raw_tokens[index + 1] if index + 1 < len(raw_tokens) else ""
            prev_has_alpha = prev_token.isalpha()
            next_has_alpha = next_token.isalpha()
            is_leading_brand_initial = index == 0 and next_has_alpha and len(raw_tokens) >= 2
            is_ampersand_initial = prev_token == "&" or next_token == "&"
            is_name_suffix_initial = prev_token in {"BIN", "BINTI", "BT", "A/L", "A/P"}
            if (
                not (prev_has_alpha and next_has_alpha)
                and not is_leading_brand_initial
                and not is_ampersand_initial
                and not is_name_suffix_initial
            ):
                continue
        if token == "&":
            prev_token = raw_tokens[index - 1] if index > 0 else ""
            next_token = raw_tokens[index + 1] if index + 1 < len(raw_tokens) else ""
            if not (prev_token.isalpha() and next_token.isalpha()):
                continue
        if token in {"SND", "SD"}:
            token = "SDN"
        if token in {"BH", "BDH"} and any(existing == "SDN" for existing in tokens):
            token = "BHD"
        elif (
            token == "B"
            and any(existing == "SDN" for existing in tokens)
            and index == len(raw_tokens) - 1
        ):
            token = "BHD"
        token = PARTY_ABBREVIATION_NORMALIZATIONS.get(token, token)
        tokens.append(token)

    if "SDN" in tokens and "BHD" not in tokens:
        tokens.append("BHD")

    if len(tokens) >= 2:
        return " ".join(tokens[: min(5, len(tokens))])
    if len(tokens) == 1:
        return tokens[0]
    return "UNKNOWN"


def annotate_maybank_counterparties(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach Maybank counterparty aliases used by the shared ledger pipeline."""
    raw_names = []
    for row in rows or []:
        raw = (
            row.get("counterparty_name_raw")
            or row.get("counterparty_name")
            or row.get("party_name")
            or extract_maybank_party_name(row.get("description", ""))
            or "UNKNOWN"
        )
        raw = normalize_text(raw).upper() or "UNKNOWN"
        row["counterparty_name_raw"] = raw
        raw_names.append(raw)

    clean_names = deduplicate_counterparty_names(raw_names)
    for row, clean_name in zip(rows or [], clean_names):
        clean_name = normalize_text(clean_name).upper() or "UNKNOWN"
        row["counterparty_name_clean"] = clean_name
        row["counterparty_name"] = clean_name
        row["party_name"] = clean_name

    return rows


def _resolve_group_party_name(row: Any) -> str:
    candidate = normalize_text(row.get("party_name"))

    if candidate and not looks_like_suspicious_short_party(candidate):
        return candidate

    if candidate:
        return candidate

    return extract_maybank_party_name(normalize_text(row.get("description")))


def build_maybank_party_export_payload(rows: Any) -> Dict[str, Any]:
    """Build a JSON-friendly party export grouped by Maybank regex-cleaned party names."""
    try:
        import pandas as pd
    except Exception:
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "group_count": 0,
            "groups": [],
            "export_status": "unavailable",
            "export_message": "Pandas is unavailable. Party export could not be built.",
        }

    if not isinstance(rows, pd.DataFrame) or rows.empty:
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "group_count": 0,
            "groups": [],
            "export_status": "empty",
            "export_message": "No transactions available for party export.",
        }

    party_df = rows.copy()
    marker_mask = pd.Series(False, index=party_df.index)
    for marker_column in ("is_balance_marker", "is_statement_balance"):
        if marker_column in party_df.columns:
            marker_mask = marker_mask | (party_df[marker_column] == True)
    if marker_mask.any():
        party_df = party_df[~marker_mask].copy()
    if "party_name" in party_df.columns:
        party_df["regex_party"] = party_df.apply(_resolve_group_party_name, axis=1)
    else:
        party_df["regex_party"] = party_df["description"].apply(extract_maybank_party_name)

    party_df["regex_party"] = party_df["regex_party"].map(normalize_text).replace("", "UNKNOWN")
    party_df["final_party"] = apply_party_aliasing(party_df["regex_party"])
    party_df["signed_amount_value"] = party_df.apply(signed_amount_from_record, axis=1)
    party_df = party_df[party_df["signed_amount_value"].notna()].copy()
    if party_df.empty:
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "group_count": 0,
            "groups": [],
            "export_status": "empty",
            "export_message": "No valid transactions available for party export.",
        }

    if "credit" not in party_df.columns:
        party_df["credit"] = 0.0
    if "debit" not in party_df.columns:
        party_df["debit"] = 0.0
    if "balance" not in party_df.columns:
        party_df["balance"] = None
    if "source_file" not in party_df.columns:
        party_df["source_file"] = ""
    if "bank" not in party_df.columns:
        party_df["bank"] = ""

    party_df["credit"] = pd.to_numeric(party_df["credit"], errors="coerce").fillna(0.0)
    party_df["debit"] = pd.to_numeric(party_df["debit"], errors="coerce").fillna(0.0)
    party_df["date_sort_key"] = party_df["date"].apply(parse_any_date)
    party_df["date_sort_key"] = party_df["date_sort_key"].apply(
        lambda x: pd.Timestamp(x) if x is not None else pd.Timestamp.max
    )
    party_df = party_df.sort_values(["final_party", "date_sort_key", "description"]).reset_index(drop=True)

    groups: List[Dict[str, Any]] = []
    for party, group in party_df.groupby("final_party", dropna=False):
        ordered_group = group.sort_values(["date_sort_key", "description"], ascending=[False, True], kind="stable")
        transactions = []
        for _, row in ordered_group.iterrows():
            transactions.append(
                {
                    "date": display_transaction_date(row.get("date")),
                    "raw_date": normalize_text(row.get("date")),
                    "description": normalize_text(row.get("description")),
                    "amount": round(float(row.get("signed_amount_value") or 0.0), 2),
                    "credit": round(float(row.get("credit") or 0.0), 2),
                    "debit": round(float(row.get("debit") or 0.0), 2),
                    "balance": None if row.get("balance") in {None, ""} else safe_float(row.get("balance")),
                    "source_file": normalize_text(row.get("source_file")),
                    "bank": normalize_text(row.get("bank")),
                }
            )

        sample_descriptions = []
        for value in ordered_group["description"].dropna().astype(str).tolist():
            cleaned_value = normalize_text(value)
            if cleaned_value and cleaned_value not in sample_descriptions:
                sample_descriptions.append(cleaned_value)
            if len(sample_descriptions) >= 3:
                break

        groups.append(
            {
                "regex_party": str(party or "UNKNOWN"),
                "final_party": str(party or "UNKNOWN"),
                "category": "UNREVIEWED",
                "transaction_count": int(len(ordered_group)),
                "total_credit": round(float(ordered_group["credit"].sum()), 2),
                "total_debit": round(float(ordered_group["debit"].sum()), 2),
                "merged_from": sorted(
                    {
                        normalize_text(value)
                        for value in ordered_group["regex_party"].dropna().astype(str).tolist()
                        if normalize_text(value)
                    }
                ),
                "sample_descriptions": sample_descriptions,
                "transactions": transactions,
            }
        )

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "group_count": len(groups),
        "groups": groups,
        "export_status": "success",
        "export_message": "Regex-grouped party export generated.",
    }


def build_transactions_by_maybank_party(df: Any) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper. Prefer party_utils.build_transactions_by_party()."""
    return build_transactions_by_party(df, fallback_party_extractor=extract_maybank_party_name)


def build_maybank_party_tables_from_uploaded_json(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        import pandas as pd
    except Exception:
        return []

    if not isinstance(payload, dict):
        return []

    groups = payload.get("groups")
    if not isinstance(groups, list):
        return []

    party_tables: List[Dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue

        party_name = normalize_text(group.get("final_party") or group.get("regex_party") or "UNKNOWN").upper()
        transactions = group.get("transactions") or []
        if not isinstance(transactions, list):
            transactions = []

        display_rows = []
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            credit_value = pd.to_numeric(pd.Series([tx.get("credit", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
            debit_value = pd.to_numeric(pd.Series([tx.get("debit", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
            if credit_value > 0:
                amount_display = f"{float(abs(credit_value)):+,.2f}"
            elif debit_value > 0:
                amount_display = f"{-float(abs(debit_value)):+,.2f}"
            else:
                amount_value = tx.get("amount")
                try:
                    amount_display = f"{float(amount_value):+,.2f}"
                except Exception:
                    amount_display = normalize_text(amount_value)

            display_rows.append(
                {
                    "date": normalize_text(tx.get("date") or tx.get("raw_date")),
                    "description": normalize_text(tx.get("description")),
                    "amount": amount_display,
                    "source_file": normalize_text(tx.get("source_file")),
                }
            )

        total_credit = pd.to_numeric(pd.Series([group.get("total_credit", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
        total_debit = pd.to_numeric(pd.Series([group.get("total_debit", 0.0)]), errors="coerce").fillna(0.0).iloc[0]
        transaction_count = int(group.get("transaction_count") or len(display_rows))

        party_tables.append(
            {
                "party": party_name or "UNKNOWN",
                "count": transaction_count,
                "total_credit": round(float(total_credit), 2),
                "total_debit": round(float(total_debit), 2),
                "sort_volume": round(float(total_credit) + float(total_debit), 2),
                "table": pd.DataFrame(display_rows, columns=["date", "description", "amount", "source_file"]),
            }
        )

    return party_tables


def _open_doc(inp: Any) -> fitz.Document:
    """Open a PDF input robustly for Streamlit, bytes, file-like, or path."""
    if isinstance(inp, (bytes, bytearray)):
        return fitz.open(stream=bytes(inp), filetype="pdf")

    # Streamlit UploadedFile often supports getvalue()
    if hasattr(inp, "getvalue"):
        try:
            b = inp.getvalue()
            return fitz.open(stream=b, filetype="pdf")
        except Exception:
            pass

    # file-like object
    if hasattr(inp, "read"):
        try:
            pos = inp.tell()
        except Exception:
            pos = None

        b = inp.read()

        if pos is not None:
            try:
                inp.seek(pos)
            except Exception:
                pass

        return fitz.open(stream=b, filetype="pdf")

    # path string
    return fitz.open(inp)


def _parse_year_and_bank(doc: fitz.Document) -> Tuple[str, int]:
    bank = "Maybank"
    year = None

    for i in range(min(2, doc.page_count)):
        txt = (doc[i].get_text("text") or "").upper()

        if "MAYBANK ISLAMIC" in txt:
            bank = "Maybank Islamic"
        elif "MAYBANK" in txt:
            bank = "Maybank"

        m = STATEMENT_DATE_RE.search(txt)
        if m:
            y = m.group(3)
            year = (2000 + int(y)) if len(y) == 2 else int(y)
            break

    if year is None:
        year = datetime.now().year

    return bank, year


def _is_footer_or_header(line_text: str) -> bool:
    up = line_text.upper()
    return any(k in up for k in FOOTER_KEYWORDS)


def _is_transaction_section_header(line_text: str) -> bool:
    up = line_text.upper()
    return any(k in up for k in TRANSACTION_SECTION_KEYWORDS)


def _should_skip_continuation(line_text: str) -> bool:
    up = _norm_spaces(line_text).upper()
    if not up:
        return True
    if CJK_RE.search(line_text):
        return True
    return any(k in up for k in CONTINUATION_BLOCKLIST)


def _money_token_value(tok: str) -> Tuple[float, Optional[str]]:
    """
    Returns (value, sign) where sign is '+' or '-' if present at end.
    """
    s = tok.strip()
    sign = None

    if s.endswith("+"):
        sign = "+"
        s = s[:-1]
    elif s.endswith("-"):
        sign = "-"
        s = s[:-1]

    s = s.replace(",", "")
    return float(s), sign


def _parse_date_token(token: str, default_year: int) -> Optional[str]:
    """
    Supports:
      DD/MM
      DD/MM/YY
      DD/MM/YYYY
      DD-MM
      DD-MM-YY
      DD-MM-YYYY
    """
    t = token.strip().upper()

    m = DATE_DMY_SLASH_RE.match(t)
    if m:
        d, mo, y = m.group("d"), m.group("m"), m.group("y")
        yy = default_year
        if y:
            yy = int(y)
            if yy < 100:
                yy = 2000 + yy
        return f"{yy:04d}-{int(mo):02d}-{int(d):02d}"

    m = DATE_DMY_DASH_RE.match(t)
    if m:
        d, mo, y = m.group("d"), m.group("m"), m.group("y")
        yy = default_year
        if y:
            yy = int(y)
            if yy < 100:
                yy = 2000 + yy
        return f"{yy:04d}-{int(mo):02d}-{int(d):02d}"

    return None


def _parse_split_date_tokens(items: List[dict]) -> Optional[str]:
    """
    Supports:
      DD MON YYYY   (e.g., 2 FEB 2025 / 02 FEB 2025)
    Only used if the PDF actually emits those tokens separately.
    """
    if len(items) < 3:
        return None

    d = items[0]["text"]
    mon = items[1]["text"]
    y = items[2]["text"]

    if not d.isdigit() or not y.isdigit():
        return None

    mon_u = mon.upper()
    if mon_u not in MONTH_MAP:
        return None

    return f"{int(y):04d}-{int(MONTH_MAP[mon_u]):02d}-{int(d):02d}"


def _cluster_lines(word_items: List[dict], y_tol: float = 3.0) -> List[Tuple[float, List[dict]]]:
    """
    Cluster words into 'visual lines' using y proximity.
    This is critical for Maybank PDFs because date/desc and amount/balance
    can be slightly misaligned in y.
    """
    if not word_items:
        return []

    word_items.sort(key=lambda r: (r["y"], r["x0"]))
    clusters: List[dict] = []

    for it in word_items:
        placed = False
        for c in clusters:
            if abs(it["y"] - c["y"]) <= y_tol:
                c["items"].append(it)
                # update centroid
                c["y"] = (c["y"] * (len(c["items"]) - 1) + it["y"]) / len(c["items"])
                placed = True
                break
        if not placed:
            clusters.append({"y": it["y"], "items": [it]})

    clusters.sort(key=lambda c: c["y"])

    out: List[Tuple[float, List[dict]]] = []
    for c in clusters:
        c["items"].sort(key=lambda r: r["x0"])
        out.append((c["y"], c["items"]))

    return out


def parse_transactions_maybank(pdf_input: Any, source_filename: str = "") -> List[Dict]:
    """
    Maybank (Conventional + Islamic) statement parser.

    Output schema matches other banks:
      date (YYYY-MM-DD), description, debit, credit, balance, page, bank, source_file
    """
    doc = _open_doc(pdf_input)
    bank_name, default_year = _parse_year_and_bank(doc)

    txs: List[Dict] = []
    prev_balance: Optional[float] = None

    # Context: Maybank sometimes omits the date for subsequent rows with the same date
    carry_date_iso: Optional[str] = None

    # Context: a date-only line may appear (rare) followed by the actual data row below
    pending_date_iso: Optional[str] = None
    pending_date_x_end: Optional[float] = None

    # For multi-line description continuation
    last_tx: Optional[Dict] = None
    last_desc_left: Optional[float] = None
    last_money_left: Optional[float] = None

    # Thresholds (tuned for Maybank PDFs)
    DATE_COL_RIGHT_FALLBACK = 85.0  # if date cell is blank, description starts after this

    def append_desc(line_items: List[dict]):
        nonlocal last_tx
        if not last_tx or last_desc_left is None or last_money_left is None:
            return

        line_text = _norm_spaces(" ".join(it["text"] for it in line_items))
        if _should_skip_continuation(line_text):
            return

        parts = []
        for it in line_items:
            if it["is_money"]:
                continue
            if it["x0"] < last_desc_left:
                continue
            if it["x0"] >= last_money_left:
                continue
            parts.append(it["text"])

        if parts:
            last_tx["description"] = _norm_spaces(
                (last_tx.get("description", "") + " " + " ".join(parts)).strip()
            )

    try:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            words = page.get_text("words")
            in_transaction_section = False

            word_items = []
            for w in words:
                txt = str(w[4]).strip()
                if not txt:
                    continue
                word_items.append({"y": float(w[1]), "x0": float(w[0]), "text": txt})

            for _, line_items in _cluster_lines(word_items, y_tol=3.0):
                line_text = _norm_spaces(" ".join(i["text"] for i in line_items))
                if not line_text:
                    continue

                if _is_transaction_section_header(line_text):
                    in_transaction_section = True
                    continue

                if _is_footer_or_header(line_text):
                    pending_date_iso = None
                    pending_date_x_end = None
                    in_transaction_section = False
                    continue

                # Mark money tokens + find money column start
                money_positions = []
                for it in line_items:
                    it["is_money"] = bool(MONEY_RE.match(it["text"]))
                    if it["is_money"]:
                        money_positions.append(it["x0"])

                money_left = min(money_positions) if money_positions else None

                # Detect date token at start (DD/MM...) OR split date tokens (DD MON YYYY)
                date_iso: Optional[str] = None
                date_x_end: Optional[float] = None
                start_after_date = 0

                split = _parse_split_date_tokens(line_items[:3]) if len(line_items) >= 3 else None
                if split:
                    date_iso = split
                    date_x_end = line_items[2]["x0"] + 20.0
                    start_after_date = 3
                else:
                    if line_items:
                        maybe = _parse_date_token(line_items[0]["text"], default_year)
                        if maybe:
                            date_iso = maybe
                            date_x_end = line_items[0]["x0"] + 20.0
                            start_after_date = 1

                if date_iso:
                    carry_date_iso = date_iso  # always update date context

                # Date-only line (no money) -> pending date for the next line
                if date_iso and money_left is None:
                    # Keep only if it is "mostly just a date"
                    if len(line_items) <= start_after_date + 3:
                        pending_date_iso = date_iso
                        pending_date_x_end = date_x_end
                        last_tx = None
                        continue

                # Determine date for a transaction row
                effective_date = date_iso or pending_date_iso

                # Fix: blank-date rows (same date as previous transaction),
                # where date column is empty but amount/balance exist.
                if effective_date is None and money_left is not None and carry_date_iso:
                    first_x = line_items[0]["x0"] if line_items else 0.0
                    # If the first token is far right, date cell is likely blank
                    if first_x > 70.0:
                        effective_date = carry_date_iso
                        date_x_end = DATE_COL_RIGHT_FALLBACK

                # Transaction row
                if effective_date and money_left is not None:
                    money_tokens = [it["text"] for it in line_items if it["is_money"]]
                    if len(money_tokens) >= 2 or (len(money_tokens) == 1 and prev_balance is not None):
                        bal_val: Optional[float] = None

                        # Normal case: balance is the last money token.
                        if len(money_tokens) >= 2:
                            bal_val, _ = _money_token_value(money_tokens[-1])

                        # Choose transaction amount: prefer last signed token before balance
                        amt_val: Optional[float] = None
                        amt_sign: Optional[str] = None

                        if len(money_tokens) >= 2:
                            for t in reversed(money_tokens[:-1]):
                                v, sgn = _money_token_value(t)
                                if sgn in ("+", "-"):
                                    amt_val, amt_sign = v, sgn
                                    break

                            if amt_val is None:
                                v, sgn = _money_token_value(money_tokens[-2])
                                amt_val, amt_sign = v, sgn
                        else:
                            # OCR fallback: some lines lose integer digits on the balance token
                            # (e.g. ".02"), leaving only one parseable money token.
                            amt_val, amt_sign = _money_token_value(money_tokens[0])

                        # Description tokens by x-range
                        desc_left = date_x_end if date_x_end is not None else pending_date_x_end
                        if desc_left is None:
                            desc_left = DATE_COL_RIGHT_FALLBACK

                        desc_parts = []
                        for it in line_items:
                            if it["is_money"]:
                                continue
                            if it["x0"] < desc_left:
                                continue
                            if it["x0"] >= money_left:
                                continue
                            # skip the actual date tokens if split-date
                            if date_iso and it in line_items[:start_after_date]:
                                continue
                            desc_parts.append(it["text"])

                        description = _norm_spaces(" ".join(desc_parts))

                        debit = 0.0
                        credit = 0.0

                        # Primary rule: sign on the transaction amount
                        if amt_sign == "+":
                            credit = float(amt_val)
                        elif amt_sign == "-":
                            debit = float(amt_val)
                        else:
                            # Fallback: balance delta if sign missing
                            if prev_balance is not None and bal_val is not None:
                                delta = round(bal_val - prev_balance, 2)
                                if delta > 0:
                                    credit = abs(delta)
                                elif delta < 0:
                                    debit = abs(delta)
                            else:
                                debit = float(amt_val)

                        # Infer missing balance for one-money-token rows.
                        if bal_val is None and prev_balance is not None:
                            if amt_sign == "+":
                                bal_val = round(prev_balance + float(amt_val), 2)
                            elif amt_sign == "-":
                                bal_val = round(prev_balance - float(amt_val), 2)

                            # If a cents-only token exists in the balance column, enforce cents.
                            for it in reversed(line_items):
                                if it["is_money"]:
                                    continue
                                if it["x0"] <= money_left:
                                    continue
                                if BALANCE_CENTS_ONLY_RE.match(it["text"]):
                                    cents = int(it["text"][-2:])
                                    if bal_val is not None:
                                        bal_val = round(int(bal_val) + (cents / 100.0), 2)
                                    break

                        if bal_val is None:
                            continue

                        # Conservative OCR correction using balance delta
                        # (Fix digit swaps / missing decimals, without breaking correct values)
                        if prev_balance is not None:
                            delta = round(bal_val - prev_balance, 2)
                            expected = abs(delta)
                            parsed = credit if credit > 0 else debit
                            if expected > 0 and parsed > 0 and abs(expected - parsed) <= 500:
                                if delta > 0:
                                    credit = expected
                                    debit = 0.0
                                elif delta < 0:
                                    debit = expected
                                    credit = 0.0

                        tx = {
                            "date": effective_date,
                            "description": description,
                            "debit": round(float(debit), 2),
                            "credit": round(float(credit), 2),
                            "balance": round(float(bal_val), 2),
                            "page": page_index + 1,
                            "bank": bank_name,
                            "source_file": source_filename,
                        }
                        txs.append(tx)

                        # Continuation boundaries
                        last_tx = tx
                        last_desc_left = desc_left
                        last_money_left = money_left

                        prev_balance = bal_val

                        # Clear pending date after use
                        if date_iso is None and pending_date_iso is not None:
                            pending_date_iso = None
                            pending_date_x_end = None

                        continue

                # Description continuation lines
                if (
                    in_transaction_section
                    and last_tx is not None
                    and money_left is None
                    and date_iso is None
                ):
                    append_desc(line_items)

    finally:
        doc.close()

    # Dedupe exact duplicates only.
    # Important: some statements legitimately contain multiple transactions
    # with the same date/debit/credit/balance on the same page, so we must
    # keep rows when descriptions differ.
    seen = set()
    out: List[Dict] = []
    for t in txs:
        key = (
            t["date"],
            t["description"],
            t["debit"],
            t["credit"],
            t["balance"],
            t["page"],
            t["source_file"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(t)

    return annotate_maybank_counterparties(out)
