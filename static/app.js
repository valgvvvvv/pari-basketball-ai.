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
  return {current:total,projection:Math.round(projection*10)/10,
    low:Math.max(total,Math.round(projection-margin)),high:Math.round(projection+margin),
    sigma:Math.round(sigma*10)/10,elapsed:Math.round(elapsed/60*10)/10,
    elapsed_seconds:elapsed,period_elapsed_seconds:length-remaining};
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
  const objects=collectEventObjects({root:ev.raw||{},related:ev.related_events||[],live:ev.live_fields||{}});
  const score=findLiveScore(ev),period=findLivePeriod(ev),clock=findLiveClock(ev);
  const lengthInfo=inferPeriodLengthForForecast(ev);
  const periodLength=lengthInfo?.seconds;
  const paceCandidate=(periodLength&&score&&period&&clock) ? estimateLiveTotal({...ev,external_live:{...(ev.external_live||{}),period_length_seconds:periodLength}},score,period,clock) : null;
  const clocklessCandidate=(!paceCandidate&&score&&period) ? estimateLiveTotalFromPeriod(ev,score,period,lengthInfo) : null;
  const paceEstimate=(paceCandidate&&paceCandidate.elapsed_seconds>=12*60)?paceCandidate:clocklessCandidate;
  const marketFallback=marketReferenceForecast(getMatchTotalLines(ev));
  const matchLines=getMatchTotalLines(ev);
  const childCount=(ev.related_events||[]).length;
  const fieldNames=[...new Set(objects.flatMap(o=>Object.keys(o)))];
  const rootFactors=(ev.factors||[]).filter(x=>x&&["930","931"].includes(String(x.f)));
  const quarterGroups=(ev.related_factor_groups||[]).filter(g=>g&&Array.isArray(g.factors)&&g.factors.some(x=>x&&["930","931"].includes(String(x.f))));
  const fmtFactors=list=>list.filter(x=>["930","931"].includes(String(x.f))).map(x=>{
    const line=x.pt!=null?String(x.pt):(x.p!=null?String(Number(x.p)/100):"линия не указана");
    return "линия "+line+" · ID "+String(x.f)+" · кэф. "+(x.v??"—");
  });
  const stats=[];
  for(const obj of objects)for(const [key,value] of Object.entries(obj)){
    if(/score|foul|rebound|assist|attempt|throw|possession|percent|three|two|block|steal|turnover|points|stat/i.test(key)&&!/^statisticsTypes$/i.test(key)&&value!=null&&(typeof value!=="object"||Array.isArray(value)))stats.push(key+": "+(Array.isArray(value)?JSON.stringify(value):String(value)));
  }
  const externalStats=Array.isArray(ev.external_statistics)?ev.external_statistics:[];
  const statsQuality=ev.statistics_quality||null;
  const statsCoverage=statsQuality?(" · покрытие ключевых категорий "+statsQuality.available_count+"/"+statsQuality.expected_count+" ("+statsQuality.coverage_percent+"%)"):"";
  const externalStatsHtml=externalStats.length?'<details class="source-statistics" open><summary>Статистика матча · '+escapeHtml(ev.statistics_source||"внешний источник")+' ('+externalStats.length+statsCoverage+')</summary>'+
    (statsQuality&&statsQuality.missing_metrics?.length?'<p class="muted">Не получены: '+statsQuality.missing_metrics.map(escapeHtml).join(", ")+'</p>':'')+
    externalStats.map(st=>'<p><b>'+escapeHtml(st.group||"Статистика")+'</b> · '+escapeHtml(st.name||"Показатель")+': '+escapeHtml(st.home??"—")+' : '+escapeHtml(st.away??"—")+'</p>').join("")+'</details>':"";
  const statsText=[...new Set(stats)].slice(0,24);
  const rootLines=fmtFactors(rootFactors);
  const allMarketLines=matchLines.map(x=>"линия "+x.line+" · ID "+x.factorId+(x.odds?" · кэф. "+x.odds:"")+(x.stale?" · последняя полученная, не текущая":""));
  const groupHtml=quarterGroups.map(g=>{
    const label=g.name||("Дочерняя строка "+(g.event_id||"PARI"));
    const lines=fmtFactors(g.factors);
    return '<p><b>'+escapeHtml(label)+'</b></p><p class="muted">'+(lines.length?lines.map(escapeHtml).join("<br>"):"Факторы не распознаны")+'</p>';
  }).join("");
  $("selectedMatchMeta").textContent=[ev.league||ev.sport_name,"Строк PARI: "+(childCount+1),"Полей: "+fieldNames.length].filter(Boolean).join(" · ");
  $("selectedMatchInfo").textContent="Счёт: "+(score?score.join(" : "):"не найден")+" · Четверть: "+(period||"не определена")+" · Время: "+(clock||"не найден")+" · Источник счёта: "+(score?(ev.score_source||"PARI"):"не получен")+" · Источник времени: "+(clock?(ev.clock_source||"PARI"):"не получен")+" · Статистика: "+(ev.external_statistics||[]).length+" показателей"+(ev.statistics_source?" ("+ev.statistics_source+")":"")+" · Рынков-факторов: "+new Set([...(ev.factors||[]),...(ev.related_factors||[])].map(x=>String(x.f)+"|"+String(x.pt||x.p||"")+"|"+String(x.v||""))).size;
  $("matchForecast").innerHTML=
    '<p><b>Факторы в основной строке матча PARI</b></p>'+
    '<p class="muted">'+(rootLines.length?rootLines.map(escapeHtml).join("<br>"):(allMarketLines.length?"В основной строке факторов нет; найдено в иерархии/локальной истории: "+allMarketLines.map(escapeHtml).join(" · "):"В текущем ответе PARI нет факторов ID 930/931."))+'</p>'+
    '<p class="muted">Это пока не подтверждённая расшифровка типа рынка: ID фактора и линия показываются как получены от PARI.</p>'+
    '<p class="muted">'+(score&&period&&clock?"Счёт, четверть и таймер доступны.":"Расчёт темпа заблокирован: нет подтверждённого счёта и/или таймера.")+'</p>'+
    (paceEstimate?'<p><b>'+(clocklessCandidate?'Предварительная оценка полного тотала без таймера:':'Темповая оценка полного тотала:')+'</b> '+paceEstimate.projection+' очка · диапазон '+paceEstimate.low+'–'+paceEstimate.high+(paceEstimate.elapsed?' · сыграно около '+paceEstimate.elapsed+' мин.':'')+(clocklessCandidate?' · длительность '+escapeHtml(paceEstimate.period_length_method):'')+'</p>':(marketFallback?'<p><b>Рыночный ориентир полного тотала PARI:</b> '+marketFallback.estimate.projection+' очков. Это линия букмекера, а не независимый игровой прогноз.</p>':'<p class="muted">Нет подтверждённой линии и счёта для расчёта.</p>'))+
    (paceEstimate && matchLines.length ? '<p><b>Сравнение с линиями PARI:</b> '+matchLines.map(x=>{
      if(x.stale)return 'Тотал '+x.line+' · устаревшая линия';
      if(clocklessCandidate){
        const direction=paceEstimate.low>x.line?'ТБ':(paceEstimate.high<x.line?'ТМ':'диапазон пересекает линию');
        return direction+' '+x.line+' · оценка '+paceEstimate.low+'–'+paceEstimate.high;
      }
      const pOver=probabilityOverLine(paceEstimate,x.line),pUnder=1-pOver;
      const direction=pOver>=0.57?'ТБ':(pUnder>=0.57?'ТМ':'без выраженного преимущества');
      return direction+' '+x.line+' · ТБ '+(pOver*100).toFixed(1)+'% / ТМ '+(pUnder*100).toFixed(1)+'%';
    }).map(escapeHtml).join(' · ')+'<p class="muted">'+(clocklessCandidate?'Широкий диапазон построен по номеру периода без таймера и не является откалиброванной вероятностью.':'Приближённые вероятности по текущему темпу; модель ещё не откалибрована на истории лиги.')+'</p>' : '<p class="muted">'+(marketFallback?'Рыночная линия отображена. Направление ТБ/ТМ не назначается без счёта и подтверждённых цен сторон.':'Линия полного тотала не найдена.')+'</p>');
  const quarterEstimate=automaticQuarterForecast(ev);
  $("quarterForecast").innerHTML=
    '<p><b>Текущая четверть:</b> '+(period||"не определена")+'</p>'+
    (quarterEstimate.ready
      ? '<p><b>Ориентир по темпу четверти:</b> '+quarterEstimate.projection+' очка · набрано '+quarterEstimate.current+' · сыграно около '+quarterEstimate.elapsed+' мин.</p>'+
        (quarterEstimate.lines.length?'<p><b>Сравнение с линиями:</b> '+quarterEstimate.lines.map(x=>{
          const sigma=Math.max(3,Math.sqrt(Math.max(quarterEstimate.projection-quarterEstimate.current,1))*1.3);
          const pOver=probabilityOverLine({projection:quarterEstimate.projection,sigma},x.line),pUnder=1-pOver;
          const direction=pOver>=0.57?'ТБ':(pUnder>=0.57?'ТМ':'без выраженного преимущества');
          return direction+' '+x.line+' · ТБ '+(pOver*100).toFixed(1)+'% / ТМ '+(pUnder*100).toFixed(1)+'%';
        }).map(escapeHtml).join(' · ')+'</p><p class="muted">Темповая вероятность без исторической калибровки.</p>':'<p class="muted">Линия тотала текущей четверти не найдена.</p>')
      : '<p class="muted">Прогноз четверти пока недоступен: '+escapeHtml(quarterEstimate.reason)+'.</p>')+
    (groupHtml||'<p class="muted">В дочерних строках не найдено факторов ID 930/931.</p>')+
    '<p class="muted">'+(clock?"Таймер: "+escapeHtml(clock):"Таймер четверти отсутствует в полученных строках PARI.")+'</p>';
  if(externalStatsHtml) $("matchForecast").insertAdjacentHTML("beforeend",externalStatsHtml);
  if(statsText.length){
    $("matchForecast").insertAdjacentHTML("beforeend",'<details class="source-statistics"><summary>Дополнительные числовые поля PARI ('+statsText.length+')</summary><p>'+statsText.map(x=>escapeHtml(x)).join("<br>")+'</p></details>');
  } else if(!externalStatsHtml) {
    $("matchForecast").insertAdjacentHTML("beforeend",'<p class="muted">Числовая игровая статистика пока не получена ни из PARI, ни из дополнительного источника.</p>');
  }
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
      return {ready:true,kind:"live_tempo",estimate,lines,message:"Темповой прогноз; вероятности не откалиброваны на истории лиги"};
  }
  if(score&&period&&lengthInfo){
    const estimate=estimateLiveTotalFromPeriod(ev,score,period,lengthInfo);
    if(estimate)return {ready:true,kind:"period_range",estimate,lines,message:"Предварительный диапазон без таймера; длительность периода: "+lengthInfo.method};
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
  const scores=ev.external_live?.period_scores || {};
  const score=scores[String(period)];
  if (!Array.isArray(score) || score.length<2) return {ready:false,reason:"нет подтверждённого счёта текущей четверти"};
  const m=String(clock).match(/^(\d{1,2}):([0-5]\d)$/);
  if (!m) return {ready:false,reason:"неизвестный формат таймера"};
  const remaining=Number(m[1])*60+Number(m[2]), elapsed=length-remaining;
  const current=Number(score[0])+Number(score[1]);
  if (elapsed<180 || elapsed>360) return {ready:false,reason:"четверть анализируется после 3–6 минут игры"};
  if (elapsed<=0 || elapsed>length || !Number.isFinite(current)) return {ready:false,reason:"недостаточно времени для расчёта"};
  const projection=Math.round(current/elapsed*length*10)/10;
  const groups=(ev.related_factor_groups||[]).filter(g=>String(g.name||"").toLowerCase().includes(String(period)));
  const lines=[];
  for(const group of groups) for(const factor of (group.factors||[])) {
    if(!factor || !["930","931"].includes(String(factor.f))) continue;
    const line=factor.pt!=null?Number(factor.pt):(factor.p!=null?Number(factor.p)/100:NaN);
    if(Number.isFinite(line)&&line>0&&line<150&&!lines.some(x=>x.line===line)) lines.push({line});
  }
  return {ready:true,projection,current,elapsed:Math.round(elapsed/60*10)/10,lines};
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
      if (controller.signal.aborted) throw new Error("PARI API не ответил за 15 секунд");
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
        const statsLabel = statCount ? ("Статистика: " + statCount + " показателей · " + (ev.statistics_source || "внешний источник")) : "Статистика: пока нет подтверждённых данных";
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
                  const direction=estimate.low>x.line?"ТБ":(estimate.high<x.line?"ТМ":"без преимущества");
                  return direction+" "+x.line+" · диапазон "+estimate.low+"–"+estimate.high;
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
            ? quarterForecast.lines.map(x => (quarterForecast.projection>x.line ? "ТБ " : "ТМ ")+x.line).join(" | ")
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
