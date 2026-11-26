"""Restore/checkpoint only the owner's signal lifecycle; never a second poller."""
import hashlib
import gzip
import hmac
import json
import os
from pathlib import Path
import time
import urllib.request


def sync(state=None, opener=None, sleep=time.sleep):
    token, owner = os.environ['SIGNAL_BOT_TOKEN'], os.environ['SIGNAL_CHAT_ID']
    site = os.environ.get('SIGNAL_SITE_URL', 'https://pari-basketball-ai.onrender.com').rstrip('/')
    if site != 'https://pari-basketball-ai.onrender.com':
        raise ValueError('Unexpected signal service')
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = gzip.compress(json.dumps({'owner': owner, 'state': state or {}}, ensure_ascii=False, allow_nan=False).encode())
    for attempt in range(5):
        timestamp = str(int(time.time()))
        signature = hmac.new(token.encode(), timestamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(site+'/internal/signals/lifecycle', data=body,
            headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'X-Signal-Timestamp': timestamp,
                     'X-Signal-Signature': signature}, method='POST')
        try:
            with opener.open(request, timeout=45) as response:
                result = json.load(response)
            if result.get('schema') != 1:
                raise ValueError('Unsupported checkpoint response')
            return result
        except Exception:
            if attempt == 4:
                raise RuntimeError('Signal checkpoint unavailable; previous cache retained') from None
            sleep(10)


if __name__ == '__main__':
    folder = Path(os.environ.get('SIGNAL_DATA_DIR', '.signal-state'))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/'lifecycle.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    result = sync(state)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)
    print('Signal checkpoint saved:', len(result.get('rows', [])), 'messages;',
          len(result.get('reports', {})), 'report windows')
