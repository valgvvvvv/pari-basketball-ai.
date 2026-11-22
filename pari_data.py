"""PARI's own event-card and match-centre metadata, with exact event identity.

Routes and factor semantics were verified against the public PARI 2.13.96
frontend and basketball LIVE eventViewTables catalogue (2026-11-22).
"""
import asyncio
import copy
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name('pari_total_catalog.json').read_text(encoding='utf-8'))
_METADATA = {}
_DETAILS = {}


def decode(response):
    response.raise_for_status()
    try:
        return json.loads(response.content.decode('utf-8-sig'))
    except UnicodeDecodeError:
        return json.loads(response.content.decode('cp1251'))


def same_event(event, info):
    def name(value):
        return re.sub(r'\s+', ' ', str(value or '').casefold()).strip()
    return (str(info.get('fon_code')) == str(event.get('id')) and
            str(info.get('sport_code')) == '11726632' and
            all(name(info.get(a)) and name(info.get(a)) == name(event.get(b))
                for a, b in [('fon_team1', 'team1'), ('fon_team2', 'team2')]))


def explicit_format(event, info, source_url):
    if not same_event(event, info):
        return None
    text = str((info.get('extra') or {}).get('quarters') or '')
    match = re.fullmatch(r'(2|4)[xх×](10|12|20)', text)
    if not match:
        return None
    count, minutes = map(int, match.groups())
    if (count, minutes) not in [(4, 10), (4, 12), (2, 20)]:
        return None
    return {'period_length_seconds': minutes*60, 'period_count': count,
            'competition_id': str(event.get('sportId')),
            'source': 'PARI match-centre event-specific extra.quarters; exact event ID and teams',
            'rules_version': 'source_event_metadata', 'source_url': source_url}


def descendants(events, root):
    ids = {str(root)}
    for _ in range(8):
        extra = {str(e['id']) for e in events if isinstance(e, dict) and e.get('id') is not None
                 and str(e.get('parentId')) in ids}
        if extra <= ids:
            break
        ids.update(extra)
    return ids


def merge_detail(payload, detail, root):
    """Replace an entire event subtree atomically, never just its price/score."""
    original = next((e for e in payload.get('events', []) if str(e.get('id')) == str(root)), None)
    current = next((e for e in detail.get('events', []) if str(e.get('id')) == str(root)), None)
    if not original or not current or any(str(original.get(k)) != str(current.get(k))
                                         for k in ['sportId', 'team1', 'team2']):
        return False
    new_ids = descendants(detail['events'], root)
    # Reject any foreign event injected into the detailed response.
    if any(str(e.get('id')) not in new_ids for e in detail['events']):
        return False
    old_ids = descendants(payload['events'], root)
    for key, field in [('events', 'id'), ('eventMiscs', 'id'), ('liveEventInfos', 'eventId'),
                       ('customFactors', 'e'), ('eventBlocks', 'eventId')]:
        rows = detail.get(key)
        if not isinstance(rows, list):
            return False
        if any(str(row.get(field)) not in new_ids for row in rows if isinstance(row, dict)):
            return False
    for key, field in [('events', 'id'), ('eventMiscs', 'id'), ('liveEventInfos', 'eventId'),
                       ('customFactors', 'e'), ('eventBlocks', 'eventId')]:
        payload[key] = [e for e in payload.get(key, []) if str(e.get(field)) not in old_ids] + copy.deepcopy(detail[key])
    return True


async def enrich_payload(client, host, payload):
    """Bounded optional requests. A failed card must not make the LIVE list fail."""
    now = time.monotonic()
    source = host+'/sportscast/actrans'
    cached = _METADATA.get(host)
    diagnostics = {'source': 'PARI event card + match centre', 'metadata': 'unavailable',
                   'detail_events': 0, 'detail_errors': 0, 'format_events': 0}
    if not cached or now-cached['saved'] > 60:
        try:
            data = decode(await client.get(source, params={'lang': 'multi'}, timeout=4))
            items = data.get('items')
            if not isinstance(items, list):
                raise ValueError('No items')
            cached = {'saved': time.monotonic(), 'items': {str(e.get('fon_code')): e for e in items
                      if isinstance(e, dict) and str(e.get('sport_code')) == '11726632'}}
            _METADATA[host] = cached
        except Exception:
            cached = None  # Expired metadata is not silently reused.
    infos = cached['items'] if cached else {}
    diagnostics['metadata'] = 'available' if cached else 'unavailable'
    roots = [e for e in payload.get('events', []) if e.get('level') == 1 and e.get('place') == 'live'
             and same_event(e, infos.get(str(e.get('id')), {}))]
    # Oldest first avoids starving matches outside the first page of the feed.
    roots.sort(key=lambda e: _DETAILS.get((host, str(e['id'])), {}).get('saved', 0))
    received = {}
    sem = asyncio.Semaphore(4)

    async def card(event):
        identity = (host, str(event['id']))
        stored = None  # A cached card must never roll a newer list snapshot backwards.
        if stored is None:
            async with sem:
                try:
                    detail = decode(await client.get(host+'/events/event',
                                    params={'eventId': event['id'], 'lang': 'ru', 'scopeMarket': '2300'}, timeout=3))
                    stored = {'saved': time.monotonic(), 'data': detail,
                              'received': datetime.now(timezone.utc).isoformat()}
                    _DETAILS[identity] = stored
                except Exception:
                    diagnostics['detail_errors'] += 1
                    return
        if merge_detail(payload, stored['data'], event['id']):
            received[str(event['id'])] = stored['received']
            diagnostics['detail_events'] += 1

    try:
        await asyncio.wait_for(asyncio.gather(*(card(e) for e in roots[:24])), timeout=7)
    except asyncio.TimeoutError:
        diagnostics['detail_errors'] += 1
    formats = {}
    for event in payload.get('events', []):
        fmt = explicit_format(event, infos.get(str(event.get('id')), {}), source)
        if fmt:
            formats[str(event['id'])] = fmt
    diagnostics['format_events'] = len(formats)
    # Keep bounded state as finished events disappear from the current feed.
    live_ids = {str(e.get('id')) for e in payload.get('events', [])}
    for key in list(_DETAILS):
        if key[0] == host and key[1] not in live_ids:
            _DETAILS.pop(key, None)
    return formats, received, diagnostics


def factor_sides(table):
    return {factor: side for pair in CATALOG['tables'][table] for factor, side in zip(pair, ['over', 'under'])}


def markets_for(event, checked_at, blocks):
    result = []
    groups = [(str(event['id']), 'match', None, event.get('factors', []))]
    for child in event.get('related_factor_groups', []):
        kind = str(child.get('kind'))
        if kind in ['100401', '100402', '100403', '100404']:
            groups.append((str(child['event_id']), 'quarter', int(kind[-1]), child.get('factors', [])))
    for source_id, target, period, factors in groups:
        sides = factor_sides('18319' if target == 'match' else '2673')
        by_line = {}
        for factor in factors:
            side = sides.get(str(factor.get('f')))
            try:
                line, odds = float(factor['pt']), float(factor['v'])
            except (KeyError, ValueError, TypeError):
                continue
            if not side or not math.isfinite(line) or not math.isfinite(odds) or odds <= 1 or odds > 1000:
                continue
            if line < 0 or line*2 != round(line*2):
                continue
            by_line.setdefault(line, {}).setdefault(side, []).append((odds, str(factor['f'])))
        for line, prices in by_line.items():
            # Missing or conflicting prices are never filled with zero or another line.
            if any(len(set(prices.get(side, []))) != 1 for side in ['over', 'under']):
                continue
            over, under = prices['over'][0], prices['under'][0]
            blocked = any(row.get('state') == 'blocked' for identity in [str(event['id']), source_id]
                          for row in blocks.get(identity, []))
            result.append({'eventId': str(event['id']), 'sourceEventId': source_id, 'target': target,
                           'period': period, 'line': line, 'pricesVerified': True,
                           'overOdds': over[0], 'underOdds': under[0],
                           'overFactorId': over[1], 'underFactorId': under[1],
                           'quotedAtUtc': checked_at, 'quoteTimestampKind': 'response_received_not_provider_update',
                           'suspended': blocked, 'rulesConfirmed': True,
                           'ruleSource': 'https://pari.ru/rules section 8.1' if target == 'match' else
                               CATALOG['source_url']+'; '+CATALOG['quarter_titles'][str(period)],
                           'includesOvertime': target == 'match',
                           'factorMappingSource': CATALOG['source_url'], 'factorMappingVersion': CATALOG['version']})
    return result
