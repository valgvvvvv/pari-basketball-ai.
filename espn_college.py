"""Independent NCAA box-score fallback; native SSIS names bind both teams.

Never match on just a nickname, and never align statistics by list position.
"""
import asyncio
import copy
import re
import time
from datetime import datetime, timezone
import httpx

BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/'
FORMATS={'19166':('mens-college-basketball',2,1200),'15100':('womens-college-basketball',4,600)}
CACHE={}
FAST_CACHE={}
FAST_TASK=None
METRICS={
 'fieldgoalsmade-fieldgoalsattempted':'field_goals',
 'threepointfieldgoalsmade-threepointfieldgoalsattempted':'three_pointers',
 'freethrowsmade-freethrowsattempted':'free_throws','totalrebounds':'rebounds',
 'offensiverebounds':'offensive_rebounds','defensiverebounds':'defensive_rebounds',
 'assists':'assists','steals':'steals','blocks':'blocks','totalturnovers':'turnovers',
 'teamturnovers':'team_turnovers','fouls':'fouls','totaltechnicalfouls':'technical_fouls'}


def normal(name):
    value=re.sub(r'\((?:stud|w|ж|студ)\)','',str(name or ''),flags=re.I)
    return re.sub('[^a-z0-9]','',value.casefold())


def aliases(team):
    return {normal(team.get(k)) for k in ('displayName','shortDisplayName','location','abbreviation')
            if len(normal(team.get(k)))>=3}


def match(event, board_event):
    fmt=FORMATS.get(str(event.get('sport_id')))
    if not fmt:return None
    competitions=board_event.get('competitions') or []
    if len(competitions)!=1:return None
    c=competitions[0];ps=c.get('competitors') or []
    if len(ps)!=2:return None
    history=(event.get('team_history') or {}).get('teams') or []
    if len(history)!=2:return None
    try:
        start=datetime.fromisoformat(str(c.get('date') or board_event.get('date')).replace('Z','+00:00')).timestamp()
        if abs(float(event['start_time'])-start)>3600:return None
    except (KeyError,ValueError,TypeError):return None
    names=[normal((t.get('name') or {}).get('eng')) for t in history]
    names=[{'miamiflorida':'miamihurricanes'}.get(n,n) for n in names]
    # Native English aliases come from a participant-ID-verified PARI review.
    native_ids=[str((event.get('raw') or {}).get(k)) for k in ('team1Id','team2Id')]
    if [str(t.get('participant_id')) for t in history]!=native_ids:return None
    found=[]
    for name in names:
        matches=[p for p in ps if name and name in aliases(p.get('team') or {})]
        if len(matches)!=1:return None
        found.append(matches[0])
    if found[0]['id']==found[1]['id']:return None
    return found


def parse_summary(payload,event,board_event,observed_at):
    sides=match(event,board_event)
    if not sides:return [],{'state':'identity_not_confirmed'}
    iid=str(board_event['id']);comps=(payload.get('header') or {}).get('competitions') or []
    if len(comps)!=1 or str(comps[0].get('id'))!=iid:return [],{'state':'summary_identity_mismatch'}
    header={str(p.get('id')):p for p in comps[0].get('competitors') or []}
    ids=[str(p['id']) for p in sides]
    if set(header)!=set(ids):return [],{'state':'summary_teams_mismatch'}
    try:score=[int(header[x]['score']) for x in ids]
    except (KeyError,ValueError,TypeError):return [],{'state':'score_unavailable'}
    if score!=event.get('score'):return [],{'state':'score_not_current','source_score':score}
    teams={str((p.get('team') or {}).get('id')):p for p in (payload.get('boxscore') or {}).get('teams') or []}
    if set(teams)!=set(ids):return [],{'state':'boxscore_teams_mismatch'}
    maps=[{str(s.get('name')).casefold():s.get('displayValue') for s in teams[x].get('statistics') or []} for x in ids]
    rows=[]
    for name,metric in METRICS.items():
        values=[m.get(name) for m in maps]
        if any(x is None for x in values):continue
        rows.append({'name':metric,'metric_key':metric,'home':values[0],'away':values[1],
          'source':'ESPN','source_event_id':iid,'match_status':'exact_event_id_match',
          'source_url':BASE+FORMATS[str(event['sport_id'])][0]+'/summary?event='+iid,
          'observed_at_utc':observed_at,'score_basis':score})
    return rows,{'state':'score_reconciled','source_event_id':iid,'source_score':score}


async def enrich(events):
    relevant=[e for e in events if str(e.get('sport_id')) in FORMATS]
    diag={'matched':0,'errors':[]}
    if not relevant:return diag
    async with httpx.AsyncClient(timeout=httpx.Timeout(6,connect=3),follow_redirects=False) as client:
        async def fetch(slug,route,params,ttl):
            key=(slug,route,tuple(sorted(params.items())))
            old=CACHE.get(key)
            if old and old[0]>time.time():return copy.deepcopy(old[1]),old[2]
            r=await client.get(BASE+slug+'/'+route,params=params);r.raise_for_status()
            data=r.json();at=datetime.now(timezone.utc).isoformat()
            CACHE[key]=(time.time()+ttl,data,at)
            return data,at
        for slug in sorted({FORMATS[str(e['sport_id'])][0] for e in relevant}):
            group=[e for e in relevant if FORMATS[str(e['sport_id'])][0]==slug]
            for date in sorted({datetime.fromtimestamp(float(e['start_time']),timezone.utc).strftime('%Y%m%d') for e in group}):
                try:
                    board,_=await fetch(slug,'scoreboard',{'dates':date,'groups':50,'limit':500},15)
                    for event in group:
                        if datetime.fromtimestamp(float(event['start_time']),timezone.utc).strftime('%Y%m%d') != date:continue
                        candidates=[b for b in board.get('events') or [] if match(event,b)]
                        if len(candidates)!=1:
                            event['espn_college_diagnostics']={'state':'no_unique_exact_match'};continue
                        b=candidates[0]
                        summary,at=await fetch(slug,'summary',{'event':str(b['id'])},5)
                        rows,state=parse_summary(summary,event,b,at)
                        event['espn_college_diagnostics']=state
                        event.setdefault('external_statistics',[]).extend(rows)
                        diag['matched']+=bool(rows)
                except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
                    diag['errors'].append(type(exc).__name__)
    for key in list(CACHE):
        if CACHE[key][0]<time.time():del CACHE[key]
    return diag


def identity(event):
    raw=event.get('raw') or {}
    return tuple(str(x) for x in (event.get('id'),event.get('start_time'),raw.get('team1Id'),raw.get('team2Id'),event.get('period_count'),event.get('period_length_seconds')))


async def _fast_refresh(events):
    global FAST_TASK
    try:
        for e in events:e['external_statistics']=[]
        await enrich(events)
        now=time.time()
        for e in events:
            FAST_CACHE[str(e['id'])]={'identity':identity(e),'checked':now,
                'statistics':copy.deepcopy(e.get('external_statistics') or []),
                'diagnostics':copy.deepcopy(e.get('espn_college_diagnostics'))}
        active={str(e['id']) for e in events}
        for key in list(FAST_CACHE):
            if key not in active and now-FAST_CACHE[key]['checked']>60:del FAST_CACHE[key]
    except Exception as exc:
        for e in events:
            FAST_CACHE[str(e['id'])]={'identity':identity(e),'checked':time.time(),'statistics':[],
                'diagnostics':{'state':'source_unavailable','error':type(exc).__name__}}
    finally:
        FAST_TASK=None


def apply_and_queue(events):
    """Fast NCAA refresh is independent of the slow optional-provider task.

    The HTTP route only copies cached rows and schedules work; it never waits.
    Every row retains its observed timestamp and native score basis.
    """
    global FAST_TASK
    now=time.time();eligible=[];needs=False
    for e in events:
        if str(e.get('sport_id')) not in FORMATS or not e.get('team_history'):continue
        eligible.append(e);cached=FAST_CACHE.get(str(e['id']),{})
        valid=cached.get('identity')==identity(e)
        if valid and now-cached.get('checked',0)<=15:
            rows=cached.get('statistics') or []
            if rows:
                e['external_statistics']=[r for r in e.get('external_statistics') or [] if r.get('source')!='ESPN']+copy.deepcopy(rows)
            if cached.get('diagnostics'):e['espn_college_diagnostics']=copy.deepcopy(cached['diagnostics'])
        if not valid or now-cached.get('checked',0)>=8:needs=True
    if eligible and needs and (FAST_TASK is None or FAST_TASK.done()):
        FAST_TASK=asyncio.create_task(_fast_refresh(copy.deepcopy(eligible)))
