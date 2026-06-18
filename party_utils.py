from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Tuple

from core_utils import (
    display_transaction_date,
    normalize_text,
    parse_any_date,
    signed_amount_from_record,
)


GENERIC_PARTY_SUFFIX_TOKENS = {
    "SDN", "BHD", "POSTPAID", "PREPAID", "BILL", "PAYMENT",
    "SERVICES", "SERVICE", "COMMUNICATIONS", "COMM", "TELCO",
}

PARTY_NUMERIC_TOKEN_RE = re.compile(r"\b\S*\d\S*\b")
PERSON_NAME_MARKER_TOKENS = {"BIN", "BINTI", "B", "BT", "ANAK"}
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
PERSON_TRANSACTION_DETAIL_SUFFIX_RE = re.compile(
    r"\s+(?:HOUSE\s+RENTAL|GENERAL\s+LABOUR|PETTY\s+CASH|EC\s+EXCEL|"
    r"CLAIM|MILEAGE|LOAN|ROADTAX|INSURANCE|RENTAL|TENDER|FAREWELL|"
    r"PERUNTUKAN(?:\s+BAJET)?|BAJET)\b.*$",
    re.I,
)


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


def canonicalize_party_name(name: Any) -> str:
    cleaned = normalize_company_suffix(_strip_person_transaction_detail_suffix(_strip_numeric_party_tokens(name)))
    if not cleaned:
        return "UNKNOWN"

    if re.fullmatch(r"\d+", cleaned):
        return "UNKNOWN"

    if re.fullmatch(r"(?:TM\s+)?UNIFI", cleaned, re.I):
        return "UNIFI"

    return cleaned


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
    candidate = normalize_text(row.get("party_name")).upper()

    if candidate and not looks_like_suspicious_short_party(candidate):
        return candidate

    if callable(fallback_party_extractor):
        fallback = normalize_text(fallback_party_extractor(normalize_text(row.get("description")))).upper()
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
        party_df["group_party"] = party_df["description"].apply(
            lambda value: fallback_party_extractor(normalize_text(value))
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
