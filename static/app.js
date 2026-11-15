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
      ["team1Score","team2Score"],["home_score","away_score"],
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
  const objects=collectEventObjects({root:ev.raw||{},related:ev.related_events||[],live:ev.live_fields||{}});
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
  const direct=ev.period??(ev.raw||{}).period??(ev.raw||{}).periodName;
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
  const candidates=[ev.clock,(ev.raw||{}).time,(ev.raw||{}).timer];
  for(const obj of objects)for(const [key,value] of Object.entries(obj))if(/^(time|timer|clock|matchTime|gameTime|remainingTime|periodTime|timeRemaining)$/i.test(key))candidates.push(value);
  for(const value of candidates){
    if(value==null)continue;
    const m=String(value).match(/(?:^|\D)(\d{1,2}):([0-5]\d)(?:\D|$)/);
    if(m)return m[1].padStart(2,"0")+":"+m[2];
  }
  return null;
}
function showSelectedMatch(ev){
  const panel=$("selectedMatch");panel.hidden=false;
  $("selectedMatchTitle").textContent=(ev.team1||"Команда 1")+" — "+(ev.team2||"Команда 2");
  const objects=collectEventObjects({root:ev.raw||{},related:ev.related_events||[],live:ev.live_fields||{}});
  const score=findLiveScore(ev),period=findLivePeriod(ev),clock=findLiveClock(ev);
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
  const statsText=[...new Set(stats)].slice(0,24);
  const rootLines=fmtFactors(rootFactors);
  const groupHtml=quarterGroups.map(g=>{
    const label=g.name||("Дочерняя строка "+(g.event_id||"PARI"));
    const lines=fmtFactors(g.factors);
    return '<p><b>'+escapeHtml(label)+'</b></p><p class="muted">'+(lines.length?lines.map(escapeHtml).join("<br>"):"Факторы не распознаны")+'</p>';
  }).join("");
  $("selectedMatchMeta").textContent=[ev.league||ev.sport_name,"Строк PARI: "+(childCount+1),"Полей: "+fieldNames.length].filter(Boolean).join(" · ");
  $("selectedMatchInfo").textContent="Счёт: "+(score?score.join(" : "):"не найден")+" · Четверть: "+(period||"не определена")+" · Время: "+(clock||"не найден")+" · Источник счёта: "+(ev.score_source||"PARI")+" · Источник времени: "+(ev.clock_source||"PARI")+" · Рынков-факторов: "+new Set([...(ev.factors||[]),...(ev.related_factors||[])].map(x=>String(x.f)+"|"+String(x.pt||x.p||"")+"|"+String(x.v||""))).size;
  $("matchForecast").innerHTML=
    '<p><b>Факторы в основной строке матча PARI</b></p>'+
    '<p class="muted">'+(rootLines.length?rootLines.map(escapeHtml).join("<br>"):"В основной строке нет факторов ID 930/931.")+'</p>'+
    '<p class="muted">Это пока не подтверждённая расшифровка типа рынка: ID фактора и линия показываются как получены от PARI.</p>'+
    '<p class="muted">'+(score&&period&&clock?"Счёт, четверть и таймер доступны.":"Расчёт темпа заблокирован: нет подтверждённого счёта и/или таймера.")+'</p>';
  $("quarterForecast").innerHTML=
    '<p><b>Текущая четверть:</b> '+(period||"не определена")+'</p>'+
    (groupHtml||'<p class="muted">В дочерних строках не найдено факторов ID 930/931.</p>')+
    '<p class="muted">'+(clock?"Таймер: "+escapeHtml(clock):"Таймер четверти отсутствует в полученных строках PARI.")+'</p>';
  if(statsText.length){
    $("matchForecast").insertAdjacentHTML("beforeend",'<details class="source-statistics"><summary>Подтверждённые статистические поля ('+statsText.length+')</summary><p>'+statsText.map(x=>escapeHtml(x)).join("<br>")+'</p></details>');
  } else {
    $("matchForecast").insertAdjacentHTML("beforeend",'<p class="muted">Числовая игровая статистика в ответе PARI не обнаружена; служебное поле statisticsTypes не считается статистикой матча.</p>');
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
  if (ev.score == null) return "";
  if (typeof ev.score === "string" || typeof ev.score === "number") return String(ev.score);
  try { return JSON.stringify(ev.score); } catch (_) { return ""; }
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
    const response = await fetch("/api/pari/live", {cache:"no-store"});
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
      ? "PARI: " + data.total_events + " событий, LIVE-матчей: " + count + ". Независимый источник счёта: " + (liveScoreSource.status === "ok" ? "получен, совпадений " + (liveScoreSource.matched || 0) : "недоступен (" + (liveScoreSource.errors || []).join(", ") + ")") + ". Счёт и таймер отображаются только при подтверждённом совпадении команд."
      : "API PARI отвечает (" + data.total_events + " событий), но сейчас не найдено LIVE-событий мужского баскетбола после фильтрации лиг.";
    if (liveUpdated) liveUpdated.textContent = "Обновлено: " + new Date(data.checked_at_utc).toLocaleTimeString("ru-RU") + " · " + (data.attempts?.[0]?.host || "PARI");
    if (liveList) {
      liveList.innerHTML = count ? data.events.map(ev => {
        const teams = escapeHtml(ev.team1 || "Команда 1") + " — " + escapeHtml(ev.team2 || "Команда 2");
        const score = eventScore(ev);
        const totalMarkets = (ev.match_total_candidates || []).map(f => f.pt ? ("Тотал " + f.pt + " @ " + f.v) : ("Коэф. " + f.v));
        const details = [ev.league || ev.sport_name, score, ev.period, ev.clock, ...totalMarkets].filter(Boolean).map(escapeHtml).join(" · ");
        return '<button type="button" class="live-match" data-event-id="' + escapeHtml(ev.id) + '">' +
          '<span class="live-match-title">' + teams + '</span><span class="live-match-meta">' + (details || "Событие получено из LIVE API PARI") + '</span></button>';
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
