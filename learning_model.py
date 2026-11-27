"""Small CPU-only challenger. Fits on whole games; never enables betting signals.

Portable regularized regression predicts remaining points, not a language-model
opinion. Missing optional inputs stay missing. No runtime ML dependencies.
"""
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'remaining-ridge-v1'
COHORTS = {'19166': ('ncaa_m', 2, 1200), '15100': ('ncaa_w', 4, 600)}
METRICS = ('field_goals_attempted', 'free_throws_attempted', 'offensive_rebounds', 'turnovers', 'team_fouls')
MIN_GAMES = 75


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def cohort(event):
    if str(event.get('sport_id')) in COHORTS:
        return COHORTS[str(event['sport_id'])]
    # NBA must be explicit. Do not confuse G-League, summer or esports with NBA.
    if str(event.get('league', '')).strip().casefold() in ('nba', 'сша. nba'):
        return ('nba', 4, 720)
    count, length = event.get('period_count'), event.get('period_length_seconds')
    if count in (2, 4) and number(length) and 0 < length <= 1800:
        return ('universal', count, length)
    return None


def features(s, profile='core'):
    """Same transformation for training and production; outcome is never read."""
    if profile == 'universal':
        required = ('current','overall_current','elapsed','remaining','period_elapsed','difference','duration','period_length')
        if any(not number(s.get(k)) for k in required) or min(s['current'],s['overall_current'],s['elapsed'],s['remaining'],s['period_elapsed']) < 0 or s['duration'] <= 0 or s['period_length'] <= 0:
            raise ValueError('Invalid universal model state')
        rem = s['remaining']/60
        rate = s['overall_current']/(s['elapsed']/60) if s['elapsed'] > 0 else 0
        pr = s.get('period_current')
        period_rate = pr/(s['period_elapsed']/60) if number(pr) and s['period_elapsed'] > 0 else rate
        progress = s['elapsed']/s['duration']
        recent = s.get('recent_rate')
        return (['remaining_minutes','game_rate_horizon','recent_rate_horizon','period_rate_horizon',
                 'progress_rate_horizon','difference_horizon','format_duration_horizon','period_length_horizon',
                 'game_start','recent_missing'],
                [rem,rem*rate,rem*(recent if number(recent) else rate),rem*period_rate,
                 rem*rate*progress,rem*abs(s['difference'])/20,rem*s['duration']/2400,
                 rem*s['period_length']/600,rem*int(s['elapsed']==0),rem*int(not number(recent))])
    required = ('current', 'overall_current', 'elapsed', 'remaining', 'period_elapsed', 'period_current', 'difference', 'duration')
    if any(not number(s.get(k)) for k in required):
        raise ValueError('Incomplete numeric state')
    if min(s['current'], s['overall_current'], s['period_current'], s['remaining']) < 0 or s['elapsed'] <= 0 or s['period_elapsed'] <= 0 or s['duration'] <= 0:
        raise ValueError('Invalid clock or score')
    rem = s['remaining']/60
    rate = s['overall_current']/(s['elapsed']/60)
    period_rate = s['period_current']/(s['period_elapsed']/60)
    previous = s.get('previous_rate')
    recent = s.get('recent_rate')
    x = [rem, rem*rate, rem*period_rate, rem*(previous if number(previous) else rate),
         rem*(recent if number(recent) else rate), rem*abs(s['difference'])/20,
         rem*s['elapsed']/s['duration'], rem*int(not number(recent)), rem*int(not number(previous))]
    names = ['remaining_minutes', 'game_rate_horizon', 'period_rate_horizon', 'previous_rate_horizon',
             'recent_rate_horizon', 'score_difference_horizon', 'game_progress_horizon', 'recent_missing', 'previous_missing']
    if profile == 'full':
        stats = s.get('statistics') or {}
        if any(not number(stats.get(k)) or stats[k] < 0 for k in METRICS):
            raise ValueError('Full statistical profile unavailable')
        x += [rem*stats[k]/(s['elapsed']/60) for k in METRICS]
        names += list(METRICS)
    elif profile != 'core':
        raise ValueError('Unknown feature profile')
    return names, x


def quantile(values, q):
    values = sorted(values)
    return values[min(len(values)-1, max(0, int(q*(len(values)-1))))]


def solve(a, b):
    a = [list(row)+[b[i]] for i, row in enumerate(a)]
    for i in range(len(b)):
        pivot = max(range(i, len(b)), key=lambda j: abs(a[j][i]))
        a[i], a[pivot] = a[pivot], a[i]
        if abs(a[i][i]) < 1e-10:
            raise ValueError('Singular fit')
        scale = a[i][i]
        a[i] = [v/scale for v in a[i]]
        for j in range(len(b)):
            if j != i:
                scale = a[j][i]
                a[j] = [v-scale*w for v, w in zip(a[j], a[i])]
    return [row[-1] for row in a]


def fit(rows, profile):
    vectors = [features(r['state'], profile)[1] for r in rows]
    names = features(rows[0]['state'], profile)[0]
    counts = defaultdict(int)
    for r in rows:
        counts[r['game_id']] += 1
    weights = [1/counts[r['game_id']] for r in rows]
    total = sum(weights)
    means = [sum(w*x[i] for w, x in zip(weights, vectors))/total for i in range(len(names))]
    scales = [max(.01, math.sqrt(sum(w*(x[i]-means[i])**2 for w, x in zip(weights, vectors))/total)) for i in range(len(names))]
    design = [[1]+[(v-m)/d for v, m, d in zip(x, means, scales)] for x in vectors]
    size = len(names)+1
    # Fixed penalty, declared before testing. No search on the held-out games.
    a = [[sum(w*x[i]*x[j] for w, x in zip(weights, design))+(5 if i == j and i else 0) for j in range(size)] for i in range(size)]
    b = [sum(w*x[i]*(r['actual']-r['state']['current']) for w, x, r in zip(weights, design, rows)) for i in range(size)]
    return dict(profile=profile, features=names, means=means, scales=scales, coefficients=solve(a, b))


def center(model, state):
    names, x = features(state, model['profile'])
    if names != model['features']:
        raise ValueError('Feature schema mismatch')
    vector = [1]+[(v-m)/d for v, m, d in zip(x, model['means'], model['scales'])]
    return state['current']+max(0, sum(w*v for w, v in zip(model['coefficients'], vector)))


def interval(model, state):
    p = center(model, state)
    errors = residuals(model, state)
    return [max(state['current'], p+quantile(errors, .1)), max(state['current'], p+quantile(errors, .9))]


def residuals(m, state):
    horizon = '0_2' if state['remaining'] <= 120 else '2_5' if state['remaining'] <= 300 else '5_10' if state['remaining'] <= 600 else '10_20' if state['remaining'] <= 1200 else '20_plus'
    return m.get('horizon_errors', {}).get(horizon, m['calibration_errors'])


def metrics(model, rows):
    games = defaultdict(list)
    for row in rows:
        s, y = row['state'], row['actual']
        p = center(model, s)
        bounds = interval(model, s)
        baseline = s['current']+(s['overall_current']/s['elapsed'])*s['remaining'] if s['elapsed'] > 0 else None
        games[row['game_id']].append((abs(p-y), p-y, abs(baseline-y) if baseline is not None else None, float(bounds[0] <= y <= bounds[1])))
    means = [[sum(x[i] for x in values if x[i] is not None)/sum(x[i] is not None for x in values) if any(x[i] is not None for x in values) else None for i in range(4)] for values in games.values()]
    result = dict(games=len(games), checkpoints=len(rows))
    result.update(zip(('mae', 'bias', 'tempo_mae', 'interval80_coverage'), [sum(x[i] for x in means if x[i] is not None)/sum(x[i] is not None for x in means) if any(x[i] is not None for x in means) else None for i in range(4)]))
    paired = [sum(x[0] for x in values if x[2] is not None)/sum(x[2] is not None for x in values) for values in games.values() if any(x[2] is not None for x in values)]
    result['tempo_comparable_mae'] = sum(paired)/len(paired) if paired else None
    result['tempo_comparable_checkpoints'] = sum(x[2] is not None for values in games.values() for x in values)
    return result


def train(rows):
    """Chronological train/calibration/test split, no game crosses partitions."""
    grouped = defaultdict(list)
    seen, exclusions = set(), defaultdict(int)
    for r in rows:
        try:
            if r.get('provenance') not in ('espn_historical_replay', 'independent_live_final'):
                raise ValueError('Unverified provenance')
            t, end = stamp(r['captured_at']), stamp(r['completed_at'])
            start = stamp(r['started_at'])
            if (t is None or end is None or start is None or not start <= t < end or r.get('target') not in ('match', 'period')
                    or r.get('cohort') not in ('ncaa_m', 'ncaa_w', 'nba', 'universal') or not number(r.get('actual'))
                    or not float(r['actual']).is_integer() or r['actual'] < r['state']['current']):
                raise ValueError('Outcome precedes forecast or score decreased')
            features(r['state'], 'universal' if r['cohort'] == 'universal' else 'core')
            key = (r['cohort'], r['target'], r['game_id'], r['period'], int(r['state']['period_elapsed']//60))
            if key in seen:
                continue
            seen.add(key)
            grouped[(r['cohort'], r['target'])].append(r)
            if r['cohort'] == 'universal':
                length = r['state'].get('period_length')
                count = r['state']['duration']/length
                fmt = {(4,600):'universal_4x10',(4,720):'universal_4x12',(2,1200):'universal_2x20'}.get((count,length))
                if fmt:
                    grouped[(fmt,r['target'])].append(r)
        except (ValueError, KeyError, TypeError):
            exclusions['invalid_or_unverified'] += 1
    models = {}
    for (group, target), samples in sorted(grouped.items()):
        games = sorted({r['game_id'] for r in samples}, key=lambda g: (min(stamp(r['started_at']) for r in samples if r['game_id'] == g), g))
        if len(games) < MIN_GAMES:
            exclusions[group+':'+target+':insufficient_games'] = len(games)
            continue
        # Split by entire start-day too: simultaneous fixtures cannot leak across.
        days = sorted({r['started_at'][:10] for r in samples})
        if len(days) < 5:
            exclusions[group+':'+target+':insufficient_days'] += 1
            continue
        cut1, cut2 = days[int(len(days)*.6)], days[int(len(days)*.8)]
        parts = [[r for r in samples if (r['started_at'][:10] < cut1 if i == 0 else cut1 <= r['started_at'][:10] < cut2 if i == 1 else r['started_at'][:10] >= cut2)] for i in range(3)]
        # A long game finishing after the next partition starts is excluded.
        for i in (0, 1):
            next_start = min(stamp(r['started_at']) for r in parts[i+1])
            parts[i] = [r for r in parts[i] if stamp(r['completed_at']) < next_start]
        if any(len({r['game_id'] for r in p}) < n for p, n in zip(parts, (40, 10, 15))):
            exclusions[group+':'+target+':small_partition'] += 1
            continue
        for profile in (('universal',) if group.startswith('universal') else ('core', 'full')):
            try:
                selected = [[r for r in p if profile in ('core','universal') or all(number((r['state'].get('statistics') or {}).get(k)) for k in METRICS)] for p in parts]
                if any(len({r['game_id'] for r in p}) < n for p, n in zip(selected, (40, 10, 15))):
                    continue
                model = fit(selected[0], profile)
                # One residual per game and clock-minute keeps long games from dominating.
                residuals = defaultdict(list)
                for r in selected[1]:
                    residuals[r['game_id']].append(r['actual']-center(model, r['state']))
                model['calibration_errors'] = [quantile(v, q) for _, v in sorted(residuals.items()) for q in (.1, .3, .5, .7, .9)]
                if profile == 'universal':
                    horizon_rows = defaultdict(lambda: defaultdict(list))
                    for r in selected[1]:
                        left = r['state']['remaining']
                        key = '0_2' if left <= 120 else '2_5' if left <= 300 else '5_10' if left <= 600 else '10_20' if left <= 1200 else '20_plus'
                        horizon_rows[key][r['game_id']].append(r['actual']-center(model,r['state']))
                    model['horizon_errors'] = {k:[quantile(v,q) for _,v in sorted(gs.items()) for q in (.1,.3,.5,.7,.9)] for k,gs in horizon_rows.items() if len(gs)>=10}
                    model['source_cohorts'] = sorted({r.get('origin_cohort',r['cohort']) for p in selected for r in p})
                    model['scope_validated_for_unseen_leagues'] = False
                model['report'] = metrics(model, selected[2])
                model['train_games'] = len({r['game_id'] for r in selected[0]})
                model['calibration_games'] = len(residuals)
                model['trained_through'] = max(r['completed_at'] for p in selected for r in p)
                model['split_days'] = [cut1, cut2]
                model['partition_game_ids'] = [[g for g in games if any(r['game_id'] == g for r in p)] for p in selected]
                model['evidence'] = sorted({r['provenance'] for p in selected for r in p})
                model['support'] = {k: [min(r['state'][k] for p in selected for r in p), max(r['state'][k] for p in selected for r in p)] for k in ('remaining', 'elapsed')}
                models[group+':'+target+':'+profile] = model
            except (ValueError, ZeroDivisionError):
                exclusions[group+':'+target+':fit_failed'] += 1
    artifact = dict(schema=SCHEMA, trained_at=datetime.now(timezone.utc).isoformat(), models=models,
                    exclusions=dict(exclusions), calibrated=False, automatic_priority_enabled=False,
                    method='regularized remaining-points regression; fixed ridge penalty 5; game-balanced loss',
                    validation='chronological whole-game replay; historical replay does not prove live availability or betting edge')
    artifact['version'] = hashlib.sha256(json.dumps({k:v for k,v in artifact.items() if k != 'trained_at'}, sort_keys=True).encode()).hexdigest()[:16]
    return artifact


def empirical(center_value, current, errors, line):
    if not all(number(v) for v in (center_value, current, line)) or not errors or not all(number(v) for v in errors):
        raise ValueError('Invalid empirical probability inputs')
    # Match JavaScript Math.round for nonnegative basketball totals.
    totals = [max(current, math.floor(center_value+e+.5)) for e in errors]
    return dict(pOver=sum(t > line for t in totals)/len(totals), pUnder=sum(t < line for t in totals)/len(totals),
                pPush=sum(t == line for t in totals)/len(totals), calibrated=False)


def load(path):
    artifact = json.loads(Path(path).read_text(encoding='utf-8'))
    if artifact.get('schema') != SCHEMA or artifact.get('calibrated') is not False or artifact.get('automatic_priority_enabled') is not False:
        raise ValueError('Unsupported or promoted model artifact')
    # Reject malformed/non-finite artifacts before they reach live calculations.
    for key, m in artifact['models'].items():
        if key not in ({g+':'+t+':'+p for g in ('ncaa_m','ncaa_w','nba') for t in ('match','period') for p in ('core','full')}|{g+':'+t+':universal' for g in ('universal','universal_4x10','universal_4x12','universal_2x20') for t in ('match','period')}):
            raise ValueError('Unknown model cohort')
        n = len(m['features'])
        if len(m['coefficients']) != n+1 or len(m['means']) != n or len(m['scales']) != n or not m['calibration_errors']:
            raise ValueError('Invalid model dimensions')
        if not all(number(v) for k in ('coefficients', 'means', 'scales', 'calibration_errors') for v in m[k]) or min(m['scales']) <= 0:
            raise ValueError('Invalid model weights')
    return artifact
