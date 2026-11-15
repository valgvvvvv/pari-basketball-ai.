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
form.addEventListener("submit",async e=>{e.preventDefault();try{const d=getData();const res=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});const payload=await res.json();if(!res.ok)throw Error(payload.detail||"Сервер не смог выполнить расчёт");let r=payload;
 if(r.p_over_mc!==undefined){r={...r,pOver:r.p_over_mc/100,pUnder:r.p_under_mc/100,expected:r.expected_total,pace:r.pace_per_min,mu:r.mu_remaining,left:r.remaining_minutes*60,elapsed:r.elapsed_minutes*60,current:r.current_total,market:r.market_over_devig,pPois:r.p_over_poisson/100,range80:r.range80,range95:r.range95,prob:Math.max(r.p_over_mc,r.p_under_mc)/100};}
 show(d,r);$("result").scrollIntoView({behavior:"smooth",block:"start"});
 }catch(err){alert(err.message||"Не удалось рассчитать прогноз");}});
$("resetBtn").addEventListener("click",()=>{Object.entries(defaults).forEach(([k,v])=>{let el=$(k);if(el)el.value=v;});$("result").hidden=true;});
function renderHistory(){let h=JSON.parse(localStorage.getItem("pariHistory")||"[]");$("historyCount").textContent=h.length;$("historyList").innerHTML=h.length?h.map(x=>`<div class="history-row"><b>${x.match}</b><span>${x.direction} ${x.line}</span><span>${x.prob}% · ${x.grade}</span><span>${x.date}</span></div>`).join(""):'<p class="muted">Пока нет сохранённых расчётов.</p>';}
$("clearHistory").addEventListener("click",()=>{localStorage.removeItem("pariHistory");renderHistory();});
// Health is a backend status only; the live feed status below is authoritative.
fetch("/api/health").catch(()=>{});
renderHistory();


// Отдельный расчёт тотала текущей четверти через серверный endpoint.
const qForm = $("quarterForm");
qForm.addEventListener("submit", async e => {
  e.preventDefault();
  const payload = {
    team1: $("qTeam1").value, team2: $("qTeam2").value,
    quarter: +$("qNumber").value, score1: +$("qScore1").value,
    score2: +$("qScore2").value, clock: $("qClock").value,
    period_minutes: +$("qMinutes").value, total_line: +$("qLine").value,
    simulations: +$("qSims").value
  };
  try {
    const response = await fetch("/api/analyze-quarter", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Ошибка расчёта четверти");
    $("quarterResult").hidden = false;
    $("qTitle").textContent = payload.team1 + " — " + payload.team2 + " · " + payload.quarter + "-я четверть";
    $("qDirection").textContent = result.direction;
    $("qLineLabel").textContent = "Линия " + result.line;
    const p = Math.max(result.p_over_mc, result.p_under_mc);
    $("qProbability").textContent = p.toFixed(1) + "%";
    $("qExpected").textContent = result.expected_total.toFixed(1);
    $("qRemaining").textContent = "Осталось " + result.remaining_minutes.toFixed(2) + " мин.";
    $("qGrade").textContent = "Сигнал " + result.grade;
    $("qRange").textContent = "Диапазон 80%: " + result.range80.join("–") + " · Диапазон 95%: " + result.range95.join("–") + " · " + result.simulations.toLocaleString("ru-RU") + " симуляций";
    $("qWarnings").innerHTML = "";
    (result.warnings || []).forEach(w => { const li=document.createElement("li"); li.textContent=w; $("qWarnings").appendChild(li); });
    $("quarterResult").scrollIntoView({behavior:"smooth",block:"nearest"});
  } catch (err) { alert(err.message || "Не удалось рассчитать четверть"); }
});

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


function parseScore(value){
  if(value==null)return null;
  if(typeof value==="object"){
    const a=Number(value.team1??value.home??value.score1);
    const b=Number(value.team2??value.away??value.score2);
    if(Number.isFinite(a)&&Number.isFinite(b))return [a,b];
    value=JSON.stringify(value);
  }
  const m=String(value).match(/(\d{1,3})\s*[:\-]\s*(\d{1,3})/);
  return m?[Number(m[1]),Number(m[2])]:null;
}
function parsePeriod(value){const m=String(value??"").match(/([1-4])/);return m?Number(m[1]):null;}
function parseClock(value){const m=String(value??"").match(/(\d{1,2}):([0-5]\d)/);return m?m[1].padStart(2,"0")+":"+m[2]:null;}
function showSelectedMatch(ev){
  const panel=$("selectedMatch");panel.hidden=false;
  $("selectedMatchTitle").textContent=(ev.team1||"Команда 1")+" — "+(ev.team2||"Команда 2");
  $("selectedMatchMeta").textContent=[ev.league||ev.sport_name,ev.period,ev.clock].filter(Boolean).join(" · ");
  const raw=ev.raw||{},score=parseScore(ev.score),period=parsePeriod(ev.period??raw.period??raw.periodName),clock=parseClock(ev.clock??raw.time??raw.timer);
  $("selectedMatchInfo").textContent="Счёт: "+(score?score.join(" : "):"нет в фиде")+" · Четверть: "+(period||"не определена")+" · Время: "+(clock||"нет в фиде");
  const candidates=ev.match_total_candidates||[];
  const line=candidates.map(x=>Number(x.pt)).find(Number.isFinite);
  if(score&&period&&clock){
    const total=score[0]+score[1],parts=clock.split(":").map(Number),leftQ=parts[0]*60+parts[1];
    const qMinutes=Number(raw.periodLength||raw.periodDuration||raw.quarterDuration||10)||10;
    const elapsed=(period-1)*qMinutes*60+(qMinutes*60-leftQ);
    if(elapsed>0){
      const remaining=4*qMinutes*60-elapsed,pace=total/(elapsed/60),expected=total+pace*(remaining/60);
      $("matchForecast").innerHTML='<div class="prediction"><div><small>ОЖИДАЕМЫЙ ИТОГ</small><strong>'+expected.toFixed(1)+'</strong></div><div><small>ТЕКУЩИЙ ТЕМП</small><strong>'+pace.toFixed(2)+'</strong><span>очков/мин</span></div></div><p class="muted">'+(Number.isFinite(line)?"Линия PARI: "+line:"Линия общего тотала не найдена в фиде")+'</p><p class="muted">Предварительная оценка по текущему темпу. Вероятность ТБ/ТМ будет рассчитана после подтверждения линии и формата.</p>';
      $("quarterForecast").innerHTML='<p>Текущий счёт: '+total+' · Четверть: '+period+' · Осталось в четверти: '+Math.floor(leftQ/60)+':'+String(leftQ%60).padStart(2,"0")+'</p><p class="muted">Для точного прогноза четверти нужен отдельный счёт четверти и линия PARI на неё. Если этих данных нет в фиде, прогноз не подменяется догадкой.</p>';
    }else{$("matchForecast").textContent="Недостаточно данных времени.";$("quarterForecast").textContent="Недостаточно данных времени.";}
  }else{
    $("matchForecast").textContent="PARI пока не передаёт распознанные счёт, четверть или таймер для этого события. Прогноз не будет выдумываться.";
    $("quarterForecast").textContent="Нет подтверждённых данных текущей четверти и линии. Прогноз появится автоматически, когда фид их передаст.";
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
async function refreshLiveMatches() {
  if (liveRefreshInFlight) return;
  liveRefreshInFlight = true;
  const controller = new AbortController();
  const requestTimeout = setTimeout(() => controller.abort(), 15000);
  const button = $("refreshLiveMatches");
  if (button) button.disabled = true;
  if (liveMessage && !liveList.children.length) liveMessage.textContent = "Запрашиваем events/list у серверов PARI…";
  try {
    const response = await fetch("/api/pari/live", {cache:"no-store", signal:controller.signal});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Источник PARI недоступен");
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
    if (liveMessage) liveMessage.textContent = count
      ? "Получен свежий ответ PARI: " + data.total_events + " событий всего, " + count + " LIVE-матчей по подтверждённым баскетбольным sportId."
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
    if (sourceDetail) sourceDetail.textContent = "Не удалось получить ответ PARI API";
    if (liveBadge) { liveBadge.textContent = "Нет подтверждения LIVE"; liveBadge.className = "pill warn"; }
    if (liveMessage) liveMessage.textContent = "Не удалось получить список матчей: " + (error.message || "ошибка запроса");
    $("feedStatus").textContent = "PARI LIVE: подключение не подтверждено";
  } finally {
    clearTimeout(requestTimeout);
    liveRefreshInFlight = false;
    if (button) button.disabled = false;
    // Schedule the next poll only after this request has finished; never stack requests.
    setTimeout(refreshLiveMatches, 30000);
  }
}
if ($("refreshLiveMatches")) $("refreshLiveMatches").addEventListener("click", refreshLiveMatches);
refreshLiveMatches();
