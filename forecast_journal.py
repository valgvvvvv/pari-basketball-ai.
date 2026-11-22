"""Append-only research records. Never infer finals from the last live score."""
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from result_validation import settle_prediction, validate_final_evidence


def path():
    return Path(os.environ.get('FORECAST_JOURNAL_PATH', str(Path(tempfile.gettempdir()) / 'pari_forecasts.sqlite3')))


def connect():
    location = path()
    location.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(location, timeout=20)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS forecasts (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL, confirmed_at TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_outcome_per_forecast ON outcomes(forecast_id)')
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, payload_z BLOB NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS labels (forecast_id TEXT PRIMARY KEY, actual INTEGER NOT NULL, payload TEXT NOT NULL)')
    for table in ('forecasts', 'outcomes', 'snapshots', 'labels'):
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS no_{operation.lower()}_{table} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable research record'); END")
    db.commit()
    return db


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def record(evaluations, source_hash):
    """Keep every evaluated line, including shadow; never only the displayed top three."""
    db = connect()
    try:
        for row in evaluations:
            payload = {**row, 'model_source_sha256': source_hash, 'journal_schema': 1}
            raw = payload.pop('source_event_snapshot', {})
            raw_hash = digest(raw)
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?)', (raw_hash, zlib.compress(packed(raw).encode(), 6)))
            payload['source_snapshot_sha256'] = raw_hash
            identity = digest(payload)
            db.execute('INSERT OR IGNORE INTO forecasts VALUES (?,?,?,?)',
                       (identity, str(row['event_id']), row['captured_at_utc'], packed(payload)))
        db.commit()
    finally:
        db.close()


def export_rows():
    db = connect()
    try:
        rows = []
        for identity, payload, outcome, label in db.execute(
            'SELECT f.id,f.payload,o.payload,l.payload FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id LEFT JOIN labels l ON l.forecast_id=f.id ORDER BY f.captured_at,f.id'):
            row = dict(id=identity, **json.loads(payload), outcome=json.loads(outcome) if outcome else None)
            row['confirmed_regulation_final'] = json.loads(label) if label else None
            raw = db.execute('SELECT payload_z FROM snapshots WHERE id=?', (row['source_snapshot_sha256'],)).fetchone()
            row['source_event_snapshot'] = json.loads(zlib.decompress(raw[0])) if raw else None
            rows.append(row)
        return rows
    finally:
        db.close()


def confirm(forecast_id, final):
    """Local/import operation only; public clients cannot submit or replace results."""
    db = connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT payload FROM forecasts WHERE id=?', (forecast_id,)).fetchone()
        if not found:
            raise ValueError('Unknown forecast ID')
        row = json.loads(found[0])
        decision = row['decision']
        target = {'type': 'match'} if decision['target'] == 'match' else {'type': 'period', 'number': decision['period']}
        market = decision.get('market') or {}
        invalid = validate_final_evidence({'event_id': row['event_id'], 'snapshot_id': row['snapshot_id']}, final)
        if invalid:
            return invalid
        known_rule = market.get('rulesConfirmed') is True and bool(market.get('ruleSource')) and isinstance(market.get('includesOvertime'), bool)
        if known_rule and final.get('includes_overtime') is not market['includesOvertime']:
            raise ValueError('Result market rule differs from captured prediction rule')
        if known_rule and target['type'] == 'period' and market['includesOvertime'] is not False:
            raise ValueError('Overtime-inclusive period settlement is not implemented')
        if final.get('line') != decision['line']:
            raise ValueError('Result line differs from captured prediction line')
        actual = final.get('regulation_total' if target['type'] == 'match' else 'period_total')
        if target['type'] == 'period' and (final.get('period_number') != target['number'] or final.get('includes_overtime') is not False):
            return {'status': 'unresolved', 'reason': 'Exact regulation-period result is not confirmed'}
        label_ready = isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual) and actual >= 0 and float(actual).is_integer()
        if label_ready:
            previous = db.execute('SELECT actual FROM labels WHERE forecast_id=?', (forecast_id,)).fetchone()
            if previous and previous[0] != actual:
                raise ValueError('Conflicting independently confirmed regulation score')
            db.execute('INSERT OR IGNORE INTO labels VALUES (?,?,?)', (forecast_id, int(actual), packed(final)))
        if not known_rule:
            db.commit()
            return {'status': 'labeled' if label_ready else 'unresolved', 'market_settlement': 'unresolved',
                    'reason': 'Prediction-time market rule missing; point accuracy is separate from betting settlement'}
        result = settle_prediction({'event_id': row['event_id'], 'snapshot_id': row['snapshot_id'],
            'target': target, 'market': {'lines': [{'line': decision['line']}]}}, final)
        if result['status'] != 'settled':
            db.commit()
            return result
        odds = (decision.get('comparison') or {}).get('odds')
        win = result['result'] == ('over_won' if decision['direction'] == 'ТБ' else 'under_won')
        push = result['result'] == 'push'
        pnl = 0 if push else (odds - 1 if win else -1) if isinstance(odds, (int, float)) and math.isfinite(odds) and odds > 1 else None
        payload = {'settlement': result, 'source_record': final, 'win': win, 'push': push,
                   'virtual_unit_pnl': pnl, 'status': decision['status']}
        identity = digest({'forecast_id': forecast_id, 'result': payload})
        if db.execute('SELECT 1 FROM outcomes WHERE forecast_id=? AND id<>?', (forecast_id, identity)).fetchone():
            raise ValueError('Conflicting confirmation: retain original and investigate separately')
        db.execute('INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?)',
                   (identity, forecast_id, final.get('confirmed_at_utc') or datetime.now(timezone.utc).isoformat(), packed(payload)))
        db.commit()
        return payload
    finally:
        db.close()


def status():
    db = connect()
    try:
        counts = dict(zip(('forecasts', 'games', 'confirmed', 'confirmed_games'), db.execute(
            'SELECT (SELECT count(*) FROM forecasts),(SELECT count(DISTINCT event_id) FROM forecasts),(SELECT count(DISTINCT forecast_id) FROM outcomes),(SELECT count(DISTINCT f.event_id) FROM labels l JOIN forecasts f ON f.id=l.forecast_id)').fetchone()))
        return {**counts, 'persistent': False,
                'storage': 'configure FORECAST_JOURNAL_PATH on a persistent disk; default is ephemeral; custom path alone does not verify persistence',
                'calibrated': False, 'automatic_priority_enabled': False,
                'readiness': {
                    'independent_results': 'missing' if counts['confirmed_games'] == 0 else 'collected_not_yet_validated',
                    'calibrator': 'not_trained', 'independent_edge_test': 'not_completed',
                    'match_overtime_model': 'not_implemented',
                    'automatic_final_verification': False,
                    'sample_size_alone_enables_signals': False,
                    'next_step': 'Collect independently verified finals, train/calibrate on earlier games, and evaluate on untouched later games. Regulation and overtime targets must stay separate.'},
                'reason': 'No independently validated calibrator/edge artifact for the current model'}
    finally:
        db.close()
