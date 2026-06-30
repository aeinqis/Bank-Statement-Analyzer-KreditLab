from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Tuple

from core_utils import (
    display_transaction_date,
    normalize_text,
    normalize_company_suffix as normalize_company_suffix_core,
    parse_any_date,
    should_drop_as_counterparty,
    signed_amount_from_record,
)


GENERIC_PARTY_SUFFIX_TOKENS = {
    "SDN", "BHD", "POSTPAID", "PREPAID", "BILL", "PAYMENT",
    "SERVICES", "SERVICE", "COMMUNICATIONS", "COMM", "TELCO",
}

PARTY_NUMERIC_TOKEN_RE = re.compile(r"\b\S*\d\S*\b")
PERSON_NAME_MARKER_TOKENS = {"BIN", "BINT", "BINTE", "BINTI", "B", "BT", "ANAK"}
TRANSACTION_DETAIL_SUFFIX_TOKENS = {
    "AC", "BERAM", "CASH", "CLAIM", "DELIVERY", "DET", "EC", "EXCEL",
    "FAREWELL", "GENERAL", "HOUSE", "INSURANCE", "INVOICE", "LABOUR", "PAYMENT",
    "BAJET", "LOAN", "MILEAGE", "PERUNTUKAN", "PETTY", "POLE", "RENTAL", "ROADTAX", "SEWA",
    "SPONSER", "SPONSOR", "TENDER", "TRIP",
}
TRANSACTION_DETAIL_LEADING_TOKENS = {
    "CLAIM", "EC", "FAREWELL", "GENERAL", "HOUSE", "INSURANCE", "LOAN",
    "MILEAGE", "PERUNTUKAN", "PETTY", "RENTAL", "ROADTAX", "TENDER",
}
COUNTERPARTY_DESCRIPTOR_TOKENS = {
    "STAFF", "SALARY", "OVERTIME", "ADVANCE", "DONATION", "INVOICE",
    "INVOICES", "PAYMENT", "BALANCE", "TOKEN", "AWARD", "TOPUP", "REF",
    "INV", "POLICY", "NO", "ACC", "ACCOUNT", "TRANSFER", "MONTHLY",
    "INCENTIVE",
}
COUNTERPARTY_MONTH_TOKENS = {
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
}
COUNTERPARTY_CHANNEL_TOKENS = {
    "MBB", "HLBB", "RHB", "BSN", "PBB", "ABMB", "AMFB", "QTN", "POB",
    "CA", "X", "SST",
}
COUNTERPARTY_PERSON_CONNECTOR_TOKENS = {"BIN", "BINT", "BINTE", "BINTI", "ANAK", "EN"}
COUNTERPARTY_COMPANY_SUFFIX_TOKENS = {"SDN", "BHD", "BERHAD"}
COUNTERPARTY_NOISE_TOKENS = (
    COUNTERPARTY_DESCRIPTOR_TOKENS
    | COUNTERPARTY_MONTH_TOKENS
    | COUNTERPARTY_CHANNEL_TOKENS
    | COUNTERPARTY_PERSON_CONNECTOR_TOKENS
)
COUNTERPARTY_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
)
COUNTERPARTY_REF_TOKEN_RE = re.compile(r"^(?:INV|REF|NO|ACC|ACCOUNT)?\d{2,}[A-Z0-9]*$")
COUNTERPARTY_ALLOWED_PUNCT_RE = re.compile(r"[^A-Z0-9&()\s]+")
COUNTERPARTY_PERSON_MEMO_SUFFIX_TOKENS = {
    "CASH", "CLAIM", "CLEANER", "EAST", "COAST", "TRAVEL", "HOUSING",
    "LOAN", "LAPTOP", "MMU", "FEES", "FEE", "OFFICE", "ELECTRICITY",
    "REFUND", "CAR", "SERVICE", "THAILAND", "TRIP", "PIKM", "THE",
    "PARK", "RESIDENT", "UNIFORM", "RENT", "RENTAL", "HOUSE", "MEDICAL",
    "ALLOWANCE", "HOSTEL", "TRANSPORT", "PETROL", "TOLL", "PARKING",
    "FAREWELL", "INSTALMENT", "INSTALLMENT", "BILL", "BILLS", "UTILITIES",
    "WATER", "PHONE", "INTERNET", "MAINTENANCE", "REIMBURSEMENT",
    "REIMBURSE", "EXPENSE", "EXPENSES",
}
COUNTERPARTY_PERSON_MEMO_PREFIX_TOKENS = {
    "KETUA", "UNIT", "KESELAMAT", "KESELAMATAN", "JABATAN", "BAHAGIAN",
    "DEPARTMENT", "DEPT", "DIVISION", "SECTION", "TEAM", "STAFF",
    "PAYMENT", "BAYARAN", "CLAIM", "EXPENSE", "EXPENSES", "REIMBURSE",
    "REIMBURSEMENT", "REFUND", "PETTY", "CASH", "BALANCE", "ADVANCE",
}
COUNTERPARTY_PERSON_NAME_START_TOKENS = {
    "ABD", "ABDUL", "AHMAD", "AINA", "AISYAH", "DAYANG", "FATHIN",
    "FATIN", "KHAIRUL", "MOHAMAD", "MOHAMED", "MOHAMMAD", "MOHD",
    "MUHAMAD", "MUHAMMAD", "NOOR", "NOR", "NUR", "NURUL", "PUAN",
    "SHAHARUDDIN", "SHAUFIAH", "SITI", "WAN",
}
COUNTERPARTY_MEMO_SUFFIX_LEADING_TOKENS = (
    TRANSACTION_DETAIL_LEADING_TOKENS
    | COUNTERPARTY_PERSON_MEMO_SUFFIX_TOKENS
)
PERSON_TRANSACTION_DETAIL_SUFFIX_RE = re.compile(
    r"\s+(?:HOUSE\s+RENTAL|GENERAL\s+LABOUR|PETTY\s+CASH|EC\s+EXCEL|"
    r"CLAIM|MILEAGE|LOAN|ROADTAX|INSURANCE|RENTAL|TENDER|FAREWELL|"
    r"PERUNTUKAN(?:\s+BAJET)?|BAJET)\b.*$",
    re.I,
)
_CP_NOISE_NAMES = {
    "ACCOUNT",
    "BANK",
    "BULK",
    "CREDIT",
    "DEBIT",
    "DUITNOW",
    "FPX",
    "FUND TRANSFER",
    "IBG",
    "INSTANT TRANSFER",
    "PAYM",
    "PAYMENT",
    "TRANSFER",
    "TRANSFER TO",
    "TRANSFER FROM",
}
_OWN_PARTY_BOILER_SUFFIX = {
    "SDN",
    "BHD",
    "BERHAD",
    "LTD",
    "LIMITED",
    "PLT",
    "LLP",
    "ENTERPRISE",
    "ENT",
    "TRADING",
    "RESOURCES",
    "HOLDINGS",
    "GROUP",
    "COMPANY",
    "CO",
    "CORP",
    "CORPORATION",
    "INC",
    "PRIVATE",
}
_OWN_PARTY_DESC_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def _normalise_counterparty(name: str) -> str:
    """CP11 normalisation: uppercase, preserve legal suffixes, merge known variants.

    Conservative: this does not do broad fragment/prefix merging. Wrong
    normalisation is worse than duplicate buckets; aliasing handles close
    variants later.
    """
    if not name:
        return "UNIDENTIFIED"
    n = name.upper().strip()
    n = re.sub(r"[.,;:]", " ", n)
    n = re.sub(r"^(?:&\s+)+", "", n)
    n = re.sub(r"(?:\s+&)+$", "", n)
    n = re.sub(r"^(?:PAYM|PAYMENT|SI)\s+", "", n).strip()

    # Legal entity suffixes are load-bearing for classification. Expand
    # truncated Malaysian company tails through the shared core utility.
    n = normalize_company_suffix_core(n)
    n = re.sub(r"\bBERHAD\b", "BHD", n)
    n = re.sub(r"\bBER\b\.?(?=\s|$)", "BHD", n)

    n = re.sub(r"\b(?:MAL|\(M\)|& CO)\b\.?", " ", n)
    n = re.sub(r"\((?:SARAWAK|SABAH|MALAYSI[A]?|SAR|L|M)\b\)?", " ", n)
    n = re.sub(r"\s+", " ", n).strip()

    while True:
        m2 = re.match(r"^(.*?)(?<=\s)(?:BH|SD|B|M|&|MALA|MALAY)\s*$", n)
        if not m2:
            break
        n = m2.group(1).strip()
        if not n:
            break

    if "PLANWORTH" in n:
        return "PLANWORTH GLOBAL"
    if n == "JANM" or n.startswith("JANM ") or " JANM" in f" {n} " or "JANM CAWANGAN" in n:
        return "JANM"

    if n in _CP_NOISE_NAMES or len(n) < 3 or should_drop_as_counterparty(n):
        return "UNCATEGORIZED"

    return n or "UNIDENTIFIED"


def _own_party_core_tokens(own_party: Any) -> List[str]:
    cleaned = _normalise_counterparty(normalize_text(own_party))
    tokens = [
        token
        for token in _OWN_PARTY_DESC_TOKEN_RE.findall(cleaned)
        if token not in _OWN_PARTY_BOILER_SUFFIX and not token.isdigit()
    ]
    return tokens


def _own_party_token_matches(candidate: str, own_token: str) -> bool:
    candidate = candidate.strip(" .,-/&()")
    own_token = own_token.strip(" .,-/&()")
    if not candidate or not own_token:
        return False
    if candidate == own_token:
        return True
    shorter, longer = sorted((candidate, own_token), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter) and len(longer) - len(shorter) <= 3


def _strip_own_party_tokens(name: str, own_party: str) -> str:
    """Strip statement-holder tokens from an extracted counterparty name.

    Handles prefix, suffix, bracketing, and column-width truncation forms.
    Requires at least two non-boilerplate holder tokens to match and keeps the
    original name when the remainder would not contain a useful counterparty.
    """
    if not name or not own_party:
        return name
    name_up = _normalise_counterparty(name).split()
    if not name_up:
        return name
    own_core = _own_party_core_tokens(own_party)
    if len(own_core) < 2:
        return name

    min_matches = max(2, (len(own_core) + 1) // 2)
    best_window: Tuple[int, int, int] | None = None

    for start in range(len(name_up)):
        for own_start in range(len(own_core)):
            count = 0
            while (
                start + count < len(name_up)
                and own_start + count < len(own_core)
                and _own_party_token_matches(name_up[start + count], own_core[own_start + count])
            ):
                count += 1
            if count < min_matches:
                continue
            end = start + count
            own_tokens = set(_OWN_PARTY_DESC_TOKEN_RE.findall(str(own_party).upper()))
            while end < len(name_up) and name_up[end] in _OWN_PARTY_BOILER_SUFFIX and name_up[end] in own_tokens:
                end += 1
            expanded_start = start
            while (
                expanded_start > 0
                and name_up[expanded_start - 1] in _OWN_PARTY_BOILER_SUFFIX
                and name_up[expanded_start - 1] in own_tokens
            ):
                expanded_start -= 1
            rank = (end - expanded_start, count)
            if best_window is None or rank > (best_window[1] - best_window[0], best_window[2]):
                best_window = (expanded_start, end, count)

    if best_window is None:
        return name

    start, end, _count = best_window
    remainder = " ".join(name_up[:start] + name_up[end:]).strip()
    if len(remainder) < 3 or should_drop_as_counterparty(remainder):
        return name

    return remainder


def _description_implies_own_party(desc: str, own_party: str) -> bool:
    """Return True iff at least two holder core tokens and >=50% of them are in desc."""
    own_core = _own_party_core_tokens(own_party)
    if len(own_core) < 2:
        return False
    desc_tokens = set(_OWN_PARTY_DESC_TOKEN_RE.findall(str(desc).upper()))
    if not desc_tokens:
        return False
    matched = sum(1 for token in own_core if token in desc_tokens)
    return matched >= 2 and matched / len(own_core) >= 0.5


def normalize_company_suffix(name: Any) -> str:
    cleaned = normalize_text(name).upper()
    if not cleaned:
        return "UNKNOWN"

    tokens = []
    for token in cleaned.split():
        token_core = token.strip(" .,-")
        if not token_core:
            continue

        if token_core == "SN" and not tokens:
            token = "SN"
        elif token_core in {"SN", "SND", "SD", "SDN"}:
            token = "SDN"
        elif token_core in {"BH", "BDH", "B", "BHD"} and any(existing == "SDN" for existing in tokens):
            token = "BHD"
        else:
            token = token_core
        tokens.append(token)

    if "SDN" in tokens:
        sdn_index = tokens.index("SDN")
        if sdn_index + 1 < len(tokens) and tokens[sdn_index + 1] == "BHD":
            tokens = tokens[: sdn_index + 2]
        else:
            tokens = tokens[: sdn_index + 1] + ["BHD"]

    return normalize_text(" ".join(tokens)) or "UNKNOWN"


def _strip_numeric_party_tokens(name: Any) -> str:
    cleaned = normalize_text(name).upper()
    if not cleaned:
        return ""
    cleaned = PARTY_NUMERIC_TOKEN_RE.sub(" ", cleaned)
    return normalize_text(cleaned).strip(" -/,.")


def _strip_person_transaction_detail_suffix(name: Any) -> str:
    cleaned = normalize_text(name).upper()
    if not cleaned or _person_marker_index(cleaned.split()) is None:
        return cleaned

    stripped = PERSON_TRANSACTION_DETAIL_SUFFIX_RE.sub("", cleaned).strip()
    if stripped and _person_marker_index(stripped.split()) is not None:
        return stripped

    return cleaned


def _counterparty_token_is_noise(token: str) -> bool:
    token_core = token.strip("()")
    if not token_core:
        return True
    if token_core in COUNTERPARTY_NOISE_TOKENS:
        return True
    if COUNTERPARTY_REF_TOKEN_RE.match(token_core):
        return True
    if any(char.isdigit() for char in token_core) and len(token_core) > 2:
        return True
    return False


def _collapse_adjacent_duplicate_tokens(tokens: List[str]) -> List[str]:
    collapsed: List[str] = []
    for token in tokens:
        if collapsed and collapsed[-1] == token:
            continue
        collapsed.append(token)
    if collapsed and len(set(collapsed)) == 1:
        return [collapsed[0]]
    return collapsed


def _looks_like_person_name_base(tokens: List[str]) -> bool:
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    if any(token in COUNTERPARTY_COMPANY_SUFFIX_TOKENS for token in tokens):
        return False
    if tokens[0] in COUNTERPARTY_PERSON_NAME_START_TOKENS:
        return True
    return all(
        token not in COUNTERPARTY_PERSON_MEMO_SUFFIX_TOKENS
        and token not in COUNTERPARTY_PERSON_MEMO_PREFIX_TOKENS
        and token not in COUNTERPARTY_DESCRIPTOR_TOKENS
        and token not in COUNTERPARTY_MONTH_TOKENS
        for token in tokens
    )


def _strip_person_memo_prefix_tokens(tokens: List[str]) -> List[str]:
    if len(tokens) <= 3:
        return tokens
    if any(token in COUNTERPARTY_COMPANY_SUFFIX_TOKENS for token in tokens):
        return tokens
    for start in range(1, min(5, len(tokens) - 1)):
        prefix = tokens[:start]
        remainder = tokens[start:]
        if all(token in COUNTERPARTY_PERSON_MEMO_PREFIX_TOKENS for token in prefix) and _looks_like_person_name_base(remainder[:4]):
            return remainder
    return tokens


def _strip_person_memo_suffix_tokens(tokens: List[str]) -> List[str]:
    tokens = _strip_person_memo_prefix_tokens(tokens)
    if len(tokens) <= 2:
        return tokens
    if any(token in COUNTERPARTY_COMPANY_SUFFIX_TOKENS for token in tokens):
        return tokens
    for keep_count in range(2, len(tokens)):
        suffix = tokens[keep_count:]
        if not suffix:
            continue
        if keep_count == 2 and suffix[0] not in COUNTERPARTY_MEMO_SUFFIX_LEADING_TOKENS:
            continue
        if all(token in COUNTERPARTY_PERSON_MEMO_SUFFIX_TOKENS for token in suffix) and _looks_like_person_name_base(tokens[:keep_count]):
            return tokens[:keep_count]
    return tokens


def clean_counterparty_name(raw_name: Any) -> str:
    """Return a reusable display/matching name for noisy counterparty strings.

    The cleaner strips transaction descriptors, month prefixes, channel
    suffixes, Malay personal-name connectors, and reference fragments while
    preserving legal company suffixes such as SDN BHD / BHD / LTD.
    """
    raw = normalize_text(raw_name).upper()
    if not raw or raw in {"UNKNOWN", "N/A", "NA", "NONE", "NULL", "-"}:
        return "UNKNOWN"
    if raw in {"TRANSFER FEE", "OTHER TRANSFER FEE"}:
        return "TRANSFER FEE"

    raw = COUNTERPARTY_DATE_RE.sub(" ", raw)
    raw = raw.replace(".", " ")
    raw = COUNTERPARTY_ALLOWED_PUNCT_RE.sub(" ", raw)
    raw = normalize_company_suffix_core(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "UNKNOWN"

    tokens: List[str] = []
    for token in raw.split():
        token = token.strip()
        token_core = token.strip("()")
        if _counterparty_token_is_noise(token):
            continue
        if len(token_core) == 1 and token_core.isalpha():
            tokens.append(token_core)
            continue
        tokens.append(token_core or token)

    while len(tokens) > 2 and len(tokens[-1]) == 1 and tokens[-1].isalpha():
        tokens.pop()

    tokens = _strip_person_memo_suffix_tokens(tokens)
    tokens = _collapse_adjacent_duplicate_tokens(tokens)
    cleaned = normalize_text(" ".join(tokens)).strip()
    cleaned = _normalise_counterparty(cleaned)
    return "UNKNOWN" if cleaned == "UNIDENTIFIED" else cleaned or "UNKNOWN"


def _last_token_prefix_match(a_tokens: List[str], b_tokens: List[str]) -> bool:
    if len(a_tokens) < 2 or len(b_tokens) < 2:
        return False
    if a_tokens[:-1] != b_tokens[:-1]:
        return False
    shorter, longer = sorted((a_tokens[-1], b_tokens[-1]), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter) and len(longer) - len(shorter) <= 3


def _counterparty_names_similar(left: str, right: str, threshold: float = 0.92) -> bool:
    if left == right:
        return True
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return False
    if _last_token_prefix_match(left_tokens, right_tokens):
        return True
    if len(left_tokens) == 1 or len(right_tokens) == 1:
        return SequenceMatcher(None, left, right).ratio() >= 0.96
    if left_tokens[0] != right_tokens[0]:
        return False

    shared = set(left_tokens) & set(right_tokens)
    overlap = len(shared) / max(len(set(left_tokens)), len(set(right_tokens)))
    if overlap < 0.67 and tuple(left_tokens[:2]) != tuple(right_tokens[:2]):
        return False
    return SequenceMatcher(None, left, right).ratio() >= threshold


def _choose_counterparty_canonical(names: List[str], counts: Dict[str, int]) -> str:
    return sorted(
        set(names),
        key=lambda value: (
            -counts.get(value, 0),
            -len(value),
            -len(value.split()),
            value,
        ),
    )[0]


def build_clean_counterparty_alias_map(
    raw_names: List[Any],
    *,
    threshold: float = 0.92,
) -> Dict[str, str]:
    """Map cleaned counterparty names to a fuzzy-deduplicated canonical name."""
    cleaned_names = [
        clean_counterparty_name(name)
        for name in raw_names
        if clean_counterparty_name(name) != "UNKNOWN"
    ]
    unique_names = sorted(set(cleaned_names), key=lambda value: (len(value.split()), len(value), value))
    counts = {name: cleaned_names.count(name) for name in unique_names}
    alias_map: Dict[str, str] = {name: name for name in unique_names}

    groups: List[List[str]] = []
    for name in unique_names:
        placed = False
        for group in groups:
            if any(_counterparty_names_similar(name, existing, threshold) for existing in group):
                group.append(name)
                placed = True
                break
        if not placed:
            groups.append([name])

    for group in groups:
        canonical = _choose_counterparty_canonical(group, counts)
        for name in group:
            alias_map[name] = canonical

    return alias_map


def deduplicate_counterparty_names(
    raw_names: List[Any],
    *,
    threshold: float = 0.92,
) -> List[str]:
    """Clean and fuzzy-deduplicate a sequence of counterparty names."""
    cleaned = [clean_counterparty_name(name) for name in raw_names]
    alias_map = build_clean_counterparty_alias_map(raw_names, threshold=threshold)
    return [alias_map.get(name, name) for name in cleaned]


def normalise_counterparty_for_ledger(
    raw_name: Any,
    *,
    own_party: Any = "",
    description: Any = "",
) -> str:
    """Return the canonical counterparty bucket used by the Counterparty Ledger."""
    cleaned = clean_counterparty_name(raw_name)
    normalised = _normalise_counterparty(cleaned)

    if own_party:
        stripped = _strip_own_party_tokens(normalised, normalize_text(own_party))
        if stripped != normalised:
            normalised = _normalise_counterparty(stripped)
        elif (
            normalised in {"UNKNOWN", "UNIDENTIFIED", "UNCATEGORIZED"}
            and _description_implies_own_party(normalize_text(description), normalize_text(own_party))
        ):
            normalised = "OWN PARTY"

    if normalised == "UNIDENTIFIED":
        return "UNKNOWN"
    return normalised or "UNKNOWN"


def canonicalize_party_name(name: Any) -> str:
    cleaned = normalize_company_suffix(_strip_person_transaction_detail_suffix(_strip_numeric_party_tokens(name)))
    if not cleaned:
        return "UNKNOWN"

    if re.fullmatch(r"\d+", cleaned):
        return "UNKNOWN"

    if re.fullmatch(r"(?:TM\s+)?UNIFI", cleaned, re.I):
        return "UNIFI"

    normalised = _normalise_counterparty(cleaned)
    return "UNKNOWN" if normalised == "UNIDENTIFIED" else normalised


def looks_like_suspicious_short_party(name: Any) -> bool:
    cleaned = normalize_text(name).upper()
    if not cleaned or cleaned == "UNKNOWN":
        return True

    tokens = cleaned.split()
    if len(tokens) == 1 and tokens[0].isdigit():
        return True

    if len(tokens) == 1 and len(tokens[0]) <= 3 and tokens[0].isalpha():
        return True

    return False


def _person_marker_index(tokens: List[str]) -> int | None:
    for idx, token in enumerate(tokens):
        if token.strip(" .,-").upper() in PERSON_NAME_MARKER_TOKENS:
            return idx
    return None


def _is_person_name(name: Any) -> bool:
    tokens = normalize_text(name).upper().split()
    return _person_marker_index(tokens) is not None


def _person_given_tokens(name: str) -> Tuple[str, ...]:
    tokens = normalize_text(name).upper().split()
    marker_idx = _person_marker_index(tokens)
    if marker_idx is None:
        return tuple()
    return tuple(tokens[:marker_idx])


def _person_prefix_alias(base_name: str, candidate_name: str) -> Tuple[str, str] | None:
    """Alias a short non-marker name only when it exactly matches a person's given names."""
    base_is_person = _is_person_name(base_name)
    candidate_is_person = _is_person_name(candidate_name)
    if base_is_person == candidate_is_person:
        return None

    short_name, person_name = (
        (candidate_name, base_name) if base_is_person else (base_name, candidate_name)
    )
    short_tokens = tuple(normalize_text(short_name).upper().split())
    if len(short_tokens) < 3:
        return None

    if short_tokens == _person_given_tokens(person_name):
        return short_name, person_name

    return None


def _shared_prefix_alias(base_tokens: List[str], candidate_tokens: List[str]) -> str:
    common_tokens: List[str] = []
    for base_token, candidate_token in zip(base_tokens, candidate_tokens):
        if base_token != candidate_token:
            break
        common_tokens.append(base_token)

    if len(common_tokens) < 2:
        return ""

    base_suffix = base_tokens[len(common_tokens):]
    candidate_suffix = candidate_tokens[len(common_tokens):]
    if not base_suffix or not candidate_suffix:
        return ""

    combined_suffix = base_suffix + candidate_suffix
    if any(token in {"SDN", "BHD"} for token in combined_suffix):
        return ""

    if (
        len(common_tokens) > 2
        or len(base_tokens) != len(candidate_tokens)
        or len(base_suffix) > 1
        or len(candidate_suffix) > 1
    ):
        return " ".join(common_tokens)

    return ""


def _is_transaction_detail_suffix(tokens: List[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] in TRANSACTION_DETAIL_LEADING_TOKENS:
        return True
    return all(token.isdigit() or token in TRANSACTION_DETAIL_SUFFIX_TOKENS for token in tokens)


def _shared_transaction_detail_alias(base_tokens: List[str], candidate_tokens: List[str]) -> str:
    common_tokens: List[str] = []
    for base_token, candidate_token in zip(base_tokens, candidate_tokens):
        if base_token != candidate_token:
            break
        common_tokens.append(base_token)

    if len(common_tokens) < 2:
        return ""

    common_name = " ".join(common_tokens)
    if (
        len(common_tokens) < 3
        and not _is_person_name(common_name)
        and not _has_company_suffix(common_name)
    ):
        return ""

    base_suffix = base_tokens[len(common_tokens):]
    candidate_suffix = candidate_tokens[len(common_tokens):]
    if not base_suffix and not candidate_suffix:
        return ""

    if base_suffix and not _is_transaction_detail_suffix(base_suffix):
        return ""
    if candidate_suffix and not _is_transaction_detail_suffix(candidate_suffix):
        return ""

    return common_name


def _has_company_suffix(name: str) -> bool:
    tokens = set(normalize_text(name).upper().split())
    return (
        {"SDN", "BHD"}.issubset(tokens)
        or "BHD" in tokens
        or "BERHAD" in tokens
        or "PLT" in tokens
        or "LLP" in tokens
    )


def _choose_front_token_canonical(names: List[str], anchor_tokens: Tuple[str, ...]) -> str:
    company_names = [name for name in names if _has_company_suffix(name)]
    if company_names:
        return sorted(company_names, key=lambda value: (len(value.split()), len(value), value))[0]
    return " ".join(anchor_tokens)


def _apply_front_token_aliasing(
    unique_names: List[str],
    alias_map: Dict[str, str],
    anchor_size: int,
) -> None:
    groups: Dict[Tuple[str, ...], List[str]] = {}
    resolved_names = sorted(
        {
            alias_map.get(name, name)
            for name in unique_names
            if normalize_text(alias_map.get(name, name)).upper() not in {"", "UNKNOWN"}
        }
    )

    for name in resolved_names:
        if _is_person_name(name):
            continue

        tokens = name.split()
        if len(tokens) < anchor_size:
            continue
        groups.setdefault(tuple(tokens[:anchor_size]), []).append(name)

    for anchor_tokens, names in groups.items():
        if len(set(names)) < 2:
            continue
        canonical_name = _choose_front_token_canonical(names, anchor_tokens)
        for original_name in unique_names:
            resolved_name = alias_map.get(original_name, original_name)
            if resolved_name in names:
                alias_map[original_name] = canonical_name
                alias_map[resolved_name] = canonical_name


def build_party_alias_map(party_names: List[str]) -> Dict[str, str]:
    normalized_names = []
    for name in party_names:
        cleaned = normalize_text(name).upper()
        if cleaned and cleaned != "UNKNOWN":
            normalized_names.append(cleaned)

    unique_names = sorted(set(normalized_names), key=lambda value: (len(value.split()), len(value), value))
    alias_map: Dict[str, str] = {name: name for name in unique_names}
    person_names_by_given: Dict[Tuple[str, ...], List[str]] = {}
    for name in unique_names:
        if _is_person_name(name):
            person_names_by_given.setdefault(_person_given_tokens(name), []).append(name)

    for base_name in unique_names:
        base_tokens = base_name.split()
        if len(base_tokens) < 2:
            continue

        anchor = tuple(base_tokens[:2])
        for candidate_name in unique_names:
            candidate_tokens = candidate_name.split()
            if candidate_name == base_name:
                continue
            if tuple(candidate_tokens[:2]) != anchor:
                continue

            detail_alias = _shared_transaction_detail_alias(base_tokens, candidate_tokens)
            if detail_alias:
                alias_map[base_name] = detail_alias
                alias_map[candidate_name] = detail_alias
                continue

            person_alias = _person_prefix_alias(base_name, candidate_name)
            if person_alias:
                short_name, person_name = person_alias
                if len(person_names_by_given.get(_person_given_tokens(person_name), [])) == 1:
                    alias_map[short_name] = person_name
                continue

            if _is_person_name(base_name) or _is_person_name(candidate_name):
                continue

            if len(candidate_tokens) > len(base_tokens) and candidate_tokens[: len(base_tokens)] == base_tokens:
                candidate_suffix = candidate_tokens[len(base_tokens):]
                if (
                    candidate_suffix
                    and all(token in GENERIC_PARTY_SUFFIX_TOKENS for token in candidate_suffix)
                    and {"SDN", "BHD"}.issubset(set(candidate_tokens))
                ):
                    alias_map[base_name] = candidate_name
                    alias_map[candidate_name] = candidate_name
                else:
                    alias_map[candidate_name] = base_name
                continue

            if len(base_tokens) >= 3 and len(candidate_tokens) == len(base_tokens):
                if base_tokens[:-1] != candidate_tokens[:-1]:
                    continue

                base_last = base_tokens[-1]
                candidate_last = candidate_tokens[-1]
                shorter, longer = sorted((base_last, candidate_last), key=len)
                if (
                    len(shorter) >= 3
                    and len(longer) - len(shorter) <= 3
                    and longer.startswith(shorter)
                ):
                    canonical_name = base_name if len(base_name) <= len(candidate_name) else candidate_name
                    alias_map[base_name] = canonical_name
                    alias_map[candidate_name] = canonical_name
                    continue

            if len(base_tokens) >= 2 and len(candidate_tokens) >= 2:
                if tuple(base_tokens[:2]) != tuple(candidate_tokens[:2]):
                    continue

                base_suffix = base_tokens[2:]
                candidate_suffix = candidate_tokens[2:]
                if not base_suffix or not candidate_suffix:
                    continue
                if (
                    all(token in GENERIC_PARTY_SUFFIX_TOKENS for token in base_suffix)
                    and all(token in GENERIC_PARTY_SUFFIX_TOKENS for token in candidate_suffix)
                ):
                    canonical_name = _choose_front_token_canonical(
                        [base_name, candidate_name],
                        tuple(base_tokens[:2]),
                    )
                    alias_map[base_name] = canonical_name
                    alias_map[candidate_name] = canonical_name
                    continue

                canonical_name = _shared_prefix_alias(base_tokens, candidate_tokens)
                if canonical_name:
                    alias_map[base_name] = canonical_name
                    alias_map[candidate_name] = canonical_name

    _apply_front_token_aliasing(unique_names, alias_map, anchor_size=3)
    _apply_front_token_aliasing(unique_names, alias_map, anchor_size=2)

    return alias_map


def apply_party_aliasing(party_series: Any):
    alias_map = build_party_alias_map(
        [canonicalize_party_name(value) for value in party_series.fillna("").astype(str).tolist()]
    )
    if not alias_map:
        return party_series.map(canonicalize_party_name).replace("", "UNKNOWN")

    return (
        party_series.fillna("")
        .astype(str)
        .map(canonicalize_party_name)
        .replace("", "UNKNOWN")
        .map(lambda value: alias_map.get(value.upper(), value))
    )


def _resolve_group_party_name(row: Any, fallback_party_extractor: Callable[[Any], str] | None = None) -> str:
    own_party = normalize_text(row.get("company_name"))
    description = normalize_text(row.get("description"))
    candidate = normalise_counterparty_for_ledger(
        row.get("party_name"),
        own_party=own_party,
        description=description,
    )

    if candidate and not looks_like_suspicious_short_party(candidate):
        return candidate

    if callable(fallback_party_extractor):
        fallback = normalise_counterparty_for_ledger(
            fallback_party_extractor(description),
            own_party=own_party,
            description=description,
        )
        if fallback:
            return fallback

    if candidate:
        return candidate

    return "UNKNOWN"


def build_transactions_by_party(
    df: Any,
    fallback_party_extractor: Callable[[Any], str] | None = None,
) -> List[Dict[str, Any]]:
    try:
        import pandas as pd
    except Exception:
        return []

    if not isinstance(df, pd.DataFrame) or df.empty:
        return []

    party_df = df.copy()
    marker_mask = pd.Series(False, index=party_df.index)
    for marker_column in ("is_balance_marker", "is_statement_balance"):
        if marker_column in party_df.columns:
            marker_mask = marker_mask | (party_df[marker_column] == True)
    if marker_mask.any():
        party_df = party_df[~marker_mask].copy()

    if "description" not in party_df.columns:
        party_df["description"] = ""
    if "date" not in party_df.columns:
        party_df["date"] = ""
    if "source_file" not in party_df.columns:
        party_df["source_file"] = ""

    if "party_name" in party_df.columns:
        party_df["group_party"] = party_df.apply(
            lambda row: _resolve_group_party_name(row, fallback_party_extractor),
            axis=1,
        )
    elif callable(fallback_party_extractor):
        party_df["group_party"] = party_df.apply(
            lambda row: normalise_counterparty_for_ledger(
                fallback_party_extractor(normalize_text(row.get("description"))),
                own_party=normalize_text(row.get("company_name")),
                description=normalize_text(row.get("description")),
            ),
            axis=1,
        )
    else:
        party_df["group_party"] = "UNKNOWN"

    party_df["group_party"] = party_df["group_party"].map(normalize_text).replace("", "UNKNOWN")
    party_df["group_party"] = apply_party_aliasing(party_df["group_party"])
    party_df["signed_amount_value"] = party_df.apply(signed_amount_from_record, axis=1)
    party_df = party_df[party_df["signed_amount_value"].notna()].copy()
    if party_df.empty:
        return []

    if "credit" not in party_df.columns:
        party_df["credit"] = 0.0
    if "debit" not in party_df.columns:
        party_df["debit"] = 0.0

    party_df["credit"] = pd.to_numeric(party_df["credit"], errors="coerce").fillna(0.0)
    party_df["debit"] = pd.to_numeric(party_df["debit"], errors="coerce").fillna(0.0)
    party_df["date_sort_key"] = party_df["date"].apply(lambda x: parse_any_date(x))
    party_df["date_sort_key"] = party_df["date_sort_key"].apply(
        lambda x: pd.Timestamp(x) if x is not None else pd.Timestamp.max
    )
    party_df["date_display"] = party_df["date"].apply(display_transaction_date)
    party_df["amount"] = party_df["signed_amount_value"].apply(lambda value: f"{float(value):+,.2f}")
    party_df = party_df.sort_values(["group_party", "date_sort_key", "description"]).reset_index(drop=True)

    grouped_tables: List[Dict[str, Any]] = []
    for party, group in party_df.groupby("group_party", dropna=False):
        group = group.sort_values(["date_sort_key", "description"], ascending=[False, True], kind="stable")
        display_df = group[["date_display", "description", "amount", "source_file"]].copy()
        display_df = display_df.rename(columns={"date_display": "date"})
        grouped_tables.append(
            {
                "party": str(party or "UNKNOWN"),
                "count": int(len(group)),
                "total_credit": round(float(group["credit"].sum()), 2),
                "total_debit": round(float(group["debit"].sum()), 2),
                "sort_volume": round(float(group["credit"].sum() + group["debit"].sum()), 2),
                "table": display_df.reset_index(drop=True),
            }
        )

    return grouped_tables


def build_top_parties_tables(party_tables: List[Dict[str, Any]], limit: int = 5):
    try:
        import pandas as pd
    except Exception:
        return None, None

    if not party_tables:
        return pd.DataFrame(), pd.DataFrame()

    summary_df = pd.DataFrame(
        [
            {
                "Counterparty": item["party"],
                "Total Amount of Transaction": round(float(item["total_credit"]), 2),
                "Freq Transaction": int(item["count"]),
            }
            for item in party_tables
            if (
                float(item["total_credit"]) > 0
                and normalize_text(item["party"]).upper() not in {"CASH DEPOSIT", "UNKNOWN"}
            )
        ]
    )
    if not summary_df.empty:
        summary_df = summary_df.sort_values(
            ["Total Amount of Transaction", "Freq Transaction", "Counterparty"],
            ascending=[False, False, True],
        ).head(limit).reset_index(drop=True)
        summary_df["Total Amount of Transaction"] = summary_df["Total Amount of Transaction"].map(
            lambda value: f"RM {float(value):,.2f}"
        )

    debit_df = pd.DataFrame(
        [
            {
                "Counterparty": item["party"],
                "Total Amount of Transaction": round(float(item["total_debit"]), 2),
                "Freq Transaction": int(item["count"]),
            }
            for item in party_tables
            if float(item["total_debit"]) > 0 and normalize_text(item["party"]).upper() != "UNKNOWN"
        ]
    )
    if not debit_df.empty:
        debit_df = debit_df.sort_values(
            ["Total Amount of Transaction", "Freq Transaction", "Counterparty"],
            ascending=[False, False, True],
        ).head(limit).reset_index(drop=True)
        debit_df["Total Amount of Transaction"] = debit_df["Total Amount of Transaction"].map(
            lambda value: f"RM {float(value):,.2f}"
        )

    return summary_df, debit_df
