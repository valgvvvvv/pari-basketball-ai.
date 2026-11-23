"""Decode the verified public PARI basketball event stream, never opaque box codes.

Schema: PARI matchCenter.entity.basketball.js EventProcessor. Corrections 1020
remove i2; 1536 = team/points/remaining/period/correction; 1565 = team/score.
All counters require exact reconstruction of the current root scoreboard.
"""
from collections import Counter, defaultdict


def integer(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def parse(event, live, metadata, response, source_url):
    result = {'statistics': [], 'periods': [], 'diagnostics': {
        'state': 'unavailable', 'source_url': source_url, 'parser': 'pari-basketball-events-v1'}}
    diag = result['diagnostics']
    extra = response.get('extraInfo', {}) if isinstance(response, dict) else {}
    if (str(extra.get('id')) != str(metadata.get('code')) or
            str(extra.get('sportId')) != '11726632' or
            str(metadata.get('fon_code')) != str(event.get('id')) or
            any(str(metadata.get(a) or '').casefold().strip() != str(event.get(b) or '').casefold().strip()
                for a, b in [('fon_team1','team1'), ('fon_team2','team2')])):
        diag['state'] = 'identity_mismatch'
        return result
    rows = response.get('events')
    if not isinstance(rows, list) or not rows:
        diag['state'] = 'no_events'
        return result
    if any(not isinstance(r, dict) or integer(r.get('id')) is None for r in rows):
        diag['state'] = 'malformed_events'
        return result
    # Duplicate IDs with different content are not a second sporting event.
    unique = {}
    for row in rows:
        identity = integer(row['id'])
        if identity in unique and unique[identity] != row:
            diag['state'] = 'conflicting_event_ids'
            return result
        unique[identity] = row
    removed = {integer(r.get('i2')) for r in rows if r.get('type') == 1020}
    rows = [r for key, r in sorted(unique.items()) if key not in removed and r.get('type') != 1020]
    diag.update(events=len(rows), removed_events=len(removed), version=extra.get('version'))
    reverse = extra.get('teamsReverse')
    if not isinstance(reverse, bool):
        diag['state'] = 'team_orientation_unknown'
        return result

    def side(row):
        team = integer(row.get('i1'))
        return (3-team if reverse else team) if team in (1, 2) else None

    scores = {1: [], 2: []}
    counters = defaultdict(Counter)
    periods = set()
    stream_score = {}
    last_clock = None
    for row in rows:
        kind, team = row.get('type'), side(row)
        if kind == 1565 and team:
            stream_score[team] = integer(row.get('i2'))
        if kind in (1500, 1503, 1504):
            time, period = integer(row.get('i1')), integer(row.get('i2'))
            if time is not None and period:
                last_clock = (period, time)
        fields = {1536: ('i4','i3'), 1565: ('i4','i3'), 1551: ('i4','i3'),
                  1524: ('i4','i3'), 1532: ('i3','i2'), 1522: ('i3','i2'),
                  1518: ('i3','i2'), 1530: ('i3','i2'), 1531: ('i3','i2'),
                  1533: ('i3','i2'), 1534: ('i3','i2'), 1535: ('i3','i2'), 1537: ('i3','i2')}
        if kind in fields:
            period_field, time_field = fields[kind]
            q, remaining = integer(row.get(period_field)), integer(row.get(time_field))
            if q and remaining is not None:
                last_clock = (q, remaining)
        if kind == 1536 and team:
            points, period, correction = integer(row.get('i2')), integer(row.get('i4')), integer(row.get('i5', 0))
            if points not in (1, 2, 3) or not period or correction not in (0, 1, 2):
                diag['state'] = 'unsupported_score_correction'
                return result
            periods.add(period)
            if correction == 2:
                index = next((i for i in range(len(scores[team])-1, -1, -1) if scores[team][i][1] == points), None)
                if index is None:
                    diag['state'] = 'unresolved_score_correction'
                    return result
                scores[team].pop(index)
            else:
                scores[team].append((period, points))
        if team and kind in (1518, 1530, 1531, 1533, 1534, 1535, 1537):
            period = integer(row.get('i3'))
            if not period:
                continue
            if kind == 1534:
                value = integer(row.get('i4'))
                # A missing shot value never silently becomes a field-goal attempt.
                counters[(period, team)]['miss_'+str(value)] += 1
            else:
                metric = {1518: 'fouls', 1530: 'miss_1', 1531: 'turnovers',
                          1533: 'rebounds', 1535: 'steals', 1537: 'blocks'}[kind]
                counters[(period, team)][metric] += 1
    observed = [sum(p for _, p in scores[team]) for team in (1, 2)]
    try:
        pair = live['scores'][0][0]
        root_score = [integer(pair['c1']), integer(pair['c2'])]
        source_periods = live['scores'][1]
    except (KeyError, IndexError, TypeError):
        diag['state'] = 'score_unavailable'
        return result
    diag.update(reconstructed_score=observed, root_score=root_score, last_clock=last_clock)
    if observed != root_score or not periods or min(periods) != 1:
        diag['state'] = 'score_not_synchronized'
        return result
    if any(value is not None and value != observed[team-1] for team, value in stream_score.items()):
        diag['state'] = 'source_score_conflict'
        return result
    for number, pair in enumerate(source_periods, 1):
        expected = [integer(pair.get('c1')), integer(pair.get('c2'))]
        actual = [sum(p for q, p in scores[team] if q == number) for team in (1, 2)]
        if expected != actual:
            diag['state'] = 'period_score_conflict'
            return result
    # PARI's cumulative timer may be supplied by the line; compare the explicit
    # source period vector rather than guessing its direction from that number.
    current_period = len(source_periods)
    if last_clock and (last_clock[0] > current_period or current_period-last_clock[0] > 1):
        diag['state'] = 'period_not_synchronized'
        return result
    for team in (1, 2):
        for period, points in scores[team]:
            metric = {1: 'free_throws_made', 2: 'two_pointers_made', 3: 'three_pointers_made'}[points]
            counters[(period, team)][metric] += 1
    all_periods = sorted(set(q for q, _ in counters) | periods)
    made_metrics = ['two_pointers_made', 'three_pointers_made', 'free_throws_made']
    observed_codes = {r.get('type') for r in rows}

    def metrics(counts):
        values = {k: counts[k] for k in made_metrics}
        values['field_goals_made'] = values['two_pointers_made']+values['three_pointers_made']
        # Only provide non-scoring event counters when this stream actually
        # contains that event family. Absence of a family is not a zero.
        for kind, metric in [(1518,'fouls'), (1531,'turnovers'), (1533,'rebounds'), (1535,'steals'), (1537,'blocks')]:
            if kind in observed_codes:
                values[metric] = counts[metric]
        if 1558 in observed_codes:
            values.pop('fouls', None)
            diag['withheld_metrics'] = ['fouls: unsupported foul correction event']
        return values

    def add(target, values, period=None):
        labels = {'two_pointers_made': 'Двухочковые попадания', 'three_pointers_made': 'Трёхочковые попадания',
                  'free_throws_made': 'Штрафные реализованы', 'field_goals_made': 'Попадания с игры',
                  'fouls': 'Фолы', 'rebounds': 'Подборы', 'turnovers': 'Потери', 'steals': 'Перехваты', 'blocks': 'Блок-шоты'}
        for metric in values[0]:
            target.append({'name': labels.get(metric, metric), 'metric_key': metric, 'home': values[0][metric],
                           'away': values[1][metric], 'source': 'PARI-PBP',
                           'source_url': source_url, 'evidence': 'score_reconciled_event_stream',
                           **({'period': period} if period else {})})

    totals = [sum((counters[(q, team)] for q in all_periods), Counter()) for team in (1, 2)]
    add(result['statistics'], [metrics(c) for c in totals])
    for q in all_periods:
        add(result['periods'], [metrics(counters[(q, team)]) for team in (1, 2)], q)
    diag['state'] = 'score_reconciled'
    diag['missing_attempts_note'] = 'No shot-attempt completeness guarantee; FGA/FTA and ORB/DRB are not invented from assignments or possession changes.'
    return result
