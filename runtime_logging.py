"""Compact, levelled service logs; stderr is reserved for warnings/errors."""
import json
import logging
import os
import sys
import time

_LAST_DIAGNOSTIC = {}


class _BelowWarning(logging.Filter):
    def filter(self, record):
        return record.levelno < logging.WARNING


def configure_service_logging():
    # Uvicorn configures handlers before importing server:app. Its default
    # startup logger writes INFO to stderr too. Do not change error severity.
    logger = logging.getLogger("uvicorn.error")
    if getattr(logger, "_pari_configured", False):
        return
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    standard = logging.StreamHandler(sys.stdout)
    standard.setFormatter(formatter)
    standard.addFilter(_BelowWarning())
    errors = logging.StreamHandler(sys.stderr)
    errors.setLevel(logging.WARNING)
    errors.setFormatter(formatter)
    logger.handlers = [standard, errors]
    logger.propagate = False
    logger._pari_configured = True


def emit_diagnostic(event, payload, *, level=None):
    if not isinstance(payload, dict):
        payload = {"items": payload if os.getenv("PARI_DIAGNOSTICS_VERBOSE") == "1" else len(payload)}
    if level is None:
        level = "error" if event.endswith("_ERROR") or payload.get("ok") is False or payload.get("error") else "info"
        if event.startswith("PARI_STATS_") and level == "error":
            level = "warning"
    verbose = os.getenv("PARI_DIAGNOSTICS_VERBOSE", "0") == "1"
    if level == "info" and event == "PARI_LIVE_DIAG":
        now = time.monotonic()
        if now - _LAST_DIAGNOSTIC.get(event, float("-inf")) < 60:
            return
        _LAST_DIAGNOSTIC[event] = now
    if not verbose:
        keys = ("ok", "connected", "host", "total_events", "basketball_events", "checked_at", "checked_at_utc",
                "events", "saved", "error", "diagnostic_version", "candidate_count", "items")
        compact = {k: payload[k] for k in keys if k in payload and not isinstance(payload[k], (list, dict))}
        if isinstance(payload.get("events"), list):
            compact["events"] = len(payload["events"])
            compact["events_with_statistics"] = sum(bool(e.get("statistics_fields")) for e in payload["events"] if isinstance(e, dict))
        enrichment = payload.get("live_score_source")
        if isinstance(enrichment, dict):
            compact["enrichment"] = {k: enrichment[k] for k in ("status", "matched", "statistics_matches") if k in enrichment}
        attempts = payload.get("attempts")
        if isinstance(attempts, list):
            compact["failed_attempts"] = sum(bool(a.get("error")) for a in attempts if isinstance(a, dict))
            if level != "info":
                compact["attempts"] = [{k: a[k] for k in ("host", "status", "error") if k in a} for a in attempts if isinstance(a, dict)]
        payload = compact
    print(json.dumps({"level": level, "event": event, **payload}, ensure_ascii=False, default=str),
          file=sys.stderr if level in ("warning", "error", "critical") else sys.stdout, flush=True)
