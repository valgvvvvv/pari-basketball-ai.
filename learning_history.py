"""Public ESPN historical play-by-play bootstrap, explicitly retrospective.

Final box scores are labels only. Features are built from plays up to the chosen
clock; whole-game totals/late corrections are never inserted as live inputs.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from learning_model import number, stamp

BASE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/'
LEAGUES = {'ncaa_m': ('mens-college-basketball', 2, 1200),
           'ncaa_w': ('womens-college-basketball', 4, 600), 'nba': ('nba', 4, 720)}


def parse_game(payload, group):
    slug, count, length = LEAGUES[group]
    header = payload.get('header') or {}
    comps = header.get('competitions') or []
    if len(comps) != 1:
        return []
    comp = comps[0]
    if (comp.get('status') or {}).get('type', {}).get('completed') is not True:
        return []
    competitors = comp.get('competitors') or []
    if len(competitors) != 2 or {p.get('homeAway') for p in competitors} != {'home', 'away'}:
        return []
    ordered = sorted(competitors, key=lambda p: p['homeAway'] != 'home')
    # Explicit linescores and PBP must independently reconcile arithmetically.
    try:
        value = lambda x: int(x.get('value', x.get('displayValue')))
        periods = [[value(p['linescores'][i]) for p in ordered] for i in range(count)]
        total = [int(p['score']) for p in ordered]
        if any(sum(value(x) for x in p['linescores']) != int(p['score']) for p in ordered):
            return []
        regulation_format = (payload.get('format') or {}).get('regulation') or {}
        if regulation_format.get('periods') != count or regulation_format.get('clock') != length:
            return []
    except (ValueError, TypeError, KeyError, IndexError):
        return []
    points = []
    last_elapsed, last_score = -1, [0, 0]
    for play in payload.get('plays') or []:
        try:
            period = int(play['period']['number'])
            clock = str(play['clock']['displayValue'])
            if ':' in clock:
                mm, ss = clock.split(':')
                remaining = int(mm)*60+float(ss)
            else:
                remaining = float(clock)  # NBA uses decimal seconds below 1:00
            if not number(remaining):
                return []
            score = [int(play['homeScore']), int(play['awayScore'])]
            if not 1 <= period <= count or not 0 <= remaining <= length:
                continue
            elapsed = (period-1)*length+length-remaining
            observed = play.get('wallclock')
            if stamp(observed) is None:
                continue
            if elapsed < last_elapsed or any(a < b for a, b in zip(score, last_score)):
                return []  # revised/incomplete sequence is not silently repaired
            points.append(dict(elapsed=elapsed, period=period, score=score, observed=observed))
            last_elapsed, last_score = elapsed, score
        except (KeyError, ValueError, TypeError):
            return []
    regulation = [sum(q[j] for q in periods) for j in (0, 1)]
    if not points or points[0]['elapsed'] != 0 or points[0]['score'] != [0, 0] or points[-1]['elapsed'] != count*length or points[-1]['score'] != regulation:
        return []
    completed = max((p.get('wallclock') for p in payload.get('plays') or [] if stamp(p.get('wallclock')) is not None), key=stamp)
    started = comp.get('date')
    if stamp(started) is None or stamp(completed) <= stamp(started):
        return []
    rows = []
    used = set()
    for point in points:
        elapsed, period = point['elapsed'], point['period']
        pe = elapsed-(period-1)*length
        if pe < 180 or pe >= length or elapsed <= 0:
            continue
        previous_score = [sum(q[j] for q in periods[:period-1]) for j in (0, 1)]
        # Using completed previous periods is legitimate; current/future periods never enter features.
        period_score = [a-b for a, b in zip(point['score'], previous_score)]
        if min(period_score) < 0:
            return []
        recent = [p for p in points if p['elapsed'] <= elapsed-120]
        recent_rate = None
        if recent and elapsed-recent[-1]['elapsed'] <= 150:
            recent_rate = (sum(point['score'])-sum(recent[-1]['score']))/((elapsed-recent[-1]['elapsed'])/60)
        for target in ('match', 'period'):
            remaining = count*length-elapsed if target == 'match' else length-pe
            # Train only supported prospective windows, with identical live gates.
            if target == 'match' and (period != count or pe < (720 if count == 2 else 120)):
                continue
            if target == 'period' and not ((480 <= pe <= 960) if count == 2 else (240 <= pe <= 480)):
                continue
            key = (target, period, int(pe//60))
            if key in used:
                continue
            used.add(key)
            current = sum(point['score']) if target == 'match' else sum(period_score)
            state = dict(current=current, overall_current=sum(point['score']), elapsed=elapsed, remaining=remaining,
                         period_elapsed=pe, period_current=sum(period_score), difference=point['score'][0]-point['score'][1],
                         previous_rate=sum(previous_score)/((period-1)*length/60) if period > 1 else None,
                         recent_rate=recent_rate, duration=count*length, statistics={})
            rows.append(dict(game_id='espn:'+slug+':'+str(header['id']), cohort=group, target=target,
                             period=period, started_at=started, captured_at=point['observed'], completed_at=completed,
                             actual=sum(regulation) if target == 'match' else sum(periods[period-1]), state=state,
                             provenance='espn_historical_replay', source_url=BASE+slug+'/summary?event='+str(header['id'])))
    return rows


async def bootstrap(cache_dir, end_day, days=24, limit=150, groups=None):
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows, report = [], {}
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=20) as client:
        async def get(url, filename, params):
            path = cache/filename
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
            async with semaphore:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                path.write_text(json.dumps(payload), encoding='utf-8')
                return payload
        for group, (slug, _, _) in LEAGUES.items():
            if groups and group not in groups:
                continue
            events, errors = {}, 0
            # Chronological, fixed quota per day to prevent one tournament day dominating.
            dates = [(end_day-timedelta(days=i)).strftime('%Y%m%d') for i in reversed(range(days))]
            for day in dates:
                try:
                    params = {'dates':day, 'limit':500}
                    if group != 'nba':
                        params['groups'] = 50
                    board = await get(BASE+slug+'/scoreboard', slug+'-board-v2-'+day+'.json', params)
                    eligible = [e for e in board.get('events') or [] if (e.get('status') or {}).get('type', {}).get('completed') is True]
                    quota = max(6, (limit+days-1)//days)
                    for e in eligible[:quota]:
                        events[str(e['id'])] = e
                except (httpx.HTTPError, ValueError):
                    errors += 1
            async def load_game(identity):
                try:
                    payload = await get(BASE+slug+'/summary', slug+'-'+identity+'.json', {'event':identity})
                    return parse_game(payload, group)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    return []
            batches = await asyncio.gather(*(load_game(i) for i in list(events)[:limit]))
            accepted = [b for b in batches if b]
            rows.extend(r for b in accepted for r in b)
            report[group] = dict(requested=len(events), accepted_games=len(accepted), failed_or_incomplete=len(batches)-len(accepted), board_errors=errors)
    return rows, report
