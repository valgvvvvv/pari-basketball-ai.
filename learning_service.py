"""Independent shadow inference and immutable learning observations."""
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import learning_model as model

ROOT = Path(__file__).resolve().parent
ARTIFACT = None
LAST_UPDATE = None
UPDATE_ERROR = None


def artifact():
    global ARTIFACT
    if ARTIFACT is None:
        try:
            ARTIFACT = model.load(ROOT/'models'/'learning-model.json')
        except (OSError, ValueError, KeyError, TypeError):
            ARTIFACT = {'schema': model.SCHEMA, 'version': 'not_trained', 'models': {}}
    return ARTIFACT


def database():
    location = Path(os.environ.get('LEARNING_STATE_PATH', str(Path(tempfile.gettempdir())/'pari_learning.sqlite3')))
    location.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(location, timeout=10)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, captured REAL NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS primary_evidence (event TEXT NOT NULL, period INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(event, period))')
    db.commit()
    return db


def state_from_event(event, target, checked, strict=True):
    fmt = model.cohort(event)
    if fmt is None:
        raise ValueError('Нет подтверждённого формата баскетбольного матча')
    group, count, length = fmt
    if event.get('period_count') != count or event.get('period_length_seconds') != length or event.get('timer_semantics_verified') is not True:
        raise ValueError('Не подтверждены формат или смысл таймера')
    age = time.time()-(model.stamp(checked) or 0)
    if not -5 <= age <= 90 or event.get('score_conflict') or event.get('identity_conflict'):
        raise ValueError('Снимок устарел или источники расходятся')
    period, score = event.get('period'), event.get('score')
    if not isinstance(period, int) or not 1 <= period <= count or not isinstance(score, list) or len(score) != 2 or any(not model.number(v) or v < 0 for v in score):
        raise ValueError('Нет согласованного счёта или периода')
    try:
        mm, ss = str(event.get('clock')).split(':')
        if len(ss) != 2 or not 0 <= int(ss) < 60:
            raise ValueError()
        left = int(mm)*60+int(ss)
    except (ValueError, TypeError):
        raise ValueError('Нет точных часов') from None
    if not (0 < left < length if strict else 0 <= left <= length):
        raise ValueError('Перерыв или начало периода: ожидаем игровое время')
    pe = length-left
    if strict and target == 'match' and (period != count or pe < (720 if count == 2 else 120)):
        raise ValueError('Модель матча применяется в последнем периоде: четверть после 2 минут, тайм после 12 минут')
    if strict and target == 'period' and not ((480 <= pe <= 960) if count == 2 else (240 <= pe <= 480)):
        raise ValueError('Окно модели периода: 4–8 минут четверти или 8–16 минут тайма')
    periods = event.get('period_scores') or []
    complete = len(periods) >= period-1 and all(isinstance(q,list) and len(q)==2 and all(model.number(v) and v>=0 for v in q) for q in periods[:period-1])
    if not complete and (strict or target == 'period'):
        raise ValueError('Не подтверждён счёт завершённых периодов; прогноз матча доступен отдельно')
    previous = [sum(q[j] for q in periods[:period-1]) for j in (0, 1)] if complete else None
    ps = [a-b for a, b in zip(score, previous)] if previous is not None else None
    if ps is not None and (min(ps) < 0 or (len(periods) >= period and periods[period-1] != ps)):
        raise ValueError('Сумма периодов не согласована с общим счётом')
    elapsed = (period-1)*length+pe
    recent_rate = None
    candidates = []
    for r in event.get('recent_snapshots') or []:
        try:
            rm, rs = str(r['clock']).split(':')
            e = (int(r['period'])-1)*length+length-int(rm)*60-int(rs)
            pair = r['score']
            if 120 <= elapsed-e <= 150 and len(pair) == 2 and all(model.number(v) and 0 <= v <= score[i] for i, v in enumerate(pair)):
                candidates.append((e, sum(pair)))
        except (ValueError, TypeError, KeyError):
            pass
    if candidates:
        e, value = max(candidates)
        recent_rate = (sum(score)-value)/((elapsed-e)/60)
    statistics = {}
    for r in event.get('external_statistics') or []:
        k = r.get('metric_key')
        if (k not in model.METRICS or r.get('calculation_eligible') is not True or r.get('target') not in (None, 'match')
                or (r.get('score_basis') is not None and r['score_basis'] != score)):
            continue
        if model.number(r.get('home')) and model.number(r.get('away')):
            statistics[k] = r['home']+r['away']
    # Team fouls are NOT inferred from personal-foul totals or generic 'fouls'.
    if (elapsed == 0 and sum(score) > 0) or (pe == 0 and ps is not None and sum(ps) > 0):
        raise ValueError('Нулевой игровой таймер не согласован с набранными очками')
    state = dict(current=sum(score) if target == 'match' else sum(ps), overall_current=sum(score), elapsed=elapsed,
                 remaining=count*length-elapsed if target == 'match' else left, period_elapsed=pe, period_current=sum(ps) if ps is not None else None,
                 difference=score[0]-score[1], duration=count*length,
                 previous_rate=sum(previous)/((period-1)*length/60) if period > 1 and previous is not None else None,
                 recent_rate=recent_rate, statistics=statistics, period_length=length)
    return group, state


def predict(event, target, checked):
    a = artifact()
    result = dict(status='waiting', target=target, calibrated=False, priority=False, model_version=a['version'])
    try:
        group, state = state_from_event(event, target, checked, strict=False)
        period, count = event['period'], event['period_count']
        pe, length = state['period_elapsed'], event['period_length_seconds']
        specialist_window = ((period == count and pe >= (720 if count == 2 else 120)) if target == 'match'
                             else (480 <= pe <= 960) if count == 2 else (240 <= pe <= 480))
        profile = 'full' if all(model.number(state['statistics'].get(k)) for k in model.METRICS) and group+':'+target+':full' in a['models'] else 'core'
        m = a['models'].get(group+':'+target+':'+profile) if specialist_window and pe > 0 and state['period_current'] is not None else None
        if m is not None and any(not m['support'][k][0]-60 <= state[k] <= m['support'][k][1]+60 for k in ('elapsed','remaining')):
            m = None
        if m is None:
            format_group = {(4,600):'universal_4x10',(4,720):'universal_4x12',(2,1200):'universal_2x20'}.get((count,length),'universal')
            m = a['models'].get(format_group+':'+target+':universal') or a['models'].get('universal:'+target+':universal')
            group, profile = 'universal', 'universal'
        if m is None:
            raise ValueError('Общая модель ещё не загружена')
        if (model.stamp(checked) or 0) <= (model.stamp(m['trained_through']) or float('inf')):
            raise ValueError('Модель обучена позже снимка: исторический прогноз запрещён')
        for k in ('elapsed', 'remaining') if profile != 'universal' else ():
            low, high = m['support'][k]
            if not low-60 <= state[k] <= high+60:
                raise ValueError('Игровое время вне проверенного диапазона обучения')
        p = model.center(m, state)
        bounds = model.interval(m, state)
        result.update(status='shadow', center=round(p, 1), expected_remaining=round(p-state['current'], 1),
                      interval80=[round(v, 1) for v in bounds], profile=profile, cohort=group,
                      train_games=m['train_games'], calibration_games=m['calibration_games'], report=m['report'],
                      evidence=m['evidence'], trained_through=m['trained_through'], state=state,
                      reason='Экспериментальная модель; точность в реальном лайве и преимущество не подтверждены')
        result['baseline_center'] = (event.get('learning_baselines') or {}).get(target)
        result['residual_sample'] = model.residuals(m, state)
        result['model_scope'] = 'universal_transfer' if profile == 'universal' else 'league_specialist'
        result['source_cohorts'] = m.get('source_cohorts', [group])
        if profile == 'universal':
            result['reason'] = 'Общая обученная модель: учитывает текущую игру; точность для этой лиги и фазы отдельно не подтверждена'
        if state['remaining'] == 0:
            result.update(status='complete', center=state['current'], expected_remaining=0, interval80=[state['current'],state['current']], reason='Игровое время цели истекло; показан текущий счёт, окончательный исход ещё требует подтверждения')
        blocked = {'data', 'rules', 'overtime', 'prices', 'quote', 'suspended'}
        markets = [r for r in event.get('learning_markets') or []
                   if (r.get('target') == 'match') == (target == 'match') and model.number(r.get('line'))
                   and not blocked.intersection(r.get('reasonCodes') or [])]
        if markets and result['status'] == 'shadow':
            row = min(markets, key=lambda r: abs(r['line']-p))
            result['market_prediction'] = dict(line=row['line'], market=row['market'],
                probabilities=model.empirical(round(p, 1), state['current'], m['calibration_errors'], row['line']))
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result['reason'] = str(exc)[:250]
    return result


def observe(event, checked, predictions):
    """Keep a frozen prediction once per clock-minute, including nonselected games."""
    fmt = model.cohort(event)
    if not fmt:
        return
    _, count, length = fmt
    db = database()
    try:
        for target, p in predictions.items():
            if p['status'] != 'shadow':
                continue
            origin = {k: event.get(k) for k in ('id', 'team1', 'team2', 'start_time', 'sport_id', 'league', 'period_count', 'period_length_seconds', 'raw')}
            raw = origin.pop('raw') or {}
            origin['raw'] = {k: raw.get(k) for k in ('team1Id', 'team2Id')}
            origin['team_history'] = {'teams': [{k: t.get(k) for k in ('participant_id', 'name')} for t in (event.get('team_history') or {}).get('teams') or []]}
            # Keep immutable numeric forecasts, not hundreds of repeated residuals
            # and the same historical report on every minute of every game.
            frozen = {k:p[k] for k in ('status','target','calibrated','priority','model_version','center','expected_remaining',
                      'interval80','profile','cohort','state','baseline_center','market_prediction','model_scope') if k in p}
            row = dict(event_id=str(event['id']), captured_at=checked, period=event['period'], target=target,
                       origin=origin, prediction=frozen)
            key = ':'.join([str(event['id']), target, str(event['period']), str(int(p['state']['period_elapsed']//60)), p['model_version']])
            db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)', (key, model.stamp(checked), json.dumps(row, ensure_ascii=False)))
        periods = event.get('period_scores') or []
        for i, pair in enumerate(periods[:count], 1):
            if i >= (event.get('period') or 0):
                continue  # current/final Q4 disappearance is NOT a finished match
            if isinstance(pair, list) and len(pair) == 2 and all(model.number(v) and v >= 0 for v in pair):
                payload = json.dumps(dict(score=pair, observed_at=checked, source='PARI completed period', period=i))
                db.execute('INSERT OR IGNORE INTO primary_evidence VALUES (?,?,?)', (str(event['id']), i, payload))
        db.commit()
    finally:
        db.close()


def attach(data, histories):
    for event in data.get('events') or []:
        try:
            e = {**event, 'recent_snapshots': histories.get(str(event.get('id')), [])}
            predictions = {t: predict(e, t, data.get('checked_at_utc')) for t in ('match', 'period')}
            event['learning'] = predictions
            observe(e, data.get('checked_at_utc'), predictions)
        except (OSError, sqlite3.Error):
            # Isolate storage failure from the live feed, but never silently claim capture.
            event['learning'] = {t: dict(status='waiting', reason='Хранилище обучения недоступно', priority=False, calibrated=False) for t in ('match', 'period')}
        finally:
            event.pop('learning_markets', None)
            event.pop('learning_baselines', None)


def export(after_cursor=0, limit=20000):
    db = database()
    try:
        since = time.time()-45*86400
        records = []
        limit = max(1,min(20000,int(limit)))
        for cursor, identity, payload in db.execute('SELECT rowid,id,payload FROM observations WHERE captured>=? AND rowid>? ORDER BY rowid LIMIT ?', (since,max(0,int(after_cursor)),limit)):
            row = json.loads(payload)
            row['id'] = identity
            row['capture_cursor'] = cursor
            row['primary_periods'] = {str(i): json.loads(p) for i, p in db.execute('SELECT period,payload FROM primary_evidence WHERE event=?', (row['event_id'],))}
            records.append(row)
        return records
    finally:
        db.close()


def status():
    a = artifact()
    return dict(schema=model.SCHEMA, version=a['version'], calibrated=False, automatic_priority_enabled=False,
                method='Регуляризованная модель оставшихся очков', trained_at=a.get('trained_at'),
                cohorts={k: {f: m[f] for f in ('profile', 'train_games', 'calibration_games', 'report', 'evidence', 'trained_through')} for k, m in a['models'].items()},
                last_update=LAST_UPDATE, update_error=UPDATE_ERROR,
                collection=a.get('collection', {}), prospective_report=a.get('prospective_report', []),
                learning='Переобучение в GitHub Actions; проверка исходов по двум источникам. Новые версии остаются Shadow.',
                storage='Локальный кэш временный; продолжение обучения хранится в артефактах GitHub Actions',
                limitation='Историческая реконструкция не подтверждает доступность всех данных в лайве; калиброванная вероятность и прибыль не заявляются')


async def refresh_loop():
    """Load only aggregate model weights from this repository; no arbitrary URL."""
    global ARTIFACT, LAST_UPDATE, UPDATE_ERROR
    import asyncio
    import httpx
    while True:
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                response = await client.get('https://api.github.com/repos/valgvvvvv/pari-basketball-ai./releases/tags/learning-models', headers={'Accept':'application/vnd.github+json'})
                if response.status_code == 404:
                    await asyncio.sleep(3600)
                    continue
                response.raise_for_status()
                assets = [r for r in response.json().get('assets', []) if r.get('name', '').startswith('learning-model-') and r.get('name', '').endswith('.json') and r.get('size', 0) <= 2_000_000]
                if assets:
                    latest = max(assets, key=lambda r: r['created_at'])
                    url = latest['browser_download_url']
                    if not url.startswith('https://github.com/valgvvvvv/pari-basketball-ai./releases/download/learning-models/'):
                        raise ValueError('Unexpected model asset URL')
                    raw = bytearray()
                    async with client.stream('GET', url) as result:
                        result.raise_for_status()
                        async for chunk in result.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2_000_000:
                                raise ValueError('Model asset too large')
                    candidate = json.loads(raw)
                    if candidate.get('schema') != model.SCHEMA:
                        raise ValueError('Incompatible model schema')
                    with tempfile.TemporaryDirectory() as tmp:
                        p = Path(tmp)/'model.json'; p.write_bytes(raw)
                        validated = model.load(p)
                    if validated.get('trained_at', '') >= artifact().get('trained_at', ''):
                        ARTIFACT = validated
                    LAST_UPDATE = datetime.now(timezone.utc).isoformat()
                    UPDATE_ERROR = None
        except (httpx.HTTPError, ValueError, OSError, KeyError, TypeError):
            UPDATE_ERROR = 'Обновление модели недоступно; используется последняя проверенная версия'
        await asyncio.sleep(3600)
