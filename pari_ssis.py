"""Public PARI statistics widget API (website 2.13.104, verified 2026-11-26).

No browser credentials/device identifier. Numeric kinds and stages follow the
widget's published enums; unknown fields remain visible, never guessed.
"""
import asyncio
import copy
import time
from datetime import datetime, timezone

import httpx

BASE = 'https://line-vk01-w.pb06e2-resources.ru/ssis/api/v1/'
KINDS = dict(zip(
    ['1','30','14','21','58','59','61','62','63','70','71','72','73','74','75','76','77','78','79','80','81','82','120','121','122','123','125','126','127','128','129'],
    ['result_code','points','fouls','blocks','assists','biggest_lead','defensive_rebounds','free_throws_made','free_throws_attempted','offensive_rebounds','rebounds','steals','team_leads','team_rebounds','team_turnovers','three_pointers_made','three_pointers_attempted','time_in_lead','timeouts','turnovers','two_pointers_made','two_pointers_attempted','field_goals_attempted','field_goals_made','free_throws_attempted','free_throws_made','personal_fouls','technical_fouls','three_pointers_attempted','three_pointers_made','rebounds']))
_REVIEWS = {}
_CURSOR = 0


def body(payload):
    return payload.get('response', {}) if isinstance(payload, dict) and payload.get('result') == 'OK' else {}


def participants(data, event):
    """Only the exact PARI event, start and two native participant IDs qualify."""
    if str(data.get('eventId')) != str(event.get('id')) or str(data.get('sport')) != '3':
        return None
    if str(data.get('startTime')) != str(event.get('start_time')):
        return None
    raw = event.get('raw') or {}
    ids = [str(raw.get('team1Id') or ''), str(raw.get('team2Id') or '')]
    rows = data.get('participants') or []
    if len(rows) != 2 or not all(ids) or ids[0] == ids[1]:
        return None
    index = {str(p.get('id')): p for p in rows}
    return [index[x] for x in ids] if set(index) == set(ids) else None


def stage_period(stage, count):
    stages = {'46':1, '47':2} if count == 2 else {'22':1,'23':2,'24':3,'25':4}
    # Generic extra-time totals are not individual periods.
    stages.update({str(code):count+i for i,code in enumerate([28,29,48,49,50,51], 1)})
    return stages.get(str(stage))


def stat_map(participant):
    result = {}
    for stat in participant.get('stats') or []:
        key = (str(stat.get('kind')), str(stat.get('stage') or ''))
        value = stat.get('value')
        # Contradictory duplicate codes cannot be selected arbitrarily.
        if key in result and result[key] != value:
            result[key] = None
        else:
            result.setdefault(key, value)
    return result


def parse_statistics(payload, event, observed_at):
    data = body(payload)
    sides = participants(data, event)
    result = {'statistics':[], 'periods':[], 'unmapped':[], 'received':[], 'state':'identity_not_confirmed'}
    if not data:
        result['state'] = 'statistics_unavailable'
        return result
    if not sides:
        return result
    maps = [stat_map(p) for p in sides]
    score = [m.get(('30','')) for m in maps]
    try:
        coherent = [int(x) for x in score] == list(event.get('score') or [])
    except (TypeError, ValueError):
        coherent = False
    result['state'] = 'score_reconciled' if coherent else 'score_not_current'
    for kind, stage in sorted(set(maps[0]) | set(maps[1])):
        values = [m.get((kind, stage)) for m in maps]
        metric = KINDS.get(kind)
        result['received'].append({'kind':kind,'stage':stage,'metric_key':metric,'home':values[0],'away':values[1]})
        if None in values:
            continue
        period = stage_period(stage, int(event.get('period_count') or 4)) if stage else None
        if metric is None or (stage and period is None):
            result['unmapped'].append({'kind':kind,'stage':stage,'home':values[0],'away':values[1]})
            continue
        if not coherent or kind == '1':
            continue
        row = dict(name=metric, metric_key=metric, home=values[0], away=values[1],
            source='PARI-SSIS', source_event_id=str(event['id']), match_status='exact_event_id_match',
            source_url=BASE+'eventStatistic?eventIid='+str(data.get('eventIid'))+'&sysId=21',
            observed_at_utc=observed_at, score_basis=list(event['score']), kind=kind, stage=stage)
        if period is not None:
            row['period'] = period
        result['periods' if period is not None else 'statistics'].append(row)
    return result


def parse_review(payload, event, observed_at):
    data = body(payload)
    sides = participants(data, event)
    if not sides:
        return None
    history = []
    cutoff = float(event['start_time'])
    for participant in sides:
        games, seen = [], set()
        details = {str(x.get('eventIid')):x for x in participant.get('lastEventsParticipantStats') or []}
        for game in participant.get('lastEvents') or []:
            iid = str(game.get('iid') or '')
            try:
                start = float(game.get('startTime'))
            except (TypeError, ValueError):
                continue
            rivals = game.get('participants') or []
            if not iid or iid in seen or not 0 < start < cutoff or len(rivals) != 2:
                continue
            if str(participant['id']) not in {str(p.get('id')) for p in rivals}:
                continue
            seen.add(iid)
            # Retain every supplied historical statistic, separate from LIVE.
            detail = details.get(iid, {})
            if str(detail.get('eventStartTime')) != str(game.get('startTime')):
                detail = {}
            games.append({'event_iid':iid, 'start_time':start,
                'league':game.get('eventGroup',{}).get('name',{}),
                'participant_statistics':copy.deepcopy(detail.get('stats') or []),
                'opponent_statistics':copy.deepcopy(detail.get('rivalStats') or []),
                'participants':[{k:p.get(k) for k in ('id','name','order','stats')} for p in rivals]})
        games.sort(key=lambda g:g['start_time'], reverse=True)
        totals = []
        for game in games[:10]:
            pairs = [stat_map(p) for p in game['participants']]
            try:
                if not all(m.get(('1','')) in ('1','2','3') for m in pairs):
                    continue
                values = [int(m[('30','')]) for m in pairs]
                own = next(i for i,p in enumerate(game['participants']) if str(p['id']) == str(participant['id']))
                totals.append((values[own], values[1-own]))
            except (KeyError, ValueError, TypeError, StopIteration):
                continue
        summary = {'sample_size':len(totals)}
        if totals:
            summary.update(points_for=sum(x[0] for x in totals)/len(totals),
                points_against=sum(x[1] for x in totals)/len(totals))
        history.append({'participant_id':str(participant['id']), 'name':participant.get('name'), 'games':games[:10], 'summary':summary})
    return {'event_iid':str(data.get('eventIid') or ''), 'source':'PARI-SSIS',
        'source_url':BASE+'eventReview?eventId='+str(event['id'])+'&sysId=21',
        'observed_at_utc':observed_at, 'teams':history,
        'league':data.get('eventGroup',{}).get('name',{}),
        'usage':'historical_context_only_not_calibrated'}


async def enrich(events):
    """Bounded rotation: native statistics first; history cached independently."""
    global _CURSOR
    eligible = [e for e in events if e.get('id') and (e.get('raw') or {}).get('team1Id')]
    if not eligible:
        return {'matched':0,'errors':[]}
    offset = _CURSOR % len(eligible)
    selected = (eligible[offset:]+eligible[:offset])[:24]
    _CURSOR = (offset+len(selected)) % len(eligible)
    sem = asyncio.Semaphore(4)
    errors, matched = [], []
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
        async def request(route, params):
            async with sem:
                r = await client.get(BASE+route, params={**params,'sysId':21})
                r.raise_for_status()
                return r.json()
        async def one(event):
            identity = (str(event['id']),str(event.get('start_time')),
                str(event['raw'].get('team1Id')),str(event['raw'].get('team2Id')))
            try:
                cached = _REVIEWS.get(identity)
                if not cached or cached[0] <= time.time():
                    payload = await request('eventReview', {'eventId':event['id']})
                    history = parse_review(payload,event,datetime.now(timezone.utc).isoformat())
                    if not history:
                        event['pari_ssis_diagnostics'] = {'state':'review_unavailable'}
                        return
                    _REVIEWS[identity] = (time.time()+600,history)
                history = copy.deepcopy(_REVIEWS[identity][1])
                event['team_history'] = history
                if not event.get('league'):
                    event['league'] = history['league'].get('rus') or history['league'].get('eng') or ''
                payload = await request('eventStatistic', {'eventIid':history['event_iid']})
                result = parse_statistics(payload,event,datetime.now(timezone.utc).isoformat())
                event.setdefault('external_statistics',[]).extend(result.pop('statistics'))
                event.setdefault('period_statistics',[]).extend(result.pop('periods'))
                event['pari_ssis_diagnostics'] = result
                matched.append(event['id'])
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                event['pari_ssis_diagnostics'] = {'state':'source_unavailable','error':type(exc).__name__}
                errors.append(type(exc).__name__)
        await asyncio.gather(*(one(e) for e in selected))
    for key in list(_REVIEWS):
        if _REVIEWS[key][0] <= time.time():
            del _REVIEWS[key]
    return {'matched':len(matched),'errors':errors[:8], 'requested':len(selected)}
