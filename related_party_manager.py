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


def render_related_party_manager(
    cp_ledger: dict = None,
    shared_report_data: dict = None,
) -> bool:
    """
    Editable related-party panel.
    Returns True if the user made any change this render (caller should
    regenerate reports).
    """
    st.markdown("## 🔗 Related Party Management")
    st.caption(
        "Confirm or dismiss candidate parties. Changes are immediately reflected "
        "in the downloaded HTML and Excel reports."
    )

    changed = False

    def _save(new_confirmed, new_dismissed):
        st.session_state.related_parties_override = new_confirmed
        st.session_state.related_party_candidates_dismissed = new_dismissed

    def _rp_name(rp) -> str:
        if isinstance(rp, dict):
            return str(rp.get("name") or rp.get("party_name") or "").strip()
        return str(rp or "").strip()

    def _rp_key(rp) -> str:
        return _rp_name(rp).upper()

    def _rp_relationship(rp) -> str:
        if isinstance(rp, dict):
            return str(rp.get("relationship") or "").strip()
        return ""

    def _rp_status(rp) -> str:
        if isinstance(rp, dict):
            status = str(rp.get("confidence") or rp.get("status") or "").strip().upper()
            if status in {"HIGH", "MEDIUM", "LOW"}:
                return status
            relationship = str(rp.get("relationship") or "").upper()
            if "ANALYST-CONFIRMED" in relationship:
                return "CONFIRMED"
        return "HIGH"

    company_name = ""
    if isinstance(shared_report_data, dict):
        company_name = str(shared_report_data.get("report_info", {}).get("company_name") or "").strip()
    if not company_name:
        company_name = str(st.session_state.get("company_name_override") or "").strip()

    def _is_own_party_name(name: str) -> bool:
        return bool(name and _report_name_matches_own_party(name, company_name))

    counterparty_rows = _counterparty_rows_for_related_party_manager(cp_ledger, shared_report_data)

    def _ledger_name_for_party(name: str) -> str:
        match = _counterparty_row_for_related_party_name(name, counterparty_rows)
        return _related_party_counterparty_row_name(match) or name

    def _related_party_financials(name: str) -> tuple[float, float]:
        match = _counterparty_row_for_related_party_name(name, counterparty_rows)
        if not match:
            return 0.0, 0.0
        return (
            safe_float(match.get("total_credits", match.get("total_credit", 0))),
            safe_float(match.get("total_debits", match.get("total_debit", 0))),
        )

    def _fmt_rm(amount) -> str:
        value = safe_float(amount)
        return f"RM {value:,.2f}" if value else "-"

    def _manager_badge(label: str) -> str:
        clean = str(label or "").strip().upper()
        palette = {
            "HIGH": ("rgba(248, 113, 113, .16)", "#fecaca", "rgba(248, 113, 113, .45)"),
            "MEDIUM": ("rgba(251, 191, 36, .16)", "#fde68a", "rgba(251, 191, 36, .45)"),
            "LOW": ("rgba(148, 163, 184, .16)", "#e2e8f0", "rgba(148, 163, 184, .45)"),
            "CONFIRMED": ("rgba(52, 211, 153, .14)", "#bbf7d0", "rgba(52, 211, 153, .42)"),
        }
        background, color, border = palette.get(clean, palette["LOW"])
        return (
            f"<span style=\"display:inline-flex;align-items:center;"
            f"border:1px solid {border};border-radius:999px;padding:.18rem .55rem;"
            f"font-size:.78rem;font-weight:700;letter-spacing:.02em;"
            f"background:{background};color:{color};\">{escape(clean or 'REVIEW')}</span>"
        )

    def _candidate_is_hidden_after_alignment(candidate: dict) -> bool:
        names = [
            _rp_name(candidate),
            str(candidate.get("original_name") or "").strip() if isinstance(candidate, dict) else "",
        ]
        review_names = [name for name in names if name]
        if any(_is_own_party_name(name) for name in review_names):
            return True
        blocker_names = list(confirmed_names) + list(dismissed)
        return any(
            name.upper() in confirmed_names
            or name.upper() in dismissed
            or any(_report_party_names_equivalent(name, blocker) for blocker in blocker_names)
            for name in review_names
        )

    def _merge_related_party_entries(entries: list) -> list:
        merged = []
        for rp in entries or []:
            raw_name = _rp_name(rp)
            name = _ledger_name_for_party(raw_name)
            if (
                not name
                or _is_own_party_name(raw_name)
                or _is_own_party_name(name)
                or _is_report_unknown_counterparty(name)
                or _is_report_special_counterparty_bucket(name)
            ):
                continue
            if any(_report_party_names_equivalent(name, existing.get("name")) for existing in merged):
                continue
            item = dict(rp) if isinstance(rp, dict) else {"name": name, "relationship": ""}
            item["name"] = name
            merged.append(item)
        return merged

    confirmed_rps = _merge_related_party_entries(
        list(st.session_state.get("related_parties_override", []) or [])
    )
    report_known_rps = _merge_related_party_entries(
        (shared_report_data or {}).get("report_info", {}).get("related_parties", [])
        if isinstance(shared_report_data, dict)
        else []
    )
    dismissed: set = set(st.session_state.get("related_party_candidates_dismissed", set()))
    known_rps = _merge_related_party_entries(report_known_rps + confirmed_rps)
    confirmed_names = {_rp_key(rp) for rp in known_rps if _rp_key(rp)}

    all_candidates = detect_related_party_candidates(
        cp_ledger,
        confirmed_names,
        dismissed,
        shared_report_data,
        company_name=company_name,
    )
    all_candidates = _align_related_party_candidates_to_counterparty_rows(
        all_candidates,
        counterparty_rows,
    )
    all_candidates = [
        candidate for candidate in all_candidates
        if not _candidate_is_hidden_after_alignment(candidate)
    ]
    auto_known_candidates, possible_candidates = partition_related_party_candidates_for_manager(all_candidates)
    auto_known_to_add = [
        {
            "name": cand["name"],
            "relationship": "High confidence",
            "confidence": "HIGH",
        }
        for cand in auto_known_candidates
        if _rp_key(cand) and _rp_key(cand) not in confirmed_names
    ]
    if auto_known_to_add:
        confirmed_rps = _merge_related_party_entries(confirmed_rps + auto_known_to_add)
        _save(confirmed_rps, dismissed)
        changed = True
        st.rerun()

    known_rps = _merge_related_party_entries(known_rps + auto_known_to_add)
    confirmed_names = {_rp_key(rp) for rp in known_rps if _rp_key(rp)}

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 1 — Known Related Parties
    # ─────────────────────────────────────────────────────────────────────
    with st.expander(f"Known Related Parties ({len(known_rps)})", expanded=True):
        st.caption("Confirmed and high-confidence parties currently used by the reports.")

        if not known_rps:
            st.info("No known related parties detected yet.")
        else:
            h = st.columns([3.2, 2.2, 1.7, 1.7, 1.1])
            for label, col in zip(["Party", "Status / Relationship", "Credits", "Debits", "Action"], h):
                col.markdown(f"**{label}**")
            st.divider()

            to_remove = None
            removable_keys = {_rp_key(rp) for rp in confirmed_rps if _rp_key(rp)}
            for i, rp in enumerate(known_rps):
                name = _rp_name(rp)
                rel = _rp_relationship(rp) or "Affiliate"
                status = _rp_status(rp)
                cr_amt, dr_amt = _related_party_financials(name)

                c1, c2, c3, c4, c5 = st.columns([3.2, 2.2, 1.7, 1.7, 1.1])
                c1.markdown(f"**{escape(name)}**")
                c1.caption(f"Relationship: {rel}")
                c2.markdown(_manager_badge(status), unsafe_allow_html=True)
                c3.write(_fmt_rm(cr_amt))
                c4.write(_fmt_rm(dr_amt))
                if _rp_key(rp) in removable_keys:
                    if c5.button("Remove", key=f"_rp_rm_{i}", help="Remove from known related parties", use_container_width=True):
                        to_remove = _rp_key(rp)
                else:
                    c5.caption("Auto")

            if to_remove is not None:
                confirmed_rps = [rp for rp in confirmed_rps if _rp_key(rp) != to_remove]
                _save(confirmed_rps, dismissed)
                changed = True
                st.rerun()

    # ─────────────────────────────────────────────────────────────────────
    # SECTION 2 — Candidate / Possible Related Parties
    # ─────────────────────────────────────────────────────────────────────
    with st.expander(f"Possible Related Parties ({len(possible_candidates)})", expanded=True):
        st.caption("Review medium and low confidence candidates before adding them to the known list.")

        if not possible_candidates:
            st.info("No medium or low confidence candidates to review.")
        else:
            h = st.columns([3.2, 1.5, 1.7, 1.7, 2.1])
            for label, col in zip(["Candidate", "Confidence", "Credits", "Debits", "Action"], h):
                col.markdown(f"**{label}**")
            st.divider()

            action = None
            for i, cand in enumerate(possible_candidates):
                name = cand["name"]
                conf = str(cand.get("confidence", "LOW") or "LOW").upper()
                evidence = str(cand.get("evidence", "") or "")
                cr_amt, dr_amt = _related_party_financials(name)
                if not (cr_amt or dr_amt):
                    cr_amt = cand.get("total_cr", 0)
                    dr_amt = cand.get("total_dr", 0)

                c1, c2, c3, c4, c5 = st.columns([3.2, 1.5, 1.7, 1.7, 2.1])
                c1.markdown(f"**{escape(name)}**")
                if evidence:
                    c1.caption(evidence[:100])
                c2.markdown(_manager_badge(conf), unsafe_allow_html=True)
                c3.write(_fmt_rm(cr_amt))
                c4.write(_fmt_rm(dr_amt))
                with c5:
                    confirm_col, dismiss_col = st.columns(2)
                    if confirm_col.button("Confirm", key=f"_cand_confirm_{i}", help="Add to known related parties", use_container_width=True):
                        action = ("confirm", i)
                    if dismiss_col.button("Dismiss", key=f"_cand_dismiss_{i}", help="Hide this candidate", use_container_width=True):
                        action = ("dismiss", i)

            if action:
                act, idx = action
                cand = possible_candidates[idx]
                candidate_names = {
                    str(cand.get("name") or "").strip().upper(),
                    str(cand.get("original_name") or "").strip().upper(),
                }
                candidate_names.discard("")
                if act == "confirm":
                    if not (
                        _is_report_unknown_counterparty(cand["name"])
                        or _is_report_special_counterparty_bucket(cand["name"])
                        or _is_own_party_name(cand["name"])
                    ):
                        confirmed_rps.append({
                            "name": cand["name"],
                            "relationship": "Analyst-confirmed - review required",
                            "confidence": str(cand.get("confidence", "") or "").upper(),
                        })
                        for candidate_name in candidate_names:
                            dismissed.discard(candidate_name)
                else:
                    dismissed.update(candidate_names)
                _save(confirmed_rps, dismissed)
                changed = True
                st.rerun()

    if changed:
        st.success("✅ Related party list updated — regenerate your reports to apply changes.")

    return changed


def detect_related_party_candidates(
    cp_ledger: dict,
    confirmed_names: set,
    dismissed: set,
    shared_report_data: dict = None,
    company_name: str = "",
) -> list:
    """
    Produce a ranked candidate list from Track 2 related-party signals only.

    This intentionally avoids UI-only rules such as circular flow or high
    outflow thresholds, so the manager matches the Track 2 engine.
    """
    _NOISE = {
        "UNKNOWN", "TRANSFER FEE", "OTHER TRANSFER FEE", "CHEQUE",
        "CASH DEPOSIT", "CASH WITHDRAWAL", "BANK FEES", "BULK SALARY",
        "FD/INTEREST", "LOAN REPAYMENT", "LOAN DISBURSEMENT", "KWSP",
        "SOCSO", "LHDN", "HRDF", "REVERSAL", "RETURNED CHEQUE",
        "INWARD RETURN", "JANM", "IBG RETURN",
    }
    candidates_by_name: dict = {}
    t2_cands = []
    confirmed_name_list = [
        str(name or "").strip() for name in (confirmed_names or set()) if name
    ]

    def _candidate_is_blocked(display_name: str) -> bool:
        name = str(display_name or "").strip().upper()
        return (
            not name
            or name in confirmed_names
            or name in dismissed
            or name in _NOISE
            or _report_name_matches_own_party(display_name, company_name)
            or _is_report_unknown_counterparty(display_name)
            or _is_report_special_counterparty_bucket(display_name)
            or any(
                _report_party_names_equivalent(display_name, confirmed_name)
                for confirmed_name in confirmed_name_list
            )
        )

    def _candidate_store_key(display_name: str) -> str:
        for existing_key, existing_candidate in candidates_by_name.items():
            if _report_party_names_equivalent(
                display_name,
                existing_candidate.get("name", existing_key),
            ):
                return existing_key
        return str(display_name or "").strip().upper()

    # ── Source 1: Track 2 candidates ──
    if isinstance(shared_report_data, dict):
        t2_cands = (
            shared_report_data.get("report_info", {}).get("related_party_candidates", []) or []
        )
        for c in t2_cands:
            if not isinstance(c, dict):
                continue
            display_name = str(c.get("name", "") or "").strip()
            if _candidate_is_blocked(display_name):
                continue
            name = _candidate_store_key(display_name)
            candidates_by_name[name] = {
                "name": display_name,
                "confidence": str(c.get("confidence", "MEDIUM")).upper(),
                "evidence": c.get("evidence", "Flagged by Track 2 engine"),
                "total_cr": safe_float(c.get("total_cr", 0)),
                "total_dr": safe_float(c.get("total_dr", 0)),
                "signals": c.get("signals", []),
                "debit_month_count": int(safe_float(c.get("debit_month_count", 0))),
                "source": "track2",
            }

    # Always supplement cached report candidates with live ledger scans. A
    # report_info candidate list can be stale/capped, and some parser regexes
    # keep the counterparty display name while the personal-keyword evidence
    # lives in the original transaction detail text.
    if _TRACK2_AVAILABLE:
        candidate_ledgers = []

        def _add_candidate_ledger(ledger):
            if not isinstance(ledger, dict) or not ledger.get("counterparties"):
                return
            if any(ledger is existing for existing in candidate_ledgers):
                return
            candidate_ledgers.append(ledger)

        _add_candidate_ledger(cp_ledger)
        if isinstance(shared_report_data, dict) and shared_report_data.get("transactions"):
            try:
                _add_candidate_ledger(
                    build_track2_counterparty_ledger(shared_report_data.get("transactions", []))
                )
            except Exception:
                pass

        for candidate_ledger in candidate_ledgers:
            try:
                live_candidates = advisory_rp_candidates(
                    scan_related_party_candidates(candidate_ledger),
                    list(confirmed_names or []),
                )
            except Exception:
                continue
            for c in live_candidates:
                if not isinstance(c, dict):
                    continue
                display_name = str(c.get("name", "") or "").strip()
                if _candidate_is_blocked(display_name):
                    continue
                name = _candidate_store_key(display_name)
                candidates_by_name[name] = {
                    "name": display_name,
                    "confidence": str(c.get("confidence", "MEDIUM")).upper(),
                    "evidence": c.get("evidence", "Flagged by Track 2 engine"),
                    "total_cr": safe_float(c.get("total_cr", 0)),
                    "total_dr": safe_float(c.get("total_dr", 0)),
                    "signals": c.get("signals", []),
                    "debit_month_count": int(safe_float(c.get("debit_month_count", 0))),
                    "source": "track2",
                }

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    priority_signals = {"monthly_recurrence", "personal_keyword_sweep"}
    return sorted(
        candidates_by_name.values(),
        key=lambda x: (
            0 if any(sig in (x.get("signals") or []) for sig in priority_signals) else 1,
            -int(x.get("debit_month_count") or 0),
            order.get(x["confidence"], 3),
            -x["total_dr"],
            str(x.get("name", "")).casefold(),
        ),
    )[:40]


def partition_related_party_candidates_for_manager(candidates: list) -> tuple[list, list]:
    """Split RP candidates for the Streamlit manager.

    HIGH confidence is treated as known. MEDIUM/LOW remain possible until the
    analyst confirms them.
    """
    known = []
    possible = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        confidence = str(candidate.get("confidence") or candidate.get("status") or "").upper()
        if confidence == "HIGH":
            known.append(candidate)
        elif confidence in {"MEDIUM", "LOW"}:
            possible.append(candidate)
    return known, possible


def _align_related_party_candidates_to_counterparty_rows(
    candidates: list,
    counterparty_rows: List[dict],
) -> list:
    """Use the visible Counterparty Ledger name for manager-facing RP rows."""
    aligned = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        item = dict(candidate)
        original_name = _report_party_display_name(item)
        match = _counterparty_row_for_related_party_name(
            original_name,
            counterparty_rows,
            total_cr=item.get("total_cr", 0),
            total_dr=item.get("total_dr", 0),
        )
        ledger_name = _related_party_counterparty_row_name(match) if match else ""
        if ledger_name and ledger_name.upper() != original_name.upper():
            item["original_name"] = original_name
            item["name"] = ledger_name
        aligned.append(item)

    merged: list[dict] = []
    for item in aligned:
        existing_index = next(
            (
                idx for idx, existing in enumerate(merged)
                if _report_party_names_equivalent(item.get("name"), existing.get("name"))
            ),
            None,
        )
        if existing_index is None:
            merged.append(item)
            continue

        existing = merged[existing_index]
        confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        merged_signals = list(existing.get("signals") or [])
        for signal in item.get("signals") or []:
            if signal not in merged_signals:
                merged_signals.append(signal)
        combined = {**existing, **item}
        combined["signals"] = merged_signals
        combined["total_cr"] = max(safe_float(existing.get("total_cr", 0)), safe_float(item.get("total_cr", 0)))
        combined["total_dr"] = max(safe_float(existing.get("total_dr", 0)), safe_float(item.get("total_dr", 0)))
        combined["debit_month_count"] = max(
            int(safe_float(existing.get("debit_month_count", 0))),
            int(safe_float(item.get("debit_month_count", 0))),
        )
        if confidence_order.get(str(existing.get("confidence") or "").upper(), 9) < confidence_order.get(
            str(item.get("confidence") or "").upper(), 9
        ):
            combined["confidence"] = existing.get("confidence")
        merged[existing_index] = combined

    return merged


def _counterparty_row_for_related_party_name(
    party_name: str,
    counterparty_rows: List[dict],
    *,
    total_cr=None,
    total_dr=None,
) -> Optional[dict]:
    display_name = _report_party_display_name(party_name)
    if not display_name:
        return None

    amount_hint = safe_float(total_cr) or safe_float(total_dr)
    matches = []
    for row in counterparty_rows or []:
        row_name = _related_party_counterparty_row_name(row)
        if (
            not row_name
            or _is_report_unknown_counterparty(row_name)
            or _is_report_special_counterparty_bucket(row_name)
        ):
            continue

        score = -1
        if _report_party_identity_names_match(row_name, display_name):
            score = 4
        elif _counterparty_row_matches_report_party_name(row, display_name):
            score = 3
        elif _counterparty_row_matches_report_party(row, display_name):
            score = 2
        if score < 0:
            continue

        amount_score = 0
        if amount_hint:
            if round(safe_float(row.get("total_credits", 0)), 2) == round(safe_float(total_cr), 2):
                amount_score += 1
            if round(safe_float(row.get("total_debits", 0)), 2) == round(safe_float(total_dr), 2):
                amount_score += 1

        matches.append((
            score,
            amount_score,
            int(safe_float(row.get("transaction_count", 0))),
            len(row_name),
            row_name.casefold(),
            row,
        ))

    if not matches:
        return None
    return max(matches, key=lambda item: item[:-1])[-1]


def _related_party_counterparty_row_name(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    return re.sub(
        r"\s+",
        " ",
        str(
            row.get("counterparty_name")
            or row.get("party_name")
            or row.get("counterparty")
            or row.get("party")
            or ""
        ).strip(),
    )


__all__ = [
    'bind_app_globals',
    'render_related_party_manager',
    'detect_related_party_candidates',
    'partition_related_party_candidates_for_manager',
    '_align_related_party_candidates_to_counterparty_rows',
    '_counterparty_row_for_related_party_name',
    '_related_party_counterparty_row_name',
]
