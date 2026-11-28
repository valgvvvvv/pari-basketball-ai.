"""Prospective delivery checks and a descriptive audit of frozen receipts.

No weights or thresholds are fitted to the Telegram outcomes in this module.
Repeated lines are not independent observations; unresolved outcomes stay open.
"""
import math
from collections import Counter, defaultdict

import learning_model

POLICY = '2026-11-28-challenger-direction-v1'


def review(card, event):
    signal = card['signal']
    prediction = (event.get('learning') or {}).get('match' if signal['target'] == 'match' else 'period') or {}
    result = dict(policy=POLICY, available=False, allow=True, calibrated=False,
                  reason='challenger_unavailable')
    if prediction.get('status') != 'shadow':
        return result
    try:
        probabilities = learning_model.empirical(prediction['center'], prediction['state']['current'],
                                                prediction['residual_sample'], signal['line'])
    except (KeyError, ValueError, TypeError):
        return result
    selected = probabilities['pOver' if signal['direction'] == 'ТБ' else 'pUnder']
    opposite = probabilities['pUnder' if signal['direction'] == 'ТБ' else 'pOver']
    result.update(available=True, allow=selected > opposite, reason='agreement' if selected > opposite else 'challenger_disagrees',
                  model_version=prediction.get('model_version'), model_scope=prediction.get('model_scope'),
                  center=prediction['center'], probabilities=probabilities)
    return result


def opportunity(row):
    payload = row['payload']
    signal = payload['signal']
    return (str(payload['event']), signal['target'], signal.get('period') if signal['target'] != 'match' else 0)


def verified_state(row):
    result = row.get('result') or {}
    state = result.get('state')
    if state not in ('won', 'lost', 'push'):
        return 'pending'
    signal = row['payload']['signal']
    score, total = result.get('score'), result.get('total')
    evidence = result.get('evidence') or {}
    allowed = {'primary_explicit_finished_protocol', 'final_protocol_corroborated_by_primary', 'primary_completed_period'}
    if (evidence.get('basis') not in allowed or result.get('target') != signal['target'] or
            result.get('period') != signal.get('period') or result.get('line') != signal['line'] or
            not isinstance(score, list) or len(score) != 2 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in score) or
            total != sum(score)):
        return 'invalid_result'
    expected = 'push' if total == signal['line'] else (
        'won' if ((total > signal['line']) == (signal['direction'] == 'ТБ')) else 'lost')
    return state if state == expected else 'invalid_result'


def stats(rows):
    counts = Counter(verified_state(r) for r in rows)
    resolved = [r for r in rows if verified_state(r) in ('won', 'lost')]
    wins = counts['won']; n = len(resolved)
    probabilities = [r['payload']['signal'].get('probability') for r in resolved]
    pairs = [(p, float(verified_state(r) == 'won')) for p, r in zip(probabilities, resolved)
             if not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1]
    interval = None
    if n:
        z = 1.96; p = wins/n; den = 1+z*z/n
        center = (p+z*z/(2*n))/den
        width = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        interval = [round(center-width, 4), round(center+width, 4)]
    return dict(records=len(rows), events=len({r['payload']['event'] for r in rows}), states=dict(counts),
                resolved=n, win_rate=round(wins/n, 4) if n else None, descriptive_wilson95=interval,
                mean_model_probability=round(sum(p for p,y in pairs)/len(pairs), 4) if pairs else None,
                brier=round(sum((p-y)**2 for p,y in pairs)/len(pairs), 4) if pairs else None)


def summarize(rows):
    rows = sorted(rows, key=lambda r:(r['sent_at'],r['id']))
    first = {}
    for row in rows:
        first.setdefault(opportunity(row), row)
    first = list(first.values())
    def groups(key):
        grouped = defaultdict(list)
        for row in first:grouped[str(key(row))].append(row)
        return {k:stats(v) for k,v in sorted(grouped.items())}
    def band(row):
        p = row['payload']['signal'].get('probability')
        return int(p*10)/10 if not isinstance(p,bool) and isinstance(p,(int,float)) and math.isfinite(p) and 0 <= p <= 1 else 'unknown'
    compatible = [r for r in first if r['payload']['signal'].get('marketReady') is True and
                  not {'overtime','rules','prices','quote','suspended'}.intersection(r['payload']['signal'].get('reasonCodes', []))]
    return dict(policy=POLICY, calibrated=False, automatic_priority_enabled=False,
                all_receipts=stats(rows), first_opportunities=stats(first), compatible_first_opportunities=stats(compatible),
                updates=len(rows)-len(first), by_target=groups(lambda r:r['payload']['signal']['target']),
                by_direction=groups(lambda r:r['payload']['signal']['direction']),
                by_probability_band=groups(band),
                by_delivery_policy=groups(lambda r:(r['payload'].get('delivery_review') or {}).get('policy','legacy')),
                limitation='Descriptive selected-signal audit, not an independent validation. Markets from one event are correlated; unresolved outcomes are excluded from accuracy. Incompatible OT markets are excluded from the compatible subset. No profitability or calibrated probability is established.')
