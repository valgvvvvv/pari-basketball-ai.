"""Run the deployed frontend's exact pure forecast functions without a browser."""
import json
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
 const cards=[],errors=[];
 for(const original of data.events||[]){
  const ev={...original,feed_checked_at_utc:data.checked_at_utc};
  try {
  const match=automaticForecast(ev),quarter=automaticQuarterForecast(ev);
  for(const signal of prioritySignals(ev,match,quarter)){
   cards.push({event:String(ev.id),teams:ev.team1+' — '+ev.team2,
    label:priorityLabel(signal),signal,expires:Date.parse(data.checked_at_utc)+90000,
    context:[ev.league||'',(findLiveScore(ev)||[]).join(':'),'Q'+findLivePeriod(ev),findLiveClock(ev)].join(' · ')});
  }
  } catch(error){errors.push({event:String(ev.id),error:String(error.message||error).slice(0,160)});}
 }
 return {cards:cards.sort((a,b)=>b.signal.probability-a.signal.probability||a.event.localeCompare(b.event)).slice(0,3),errors};
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
        return json.loads(engine.eval('JSON.stringify(serverPriorityCards(' + json.dumps(data, ensure_ascii=False) + '))', timeout_sec=20))
