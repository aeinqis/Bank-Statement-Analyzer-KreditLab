# alliance.py
# Alliance Bank Malaysia Berhad statement parser
#
# Interface matches project convention:
#   parse_transactions_alliance(pdf, filename) -> List[dict]
# where pdf is a pdfplumber.PDF instance (from bytes_to_pdfplumber)

import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from core_utils import normalize_text, sanitize_transaction_description
from party_utils import deduplicate_counterparty_names


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
    "THE ITEMS AND BALANCES SHOWN ABOVE",
    "SEGALA BUTIRAN DAN BAKI AKAUN PENYATA DI ATAS DIANGGAP BETUL",
    "SEGALA BUTIRAN DAN BAKI AKAUN PENYATA DI ATAS",
    "ALLIANCE BANK MALAYSIA BERHAD",
)

ALLIANCE_NON_PARTY_PATTERNS = [
    re.compile(r"\bBEGINNING BALANCE\b", re.I),
    re.compile(r"\bENDING BALANCE\b", re.I),
    re.compile(r"\bACH INCLEARING-CHEQUE\b", re.I),
    re.compile(r"\bCA DR CHQ PRO FEE\b", re.I),
    re.compile(r"\bCA IMPORT DR\b", re.I),
    re.compile(r"\bNBPS IBG Dr CA\b", re.I),
    re.compile(r"\bODP\s+INT/CLF\s+PFT\b", re.I),
    re.compile(r"\bPART\s+PAYMENT\b", re.I),
]

ALLIANCE_PARTY_CAPTURE_PATTERNS = [
    re.compile(r"\bCR ADVICE\s*-\s*IBG\b\s+(?P<body>.+)$", re.I),
    re.compile(r"\bRENTAS\s+CA\s+CREDIT\b\s+(?P<body>.+)$", re.I),
    re.compile(r"\bDuitNow\s+CR\s+Trf\s+CA\b\s+(?P<body>.+)$", re.I),
    re.compile(r"\bInstant\s+Transfer\b\s+(?P<body>.+)$", re.I),
    re.compile(r"\bIB2G\s+FND\s+TRF\s+CA\s*-\s*CA\b\s+(?P<body>.+)$", re.I),
    re.compile(r"\bLOCAL\s+CHQ\s+DEP/MISC\b\s+(?P<body>.+)$", re.I),
]

ALLIANCE_DIRECT_PARTY_REGEXES = [
    re.compile(r"Transfer From \w+\s+([\w\s\(\)]+)", re.I),
    re.compile(r"([\w\s\(\)]+(?:SDN BHD|MARKETING|ELECTRICAL))", re.I),
    re.compile(r"SETTLED\s[A-Z0-9']{3,10}\sA/C\s+(.*)", re.I),
]

ALLIANCE_REFERENCE_TOKEN_RE = re.compile(r"^(?=.*\d)[A-Z0-9]+(?:[-/'().][A-Z0-9]+)*$", re.I)
ALLIANCE_TRAILING_NOISE_RE = re.compile(
    r"\b(?:INVOICE|INV|IV|FIN|NO|ACC|REPAYM|ISSUER)\b.*$",
    re.I,
)
ALLIANCE_LEGAL_SUFFIX_RE = re.compile(r"\b(?:SDN\.?\s*BHD\.?|S/B|SB|BHD)\b", re.I)
ALLIANCE_GENERIC_TOKENS = {
    "TRANSFER", "FROM", "TO", "ABMB", "FUND", "TRF", "PAYMENT", "BILL", "CREDITOR",
    "CR", "ADVICE", "IBG", "RENTAS", "CA", "CREDIT", "DR", "PYM", "LOCAL", "CHQ",
    "DEP/MISC", "DEP", "MISC", "INSTANT", "FOR", "AB", "THE", "ITEMS", "BALANCES",
    "SHOWN", "ABOVE", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUNE", "JUL",
    "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
}
ALLIANCE_MEMO_LINE_RE = re.compile(
    r"^(?:PAYMENT(?:\s+FOR)?|FUND\s+TRANSFER|TRANSFER\s+FROM\s+ABMB(?:\s+TO\s+[A-Z]+)?|PYM|BILL\s+PAYMENT|CREDITOR\s+PAYMENT|SETTLED\b.*|ACC\b.*|A/C\b.*|INV(?:OICE)?\b.*|IV\d.*)$",
    re.I,
)
ALLIANCE_REFERENCE_LINE_RE = re.compile(r"^(?:[A-Z0-9]{10,}|[A-Z]{1,4}\d{4,}.*|\d[\dA-Z\s'/-]*)$", re.I)
ALLIANCE_COMPANY_HINT_RE = re.compile(
    r"\b(?:SDN\.?\s*BHD\.?|S/B|SB|BERHAD|ENTERPRISE|TRADING|MARKETING|INDUSTRIES|TECHNOLOGIES|ELECTRICAL|ENGINEERING|LOGISTICS|SUPPLIES|SERVICE(?:S)?|HOLDINGS|CARRIER|CONCRETE|LIGHTING|HARDWARE)\b",
    re.I,
)
ALLIANCE_PERSON_LINE_RE = re.compile(r"^[A-Z]+(?:\s+[A-Z]+){1,4}$", re.I)
ALLIANCE_TRANSACTION_CATEGORY_PATTERNS = [
    ("CHEQUE Transaction", re.compile(r"\b(?:CHQ|CHEQUE)\b", re.I)),
]
ALLIANCE_CHEQUE_TRANSACTION_RE = re.compile(r"\b(?:CHQ|CHEQUE)\b", re.I)
ALLIANCE_BANK_ROUTE_TOKENS = {
    "AB", "ABB", "ABMB", "AMB", "CIMB", "HLB", "MBB", "MBS", "MBSB", "OCBC", "PBB", "RHB", "UOB", "SC", "AB",
}
ALLIANCE_SYSTEM_PARTY_PATTERNS = [
    re.compile(r"\bBNM\s+NULL\b", re.I),
    re.compile(r"\bODP\s+INT/CLF\s+PFT\b", re.I),
    re.compile(r"\bPART\s+PAYMENT\b", re.I),
]
ALLIANCE_LEGAL_CANDIDATE_NOISE_TOKENS = {"SETTLED", "A/C", "ACC", "INV", "INVOICE"}
ALLIANCE_MEMO_PREFIX_RE = re.compile(
    r"\b[A-Z]+-(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)'?\d{2}\b",
    re.I,
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\x00", " ")).strip()


def extract_alliance_transaction_category(description: str) -> str:
    """Map Alliance transaction descriptions to a bank-specific category."""
    desc = normalize_text(description).upper()
    if not desc:
        return "Other"

    for category, pattern in ALLIANCE_TRANSACTION_CATEGORY_PATTERNS:
        if pattern.search(desc):
            return category
    return "Other"


def _is_alliance_cheque_transaction(description: str, description_lines: Any = None) -> bool:
    lines = _prepare_alliance_description_lines(description, description_lines)
    if not lines:
        lines = [sanitize_transaction_description(description)]

    for line in lines:
        if ALLIANCE_CHEQUE_TRANSACTION_RE.search(normalize_text(line)):
            return True
    return False


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


def _cut_alliance_stop_text(text: str) -> str:
    cleaned = normalize_text(text)
    upper = cleaned.upper()
    cut_index = None
    for marker in _STOP_MARKERS:
        marker_index = upper.find(marker.upper())
        if marker_index >= 0 and (cut_index is None or marker_index < cut_index):
            cut_index = marker_index
    if cut_index is not None:
        cleaned = cleaned[:cut_index]
    return normalize_text(cleaned)


def _normalize_alliance_party_name(value: str) -> str:
    cleaned = normalize_text(value).upper()
    if not cleaned:
        return "UNKNOWN"

    cleaned = re.sub(r"[^A-Z0-9&()/.'\s-]", " ", cleaned)
    cleaned = normalize_text(cleaned)

    tokens = []
    for token in cleaned.split():
        token = token.strip()
        token_core = token.strip(".")
        if token_core in {"SND", "SD", "SDN"}:
            token = "SDN"
        elif token_core in {"BH", "BDH", "B", "BHD"} and any(existing == "SDN" for existing in tokens):
            token = "BHD"
        tokens.append(token)

    while tokens and re.fullmatch(r"\d+(?:[./-]\d+)*", tokens[0]):
        tokens.pop(0)

    while tokens and tokens[0] == "&":
        tokens.pop(0)
    while tokens and tokens[-1] == "&":
        tokens.pop()

    if len(tokens) % 2 == 0 and tokens[: len(tokens) // 2] == tokens[len(tokens) // 2 :]:
        tokens = tokens[: len(tokens) // 2]

    if "SDN" in tokens and "BHD" in tokens:
        sdn_index = tokens.index("SDN")
        prefix_tokens = tokens[:sdn_index]
        original_prefix_tokens = list(prefix_tokens)
        for size in range(min(4, len(prefix_tokens) // 2), 2, -1):
            anchor_tokens = prefix_tokens[:size]
            for start in range(1, len(prefix_tokens) - size + 1):
                if prefix_tokens[start : start + size] == anchor_tokens:
                    tokens = prefix_tokens[start:] + tokens[sdn_index:]
                    prefix_tokens = tokens[: tokens.index("SDN")]
                    break
            if prefix_tokens != original_prefix_tokens:
                sdn_index = tokens.index("SDN")
                break
        for size in range(min(4, len(prefix_tokens) - 1), 0, -1):
            suffix_tokens = prefix_tokens[-size:]
            earlier_tokens = prefix_tokens[:-size]
            if not earlier_tokens:
                continue
            for start in range(0, len(earlier_tokens) - size + 1):
                if earlier_tokens[start : start + size] == suffix_tokens:
                    tokens = suffix_tokens + tokens[sdn_index:]
                    prefix_tokens = suffix_tokens
                    break
            if prefix_tokens == suffix_tokens:
                break

    if "SDN" in tokens and "BHD" not in tokens:
        tokens.append("BHD")

    return " ".join(tokens).strip() or "UNKNOWN"


def _cleanup_alliance_party_candidate(value: str) -> str:
    source_text = normalize_text(value).upper()
    cleaned = source_text
    if not cleaned:
        return "UNKNOWN"

    cleaned = re.sub(r"\b[A-Z0-9]{2,12}\s+SETTLEMENT\b.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^PAYMENT\s+FOR\s+[A-Z0-9/'-]+\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^PAYMENT\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^BIL\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^AC\s+", "", cleaned, flags=re.I)
    cleaned = re.sub(
        r"^(?:CR\s+ADVICE\s*-\s*IBG|RENTAS\s+CA\s+CREDIT|DUITNOW\s+CR\s+TRF\s+CA|INSTANT\s+TRANSFER|IB2G\s+FND\s+TRF\s+CA\s*-\s*CA)\b",
        "",
        cleaned,
        flags=re.I,
    )
    raw_tokens = normalize_text(cleaned).split()
    filtered_tokens = []
    for index, token in enumerate(raw_tokens):
        if token in {"(", ")"}:
            continue
        prev_token = raw_tokens[index - 1] if index > 0 else ""
        next_token = raw_tokens[index + 1] if index + 1 < len(raw_tokens) else ""
        if (
            len(token) == 1
            and token.isalpha()
            and prev_token != "&"
            and next_token != "&"
        ):
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if token in ALLIANCE_GENERIC_TOKENS:
            continue
        if token in ALLIANCE_BANK_ROUTE_TOKENS:
            continue
        filtered_tokens.append(token)
    cleaned = normalize_text(" ".join(filtered_tokens))
    legal_name_matches = list(
        re.finditer(
            r"[A-Z][A-Z&()/.'-]*(?:\s+(?:&\s+)?[A-Z][A-Z&()/.'-]*){0,5}\s+SDN\.?\s*(?:BHD|B\.?\s*H\.?\s*D\.?|B\.)\.?",
            cleaned,
            re.I,
        )
    )
    if legal_name_matches:
        legal_candidates = [(normalize_text(match.group(0)), match.start(), match.end()) for match in legal_name_matches]
        compact_legal_matches = list(
            re.finditer(
                r"\(?[A-Z][A-Z&()/.'-]*\)?(?:\s+(?:&\s+)?\(?[A-Z][A-Z&()/.'-]*\)?){0,4}\s+SDN\.?\s*(?:BHD|B\.?\s*H\.?\s*D\.?|B\.)\.?",
                cleaned,
                re.I,
            )
        )
        legal_candidates.extend((normalize_text(match.group(0)), match.start(), match.end()) for match in compact_legal_matches)
        noise_suffix_matches = list(re.finditer(r"\b(?:INV(?:OICE)?|SETTLED|A/C|ACC)\b", cleaned, re.I))
        noise_suffix_matches.extend(ALLIANCE_MEMO_PREFIX_RE.finditer(cleaned))
        for noise_match in noise_suffix_matches:
            suffix = cleaned[noise_match.end() :]
            suffix_compact_matches = list(
                re.finditer(
                    r"\(?[A-Z][A-Z&()/.'-]*\)?(?:\s+(?:&\s+)?\(?[A-Z][A-Z&()/.'-]*\)?){0,4}\s+SDN\.?\s*(?:BHD|B\.?\s*H\.?\s*D\.?|B\.)\.?",
                    suffix,
                    re.I,
                )
            )
            legal_candidates.extend(
                (normalize_text(match.group(0)), noise_match.end() + match.start(), noise_match.end() + match.end())
                for match in suffix_compact_matches
            )

        def _legal_candidate_score(item: tuple[str, int, int]) -> tuple[int, int, int, int, int]:
            candidate, start_pos, end_pos = item
            tokens = candidate.split()
            upper_tokens = [token.upper().strip(".") for token in tokens]
            noise_penalty = sum(1 for token in upper_tokens if token in ALLIANCE_LEGAL_CANDIDATE_NOISE_TOKENS)
            legal_repeat_penalty = max(0, upper_tokens.count("SDN") - 1) + max(0, upper_tokens.count("BHD") - 1)
            leading_penalty = 1 if upper_tokens and upper_tokens[0] in (ALLIANCE_LEGAL_CANDIDATE_NOISE_TOKENS | {"SDN", "BHD"}) else 0
            return (noise_penalty + legal_repeat_penalty + leading_penalty, start_pos, len(tokens), -end_pos, -len(candidate))

        cleaned = min(legal_candidates, key=_legal_candidate_score)[0]
        leading_initial_match = re.match(r"^([A-Z])\s+(.+)$", cleaned)
        if leading_initial_match:
            initial = leading_initial_match.group(1)
            remainder = leading_initial_match.group(2)
            ampersand_prefix_match = re.search(
                rf"([A-Z]\s*&\s*){re.escape(initial)}\s+{re.escape(remainder)}",
                source_text,
                re.I,
            )
            if ampersand_prefix_match:
                prefix = normalize_text(ampersand_prefix_match.group(1))
                cleaned = f"{prefix} {cleaned}"

    return _normalize_alliance_party_name(cleaned)


def _looks_like_alliance_party_line(value: str, account_holder: str = "") -> bool:
    cleaned = normalize_text(value).upper()
    if not cleaned:
        return False
    if _is_weak_alliance_party_name(cleaned):
        return False
    if ALLIANCE_MEMO_LINE_RE.fullmatch(cleaned):
        return False
    if ALLIANCE_REFERENCE_LINE_RE.fullmatch(cleaned):
        return False

    stripped_self = _remove_account_holder_mentions(cleaned, account_holder)
    if not stripped_self:
        return False

    if ALLIANCE_COMPANY_HINT_RE.search(stripped_self):
        return True
    if ALLIANCE_PERSON_LINE_RE.fullmatch(stripped_self):
        return True

    alpha_count = sum(ch.isalpha() for ch in stripped_self)
    digit_count = sum(ch.isdigit() for ch in stripped_self)
    if alpha_count >= 4 and digit_count <= 2:
        return True
    return False


def _prepare_alliance_description_lines(description: str, description_lines: Any = None) -> List[str]:
    lines: List[str] = []
    if isinstance(description_lines, list):
        for item in description_lines:
            cleaned = _cut_alliance_stop_text(normalize_text(item))
            if cleaned:
                lines.append(cleaned)

    if lines:
        return lines

    cleaned_description = _cut_alliance_stop_text(sanitize_transaction_description(description))
    if cleaned_description:
        lines.append(cleaned_description)
    return lines


def _extract_alliance_bottom_up_party(
    description: str,
    description_lines: Any = None,
    account_holder: str = "",
) -> str:
    lines = _prepare_alliance_description_lines(description, description_lines)
    if not lines:
        return "UNKNOWN"

    surviving_lines: List[str] = []
    for line in lines:
        upper = normalize_text(line).upper()
        if not upper:
            continue
        if any(pattern.search(upper) for pattern in ALLIANCE_NON_PARTY_PATTERNS):
            continue
        if upper in {
            "CR ADVICE - IBG",
            "RENTAS CA CREDIT",
            "DUITNOW CR TRF CA",
            "INSTANT TRANSFER",
            "IB2G FND TRF CA - CA",
        }:
            continue
        if any(ch.isdigit() for ch in upper) and not re.search(r"[A-Z]{3,}", upper):
            continue
        if ALLIANCE_REFERENCE_LINE_RE.fullmatch(upper):
            continue
        surviving_lines.append(upper)

    if not surviving_lines:
        return "UNKNOWN"

    for line in reversed(surviving_lines):
        candidate = _cleanup_alliance_party_candidate(line)
        if _looks_like_alliance_party_line(candidate, account_holder=account_holder):
            return candidate

    if len(surviving_lines) >= 2:
        candidate = _cleanup_alliance_party_candidate(surviving_lines[-2])
        if not _is_weak_alliance_party_name(candidate):
            return candidate

    candidate = _cleanup_alliance_party_candidate(surviving_lines[-1])
    if not _is_weak_alliance_party_name(candidate):
        return candidate

    return "UNKNOWN"


def _is_weak_alliance_party_name(value: str) -> bool:
    cleaned = _cleanup_alliance_party_candidate(value)
    if cleaned in {"UNKNOWN", "SDN BHD", "BHD", "SDN", "SB", "S/B"}:
        return True
    if any(pattern.search(cleaned) for pattern in ALLIANCE_SYSTEM_PARTY_PATTERNS):
        return True
    if cleaned.startswith("TRANSFER FROM ABMB"):
        return True
    return False


def _account_holder_variants(account_holder: str) -> List[str]:
    base = _normalize_alliance_party_name(account_holder)
    if not base or base == "UNKNOWN":
        return []

    variants = {base}
    stripped = re.sub(r"\bSDN\s+BHD\b", "", base).strip()
    if stripped:
        variants.add(stripped)
    return sorted(variants, key=len, reverse=True)


def _strip_alliance_reference_prefix(body: str) -> str:
    tokens = body.split()
    while tokens:
        token = tokens[0]
        if token.upper() in ALLIANCE_GENERIC_TOKENS:
            tokens.pop(0)
            continue
        if any(ch.isdigit() for ch in token) or ALLIANCE_REFERENCE_TOKEN_RE.fullmatch(token):
            tokens.pop(0)
            continue
        break
    return normalize_text(" ".join(tokens))


def _remove_account_holder_mentions(text: str, account_holder: str) -> str:
    cleaned = normalize_text(text).upper()
    for variant in _account_holder_variants(account_holder):
        cleaned = re.sub(rf"\b{re.escape(variant)}\b", " ", cleaned, flags=re.I)
    return normalize_text(cleaned)


def _extract_alliance_legal_name(text: str, account_holder: str = "") -> str:
    cleaned = _remove_account_holder_mentions(text, account_holder)
    matches = list(
        re.finditer(
            r"\(?[A-Z][A-Z&()/.'-]*\)?(?:\s+\(?[A-Z][A-Z&()/.'-]*\)?){1,7}\s+(?:SDN\.?\s*BHD\.?|S/B|SB|BHD)",
            cleaned,
            re.I,
        )
    )
    if not matches:
        return "UNKNOWN"

    valid_candidates = []
    for match in matches:
        candidate = _strip_alliance_reference_prefix(match.group(0))
        candidate = _cleanup_alliance_party_candidate(candidate)
        if _is_weak_alliance_party_name(candidate):
            continue
        valid_candidates.append(candidate)

    if not valid_candidates:
        return "UNKNOWN"

    return valid_candidates[-1]


def _extract_alliance_direct_regex_party(text: str, account_holder: str = "") -> str:
    cleaned = _cut_alliance_stop_text(text)
    cleaned = _remove_account_holder_mentions(cleaned, account_holder)
    if not cleaned:
        return "UNKNOWN"

    for pattern in ALLIANCE_DIRECT_PARTY_REGEXES:
        match = pattern.search(cleaned)
        if not match:
            continue
        candidate = normalize_text(match.group(1) if match.groups() else match.group(0))
        candidate = _strip_alliance_reference_prefix(candidate)
        candidate = ALLIANCE_TRAILING_NOISE_RE.sub("", candidate)
        candidate = _cleanup_alliance_party_candidate(candidate)
        if not _is_weak_alliance_party_name(candidate):
            return candidate

    return "UNKNOWN"


def _extract_alliance_named_phrase(text: str, account_holder: str = "") -> str:
    cleaned = _cut_alliance_stop_text(text)
    cleaned = _remove_account_holder_mentions(cleaned, account_holder)
    if not cleaned:
        return "UNKNOWN"

    direct_regex_candidate = _extract_alliance_direct_regex_party(cleaned, account_holder="")
    if direct_regex_candidate != "UNKNOWN":
        return direct_regex_candidate

    legal_candidate = _extract_alliance_legal_name(cleaned, account_holder="")
    if legal_candidate != "UNKNOWN":
        return legal_candidate

    for marker in ["TRANSFER FROM ABMB", "FUND TRANSFER"]:
        if marker in cleaned:
            candidate = cleaned.rsplit(marker, 1)[-1]
            candidate = _strip_alliance_reference_prefix(candidate)
            candidate = ALLIANCE_TRAILING_NOISE_RE.sub("", candidate)
            candidate = _cleanup_alliance_party_candidate(candidate)
            if not _is_weak_alliance_party_name(candidate):
                return candidate

    tokens = []
    for token in normalize_text(cleaned).split():
        if token in ALLIANCE_GENERIC_TOKENS:
            continue
        if any(ch.isdigit() for ch in token):
            continue
        tokens.append(token)

    if len(tokens) >= 2:
        candidate = _cleanup_alliance_party_candidate(" ".join(tokens[-min(5, len(tokens)):]))
        if not _is_weak_alliance_party_name(candidate):
            return candidate
    if len(tokens) == 1:
        candidate = _cleanup_alliance_party_candidate(tokens[0])
        if not _is_weak_alliance_party_name(candidate):
            return candidate
    return "UNKNOWN"


def extract_alliance_party_name(description: str, account_holder: str = "", description_lines: Any = None) -> str:
    if _is_alliance_cheque_transaction(description, description_lines=description_lines):
        return "CHEQUE TRANSACTION"

    bottom_up_candidate = _extract_alliance_bottom_up_party(
        description,
        description_lines=description_lines,
        account_holder=account_holder,
    )
    if bottom_up_candidate != "UNKNOWN":
        return bottom_up_candidate

    desc = sanitize_transaction_description(description)
    desc = _cut_alliance_stop_text(desc)
    desc = normalize_text(desc).upper()
    if not desc:
        return "UNKNOWN"

    if any(pattern.search(desc) for pattern in ALLIANCE_NON_PARTY_PATTERNS):
        return "UNKNOWN"

    for pattern in ALLIANCE_PARTY_CAPTURE_PATTERNS:
        match = pattern.search(desc)
        if not match:
            continue

        body = normalize_text(match.group("body"))
        body = _cut_alliance_stop_text(body)
        body = _strip_alliance_reference_prefix(body)
        body = ALLIANCE_TRAILING_NOISE_RE.sub("", body)
        body = normalize_text(body)
        if not body:
            return "UNKNOWN"

        candidate = _extract_alliance_named_phrase(body, account_holder=account_holder)
        if candidate != "UNKNOWN":
            return candidate

    return "UNKNOWN"


def annotate_alliance_counterparties(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach Alliance counterparty aliases used by the shared ledger pipeline."""
    raw_names = []
    for row in rows or []:
        raw = (
            row.get("counterparty_name_raw")
            or row.get("counterparty_name")
            or row.get("party_name")
            or extract_alliance_party_name(
                row.get("description", ""),
                account_holder=row.get("company_name", ""),
                description_lines=row.get("description_lines"),
            )
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
                    "description_lines": ["BEGINNING BALANCE"],
                    "debit": 0.0,
                    "credit": 0.0,
                    "balance": float(bal),
                    "is_balance_marker": True,
                    "page": int(r.get("page") or 0),
                    "seq": seq,
                    "bank": "Alliance Bank",
                    "source_file": filename,
                }
            )
            continue

        if "ENDING BALANCE" in desc_up and isinstance(bal, (int, float)):
            prev_balance = float(bal)
            out.append(
                {
                    "date": r["date"],
                    "description": "ENDING BALANCE",
                    "description_lines": ["ENDING BALANCE"],
                    "debit": 0.0,
                    "credit": 0.0,
                    "balance": float(bal),
                    "is_balance_marker": True,
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

        out.append(
            {
                "date": r["date"],
                "description": desc,
                "description_lines": [normalize_text(part) for part in (r.get("description_parts") or []) if normalize_text(part)],
                "debit": float(debit),
                "credit": float(credit),
                "balance": float(bal) if isinstance(bal, (int, float)) else None,
                "is_balance_marker": False,
                "page": int(r.get("page") or 0),
                "seq": seq,
                "bank": "Alliance Bank",
                "source_file": filename,
            }
        )

    return annotate_alliance_counterparties(out)
