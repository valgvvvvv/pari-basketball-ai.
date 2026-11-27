"""Owner-run final verification, replay evaluation and scheduled shadow training.

Checkpoint contains public game observations only, never Telegram logs/tokens.
Unresolved results remain unresolved; disappearance is never a final score.
"""
import argparse
import asyncio
import gzip
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import espn_college
import learning_model as model
from signal_results import ResultsParser, normalized, primary_final_evidence

SITE = 'https://pari-basketball-ai.onrender.com'
SLUGS = {'ncaa_m':'mens-college-basketball', 'ncaa_w':'womens-college-basketball', 'nba':'nba'}


def second_espn(origin, board, summary, count, period):
    """Native participant-ID verified aliases plus date, competition and full scores."""
    sides = espn_college.match(origin, board)
    if sides is None:
        return None
    comps = (summary.get('header') or {}).get('competitions') or []
    if len(comps) != 1 or str(comps[0].get('id')) != str(board.get('id')) or (comps[0].get('status') or {}).get('type', {}).get('completed') is not True:
        return None
    lookup = {str(p.get('id')):p for p in comps[0].get('competitors') or []}
    try:
        teams = [lookup[str(p['id'])] for p in sides]
        scores = [[int(q.get('value', q.get('displayValue'))) for q in p['linescores']] for p in teams]
        if any(len(s) < count or sum(s) != int(p['score']) for p, s in zip(teams, scores)):
            return None
        return [sum(s[:count]) if period == 0 else s[period-1] for s in scores]
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def verified_training_row(observation, primary, secondary, sources, observed_at):
    p, origin = observation['prediction'], observation['origin']
    if not primary or not secondary or primary != secondary or len(primary) != 2 or any(not model.number(v) or v < 0 for v in primary):
        return None
    if len(set(sources)) < 2 or not any('PARI' in s for s in sources):
        return None
    if (model.stamp(observation['captured_at']) or float('inf')) >= (model.stamp(observed_at) or 0):
        return None
    actual = sum(primary)
    if actual < p['state']['current']:
        return None
    start = origin.get('start_time')
    try:
        started = datetime.fromtimestamp(float(start), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
    return dict(game_id='pari:'+observation['event_id'], cohort=p['cohort'], target=observation['target'],
                period=observation['period'], started_at=started, captured_at=observation['captured_at'], completed_at=observed_at,
                state=p['state'], actual=actual, provenance='independent_live_final', sources=sources,
                frozen_prediction={k:p.get(k) for k in ('center', 'interval80', 'model_version', 'baseline_center', 'market_prediction')})


async def reconcile(observations, labels, cursor=0):
    unresolved = [r for k, r in observations.items() if k not in labels and time.time()-(model.stamp(r.get('captured_at')) or 0) < 7*86400]
    ids = sorted({r['event_id'] for r in unresolved})
    if not ids:
        return cursor, {'pending_games':0, 'verified_checkpoints':0}
    selected = (ids+ids)[cursor % len(ids):cursor % len(ids)+min(96, len(ids))]
    now = datetime.now(timezone.utc).isoformat()
    cache, diagnostics = {}, dict(pending_games=len(ids), queried_games=len(selected), verified_checkpoints=0, unresolved=0)
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        async def get(url, params=None):
            key = url+json.dumps(params, sort_keys=True)
            if key not in cache:
                response = await client.get(url, params=params)
                response.raise_for_status()
                cache[key] = response.json()
            return cache[key]
        semaphore = asyncio.Semaphore(4)
        async def check_identity(identity):
            async with semaphore:
                items = [r for r in unresolved if r['event_id'] == identity]
                origin = items[0]['origin']
                count = origin.get('period_count')
                if count not in (2, 4):
                    return
                native, board_events, secondary = {}, [], None
                try:
                    native = await get('https://line-vk01-w.pb06e2-resources.ru/events/event', {'eventId':identity, 'lang':'ru', 'scopeMarket':'2300'})
                except (httpx.HTTPError, ValueError):
                    pass
                group = (model.cohort(origin) or ('universal',))[0]
                # ESPN linkage exists only for ID-verified NCAA aliases, not guessed translations.
                if group in ('ncaa_m', 'ncaa_w'):
                    try:
                        day = datetime.fromtimestamp(float(origin['start_time']), timezone.utc).strftime('%Y%m%d')
                        board_events = (await get(espn_college.BASE+SLUGS[group]+'/scoreboard', {'dates':day, 'groups':50, 'limit':500})).get('events') or []
                        matched = [e for e in board_events if espn_college.match(origin, e) is not None]
                        if len(matched) == 1:
                            secondary = (matched[0], await get(espn_college.BASE+SLUGS[group]+'/summary', {'event':matched[0]['id']}))
                    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
                        pass
                # The existing independent Russian result protocol also covers other leagues.
                result = None
                try:
                    day = datetime.fromtimestamp(float(origin['start_time']), timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).date().isoformat()
                    key = 'scorebasket:'+day
                    if key not in cache:
                        response = await client.get('https://scorebasketball.ru/matches/'+day+'/')
                        response.raise_for_status()
                        parser = ResultsParser(); parser.feed(response.text); cache[key] = parser.rows
                    result = cache[key].get(identity)
                except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
                    pass
                for r in items:
                    period = 0 if r['target'] == 'match' else r['period']
                    fake = {'payload': {'event':identity, 'signal': {'target':'match' if period == 0 else 'half' if count == 2 else 'quarter', 'period':r['period']}, 'origin':origin}}
                    primary = primary_final_evidence(fake, native, 'PARI /events/event')
                    pair = primary['score'] if primary else None
                    if not pair and period > 0:
                        pair = (r.get('primary_periods', {}).get(str(period)) or {}).get('score')
                    second, source = None, None
                    if secondary:
                        second = second_espn(origin, *secondary, count, period)
                        source = 'ESPN: '+espn_college.BASE+SLUGS[group]+'/summary?event='+str(secondary[0]['id'])
                    if second is None and result and result.get('final') is True:
                        try:
                            ps = result['periods']
                            if (len(ps) >= count and all(len(q) == 2 and all(isinstance(v, int) and v >= 0 for v in q) for q in ps)
                                    and result['teams'] == [origin['team1'], origin['team2']]
                                    and [sum(q[j] for q in ps) for j in (0, 1)] == result['score']):
                                second = [sum(q[j] for q in ps[:count]) for j in (0, 1)] if period == 0 else ps[period-1]
                                source = 'ScoreBasketball: '+str(result.get('url'))
                        except (KeyError, IndexError, TypeError):
                            pass
                    verified = verified_training_row(r, pair, second, ['PARI: exact completed protocol', source] if source else [], now)
                    if verified:
                        labels[r['id']] = verified
                        diagnostics['verified_checkpoints'] += 1
                    else:
                        diagnostics['unresolved'] += 1
        await asyncio.gather(*(check_identity(identity) for identity in selected))
    return (cursor+len(selected)) % len(ids), diagnostics


def prospective_report(labels):
    groups = defaultdict(lambda: defaultdict(list))
    for r in labels.values():
        p = r.get('frozen_prediction') or {}
        if not model.number(p.get('center')):
            continue
        groups[(p['model_version'], r['cohort'], r['target'])][r['game_id']].append((abs(p['center']-r['actual']),
            abs(p['baseline_center']-r['actual']) if model.number(p.get('baseline_center')) else None))
    report = []
    for (version, cohort, target), games in groups.items():
        game_errors = [sum(x[0] for x in v)/len(v) for v in games.values()]
        base = [sum(x[1] for x in v if x[1] is not None)/sum(x[1] is not None for x in v) for v in games.values() if any(x[1] is not None for x in v)]
        paired = [sum(x[0] for x in v if x[1] is not None)/sum(x[1] is not None for x in v) for v in games.values() if any(x[1] is not None for x in v)]
        relevant = [r for r in labels.values() if (r.get('frozen_prediction', {}).get('model_version'), r['cohort'], r['target']) == (version, cohort, target)]
        market_games = defaultdict(list)
        for r in relevant:
            mp = r['frozen_prediction'].get('market_prediction')
            if not mp:
                continue
            line, probs, market = mp['line'], mp['probabilities'], mp['market']
            if market.get('includesOvertime') is not False or market.get('rulesConfirmed') is not True or market.get('pricesVerified') is not True:
                continue
            over, under = market.get('overOdds'), market.get('underOdds')
            if not all(model.number(v) and v > 1 for v in (over, under)):
                continue
            truth = [int(r['actual'] > line), int(r['actual'] < line), int(r['actual'] == line)]
            brier = sum((probs[k]-y)**2 for k, y in zip(('pOver', 'pUnder', 'pPush'), truth))
            margin = 1/over+1/under
            market_brier = sum((p-y)**2 for p, y in zip((1/over/margin, 1/under/margin, 0), truth)) if line % 1 != 0 else None
            side = 'over' if probs['pOver'] > probs['pUnder'] else 'under' if probs['pUnder'] > probs['pOver'] else None
            pnl = 0 if truth[2] or side is None else (over-1 if side == 'over' else under-1) if (truth[0] and side == 'over') or (truth[1] and side == 'under') else -1
            market_games[r['game_id']].append((brier, market_brier, pnl))
        # ROI uses one frozen entry per game/target, not repeated minute snapshots.
        trades = [v[0] for _, v in sorted(market_games.items())]
        report.append(dict(version=version, cohort=cohort, target=target, games=len(games), mae=sum(game_errors)/len(game_errors), incumbent_mae=sum(base)/len(base) if base else None,
                           comparable_incumbent_games=len(base), calibrated=False, probability_and_market_metrics='unavailable until frozen compatible prices/probabilities are collected'))
        report[-1]['market'] = dict(games=len(trades), brier=sum(v[0] for v in trades)/len(trades) if trades else None,
            market_brier=sum(v[1] for v in trades if v[1] is not None)/sum(v[1] is not None for v in trades) if any(v[1] is not None for v in trades) else None,
            virtual_one_entry_roi=sum(v[2] for v in trades)/len(trades) if trades else None,
            uncertainty='Not an accuracy or profitability claim; low sample sizes remain experimental')
        report[-1]['comparable_challenger_mae'] = sum(paired)/len(paired) if paired else None
    return report


async def run(directory):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    path = root/'checkpoint.json.gz'
    state = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {'observations':{}, 'labels':{}, 'cursor':0}
    async with httpx.AsyncClient(timeout=45) as client:
        cursor = 0
        for _ in range(100):
            response = await client.get(SITE+'/api/learning/export', params={'after_cursor':cursor,'limit':2000})
            response.raise_for_status()
            batch = [json.loads(line) for line in response.text.splitlines() if line.strip()]
            for r in batch:
                if r['id'] not in state['observations']:
                    state['observations'][r['id']] = r
                else:
                    # Only append corroboration, never replace the original forecast.
                    old = state['observations'][r['id']]
                    for k, v in (r.get('primary_periods') or {}).items():
                        if k in old.setdefault('primary_periods', {}) and old['primary_periods'][k]['score'] != v['score']:
                            old['result_conflict'] = True
                        else:
                            old['primary_periods'][k] = v
            if len(batch) < 2000 or not batch[-1].get('capture_cursor'):
                break
            next_cursor = batch[-1]['capture_cursor']
            if next_cursor <= cursor:
                raise ValueError('Learning export cursor did not advance')
            cursor = next_cursor
        else:
            raise ValueError('Learning backlog exceeds bounded collection; preserve checkpoint and investigate')
    eligible = {k:v for k, v in state['observations'].items() if not v.get('result_conflict')}
    state['cursor'], diagnostic = await reconcile(eligible, state['labels'], state['cursor'])
    path.write_bytes(gzip.compress(json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()))
    seed = json.loads(gzip.decompress((Path(__file__).parent/'models'/'learning-seed.json.gz').read_bytes()))
    universal_seed = json.loads(gzip.decompress((Path(__file__).parent/'models'/'learning-universal-seed.json.gz').read_bytes()))
    fresh = [r for k,r in state['labels'].items() if not state['observations'].get(k,{}).get('result_conflict')]
    universal_fresh = [{**r, 'cohort':'universal','origin_cohort':r['cohort']} for r in fresh if r['cohort'] != 'universal' and model.number(r['state'].get('period_length'))]
    rows = seed+universal_seed+fresh+universal_fresh
    a = model.train(rows)
    a['prospective_report'] = prospective_report(state['labels'])
    a['collection'] = {**diagnostic, 'frozen_observations':len(state['observations']), 'independent_labeled_checkpoints':len(state['labels']), 'independent_games':len({r['game_id'] for r in state['labels'].values()})}
    # A malformed/empty candidate never replaces the bundled model.
    if a['models']:
        (root/'learning-model.json').write_text(json.dumps(a, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        model.load(root/'learning-model.json')
    path.write_bytes(gzip.compress(json.dumps(state, ensure_ascii=False, separators=(',', ':')).encode()))
    print(json.dumps({'collection':a['collection'], 'models':len(a['models']), 'status':'shadow', 'calibrated':False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', default='learning-checkpoint')
    args = parser.parse_args()
    asyncio.run(run(args.directory))
