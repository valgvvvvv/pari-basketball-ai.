"""Conservative settlement of archived basketball total predictions.

This module never guesses a final score or overtime rule. It settles only a
confirmed, identity-matched final record with the exact market settlement rule.
"""
from __future__ import annotations

from typing import Any


def settle_prediction(contract: dict[str, Any], final_record: dict[str, Any] | None) -> dict[str, Any]:
    event_id = str(contract.get("event_id") or "")
    snapshot_id = str(contract.get("snapshot_id") or "")
    target = contract.get("target") if isinstance(contract.get("target"), dict) else {}
    if not isinstance(final_record, dict):
        return {"status": "unresolved", "reason": "final result is missing",
                "event_id": event_id, "snapshot_id": snapshot_id}
    final_event_id = str(final_record.get("event_id") or "")
    if not event_id or final_event_id != event_id:
        return {"status": "disputed", "reason": "final result event ID does not exactly match prediction",
                "event_id": event_id, "snapshot_id": snapshot_id}
    if final_record.get("score_conflict") or final_record.get("identity_conflict"):
        return {"status": "disputed", "reason": "source reports an identity or score conflict",
                "event_id": event_id, "snapshot_id": snapshot_id}
    if final_record.get("final_status") not in {"finished", "completed", "final"}:
        return {"status": "unresolved", "reason": "event is not confirmed final",
                "event_id": event_id, "snapshot_id": snapshot_id}
    if final_record.get("confirmed") is not True or final_record.get("identity_verified") is not True or final_record.get("independent_confirmation") is not True:
        return {"status": "unresolved", "reason": "final score or event identity is not independently confirmed by a second source",
                "event_id": event_id, "snapshot_id": snapshot_id}
    source = str(final_record.get("source") or "").strip()
    if not source:
        return {"status": "unresolved", "reason": "final result source is missing",
                "event_id": event_id, "snapshot_id": snapshot_id}

    target_type = target.get("type")
    if target_type == "match":
        includes_overtime = final_record.get("includes_overtime")
        if not isinstance(includes_overtime, bool) or final_record.get("market_rule_confirmed") is not True:
            return {"status": "unresolved", "reason": "market overtime settlement rule is not independently confirmed",
                    "event_id": event_id, "snapshot_id": snapshot_id}
        total_key = "total_with_overtime" if includes_overtime else "regulation_total"
        total = final_record.get(total_key)
    elif target_type == "period":
        expected_period = target.get("number")
        actual_period = final_record.get("period_number")
        if expected_period is None or actual_period != expected_period:
            return {"status": "disputed", "reason": "final period number does not match prediction target",
                    "event_id": event_id, "snapshot_id": snapshot_id}
        total = final_record.get("period_total")
        includes_overtime = False
    else:
        return {"status": "unresolved", "reason": "unsupported prediction target",
                "event_id": event_id, "snapshot_id": snapshot_id}

    try:
        total = float(total)
        line = float(final_record.get("line"))
    except (TypeError, ValueError):
        return {"status": "unresolved", "reason": "confirmed final total or market line is missing",
                "event_id": event_id, "snapshot_id": snapshot_id}
    market = contract.get("market") if isinstance(contract.get("market"), dict) else {}
    market_lines = market.get("lines") if isinstance(market.get("lines"), list) else []
    captured_lines = []
    for row in market_lines:
        if not isinstance(row, dict) or row.get("line") is None:
            continue
        try:
            captured_lines.append(float(row["line"]))
        except (TypeError, ValueError):
            continue
    if not any(abs(captured - line) < 1e-9 for captured in captured_lines):
        return {"status": "unresolved", "reason": "settlement line was not captured in this prediction snapshot",
                "event_id": event_id, "snapshot_id": snapshot_id}
    if total < 0 or line < 0 or not total.is_integer():
        return {"status": "disputed", "reason": "negative total or line is invalid",
                "event_id": event_id, "snapshot_id": snapshot_id}
    if total > line:
        result = "over_won"
    elif total < line:
        result = "under_won"
    else:
        result = "push"
    return {
        "status": "settled",
        "result": result,
        "final_total": int(total) if total.is_integer() else total,
        "line": line,
        "includes_overtime": includes_overtime,
        "source": source,
        "confirmed_at_utc": final_record.get("confirmed_at_utc"),
        "event_id": event_id,
        "snapshot_id": snapshot_id,
    }
