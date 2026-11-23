"""Owner-only Telegram delivery for the in-process priority detector."""
import asyncio
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile

import httpx

logging.getLogger('httpx').setLevel(logging.WARNING)
STATE_PATH = Path(os.environ.get('SIGNAL_STATE_PATH', str(Path(tempfile.gettempdir()) / 'pari_signal_state.sqlite3')))
STATUS = {'mode': 'server', 'interval_seconds': 30, 'checked_at_utc': None,
          'cards': [], 'error': None, 'sent': 0, 'commands_checked_at_utc': None}
LOCK = asyncio.Lock()


def enabled():
    return (os.environ.get('SIGNAL_SERVER_ENABLED') == '1' and
            bool(os.environ.get('SIGNAL_BOT_TOKEN')) and
            os.environ.get('SIGNAL_CHAT_ID', '').lstrip('-').isdigit())


def experimental_enabled():
    return os.environ.get('SIGNAL_EXPERIMENTAL_ENABLED') == '1'


def notification_mode():
    return 'experimental' if experimental_enabled() else 'confirmed_only'


def experimental_cards(result):
    """Opted-in research watchlist, without promoting it to validated priority."""
    if not experimental_enabled():
        return []
    selected = []
    blocked = {'data', 'window', 'preliminary', 'probabilities', 'suspended',
               'rules', 'overtime', 'prices', 'quote'}
    for card in result.get('researchCandidates', []):
        signal = card.get('signal', {})
        robust = signal.get('robustness') or {}
        probability = robust.get('probabilityMin')
        if (signal.get('marketReady') is not True or
                signal.get('watchlist') is not True or
                robust.get('available') is not True or robust.get('directionStable') is not True or
                isinstance(probability, bool) or not isinstance(probability, (int, float)) or
                not math.isfinite(probability) or not .65 <= probability <= 1 or
                blocked.intersection(signal.get('reasonCodes', []))):
            continue
        selected.append({**card, 'experimental': True})
    return sorted(selected, key=lambda card: (-card['signal']['robustness']['probabilityMin'], str(card['event'])))[:3]


def message_for(card):
    if not card.get('experimental'):
        return ('🔔 Сигнал\n' + card['teams'] + '\n' + card['label'] + '\n' + card['context'] +
                '\n\nДетектор сервера · оценка модели не калибрована.\nhttps://pari-basketball-ai.onrender.com/')
    signal = card['signal']
    target = 'Матч · основное время' if signal['target'] == 'match' else 'Четверть Q' + str(signal['period'])
    label = target + ' · ' + signal['direction'] + ' ' + format(signal['line'], 'g')
    return ('🔔 Сигнал · экспериментальный прогноз\n' + card['teams'] + '\n' + label +
            '\nНаправление сохраняется в сценариях риска. Это не проверенная вероятность выигрыша.' +
            '\n' + card.get('context', '') +
            '\nОдна рекомендация на этот матч и период; изменение линии не является новым входом.' +
            '\n\nПредварительное направление. Вероятность и преимущество не подтверждены независимой проверкой.' +
            '\nhttps://pari-basketball-ai.onrender.com/')


def connection():
    db = sqlite3.connect(STATE_PATH, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, sent_at REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT)')
    return db


def get(db, key, default=''):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
    db.commit()


def key_for(card):
    s = card['signal']
    # A different price/line or a direction flip is still the same risk exposure.
    identity = ['one-decision-v2', card['event'], s['target'], s['period'] if s['target'] == 'quarter' else 0]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def audit_delivery(card, state, message_id=None):
    import forecast_journal
    forecast_journal.record_notification(card['forecast_id'], state, message_id)


async def telegram(method, **payload):
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post('https://api.telegram.org/bot' + os.environ['SIGNAL_BOT_TOKEN'] + '/' + method, json=payload)
            data = response.json()
        if not data.get('ok'):
            raise RuntimeError('Telegram API rejected request')
        return data['result']
    except Exception:
        # Never include an HTTP exception or a token-bearing request URL.
        raise RuntimeError('Telegram API unavailable') from None


async def say(text):
    return await telegram('sendMessage', chat_id=int(os.environ['SIGNAL_CHAT_ID']), text=text,
                          link_preview_options={'is_disabled': True})


def public_status():
    checked = STATUS['checked_at_utc']
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(checked)).total_seconds() if checked else None
    return {**STATUS, 'enabled': enabled(), 'notification_mode': notification_mode(), 'state_persistent': False,
            'age_seconds': round(age, 1) if age is not None else None,
            'detector_fresh': age is not None and 0 <= age <= 90,
            'limitation': 'Runs while the free Render service is awake; state is lost after ephemeral disk resets.'}


async def deliver(cards):
    if not enabled():
        return
    async with LOCK:
        db = connection()
        try:
            if get(db, 'baseline') != '1':
                for card in cards:
                    db.execute('INSERT OR IGNORE INTO sent VALUES (?,?)', (key_for(card), time.time()))
                put(db, 'baseline', '1')
                return
            if get(db, 'paused') == '1':
                return
            for card in cards:
                key = key_for(card)
                if card['expires'] <= time.time()*1000 or db.execute('SELECT 1 FROM sent WHERE key=?', (key,)).fetchone():
                    continue
                if not card.get('forecast_id'):
                    STATUS['error'] = 'notification_missing_saved_forecast'
                    continue
                audit_delivery(card, 'attempt')
                try:
                    result = await say(message_for(card))
                except Exception:
                    audit_delivery(card, 'failed')
                    raise
                db.execute('INSERT OR IGNORE INTO sent VALUES (?,?)', (key, time.time()))
                db.commit()
                STATUS['sent'] += 1
                audit_delivery(card, 'sent', result.get('message_id') if isinstance(result, dict) else None)
        finally:
            db.close()


async def process(data, history):
    import server_detector
    from copy import deepcopy
    snapshot = deepcopy(data)
    for event in snapshot.get('events', []):
        event['recent_snapshots'] = history.get(str(event.get('id')), [])
    result = await asyncio.to_thread(server_detector.calculate, snapshot)
    import forecast_journal
    identities = await asyncio.to_thread(forecast_journal.record, result['evaluations'], result['model_source_sha256'])
    cards = result['cards']
    saved = {}
    for row, identity in zip(result['evaluations'], identities):
        decision = row['decision']
        saved[(str(row['event_id']), decision['target'], decision['period'], decision['line'])] = identity
    for card in cards + result.get('researchCandidates', []):
        s = card['signal']
        card['forecast_id'] = saved.get((str(card['event']), s['target'], s['period'], s['line']))
    data['priority_signals'] = cards
    data['research_candidates'] = result.get('researchCandidates', [])
    data['signal_diagnostics'] = result.get('diagnostics', {})
    STATUS.update(checked_at_utc=data.get('checked_at_utc'), cards=cards,
                  error='partial_model_errors' if result['errors'] else None,
                  calculation_errors=result['errors'],
                  research_candidates=result.get('researchCandidates', []),
                  diagnostics=result.get('diagnostics', {}))
    notifications = cards + [card for card in experimental_cards(result) if key_for(card) not in {key_for(x) for x in cards}]
    STATUS['notification_candidates'] = len(notifications)
    await deliver(notifications)


async def commands_once():
    if not enabled():
        return
    async with LOCK:
        db = connection()
        try:
            updates = await telegram('getUpdates', offset=int(get(db, 'offset', '0')), timeout=0, allowed_updates=['message'])
            for update in updates:
                msg = update.get('message', {})
                if str(msg.get('chat', {}).get('id')) == os.environ['SIGNAL_CHAT_ID']:
                    command = msg.get('text', '').strip().split('@')[0]
                    if command in ('/start', '/resume'):
                        put(db, 'paused', '0')
                        await say('Сигнал включён. Сервер проверяет детектор примерно каждые 30 секунд, пока Render активен.')
                    elif command == '/pause':
                        put(db, 'paused', '1')
                        await say('Уведомления приостановлены. /resume — включить. При сбросе временного диска Render настройка может сброситься.')
                    elif command == '/test':
                        await say('Проверка доставки: Telegram работает. Режим: ' +
                                  ('экспериментальный прогноз.' if experimental_enabled() else 'только подтверждённые сигналы.') +
                                  '\nЭто техническая проверка, а не прогноз матча.')
                    elif command == '/status':
                        checked = STATUS['checked_at_utc']
                        checked = (datetime.fromisoformat(checked).astimezone(timezone(timedelta(hours=3))).strftime('%d.%m %H:%M:%S МСК')
                                   if checked else 'ожидание первого расчёта')
                        await say(('Пауза. ' if get(db, 'paused') == '1' else 'Включён. ') +
                                  'Работаю на сервере сайта. Интервал около 30 секунд.\nПоследний расчёт: ' + checked +
                                  '\nКарточек: ' + str(len(STATUS['cards'])) +
                                  ('\nУведомления: экспериментальный прогноз. Предварительных направлений: ' +
                                   str(STATUS.get('notification_candidates', 0)) if experimental_enabled() else '') +
                                  ('\nПодтверждённые сигналы отключены: модель ещё не прошла независимую проверку.'
                                   if STATUS.get('diagnostics', {}).get('automaticPriorityEnabled') is False else '') +
                                  ('\nОшибка: ' + STATUS['error'] if STATUS['error'] else ''))
                put(db, 'offset', update['update_id'] + 1)
            STATUS['commands_checked_at_utc'] = datetime.now(timezone.utc).isoformat()
            STATUS.pop('command_error', None)
        finally:
            db.close()


async def commands_loop():
    while True:
        try:
            await commands_once()
        except Exception as exc:
            STATUS['command_error'] = type(exc).__name__
        await asyncio.sleep(3)
