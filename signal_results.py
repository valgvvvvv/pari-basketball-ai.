"""Bounded result lookup. A final secondary protocol must match primary PARI facts."""
from datetime import datetime
import asyncio
from html.parser import HTMLParser
import re
import time

import httpx
import signal_lifecycle as life

_CACHE = {}
_PRIMARY = {}
_CURSOR = 0


def primary_final_evidence(row, payload, source_url):
    """PARI's explicit fin/finished flags, exact identity and coherent periods."""
    from server import _pari_period_score_records, _pari_score_pair_from_row
    p, s, o = row['payload'], row['payload']['signal'], row['payload'].get('origin', {})
    def records(key):
        values = payload.get(key, []) if isinstance(payload, dict) else []
        return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []
    event = next((e for e in records('events') if str(e.get('id')) == p['event']), None)
    info = next((e for e in records('liveEventInfos') if str(e.get('eventId')) == p['event']), {})
    misc = next((e for e in records('eventMiscs') if str(e.get('id')) == p['event']), {})
    if (not event or not o.get('team1') or not o.get('team2')
            or any(normalized(o[k]) != normalized(event.get(k)) for k in ('team1', 'team2'))
            or info.get('scoreFunction') != 'Basketball'
            or not (misc.get('fin') is True or info.get('finished') is True)):
        return None
    if isinstance(misc.get('fin'), bool) and isinstance(info.get('finished'), bool) and misc['fin'] != info['finished']:
        return None
    status_text = ' '.join(str(r.get(k, '')) for r in (event, info, misc) for k in ('comment', 'scoreComment', 'status', 'state'))
    if re.search(r'отмен|прерван|не состоял|перенес|перенёс|abandon|cancel|postpon', status_text, re.I):
        return None
    if o.get('start_time') and float(event.get('startTime', 0)) != life.stamp(o['start_time']):
        return None
    count = o.get('period_count')
    if count not in (2, 4):
        return None
    explicit = re.search(r'([24])[xх×](10|12|20)', str(misc.get('ef', '')))
    if explicit and int(explicit.group(1)) != count:
        return None
    periods = {}
    for record in _pari_period_score_records(info, count):
        number, score = record['period'], record['score']
        if number in periods and periods[number] != score:
            return None
        periods[number] = score
    if any(i not in periods or not life.checked_pair(periods[i]) for i in range(1, count+1)):
        return None
    try:
        total = _pari_score_pair_from_row(info['scores'][0][0])
    except (KeyError, IndexError, TypeError):
        return None
    if not life.checked_pair(total):
        return None
    if 'score1' in misc and [misc.get('score1'), misc.get('score2')] != total:
        return None
    regulation = [sum(periods[i][j] for i in range(1, count+1)) for j in (0, 1)]
    if regulation != total:
        comment = str(info.get('scoreComment', ''))+' '+str(misc.get('comment', ''))
        overtime = re.search(r'\bОТ\b|овертайм|overtime', comment, re.I)
        if not overtime or regulation[0] != regulation[1] or any(a > b for a, b in zip(regulation, total)):
            return None
    if s['target'] == 'match':
        score = regulation
    else:
        number = s.get('period')
        if number not in periods or (s['target'] == 'half') != (count == 2):
            return None
        score = periods[number]
    return {'score': score, 'source': 'PARI: confirmed finished event and period protocol',
            'source_url': source_url, 'basis': 'primary_explicit_finished_protocol',
            'observed_at': life.now_ts()}


class ResultsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.capture = {}, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'article':
            self.row = {'id': a.get('data-id'), 'final': 'is-fin' in a.get('class', '').split(),
                        'teams': [], 'period_text': '', 'score': []}
        if not self.row:
            return
        classes = a.get('class', '').split()
        if tag == 'span' and 'tn' in classes:
            self.capture = 'team'
        elif tag == 'div' and 'm-per' in classes:
            self.capture = 'periods'
        elif tag == 'div' and 'm-score' in classes:
            self.capture = 'score'
        elif tag == 'a' and 'm-link' in classes:
            self.row['url'] = 'https://scorebasketball.ru'+a.get('href', '')

    def handle_data(self, data):
        if self.row and data.strip():
            if self.capture == 'team':
                self.row['teams'].append(data.strip())
            elif self.capture == 'periods':
                self.row['period_text'] += data
            elif self.capture == 'score' and data.strip().isdigit():
                self.row['score'].append(int(data.strip()))

    def handle_endtag(self, tag):
        if tag == 'span' and self.capture == 'team':
            self.capture = None
        if tag == 'div' and self.capture in ('periods', 'score'):
            self.capture = None
        if tag == 'article' and self.row:
            self.row['periods'] = [[int(a), int(b)] for a, b in re.findall(r'(\d+):(\d+)', self.row['period_text'])]
            self.rows[self.row['id']] = self.row
            self.row, self.capture = None, None


def normalized(name):
    return re.sub(r'\s+', ' ', str(name or '')).strip().casefold().replace('ё', 'е')


def final_evidence(row, result, primary):
    p, s = row['payload'], row['payload']['signal']
    if (not result or result.get('final') is not True or result.get('id') != p['event']
            or not primary or str(primary.get('id')) != p['event']):
        return None
    teams = [primary.get('team1'), primary.get('team2')]
    if len(result.get('teams', [])) != 2 or any(normalized(a) != normalized(b) for a, b in zip(teams, result['teams'])):
        return None
    periods, count = result.get('periods', []), primary.get('period_count')
    if (count not in (2, 4) or len(periods) < count or not all(life.checked_pair(q) for q in periods)
            or not life.checked_pair(result.get('score'))
            or [sum(q[j] for q in periods) for j in (0, 1)] != result['score']):
        return None
    if s['target'] == 'match':
        actual = [sum(q[j] for q in periods[:count]) for j in (0, 1)]
        observed_periods = primary.get('period_scores', [])
        if len(observed_periods) < count:
            return None
        corroborated = [sum(q[j] for q in observed_periods[:count]) for j in (0, 1)]
    else:
        number = s.get('period')
        if (not isinstance(number, int) or not 1 <= number <= count
                or (s['target'] == 'half') != (count == 2)):
            return None
        actual = periods[number-1]
        observed_periods = primary.get('period_scores', [])
        if len(observed_periods) < number:
            return None
        corroborated = observed_periods[number-1]
    if actual != corroborated:
        return None
    return {'score': actual, 'source': 'PARI protocol + ScoreBasketball finished result',
            'source_url': result.get('url'), 'basis': 'final_protocol_corroborated_by_primary',
            'observed_at': primary.get('feed_checked_at_utc')}


async def check_finals():
    global _CURSOR
    records = [r for r in life.rows() if (not r.get('result') or
        r['result'].get('evidence', {}).get('basis') == 'primary_completed_period')
        and life.now_ts()-r['sent_at'] < 7*86400]
    if not records:
        return
    state = life.export_state()
    async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
        ids = list(dict.fromkeys(r['payload']['event'] for r in records))
        chosen = (ids+ids)[_CURSOR:_CURSOR+min(12, len(ids))]
        _CURSOR = (_CURSOR+len(chosen)) % len(ids)
        semaphore = asyncio.Semaphore(4)
        async def load_primary(identity):
            if identity in _PRIMARY and time.monotonic()-_PRIMARY[identity][0] < 30:
                return
            import pari_data
            async with semaphore:
                try:
                    payload = await pari_data.request_json(client, 'https://line-vk01-w.pb06e2-resources.ru',
                        '/events/event', {'eventId': identity, 'lang': 'ru', 'scopeMarket': '2300'}, 3)
                    _PRIMARY[identity] = (time.monotonic(), payload)
                except Exception:
                    _PRIMARY[identity] = (time.monotonic(), {})
        await asyncio.gather(*(load_primary(identity) for identity in chosen))
        remaining = []
        for row in records:
            identity = row['payload']['event']
            raw = _PRIMARY.get(identity, (0, {}))[1]
            evidence = primary_final_evidence(row, raw,
                'https://line-vk01-w.pb06e2-resources.ru/events/event?eventId='+identity+'&lang=ru&scopeMarket=2300')
            if evidence:
                life.save_result(row['id'], life.result_for(row, evidence))
            else:
                remaining.append(row)
        records = remaining
        if not records:
            return
        dates = set()
        for row in records:
            value = row['payload'].get('origin', {}).get('start_time')
            try:
                dates.add(datetime.fromtimestamp(life.stamp(value), life.MSK).date().isoformat())
            except (ValueError, TypeError, OverflowError):
                dates.add(datetime.fromtimestamp(row['sent_at'], life.MSK).date().isoformat())
        for day in sorted(dates)[-8:]:
            if day not in _CACHE or time.monotonic()-_CACHE[day][0] >= 60:
                try:
                    response = await client.get('https://scorebasketball.ru/matches/'+day+'/')
                    response.raise_for_status()
                    parser = ResultsParser(); parser.feed(response.text)
                    _CACHE[day] = (time.monotonic(), parser.rows)
                except (httpx.HTTPError, ValueError):
                    continue
        for row in records:
            for _, results in _CACHE.values():
                final = results.get(row['payload']['event'])
                evidence = final_evidence(row, final, state.get('evidence', {}).get(row['payload']['event']))
                if evidence:
                    life.save_result(row['id'], life.result_for(row, evidence))
                    break
