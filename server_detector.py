"""Run the deployed frontend's exact pure forecast functions without a browser."""
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOUNDARY = 'let userScrollIntentVersion = 0;'
BOOTSTRAP = """
globalThis.window=globalThis;
globalThis.document={getElementById:()=>null,addEventListener:()=>{}};
globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
globalThis.setInterval=()=>0;
"""
CALCULATE = """
function serverPriorityCards(data){
 const cards=[],errors=[],evaluations=[];
 for(const original of data.events||[]){
  const ev={...original,feed_checked_at_utc:data.checked_at_utc};
  try {
  const match=automaticForecast(ev),quarter=automaticQuarterForecast(ev);
  const decisions=signalEvaluations(ev,match,quarter);
  for(const decision of decisions){
   const forecast=decision.target==='match'?match.estimate:quarter;
   evaluations.push({event_id:String(ev.id),snapshot_id:ev.snapshot_id||data.checked_at_utc+':'+String(ev.id),
    captured_at_utc:data.checked_at_utc,decision,source_event_snapshot:ev,
    prediction:{center:forecast?.projection,interval80:[forecast?.poisson?.low80??forecast?.low80,forecast?.poisson?.high80??forecast?.high80],
     current:forecast?.current,score:findLiveScore(ev),clock:findLiveClock(ev),period:findLivePeriod(ev),
     periodLengthSeconds:inferPeriodLengthForForecast(ev)?.seconds}});
  }
  for(const signal of decisions.filter(x=>x.priority===true)){
   cards.push({event:String(ev.id),teams:ev.team1+' — '+ev.team2,
    label:priorityLabel(signal),signal,expires:Date.parse(data.checked_at_utc)+90000,
    context:[ev.league||'',(findLiveScore(ev)||[]).join(':'),'Q'+findLivePeriod(ev),findLiveClock(ev)].join(' · ')});
  }
  } catch(error){errors.push({event:String(ev.id),error:String(error.message||error).slice(0,160)});}
 }
 return {cards:cards.sort((a,b)=>b.signal.probability-a.signal.probability||a.event.localeCompare(b.event)).slice(0,3),evaluations,errors};
}
"""


def source_bundle():
    source = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    if source.count(BOUNDARY) != 1:
        raise RuntimeError('Frontend calculation boundary changed; detector requires review')
    return BOOTSTRAP + (ROOT / 'static' / 'forecast-core.js').read_text(encoding='utf-8') + '\n' + source.split(BOUNDARY)[0] + '\n' + CALCULATE


def calculate(data):
    from py_mini_racer import mini_racer
    # Create the engine in a worker thread, with no Python callbacks or network.
    with mini_racer() as engine:
        engine.eval(source_bundle(), timeout_sec=5)
        result = json.loads(engine.eval('JSON.stringify(serverPriorityCards(' + json.dumps(data, ensure_ascii=False) + '))', timeout_sec=20))
        result['model_source_sha256'] = hashlib.sha256(source_bundle().encode()).hexdigest()
        return result
