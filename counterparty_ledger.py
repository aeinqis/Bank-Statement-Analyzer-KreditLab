# Extracted from app.py to keep the Streamlit entrypoint smaller.
from __future__ import annotations

import copy
import hashlib
import json
import re
import textwrap
from datetime import datetime
from html import escape
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from core_utils import safe_float
except Exception:  # pragma: no cover - rebound from app.py during normal use
    safe_float = float


def bind_app_globals(app_globals: dict) -> None:
    """Expose app.py helpers/constants that these extracted functions already use."""
    for name, value in app_globals.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = value


def build_track2_counterparty_ledger(transactions: List[dict]) -> dict:
    """Convert raw transaction rows into the counterparty_ledger dict shape
    that build_track2_result (kredit_lab_classify_track2) expects.

    Schema: {"counterparties": [{"counterparty_name": str,
                                  "transaction_count": int,
                                  "credit_count": int,
                                  "debit_count": int,
                                  "total_credits": float,
                                  "total_debits": float,
                                  "net_position": float,
                                  "transactions": [{"date", "description",
                                                    "amount", "type"}]
                                 }]}
    """
    from collections import defaultdict

    def _clean_name(value) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value).strip()).upper()

    def _is_special_bucket(name: str) -> bool:
        return _is_report_special_counterparty_bucket(name)

    def _method_for_name(name: str, matched_parser_pattern: bool) -> str:
        if _is_report_unknown_counterparty(name):
            return "raw_fallback"
        if _is_special_bucket(name):
            return "special_bucket"
        if matched_parser_pattern:
            return "pattern_matched"
        return "raw_fallback"

    def _clean_optional_text(value) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        return re.sub(r"\s+", " ", str(value).strip()).upper()

    def _ledger_transaction_entry(
        tx: dict,
        amount: float,
        txn_type: str,
        extraction_method: str,
        clean_name: str,
    ) -> dict:
        raw_counterparty = _clean_optional_text(
            tx.get("_raw_counterparty")
            or tx.get("counterparty_name_raw")
            or tx.get("raw_counterparty")
            or tx.get("party_name")
            or tx.get("counterparty_name")
            or tx.get("counterparty")
        )
        entry = {
            "date": tx.get("date", ""),
            "description": tx.get("description", ""),
            "amount": round(amount, 2),
            "type": txn_type,
            "balance": safe_float(tx.get("balance", 0)),
            "extraction_method": extraction_method,
            "counterparty_name": clean_name,
            "counterparty_name_clean": clean_name,
            "party_name": clean_name,
        }
        if raw_counterparty:
            entry["counterparty_name_raw"] = raw_counterparty
            entry["raw_counterparty"] = raw_counterparty

        for detail_key in (
            "transaction_details",
            "transaction_detail",
            "details",
            "detail",
            "narration",
            "memo",
            "remarks",
            "reference",
            "particulars",
        ):
            detail_value = _clean_optional_text(tx.get(detail_key))
            if detail_value:
                entry[detail_key] = detail_value
        return entry

    extraction_stats = {
        "pattern_matched": 0,
        "special_bucket": 0,
        "raw_fallback": 0,
        "total_transactions": 0,
    }

    tx_df = pd.DataFrame(transactions or [])
    if tx_df.empty:
        return {
            "counterparties": [],
            "total_counterparties": 0,
            "extraction_stats": extraction_stats,
        }

    tx_df = prepare_counterparty_dataframe(tx_df)

    buckets: dict = defaultdict(lambda: {
        "total_credits": 0.0,
        "total_debits": 0.0,
        "credit_count": 0,
        "debit_count": 0,
        "transactions": [],
        "pattern_matched": 0,
        "special_bucket": 0,
        "raw_fallback": 0,
    })

    for tx in tx_df.to_dict("records"):
        name = _clean_name(tx.get("counterparty") or tx.get("party_name"))
        matched_parser_pattern = bool(tx.get("_counterparty_pattern_matched"))
        if not name:
            name = "UNKNOWN"

        extraction_method = _method_for_name(name, matched_parser_pattern)
        bucket = buckets[name]
        credit = float(tx.get("credit") or 0)
        debit = float(tx.get("debit") or 0)
        if credit > 0:
            bucket["total_credits"] += credit
            bucket["credit_count"] += 1
            bucket[extraction_method] += 1
            extraction_stats[extraction_method] += 1
            extraction_stats["total_transactions"] += 1
            bucket["transactions"].append(
                _ledger_transaction_entry(tx, credit, "CREDIT", extraction_method, name)
            )
        if debit > 0:
            bucket["total_debits"] += debit
            bucket["debit_count"] += 1
            bucket[extraction_method] += 1
            extraction_stats[extraction_method] += 1
            extraction_stats["total_transactions"] += 1
            bucket["transactions"].append(
                _ledger_transaction_entry(tx, debit, "DEBIT", extraction_method, name)
            )

    counterparties = []
    for name, b in buckets.items():
        if not b["transactions"]:
            continue
        cr = round(b["total_credits"], 2)
        dr = round(b["total_debits"], 2)
        counterparties.append({
            "counterparty_name": name,
            "transaction_count": b["credit_count"] + b["debit_count"],
            "credit_count": b["credit_count"],
            "debit_count": b["debit_count"],
            "total_credits": cr,
            "total_debits": dr,
            "net_position": round(cr - dr, 2),
            "pattern_matched": b["pattern_matched"],
            "special_bucket": b["special_bucket"],
            "raw_fallback": b["raw_fallback"],
            "transactions": b["transactions"],
        })

    counterparties.sort(
        key=lambda c: abs(c["total_credits"] - c["total_debits"]), reverse=True
    )
    return {
        "counterparties": counterparties,
        "total_counterparties": len(counterparties),
        "extraction_stats": extraction_stats,
    }


def prepare_counterparty_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add the counterparty columns used by the CP ledger.

    The raw parser-extracted name is passed into party_utils, which canonicalizes
    and aliases related spelling variants for cleaner ledger display.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()

    prepared = df.copy()
    resolved_names = []
    ledger_names = []
    matched_flags = []
    for _idx, row in prepared.iterrows():
        counterparty, matched = _resolve_transaction_counterparty_details(row)
        resolved_names.append(counterparty)
        ledger_names.append(
            normalise_counterparty_for_ledger(
                counterparty,
                own_party=row.get("company_name", ""),
                description=row.get("description", ""),
            )
        )
        matched_flags.append(bool(matched))

    raw_series = pd.Series(resolved_names, index=prepared.index, dtype="object")
    ledger_input_series = pd.Series(ledger_names, index=prepared.index, dtype="object")
    try:
        clean_names = deduplicate_counterparty_names(ledger_input_series.fillna("").astype(str).tolist())
        grouped_series = pd.Series(clean_names, index=prepared.index, dtype="object")
    except Exception:
        try:
            grouped_series = apply_party_aliasing(ledger_input_series)
        except Exception:
            grouped_series = ledger_input_series

    grouped_series = grouped_series.apply(lambda value: normalize_counterparty_value(value) or "UNKNOWN")
    grouped_series = pd.Series(
        [
            _apply_counterparty_overrides(raw_name, clean_name)
            for raw_name, clean_name in zip(raw_series.tolist(), grouped_series.tolist())
        ],
        index=prepared.index,
        dtype="object",
    ).apply(lambda value: normalize_counterparty_value(value) or "UNKNOWN")

    prepared["_raw_counterparty"] = raw_series
    prepared["_counterparty_pattern_matched"] = matched_flags
    prepared["counterparty_name_raw"] = raw_series
    prepared["counterparty_name_clean"] = grouped_series
    prepared["party_name"] = grouped_series
    prepared["counterparty"] = grouped_series
    prepared["counterparty_name"] = grouped_series
    return prepared


def resolve_transaction_counterparty(row: pd.Series) -> str:
    """
    Prefer counterparty values extracted by bank parsers. Parser-specific
    helpers may be used, but the UI does not extract counterparties itself.
    """
    counterparty, _matched = _resolve_transaction_counterparty_details(row)
    return counterparty


def render_counterparty_ledger_table(df: pd.DataFrame) -> dict:
    """
    Render counterparty ledger as a table with transaction details on selection
    """
    if df.empty:
        st.info("No counterparty data available.")
        return {}
    
    st.markdown("## Counterparty Ledger")

    download_col, upload_col = st.columns(2)
    with upload_col:
        uploaded_cp_json = st.file_uploader(
            "Upload cleaned counterparty JSON",
            type=["json"],
            key="counterparty_mapping_upload",
            help="Upload the downloaded CP JSON after editing counterparty_name_clean values or provide a mapping of raw/current names to clean names.",
        )

    if uploaded_cp_json is not None:
        try:
            payload = json.load(uploaded_cp_json)
            overrides, entry_count = _extract_counterparty_mapping_from_json(payload)
            if overrides:
                st.session_state.counterparty_name_overrides = overrides
                st.success(
                    f"Applied cleaned counterparty mapping for {entry_count} entries "
                    f"({len(overrides)} aliases)."
                )
            else:
                st.warning("The uploaded JSON did not contain usable counterparty mappings.")
        except Exception as exc:
            st.error(f"Could not read counterparty JSON: {exc}")

    if st.session_state.get("counterparty_name_overrides"):
        if st.button("Clear uploaded counterparty mapping", use_container_width=True):
            st.session_state.counterparty_name_overrides = {}
            st.rerun()

    prepared_df = prepare_counterparty_dataframe(df)

    # Build the same canonical counterparty summary used by HTML and Excel.
    display_cp_ledger = build_track2_counterparty_ledger(prepared_df.to_dict("records"))
    company_name_for_top = (st.session_state.get("company_name_override") or "").strip()
    if not company_name_for_top and "company_name" in prepared_df.columns:
        company_names = [
            str(value).strip()
            for value in prepared_df["company_name"].dropna().tolist()
            if str(value).strip()
        ]
        company_name_for_top = company_names[0] if company_names else ""
    canonical_cp_rows = build_report_counterparty_ledger_rows(
        display_cp_ledger,
        related_parties=filter_report_related_parties(
            st.session_state.get("related_parties_override", []) or [],
            company_name=company_name_for_top,
        ),
        own_related={},
        company_name=company_name_for_top,
    )
    canonical_cp_rows = copy_report_counterparty_rows(canonical_cp_rows)
    top_party_view = prepare_top_parties_for_report(
        _top_parties_from_counterparty_rows(
            canonical_cp_rows,
            limit=None,
            company_name=company_name_for_top,
        ),
        limit=10,
        company_name=company_name_for_top,
    )
    counterparty_summary = pd.DataFrame(
        [
            {
                "counterparty_name": row.get("counterparty_name", ""),
                "transaction_count": row.get("transaction_count", 0),
                "credit_count": row.get("credit_count", 0),
                "debit_count": row.get("debit_count", 0),
                "total_credits": row.get("total_credits", 0),
                "total_debits": row.get("total_debits", 0),
                "net_position": row.get("net_position", 0),
            }
            for row in canonical_cp_rows
        ]
    )
    
    if counterparty_summary.empty:
        st.info("No counterparty data available.")
        return {}

    with download_col:
        cp_payload = _build_counterparty_json_payload(prepared_df, counterparty_summary)
        st.download_button(
            "Download Counterparty List (JSON)",
            json.dumps(cp_payload, indent=4),
            "counterparty_list.json",
            "application/json",
            use_container_width=True,
        )
        st.caption("Edit counterparty_name_clean or mapping values, then upload the JSON back here to regroup the ledger and matching transactions.")
    
    def build_top_counterparty_table(side: str) -> pd.DataFrame:
        rows = top_party_view["payers" if side == "credit" else "payees"]
        if not rows:
            return pd.DataFrame(columns=["Counterparty", "Total Txn", "Total Amnt of Txn"])

        return pd.DataFrame(
            {
                "Counterparty": [row.get("party_name", "") for row in rows],
                "Total Txn": [int(safe_float(row.get("transaction_count", 0))) for row in rows],
                "Total Amnt of Txn": [
                    f"RM {safe_float(row.get('total_amount', 0)):,.2f}"
                    for row in rows
                ],
            }
        )


    # Display summary table
    display_df = counterparty_summary.copy()
    display_df['total_credits'] = display_df['total_credits'].apply(lambda x: f"RM {x:,.2f}")
    display_df['total_debits'] = display_df['total_debits'].apply(lambda x: f"RM {x:,.2f}")
    
    # Format net position with color indicators using column config
    def format_net_position(val):
        if val > 0:
            return f"🟢 RM {val:,.2f}"
        elif val < 0:
            return f"🔴 RM {abs(val):,.2f}"
        return f"⚪ RM {val:,.2f}"
    
    display_df['net_position_display'] = counterparty_summary['net_position'].apply(format_net_position)
    display_df = display_df[
        [
            'counterparty_name',
            'transaction_count',
            'credit_count',
            'debit_count',
            'total_credits',
            'total_debits',
            'net_position_display',
        ]
    ]

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            'counterparty_name': 'Counterparty',
            'transaction_count': 'Transactions',
            'credit_count': 'Credits',
            'debit_count': 'Debits',
            'total_credits': 'Total Credits',
            'total_debits': 'Total Debits',
            'net_position_display': 'Net Position'
        }
    )
    
    # Selection dropdown
    selected_counterparty = st.selectbox(
        "Select a counterparty to inspect transaction lines",
        options=counterparty_summary['counterparty_name'].tolist(),
        index=0,
    )
    
    # Show transactions for selected counterparty
    selected_cp_row = next(
        (row for row in canonical_cp_rows if row.get("counterparty_name") == selected_counterparty),
        {},
    )
    counterparty_tx = pd.DataFrame(selected_cp_row.get("transactions", []) or [])

    if not counterparty_tx.empty:
        for required_col in ("date", "description", "amount", "type", "balance"):
            if required_col not in counterparty_tx.columns:
                counterparty_tx[required_col] = ""
        display_tx = counterparty_tx[["date", "description", "amount", "type", "balance"]].copy()
        display_tx["credit"] = display_tx.apply(
            lambda row: f"RM {safe_float(row.get('amount')):,.2f}" if str(row.get("type", "")).upper() == "CREDIT" else "",
            axis=1,
        )
        display_tx["debit"] = display_tx.apply(
            lambda row: f"RM {safe_float(row.get('amount')):,.2f}" if str(row.get("type", "")).upper() == "DEBIT" else "",
            axis=1,
        )
        display_tx["balance"] = display_tx["balance"].apply(lambda x: f"RM {safe_float(x):,.2f}" if str(x) != "nan" and x != "" else "")
        display_tx = display_tx[["date", "description", "credit", "debit", "balance"]]
        
        st.dataframe(
            display_tx,
            use_container_width=True,
            hide_index=True,
            column_config={
                'date': 'Date',
                'description': 'Description',
                'credit': 'Credit (RM)',
                'debit': 'Debit (RM)',
                'balance': 'Balance'
            }
        )

    credit_col, debit_col = st.columns(2)
    with credit_col:
        st.markdown("#### Top 10 Credit Counterparties")
        st.dataframe(
            build_top_counterparty_table("credit"),
            use_container_width=True,
            hide_index=True,
        )
    with debit_col:
        st.markdown("#### Top 10 Debit Counterparties")
        st.dataframe(
            build_top_counterparty_table("debit"),
            use_container_width=True,
            hide_index=True,
        )

    return {
        "prepared_transactions": prepared_df.to_dict("records"),
        "counterparty_ledger": display_cp_ledger,
        "report_counterparty_rows": copy_report_counterparty_rows(canonical_cp_rows),
        "top_parties": _top_parties_from_counterparty_rows(
            canonical_cp_rows,
            limit=None,
            company_name=company_name_for_top,
        ),
        "company_name": company_name_for_top,
    }


def _resolve_transaction_counterparty_details(row: pd.Series) -> Tuple[str, bool]:
    """
    Prefer counterparty values extracted by bank parsers and keep whether a
    parser/extractor supplied the name.
    """
    for column in COUNTERPARTY_NAME_FIELDS:
        if column in row:
            counterparty = normalize_counterparty_value(row.get(column))
            if counterparty:
                return counterparty, True

    description = row.get("description", "")
    try:
        if pd.isna(description):
            description = ""
    except Exception:
        pass
    bank = normalize_counterparty_value(row.get("bank"))
    if "CIMB" in bank:
        counterparty = normalize_counterparty_value(extract_cimb_party_name(description))
        if counterparty:
            return counterparty, True
    description_text = normalize_counterparty_value(description)
    if IBG_CREDIT_DESCRIPTION_RE.match(description_text):
        counterparty = normalize_counterparty_value(extract_cimb_party_name(description_text))
        if counterparty:
            return counterparty, True

    return "UNKNOWN", False


def _extract_counterparty_mapping_from_json(payload) -> Tuple[Dict[str, str], int]:
    """
    Accept a downloaded CP list, a mapping object, or common AI-cleaned JSON
    variants and convert them to normalized raw/current -> clean mappings.
    """
    mapping: Dict[str, str] = {}
    entry_count = 0

    def add_mapping(source, target):
        nonlocal entry_count
        clean_target = normalize_counterparty_value(target)
        if not clean_target:
            return
        for candidate in _counterparty_override_candidates(str(source or ""), clean_target):
            mapping[candidate] = clean_target
        entry_count += 1

    def add_entry(entry):
        if not isinstance(entry, dict):
            add_mapping(entry, entry)
            return

        target = (
            entry.get("counterparty_name_clean")
            or entry.get("clean_counterparty_name")
            or entry.get("clean_name")
            or entry.get("counterparty_name")
            or entry.get("name")
            or entry.get("party_name")
        )
        aliases = entry.get("aliases") or entry.get("raw_names") or entry.get("variants") or []
        if isinstance(aliases, str):
            aliases = [aliases]

        sources = list(aliases) if isinstance(aliases, list) else []
        for key in ("counterparty_name_raw", "raw_name", "original_name", "from", "source"):
            if entry.get(key):
                sources.append(entry.get(key))
        if not sources and target:
            sources = [target]

        for source in sources:
            add_mapping(source, target)

    if isinstance(payload, dict):
        counterparties = payload.get("counterparties") or payload.get("counterparty_list") or []
        if isinstance(counterparties, list):
            for entry in counterparties:
                add_entry(entry)

        raw_mapping = payload.get("mapping") or payload.get("counterparty_mapping")
        if isinstance(raw_mapping, dict):
            for source, target in raw_mapping.items():
                add_mapping(source, target)
        elif isinstance(raw_mapping, list):
            for item in raw_mapping:
                if isinstance(item, dict):
                    add_mapping(
                        item.get("from") or item.get("raw") or item.get("source") or item.get("counterparty_name_raw"),
                        item.get("to") or item.get("clean") or item.get("target") or item.get("counterparty_name_clean"),
                    )
    elif isinstance(payload, list):
        for entry in payload:
            add_entry(entry)

    return mapping, entry_count


def _build_counterparty_json_payload(prepared_df: pd.DataFrame, summary_df: pd.DataFrame) -> dict:
    counterparties = []
    for row in summary_df.to_dict(orient="records"):
        name = normalize_counterparty_value(row.get("counterparty_name")) or "UNKNOWN"
        aliases = []
        if not prepared_df.empty and "_raw_counterparty" in prepared_df.columns:
            alias_series = prepared_df.loc[
                prepared_df["counterparty_name"].astype(str).str.upper() == name,
                "_raw_counterparty",
            ]
            aliases = sorted(
                {
                    normalize_counterparty_value(alias)
                    for alias in alias_series.tolist()
                    if normalize_counterparty_value(alias)
                }
            )

        counterparties.append(
            {
                "counterparty_name_clean": name,
                "aliases": aliases or [name],
                "transaction_count": int(row.get("transaction_count", 0) or 0),
                "credit_count": int(row.get("credit_count", 0) or 0),
                "debit_count": int(row.get("debit_count", 0) or 0),
                "total_credits": round(float(row.get("total_credits", 0) or 0), 2),
                "total_debits": round(float(row.get("total_debits", 0) or 0), 2),
                "net_position": round(float(row.get("net_position", 0) or 0), 2),
            }
        )

    mapping = {}
    for cp in counterparties:
        clean_name = cp["counterparty_name_clean"]
        for alias in cp["aliases"]:
            mapping[alias] = clean_name

    return {
        "schema": "kredit_lab_counterparty_mapping",
        "version": 1,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instructions": "Edit counterparty_name_clean to regroup aliases, keep aliases for matching, then upload this JSON back into the Counterparty Ledger.",
        "counterparties": counterparties,
        "mapping": mapping,
    }


def build_report_counterparty_ledger_rows(
    cp_ledger: dict,
    related_parties=None,
    own_related=None,
    company_name: str = "",
) -> List[dict]:
    rows = build_canonical_counterparty_ledger_rows(cp_ledger)
    targets = _report_counterparty_alignment_targets(
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )
    if not rows or not targets:
        return rows

    merged: Dict[str, dict] = {}
    for cp in rows:
        matches = [
            target for target in targets
            if _counterparty_row_matches_report_party(cp, target["name"])
        ]
        if matches:
            target = max(matches, key=lambda item: (len(_report_party_core_tokens(item["name"])), len(item["name"])))
            display_name = target["name"]
            is_related_party = bool(target.get("is_related_party"))
        else:
            display_name = str(cp.get("counterparty_name") or "UNKNOWN")
            is_related_party = bool(cp.get("is_related_party"))

        key = display_name.casefold()
        if key not in merged:
            merged[key] = _copy_counterparty_row_for_report(cp, display_name, is_related_party=is_related_party)
        else:
            _merge_report_counterparty_row(
                merged[key],
                cp,
                display_name,
                is_related_party=is_related_party,
            )

    output = list(merged.values())
    for row in output:
        row["total_credits"] = round(safe_float(row.get("total_credits")), 2)
        row["total_debits"] = round(safe_float(row.get("total_debits")), 2)
        row["net_position"] = round(row["total_credits"] - row["total_debits"], 2)
        if not row.get("transaction_count"):
            row["transaction_count"] = int(safe_float(row.get("credit_count"))) + int(safe_float(row.get("debit_count")))
        row["raw_names"] = sorted(row.get("raw_names", []) or [])
        row["transactions"] = sorted(
            row.get("transactions", []) or [],
            key=lambda tx: (str(tx.get("date") or ""), str(tx.get("description") or "")),
        )

    output.sort(key=lambda cp: str(cp.get("counterparty_name", "") or "").casefold())
    return output


def get_report_counterparty_rows_from_data(
    data: dict,
    cp_ledger: dict,
    related_parties=None,
    own_related=None,
    company_name: str = "",
) -> List[dict]:
    """Return exported ledger rows, preferring the exact rows rendered by Streamlit."""
    if isinstance(data, dict):
        for key in ("report_counterparty_rows", "counterparty_ledger_rows"):
            rows = copy_report_counterparty_rows(data.get(key))
            if rows:
                return rows

    return build_report_counterparty_ledger_rows(
        cp_ledger,
        related_parties=related_parties,
        own_related=own_related,
        company_name=company_name,
    )


def copy_report_counterparty_rows(rows) -> List[dict]:
    """Copy finalized UI ledger rows and normalize display legal suffixes."""
    output: List[dict] = []
    if not isinstance(rows, list):
        return output

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _canonical_report_counterparty_display_name(
            row.get("counterparty_name") or row.get("counterparty") or "UNKNOWN"
        )
        copied = dict(row)
        copied["counterparty_name"] = name
        copied["total_credits"] = round(safe_float(copied.get("total_credits", copied.get("total_credit", 0))), 2)
        copied["total_debits"] = round(safe_float(copied.get("total_debits", copied.get("total_debit", 0))), 2)
        copied["credit_count"] = int(safe_float(copied.get("credit_count", copied.get("credit_tx_count", 0))))
        copied["debit_count"] = int(safe_float(copied.get("debit_count", copied.get("debit_tx_count", 0))))
        copied["transaction_count"] = int(
            safe_float(
                copied.get("transaction_count")
                or copied["credit_count"] + copied["debit_count"]
            )
        )
        copied["net_position"] = round(copied["total_credits"] - copied["total_debits"], 2)

        raw_names = copied.get("raw_names", [])
        if isinstance(raw_names, (set, tuple)):
            raw_names = list(raw_names)
        elif raw_names and not isinstance(raw_names, list):
            raw_names = [raw_names]
        copied["raw_names"] = sorted(str(value) for value in (raw_names or []) if value)

        transactions = []
        for txn in copied.get("transactions", []) or []:
            if not isinstance(txn, dict):
                continue
            txn_copy = dict(txn)
            txn_copy["counterparty_name"] = name
            txn_copy["counterparty_name_clean"] = name
            txn_copy["party_name"] = name
            transactions.append(txn_copy)
        copied["transactions"] = sorted(
            transactions,
            key=lambda tx: (str(tx.get("date") or ""), str(tx.get("description") or "")),
        )
        output.append(copied)

    output.sort(key=lambda cp: str(cp.get("counterparty_name", "") or "").casefold())
    return output


def _merge_report_counterparty_row(target: dict, source: dict, display_name: str, is_related_party: bool = False) -> None:
    for key in ("total_credits", "total_debits"):
        target[key] = round(safe_float(target.get(key)) + safe_float(source.get(key)), 2)
    for key in ("credit_count", "debit_count", "transaction_count", "pattern_matched", "special_bucket", "raw_fallback"):
        target[key] = int(safe_float(target.get(key))) + int(safe_float(source.get(key)))
    target["net_position"] = round(safe_float(target.get("total_credits")) - safe_float(target.get("total_debits")), 2)
    target["is_related_party"] = bool(target.get("is_related_party")) or bool(source.get("is_related_party")) or is_related_party

    raw_names = target.setdefault("raw_names", set())
    source_raw = source.get("raw_names", [])
    if isinstance(source_raw, (list, tuple, set)):
        raw_names.update(str(value) for value in source_raw if value)
    elif source_raw:
        raw_names.add(str(source_raw))
    source_name = source.get("counterparty_name") or source.get("counterparty")
    if source_name:
        raw_names.add(str(source_name))

    target_txns = target.setdefault("transactions", [])
    for txn in source.get("transactions", []) or []:
        if not isinstance(txn, dict):
            continue
        txn_copy = dict(txn)
        txn_copy["counterparty_name_clean"] = display_name
        txn_copy["counterparty_name"] = display_name
        txn_copy["party_name"] = display_name
        target_txns.append(txn_copy)


def _copy_counterparty_row_for_report(cp: dict, display_name: str, is_related_party: bool = False) -> dict:
    copied = dict(cp)
    copied["counterparty_name"] = display_name
    copied["is_related_party"] = bool(copied.get("is_related_party")) or is_related_party
    raw_names = cp.get("raw_names", [])
    copied_raw_names = set()
    if isinstance(raw_names, (list, tuple, set)):
        copied_raw_names.update(str(value) for value in raw_names if value)
    elif raw_names:
        copied_raw_names.add(str(raw_names))
    copied["raw_names"] = copied_raw_names
    original_name = cp.get("counterparty_name") or cp.get("counterparty")
    if original_name:
        copied["raw_names"].add(str(original_name))
    copied["transactions"] = []
    for txn in cp.get("transactions", []) or []:
        if not isinstance(txn, dict):
            continue
        txn_copy = dict(txn)
        txn_copy["counterparty_name_clean"] = display_name
        txn_copy["counterparty_name"] = display_name
        txn_copy["party_name"] = display_name
        copied["transactions"].append(txn_copy)
    return copied


def _report_counterparty_alignment_targets(
    related_parties=None,
    own_related=None,
    company_name: str = "",
) -> List[dict]:
    targets: List[dict] = []
    seen = set()

    def add_target(name: str, is_related_party: bool = False) -> None:
        display_name = re.sub(r"\s+", " ", str(name or "").strip())
        key = display_name.upper()
        if (
            not key
            or key in seen
            or _is_report_unknown_counterparty(display_name)
            or _is_report_special_counterparty_bucket(display_name)
            or len(_report_party_core_tokens(display_name)) < 2
        ):
            return
        targets.append({"name": display_name, "is_related_party": is_related_party})
        seen.add(key)

    for group in build_own_related_party_groups_for_report(
        own_related,
        related_parties=related_parties,
        company_name=company_name,
    ):
        add_target(
            group.get("party_name", ""),
            is_related_party=group.get("badge_type") == "RP",
        )

    for party in related_parties or []:
        add_target(_report_party_display_name(party), is_related_party=True)

    return targets


def _counterparty_row_matches_report_party_name(cp: dict, party_name: str) -> bool:
    for source in _counterparty_row_name_sources(cp):
        if _report_party_identity_names_match(source, party_name):
            return True
    return False


def _counterparty_row_report_match_sources(cp: dict) -> List[str]:
    sources = [
        cp.get("counterparty_name"),
        cp.get("counterparty"),
    ]
    raw_names = cp.get("raw_names", [])
    if isinstance(raw_names, (list, tuple, set)):
        sources.extend(raw_names)
    elif raw_names:
        sources.append(raw_names)

    for txn in cp.get("transactions", []) or []:
        if not isinstance(txn, dict):
            continue
        sources.extend(
            [
                txn.get("counterparty_name"),
                txn.get("counterparty_name_clean"),
                txn.get("counterparty_name_raw"),
                txn.get("party_name"),
                txn.get("description"),
            ]
        )
    return [str(source) for source in sources if source]


def _report_party_names_equivalent(left, right) -> bool:
    left_name = _report_party_display_name(left)
    right_name = _report_party_display_name(right)
    if not left_name or not right_name:
        return False
    if left_name.upper() == right_name.upper():
        return True
    left_tokens = _report_party_alias_core_tokens(left_name)
    right_tokens = _report_party_alias_core_tokens(right_name)
    return (
        _report_tokens_ordered_match(left_tokens, right_tokens)
        or _report_tokens_ordered_match(right_tokens, left_tokens)
    )


def _report_candidate_contains_party_tokens(candidate, party_name) -> bool:
    party_tokens = _report_party_core_tokens(party_name)
    candidate_tokens = _report_match_tokens(candidate)
    return _report_tokens_ordered_match(party_tokens, candidate_tokens)


def _report_name_matches_own_party(name, company_name: str = "") -> bool:
    """Return True when a report party label is the statement holder."""
    party_name = _report_party_display_name(name)
    own_party = _report_party_display_name(company_name)
    if not party_name or not own_party:
        return False
    if party_name.upper() == own_party.upper():
        return True
    if _report_party_names_equivalent(party_name, own_party):
        return True
    try:
        return _report_party_names_equivalent(
            _canonical_report_counterparty_display_name(party_name),
            _canonical_report_counterparty_display_name(own_party),
        )
    except Exception:
        return False


def _canonical_report_counterparty_display_name(value) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return "UNKNOWN"
    try:
        canonical = normalise_counterparty_for_ledger(raw)
    except Exception:
        canonical = clean_counterparty_name(raw)
    canonical = re.sub(r"\s+", " ", str(canonical or "").strip())
    return canonical or raw


def filter_report_related_parties(related_parties, company_name: str = "") -> List:
    """Return analyst-confirmed related parties excluding synthetic and own-party buckets."""
    filtered: List = []
    for name, relationship in _report_related_party_entries(related_parties):
        original = next(
            (
                party for party in (related_parties or [])
                if _report_party_names_equivalent(name, _report_party_display_name(party))
            ),
            None,
        )
        if isinstance(original, dict):
            item = dict(original)
            item["name"] = name
            if relationship:
                item["relationship"] = relationship
            if _report_name_matches_own_party(name, company_name):
                continue
            filtered.append(item)
        else:
            if _report_name_matches_own_party(name, company_name):
                continue
            filtered.append({"name": name, "relationship": relationship})
    return filtered


def _report_related_party_entries(related_parties) -> List[tuple[str, str]]:
    entries: List[tuple[str, str]] = []
    seen = set()
    for party in related_parties or []:
        name = _report_party_display_name(party)
        if not name:
            continue
        if _is_report_unknown_counterparty(name) or _is_report_special_counterparty_bucket(name):
            continue
        key = name.upper()
        if key in seen:
            continue
        entries.append((name, _report_party_relationship(party)))
        seen.add(key)
    return entries


def _report_party_display_name(party) -> str:
    if isinstance(party, dict):
        raw_name = party.get("name") or party.get("party_name") or ""
    else:
        raw_name = "" if party is None else str(party)
    return re.sub(r"\s+", " ", str(raw_name).strip())


__all__ = [
    'bind_app_globals',
    'build_track2_counterparty_ledger',
    'prepare_counterparty_dataframe',
    'resolve_transaction_counterparty',
    'render_counterparty_ledger_table',
    '_resolve_transaction_counterparty_details',
    '_extract_counterparty_mapping_from_json',
    '_build_counterparty_json_payload',
    'build_report_counterparty_ledger_rows',
    'get_report_counterparty_rows_from_data',
    'copy_report_counterparty_rows',
    '_merge_report_counterparty_row',
    '_copy_counterparty_row_for_report',
    '_report_counterparty_alignment_targets',
    '_counterparty_row_matches_report_party_name',
    '_counterparty_row_report_match_sources',
    '_report_party_names_equivalent',
    '_report_candidate_contains_party_tokens',
    '_report_name_matches_own_party',
    '_canonical_report_counterparty_display_name',
    'filter_report_related_parties',
    '_report_related_party_entries',
    '_report_party_display_name',
]
