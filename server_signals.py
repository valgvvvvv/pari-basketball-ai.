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
import signal_lifecycle as lifecycle

logging.getLogger('httpx').setLevel(logging.WARNING)
STATE_PATH = Path(os.environ.get('SIGNAL_STATE_PATH', str(Path(tempfile.gettempdir()) / 'pari_signal_state.sqlite3')))
INTERVAL_SECONDS = 5
STATUS = {'mode': 'server', 'delivery_version': '2026-11-28-reviewed-delivery-v5',
          'interval_seconds': INTERVAL_SECONDS, 'checked_at_utc': None,
          'cards': [], 'error': None, 'sent': 0, 'commands_checked_at_utc': None}
LOCK = asyncio.Lock()
COMMAND_LOCK = asyncio.Lock()
PENDING = {}
RETRY_AT = {}
WAKE = asyncio.Event()
RESULT_LOCK = asyncio.Lock()
FINAL_CHECK_LOCK = asyncio.Lock()
REPORT_LOCK = asyncio.Lock()
RESULT_WAKE = asyncio.Event()
_TELEGRAM_CLIENT = None
_TELEGRAM_LOOP = None
_TELEGRAM_RETRY_UNTIL = 0


class TelegramRetryAfter(RuntimeError):
    def __init__(self, seconds):
        self.retry_after = seconds
        super().__init__('Telegram rate limit; retry scheduled')


async def close_transport():
    global _TELEGRAM_CLIENT, _TELEGRAM_LOOP
    if _TELEGRAM_CLIENT is not None:
        await _TELEGRAM_CLIENT.aclose()
    _TELEGRAM_CLIENT = _TELEGRAM_LOOP = None


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
    # Mirror the site's explicitly opted-in RESEARCH watchlist, whose selector
    # already applies these input/window gates. Market/model blockers remain in
    # the immutable signal and its warning; this never promotes betting readiness.
    blocked = {'data', 'window', 'preliminary', 'probabilities', 'suspended'}
    for card in result.get('researchCandidates', []):
        signal = card.get('signal', {})
        robust = signal.get('robustness') or {}
        probability = robust.get('probabilityMin')
        if (signal.get('watchlist') is not True or
                robust.get('available') is not True or robust.get('directionStable') is not True or
                isinstance(probability, bool) or not isinstance(probability, (int, float)) or
                not math.isfinite(probability) or not .65 <= probability <= 1 or
                blocked.intersection(signal.get('reasonCodes', []))):
            continue
        selected.append({**card, 'experimental': True})
    return sorted(selected, key=lambda card: (-card['signal']['robustness']['probabilityMin'], str(card['event'])))


def message_for(card):
    if not card.get('experimental'):
        return lifecycle.clean_text('🔔 Сигнал\n' + card['teams'] + '\n' + card['label'] + '\n' + card['context'] +
                '\n\nДетектор сервера · оценка модели не калибрована.\nhttps://pari-basketball-ai.onrender.com/')
    signal = card['signal']
    target = ('Матч · основное время' if signal['target'] == 'match' else
              'Тайм ' + str(signal['period']) if signal['target'] == 'half' else 'Четверть Q' + str(signal['period']))
    label = target + ' · ' + signal['direction'] + ' ' + format(signal['line'], 'g')
    market_note = ('\nРынок совместим; преимущество не проверено.' if signal.get('marketReady') else
                   '\nТолько ориентир для наблюдения: правила или цены рынка не подтверждены.')
    if 'overtime' in signal.get('reasonCodes', []):
        market_note = '\nТолько ориентир для наблюдения: рынок с ОТ, прогноз без ОТ. Для этого рынка не применять.'
    review = card.get('delivery_review') or {}
    model_note = ('\nОбучаемый расчёт согласен с направлением; точность для этой лиги отдельно не подтверждена.'
                  if review.get('available') and review.get('allow') else
                  '\nСопоставимая проверка обучаемой моделью недоступна.' if review else '')
    return lifecycle.clean_text((('🔄 Обновление · экспериментальный прогноз\n' if card.get('update') else
             '🔔 Сигнал · экспериментальный прогноз\n') + card['teams'] + '\n' + label +
            '\nНаправление сохраняется в сценариях риска. Это не проверенная вероятность выигрыша.' +
            '\n' + card.get('context', '') +
            market_note + model_note +
            '\nИзменение линии — обновление наблюдения, а не новый вход.' +
            '\n\nПредварительное направление. Вероятность и преимущество не подтверждены независимой проверкой.' +
            '\nhttps://pari-basketball-ai.onrender.com/\n⏳ Ожидает подтверждения результата.'))


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
    identity = ['notification-v3', str(card['event']), s['target'],
                s['period'] if s['target'] in ('quarter', 'half') else 0, float(s['line']), s['direction']]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def exposure_key(card):
    s = card['signal']
    return json.dumps([str(card['event']), s['target'], s['period'] if s['target'] in ('quarter', 'half') else 0])


def audit_delivery(card, state, message_id=None):
    import forecast_journal
    forecast_journal.record_notification(card['forecast_id'], state, message_id,
        notification_text=message_for(card) if state == 'sent' else None,
        notification_card={k: card.get(k) for k in ('event', 'teams', 'signal', 'origin', 'update', 'entry_state', 'delivery_review')} if state == 'sent' else None)


async def telegram(method, **payload):
    global _TELEGRAM_CLIENT, _TELEGRAM_LOOP, _TELEGRAM_RETRY_UNTIL
    if _TELEGRAM_RETRY_UNTIL > time.time():
        raise TelegramRetryAfter(_TELEGRAM_RETRY_UNTIL-time.time())
    try:
        loop = asyncio.get_running_loop()
        if _TELEGRAM_CLIENT is None or _TELEGRAM_CLIENT.is_closed or _TELEGRAM_LOOP is not loop:
            _TELEGRAM_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3, pool=2))
            _TELEGRAM_LOOP = loop
        response = await _TELEGRAM_CLIENT.post('https://api.telegram.org/bot' + os.environ['SIGNAL_BOT_TOKEN'] + '/' + method, json=payload)
        data = response.json()
        if response.status_code == 429 or data.get('error_code') == 429:
            try:
                seconds = float((data.get('parameters') or {}).get('retry_after', 3))
                if not math.isfinite(seconds) or seconds < 1:
                    seconds = 3
            except (TypeError, ValueError):
                seconds = 3
            _TELEGRAM_RETRY_UNTIL = time.time()+seconds
            raise TelegramRetryAfter(seconds)
        if not data.get('ok'):
            if method == 'editMessageText' and 'message is not modified' in str(data.get('description', '')).lower():
                return {'unchanged': True}
            raise RuntimeError('Telegram API rejected request')
        return data['result']
    except TelegramRetryAfter:
        raise
    except Exception:
        # Never include an HTTP exception or a token-bearing request URL.
        raise RuntimeError('Telegram API unavailable') from None


async def say(text):
    return await telegram('sendMessage', chat_id=int(os.environ['SIGNAL_CHAT_ID']), text=text,
                          link_preview_options={'is_disabled': True})


def public_status():
    checked = STATUS['checked_at_utc']
    age = (datetime.now(timezone.utc)-datetime.fromisoformat(checked)).total_seconds() if checked else None
    return {**STATUS, 'delivery_trigger': 'new_snapshot_event', 'delivery_retry_check_seconds': 1,
            'pending_notifications': len(PENDING),
            'telegram_retry_after_seconds': round(max(0, _TELEGRAM_RETRY_UNTIL-time.time()), 1),
            'daily_report_timezone': 'Europe/Moscow', 'daily_report_hour': 23, 'enabled': enabled(), 'notification_mode': notification_mode(), 'state_persistent': False,
            'age_seconds': round(age, 1) if age is not None else None,
            'detector_fresh': age is not None and 0 <= age <= 90,
            'limitation': 'Runs while the free Render service is awake; state is lost after ephemeral disk resets.'}


async def deliver(cards, current_only=False):
    if not enabled():
        return
    async with LOCK, RESULT_LOCK:
        db = connection()
        try:
            if get(db, 'paused') == '1':
                return
            failures = []
            decisions = []
            for card in cards:
                key = key_for(card)
                decision = {'event': str(card['event']), 'target': card['signal']['target'],
                            'period': card['signal']['period'], 'line': card['signal']['line'],
                            'direction': card['signal']['direction']}
                decisions.append(decision)
                if current_only and PENDING.get(key) is not card:
                    decision['state'] = 'replaced'
                    continue
                if card['expires'] <= time.time()*1000:
                    decision['state'] = 'expired'
                    continue
                if db.execute('SELECT 1 FROM sent WHERE key=?', (key,)).fetchone():
                    decision['state'] = 'already_sent'
                    continue
                if RETRY_AT.get(key, 0) > time.time():
                    decision['state'] = 'retry_wait'
                    continue
                if not card.get('forecast_id'):
                    STATUS['error'] = 'notification_missing_saved_forecast'
                    decision['state'] = 'missing_saved_forecast'
                    continue
                records = await asyncio.to_thread(lifecycle.rows)
                # The source can replace a card while the database read awaits.
                # Never send a superseded or newly expired queue item.
                if current_only and PENDING.get(key) is not card:
                    decision['state'] = 'replaced'
                    continue
                if card['expires'] <= time.time()*1000:
                    decision['state'] = 'expired'
                    continue
                # Checkpoint receipts survive a reset of the delivery dedupe DB.
                if any(key_for({**r['payload']}) == key for r in records):
                    decision['state'] = 'already_sent'
                    continue
                previous = [r for r in records if r['payload']['event'] == str(card['event'])]
                root = min(previous, key=lambda r: r['sent_at']) if previous else None
                card = {**card, 'update': bool(previous) or get(db, 'exposure:' + exposure_key(card)) == '1'}
                sent_at = time.time()
                message_text = message_for(card)
                try:
                    audit_delivery(card, 'attempt')
                    if root:
                        candidate = {'id': card['forecast_id'], 'sent_at': sent_at,
                                     'payload': {'teams': card['teams'], 'signal': card['signal'],
                                                 'original_text': message_text}, 'result': None}
                        shared = [r for r in records if r['message_id'] == root['message_id']]
                        rendered = lifecycle.group_text(shared+[candidate])
                        await telegram('editMessageText', chat_id=int(os.environ['SIGNAL_CHAT_ID']),
                                       message_id=root['message_id'], text=rendered,
                                       link_preview_options={'is_disabled': True})
                        result = {'message_id': root['message_id'], 'date': sent_at}
                    else:
                        result = await say(message_text)
                except Exception as exc:
                    RETRY_AT[key] = time.time() + (exc.retry_after if isinstance(exc, TelegramRetryAfter) else 3)
                    decision['state'] = 'failed_retrying'
                    failures.append({'event': str(card['event']), 'target': card['signal']['target']})
                    try:
                        audit_delivery(card, 'failed')
                    except Exception:
                        pass
                    continue  # A failed card must never suppress the next one.
                db.execute('INSERT OR IGNORE INTO sent VALUES (?,?)', (key, time.time()))
                put(db, 'exposure:' + exposure_key(card), '1')
                STATUS['sent'] += 1
                decision['state'] = 'sent'
                STATUS['last_sent_at_utc'] = datetime.now(timezone.utc).isoformat()
                STATUS['last_delivery_queue_seconds'] = round(max(0, sent_at-card.get('queued_at', sent_at)), 3)
                RETRY_AT.pop(key, None)
                try:
                    lifecycle.register(card, result.get('message_id') if isinstance(result, dict) else None, message_for(card),
                                       result.get('date') if isinstance(result, dict) else None)
                    if root:
                        for saved in [r for r in lifecycle.rows() if r['message_id'] == root['message_id']]:
                            lifecycle.mark_edited(saved['id'], rendered)
                except Exception:
                    STATUS['lifecycle_error'] = 'delivery_receipt_not_saved'
                try:
                    audit_delivery(card, 'sent', result.get('message_id') if isinstance(result, dict) else None)
                except Exception:
                    STATUS['audit_error'] = 'sent_receipt_not_saved'
            STATUS['delivery_failures'] = failures
            STATUS['delivery_decisions'] = decisions
        finally:
            db.close()


async def process(data, history, publish=True):
    import server_detector
    from copy import deepcopy
    snapshot = deepcopy(data)
    for event in snapshot.get('events', []):
        event['recent_snapshots'] = history.get(str(event.get('id')), [])
        event['feed_checked_at_utc'] = snapshot.get('checked_at_utc')
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
        event = next((e for e in snapshot.get('events', []) if str(e.get('id')) == str(card['event'])), {})
        card['origin'] = lifecycle.origin(event)
    await asyncio.to_thread(lifecycle.observe, snapshot.get('events', []))
    RESULT_WAKE.set()
    data['priority_signals'] = cards
    data['research_candidates'] = result.get('researchCandidates', [])
    data['signal_diagnostics'] = result.get('diagnostics', {})
    for event in data.get('events', []):
        event['learning_baselines'] = {
            ('match' if row['decision']['target'] == 'match' else 'period'): row['prediction'].get('center')
            for row in result['evaluations'] if str(row['event_id']) == str(event.get('id'))}
        event['learning_markets'] = [row['decision'] for row in result['evaluations']
                                     if str(row['event_id']) == str(event.get('id'))]
    STATUS.update(checked_at_utc=data.get('checked_at_utc'), cards=cards,
                  error='partial_model_errors' if result['errors'] else None,
                  calculation_errors=result['errors'],
                  research_candidates=result.get('researchCandidates', []),
                  diagnostics=result.get('diagnostics', {}))
    if publish:
        queue_notifications(data)


def queue_notifications(data):
    """Publish only after the same snapshot has its trained challenger attached."""
    import signal_review
    from copy import deepcopy
    cards = data.get('priority_signals') or []
    result = {'researchCandidates': data.get('research_candidates') or []}
    candidates = cards + [card for card in experimental_cards(result) if key_for(card) not in {key_for(x) for x in cards}]
    events = {str(event['id']):event for event in data.get('events') or []}
    notifications, decisions = [], []
    for card in candidates:
        event = events.get(str(card['event']), {})
        review = signal_review.review(card, event)
        decision = {'event':str(card['event']), 'target':card['signal']['target'],
                    'period':card['signal']['period'], 'line':card['signal']['line'], **review}
        decisions.append(decision)
        if card.get('experimental') and review['available'] and not review['allow']:
            continue
        predictions = event.get('learning') or {}
        target = 'match' if card['signal']['target'] == 'match' else 'period'
        prediction = predictions.get(target) or {}
        entry = {k:deepcopy(event.get(k)) for k in ('score','clock','period','period_scores','period_count','period_length_seconds',
                    'period_elapsed_seconds','period_remaining_seconds','match_elapsed_seconds','clock_source','score_source',
                    'feed_checked_at_utc','statistics_quality','statistics_source','external_statistics','period_statistics',
                    'timer_semantics_verified','timer_interpretation_status')}
        entry['checked_at_utc'] = data.get('checked_at_utc')
        entry['learning'] = {k:deepcopy(prediction.get(k)) for k in ('status','model_version','center','baseline_center','interval80','profile','cohort','state')}
        notifications.append({**card, 'entry_state':entry, 'delivery_review':review})
    STATUS['delivery_review_policy'] = signal_review.POLICY
    STATUS['delivery_review_decisions'] = decisions
    STATUS['challenger_disagreements'] = sum(r['available'] and not r['allow'] for r in decisions)
    STATUS['notification_candidates'] = len(notifications)
    # Refresh the queue atomically. Disappeared/replaced lines are not retried
    # from an old snapshot, and Telegram latency cannot delay the next fetch.
    PENDING.clear()
    queued_at = time.time()
    PENDING.update({key_for(card): {**card, 'queued_at': queued_at} for card in notifications})
    for key in list(RETRY_AT):
        if key not in PENDING:
            RETRY_AT.pop(key, None)
    WAKE.set()


async def delivery_loop():
    while True:
        delay = max(.05, min(1, min(RETRY_AT.values(), default=time.time()+1) - time.time() + .01))
        try:
            await asyncio.wait_for(WAKE.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        WAKE.clear()
        try:
            await deliver(list(PENDING.values()), current_only=True)
        except Exception as exc:
            STATUS['delivery_error'] = type(exc).__name__
        else:
            STATUS.pop('delivery_error', None)



def restore_journal_receipts():
    import forecast_journal
    restored = 0
    for row in forecast_journal.export_rows(deliveries_only=True):
        for receipt in row.get('notifications', []):
            card = receipt.get('card')
            if receipt.get('state') == 'sent' and receipt.get('original_text') and card:
                lifecycle.register({**card, 'forecast_id': row['id']}, receipt['telegram_message_id'],
                                   receipt['original_text'], lifecycle.stamp(receipt['at_utc']))
                restored += 1
    return restored


async def results_once():
    if not enabled():
        return
    import signal_results
    async with RESULT_LOCK:
        if STATUS.get('lifecycle_error') == 'delivery_receipt_not_saved':
            await asyncio.to_thread(restore_journal_receipts)
            STATUS.pop('lifecycle_error', None)
    # Slow external final-result lookups must not hold the Telegram edit/send
    # lock. Only rendering and editing shared match messages are serialized.
    async with FINAL_CHECK_LOCK:
        await signal_results.check_finals()
    records = await asyncio.to_thread(lifecycle.rows)
    edit_failed = False
    for message_id in dict.fromkeys(row['message_id'] for row in records):
        async with RESULT_LOCK:
            # A delivery may have appended a new line while finals were loading.
            # Re-read under the edit lock so a result update cannot erase it.
            group = [row for row in await asyncio.to_thread(lifecycle.rows) if row['message_id'] == message_id]
            if not group:
                continue
            text = lifecycle.group_text(group)
            if (len(group) == 1 and not group[0].get('result')
                    and text == group[0]['payload']['original_text']):
                continue
            if all(row.get('edited_hash') == lifecycle.edit_hash(text) for row in group):
                continue
            try:
                await telegram('editMessageText', chat_id=int(os.environ['SIGNAL_CHAT_ID']),
                               message_id=message_id, text=text,
                               link_preview_options={'is_disabled': True})
                for row in group:
                    await asyncio.to_thread(lifecycle.mark_edited, row['id'], text)
            except Exception:
                edit_failed = True
                STATUS['result_edit_error'] = 'Telegram edit failed; retry scheduled'
                continue
    if not edit_failed:
        STATUS.pop('result_edit_error', None)
    counts = {}
    for row in await asyncio.to_thread(lifecycle.rows):
        key = (row.get('result') or {}).get('state', 'pending')
        counts[key] = counts.get(key, 0)+1
    STATUS['result_counts'] = counts
    STATUS['results_checked_at_utc'] = datetime.now(timezone.utc).isoformat()


async def daily_report_once(now=None):
    if not enabled() or STATUS.get('checkpoint_ready') is not True:
        return False
    cutoff = lifecycle.latest_cutoff(now).timestamp()
    async with REPORT_LOCK:
        state = await asyncio.to_thread(lifecycle.report_state, cutoff)
        if not state or len(state['sent']) == len(state['chunks']):
            return False
        for index in range(len(state['sent']), len(state['chunks'])):
            suffix = f"\n(часть {index+1}/{len(state['chunks'])})" if len(state['chunks']) > 1 else ''
            result = await say(state['chunks'][index]+suffix)
            if not isinstance(result, dict) or not isinstance(result.get('message_id'), int):
                raise RuntimeError('Daily report receipt missing')
            state['sent'].append(result['message_id'])
            await asyncio.to_thread(lifecycle.report_receipt, cutoff, state)
        STATUS['last_daily_report_at_utc'] = datetime.now(timezone.utc).isoformat()
        STATUS['daily_report_timezone'] = 'Europe/Moscow'
        STATUS['daily_report_hour'] = 23
        return True


async def daily_report_loop():
    # Separate from live collection. The existing GitHub job restores this
    # checkpoint and wakes the free service, including a dedicated 20:00 UTC run.
    try:
        await asyncio.to_thread(restore_journal_receipts)
    except Exception:
        STATUS['lifecycle_error'] = 'journal_restore_failed'
    while True:
        try:
            await results_once()
            await daily_report_once()
            STATUS.pop('daily_report_error', None)
        except Exception as exc:
            STATUS['daily_report_error'] = type(exc).__name__
        try:
            await asyncio.wait_for(RESULT_WAKE.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass
        RESULT_WAKE.clear()


async def commands_once():
    if not enabled():
        return
    async with COMMAND_LOCK:
        db = connection()
        try:
            updates = await telegram('getUpdates', offset=int(get(db, 'offset', '0')), timeout=0, allowed_updates=['message'])
            for update in updates:
                msg = update.get('message', {})
                if str(msg.get('chat', {}).get('id')) == os.environ['SIGNAL_CHAT_ID']:
                    command = msg.get('text', '').strip().split('@')[0]
                    if command in ('/start', '/resume'):
                        put(db, 'paused', '0')
                        await say('Сигнал включён. Сервер проверяет детектор примерно каждые 10 секунд, пока Render активен. Изменения линии приходят как обновления.')
                    elif command == '/pause':
                        put(db, 'paused', '1')
                        await say('Уведомления приостановлены. /resume — включить. При сбросе временного диска Render настройка может сброситься.')
                    elif command == '/test':
                        await say('Проверка доставки: Telegram работает. Режим: ' +
                                  ('экспериментальный прогноз.' if experimental_enabled() else 'только подтверждённые сигналы.') +
                                  '\nЭто техническая проверка, а не прогноз матча.')
                    elif command == '/report':
                        for part in lifecycle.chunks(lifecycle.report_text(datetime.now(lifecycle.MSK))):
                            await say(part)
                    elif command == '/status':
                        checked = STATUS['checked_at_utc']
                        checked = (datetime.fromisoformat(checked).astimezone(timezone(timedelta(hours=3))).strftime('%d.%m %H:%M:%S МСК')
                                   if checked else 'ожидание первого расчёта')
                        await say(('Пауза. ' if get(db, 'paused') == '1' else 'Включён. ') +
                                  'Работаю на сервере сайта. Интервал около 10 секунд.\nПоследний расчёт: ' + checked +
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
