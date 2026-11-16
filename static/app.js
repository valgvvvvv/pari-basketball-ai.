const $ = id => document.getElementById(id);
const form = $("analysisForm");
const defaults = {team1:"Юнион Креминь",team2:"Вела Баскет",score1:75,score2:82,period:4,clock:"04:01",periodMinutes:10,line:187.5,overOdds:1.8,underOdds:1.9,fouls:12,sims:20000};
function getData(){return {team1:$("team1").value,team2:$("team2").value,score1:+$("score1").value,score2:+$("score2").value,period:+$("period").value,clock:$("clock").value,period_minutes:+$("periodMinutes").value,total_line:+$("line").value,over_odds:+$("overOdds").value||null,under_odds:+$("underOdds").value||null,fouls_total:+$("fouls").value||0,simulations:+$("sims").value};}
function poissonTail(mu,k){if(k<=0)return 1;if(mu>700)return .5;let p=Math.exp(-mu),cdf=p;for(let i=1;i<k;i++){p*=mu/i;cdf+=p;}return Math.max(0,Math.min(1,1-cdf));}
function quantile(a,q){const s=[...a].sort((x,y)=>x-y);return s[Math.min(s.length-1,Math.floor(q*(s.length-1)))];}
function calculate(d){
 const [m,s]=d.clock.split(":").map(Number);if(!Number.isFinite(m)||!Number.isFinite(s)||s>59)throw Error("Проверьте время MM:SS");
 const psec=d.period_minutes*60,remaining=psec-(m*60+s);if(remaining<0)throw Error("Время больше длительности четверти");
 const left=(4-d.period)*psec+remaining,elapsed=4*psec-left;if(elapsed<=0)throw Error("Недостаточно прошедшего времени");
 const current=d.score1+d.score2,pace=current/(elapsed/60),close=Math.abs(d.score1-d.score2)<=6,late=d.period===4&&remaining<=300;
 let endFactor=late?(close?1.035:.975):1;let foulFactor=1+Math.min(d.fouls_total,20)*.0015;
 const mu=pace*(left/60)*endFactor*foulFactor,expected=current+mu,sigma=Math.max(Math.sqrt(Math.max(mu,1))*1.18,1);
 const k=Math.floor(d.total_line-current)+1,pPois=poissonTail(mu,k);
 let over=0,vals=[];for(let i=0;i<d.simulations;i++){let u=Math.random()||1,v=Math.random();let z=Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);let rem=Math.max(0,mu+sigma*z),t=current+rem;vals.push(t);if(t>d.total_line)over++;}
 let pOver=over/d.simulations,pUnder=1-pOver,market=null;
 if(d.over_odds>1&&d.under_odds>1){let a=1/d.over_odds,b=1/d.under_odds;market=100*a/(a+b);}
 const direction=pOver>=.5?"ТБ":"ТМ",prob=Math.max(pOver,pUnder),edge=Math.abs(expected-d.total_line),grade=prob>=.72&&edge>=4?"A":prob>=.62&&edge>=2?"B":"C";
 return {current,elapsed,left,pace,mu,expected,sigma,pPois,pOver,pUnder,market,direction,prob,grade,range80:[quantile(vals,.1),quantile(vals,.9)],range95:[quantile(vals,.025),quantile(vals,.975)]};
}
function show(d,r){
 $("result").hidden=false;$("matchTitle").textContent=`${d.team1} — ${d.team2}`;$("direction").textContent=r.direction;$("lineLabel").textContent=`Линия ${d.total_line}`;$("probability").textContent=(r.prob*100).toFixed(1)+"%";$("expected").textContent=r.expected.toFixed(1);$("remaining").textContent=`Осталось ${ (r.left/60).toFixed(2)} мин.`;$("grade").textContent=`Сигнал ${r.grade}`;
 $("overPct").textContent=(r.pOver*100).toFixed(1)+"%";$("underPct").textContent=(r.pUnder*100).toFixed(1)+"%";$("overBar").style.width=(r.pOver*100)+"%";$("underBar").style.width=(r.pUnder*100)+"%";
 $("pace").textContent=r.pace.toFixed(2);$("mu").textContent=r.mu.toFixed(1);$("range80").textContent=`${r.range80[0].toFixed(0)}–${r.range80[1].toFixed(0)}`;$("range95").textContent=`${r.range95[0].toFixed(0)}–${r.range95[1].toFixed(0)}`;
 $("explanation").textContent=`За ${(r.elapsed/60).toFixed(2)} мин. набрано ${r.current} очков. Наблюдаемый темп — ${r.pace.toFixed(2)} очка/мин. На оставшиеся ${(r.left/60).toFixed(2)} мин. модель оценивает ещё ${r.mu.toFixed(1)} очка; математическое ожидание итогового тотала — ${r.expected.toFixed(1)}. Пуассоновская базовая оценка ТБ: ${(r.pPois*100).toFixed(1)}%; Монте-Карло: ТБ ${(r.pOver*100).toFixed(1)}%, ТМ ${(r.pUnder*100).toFixed(1)}%.`;
 $("warnings").innerHTML="<li>Вероятности — приближённые оценки. Модель пока не обучена на исторических данных конкретной лиги.</li><li>Текущая оценка темпа предполагает сохранение средней скорости; качество улучшится при наличии владений, бросков, фолов по командам и поквартального темпа.</li>";
 $("market").textContent="Рыночная вероятность ТБ без маржи: "+(r.market===null?"нет коэффициентов":r.market.toFixed(1)+"%");$("simCount").textContent=d.simulations.toLocaleString("ru-RU")+" симуляций";
 const item={date:new Date().toLocaleString("ru-RU"),match:$("matchTitle").textContent,direction:r.direction,line:d.total_line,prob:(r.prob*100).toFixed(1),grade:r.grade};let hist=JSON.parse(localStorage.getItem("pariHistory")||"[]");hist.unshift(item);localStorage.setItem("pariHistory",JSON.stringify(hist.slice(0,50)));renderHistory();
}
if (form) form.addEventListener("submit",async e=>{e.preventDefault();try{const d=getData();const res=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});const payload=await res.json();if(!res.ok)throw Error(payload.detail||"Сервер не смог выполнить расчёт");let r=payload;
 if(r.p_over_mc!==undefined){r={...r,pOver:r.p_over_mc/100,pUnder:r.p_under_mc/100,expected:r.expected_total,pace:r.pace_per_min,mu:r.mu_remaining,left:r.remaining_minutes*60,elapsed:r.elapsed_minutes*60,current:r.current_total,market:r.market_over_devig,pPois:r.p_over_poisson/100,range80:r.range80,range95:r.range95,prob:Math.max(r.p_over_mc,r.p_under_mc)/100};}
 show(d,r);$("result").scrollIntoView({behavior:"smooth",block:"start"});
 }catch(err){alert(err.message||"Не удалось рассчитать прогноз");}});
const resetButton = $("resetBtn");
if (resetButton) resetButton.addEventListener("click",()=>{Object.entries(defaults).forEach(([k,v])=>{let el=$(k);if(el)el.value=v;});$("result").hidden=true;});
function renderHistory(){let h=JSON.parse(localStorage.getItem("pariHistory")||"[]");$("historyCount").textContent=h.length;$("historyList").innerHTML=h.length?h.map(x=>`<div class="history-row"><b>${x.match}</b><span>${x.direction} ${x.line}</span><span>${x.prob}% · ${x.grade}</span><span>${x.date}</span></div>`).join(""):'<p class="muted">Пока нет сохранённых расчётов.</p>';}
$("clearHistory").addEventListener("click",()=>{localStorage.removeItem("pariHistory");renderHistory();});
// Health is a backend status only; the live feed status below is authoritative.
fetch("/api/health").catch(()=>{});
renderHistory();


// Public PARI asset/API discovery. This is diagnostic only; it does not claim that a feed is connected.
const discoverSourceButton = $("discoverSource");
const copySourceDiagnosticButton = $("copySourceDiagnostic");
let lastSourceDiagnostic = "";
if (copySourceDiagnosticButton) {
  copySourceDiagnosticButton.addEventListener("click", async () => {
    if (!lastSourceDiagnostic) return;
    try {
      await navigator.clipboard.writeText(lastSourceDiagnostic);
      copySourceDiagnosticButton.textContent = "Отчёт скопирован";
    } catch (_) {
      copySourceDiagnosticButton.textContent = "Не удалось скопировать";
    }
  });
}
if (discoverSourceButton) {
  discoverSourceButton.addEventListener("click", async () => {
    discoverSourceButton.disabled = true;
    $("sourceDiscoverStatus").textContent = "Проверяем страницу PARI и загружаемые JS-модули…";
    $("sourceDiscoverOutput").textContent = "";
    try {
      const response = await fetch("/api/pari/discover", {cache: "no-store"});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Источник не ответил");
      const count = (data.route_candidates || []).length;
      $("sourceDiscoverStatus").textContent = `Проверено JS-чанков: ${(data.chunk_scans || []).length}; рабочих CDN-зеркал: ${(data.bootstrap_scans || []).filter(x => x.status === 200).length}; кандидатов маршрутов: ${count}. API пока не подтверждён.`;
      const compact = {
        diagnostic_version: data.diagnostic_version,
        pages: (data.pages || []).map(x => ({
          requested_url:x.requested_url,status:x.status,error:x.error,bytes:x.bytes,
          runtime_host_hints:x.runtime_host_hints,
          runtime_config_context_windows:(x.runtime_config_context_windows||[]).slice(0,12)
        })),
        bootstrap_scans: (data.bootstrap_scans || []).map(x => ({
          url:x.url,status:x.status,error:x.error,bytes:x.bytes,host_hints:x.host_hints,
          semantic_chunk_candidates:x.semantic_chunk_candidates,
          runtime_windows:(x.runtime_windows||[]).slice(0,12),
          webpack_chunk_name_map:x.webpack_chunk_name_map,
          api_path_hints:(x.api_path_hints||[]).slice(0,35)
        })),
        chunk_scans: (data.chunk_scans || []).map(x => ({
          chunk_id:x.chunk_id,status:x.status,error:x.error,url:x.url,bytes:x.bytes,
          data_model_keyword_counts:x.data_model_keyword_counts,
          api_path_hints:(x.api_path_hints||[]).slice(0,35),
          data_model_context_windows:(x.data_model_context_windows||[]).slice(0,8),
          api_context_windows:(x.api_context_windows||[]).slice(0,18),
          targeted_context_windows:(x.targeted_context_windows||[]).slice(0,30),
          service_keyword_counts:x.service_keyword_counts,
          request_string_hints:(x.request_string_hints||[]).slice(0,45)
        })),
        route_candidates:(data.route_candidates||[]).slice(0,80),
        confirmed_api:data.confirmed_api,
        note:data.note
      };
      lastSourceDiagnostic = JSON.stringify(compact, null, 2);
      $("sourceDiscoverOutput").textContent = lastSourceDiagnostic;
      if (copySourceDiagnosticButton) {
        copySourceDiagnosticButton.disabled = false;
        copySourceDiagnosticButton.textContent = "Скопировать отчёт";
      }
    } catch (error) {
      $("sourceDiscoverStatus").textContent = "Не удалось выполнить поиск: " + (error.message || "ошибка");
    } finally {
      discoverSourceButton.disabled = false;
    }
  });
}


function collectEventObjects(root){
  const out=[],seen=new Set();
  function walk(value,depth){
    if(!value||typeof value!=="object"||depth>6||seen.has(value))return;
    seen.add(value);
    if(!Array.isArray(value))out.push(value);
    if(Array.isArray(value)){for(const x of value)walk(x,depth+1);}
    else for(const x of Object.values(value))walk(x,depth+1);
  }
  walk(root,0);return out;
}
function parseScore(value){
  if(value==null)return null;
  if(Array.isArray(value)&&value.length>=2){
    const a=Number(value[0]),b=Number(value[1]);
    if(Number.isFinite(a)&&Number.isFinite(b))return [a,b];
  }
  if(typeof value==="object"){
    const pairs=[
      ["team1","team2"],["home","away"],["score1","score2"],
      ["homeScore","awayScore"],["scoreHome","scoreAway"],
      ["team1Score","team2Score"],["team1_score","team2_score"],
      ["home_score","away_score"],["homePoints","awayPoints"],
      ["participant1","participant2"]
    ];
    for(const [aKey,bKey] of pairs){
      const a=Number(value[aKey]),b=Number(value[bKey]);
      if(value[aKey]!=null&&value[bKey]!=null&&Number.isFinite(a)&&Number.isFinite(b))return [a,b];
    }
    for(const [key,nested] of Object.entries(value)){
      if(/score|result|period/i.test(key)){const found=parseScore(nested);if(found)return found;}
    }
    return null;
  }
  const m=String(value).match(/(?:^|[^0-9])(\d{1,3})\s*[:\-]\s*(\d{1,3})(?:[^0-9]|$)/);
  return m?[Number(m[1]),Number(m[2])]:null;
}
function findLiveScore(ev){
  const objects=collectEventObjects({root:{...(ev.raw||{}), external_live:ev.external_live||{}, live_score:ev.live_score, live_clock:ev.live_clock, live_period:ev.live_period},related:ev.related_events||[],live:ev.live_fields||{}});
  const direct=parseScore(ev.score);if(direct)return direct;
  for(const obj of objects){
    for(const [key,value] of Object.entries(obj)){
      if(/^(score|scoreInfo|currentScore|result|liveScore|matchScore|totalScore)$/i.test(key)){
        const parsed=parseScore(value);if(parsed)return parsed;
      }
    }
    const home=Object.entries(obj).find(([k,v])=>/^(homeScore|scoreHome|team1Score|team1Points|home_points)$/i.test(k)&&v!=null);
    const away=Object.entries(obj).find(([k,v])=>/^(awayScore|scoreAway|team2Score|team2Points|away_points)$/i.test(k)&&v!=null);
    if(home&&away){const a=Number(home[1]),b=Number(away[1]);if(Number.isFinite(a)&&Number.isFinite(b))return [a,b];}
  }
  return null;
}
function findLivePeriod(ev){
  const objects=collectEventObjects({root:ev.raw||{},related:ev.related_events||[],live:ev.live_fields||{}});
  const direct=ev.period??ev.external_live?.period??ev.live_period??(ev.raw||{}).period??(ev.raw||{}).periodName;
  const candidates=[direct];
  for(const obj of objects)for(const [key,value] of Object.entries(obj))if(/period|quarter|gamePart|stageNumber/i.test(key))candidates.push(value);
  for(const value of candidates){
    if(value==null)continue;
    if(typeof value==="number"&&Number.isInteger(value)&&value>=1&&value<=4)return value;
    const str=String(value).toLowerCase();
    const m=str.match(/(?:q|quarter|period|период|четверт\w*|\b)([1-4])(?:\b|$)/i)||str.match(/^([1-4])$/);
    if(m)return Number(m[1]);
  }
  return null;
}
function findLiveClock(ev){
  const objects=collectEventObjects({root:ev.raw||{},related:ev.related_events||[],live:ev.live_fields||{}});
  const candidates=[ev.clock,ev.external_live?.clock,ev.live_clock,(ev.raw||{}).time,(ev.raw||{}).timer];
  for(const obj of objects)for(const [key,value] of Object.entries(obj))if(/^(time|timer|clock|matchTime|gameTime|remainingTime|periodTime|timeRemaining|time_remaining|countdown|remaining_seconds|clock_seconds)$/i.test(key))candidates.push(value);
  for(const value of candidates){
    if(value==null)continue;
    const text=String(value).trim();
    if(/^\d{4}-\d{2}-\d{2}[T ]/.test(text)) continue;
    const m=text.match(/^(\d{1,2}):([0-5]\d)$/);
    if(m){
      const seconds=Number(m[1])*60+Number(m[2]);
      if(seconds<=1200)return m[1].padStart(2,"0")+":"+m[2];
      continue;
    }
    if(typeof value==="number" || /^\d{1,3}$/.test(text)){
      const seconds=Number(value);
      if(Number.isInteger(seconds)&&seconds>=0&&seconds<=1200){
        return String(Math.floor(seconds/60)).padStart(2,"0")+":"+String(seconds%60).padStart(2,"0");
      }
    }
  }
  return null;
}
function estimateLiveTotal(ev, score, period, clock){
  const live=ev.external_live||{};
  const length=Number(live.period_length_seconds||0);
  const periodCount=Number(live.period_count||4);
  if(!score||!period||!clock||!Number.isFinite(length)||length<300||length>1200||![2,4].includes(periodCount))return null;
  const match=String(clock).match(/^(\d{1,2}):([0-5]\d)$/);
  if(!match)return null;
  const remaining=Number(match[1])*60+Number(match[2]);
  if(remaining>length)return null;
  const elapsed=(Number(period)-1)*length+(length-remaining);
  if(elapsed<=0)return null;
  const total=score[0]+score[1];
  const projection=total/elapsed*(length*periodCount);
  if(!Number.isFinite(projection)||projection<=0)return null;
  const remainingExpected=Math.max(0,projection-total);
  const sigma=Math.max(6,Math.sqrt(Math.max(remainingExpected,1))*1.30+remainingExpected*0.08);
  const margin=1.2816*sigma;
  const elapsedMinutes=elapsed/60,remainingSeconds=length*periodCount-elapsed;
  return {current:total,projection:Math.round(projection*10)/10,
    low:Math.max(total,Math.round(projection-margin)),high:Math.round(projection+margin),
    sigma:Math.round(sigma*10)/10,elapsed:Math.round(elapsedMinutes*10)/10,
    elapsed_minutes:Math.round(elapsedMinutes*10)/10,
    remaining_minutes:Math.round(remainingSeconds/60*10)/10,
    pace_per_min:Math.round((total/elapsedMinutes)*100)/100,
    expected_remaining_points:Math.round(remainingExpected*10)/10,
    elapsed_seconds:elapsed,remaining_seconds:remainingSeconds,
    period_elapsed_seconds:length-remaining};
}
function poissonMonteCarlo(current, mu, lines=[], seedText="") {
  const n=10000;
  let seed=2166136261;
  for(const ch of String(seedText)) seed=Math.imul(seed^ch.charCodeAt(0),16777619)>>>0;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed+1)/4294967297;};
  const outcomes=[];
  for(let i=0;i<n;i++){
    const limit=Math.exp(-Math.max(0,mu));let product=1,k=0;
    do{k++;product*=random();}while(product>limit&&k<1000);
    outcomes.push(current+k-1);
  }
  outcomes.sort((a,b)=>a-b);
  const quantile=q=>outcomes[Math.min(n-1,Math.max(0,Math.ceil(q*n)-1))];
  const probs=(lines||[]).map(x=>{
    const line=Number(x.line),wins=outcomes.reduce((sum,total)=>sum+(total>line?1:0),0);
    const over=wins/n;
    return {...x,pOver:over,pUnder:1-over,
      direction:over>=0.57?"ТБ":(over<=0.43?"ТМ":"без выраженного преимущества"),
      edge:Math.max(over,1-over)-0.5};
  });
  return {simulations:n,mu:Math.round(mu*100)/100,low80:quantile(.10),high80:quantile(.90),
    low95:quantile(.025),high95:quantile(.975),probabilities:probs};
}
function clampForecast(value,min,max){return Math.max(min,Math.min(max,value));}
function readPeriodScore(ev,index){
  const scores=ev.period_scores??ev.external_live?.period_scores??[];
  const row=Array.isArray(scores)?scores[index]:scores[String(index+1)];
  if(!Array.isArray(row)||row.length<2)return null;
  const a=Number(row[0]),b=Number(row[1]);
  return Number.isFinite(a)&&Number.isFinite(b)?a+b:null;
}
function applyGameContext(estimate,ev,period,clock){
  const factors=[];
  if(!estimate||!Number.isFinite(estimate.projection)||!Number.isFinite(estimate.current))
    return {estimate,factors,adjustment:1};
  const length=Number(ev.period_length_seconds||ev.external_live?.period_length_seconds||inferPeriodLengthForForecast(ev)?.seconds||0);
  const count=Number(ev.external_live?.period_count||4);
  const totalSeconds=length*count;
  const elapsed=Number(estimate.elapsed_seconds||Number(estimate.elapsed||0)*60);
  const remaining=Math.max(0,totalSeconds-elapsed);
  const baseMu=Math.max(0,estimate.projection-estimate.current);
  let multiplier=1;
  const globalRate=elapsed>0?estimate.current/elapsed:0;
  const previousQuarter=Number(period)>1?readPeriodScore(ev,Number(period)-2):null;
  if(previousQuarter!=null&&length>0&&globalRate>0){
    const ratio=clampForecast((previousQuarter/length)/globalRate,0.85,1.15);
    const delta=1+(ratio-1)*0.20;
    multiplier*=delta;
    factors.push("темп последней завершённой четверти "+Math.round((ratio-1)*100)+"% (вес 20%)");
  }
  const currentQuarter=Number(period)>0?readPeriodScore(ev,Number(period)-1):null;
  const clockMatch=String(clock||"").match(/^(\d{1,2}):([0-5]\d)$/);
  const periodElapsed=clockMatch&&length>0?length-(Number(clockMatch[1])*60+Number(clockMatch[2])):0;
  if(currentQuarter!=null&&periodElapsed>=180&&globalRate>0){
    const ratio=clampForecast((currentQuarter/periodElapsed)/globalRate,0.85,1.15);
    const delta=1+(ratio-1)*0.15;
    multiplier*=delta;
    factors.push("темп текущей четверти "+Math.round((ratio-1)*100)+"% (вес 15%)");
  }
  const score=findLiveScore(ev);
  if(Number(period)===4&&score&&remaining>0){
    const margin=Math.abs(Number(score[0])-Number(score[1]));
    let delta=1;
    if(remaining<=180&&margin<=8)delta=1.08;
    else if(remaining<=360&&margin<=8)delta=1.04;
    else if(remaining<=180&&margin>=16)delta=0.94;
    else if(remaining<=360&&margin>=16)delta=0.96;
    if(delta!==1){
      multiplier*=delta;
      factors.push((margin<=8?"вероятность тактических фолов в близкой концовке":"риск замедления при крупном преимуществе")+" ("+Math.round((delta-1)*100)+"%)");
    }
  }
  multiplier=clampForecast(multiplier,0.82,1.20);
  return {estimate:{...estimate,projection:Math.round((estimate.current+baseMu*multiplier)*10)/10},
    factors,adjustment:multiplier,remainingSeconds:remaining};
}
function parseStatCount(value){
  if(value==null)return null;
  if(typeof value==="object"){
    for(const key of ["current","value","total","count","displayValue","display_value","made","attempted","score"]){
      if(value[key]!=null){const parsed=parseStatCount(value[key]);if(parsed)return parsed;}
    }
    return null;
  }
  if(typeof value==="number"&&Number.isFinite(value))return {value,attempts:null};
  const text=String(value).trim().replace(/,/g,".");
  const pair=text.match(/^(\d+(?:\.\d+)?)\s*[-/：:]\s*(\d+(?:\.\d+)?)$/);
  if(pair)return {value:Number(pair[1]),attempts:Number(pair[2])};
  const single=text.match(/^\d+(?:\.\d+)?$/);
  return single?{value:Number(text),attempts:null}:null;
}
function liveShootingContext(ev,estimate){
  const stats=Array.isArray(ev?.external_statistics)?ev.external_statistics:[];
  if(!stats.length||!estimate?.elapsed_seconds)return {adjustment:1,factors:[],quality:"нет пригодных показателей бросков"};
  const sides={home:{},away:{}};
  const set=(side,key,value)=>{const parsed=parseStatCount(value);if(parsed)sides[side][key]=parsed;};
  for(const row of stats){
    if(!row||!row.metric_key)continue;
    const key=String(row.metric_key).toLowerCase();
    for(const side of ["home","away"]){
      const value=row[side];
      if(["field_goals","three_pointers","two_pointers","free_throws"].includes(key)){
        const parsed=parseStatCount(value);
        if(parsed){sides[side][key+"_made"]=parsed.value;if(parsed.attempts!=null)sides[side][key+"_attempted"]=parsed.attempts;}
      } else if(/^(field_goals|three_pointers|two_pointers|free_throws)_(made|attempted)$/.test(key)){
        set(side,key,value);
      }
    }
  }
  let weightedEfg=0,totalAttempts=0,pointsFromBox=0,completeTeams=0;
  for(const side of ["home","away"]){
    const x=sides[side];
    const fgm=x.field_goals_made??((x.two_pointers_made??0)+(x.three_pointers_made??0));
    const fga=x.field_goals_attempted??((x.two_pointers_attempted??0)+(x.three_pointers_attempted??0));
    const tpm=x.three_pointers_made??0;
    const ftm=x.free_throws_made??0;
    if(Number.isFinite(fgm)&&Number.isFinite(fga)&&fga>0&&
       (x.field_goals_attempted!=null||x.two_pointers_attempted!=null||x.three_pointers_attempted!=null)){
      weightedEfg+=(fgm+0.5*tpm)/fga*fga;
      totalAttempts+=fga;
      completeTeams++;
    }
    if(Number.isFinite(fgm))pointsFromBox+=2*fgm+tpm+ftm;
  }
  if(completeTeams<2||totalAttempts<=0)
    return {adjustment:1,factors:[],quality:"недостаточно парных бросковых показателей обеих команд"};
  // Reject a box score that contradicts the authoritative live score.
  const score=findLiveScore(ev),boxError=score?Math.abs(pointsFromBox-(Number(score[0])+Number(score[1]))):Infinity;
  if(!score||boxError>Math.max(6,(Number(score[0])+Number(score[1]))*0.08))
    return {adjustment:1,factors:["бросковая статистика не прошла сверку со счётом — не использована"],quality:"расхождение со счётом"};
  const efg=weightedEfg/totalAttempts;
  // Small regression-to-neutral correction: current hot/cold shooting is not
  // projected unchanged for the whole remaining game.
  const adjustment=clampForecast(1+(efg-0.50)*0.20,0.96,1.04);
  return {
    adjustment,
    factors:["эффективность бросков eFG "+(efg*100).toFixed(1)+"% · поправка оставшегося ожидания "+((adjustment-1)*100).toFixed(1)+"%"],
    quality:"сверено со счётом (ошибка "+boxError+" оч.)",
    efg:Math.round(efg*1000)/1000,
    boxScorePoints:pointsFromBox
  };
}
function liveStatisticsAnalysis(ev){
  const rows=Array.isArray(ev?.external_statistics)?ev.external_statistics:[];
  const result=[];
  const aliases={
    possessions:["possessions","possession"],fouls:["fouls","personal_fouls","team_fouls"],
    turnovers:["turnovers"],rebounds:["rebounds"],offensive_rebounds:["offensive_rebounds"],
    defensive_rebounds:["defensive_rebounds"],assists:["assists"],steals:["steals"],blocks:["blocks"],
    three_pointers:["three_pointers","three_pointers_made"],two_pointers:["two_pointers","two_pointers_made"],
    free_throws:["free_throws","free_throws_made"],field_goals:["field_goals","field_goals_made"]
  };
  const read=(keys)=>{
    const row=rows.find(x=>keys.includes(String(x?.metric_key||"").toLowerCase()));
    if(!row)return null;
    const h=parseStatCount(row.home),v=parseStatCount(row.away);
    if(!h||!v)return null;
    return {home:h,away:v,name:row.name||keys[0]};
  };
  for(const [key,labels] of Object.entries(aliases)){
    const x=read(labels);if(!x)continue;
    const h=x.home.value,a=x.away.value;
    if(!Number.isFinite(h)||!Number.isFinite(a))continue;
    const diff=h-a;
    let note="";
    if(key==="possessions")note=diff>2?"больше владений у первой команды":diff< -2?"больше владений у второй команды":"по владениям равенство";
    else if(key==="fouls")note=diff>2?"первая команда чаще фолит":diff< -2?"вторая команда чаще фолит":"по фолам близко";
    else if(key==="turnovers")note=diff>2?"у первой команды больше потерь":diff< -2?"у второй команды больше потерь":"по потерям близко";
    else if(key==="rebounds"||key==="offensive_rebounds")note=diff>2?"преимущество первой команды на щите":diff< -2?"преимущество второй команды на щите":"подборы близки";
    else if(key==="assists")note=diff>2?"у первой команды больше результативных передач":diff< -2?"у второй команды больше результативных передач":"по передачам близко";
    else if(key==="steals"||key==="blocks")note=diff>1?"выше у первой команды":diff< -1?"выше у второй команды":"показатели близки";
    else continue;
    result.push(x.name+": "+h+" : "+a+" — "+note);
  }
  const score=findLiveScore(ev);
  const shot=liveShootingContext(ev,{elapsed_seconds:1});
  if(shot.efg!=null)result.unshift("Эффективность бросков eFG: "+(shot.efg*100).toFixed(1)+"%; данные сверены с текущим счётом.");
  else if(shot.quality)result.push("Броски: "+shot.quality+".");
  return result;
}
function possessionModelContext(ev,estimate,period,clock){
  const rows=Array.isArray(ev?.external_statistics)?ev.external_statistics:[];
  const aliases={fga:["field_goals_attempted","field_goal_attempts","field_goals_attempts","field_goal_attempted","fga","fg_attempted","fg_attempts","field_goals"],oreb:["offensive_rebounds","offensive_rebound","offensive_rebound_count","oreb"],tov:["turnovers","turnover","lost_balls","turnover_count"],fta:["free_throws_attempted","free_throw_attempts","free_throw_attempts_total","free_throw_attempted","fta","free_throws"],poss:["possessions","possession","team_possessions","total_possessions","ball_possessions","possession_count","team_possession","possessions_total","total_possession"]};
  const normalizeKey=value=>String(value||"").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"").replace(/_+/g,"_");
  const read=(side,keys)=>{
    const row=rows.find(x=>keys.includes(normalizeKey(x?.metric_key||x?.name)));
    if(!row)return null;
    const value=row[side];
    // parseStatCount understands both scalar and provider-specific value objects.
    const parsed=parseStatCount(value);
    if(!parsed)return null;
    // PARI/SofaScore may encode made-attempted as "M/A". For FGA/FTA
    // use the attempted component, never the made component.
    const compositeAttemptMetric=keys.includes("field_goals")||keys.includes("free_throws");
    const result=compositeAttemptMetric?parsed.attempts:parsed.value;
    return result!=null&&Number.isFinite(result)?result:null;
  };
  const sides={home:{},away:{}};
  for(const side of ["home","away"])for(const [key,names] of Object.entries(aliases))sides[side][key]=read(side,names);
  let home=sides.home.poss,away=sides.away.poss,method="фактические владения источника";
  if(!(home>0&&away>0)){
    if(!["home","away"].every(side=>["fga","oreb","tov","fta"].every(k=>sides[side][k]!=null)))
      return {applied:false,reason:"нет владений или полного набора бросков, подборов в нападении, потерь и штрафных"};
    home=sides.home.fga-sides.home.oreb+sides.home.tov+0.44*sides.home.fta;
    away=sides.away.fga-sides.away.oreb+sides.away.tov+0.44*sides.away.fta;
    method="расчёт по FGA − OREB + потери + 0,44×FTA";
  }
  if(!(home>0&&away>0))return {applied:false,reason:"некорректные значения владений"};
  const imbalance=Math.abs(home-away)/Math.max(home,away);
  if(imbalance>0.25)return {applied:false,reason:"владения команд расходятся более чем на 25% — проверка качества не пройдена"};
  const score=findLiveScore(ev);if(!score)return {applied:false,reason:"нет подтверждённого счёта"};
  const current=Number(score[0])+Number(score[1]),possessions=(home+away)/2;
  if(possessions<10||possessions>180)return {applied:false,reason:"значение владений вне допустимого диапазона"};
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||inferPeriodLengthForForecast(ev)?.seconds||0);
  const count=Number(ev?.external_live?.period_count||inferPeriodLengthForForecast(ev)?.periods||4);
  const totalSeconds=length*count,elapsed=Number(estimate?.elapsed_seconds||0);
  if(!length||!elapsed||elapsed>=totalSeconds)return {applied:false,reason:"не хватает подтверждённого времени матча"};
  const pointsPerPossession=current/possessions,remaining=totalSeconds-elapsed;
  const possPerMinute=possessions/(elapsed/60);
  // Team-level possession efficiency is retained separately for diagnostics.
  // The combined projection uses the average team possession count, avoiding
  // double-counting the two teams' possessions in a single game total.
  const projected=current+possPerMinute*(remaining/60)*pointsPerPossession;
  const baseline=Math.max(1,Number(estimate.projection)-current);
  return {applied:true,method,home:Math.round(home*10)/10,away:Math.round(away*10)/10,
    average:Math.round(possessions*10)/10,imbalance_percent:Math.round(imbalance*100),
    pointsPerPossession:Math.round(pointsPerPossession*1000)/1000,
    possPerMinute:Math.round(possPerMinute*100)/100,
    projection:Math.round(projected*10)/10,
    remaining_minutes:Math.round(remaining/60*100)/100,
    adjustment:clampForecast((projected-current)/baseline,0.94,1.06)};
}
function applyPoissonModel(estimate,lines,seedText,ev=null,period=null,clock=null){
  if(!estimate||!Number.isFinite(estimate.projection)||!Number.isFinite(estimate.current))return estimate;
  const context=estimate.source==="period_range"?{estimate,factors:[],adjustment:1}:applyGameContext(estimate,ev||{},period,clock);
  const adjusted=context.estimate||estimate,statsContext=liveShootingContext(ev||{},adjusted);
  const possession=estimate.source==="period_range"?{applied:false,reason:"нет точного таймера"}:possessionModelContext(ev||{},adjusted,period,clock);
  const baseMu=Math.max(0,adjusted.projection-adjusted.current);
  const possMu=possession.applied?Math.max(0,possession.projection-adjusted.current):baseMu;
  const mu=possMu*statsContext.adjustment,mc=poissonMonteCarlo(adjusted.current,mu,lines,seedText);
  const factors=[...(context.factors||[]),...(statsContext.factors||[])];
  if(possession.applied)factors.push("владения: "+possession.average+" на команду; "+possession.possPerMinute+" влад./мин; "+possession.pointsPerPossession+" очка/владение");
  return {...adjusted,context_factors:factors,context_adjustment:context.adjustment,
    statistics_adjustment:statsContext.adjustment,statistics_quality:statsContext.quality,
    shooting_efg:statsContext.efg??null,possession_model:possession,
    poisson:mc,low:mc.low80,high:mc.high80,sigma:Math.sqrt(Math.max(mu,0)),
    projection:Math.round((adjusted.current+mu)*10)/10};
}
function normalCdf(z){
  const sign=z<0?-1:1,x=Math.abs(z),t=1/(1+0.2316419*x);
  const density=0.3989422804014327*Math.exp(-0.5*x*x);
  const tail=density*t*(0.319381530+t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))));
  return sign>0?1-tail:tail;
}
function probabilityOverLine(estimate,line){
  const boundary=Math.floor(Number(line))+0.5;
  const pOver=1-normalCdf((boundary-estimate.projection)/estimate.sigma);
  return Math.max(0,Math.min(1,pOver));
}
function showSelectedMatch(ev){
  const panel=$("selectedMatch");panel.hidden=false;
  $("selectedMatchTitle").textContent=(ev.team1||"Команда 1")+" — "+(ev.team2||"Команда 2");
  const score=findLiveScore(ev),period=findLivePeriod(ev),clock=findLiveClock(ev);
  const matchForecast=automaticForecast(ev);
  const estimate=matchForecast.ready&&matchForecast.kind!=="market_reference"?matchForecast.estimate:null;
  const lines=getMatchTotalLines(ev).filter(x=>!x.stale);
  const quarter=automaticQuarterForecast(ev);
  const rows=Array.isArray(ev.external_statistics)?ev.external_statistics:[];
  const statsQuality=ev.statistics_quality||null;
  const possession=estimate?.possession_model||possessionModelContext(ev,estimate||{},period,clock);
  const shooting=liveShootingContext(ev,{elapsed_seconds:estimate?.elapsed_seconds||1});
  const modelBadge=$("possessionModelStatus");
  if(modelBadge){
    modelBadge.textContent=possession.applied?"АКТИВНА · "+(possession.method||"расчёт"):"НЕ АКТИВНА · нет входных данных";
    modelBadge.className=possession.applied?"model-active":"muted";
  }
  const norm=k=>String(k||"").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"");
  const stat=(keys)=>{
    const row=rows.find(x=>keys.map(norm).includes(norm(x?.metric_key||x?.name)));
    if(!row)return null;
    const read=v=>{if(v&&typeof v==="object")v=v.current??v.value??v.total??v.count??v.displayValue??v.display_value;const p=parseStatCount(v);return p?.value??null;};
    const home=read(row.home),away=read(row.away);
    return home!=null&&away!=null?{home,away,name:row.name||keys[0],source:row.source||""}:null;
  };
  const statGroups=[
    ["Владения",["possessions","possession","team_possessions","total_possessions","ball_possessions","possession_count"]],
    ["Броски с игры",["field_goals","field_goals_made","fgm"]],
    ["Попытки с игры",["field_goals_attempted","field_goal_attempts","fga"]],
    ["2 очка",["two_pointers","two_pointers_made","2pt_made"]],
    ["Попытки 2 очка",["two_pointers_attempted","2pt_attempted"]],
    ["3 очка",["three_pointers","three_pointers_made","3pt_made"]],
    ["Попытки 3 очка",["three_pointers_attempted","3pt_attempted"]],
    ["Штрафные",["free_throws","free_throws_made","ftm"]],
    ["Попытки штрафных",["free_throws_attempted","free_throw_attempts","fta"]],
    ["Подборы",["rebounds","total_rebounds"]],
    ["Подборы в нападении",["offensive_rebounds","oreb"]],
    ["Подборы в защите",["defensive_rebounds","dreb"]],
    ["Потери",["turnovers","turnover","lost_balls"]],
    ["Фолы",["fouls","personal_fouls","team_fouls"]],
    ["Передачи",["assists"]],
    ["Перехваты",["steals"]],
    ["Блоки",["blocks"]]
  ].map(([label,keys])=>({label,data:stat(keys)})).filter(x=>x.data);
  const timeline=Array.isArray(ev.period_scores)?ev.period_scores.map((p,i)=>"Q"+(i+1)+" "+(p?.[0]??"—")+":"+ (p?.[1]??"—")).join(" · "):"";
  const lineRows=lines.map(x=>{
    const p=(estimate?.poisson?.probabilities||[]).find(z=>Number(z.line)===Number(x.line));
    return '<tr><td>'+x.line+'</td><td>'+(p?(p.pOver*100).toFixed(1)+'%':'—')+'</td><td>'+(p?(p.pUnder*100).toFixed(1)+'%':'—')+'</td><td>'+(p?escapeHtml(p.direction):'—')+'</td><td>'+(x.odds?escapeHtml(x.odds):'—')+'</td></tr>';
  }).join("");
  const statHtml=statGroups.length?'<div class="calc-grid">'+statGroups.map(x=>'<div class="calc-card"><span>'+escapeHtml(x.label)+'</span><b>'+x.data.home+' : '+x.data.away+'</b></div>').join("")+'</div>':'<p class="muted">PARI пока не передал командную статистику в ответе матча. Значения не подставляются искусственно.</p>';
  $("selectedMatchMeta").textContent=[ev.league||ev.sport_name].filter(Boolean).join(" · ");
  $("selectedMatchInfo").textContent="Счёт "+(score?score.join(" : "):"нет данных")+" · "+(period?"Четверть "+period:"четверть —")+" · "+(clock?"Время "+clock:"время —")+(ev.score_source?" · источник: "+ev.score_source:"");
  $("matchForecast").innerHTML=estimate
    ? '<div class="forecast-main"><p><b>Ожидаемый тотал матча</b></p><div class="forecast-number">'+estimate.projection+'</div><p>Текущий тотал: '+estimate.current+' · прошло: '+estimate.elapsed_minutes+' мин · осталось: '+estimate.remaining_minutes+' мин</p><p>Диапазон 80%: '+(estimate.poisson?.low80??estimate.low)+'–'+(estimate.poisson?.high80??estimate.high)+' · диапазон 95%: '+(estimate.poisson?.low95??"—")+'–'+(estimate.poisson?.high95??"—")+'</p></div>'+
      '<h3>Расчёт темпа и прогноза</h3><div class="calc-grid"><div class="calc-card"><span>Очков в минуту</span><b>'+estimate.pace_per_min+'</b></div><div class="calc-card"><span>Ожидается до конца</span><b>'+(estimate.poisson?.mu??(estimate.projection-estimate.current))+'</b></div><div class="calc-card"><span>Разброс модели σ</span><b>'+(estimate.sigma??"—")+'</b></div><div class="calc-card"><span>Поправка контекста</span><b>'+((Number(estimate.context_adjustment||1)-1)*100).toFixed(1)+'%</b></div></div>'+
      '<p class="calc-formula">Базовая формула: текущие очки ÷ прошедшие минуты = темп; темп × оставшиеся минуты = ожидаемые очки до конца; затем учитываются подтверждённые игровые факторы и статистика.</p>'+
      '<h3>Вероятности по линии PARI</h3>'+(lineRows?'<table class="calc-table"><thead><tr><th>Линия</th><th>ТБ</th><th>ТМ</th><th>Ориентир</th><th>Коэфф. PARI</th></tr></thead><tbody>'+lineRows+'</tbody></table>':'<p class="muted">Линия полного тотала не найдена.</p>')+
      '<h3>Модель владений</h3>'+(possession.applied?'<div class="calc-grid"><div class="calc-card"><span>Владения команды 1</span><b>'+possession.home+'</b></div><div class="calc-card"><span>Владения команды 2</span><b>'+possession.away+'</b></div><div class="calc-card"><span>Среднее владений</span><b>'+possession.average+'</b></div><div class="calc-card"><span>Владений в минуту</span><b>'+possession.possPerMinute+'</b></div><div class="calc-card"><span>Очков на владение</span><b>'+possession.pointsPerPossession+'</b></div><div class="calc-card"><span>Разница владений</span><b>'+possession.imbalance_percent+'%</b></div></div><p class="calc-formula">Источник: '+escapeHtml(possession.method)+'. Прогноз: текущие очки + (владения в минуту × оставшиеся минуты × очки на владение).</p>':'<p class="muted">Расчёт не активирован: '+escapeHtml(possession.reason||"нет пригодной статистики владений")+'. Модель не подменяет реальные владения оценкой по одному лишь счёту.</p>')+
      '<h3>Эффективность бросков</h3><p>'+(shooting.efg!=null?'eFG: '+(shooting.efg*100).toFixed(1)+'% · поправка оставшегося ожидания '+((shooting.adjustment-1)*100).toFixed(1)+'%':'Не рассчитана: '+escapeHtml(shooting.quality||"нет статистики бросков"))+'</p>'+
      '<h3>Факторы, вошедшие в расчёт</h3><ul class="calc-factors">'+(estimate.context_factors||[]).map(x=>'<li>'+escapeHtml(x)+'</li>').join("")+'</ul>'
    : '<p class="muted">'+escapeHtml(matchForecast.reason||"Для прогноза пока недостаточно подтверждённых данных.")+'</p>';
  $("quarterForecast").innerHTML=quarter.ready
    ? '<div class="forecast-main"><p><b>Ожидаемый тотал четверти</b></p><div class="forecast-number">'+quarter.projection+'</div><p>Сейчас: '+quarter.current+' · прошло: '+quarter.elapsed+' мин · диапазон 80% '+quarter.low80+'–'+quarter.high80+' · 95% '+quarter.low95+'–'+quarter.high95+'</p></div>'+
      '<div class="calc-grid"><div class="calc-card"><span>ТБ вероятность</span><b>'+((quarter.poisson?.probabilities?.[0]?.pOver??0)*100).toFixed(1)+'%</b></div><div class="calc-card"><span>ТМ вероятность</span><b>'+((quarter.poisson?.probabilities?.[0]?.pUnder??0)*100).toFixed(1)+'%</b></div></div>'+
      (quarter.lines.length?'<p>Линии: '+quarter.lines.map(x=>escapeHtml(x.line+' · ТБ '+(x.pOver*100).toFixed(1)+'% / ТМ '+(x.pUnder*100).toFixed(1)+'%')).join(' · ')+'</p>':'<p class="muted">Линия четверти пока недоступна.</p>')+
      '<p class="calc-formula">Ориентир четверти: набранные очки ÷ прошедшие секунды × длительность четверти. Интервал строится по 10 000 симуляций.</p>'
    : '<p class="muted">'+escapeHtml(quarter.reason||"Нет достаточных данных для прогноза четверти.")+'</p>';
  $("matchForecast").insertAdjacentHTML("beforeend",'<div class="compact-analysis"><h3>Статистика матча</h3>'+statHtml+
    (timeline?'<p><b>Счёт по четвертям:</b> '+escapeHtml(timeline)+'</p>':'')+
    (statsQuality?'<p class="muted">Получено показателей: '+statsQuality.available_count+'/'+statsQuality.expected_count+'. Источник: '+escapeHtml(ev.statistics_source||"не указан")+'.</p>':'')+
    '<h3>Все полученные показатели ('+rows.length+')</h3>'+
    (rows.length?rows.map(st=>'<p>'+escapeHtml(st.name||st.metric_key||"Показатель")+': '+escapeHtml(st.home??"—")+' : '+escapeHtml(st.away??"—")+' <span class="muted">'+escapeHtml(st.source||"")+'</span></p>').join(""):'<p class="muted">Подробные показатели не получены.</p>')+'</div>');
  panel.scrollIntoView({behavior:"smooth",block:"start"});
}
// Direct PARI event feed: refresh on page open and every 30 seconds.
const liveList = $("liveMatchList");
const liveMessage = $("liveFeedMessage");
const liveBadge = $("liveFeedBadge");
const liveUpdated = $("liveFeedUpdated");
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}
function eventScore(ev) {
  const score=findLiveScore(ev);
  return score ? String(score[0])+":"+String(score[1]) : "";
}
function eventPeriod(ev) {
  const period=findLivePeriod(ev);
  return period ? "Q"+period : null;
}
function eventClock(ev) {
  return findLiveClock(ev);
}

function findPeriodLengthSeconds(ev) {
  const raw = ev.raw || {};
  const candidates = [ev.external_live?.period_length_seconds, ev.period_length_seconds,
    raw.periodLength, raw.period_length, raw.periodDuration, raw.period_duration,
    ev.live_fields?.periodLength, ev.live_fields?.period_length];
  for (const value of candidates) {
    if (value == null) continue;
    if (typeof value === "string" && /^\d{1,2}:[0-5]\d$/.test(value.trim())) {
      const [m,s] = value.trim().split(":").map(Number);
      const seconds = m*60+s;
      if (seconds >= 300 && seconds <= 1200) return seconds;
    }
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) {
      const seconds = n > 100000 ? Math.round(n/1000) : Math.round(n);
      if (seconds >= 300 && seconds <= 1200) return seconds;
    }
  }
  // Fallback to PARI's competition label when the API omits period duration.
  // Do not apply a generic 10-minute assumption to unknown competitions.
  const competition = [ev.league, ev.sport_name, raw.name, raw.competitionName]
    .filter(Boolean).join(" ").toLowerCase();
  if (/\bc\s*dbl\b|chinese basketball development league/.test(competition)) return 720;
  if (/bskt\s*cup|cup\s*bskt|кубок\s*bskt/.test(competition)) return 600;
  if (/\bnba\b|g league/.test(competition)) return 720;
  if (/fiba|euroleague|eurocup|eurobasket|vtb|суперлиг|единая лига|чемпионат европ|world cup|basketball champions league/.test(competition)) return 600;
  return null;
}

function getMatchTotalLines(ev) {
  const factors = [];
  const add = value => {
    if (Array.isArray(value)) { for (const item of value) add(item); return; }
    if (!value || typeof value !== "object") return;
    if (["930","931"].includes(String(value.f))) factors.push(value);
    for (const [key, child] of Object.entries(value)) {
      if (["factors","markets","customFactors","match_total_candidates","related_factors"].includes(key)) add(child);
    }
  };
  add(ev.match_total_candidates);
  add(ev.factors);
  add(ev.related_factors);
  add(ev.related_factor_groups);
  add(ev.related_events);
  const seen = new Set(), lines = [];
  for (const factor of factors) {
    const line = factor.pt != null ? Number(factor.pt) : (factor.p != null ? Number(factor.p)/100 : NaN);
    if (!Number.isFinite(line) || line <= 0 || line > 400) continue;
    const key = line.toFixed(2);
    if (seen.has(key)) continue;
    seen.add(key);
    lines.push({line, odds: factor.v == null ? null : String(factor.v), factorId:String(factor.f), source:"PARI"});
  }
  // PARI can temporarily omit suspended markets late in a quarter. Keep the
  // last verified line for this exact event in this browser, visibly marked stale.
  const cacheKey = "pari-total-lines-v1:" + String(ev.id || "");
  try {
    if (lines.length) {
      localStorage.setItem(cacheKey, JSON.stringify({savedAt:Date.now(), lines}));
      return lines.sort((a,b)=>a.line-b.line);
    }
    const cached = JSON.parse(localStorage.getItem(cacheKey) || "null");
    if (cached && Array.isArray(cached.lines) && Date.now()-Number(cached.savedAt) < 6*60*60*1000) {
      return cached.lines.map(x=>({...x,source:"PARI · последняя полученная линия",stale:true}));
    }
  } catch (_) {}
  return lines;
}

function inferPeriodLengthForForecast(ev) {
  const explicit=findPeriodLengthSeconds(ev);
  const text=[ev.league,ev.sport_name,ev.team1,ev.team2].filter(Boolean).join(" ").toLowerCase();
  const periodCount=Number(ev.external_live?.period_count||0);
  if(explicit)return {seconds:explicit,periods:periodCount===2?2:4,
    method:ev.external_live?.source==="ESPN"&&periodCount===2
      ?"ESPN NCAA · 2 половины по 20 минут":"длительность периода получена из источника"};
  // NCAA men's basketball uses two 20-minute halves, not four quarters.
  if(/ncaa|college|university|студент|студ\b|колледж|march madness/.test(text))return null;
  // Verified competition-specific period lengths.
  if(/\bc\s*dbl\b|chinese basketball development league/.test(text))
    return {seconds:720,periods:4,method:"China CDBL · 4×12 минут"};
  if(/bskt\s*cup/.test(text))
    return {seconds:600,periods:4,method:"BSKT Cup · 4×10 минут"};
  if(/\bnba\b|g league|баскетбол nba/.test(text))
    return {seconds:720,periods:4,method:"формат NBA · 12 минут"};
  // Apply the 10-minute FIBA format only to clearly identified competitions.
  // Unknown leagues must not inherit an assumed period length.
  if(/fiba|euroleague|eurocup|eurobasket|vtb|суперлиг|единая лига|чемпионат европ|world cup|basketball champions league/.test(text))
    return {seconds:600,periods:4,method:"формат FIBA · 10 минут (по типу турнира)"};
  return null;
}
function estimateLiveTotalFromPeriod(ev,score,period,lengthInfo) {
  if(!score||!period||period<2||!lengthInfo)return null;
  const total=Number(score[0])+Number(score[1]), length=lengthInfo.seconds, periods=Number(lengthInfo.periods||4);
  if(!Number.isFinite(total)||total<=0||!Number.isFinite(length)||length<300)return null;
  if(![2,4].includes(periods)||period>periods)return null;
  const elapsedMin=(period-1)*length, elapsedMax=period*length;
  if(elapsedMin<12*60)return null;
  const gameSeconds=length*periods;
  const low=Math.round(total/elapsedMax*gameSeconds);
  const high=Math.round(total/elapsedMin*gameSeconds);
  if(!Number.isFinite(low)||!Number.isFinite(high)||low<=0||high<low||high>500)return null;
  const projection=Math.round((low+high)/2*10)/10;
  return {current:total,projection,low,high,sigma:Math.max(8,(high-low)/2.563),
    elapsed_seconds_min:elapsedMin,elapsed_seconds_max:elapsedMax,
    elapsed:Math.round((elapsedMin+elapsedMax)/12)/10,
    source:"period_range",period_length_seconds:length,period_count:periods,period_length_method:lengthInfo.method};
}
function marketReferenceForecast(lines) {
  const valid=(lines||[]).filter(x=>Number.isFinite(Number(x.line))&&!x.stale);
  if(!valid.length)return null;
  const sorted=valid.map(x=>Number(x.line)).sort((a,b)=>a-b);
  const projection=Math.round(sorted.reduce((sum,value)=>sum+value,0)/sorted.length*10)/10;
  return {ready:true,kind:"market_reference",estimate:{projection,low:sorted[0],high:sorted[sorted.length-1]},
    lines,message:"Рыночный ориентир PARI, не самостоятельная игровая модель"};
}
function automaticForecast(ev) {
  const lines=getMatchTotalLines(ev);
  const score=findLiveScore(ev), period=findLivePeriod(ev), clock=findLiveClock(ev);
  const lengthInfo=inferPeriodLengthForForecast(ev);
  const periodLength=lengthInfo?.seconds;
  if(score&&period&&clock&&periodLength){
    const estimate=estimateLiveTotal({...ev,external_live:{...(ev.external_live||{}),period_length_seconds:periodLength}},score,period,clock);
    if(estimate&&estimate.elapsed_seconds>=12*60)
      return {ready:true,kind:"live_tempo",estimate:applyPoissonModel(estimate,lines,String(ev.id||"")+"|"+score.join(":")+"|"+clock,ev,period,clock),lines,message:"Линейный темп + распределение Пуассона + 10 000 симуляций Монте-Карло"};
  }
  if(score&&period&&lengthInfo){
    const estimate=estimateLiveTotalFromPeriod(ev,score,period,lengthInfo);
    if(estimate)return {ready:true,kind:"period_range",estimate:applyPoissonModel(estimate,lines,String(ev.id||"")+"|"+score.join(":")+"|Q"+period,ev,period,clock),lines,message:"Предварительная оценка без таймера; вероятность не считается подтверждённой"};
  }
  const market=marketReferenceForecast(lines);
  if(market){
    const missing=[];
    if(!score)missing.push("счёт");
    if(!period)missing.push("четверть");
    if(!clock)missing.push("таймер");
    if(!periodLength)missing.push("формат/длительность");
    return {...market,reason:"Игровой расчёт ждёт: "+missing.join(", ")};
  }
  const missing=[];
  if(!score)missing.push("счёт");
  if(!period)missing.push("четверть");
  if(!clock)missing.push("таймер");
  if(!periodLength)missing.push("длительность периода");
  return {ready:false,reason:"Нет подтверждённых данных: "+missing.join(", "),lines};
}

function automaticQuarterForecast(ev) {
  const period=findLivePeriod(ev), clock=findLiveClock(ev), length=findPeriodLengthSeconds(ev) || inferPeriodLengthForForecast(ev)?.seconds;
  if(Number(ev.external_live?.period_count)===2)return {ready:false,reason:"в турнире играют две половины, а не четыре четверти"};
  if (!period || !clock || !length) return {ready:false,reason:"нужны четверть, таймер и длительность периода"};
  // PARI supplies period_scores as an ordered array; external providers
  // may supply an object keyed by period number. Support both shapes.
  const scores=ev.period_scores ?? ev.external_live?.period_scores ?? {};
  const score=Array.isArray(scores) ? scores[period-1] : scores[String(period)];
  if (!Array.isArray(score) || score.length<2 || score.some(v=>v==null || !Number.isFinite(Number(v))))
    return {ready:false,reason:"нет подтверждённого счёта текущей четверти"};
  const m=String(clock).match(/^(\d{1,2}):([0-5]\d)$/);
  if (!m) return {ready:false,reason:"неизвестный формат таймера"};
  const remaining=Number(m[1])*60+Number(m[2]), elapsed=length-remaining;
  const current=Number(score[0])+Number(score[1]);
  // Start only after 3 minutes, but don't stop forecasting after minute 6:
  // later valid snapshots are still more useful than no forecast.
  if (elapsed<180) return {ready:false,reason:"ожидаем минимум 3 минуты игрового времени в четверти"};
  if (elapsed<=0 || elapsed>length || !Number.isFinite(current)) return {ready:false,reason:"недостаточно времени для расчёта"};
  const projection=Math.round(current/elapsed*length*10)/10;
  const groups=(ev.related_factor_groups||[]).filter(g=>String(g.name||"").toLowerCase().includes(String(period)));
  const lines=[];
  for(const group of groups) for(const factor of (group.factors||[])) {
    if(!factor || !["930","931"].includes(String(factor.f))) continue;
    const line=factor.pt!=null?Number(factor.pt):(factor.p!=null?Number(factor.p)/100:NaN);
    if(Number.isFinite(line)&&line>0&&line<150&&!lines.some(x=>x.line===line)) lines.push({line});
  }
  const quarterEstimate={current,projection,current_score:current,elapsed:Math.round(elapsed/60*10)/10};
  const poisson=poissonMonteCarlo(current,Math.max(0,projection-current),lines,String(ev.id||"")+"|Q"+period+"|"+clock);
  return {ready:true,...quarterEstimate,low80:poisson.low80,high80:poisson.high80,low95:poisson.low95,high95:poisson.high95,poisson,lines:poisson.probabilities,context_factors:["проекция по темпу текущей четверти"]};
}
let liveRefreshInFlight = false;
let nextLiveRefreshDelay = 30000;
async function refreshLiveMatches() {
  nextLiveRefreshDelay = 30000;
  if (liveRefreshInFlight) return;
  liveRefreshInFlight = true;
  const button = $("refreshLiveMatches");
  if (button) button.disabled = true;
  if (liveMessage && !liveList.children.length) liveMessage.textContent = "Запрашиваем events/list у серверов PARI…";
  let pariFeedFailure = null;
  try {
    const controller = new AbortController();
    const requestTimeout = setTimeout(() => controller.abort(), 22000);
    let response;
    try {
      response = await fetch("/api/pari/live", {cache:"no-store", signal:controller.signal});
    } catch (error) {
      if (controller.signal.aborted) throw new Error("PARI API не ответил за 22 секунды");
      throw error;
    } finally {
      clearTimeout(requestTimeout);
    }
    const data = await response.json();
    if (!response.ok || !data.ok) {
      pariFeedFailure = data;
      throw new Error(data.error || "Источник PARI недоступен");
    }
    if (data.loading) {
      nextLiveRefreshDelay = 4000;
      if (liveBadge) { liveBadge.textContent = "Загружаем матчи PARI…"; liveBadge.className = "pill warn"; }
      if (liveMessage) liveMessage.textContent = "Сервер получает свежий список матчей PARI и проверяет дополнительный LIVE-счёт.";
      if ($("liveSourceStatus")) { $("liveSourceStatus").textContent = "Загрузка…"; $("liveSourceStatus").className = "amber"; }
      $("feedStatus").textContent = "PARI LIVE: получаем список матчей…";
      return;
    }
    const count = (data.events || []).length;
    window.pariLiveEvents = data.events || [];
    const sourceStatus = $("liveSourceStatus");
    const sourceDetail = $("liveSourceDetail");
    if (sourceStatus) { sourceStatus.textContent = "Подключён"; sourceStatus.className = "success"; }
    if (sourceDetail) sourceDetail.textContent = "PARI API отвечает · автообновление каждые 30 секунд";
    if (liveBadge) {
      liveBadge.textContent = count ? "LIVE-источник отвечает" : "API отвечает, матчей не найдено";
      liveBadge.className = "pill " + (count ? "success" : "warn");
    }
    const liveScoreSource = data.live_score_source || {};
    if (liveMessage) liveMessage.textContent = count
      ? "PARI: " + data.total_events + " событий, LIVE-матчей: " + count + ". Независимый источник счёта: " + (liveScoreSource.status === "ok" ? "получен, совпадений " + (liveScoreSource.matched || 0) : liveScoreSource.status === "no_live_events" ? "ответил, но не вернул LIVE-матчи" : "недоступен (" + (liveScoreSource.errors || []).join(", ") + ")") + ". Счёт и таймер отображаются только при подтверждённом совпадении команд."
      : "API PARI отвечает (" + data.total_events + " событий), но сейчас не найдено LIVE-событий мужского баскетбола после фильтрации лиг.";
    if (liveUpdated) liveUpdated.textContent = "Обновлено: " + new Date(data.checked_at_utc).toLocaleTimeString("ru-RU") + " · " + (data.attempts?.[0]?.host || "PARI");
    if (liveList) {
      liveList.innerHTML = count ? data.events.map(ev => {
        const teams = escapeHtml(ev.team1 || "Команда 1") + " — " + escapeHtml(ev.team2 || "Команда 2");
        const score = eventScore(ev);
        const totalMarkets = (ev.match_total_candidates || []).map(f => f.pt ? ("Тотал " + f.pt + " @ " + f.v) : ("Коэф. " + f.v));
        const statCount = (ev.external_statistics || []).length;
        const coverage = ev.statistics_quality ? (" · покрытие " + ev.statistics_quality.available_count + "/" + ev.statistics_quality.expected_count) : "";
        const statsLabel = statCount ? ("Статистика: " + statCount + " показателей" + coverage + " · " + (ev.statistics_source || "источник не указан")) : "Статистика: нет подтверждённых значений PARI/внешнего источника";
        const details = [ev.league || ev.sport_name, score, eventPeriod(ev), eventClock(ev), statsLabel, ...totalMarkets].filter(Boolean).map(escapeHtml).join(" · ");
        const forecast = automaticForecast(ev);
        const quarterForecast = automaticQuarterForecast(ev);
        let forecastHtml = "";
        if (forecast.ready) {
          const estimate = forecast.estimate;
          let lineText;
          if(forecast.kind==="market_reference") {
            lineText="рыночный ориентир PARI "+estimate.projection+" · игровое направление пока не подтверждено";
          } else {
            lineText = forecast.lines.length
              ? forecast.lines.map(x => {
                  const mc=(estimate.poisson?.probabilities||[]).find(p=>Number(p.line)===Number(x.line));
                  const direction=mc?mc.direction:(estimate.low>x.line?"ТБ":(estimate.high<x.line?"ТМ":"без преимущества"));
                  return direction+" "+x.line+(mc?" · ТБ "+(mc.pOver*100).toFixed(0)+"% / ТМ "+(mc.pUnder*100).toFixed(0)+"%":"")+" · диапазон "+estimate.low+"–"+estimate.high;
                }).join(" | ")
              : "ориентир "+estimate.projection+" · диапазон "+estimate.low+"–"+estimate.high;
            if(forecast.kind==="period_range")lineText+=" · без таймера, предварительно";
          }
          forecastHtml = '<span class="live-match-meta forecast-live">Прогноз матча: ' + escapeHtml(lineText) + '</span>';
        } else {
          forecastHtml = '<span class="live-match-meta forecast-pending">Прогноз матча ожидает данные: ' + escapeHtml(forecast.reason) + '</span>';
        }
        if (quarterForecast.ready) {
          const qLines=quarterForecast.lines.length
            ? quarterForecast.lines.map(x => x.direction+" "+x.line+" · ТБ "+(x.pOver*100).toFixed(0)+"% / ТМ "+(x.pUnder*100).toFixed(0)+"%").join(" | ")
            : "линия четверти пока не найдена";
          forecastHtml += '<span class="live-match-meta forecast-live">Прогноз Q'+escapeHtml(findLivePeriod(ev))+': '+escapeHtml(quarterForecast.projection)+' очков за четверть · '+escapeHtml(qLines)+'</span>';
        } else {
          forecastHtml += '<span class="live-match-meta forecast-pending">Прогноз четверти: '+escapeHtml(quarterForecast.reason)+'</span>';
        }
        return '<button type="button" class="live-match" data-event-id="' + escapeHtml(ev.id) + '">' +
          '<span class="live-match-title">' + teams + '</span><span class="live-match-meta">' + (details || "Событие получено из LIVE API PARI") + '</span>' + forecastHtml + '</button>';
      }).join("") : '<p class="muted">В текущем ответе нет подтверждённых баскетбольных LIVE-матчей.</p>';
      liveList.querySelectorAll(".live-match").forEach(el => el.addEventListener("click", () => {
        const ev = (window.pariLiveEvents || []).find(x => String(x.id) === el.dataset.eventId);
        if (ev) showSelectedMatch(ev);
      }));
    }
    $("feedStatus").textContent = count ? "PARI LIVE: найдено " + count + " матчей" : "PARI API отвечает; матчи не распознаны";
  } catch (error) {
    const sourceStatus = $("liveSourceStatus");
    const sourceDetail = $("liveSourceDetail");
    if (sourceStatus) { sourceStatus.textContent = "Нет соединения"; sourceStatus.className = "amber"; }
    if (sourceDetail) {
      const attempts = (pariFeedFailure && pariFeedFailure.attempts) || [];
      const details = attempts.map(x => {
        const host = String(x.host || "PARI");
        const status = x.status == null ? "" : " · HTTP " + x.status;
        const type = x.content_type ? " · " + x.content_type : "";
        const reason = x.error || "ошибка";
        const preview = x.body_preview ? " · " + String(x.body_preview).slice(0, 150) : "";
        return host + status + type + " · " + reason + preview;
      });
      sourceDetail.textContent = details.length ? details.join(" | ") : "Не удалось получить ответ PARI API";
    }
    if (liveBadge) { liveBadge.textContent = "Нет подтверждения LIVE"; liveBadge.className = "pill warn"; }
    if (liveMessage) liveMessage.textContent = "Не удалось получить список матчей: " + (error.message || "ошибка запроса");
    if (liveUpdated) liveUpdated.textContent = "Ошибка первого ответа: " + (error.message || "сбой запроса");
    $("feedStatus").textContent = "PARI LIVE: подключение не подтверждено";
  } finally {
    liveRefreshInFlight = false;
    if (button) button.disabled = false;
    // Schedule the next poll only after this request has finished; never stack requests.
    setTimeout(refreshLiveMatches, nextLiveRefreshDelay);
  }
}
if ($("refreshLiveMatches")) $("refreshLiveMatches").addEventListener("click", refreshLiveMatches);
refreshLiveMatches();
