"""Telegram observation results, separate from validated betting/model outcomes.

The original decision and text never change. Only result evidence and delivery
receipts are appended. A missing live event never means that a game finished.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def now_ts():
    return datetime.now(timezone.utc).timestamp()


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def connect():
    path = Path(os.environ.get('SIGNAL_LIFECYCLE_PATH', str(Path(tempfile.gettempdir())/'pari_signal_lifecycle.sqlite3')))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, message_id INTEGER NOT NULL, sent_at REAL NOT NULL, payload TEXT NOT NULL, result TEXT, edited_hash TEXT)')
    # One Telegram message may now contain several immutable forecast receipts.
    db.execute('BEGIN IMMEDIATE')
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()[0]
    if 'UNIQUE' in schema.upper():
        db.execute('ALTER TABLE messages RENAME TO messages_legacy')
        db.execute('CREATE TABLE messages (id TEXT PRIMARY KEY, message_id INTEGER NOT NULL, sent_at REAL NOT NULL, payload TEXT NOT NULL, result TEXT, edited_hash TEXT)')
        db.execute('INSERT INTO messages SELECT * FROM messages_legacy')
        db.execute('DROP TABLE messages_legacy')
    db.commit()
    db.execute('CREATE TABLE IF NOT EXISTS reports (cutoff REAL PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS evidence (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO settings VALUES ('started_at', ?)", (str(now_ts()),))
    db.commit()
    return db


def close(db):
    db.close()


def origin(event):
    keys = ('id', 'team1', 'team2', 'start_time', 'league', 'period_count', 'period_length_seconds')
    return {key: event.get(key) for key in keys if key in event}


def register(card, message_id, text, sent_at=None):
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        raise ValueError('A Telegram message receipt is required')
    s = card['signal']
    if (s.get('target') not in ('match', 'quarter', 'half') or s.get('direction') not in ('ТБ', 'ТМ')
            or isinstance(s.get('line'), bool) or not isinstance(s.get('line'), (int, float))
            or not math.isfinite(s['line']) or s['line'] < 0):
        raise ValueError('Invalid saved signal')
    payload = {'event': str(card['event']), 'teams': card['teams'], 'signal': s,
               'original_text': text, 'origin': card.get('origin', {}), 'update': bool(card.get('update'))}
    db = connect()
    try:
        db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,NULL,NULL)',
                   (card['forecast_id'], message_id, sent_at or now_ts(), pack(payload)))
        db.commit()
    finally:
        db.close()


def rows():
    db = connect()
    try:
        return [dict(id=i, message_id=m, sent_at=t, payload=json.loads(p),
                     result=json.loads(r) if r else None, edited_hash=h)
                for i, m, t, p, r, h in db.execute('SELECT * FROM messages ORDER BY sent_at,id')]
    finally:
        db.close()


def checked_pair(pair):
    return (isinstance(pair, list) and len(pair) == 2 and
            all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and 0 <= v <= 400 and float(v).is_integer() for v in pair))


def completed_period(row, event):
    """A later, coherent primary PARI snapshot confirms a previous period only."""
    p, s = row['payload'], row['payload']['signal']
    if s['target'] == 'match' or str(event.get('id')) != p['event']:
        return None
    try:
        observed = stamp(event.get('feed_checked_at_utc'))
        if not 0 <= now_ts()-observed <= 90 or observed < row['sent_at']-5:
            return None
    except (ValueError, TypeError):
        return None
    o = p.get('origin', {})
    if any(o.get(k) and o[k] != event.get(k) for k in ('team1', 'team2')):
        return None
    count, current, number = event.get('period_count'), event.get('period'), s.get('period')
    periods, score = event.get('period_scores'), event.get('score')
    if (count not in (2, 4) or (s['target'] == 'half' and count != 2)
            or (s['target'] == 'quarter' and count != 4)
            or not isinstance(number, int) or not 1 <= number <= count
            or not isinstance(current, int) or current <= number
            or not checked_pair(score) or not isinstance(periods, list)
            or len(periods) < current or not all(checked_pair(q) for q in periods[:current])
            or event.get('period_score_conflicts') or event.get('score_conflict')):
        return None
    if [sum(q[j] for q in periods[:current]) for j in (0, 1)] != score:
        return None
    return {'score': periods[number-1], 'source': 'PARI completed period in a later live snapshot',
            'source_url': 'https://pari-basketball-ai.onrender.com/api/pari/live',
            'observed_at': event.get('feed_checked_at_utc'), 'basis': 'primary_completed_period'}


def result_for(row, evidence):
    if not evidence or not checked_pair(evidence.get('score')):
        return None
    actual = sum(evidence['score'])
    s = row['payload']['signal']
    state = 'push' if actual == s['line'] else ('won' if (actual > s['line']) == (s['direction'] == 'ТБ') else 'lost')
    return {'state': state, 'total': actual, 'score': evidence['score'],
            'target': s['target'], 'period': s.get('period'), 'line': s['line'],
            'evidence': evidence, 'checked_at': now_ts()}


def save_result(identity, result):
    db = connect()
    try:
        old = db.execute('SELECT result FROM messages WHERE id=?', (identity,)).fetchone()
        if old and old[0]:
            previous = json.loads(old[0])
            if previous.get('state') == 'disputed':
                return
            # A correction is visible rather than silently rewriting an old win.
            if previous.get('total') != result.get('total'):
                result = {'state': 'disputed', 'previous': previous, 'correction': result,
                          'checked_at': now_ts()}
            elif previous.get('evidence', {}).get('basis') == 'primary_completed_period' and result.get('evidence', {}).get('basis') in ('final_protocol_corroborated_by_primary', 'primary_explicit_finished_protocol'):
                pass
            else:
                return
        db.execute('UPDATE messages SET result=? WHERE id=?', (pack(result), identity))
        db.commit()
    finally:
        db.close()


def observe(events):
    by_id = {str(e.get('id')): e for e in events}
    db = connect()
    try:
        for event in events:
            score, periods = event.get('score'), event.get('period_scores')
            if (checked_pair(score) and isinstance(periods, list) and periods
                    and all(checked_pair(q) for q in periods)
                    and [sum(q[j] for q in periods) for j in (0, 1)] == score
                    and not event.get('period_score_conflicts')):
                snapshot = {**origin(event), **{k: event.get(k) for k in
                    ('score', 'period_scores', 'period', 'feed_checked_at_utc')}}
                db.execute('INSERT OR REPLACE INTO evidence VALUES (?,?)', (str(event['id']), pack(snapshot)))
        db.commit()
    finally:
        db.close()
    for row in rows():
        event = by_id.get(row['payload']['event'])
        if event:
            result = result_for(row, completed_period(row, event))
            if result:
                save_result(row['id'], result)


def edited_text(row):
    result = row.get('result')
    if not result:
        return None
    state = result['state']
    marks = {'won': '✅ ЗАШЁЛ', 'lost': '❌ НЕ ЗАШЁЛ', 'push': '↔️ ВОЗВРАТ',
             'disputed': '⚠️ РЕЗУЛЬТАТ ТРЕБУЕТ ПЕРЕПРОВЕРКИ'}
    text = clean_text(row['payload']['original_text']).removesuffix('\n⏳ Ожидает подтверждения результата.')
    suffix = '\n\n' + marks[state]
    if state != 'disputed':
        suffix += '\nИтог цели: ' + ':'.join(map(str, result['score'])) + f" · тотал {result['total']}"
        if row['payload']['signal']['target'] == 'match':
            suffix += '\nПроверено основное время, без ОТ; это исход прогноза, не расчёт ставки с ОТ.'
        suffix += '\nИсточник: ' + result['evidence']['source']
    return text + suffix


def clean_text(text):
    removed = {'Рынок совместим; преимущество не проверено.',
               'Изменение линии — обновление наблюдения, а не новый вход.',
               'Предварительное направление. Вероятность и преимущество не подтверждены независимой проверкой.',
               'https://pari-basketball-ai.onrender.com/'}
    lines = [line for line in text.splitlines() if line.strip() not in removed]
    while '\n\n\n' in '\n'.join(lines):
        lines = '\n'.join(lines).replace('\n\n\n', '\n\n').splitlines()
    return '\n'.join(lines).strip()


def group_text(records):
    """Render a message from immutable forecast rows, never erase old lines."""
    records = sorted(records, key=lambda row: (row['sent_at'], row['id']))
    if len(records) == 1:
        return edited_text(records[0]) or clean_text(records[0]['payload']['original_text'])
    first = records[0]['payload']
    header = '🔔 Сигнал · экспериментальный прогноз\n' + first['teams']
    marks = {'won': '✅ ЗАШЁЛ', 'lost': '❌ НЕ ЗАШЁЛ', 'push': '↔️ ВОЗВРАТ',
             'disputed': '⚠️ Перепроверка', 'pending': '⏳'}
    lines = []
    for row in records:
        s, result = row['payload']['signal'], row.get('result') or {}
        target = 'Матч · основное время' if s['target'] == 'match' else ('Тайм ' if s['target'] == 'half' else 'Четверть Q')+str(s['period'])
        at = datetime.fromtimestamp(row['sent_at'], MSK).strftime('%H:%M')
        line = f"{at} · {target} · {s['direction']} {s['line']:g} · {marks[result.get('state', 'pending')]}"
        if 'total' in result:
            line += f" · итог {result['total']}"
        lines.append(line)
    tail = ''
    if any('overtime' in r['payload']['signal'].get('reasonCodes', []) for r in records):
        tail = '\n\nМатч рассчитан без ОТ; для рынка с ОТ не применять.'
    # Telegram limits text to 4096 characters. Full history remains in the
    # lifecycle checkpoint and daily report even when a long match is condensed.
    shown = list(lines)
    omitted = 0
    while len(header+'\n\n'+'\n'.join(shown)+tail) > 3700 and len(shown) > 2:
        shown.pop(1)
        omitted += 1
    note = f'\nЕщё {omitted} изменений сохранено в отчёте.' if omitted else ''
    return header+'\n\n'+'\n'.join(shown)+note+tail


def edit_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def mark_edited(identity, text):
    db = connect()
    try:
        db.execute('UPDATE messages SET edited_hash=? WHERE id=?', (edit_hash(text), identity))
        db.commit()
    finally:
        db.close()


def latest_cutoff(now=None):
    local = (now or datetime.now(MSK)).astimezone(MSK)
    end = local.replace(hour=23, minute=0, second=0, microsecond=0)
    return end if local >= end else end-timedelta(days=1)


def report_text(end, records=None):
    end = end.astimezone(MSK)
    start = end-timedelta(hours=24)
    selected = [r for r in (rows() if records is None else records) if start.timestamp() <= r['sent_at'] < end.timestamp()]
    counts = {k: 0 for k in ('won', 'lost', 'push', 'pending', 'disputed')}
    for row in selected:
        counts[(row.get('result') or {}).get('state', 'pending')] += 1
    denominator = counts['won']+counts['lost']
    rate = f"{100*counts['won']/denominator:.1f}%" if denominator else 'нет рассчитанных исходов'
    lines = [f"📊 Прогнозы за 24 часа: {start:%d.%m %H:%M} — {end:%d.%m %H:%M} МСК",
        f"Сообщений: {len({r['message_id'] for r in selected})} · прогнозов: {len(selected)} · матчей: {len({r['payload']['event'] for r in selected})}",
        f"Из них обновлений: {sum(bool(r['payload'].get('update')) for r in selected)}",
        f"✅ Зашли: {counts['won']} · ❌ Не зашли: {counts['lost']} · ↔️ Возвраты: {counts['push']}",
        f"⏳ Ожидают подтверждения: {counts['pending']} · ⚠️ Спорные: {counts['disputed']}",
        'Проход среди подтверждённых без возвратов: '+rate,
        'Каждая строка относится к исходной линии. Обновления одного матча зависимы; это не прибыль.']
    db = connect()
    try:
        started = float(db.execute("SELECT value FROM settings WHERE key='started_at'").fetchone()[0])
    finally:
        db.close()
    if start.timestamp() < started < end.timestamp():
        lines.append('Первый отчёт: история до включения автоучёта может быть неполной.')
    marks = {'won': '✅', 'lost': '❌', 'push': '↔️', 'pending': '⏳', 'disputed': '⚠️'}
    for row in selected:
        p, result = row['payload'], row.get('result') or {}
        s = p['signal']
        target = 'Матч без ОТ' if s['target'] == 'match' else ('Тайм ' if s['target'] == 'half' else 'Q')+str(s['period'])
        total = f" → {result['total']}" if 'total' in result else ''
        lines.append(f"{marks[result.get('state', 'pending')]} {p['teams']} · {target} {s['direction']} {s['line']:g}{total}")
    if not selected:
        lines.append('В этом окне отправленных прогнозов не было.')
    return '\n'.join(lines)


def chunks(text, limit=3700):
    parts, current = [], ''
    for line in text.splitlines():
        for offset in range(0, max(1, len(line)), limit):
            piece = line[offset:offset+limit]
            if len(current)+len(piece)+1 > limit:
                parts.append(current); current = ''
            current += ('\n' if current else '')+piece
    if current:
        parts.append(current)
    return parts


def report_state(cutoff):
    db = connect()
    try:
        result = db.execute('SELECT payload FROM reports WHERE cutoff=?', (cutoff,)).fetchone()
        if result:
            return json.loads(result[0])
        started = float(db.execute("SELECT value FROM settings WHERE key='started_at'").fetchone()[0])
        if cutoff < started:
            return None
        state = {'chunks': chunks(report_text(datetime.fromtimestamp(cutoff, MSK))), 'sent': []}
        db.execute('INSERT INTO reports VALUES (?,?)', (cutoff, pack(state)))
        db.commit()
        return state
    finally:
        db.close()


def report_receipt(cutoff, state):
    db = connect()
    try:
        db.execute('INSERT OR REPLACE INTO reports VALUES (?,?)', (cutoff, pack(state)))
        db.commit()
    finally:
        db.close()


def export_state():
    db = connect()
    try:
        recent = [row for row in rows() if row['sent_at'] >= now_ts()-7*86400]
        events = {row['payload']['event'] for row in recent}
        return {'schema': 1, 'rows': recent,
                'reports': {str(k): json.loads(v) for k, v in db.execute('SELECT * FROM reports') if k >= now_ts()-8*86400},
                'evidence': {k: json.loads(v) for k, v in db.execute('SELECT * FROM evidence') if k in events},
                'started_at': float(db.execute("SELECT value FROM settings WHERE key='started_at'").fetchone()[0])}
    finally:
        db.close()


def import_state(state):
    if not state:
        return
    if state.get('schema') != 1 or len(state.get('rows', [])) > 10000:
        raise ValueError('Unsupported checkpoint')
    for row in state.get('rows', []):
        p = row['payload']
        register({**p, 'forecast_id': row['id']}, row['message_id'], p['original_text'], row['sent_at'])
    db = connect()
    try:
        for row in state.get('rows', []):
            p = row['payload']
            # Immutable ID collisions must never overwrite the original text.
            old = db.execute('SELECT payload FROM messages WHERE id=?', (row['id'],)).fetchone()
            if old and old[0] != pack(p):
                raise ValueError('Conflicting original decision')
            if row.get('result'):
                current = db.execute('SELECT result FROM messages WHERE id=?', (row['id'],)).fetchone()
                if current and not current[0]:
                    db.execute('UPDATE messages SET result=?,edited_hash=? WHERE id=?',
                        (pack(row['result']), row.get('edited_hash'), row['id']))
        for cutoff, report in state.get('reports', {}).items():
            old = db.execute('SELECT payload FROM reports WHERE cutoff=?', (float(cutoff),)).fetchone()
            if not old or len(json.loads(old[0])['sent']) < len(report['sent']):
                db.execute('INSERT OR REPLACE INTO reports VALUES (?,?)', (float(cutoff), pack(report)))
        for key, value in state.get('evidence', {}).items():
            db.execute('INSERT OR IGNORE INTO evidence VALUES (?,?)', (str(key), pack(value)))
        if state.get('started_at'):
            db.execute("UPDATE settings SET value=CAST(MIN(CAST(value AS REAL),?) AS TEXT) WHERE key='started_at'", (float(state['started_at']),))
        db.commit()
    finally:
        db.close()
