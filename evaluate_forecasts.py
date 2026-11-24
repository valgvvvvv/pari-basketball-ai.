"""Offline evaluation of immutable, independently settled journal records.

No fitting on the test set, no external requests and no automatic model promotion.
"""
import argparse
import json
import math
import sqlite3
from contextlib import closing
from collections import defaultdict


def summarize(rows):
    errors, covered, briers, baseline_errors, market_briers = [], [], [], [], []
    bins = defaultdict(list)
    units = defaultdict(list)
    games = set()
    for row in rows:
        games.add(row['event_id'])
        d, pred, outcome = row['decision'], row['prediction'], row['outcome']
        final = outcome['source_record']
        actual = final.get('regulation_total' if d['target'] == 'match' else 'period_total')
        if isinstance(actual, (int, float)) and math.isfinite(actual):
            if isinstance(pred.get('center'), (int, float)):
                errors.append(pred['center'] - actual)
            interval = pred.get('interval80') or []
            if len(interval) == 2 and all(isinstance(x, (int, float)) for x in interval):
                covered.append(interval[0] <= actual <= interval[1])
            # Simple score/time baseline, evaluated against exactly the same target.
            clock = pred.get('clock') or ''
            if ':' in clock and pred.get('periodLengthSeconds') and pred.get('current') is not None:
                minutes, seconds = map(int, clock.split(':'))
                length = pred['periodLengthSeconds']
                elapsed = length - (minutes * 60 + seconds)
                total_time = length
                if d['target'] == 'match':
                    elapsed += length * (pred['period'] - 1)
                    count = pred.get('periodCount')
                    total_time = length*count if count in (2, 4) else None
                if elapsed > 0 and total_time is not None:
                    baseline_errors.append(pred['current'] * total_time / elapsed - actual)
            y = [float(actual > d['line']), float(actual < d['line']), float(actual == d['line'])]
            briers.append(sum((p - truth) ** 2 for p, truth in zip([d['pOver'], d['pUnder'], d['pPush']], y)))
            # Market baseline is comparable only when the settlement target matches the prediction.
            c = d.get('comparison')
            if c and d['pPush'] == 0 and (d.get('market') or {}).get('includesOvertime') is False:
                market_briers.append(sum((p - truth) ** 2 for p, truth in zip([c['noVigOver'], c['noVigUnder'], 0], y)))
            if actual != d['line'] and d['pPush'] < 1:
                confidence = d['probability'] / (1 - d['pPush'])
                won = (actual > d['line']) == (d['direction'] == 'ТБ')
                bins[min(9, int(confidence * 10))].append((confidence, won))
        pnl = outcome.get('virtual_unit_pnl')
        if pnl is not None:
            units[d['status']].append(pnl)
    mean = lambda values: sum(values) / len(values) if values else None
    return {'games': len(games), 'checkpoints': len(rows),
            'regulation_mae': mean([abs(x) for x in errors]), 'regulation_bias': mean(errors),
            'simple_tempo_mae': mean([abs(x) for x in baseline_errors]),
            'interval80_observed_coverage': mean(covered), 'three_class_brier': mean(briers),
            'market_brier_same_target': mean(market_briers), 'market_comparable_checkpoints': len(market_briers),
            'reliability_conditional_on_no_push': [{'bin': f'{k*10}-{(k+1)*10}%', 'count': len(v),
                'mean_model_probability': mean([a for a, _ in v]), 'observed_win_rate': mean([b for _, b in v])}
                for k, v in sorted(bins.items())],
            'virtual_market_roi_by_status': {k: {'count': len(v), 'pnl': sum(v), 'roi': mean(v)} for k, v in units.items()}}


def evaluate(rows):
    versions = {x.get('model_source_sha256', 'unknown') for x in rows}
    if len(versions) > 1:
        return {'schema': 1, 'promotion_allowed': False, 'by_model_source_sha256': {
            version: evaluate([x for x in rows if x.get('model_source_sha256', 'unknown') == version])
            for version in sorted(versions)}, 'note': 'Different model versions are never pooled'}
    # A single predeclared first checkpoint per game/target/quarter, not per line.
    # Choose BEFORE looking at whether a result exists; missing first results remain missing.
    first = {}
    for row in sorted(rows, key=lambda x: (x['captured_at_utc'], x['id'])):
        d = row['decision']
        if set(d.get('reasonCodes', [])) & {'data', 'window', 'preliminary'}:
            continue
        key = (row['event_id'], d['target'], d['period'] if d['target'] in ('quarter', 'half') else 0)
        if key not in first or (row['captured_at_utc'] == first[key]['captured_at_utc'] and
                abs(d['line'] - row['prediction']['center']) < abs(first[key]['decision']['line'] - first[key]['prediction']['center'])):
            first[key] = row
    selected = list(first.values())
    game_time = {}
    for row in selected:
        game_time[row['event_id']] = min(game_time.get(row['event_id'], row['captured_at_utc']), row['captured_at_utc'])
    ordered = sorted(game_time, key=lambda x: (game_time[x], x))
    # Fixed whole-game chronological partition; not a substitute for a trained calibration artifact.
    cutoff = max(1, int(len(ordered) * .7)) if ordered else 0
    development = set(ordered[:cutoff])
    labeled = [x for x in selected if x.get('outcome')]
    return {'schema': 1, 'selection': 'first eligible checkpoint; nearest line to forecast center; no future line search',
        'selected_games': len(ordered), 'selected_checkpoints': len(selected),
        'unresolved_checkpoints': len(selected) - len(labeled), 'development_game_ids': ordered[:cutoff], 'test_game_ids': ordered[cutoff:],
        'partitions': {name: {target: summarize([x for x in labeled if x['decision']['target'] == target and
            ((x['event_id'] in development) == is_dev)]) for target in ('match', 'quarter', 'half')}
            for name, is_dev in [('development', True), ('test', False)]},
        'promotion_allowed': False,
        'limitations': ['No calibrator trained by this report', 'Multiple targets from one game remain correlated',
                        'Sample size alone does not establish edge', 'ROI by class; shadow is not an executable recommendation']}


def load_database(location):
    with closing(sqlite3.connect(location)) as db:
        rows = []
        for identity, payload, outcome, label in db.execute('SELECT f.id,f.payload,o.payload,l.payload FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id LEFT JOIN labels l ON l.forecast_id=f.id'):
            resolved = json.loads(outcome) if outcome else {'source_record': json.loads(label), 'virtual_unit_pnl': None} if label else None
            rows.append({'id': identity, **json.loads(payload), 'outcome': resolved})
        return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    with open(args.output, 'w', encoding='utf-8') as stream:
        json.dump(evaluate(load_database(args.database)), stream, ensure_ascii=False, indent=2, allow_nan=False)
