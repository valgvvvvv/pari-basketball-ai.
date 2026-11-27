"""Read the website's actual detector; never independently calculate picks."""
import argparse
import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('SIGNAL_DATA_DIR', str(ROOT / 'data')))
CONFIG = ROOT / 'data' / 'signal-bot.json'
SITE = 'https://pari-basketball-ai.onrender.com/'
LOG = logging.getLogger('signal')


def load_config():
    if os.environ.get('SIGNAL_BOT_TOKEN'):
        return {'token': os.environ['SIGNAL_BOT_TOKEN'],
                'chat_id': int(os.environ['SIGNAL_CHAT_ID'])}
    return json.loads(CONFIG.read_text(encoding='utf-8'))

# Read only visible cards from the top detector. Require its own expiry guard.
READ_CARDS = """() => {
 const panel=document.querySelector('#priorityForecasts');
 if(!panel) throw new Error('Detector panel not found');
 const expiry=typeof prioritySignalsExpiresAt==='number'?prioritySignalsExpiresAt:0;
 if(!expiry || expiry<=Date.now()) return [];
 return [...panel.querySelectorAll('.priority-card')].filter(e=>e.getClientRects().length).map(e=>({
  event:e.dataset.eventId, teams:e.querySelector('b')?.textContent?.trim(),
  label:e.querySelector('span')?.textContent?.trim(), expires:expiry,
  context:[...document.querySelectorAll('.live-match')].find(x=>x.dataset.eventId===e.dataset.eventId)?.innerText||''
 }));
}"""


def signal_key(card):
    # Probability changes do not create a new signal. Period, direction and line do.
    label = card.get('label', '')
    parts = label.split(' · ')
    if not card.get('event') or not card.get('teams') or len(parts) < 3:
        raise ValueError('Incomplete detector card')
    identity = [str(card['event']), *parts[:-1]]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def message(card):
    return (f"🔔 Сигнал\n{card['teams']}\n{card['label']}\n\n"
            f"{card.get('context', '')[:1200]}\n\n"
            "Источник: верхний детектор сайта. Оценка модели не калибрована.\n" + SITE)


class Telegram:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, **params):
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=json.dumps(params).encode(), headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=40) as response:
                result = json.load(response)
        except Exception:
            # Exceptions can contain the request URL with the secret token.
            raise RuntimeError('Telegram API request failed') from None
        if not result.get('ok'):
            raise RuntimeError('Telegram API rejected request')
        return result['result']

    def send(self, chat_id, text):
        return self.call('sendMessage', chat_id=chat_id, text=text,
                         link_preview_options={'is_disabled': True})


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY, sent_at REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
        self.db.commit()

    def seen(self, key):
        return self.db.execute('SELECT 1 FROM sent WHERE key=?', (key,)).fetchone() is not None

    def mark(self, key):
        self.db.execute('INSERT OR IGNORE INTO sent VALUES (?,?)', (key, time.time()))
        self.db.commit()

    def get(self, key, default=''):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))
        self.db.commit()

    def bootstrap(self, seed):
        if not seed or self.get('seed_imported') == '1':
            return
        state = json.loads(seed)
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO sent VALUES (?,?)', state.get('sent', []))
            self.db.executemany('INSERT OR IGNORE INTO meta VALUES (?,?)',
                                [(k, str(v)) for k, v in state.get('meta', {}).items()
                                 if k in ('paused', 'offset')])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('seed_imported','1')")


def deliver(cards, store, tg, chat_id, now=None):
    sent = 0
    for card in cards:
        try:
            key = signal_key(card)
        except ValueError:
            continue
        current_time = time.time() if now is None else now
        if card.get('expires', 0) <= current_time * 1000 or store.seen(key):
            continue
        tg.send(chat_id, message(card))
        store.mark(key)  # Failed sends remain retryable.
        sent += 1
    return sent


def establish_baseline(cards, store):
    if store.get('baseline_done') == '1':
        return False
    for card in cards:
        try:
            if card.get('expires', 0) > time.time() * 1000:
                store.mark(signal_key(card))
        except ValueError:
            continue
    store.put('baseline_done', '1')
    return True


def commands(tg, config, store, status):
    offset = int(store.get('offset', '0'))
    for update in tg.call('getUpdates', offset=offset, timeout=0, allowed_updates=['message']):
        msg = update.get('message', {})
        if str(msg.get('chat', {}).get('id')) == str(config['chat_id']):
            cmd = msg.get('text', '').split('@')[0].strip()
            if cmd in ('/start', '/resume'):
                store.put('paused', '0')
                tg.send(config['chat_id'], 'Сигнал включён. Отслеживаю верхний детектор сайта. Повторы одной линии и направления не присылаю.')
            elif cmd == '/pause':
                store.put('paused', '1')
                tg.send(config['chat_id'], 'Уведомления приостановлены. Включить: /resume')
            elif cmd == '/status':
                tg.send(config['chat_id'], ('Пауза. ' if store.get('paused') == '1' else 'Включён. ') + status)
        store.put('offset', update['update_id'] + 1)


def check_server(opener=None, sleep=time.sleep, monotonic=time.monotonic, max_wait=240):
    """Wait for a free Render cold start and a fresh completed calculation.

    Only 404 permits the legacy browser fallback. Never launch a second
    Telegram poller on a timeout or a temporarily stale server response.
    """
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = monotonic() + max_wait
    while True:
        try:
            with opener.open(SITE + 'api/signals/status', timeout=min(45, max(1, deadline-monotonic()))) as response:
                status = json.load(response)
            if status.get('mode') != 'server':
                raise ValueError('Unexpected detector status')
            if not status.get('enabled'):
                raise ValueError('Server Telegram delivery is disabled')
            if status.get('detector_fresh') and not status.get('error'):
                print('Server detector enabled. Last check:', status.get('checked_at_utc'))
                diagnostics = status.get('diagnostics', {})
                if diagnostics.get('automaticPriorityEnabled') is False:
                    print('Confirmed priority signals are disabled by model validation; see site diagnostics.')
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise RuntimeError('Cannot check server detector') from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass  # Render can return a loading page before the JSON API is ready.
        if monotonic() >= deadline:
            raise RuntimeError('Server detector did not become healthy within cold-start grace period')
        sleep(min(10, max(0, deadline-monotonic())))


def run(config, once=False, server_only=False):
    if once or server_only:
        if check_server():
            return
        if server_only:
            raise RuntimeError('Server detector endpoint is unavailable; no second Telegram poller started')
    from playwright.sync_api import sync_playwright
    tg = Telegram(config['token'])
    DATA.mkdir(parents=True, exist_ok=True)
    store = Store(DATA / 'signal-bot.sqlite3')
    store.bootstrap(os.environ.get('SIGNAL_INITIAL_STATE'))
    status = store.get('status', 'Ожидаю первую проверку сайта из облака.')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        page = browser.new_page()
        next_reload = 0
        failures = 0
        try:
            while True:
                try:
                    commands(tg, config, store, status)
                except RuntimeError:
                    LOG.warning('Telegram commands temporarily unavailable')
                try:
                    if time.monotonic() >= next_reload:
                        page.goto(SITE, wait_until='domcontentloaded', timeout=60000)
                        page.locator('#priorityForecasts').wait_for(timeout=30000)
                        # The page renders its detector after the LIVE API response.
                        page.wait_for_function("typeof liveRefreshInFlight !== 'undefined' && !liveRefreshInFlight && document.querySelector('#priorityForecasts')?.textContent?.trim().length > 0", timeout=90000)
                        next_reload = time.monotonic() + 1800
                    cards = page.evaluate(READ_CARDS)
                    checked = datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m %H:%M:%S МСК')
                    panel_text = page.locator('#priorityForecasts').inner_text()
                    source_text = page.locator('#liveSourceStatus').inner_text()
                    if source_text.strip() != 'Подключён':
                        raise RuntimeError('Fresh LIVE source unavailable')
                    status = f'Проверка: {checked}. Карточек детектора: {len(cards)}. Источник: {source_text}. {panel_text[:350]}'
                    store.put('status', status)
                    if os.environ.get('SIGNAL_FIRST_RUN_BASELINE') == '1':
                        if establish_baseline(cards, store):
                            LOG.info('Initial detector baseline stored; existing cards not resent')
                    if store.get('paused') != '1':
                        count = deliver(cards, store, tg, config['chat_id'])
                        if count:
                            LOG.info('Delivered %d new detector signals', count)
                    if once:
                        print(status)
                        return
                    failures = 0
                    page.wait_for_timeout(5000)
                except Exception as exc:
                    status = 'Нет свежего ответа сайта или Telegram. Выполняю переподключение.'
                    store.put('status', status)
                    LOG.warning('Monitor unavailable (%s)', type(exc).__name__)
                    failures += 1
                    if once or failures >= 3 or not browser.is_connected():
                        raise RuntimeError(status) from None
                    next_reload = 0
                    time.sleep(15)
        finally:
            browser.close()
            store.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--server-only', action='store_true', help='Check the server detector without installing or launching a browser')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config = load_config()
    while True:
        try:
            run(config, args.once, args.server_only)
            break
        except Exception as exc:
            LOG.error('Restarting monitor (%s)', type(exc).__name__)
            if args.once or args.server_only:
                raise SystemExit(1)
            time.sleep(15)
