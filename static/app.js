const $ = id => document.getElementById(id);

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
  if(ev?.score_integrity?.valid===false||ev?.event_freshness_status==="stale_suspected"||ev?.score_clock_freshness==="stale_suspected")return null;
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
  const roots=[ev.raw||{},ev.live_fields||{}];
  const direct=ev.period??ev.external_live?.period??ev.live_period??(ev.raw||{}).period??(ev.raw||{}).periodName;
  const candidates=[direct];
  for(const root of roots)for(const obj of collectEventObjects(root))
    for(const [key,value] of Object.entries(obj))
      if(/^(period|periodNumber|quarter|quarterNumber|gamePart|stageNumber)$/i.test(key))candidates.push(value);
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
  if(ev?.event_freshness_status==="stale_suspected"||ev?.score_clock_freshness==="stale_suspected")return null;
  // Refresh a PARI count-up timer against its source timestamp. The server
  // has already verified period format and period/timer consistency.
  const period=findLivePeriod(ev);
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||
    findPeriodLengthSeconds(ev)||inferPeriodLengthForForecast(ev)?.seconds||0);
  const elapsedRaw=ev?.match_elapsed_seconds??ev?.external_live?.match_elapsed_seconds;
  const elapsedBase=Number(elapsedRaw);
  if(ev?.clock_source==="PARI_timer_and_period_consistency"&&
     Number.isFinite(elapsedBase)&&length>=300&&length<=1200&&period>=1&&period<=4){
    let elapsed=elapsedBase;
    const timestamp=Number(ev?.timer_timestamp_msec||ev?.external_live?.timer_timestamp_msec||0);
    const stateText=String(ev?.live_fields?.st3||ev?.live_fields?.status||ev?.live_fields?.comment||"").toLowerCase();
    const paused=!!ev?.timer_paused||/timeout|break|halftime|перерыв|тайм-аут|таймаут/.test(stateText);
    if(!paused){
      if(!(timestamp>0))return null;
      const age=(Date.now()-timestamp)/1000;
      if(!Number.isFinite(age)||age<0||age>90)return null;
      elapsed+=age;
    }
    const elapsedPeriod=elapsed-(period-1)*length;
    if(elapsedPeriod>=0&&elapsedPeriod<=length+2){
      const remaining=Math.max(0,length-Math.min(length,elapsedPeriod));
      return String(Math.floor(remaining/60)).padStart(2,"0")+":"+String(Math.floor(remaining%60)).padStart(2,"0");
    }
  }
  // ev.clock and external_live.clock are normalized countdowns. Do not
  // interpret PARI live_fields.timer as a countdown: it is a match timer.
  const clockBlocked=ev?.clock_integrity?.valid===false;
  const candidates=clockBlocked?[]:[ev.clock,ev.external_live?.clock,ev.live_clock];
  const roots=clockBlocked?[]:[ev.raw||{},ev.live_fields||{}];
  const allowed=/^(clock|countdown|timeRemaining|remainingTime|periodClock|period_clock|time_remaining|remaining_time|remaining_seconds|clock_seconds|countdown_seconds|currentPeriodTime|periodTime|time_left|timeLeft)$/i;
  for(const root of roots){
    for(const obj of collectEventObjects(root)){
      for(const [key,value] of Object.entries(obj))if(allowed.test(key))candidates.push(value);
    }
  }
  for(const value of candidates){
    if(value==null)continue;
    let candidate=value;
    if(candidate&&typeof candidate==="object"){
      candidate=candidate.remaining??candidate.remainingTime??candidate.seconds??candidate.value??candidate.clock??candidate.time;
    }
    if(candidate==null)continue;
    const text=String(candidate).trim();
    if(/^\d{4}-\d{2}-\d{2}[T ]/.test(text))continue;
    const m=text.match(/^(\d{1,2}):([0-5]\d)$/);
    if(m){
      const seconds=Number(m[1])*60+Number(m[2]);
      if(seconds<=1200)return m[1].padStart(2,"0")+":"+m[2];
      continue;
    }
    if(typeof candidate==="number"||/^\d{1,4}$/.test(text)){
      const seconds=Number(candidate);
      if(Number.isInteger(seconds)&&seconds>=0&&seconds<=1200)
        return String(Math.floor(seconds/60)).padStart(2,"0")+":"+String(seconds%60).padStart(2,"0");
    }
  }
  // Fallback only for server-confirmed cumulative PARI timers.
  if(Number.isFinite(elapsedBase)&&length>=300&&length<=1200&&period>=1&&period<=4){
    const elapsedPeriod=elapsedBase-(period-1)*length;
    if(elapsedPeriod>=0&&elapsedPeriod<=length+2){
      const remaining=Math.max(0,length-Math.min(length,elapsedPeriod));
      return String(Math.floor(remaining/60)).padStart(2,"0")+":"+String(Math.floor(remaining%60)).padStart(2,"0");
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
  return {current:total,projection:Math.round(projection*10)/10,projection_exact:projection,
    low:Math.max(total,Math.round(projection-margin)),high:Math.round(projection+margin),
    sigma:Math.round(sigma*10)/10,elapsed:Math.round(elapsedMinutes*10)/10,
    elapsed_minutes:Math.round(elapsedMinutes*10)/10,
    remaining_minutes:Math.round(remainingSeconds/60*10)/10,
    pace_per_min:Math.round((total/elapsedMinutes)*100)/100,
    expected_remaining_points:Math.round(remainingExpected*10)/10,
    elapsed_seconds:elapsed,remaining_seconds:remainingSeconds,
    period_elapsed_seconds:length-remaining};
}

function dynamicScenarioMonteCarlo(current,mu,lines=[],seedText="",ev=null,period=null,clock=null,kind="match"){
  const n=100000;let seed=2166136261;
  for(const ch of String(seedText))seed=Math.imul(seed^ch.charCodeAt(0),16777619)>>>0;
  const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed+1)/4294967297;};
  const poisson=lambda=>{
    if(lambda<=0)return 0;
    if(lambda>35){const u=Math.max(random(),1e-12),v=random(),z=Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);return Math.max(0,Math.round(lambda+Math.sqrt(lambda)*z));}
    const limit=Math.exp(-lambda);let product=1,k=0;do{k++;product*=random();}while(product>limit&&k<1000);return k-1;
  };
  const info=inferPeriodLengthForForecast(ev||{}),length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||info?.seconds||0);
  const completed=[];
  if(kind==="match"&&length>0)for(let i=0;i<Math.max(0,Number(period||1)-1);i++){const q=readPeriodScore(ev,i);if(Number.isFinite(q)&&q>0)completed.push(q);}
  const score=findLiveScore(ev),pair=period?currentQuarterScore(ev,Number(period),score):null,qCurrent=pair?pair.pair[0]+pair.pair[1]:null;
  const cm=String(clock||"").match(/^([0-9]{1,2}):([0-5][0-9])$/),qElapsed=cm&&length?length-(Number(cm[1])*60+Number(cm[2])):0;
  const rates=[];
  if(completed.length&&length>0){
    const avg=completed.reduce((a,b)=>a+b,0)/completed.length,last=completed[completed.length-1];
    rates.push({value:clampForecast(last/Math.max(avg,1)-1,-0.35,0.35),weight:0.55});
    if(completed.length>=2){const prior=completed.slice(0,-1).reduce((a,b)=>a+b,0)/(completed.length-1);rates.push({value:clampForecast(last/Math.max(prior,1)-1,-0.35,0.35),weight:0.25});}
  }
  if(qCurrent!=null&&qElapsed>=180&&length>0){
    const baseline=completed.length?completed.reduce((a,b)=>a+b,0)/completed.length:(score&&period>1?((score[0]+score[1])/Math.max((Number(period)-1)*length/60,1))*(length/60):null);
    if(baseline>0)rates.push({value:clampForecast((qCurrent/(qElapsed/60))/ (baseline/(length/60))-1,-0.35,0.35),weight:0.20});
  }
  const trend=rates.length?rates.reduce((a,x)=>a+x.value*x.weight,0)/rates.reduce((a,x)=>a+x.weight,0):0;
  const momentum=clampForecast(trend,-0.25,0.25);
  const pFast=clampForecast(0.25+momentum*0.55,0.12,0.40),pSlow=clampForecast(0.25-momentum*0.25,0.16,0.34),pBase=Math.max(0.20,1-pFast-pSlow);
  const norm=pSlow*0.78+pBase+pFast*1.22;
  const scenarios=[{name:"Замедление",factor:0.78,probability:pSlow},{name:"Базовый темп",factor:1,probability:pBase},{name:"Ускорение",factor:1.22,probability:pFast}];
  const outcomes=new Array(n);
  for(let i=0;i<n;i++){const u=random(),sc=u<pSlow?scenarios[0]:(u<pSlow+pBase?scenarios[1]:scenarios[2]);outcomes[i]=Math.max(current,current+poisson(Math.max(0,mu*sc.factor/norm)));}
  outcomes.sort((a,b)=>a-b);
  const mean=outcomes.reduce((sum,value)=>sum+value,0)/n;
  const variance=outcomes.reduce((sum,value)=>sum+(value-mean)*(value-mean),0)/n;
  const stddev=Math.sqrt(variance);
  const quantile=q=>outcomes[Math.min(n-1,Math.max(0,Math.ceil(q*n)-1))];
  const probabilities=(lines||[]).filter(x=>Number.isFinite(Number(x.line))).map(x=>{
    const line=Number(x.line);let over=0,under=0,push=0;
    for(const total of outcomes){if(total>line)over++;else if(total<line)under++;else push++;}
    const pOver=over/n,pUnder=under/n,pPush=push/n,resolved=pOver+pUnder;
    const conditionalOver=resolved>0?pOver/resolved:null;
    return {...x,pOver,pUnder,pPush,conditionalOver,
      direction:conditionalOver==null?"возврат":conditionalOver>=0.57?"ТБ":conditionalOver<=0.43?"ТМ":"без выраженного преимущества",
      edge:conditionalOver==null?0:Math.max(conditionalOver,1-conditionalOver)-0.5,
      simulation_se:Math.sqrt(pOver*(1-pOver)/n)};
  });
  return {simulations:n,mu:Math.round(mu*100)/100,mu_exact:mu,mean:Math.round(mean*10)/10,mean_exact:mean,stddev:Math.round(stddev*10)/10,stddev_exact:stddev,
    low80:quantile(.10),high80:quantile(.90),low95:quantile(.025),high95:quantile(.975),
    probabilities,scenarios:scenarios.map(x=>({name:x.name,probability:Math.round(x.probability*100),multiplier:x.factor/norm})),
    momentum:Math.round(momentum*1000)/10,method:"Смесь сценариев Пуассона · прогнозное распределение, не независимая модель"};
}
function poissonMonteCarlo(current, mu, lines=[], seedText="") {
  const n=100000;
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
    const line=Number(x.line);let over=0,under=0,push=0;
    for(const total of outcomes){if(total>line)over++;else if(total<line)under++;else push++;}
    const pOver=over/n,pUnder=under/n,pPush=push/n,resolved=pOver+pUnder;
    const conditionalOver=resolved>0?pOver/resolved:null;
    return {...x,pOver,pUnder,pPush,conditionalOver,
      direction:conditionalOver==null?"возврат":conditionalOver>=0.57?"ТБ":(conditionalOver<=0.43?"ТМ":"без выраженного преимущества"),
      edge:conditionalOver==null?0:Math.max(conditionalOver,1-conditionalOver)-0.5};
  });
  return {simulations:n,mu:Math.round(mu*100)/100,low80:quantile(.10),high80:quantile(.90),
    low95:quantile(.025),high95:quantile(.975),probabilities:probs};
}
function clampForecast(value,min,max){return Math.max(min,Math.min(max,value));}
function readPeriodScorePair(ev,index){
  const parsePair=row=>{
    if(Array.isArray(row)&&row.length>=2){
      const a=Number(row[0]),b=Number(row[1]);
      if(Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b>=0)return [a,b];
    }
    if(row&&typeof row==="object"){
      const a=Number(row.home??row.homeScore??row.home_score??row.c1??row.team1??row.team1Score??row.team1_score);
      const b=Number(row.away??row.awayScore??row.away_score??row.c2??row.team2??row.team2Score??row.team2_score);
      if(Number.isFinite(a)&&Number.isFinite(b)&&a>=0&&b>=0)return [a,b];
    }
    return null;
  };
  const sources=[ev?.period_scores,ev?.external_live?.period_scores,ev?.live_fields?.period_scores,ev?.raw?.period_scores,ev?.raw?.live_fields?.period_scores];
  for(const scores of sources){
    const row=Array.isArray(scores)?scores[index]:(scores&&typeof scores==="object"?(scores[String(index+1)]??scores[index]):null);
    const pair=parsePair(row);
    if(pair)return pair;
  }
  // PARI's normalized timeline has an explicit period number; missing quarters
  // must not shift the following quarter into the wrong slot.
  const timelines=[ev?.match_timeline,ev?.external_live?.match_timeline,ev?.raw?.match_timeline];
  for(const timeline of timelines){
    if(!Array.isArray(timeline))continue;
    const row=timeline.find(item=>Number(item?.period??item?.quarter??item?.periodNumber)===index+1);
    const pair=parsePair(row);
    if(pair)return pair;
  }
  return null;
}
function readPeriodScore(ev,index){
  const pair=readPeriodScorePair(ev,index);
  return pair?pair[0]+pair[1]:null;
}
function currentQuarterScore(ev,period,matchScore){
  const direct=readPeriodScorePair(ev,period-1);
  if(direct)return {pair:direct,source:"PARI/внешний источник · счёт текущей четверти"};
  if(!Array.isArray(matchScore)||matchScore.length<2)return null;
  // In Q1 the match score is also the current-quarter score. This is exact,
  // not an estimate, provided the event is confirmed to be in period one.
  if(Number(period)===1){
    const home=Number(matchScore[0]),away=Number(matchScore[1]);
    if(Number.isFinite(home)&&Number.isFinite(away)&&home>=0&&away>=0)
      return {pair:[home,away],source:"общий счёт матча · первая четверть"};
    return null;
  }
  if(!Number.isInteger(Number(period))||period<2||period>4)return null;
  let previousHome=0,previousAway=0;
  for(let i=0;i<period-1;i++){
    const pair=readPeriodScorePair(ev,i);
    if(!pair)return null;
    previousHome+=pair[0];previousAway+=pair[1];
  }
  const home=Number(matchScore[0])-previousHome,away=Number(matchScore[1])-previousAway;
  if(!Number.isFinite(home)||!Number.isFinite(away)||home<0||away<0)return null;
  return {pair:[home,away],source:"расчёт из общего счёта минус завершённые четверти"};
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
  const baseMu=Math.max(0,(estimate.projection_exact??estimate.projection)-estimate.current);
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
  const projectionExact=estimate.current+baseMu*multiplier;
  return {estimate:{...estimate,projection:Math.round(projectionExact*10)/10,projection_exact:projectionExact},
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

function advancedMatchCalculations(ev,estimate,period,clock,possession,quarterForecast){
  const out=[],fmt=(x,d=1)=>Number.isFinite(Number(x))?Number(x).toFixed(d):"—";
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||inferPeriodLengthForForecast(ev)?.seconds||0);
  const completed=[];
  for(let i=0;i<Math.max(0,Number(period||1)-1);i++){
    const total=readPeriodScore(ev,i);
    if(Number.isFinite(total))completed.push(total);
  }
  if(completed.length>=2){
    const avg=completed.reduce((a,b)=>a+b,0)/completed.length;
    const variance=completed.reduce((a,b)=>a+(b-avg)**2,0)/completed.length;
    const sd=Math.sqrt(variance),cv=avg>0?sd/avg*100:null;
    out.push({name:"Средний тотал Q",value:fmt(avg),detail:"σ "+fmt(sd)+" · разброс "+fmt(cv,1)+"%"});
    const prior=completed.slice(0,-1),priorAvg=prior.reduce((a,b)=>a+b,0)/prior.length,last=completed[completed.length-1];
    if(priorAvg>0)out.push({name:"Динамика последней Q",value:(last>=priorAvg?"+":"")+fmt((last/priorAvg-1)*100,1)+"%",detail:fmt(last)+" оч. · до этого "+fmt(priorAvg)+" оч./Q"});
  }
  const matchElapsed=Number(estimate?.elapsed_seconds||0),matchCurrent=Number(estimate?.current||0);
  const matchRate=matchElapsed>0?matchCurrent/(matchElapsed/60):null;
  const qMatch=Number(period)>0?currentQuarterScore(ev,Number(period),findLiveScore(ev)):null;
  const dynamics=liveTempoDynamics(ev,estimate,period,clock,"match");
  if(dynamics.applied){
    out.push({name:"Коэффициент ускорения / спада",value:(dynamics.rawChange>=0?"+":"")+fmt(dynamics.rawChange,1)+"%",
      detail:"Текущий темп "+fmt(dynamics.currentRate,2)+" · базовый "+fmt(dynamics.baselineRate,2)+" оч./мин"});
    out.push({name:"Поправка к прогнозу",value:"×"+fmt(dynamics.factor,3),
      detail:"Влияние на оставшийся тотал "+(dynamics.factor>=1?"+":"")+fmt((dynamics.factor-1)*100,1)+"% · уверенность "+dynamics.confidence});
  }else out.push({name:"Ускорение / спад",value:"не рассчитано",detail:dynamics.reason});
  const qClock=String(clock||"").match(/^(\d{1,2}):([0-5]\d)$/);
  const qElapsed=length>0&&qClock?length-(Number(qClock[1])*60+Number(qClock[2])):null;
  const formatUnverified=String(inferPeriodLengthForForecast(ev)?.method||"").includes("предварительно");
  if(qMatch&&qElapsed>=120&&matchRate>0&&!formatUnverified){
    const qRate=(qMatch.pair[0]+qMatch.pair[1])/(qElapsed/60);
    out.push({name:"Ускорение / замедление",value:(qRate>=matchRate?"+":"")+fmt((qRate/matchRate-1)*100,1)+"%",detail:"Q "+fmt(qRate,2)+" · матч "+fmt(matchRate,2)+" оч./мин"});
  }
  if(possession?.applied&&Number.isFinite(Number(possession.projection))&&Number.isFinite(Number(estimate?.projection))){
    const delta=Number(possession.projection)-Number(estimate.projection);
    out.push({name:"Владения vs темп",value:(delta>=0?"+":"")+fmt(delta)+" оч.",detail:"Владения "+fmt(possession.projection)+" · темп "+fmt(estimate.projection)});
  }
  const hp=Number(possession?.homePointsPerPossession),ap=Number(possession?.awayPointsPerPossession);
  if(possession?.applied&&Number.isFinite(hp)&&Number.isFinite(ap))
    out.push({name:"Разница PPP",value:(hp>=ap?"+":"")+fmt(hp-ap,3),detail:"Команда 1 "+fmt(hp,3)+" · команда 2 "+fmt(ap,3)});
  if(quarterForecast?.ready&&Number.isFinite(Number(quarterForecast.projection))&&Number.isFinite(Number(quarterForecast.current)))
    out.push({name:"Прогноз текущей Q",value:fmt(quarterForecast.projection)+" оч.",detail:"Сейчас "+fmt(quarterForecast.current,0)+" · предиктивный интервал 80% "+fmt(quarterForecast.low80,0)+"–"+fmt(quarterForecast.high80,0)});
  const score=findLiveScore(ev);
  if(score)out.push({name:"Разница счёта",value:(score[0]>=score[1]?"+":"")+fmt(score[0]-score[1],0),detail:"Команда 1 "+fmt(score[0],0)+" · команда 2 "+fmt(score[1],0)});
  return out;
}

function possessionModelContext(ev,estimate,period,clock){
  const rows=Array.isArray(ev?.external_statistics)?ev.external_statistics:[];
  const aliases={
    fga:["field_goals_attempted","field_goal_attempts","field_goals_attempts","field_goal_attempted","fga","fg_attempted","fg_attempts","field_goals"],
    fgm:["field_goals_made","fgm","field_goals"],
    oreb:["offensive_rebounds","offensive_rebound","offensive_rebound_count","oreb"],
    dreb:["defensive_rebounds","defensive_rebound","dreb"],rebounds:["rebounds","total_rebounds"],
    tov:["turnovers","turnover","lost_balls","turnover_count"],
    fta:["free_throws_attempted","free_throw_attempts","free_throw_attempts_total","free_throw_attempted","fta","free_throws"],
    ftm:["free_throws_made","ftm","free_throws"],
    poss:["possessions","possession","team_possessions","total_possessions","ball_possessions","possession_count","team_possession","possessions_total","total_possession"],
    twoMade:["two_pointers_made","2pt_made","two_pointers"],twoAtt:["two_pointers_attempted","2pt_attempted","two_point_attempts"],
    threeMade:["three_pointers_made","3pt_made","three_pointers"],threeAtt:["three_pointers_attempted","3pt_attempted","three_point_attempts"],
    assists:["assists","assist"],steals:["steals","steal"],blocks:["blocks","block"],fouls:["personal_fouls","team_fouls","fouls","foul"]
  };
  const normalizeKey=value=>String(value||"").toLowerCase().replace(/ё/g,"е").replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"").replace(/_+/g,"_");
  const rowFor=keys=>{
    const matches=rows.filter(x=>keys.includes(normalizeKey(x?.metric_key||x?.name)));
    matches.sort((a,b)=>(String(a?.source||"").toUpperCase()==="PARI"?0:1)-(String(b?.source||"").toUpperCase()==="PARI"?0:1));
    return matches[0]||null;
  };
  const read=(side,keys,part="value")=>{
    const row=rowFor(keys);if(!row)return null;
    const value=row[side];
    if(value&&typeof value==="object"){
      const attempt=value.attempts??value.attempted??value.attemptsCount??value.attempt_count;
      if(part==="attempts"&&attempt!=null&&Number.isFinite(Number(attempt)))return Number(attempt);
      const made=value.made??value.scored??value.current??value.value??value.total??value.count??value.displayValue??value.display_value;
      if(made!=null){const parsed=parseStatCount(made);if(parsed)return parsed.value;const n=Number(made);if(Number.isFinite(n))return n;}
    }
    const parsed=parseStatCount(value);if(!parsed)return null;
    if(part==="attempts")return parsed.attempts!=null&&Number.isFinite(parsed.attempts)?parsed.attempts:null;
    return Number.isFinite(parsed.value)?parsed.value:null;
  };
  const sides={home:{},away:{}};
  for(const side of ["home","away"]){
    for(const [key,names] of Object.entries(aliases)){
      const isAttempt=["fga","fta","twoAtt","threeAtt"].includes(key);
      const composite=["fga","fta","fgm","ftm","twoMade","twoAtt","threeMade","threeAtt"].includes(key);
      sides[side][key]=read(side,names,composite?(isAttempt?"attempts":"value"):"value");
    }
    if(sides[side].fga==null&&sides[side].twoAtt!=null&&sides[side].threeAtt!=null)sides[side].fga=sides[side].twoAtt+sides[side].threeAtt;
    if(sides[side].fgm==null&&sides[side].twoMade!=null&&sides[side].threeMade!=null)sides[side].fgm=sides[side].twoMade+sides[side].threeMade;
  }
  const derivedAvailable=["home","away"].every(side=>["fga","oreb","tov","fta"].every(k=>Number.isFinite(sides[side][k])));
  const derived=derivedAvailable?{home:sides.home.fga-sides.home.oreb+sides.home.tov+0.44*sides.home.fta,away:sides.away.fga-sides.away.oreb+sides.away.tov+0.44*sides.away.fta}:null;
  const direct={home:sides.home.poss,away:sides.away.poss};
  let home=null,away=null,method="",qualityNotes=[];
  if(direct.home>0&&direct.away>0){
    home=direct.home;away=direct.away;method="фактические владения PARI/box-score";
    if(derived){
      const gap=Math.max(Math.abs(home-derived.home)/Math.max(derived.home,1),Math.abs(away-derived.away)/Math.max(derived.away,1));
      if(gap>0.30)return {applied:false,reason:"прямые владения расходятся с расчётом по броскам более чем на 30% — данные отправлены на проверку",conflict_percent:Math.round(gap*100)};
      if(gap>0.15)qualityNotes.push("есть расхождение прямых и расчётных владений "+Math.round(gap*100)+"%");
    }
  }else if(derived){
    home=derived.home;away=derived.away;method="расчёт: FGA − OREB + потери + 0,44×FTA";
    qualityNotes.push("прямой показатель владений не получен; использована формула по box-score");
  }else return {applied:false,reason:"не хватает парных данных: владения либо FGA, подборы в нападении, потери и FTA обеих команд"};
  if(!(Number.isFinite(home)&&Number.isFinite(away)&&home>0&&away>0))return {applied:false,reason:"некорректные значения владений"};
  const imbalance=Math.abs(home-away)/Math.max(home,away);
  if(imbalance>0.30)return {applied:false,reason:"разница владений команд выше 30% — проверка качества не пройдена"};
  const score=findLiveScore(ev);if(!score)return {applied:false,reason:"нет подтверждённого счёта"};
  const homePoints=Number(score[0]),awayPoints=Number(score[1]),current=homePoints+awayPoints,possessions=(home+away)/2;
  if(possessions<2||possessions>180)return {applied:false,reason:"значение владений вне допустимого диапазона"};
  const info=inferPeriodLengthForForecast(ev);
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||info?.seconds||0);
  const count=Number(ev?.external_live?.period_count||info?.periods||4);
  const totalSeconds=length*count,elapsed=Number(estimate?.elapsed_seconds||0);
  if(!length||!elapsed||elapsed>=totalSeconds)return {applied:false,reason:"не хватает подтверждённого времени матча"};
  const elapsedMinutes=elapsed/60,remaining=totalSeconds-elapsed,possPerMinute=possessions/elapsedMinutes,pointsPerPossession=current/possessions;
  if(possPerMinute<0.15||possPerMinute>2.25)return {applied:false,reason:"темп владений неправдоподобен для прошедшего времени"};
  if(pointsPerPossession<0.45||pointsPerPossession>2.25)return {applied:false,reason:"эффективность очков на владение не прошла проверку"};
  const projected=current+possPerMinute*(remaining/60)*pointsPerPossession;
  const teamPppHome=homePoints/home,teamPppAway=awayPoints/away;
  const efg=side=>{const x=sides[side],fga=x.fga,fgm=x.fgm??((x.twoMade??0)+(x.threeMade??0)),tpm=x.threeMade;return fga>0&&Number.isFinite(fgm)&&Number.isFinite(tpm)?(fgm+0.5*tpm)/fga:null;};
  const homeEfg=efg("home"),awayEfg=efg("away");
  const metricCoverage=["fga","fgm","oreb","dreb","rebounds","tov","fta","ftm","poss","twoMade","twoAtt","threeMade","threeAtt","assists","steals","blocks","fouls"].filter(k=>sides.home[k]!=null&&sides.away[k]!=null).length;
  const metrics={
    home_ppp:Math.round(teamPppHome*1000)/1000,away_ppp:Math.round(teamPppAway*1000)/1000,
    home_efg:homeEfg==null?null:Math.round(homeEfg*1000)/1000,away_efg:awayEfg==null?null:Math.round(awayEfg*1000)/1000,
    turnover_rate_home:sides.home.tov==null?null:Math.round(sides.home.tov/home*1000)/10,turnover_rate_away:sides.away.tov==null?null:Math.round(sides.away.tov/away*1000)/10,
    offensive_rebounds_home:sides.home.oreb,offensive_rebounds_away:sides.away.oreb,
    rebounds_home:sides.home.rebounds,rebounds_away:sides.away.rebounds,
    free_throw_rate_home:sides.home.fga>0&&sides.home.fta!=null?Math.round(sides.home.fta/sides.home.fga*1000)/10:null,
    free_throw_rate_away:sides.away.fga>0&&sides.away.fta!=null?Math.round(sides.away.fta/sides.away.fga*1000)/10:null,
    three_point_rate_home:sides.home.fga>0&&sides.home.threeAtt!=null?Math.round(sides.home.threeAtt/sides.home.fga*1000)/10:null,
    three_point_rate_away:sides.away.fga>0&&sides.away.threeAtt!=null?Math.round(sides.away.threeAtt/sides.away.fga*1000)/10:null,
    assists_home:sides.home.assists,assists_away:sides.away.assists,fouls_home:sides.home.fouls,fouls_away:sides.away.fouls,
    steals_home:sides.home.steals,steals_away:sides.away.steals,blocks_home:sides.home.blocks,blocks_away:sides.away.blocks
  };
  const baseline=Math.max(1,Number(estimate?.projection_exact??estimate?.projection)-current),confidence=qualityNotes.length?"средняя":(direct.home>0&&direct.away>0&&metricCoverage>=8?"высокая":metricCoverage>=5?"средняя":"ограниченная");
  return {applied:true,model:"Possession Engine v2",method,confidence,qualityNotes,metrics,metric_coverage:metricCoverage,
    home:Math.round(home*10)/10,away:Math.round(away*10)/10,average:Math.round(possessions*10)/10,imbalance_percent:Math.round(imbalance*100),
    pointsPerPossession:Math.round(pointsPerPossession*1000)/1000,homePointsPerPossession:Math.round(teamPppHome*1000)/1000,awayPointsPerPossession:Math.round(teamPppAway*1000)/1000,
    possPerMinute:Math.round(possPerMinute*100)/100,expectedRemainingPossessions:Math.round(possPerMinute*(remaining/60)*10)/10,
    expectedRemainingPoints:Math.round((projected-current)*10)/10,projection:Math.round(projected*10)/10,projection_exact:projected,remaining_minutes:Math.round(remaining/60*100)/100,
    adjustment:clampForecast((projected-current)/baseline,0.90,1.10)};
}
function liveTempoDynamics(ev,estimate,period,clock,kind="match"){
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||inferPeriodLengthForForecast(ev||{})?.seconds||0);
  const p=Math.max(1,Number(period)||1),completed=[];
  if(length>0){
    for(let i=0;i<p-1;i++){const q=readPeriodScore(ev,i);if(Number.isFinite(q)&&q>0)completed.push(q);}
  }
  const cm=String(clock||"").match(/^([0-9]{1,2}):([0-5][0-9])$/);
  const elapsed=cm&&length?Math.max(0,length-(Number(cm[1])*60+Number(cm[2]))):0;
  const currentPair=currentQuarterScore(ev,p,findLiveScore(ev));
  const currentQ=currentPair?.pair?.reduce((a,b)=>a+b,0);
  if(!length||elapsed<180||!Number.isFinite(currentQ)||!completed.length)
    return {applied:false,factor:1,acceleration:0,decline:0,confidence:"недостаточно данных",reason:"нужны минимум 3 минуты текущей четверти и завершённая четверть"};
  const qMinutes=length/60;
  const baseline=completed.reduce((a,b)=>a+b,0)/completed.length/qMinutes;
  if(!(baseline>0))return {applied:false,factor:1,acceleration:0,decline:0,confidence:"недостаточно данных",reason:"нет базового темпа"};
  const currentRate=currentQ/(elapsed/60);
  const rawChange=currentRate/baseline-1;
  const acceleration=Math.max(0,rawChange*100);
  const decline=Math.max(0,-rawChange*100);
  const last=completed[completed.length-1];
  const earlier=completed.slice(0,-1);
  const earlierAvg=earlier.length?earlier.reduce((a,b)=>a+b,0)/earlier.length:last;
  const priorTrend=earlier.length?Math.max(-0.20,Math.min(0.20,last/Math.max(earlierAvg,1)-1)):0;
  const liveTrend=Math.max(-0.30,Math.min(0.30,rawChange));
  const combined=liveTrend*0.75+priorTrend*0.25;
  const factor=Math.max(0.94,Math.min(1.06,1+combined*0.12));
  return {applied:true,factor,acceleration,decline,currentRate,baselineRate:baseline,
    lastQuarter:last,priorTrend:priorTrend*100,rawChange:rawChange*100,
    confidence:elapsed>=300&&completed.length>=2?"средняя":"ограниченная",
    reason:"темп текущей Q сравнен со средним завершённых Q; тренд прошлой Q учтён с весом 25%"};
}
function applyPoissonModel(estimate,lines,seedText,ev=null,period=null,clock=null){
  if(!estimate||!Number.isFinite(estimate.projection)||!Number.isFinite(estimate.current))return estimate;
  const context=estimate.source==="period_range"?{estimate,factors:[],adjustment:1}:applyGameContext(estimate,ev||{},period,clock);
  const adjusted=context.estimate||estimate;
  const formatInfo=inferPeriodLengthForForecast(ev||{});
  const formatUnverified=String(formatInfo?.method||"").includes("предварительно");
  const possession=estimate.source==="period_range"?{applied:false,reason:"нет точного таймера"}:
    (formatUnverified?{applied:false,reason:"длительность четверти не подтверждена"}:possessionModelContext(ev||{},adjusted,period,clock));
  const statsContext=liveShootingContext(ev||{},adjusted);
  // Possession pace × points per possession is the lead forecast.
  // Other signals are secondary and damped to avoid double-counting.
  const baseMu=Math.max(0,(adjusted.projection_exact??adjusted.projection)-adjusted.current);
  const possMu=possession.applied?Math.max(0,(possession.projection_exact??possession.projection)-adjusted.current):baseMu;
  const secondaryAdjustment=possession.applied?clampForecast(1+(statsContext.adjustment-1)*0.35,0.985,1.015):statsContext.adjustment;
  const tempoDynamics=liveTempoDynamics(ev||{},adjusted,period,clock,"match");
  const tempoFactor=tempoDynamics.applied?tempoDynamics.factor:1;
  const mu=possMu*secondaryAdjustment*tempoFactor,mc=dynamicScenarioMonteCarlo(adjusted.current,mu,lines,seedText,ev,period,clock,"match");
  const factors=[...(context.factors||[]),...(statsContext.factors||[])];
  if(tempoDynamics.applied)factors.push("Динамика темпа: "+(tempoDynamics.acceleration>0?"ускорение ":"спад ")+Math.abs(tempoDynamics.rawChange).toFixed(1)+"% · коэффициент "+tempoFactor.toFixed(3)+" · влияние на оставшийся тотал "+((tempoFactor-1)*100).toFixed(1)+"%");
  else factors.push("Динамика темпа: коэффициент не применён — "+tempoDynamics.reason);
  const scenarioText=(mc?.scenarios||[]).map(x=>x.name+" "+x.probability+"%").join(" · ");
  if(scenarioText)factors.push("100 000 динамических сценариев: "+scenarioText+" · импульс "+Number(mc.momentum||0).toFixed(1)+"%");
  if(possession.applied){
    factors.unshift("ведущая модель владений: "+possession.average+" владений на команду · "+possession.possPerMinute+" влад./мин · "+possession.pointsPerPossession+" очка/владение");
    factors.push("ожидаемые владения до конца: "+possession.expectedRemainingPossessions+" · очки до конца по модели владений: "+possession.expectedRemainingPoints);
    if(possession.qualityNotes?.length)factors.push(...possession.qualityNotes);
  }
  return {...adjusted,context_factors:factors,context_adjustment:context.adjustment,statistics_adjustment:secondaryAdjustment,
    statistics_quality:statsContext.quality,shooting_efg:statsContext.efg??null,possession_model:possession,
    poisson:mc,tempo_dynamics:tempoDynamics,low:mc.low80,high:mc.high80,
    sigma:mc.stddev_exact,projection:mc.mean,projection_exact:mc.mean_exact,expected_remaining_exact:mu,distribution_method:mc.method};
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
function calculationStepsHtml(options){
  const {kind="match",projection,current,elapsedMinutes,remainingMinutes,homeScore,awayScore,poss,lines=[],preliminary=false,clock=null,period=null}=options||{};
  if(!Number.isFinite(Number(current))||!Number.isFinite(Number(projection)))return "";
  const n=x=>Number.isFinite(Number(x))?Number(x):null, f=(x,d=1)=>x==null?"—":Number(x).toFixed(d);
  const now=n(current),final=n(projection),rest=Math.max(0,final-now),elapsed=n(elapsedMinutes),remaining=n(remainingMinutes);
  const home=n(homeScore),away=n(awayScore),active=!!poss?.applied;
  let hw=.5,aw=.5;
  if(active&&n(poss.homePointsPerPossession)!=null&&n(poss.awayPointsPerPossession)!=null&&poss.homePointsPerPossession+poss.awayPointsPerPossession>0){hw=poss.homePointsPerPossession/(poss.homePointsPerPossession+poss.awayPointsPerPossession);aw=1-hw;}
  else if(home!=null&&away!=null&&home+away>0){hw=home/(home+away);aw=1-hw;}
  const projectedTotal=Math.round(final);
  const finalHome=home==null?null:Math.round(home+rest*hw);
  const finalAway=away==null?null:Math.max(0,projectedTotal-finalHome);
  const strongest=(lines||[]).filter(x=>x&&Number.isFinite(Number(x.pOver))&&Number.isFinite(Number(x.line))).slice().sort((a,b)=>Math.abs(b.pOver-.5)-Math.abs(a.pOver-.5))[0];
  const scoreLabel=kind==="quarter"?"Счёт текущей четверти":"Текущий счёт матча";
  const stages=[{name:scoreLabel,value:home!=null&&away!=null?f(home,0)+" : "+f(away,0):f(now,0),detail:(kind==="quarter"?"Очки только в Q"+(period||"—")+" · ":"")+f(elapsed)+" мин прошло · "+f(remaining)+" мин осталось"+(clock?" · таймер "+clock:"")}];
  if(active){
    stages.push({name:"Владения",value:f(poss.possPerMinute,2)+" / мин",detail:"Среднее "+f(poss.average,1)+" · PPP "+f(poss.pointsPerPossession,3)});
    stages.push({name:"Остаток",value:f(poss.expectedRemainingPossessions,1)+" влад.",detail:f(poss.expectedRemainingPossessions,1)+" × "+f(poss.pointsPerPossession,3)+" = "+f(poss.expectedRemainingPoints,1)+" оч."});
  }else{
    const rate=elapsed>0?now/elapsed:null;
    stages.push({name:"Темп",value:f(rate,2)+" оч./мин",detail:f(now,0)+" ÷ "+f(elapsed,1)});
    stages.push({name:"Остаток",value:f(rest,1)+" оч.",detail:f(rate,2)+" × "+f(remaining,1)+" мин"});
  }
  stages.push({name:"Итог",value:f(final,1)+" оч.",detail:f(now,0)+" + "+f(rest,1)+(preliminary?" · предварительно":"")});
  const trajectory=[25,50,75,100].map(percent=>{const part=rest*percent/100,total=now+part,h=home==null?null:Math.round(home+part*hw),a=away==null?null:Math.max(0,Math.round(total)-h);return {percent,total,h,a};});
  const rows=trajectory.map(x=>'<tr><td>'+x.percent+'% остатка</td><td>'+f(x.total,0)+' оч.</td><td>'+(x.h==null?"—":x.h+' : '+x.a)+'</td></tr>').join("");
  return '<section class="calculation-steps"><div class="calc-section-head"><div><span class="eyebrow">'+(kind==="quarter"?"Q"+(period||""):"FULL GAME")+'</span><h3>Этапы расчёта</h3></div><span class="calc-live-tag">'+(preliminary?"ПРЕДВАРИТЕЛЬНО":"LIVE")+'</span></div>'+
    '<div class="calc-stage-list">'+stages.map((x,i)=>'<div class="calc-stage"><span class="calc-stage-index">'+(i+1)+'</span><div><b>'+x.name+'</b><strong>'+escapeHtml(x.value)+'</strong><small>'+escapeHtml(x.detail)+'</small></div></div>').join("")+'</div>'+
    (home!=null&&away!=null?'<div class="calc-score-prediction"><span>'+(kind==="quarter"?"Ориентир очков в Q"+(period||"—"):"Ориентир итогового счёта матча")+'</span><b>'+finalHome+' : '+finalAway+'</b><small>Распределение общего прогноза по текущей доле очков команд; это не отдельная модель индивидуальных тоталов.</small></div>':'')+
    '<h4>Как меняется общий тотал</h4><table class="calc-table trajectory-table"><thead><tr><th>Остаток</th><th>Прогноз тотала</th><th>Ориентир по командам</th></tr></thead><tbody>'+rows+'</tbody></table>'+
    '<div class="calc-direction"><span>Направление '+(kind==="quarter"?"четверти Q"+(period||"—"):"матча")+'</span><b>'+escapeHtml(strongest?(strongest.direction==="без преимущества"?"Нет перевеса":strongest.direction):"Линия PARI не получена")+'</b>'+(strongest?'<small>Линия '+f(strongest.line,1)+' · вероятность ТБ '+(strongest.pOver*100).toFixed(1)+'% · ТМ '+(strongest.pUnder*100).toFixed(1)+'%</small>':'<small>Без подтверждённой линии нельзя корректно выбрать ТБ или ТМ</small>')+'</div></section>';
}

const ANALYSIS_HISTORY_KEY = "pari-analysis-history-v1";
function readAnalysisHistory(){
  try { const value=JSON.parse(localStorage.getItem(ANALYSIS_HISTORY_KEY)||"[]"); return Array.isArray(value)?value.filter(x=>x&&typeof x==="object"):[]; }
  catch (_) { return []; }
}
function renderAnalysisHistory(){
  const rows=readAnalysisHistory(),count=$("historyCount"),list=$("historyList");
  if(count)count.textContent=String(rows.length);
  if(!list)return;
  if(!rows.length){list.innerHTML='<p class="muted">Пока нет сохранённых расчётов.</p>';return;}
  list.innerHTML=rows.slice().reverse().map(row=>{
    const when=row.savedAt?new Date(row.savedAt).toLocaleString("ru-RU"):"";
    const score=row.score?row.score.join(" : "):"счёт —";
    const projection=row.matchProjection!=null?"Матч "+row.matchProjection:(row.quarterProjection!=null?"Q"+row.period+" "+row.quarterProjection:"Прогноз —");
    const signal=row.direction?row.direction+(row.line!=null?" "+row.line:"")+(row.probability!=null?" · "+row.probability+"%":""):"без линии";
    return '<div class="history-row"><b>'+escapeHtml(row.teams||"Матч")+'</b><span>'+escapeHtml(when+" · "+score+" · Q"+(row.period||"—")+" "+(row.clock||"—"))+'</span><span>'+escapeHtml(projection)+'</span><span>'+escapeHtml(signal)+'</span></div>';
  }).join("");
}
function saveAnalysisHistory(ev,score,period,clock,estimate,quarter){
  if(!estimate&&!quarter?.ready)return;
  const probabilities=estimate?.poisson?.probabilities||[];
  const strongest=probabilities.filter(x=>x&&Number.isFinite(Number(x.pOver))).sort((a,b)=>Math.abs(Number(b.pOver)-0.5)-Math.abs(Number(a.pOver)-0.5))[0];
  const savedAt=Date.now();
  const item={snapshotId:String(ev?.id||"event")+"|"+savedAt+"|"+Math.random().toString(36).slice(2),
    eventId:String(ev?.id||""),teams:String((ev?.team1||"Команда 1")+" — "+(ev?.team2||"Команда 2")),
    league:ev?.league||null,score:Array.isArray(score)?score.map(Number):null,period:period||null,clock:clock||null,
    target:estimate?"match":(quarter?.ready?"quarter":"unknown"),overtimeIncluded:false,
    dataTimestamp:ev?.source_timestamp_utc||ev?.updated_at||ev?.data_timestamp_utc||null,
    matchProjection:estimate?.projection_exact??estimate?.projection??null,matchExpectedRemaining:estimate?.expected_remaining_exact??estimate?.poisson?.mu??null,
    matchDistribution:estimate?.poisson?{method:estimate.poisson.method||"Poisson scenario simulation",simulations:estimate.poisson.simulations,
      mean:estimate.poisson.mean_exact??estimate.projection_exact??estimate.projection,standardDeviation:estimate.poisson.stddev_exact??estimate.sigma,low80:estimate.poisson.low80,high80:estimate.poisson.high80,low95:estimate.poisson.low95,high95:estimate.poisson.high95,
      probabilities:estimate.poisson.probabilities}:null,
    quarterProjection:quarter?.ready?quarter.projection:null,
    quarterDistribution:quarter?.ready?{model:quarter.distribution,simulations:quarter.simulations,
      low80:quarter.low80,high80:quarter.high80,low95:quarter.low95,high95:quarter.high95,
      probabilities:quarter.lines,posterior:quarter.posterior,prior:quarter.prior}:null,
    line:strongest?.line??null,direction:strongest?.direction??null,
    probability:strongest?Math.round(Math.max(strongest.pOver,strongest.pUnder)*1000)/10:null,
    outcomeStatus:"unresolved",settlementStatus:"unresolved",finalScore:null,
    dataQuality:ev?.statistics_quality||null,scoreSource:ev?.score_source||null,clockSource:ev?.clock_source||null,
    modelVersion:quarter?.model_version||"match-live-v1",savedAt};
  const history=readAnalysisHistory();
  history.push(item);
  try{localStorage.setItem(ANALYSIS_HISTORY_KEY,JSON.stringify(history));}catch(_){}
  renderAnalysisHistory();
}
function clearAnalysisHistory(){
  try{localStorage.removeItem(ANALYSIS_HISTORY_KEY);}catch(_){}
  renderAnalysisHistory();
}
let selectedLiveEventId = null;
function showSelectedMatch(ev,options={}){
  selectedLiveEventId=String(ev?.id||"");
  const panel=$("selectedMatch");panel.hidden=false;
  $("selectedMatchTitle").textContent=(ev.team1||"Команда 1")+" — "+(ev.team2||"Команда 2");
  const score=findLiveScore(ev),period=findLivePeriod(ev),clock=findLiveClock(ev);
  const matchForecast=automaticForecast(ev);
  const estimate=matchForecast.ready&&matchForecast.kind!=="market_reference"?matchForecast.estimate:null;
  const lines=getMatchTotalLines(ev).filter(x=>!x.stale);
  const quarter=automaticQuarterForecast(ev);
  const aiAnalysis=aiModelAnalysis(ev,matchForecast,quarter);
  const classicMatchModels=classicBasketballModelAnalysis(ev,matchForecast,quarter,"match");
  const classicQuarterModels=classicBasketballModelAnalysis(ev,matchForecast,quarter,"quarter");
  const rows=Array.isArray(ev.external_statistics)?ev.external_statistics:[];
  const statsQuality=ev.statistics_quality||null;
  const possession=estimate?.possession_model||possessionModelContext(ev,estimate||{},period,clock);
  const shooting=liveShootingContext(ev,{elapsed_seconds:estimate?.elapsed_seconds||1});
  const modelBadge=$("possessionModelStatus");
  if(modelBadge){modelBadge.textContent=possession.applied?"АКТИВНА":"НЕТ ДАННЫХ";modelBadge.className=possession.applied?"model-active":"muted";}
  const norm=k=>String(k||"").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"");
  const stat=(keys)=>{
    const row=rows.find(x=>keys.map(norm).includes(norm(x?.metric_key||x?.name)));
    if(!row)return null;
    const read=v=>{if(v&&typeof v==="object")v=v.current??v.value??v.total??v.count??v.displayValue??v.display_value;const p=parseStatCount(v);return p?(p.attempts!=null?p.value+"/"+p.attempts:p.value):null;};
    const home=read(row.home),away=read(row.away);
    return home!=null&&away!=null?{home,away,name:row.name||keys[0]}:null;
  };
  const statGroups=[
    ["Владения",["possessions","possession","team_possessions","total_possessions","ball_possessions","possession_count"]],
    ["Броски",["field_goals","field_goals_made","fgm"]],["Попытки",["field_goals_attempted","field_goal_attempts","fga"]],
    ["2 очка",["two_pointers","two_pointers_made","2pt_made"]],["2 очка · попытки",["two_pointers_attempted","2pt_attempted"]],
    ["3 очка",["three_pointers","three_pointers_made","3pt_made"]],["3 очка · попытки",["three_pointers_attempted","3pt_attempted"]],
    ["Штрафные",["free_throws","free_throws_made","ftm"]],["Штрафные · попытки",["free_throws_attempted","free_throw_attempts","fta"]],
    ["Подборы",["rebounds","total_rebounds"]],["Подборы в нападении",["offensive_rebounds","oreb"]],
    ["Подборы в защите",["defensive_rebounds","dreb"]],["Потери",["turnovers","turnover","lost_balls"]],
    ["Фолы",["fouls","personal_fouls","team_fouls"]],["Передачи",["assists"]],["Перехваты",["steals"]],["Блоки",["blocks"]]
  ].map(([label,keys])=>({label,data:stat(keys)})).filter(x=>x.data);
  const timeline=Array.isArray(ev.period_scores)?ev.period_scores.map((p,i)=>"Q"+(i+1)+" "+(p?.[0]??"—")+":"+ (p?.[1]??"—")).join(" · "):"";
  const statHtml=statGroups.length?'<div class="calc-grid">'+statGroups.map(x=>'<div class="calc-card"><span>'+escapeHtml(x.label)+'</span><b>'+x.data.home+' : '+x.data.away+'</b></div>').join("")+'</div>':'<p class="muted">Нет подтверждённой статистики</p>';
  const periodStatRows=Array.isArray(ev.period_statistics)?ev.period_statistics:[];
  const periodStatHtml=periodStatRows.length?'<h4>Статистика по четвертям</h4>'+periodStatRows.map(st=>'<p>Q'+escapeHtml(st.period||"—")+' · '+escapeHtml(st.name||st.metric_key||"Показатель")+': <b>'+escapeHtml(st.home??"—")+' : '+escapeHtml(st.away??"—")+'</b></p>').join(""):'';
  $("selectedMatchMeta").textContent=[ev.league||ev.sport_name].filter(Boolean).join(" · ");
  $("selectedMatchInfo").textContent="Счёт "+(score?score.join(" : "):"—")+" · Q"+(period||"—")+" · "+(clock||"—")+
    " · снимок "+(ev.feed_fetched_at_utc?new Date(ev.feed_fetched_at_utc).toLocaleTimeString("ru-RU"):"время снимка неизвестно")+
    " · свежесть события "+(ev.event_freshness_status==="stale_suspected"?"таймер/счёт не менялись ≥120 с":ev.event_freshness_status==="provider_timestamp_available"?"есть отметка источника":"не подтверждена источником");
  const lineRows=lines.map(x=>{
    const p=(estimate?.poisson?.probabilities||[]).find(z=>Number(z.line)===Number(x.line));
    return '<tr><td>'+x.line+'</td><td>'+(p?(p.pOver*100).toFixed(1)+'%':'—')+'</td><td>'+(p?(p.pUnder*100).toFixed(1)+'%':'—')+'</td><td>'+(p?(p.pPush*100).toFixed(1)+'%':'—')+'</td><td>'+(p?escapeHtml(p.direction):'—')+'</td><td>'+(x.prices?.length?escapeHtml(x.prices.map(v=>"f"+v.factorId+": "+v.price).join(" · ")):(x.odds?escapeHtml(x.odds):"—"))+'</td></tr>';
  }).join("");
  $("matchForecast").innerHTML=estimate
    ? '<div class="forecast-main"><b>Единый основной прогноз · матч</b><div class="forecast-number">'+Number(estimate.projection).toFixed(1)+'</div><p>Сейчас '+estimate.current+' · прошло '+estimate.elapsed_minutes+' мин · осталось '+estimate.remaining_minutes+' мин · '+escapeHtml(estimate.confidence||"уверенность не определена")+'</p><p>Данные: '+escapeHtml(estimate.data_quality_mode||"режим качества не определён")+' · вероятность не калибрована</p><p>ОТ в прогнозе не включён; правило ОТ в линии PARI не подтверждено источником.</p><p>Предиктивный интервал итогового тотала · 80%: '+(estimate.poisson?.low80??estimate.low)+'–'+(estimate.poisson?.high80??estimate.high)+' · 95%: '+(estimate.poisson?.low95??"—")+'–'+(estimate.poisson?.high95??"—")+'</p></div>'+
      '<h3>Показатели</h3><div class="calc-grid"><div class="calc-card"><span>Очков/мин</span><b>'+estimate.pace_per_min+'</b></div><div class="calc-card"><span>Остаток, оч.</span><b>'+(estimate.poisson?.mu??(estimate.projection-estimate.current))+'</b></div><div class="calc-card"><span>σ итогового распределения</span><b>'+(estimate.sigma??"—")+'</b></div><div class="calc-card"><span>Поправка</span><b>'+((Number(estimate.context_adjustment||1)-1)*100).toFixed(1)+'%</b></div></div>'+
      '<h3>Вероятности · PARI</h3>'+(lineRows?'<table class="calc-table"><thead><tr><th>Линия</th><th>ТБ</th><th>ТМ</th><th>Возврат</th><th>Направление</th><th>Цены PARI · сторона/свежесть не подтверждены</th></tr></thead><tbody>'+lineRows+'</tbody></table>':'<p class="muted">Линия не получена</p>')+
      '<h3>Владения</h3>'+(possession.applied?'<div class="calc-grid"><div class="calc-card"><span>Команда 1</span><b>'+possession.home+'</b></div><div class="calc-card"><span>Команда 2</span><b>'+possession.away+'</b></div><div class="calc-card"><span>Среднее</span><b>'+possession.average+'</b></div><div class="calc-card"><span>Влад./мин</span><b>'+possession.possPerMinute+'</b></div><div class="calc-card"><span>PPP</span><b>'+possession.pointsPerPossession+'</b></div><div class="calc-card"><span>Разница</span><b>'+possession.imbalance_percent+'%</b></div><div class="calc-card"><span>Покрытие</span><b>'+possession.metric_coverage+'/17</b></div></div>':'<p class="muted">Нет данных владений</p>')+
      '<p>eFG '+(shooting.efg!=null?(shooting.efg*100).toFixed(1)+'%':'—')+' · поправка '+((shooting.adjustment-1)*100).toFixed(1)+'%</p>'+
      (advancedMatchCalculations(ev,estimate,period,clock,possession,quarter).length?'<h3>Дополнительные расчёты</h3><div class="calc-grid">'+advancedMatchCalculations(ev,estimate,period,clock,possession,quarter).map(x=>'<div class="calc-card"><span>'+escapeHtml(x.name)+'</span><b>'+escapeHtml(x.value)+'</b><small>'+escapeHtml(x.detail)+'</small></div>').join("")+'</div>':'')+
      calculationStepsHtml({kind:"match",ev,projection:Number(estimate.projection),current:Number(estimate.current),elapsedMinutes:Number(estimate.elapsed_minutes),remainingMinutes:Number(estimate.remaining_minutes),homeScore:score?.[0],awayScore:score?.[1],poss:estimate.possession_model||possession,lines:estimate.poisson?.probabilities||[],preliminary:!!estimate.preliminary,clock})
    :'<p class="muted">'+escapeHtml(matchForecast.reason||"Недостаточно данных")+'</p>';
  $("matchForecast").insertAdjacentHTML("afterbegin",aiSignalHtml(aiAnalysis.match,"ИИ-модельный анализ"));
  $("matchForecast").insertAdjacentHTML("beforeend",classicModelPanelHtml(classicMatchModels,"FULL GAME · МОДЕЛИ ПЕРВОГО САЙТА"));
  $("quarterForecast").innerHTML=quarter.ready
    ? '<div class="forecast-main"><b>Единый основной прогноз · Q'+escapeHtml(quarter.period||period||"?")+'</b><div class="forecast-number">'+Number(quarter.projection).toFixed(1)+'</div><p>Модель: '+escapeHtml(quarter.distribution||"не указана")+' · prior: '+escapeHtml(quarter.prior?.source||"нет")+' · '+escapeHtml(quarter.calibration_status||"вероятности не калиброваны")+" · "+escapeHtml(quarter.confidence||"уверенность не определена")+'</p><p>Счёт '+(quarter.home==null?"—":quarter.home+" : "+quarter.away)+' · очков '+(quarter.current??"—")+' · прошло '+(quarter.elapsed!=null?quarter.elapsed+" мин":"—")+' · таймер '+(clock||"—")+'</p><p>80%: '+quarter.low80+'–'+quarter.high80+' · 95%: '+quarter.low95+'–'+quarter.high95+(quarter.preliminary?' · предварительно':'')+'</p></div>'+
      (quarter.lines.length?'<h3>Вероятности · Q'+escapeHtml(quarter.period||period||"?")+'</h3><table class="calc-table"><thead><tr><th>Линия</th><th>ТБ</th><th>ТМ</th><th>Направление</th><th>Коэфф.</th></tr></thead><tbody>'+quarter.lines.map(x=>'<tr><td>'+x.line+'</td><td>'+(x.pOver*100).toFixed(1)+'%</td><td>'+(x.pUnder*100).toFixed(1)+'%</td><td>'+(x.pPush*100).toFixed(1)+'%</td><td>'+escapeHtml(x.direction)+'</td><td>'+(x.prices?.length?escapeHtml(x.prices.map(v=>"f"+v.factorId+": "+v.price).join(" · ")):(x.odds?escapeHtml(x.odds):"—"))+'</td></tr>').join('')+'</tbody></table>':'<p class="muted">Линия четверти не получена</p>')+
      calculationStepsHtml({kind:"quarter",period:quarter.period,ev,projection:Number(quarter.projection),current:Number(quarter.current),elapsedMinutes:quarter.elapsed,remainingMinutes:clock&&findPeriodLengthSeconds(ev)?Math.max(0,(Number(clock.split(":")[0])*60+Number(clock.split(":")[1]))/60):null,homeScore:quarter.home,awayScore:quarter.away,poss:quarter.possession_model,lines:quarter.lines||[],preliminary:!!quarter.preliminary,clock})
    :'<p class="muted">'+escapeHtml(quarter.reason||"Недостаточно данных")+'</p>';
  $("quarterForecast").insertAdjacentHTML("afterbegin",aiSignalHtml(aiAnalysis.quarter,"ИИ-анализ четверти"));
  $("quarterForecast").insertAdjacentHTML("beforeend",classicModelPanelHtml(classicQuarterModels,"Q"+(quarter.period||period||"?")+" · МОДЕЛИ ПЕРВОГО САЙТА"));
  const statsMeta=statsQuality?'<p>Покрытие: <b>'+Number(statsQuality.coverage_percent||0)+'%</b> · источники: '+escapeHtml(ev.statistics_source||"нет")+'</p>':'';
  const missingStats=statsQuality?.missing_metrics?.length?'<p class="muted">Нет данных: '+escapeHtml(statsQuality.missing_metrics.join(", "))+'</p>':'';
  const statsConflicts=statsQuality?.conflicts?.length?'<p class="warn">Расхождения источников: '+statsQuality.conflicts.length+' · значения не скрыты</p>':'';
  $("matchForecast").insertAdjacentHTML("beforeend",'<div class="compact-analysis"><h3>Статистика · '+rows.length+'/'+(statsQuality?.expected_count??"—")+'</h3>'+statsMeta+missingStats+statsConflicts+statHtml+(timeline?'<p><b>Четверти:</b> '+escapeHtml(timeline)+'</p>':'')+
    (rows.length?'<h4>Все показатели матча</h4>'+rows.map(st=>'<p>'+escapeHtml(st.name||st.metric_key||"Показатель")+': <b>'+escapeHtml(st.home??"—")+' : '+escapeHtml(st.away??"—")+'</b> · '+escapeHtml(st.source||"источник не указан")+'</p>').join(""):'')+periodStatHtml+'</div>');
  saveAnalysisHistory(ev,score,period,clock,estimate,quarter);
  if(!options.silent)panel.scrollIntoView({behavior:"smooth",block:"start"});
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
  if (/\bc\s*dbl\b|chinese basketball development league|\bnba\b|g[- ]?лига|g[- ]?league/.test(competition)) return 720;
  if (/bskt\s*cup|cup\s*bskt|кубок\s*bskt|philippines.*ncaa|ncaa.*philippines|филиппин.*ncaa/.test(competition)) return 600;
  if (!/\bncaa\b|college|university|студент|колледж/.test(competition) &&
      /adriatic|адриат|аба\s*лига|plk|польша|израил|израиль|super\s*league|суперлиг|bosnia|босни|герцеговин|greece|греци|italy|итал|cyprus|кипр|morocco|марокко|montenegro|черногор|serbia|серби|croatia|хорват|fiba|euroleague|eurocup|eurobasket|vtb|basketball champions league/.test(competition)) return 600;
  return null;
}

function getMatchTotalLines(ev) {
  const candidates=Array.isArray(ev?.match_total_candidates)?ev.match_total_candidates:[];
  const grouped=new Map();
  for(const factor of candidates){
    if(!factor||!["930","931"].includes(String(factor.f))||factor.pt==null)continue;
    const line=Number(factor.pt),price=Number(factor.v);
    if(!Number.isFinite(line)||line<40||line>400||Math.abs(line*2-Math.round(line*2))>1e-8)continue;
    if(!Number.isFinite(price)||price<=1||price>1000)continue;
    const key=line.toFixed(2);
    if(!grouped.has(key))grouped.set(key,{line,prices:[]});
    const row=grouped.get(key);
    const marker=String(factor.f)+":"+String(factor.v);
    if(!row.prices.some(x=>x.marker===marker))
      row.prices.push({factorId:String(factor.f),price:String(factor.v),side:null,marker});
  }
  const lines=Array.from(grouped.values()).map(row=>({...row,odds:row.prices[0]?.price??null,
    source:"PARI",market:"full_game_total",price_side_status:"not_mapped",freshness:"source_timestamp_unavailable"}));
  const cacheKey="pari-total-lines-v3:"+String(ev?.id||"");
  try{if(lines.length)localStorage.setItem(cacheKey,JSON.stringify({savedAt:Date.now(),lines}));}catch(_){}
  return lines.sort((a,b)=>a.line-b.line);
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
  // Apply the 10-minute FIBA format to identified competitions.
  if(/adriatic|адриат|аба\s*лига|plk|польша|израил|израиль|super\s*league|суперлиг|bosnia|босни|герцеговин|greece|греци|italy|итал|cyprus|кипр|morocco|марокко|montenegro|черногор|serbia|серби|croatia|хорват|fiba|euroleague|eurocup|eurobasket|vtb|basketball champions league/.test(text))
    return {seconds:600,periods:4,method:"формат FIBA · 10 минут (по типу турнира)"};
  // If PARI supplies the countdown but not the period length, use >10:00
  // as evidence of a 12-minute period. Otherwise provide a clearly marked
  // provisional 4×10-minute estimate for quarter-based basketball.
  const clock=findLiveClock(ev);
  const clockMatch=String(clock||"").match(/^(\d{1,2}):([0-5]\d)$/);
  const remaining=clockMatch?Number(clockMatch[1])*60+Number(clockMatch[2]):null;
  if(remaining!=null&&remaining>600&&remaining<=720)
    return {seconds:720,periods:4,method:"предварительно 4×12 минут · таймер больше 10:00"};
  const currentPeriod=findLivePeriod(ev);
  if(Number.isInteger(currentPeriod)&&currentPeriod>=1&&currentPeriod<=4)
    return {seconds:600,periods:4,method:"предварительно 4×10 минут · формат турнира не передан"};
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
  const periodCount=Number(ev?.external_live?.period_count||lengthInfo?.periods||4);
  const minimumFullGamePeriod=periodCount===2?2:3;
  const formatUnverified=String(lengthInfo?.method||"").includes("предварительно");
  if((period&&period<minimumFullGamePeriod)||formatUnverified){
    const reason=formatUnverified
      ?"Формат матча не подтверждён — полный прогноз не рассчитывается."
      :"Полный тотал доступен со второй половины матча; прогноз четверти рассчитывается отдельно.";
    const market=marketReferenceForecast(lines);
    return market?{...market,reason}:{ready:false,reason,lines};
  }
  if(score&&period&&clock&&periodLength){
    const estimate=estimateLiveTotal({...ev,external_live:{...(ev.external_live||{}),period_length_seconds:periodLength}},score,period,clock);
    if(estimate&&estimate.elapsed_seconds>0){
      const preliminary=false;
      const modeled=applyPoissonModel(estimate,lines,String(ev.id||"")+"|"+score.join(":")+"|"+clock,ev,period,clock);
      const statsCount=Number(ev?.statistics_quality?.available_count||0);
      const completedPeriods=Array.from({length:Math.max(0,Number(period)-1)},(_,i)=>readPeriodScore(ev,i)).filter(Number.isFinite).length;
      const dataQualityMode=statsCount>=8&&modeled.possession_model?.applied?"confirmed_box_score":
        completedPeriods>=2?"score_time_and_periods":"score_and_time_only";
      const confidence=preliminary?"низкая · ранняя стадия/формат":
        dataQualityMode==="confirmed_box_score"?"ограниченная · подтверждённые статистические поля":
        dataQualityMode==="score_time_and_periods"?"низкая · счёт, таймер и периоды":"низкая · только счёт и время";
      return {ready:true,kind:"live_tempo",estimate:{...modeled,confidence,data_quality_mode:dataQualityMode,
        calibration_status:"не калибрована на независимых финальных исходах",overtime_included:false,market_scope_status:"правило ОТ линии не подтверждено",preliminary},lines,
        message:preliminary?"Предварительная оценка: ранняя стадия или формат не подтверждены":"Единый основной прогноз по текущему счёту, времени и доступной статистике"};
    }
  }
  if(score&&period&&lengthInfo){
    const estimate=estimateLiveTotalFromPeriod(ev,score,period,lengthInfo);
    if(estimate)return {ready:true,kind:"period_range",estimate:applyPoissonModel(estimate,lines,String(ev.id||"")+"|"+score.join(":")+"|Q"+period,ev,period,clock),lines,message:"Диапазон по номеру периода без подтверждённого таймера"};
  }
  const market=marketReferenceForecast(lines);
  if(market){
    const missing=[];
    if(!score)missing.push("счёт");
    if(!period)missing.push("период");
    if(!clock)missing.push("таймер");
    if(!periodLength)missing.push("длительность");
    return {...market,reason:"Игровая модель уточнит ориентир после получения: "+missing.join(", ")};
  }
  const missing=[];
  if(!score)missing.push("счёт");
  if(!period)missing.push("период");
  if(!clock)missing.push("таймер");
  if(!periodLength)missing.push("длительность периода");
  return {ready:false,reason:"Нельзя достоверно спрогнозировать без: "+missing.join(", "),lines};
}

function quarterHighlightInfo(ev){
  const period=findLivePeriod(ev),clock=findLiveClock(ev);
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||inferPeriodLengthForForecast(ev)?.seconds||0);
  const match=String(clock||"").match(/^(\\d{1,2}):([0-5]\\d)$/);
  if(!period||!match||!Number.isFinite(length)||length<300||length>1200)return null;
  const remaining=Number(match[1])*60+Number(match[2]);
  if(remaining>length)return null;
  const elapsed=length-remaining;
  if(elapsed<300||elapsed>480)return null;
  return {period,elapsed,minute:Math.floor(elapsed/60),second:elapsed%60};
}

function aiModelAnalysis(ev,matchForecast,quarterForecast){
  const result={match:null,quarter:null};
  const estimate=matchForecast?.ready&&matchForecast.kind!=="market_reference"?matchForecast.estimate:null;
  const period=findLivePeriod(ev),clock=findLiveClock(ev),info=inferPeriodLengthForForecast(ev);
  const length=Number(ev?.period_length_seconds||info?.seconds||0);
  const makeSignal=(projection,current,lines,sigma,confidence,drivers,kind)=>{
    if(!Number.isFinite(projection)||!Number.isFinite(current))return null;
    const probabilities=(Array.isArray(lines)?lines:[]).filter(x=>Number.isFinite(Number(x.line))).map(x=>{
      const line=Number(x.line),pOver=Math.max(0,Math.min(1,1-normalCdf((Math.floor(line)+0.5-projection)/Math.max(sigma,1))));
      return {...x,pOver,pUnder:1-pOver,direction:pOver>=0.57?"ТБ":pOver<=0.43?"ТМ":"без преимущества",edge:Math.abs(pOver-0.5)};
    });
    const strongest=probabilities.slice().sort((a,b)=>b.edge-a.edge)[0]||null;
    return {kind,projection:Math.round(projection*10)/10,current,probabilities,strongest,confidence,drivers:drivers.slice(0,5)};
  };
  if(estimate){
    const current=Number(estimate.current),remaining=Number(estimate.remaining_minutes);
    const candidates=[{value:current+Number(estimate.pace_per_min||0)*remaining,weight:0.58}];
    const drivers=["темп "+Number(estimate.pace_per_min||0).toFixed(2)+" оч./мин"];
    const possession=estimate.possession_model;
    if(possession?.applied&&Number.isFinite(Number(possession.projection))){
      candidates.push({value:Number(possession.projection),weight:0.32});
      drivers.push("владения "+Number(possession.average).toFixed(1)+" · PPP "+Number(possession.pointsPerPossession).toFixed(3));
    }
    if(Number(period)>1&&length>0&&remaining>0){
      const previous=readPeriodScore(ev,Number(period)-2);
      if(Number.isFinite(previous)&&previous>0){
        const rate=previous/(length/60);
        candidates.push({value:current+rate*remaining,weight:possession?.applied?0.10:0.18});
        drivers.push("прошлая четверть "+rate.toFixed(2)+" оч./мин");
      }
    }
    const totalWeight=candidates.reduce((a,x)=>a+x.weight,0);
    const projection=candidates.reduce((a,x)=>a+x.value*x.weight,0)/totalWeight;
    const stats=liveShootingContext(ev,estimate);
    if(stats.efg!=null)drivers.push("eFG "+(stats.efg*100).toFixed(1)+"%");
    const coverage=Number(ev?.statistics_quality?.available_count||0);
    const verifiedClock=!!clock&&!!length&&!String(info?.method||"").includes("предварительно");
    let confidence=verifiedClock?"средняя":"низкая";
    if(verifiedClock&&possession?.applied&&coverage>=8)confidence="ограниченная · статистика владений доступна";
    if(!coverage)confidence="низкая · только счёт и время";
    if(estimate.preliminary||!verifiedClock)confidence="низкая · формат или таймер предварительный";
    const sigma=Math.max(Number(estimate.sigma||0),Math.sqrt(Math.max(1,projection-current))*1.18,2);
    result.match=makeSignal(projection,current,getMatchTotalLines(ev),sigma,confidence,drivers,"match");
  }
  if(quarterForecast?.ready&&quarterForecast.current!=null){
    const q=quarterForecast;
    const sigma=Math.max(2,Math.sqrt(Math.max(1,Number(q.projection)-Number(q.current||0)))*1.25);
    result.quarter=makeSignal(Number(q.projection),Number(q.current||0),q.lines,sigma,
      q.preliminary?"низкая":"средняя",[q.score_source||"темп четверти",...(q.possession_model?.applied?["модель владений"]:[])],"quarter");
  }
  return result;
}

function classicBasketballModelAnalysis(ev,matchForecast,quarterForecast,kind="match"){
  const isQuarter=kind==="quarter";
  const forecast=isQuarter?quarterForecast:matchForecast;
  if(!forecast?.ready||(!isQuarter&&forecast.kind==="market_reference"))return {models:[],consensus:null,groups:[],anomaly:null,mlStatus:"ожидают подтверждённой игровой выборки"};
  const estimate=isQuarter?forecast:forecast.estimate;
  const current=Number(estimate?.current);
  const projection=Number(estimate?.projection);
  if(!Number.isFinite(current)||!Number.isFinite(projection))return {models:[],consensus:null,groups:[],anomaly:null,mlStatus:"ожидают подтверждённой игровой выборки"};
  const lines=(Array.isArray(forecast.lines)?forecast.lines:[]).filter(x=>Number.isFinite(Number(x.line)));
  const period=findLivePeriod(ev),clock=findLiveClock(ev);
  const info=inferPeriodLengthForForecast(ev);
  const length=Number(ev?.period_length_seconds||ev?.external_live?.period_length_seconds||info?.seconds||0);
  const elapsed=isQuarter?Number(forecast.elapsed)*60:Number(estimate.elapsed_seconds||0);
  const remaining=isQuarter
    ?(clock&&length?Math.max(0,(Number(clock.split(":")[0])*60+Number(clock.split(":")[1]))):null)
    :Number(estimate.remaining_seconds??(Number(estimate.remaining_minutes)*60));
  const safeSigma=Math.max(2,Number(isQuarter?Math.sqrt(Math.max(1,projection-current))*1.2:estimate.sigma||0),Math.sqrt(Math.max(1,projection-current))*1.15);
  const models=[];
  const add=(name,pred,sigma,detail,weight=1)=>{
    if(!Number.isFinite(pred)||pred<current-0.01||pred>400)return;
    models.push({name,projection:Math.round(pred*10)/10,sigma:Math.max(1,Number(sigma)||safeSigma),detail,weight});
  };
  const linear=elapsed>0&&length>0
    ?current/elapsed*(isQuarter?length:length*Number(ev?.external_live?.period_count||info?.periods||4))
    :projection;
  add("Линейный темп",linear,Math.max(2,Math.sqrt(Math.max(1,linear-current))*1.3),"Текущие очки ÷ прошедшее время × полная длительность",1);
  const completed=[];
  if(!isQuarter){
    for(let i=0;i<Math.max(0,Number(period||1)-1);i++){
      const v=readPeriodScore(ev,i);if(Number.isFinite(v)&&v>0)completed.push(v);
    }
  }
  const recent=completed.length?completed[completed.length-1]:null;
  let dynamic=linear;
  if(recent!=null&&length>0&&elapsed>0){
    const rate=recent/(length/60),global=current/(elapsed/60);
    dynamic=linear*0.72+(current+rate*(remaining/60))*0.28;
    add("Динамический темп",dynamic,safeSigma*1.05,"72% общий темп + 28% темп последней четверти",1.05);
  } else if(isQuarter&&Number.isFinite(Number(forecast.projection))){
    dynamic=projection;
    add("Динамика четверти",dynamic,safeSigma,"Текущий темп четверти с поправкой на доступный таймер",1);
  }
  const possession=isQuarter?forecast.possession_model:estimate.possession_model;
  if(possession?.applied&&Number.isFinite(Number(isQuarter?forecast.possession_projection:possession.projection))){
    add("Модель владений",Number(isQuarter?forecast.possession_projection:possession.projection),safeSigma*0.95,
      "Владения × очки за владение (PPP)",1.2);
  }
  if(!isQuarter&&Number(period)===4&&Number.isFinite(remaining)){
    const score=findLiveScore(ev),margin=score?Math.abs(score[0]-score[1]):null;
    let endgame=projection,detail="Базовый прогноз без неподтверждённых поправок";
    if(margin!=null&&remaining<=360){
      if(margin<=8){endgame=projection+(360-remaining)/360*2.5;detail="Близкая концовка: возможные тактические фолы";}
      else if(margin>=16){endgame=projection-(360-remaining)/360*1.5;detail="Большая разница: риск замедления концовки";}
    }
    add("Модель концовки",endgame,safeSigma*1.1,detail,0.9);
  }
  const smoothValues=isQuarter?[]:completed;
  let anomaly=null;
  if(smoothValues.length>=2){
    const avg=smoothValues.reduce((x,y)=>x+y,0)/smoothValues.length;
    const sd=Math.sqrt(smoothValues.reduce((x,y)=>x+(y-avg)**2,0)/smoothValues.length);
    const cv=avg>0?sd/avg:1;
    const isSmooth=cv<=0.18;
    const adjusted=isSmooth?projection*0.98+avg*(Number(ev?.external_live?.period_count||4)-Number(period||1))*0.02:projection;
    add("Smooth Game",adjusted,safeSigma*(isSmooth?0.9:1.25),
      (isSmooth?"ровная траектория":"нестабильная траектория")+" · CV "+(cv*100).toFixed(1)+"%",isSmooth?1.1:0.7);
    anomaly={smooth:isSmooth,cv:Math.round(cv*1000)/10,average:Math.round(avg*10)/10,sd:Math.round(sd*10)/10};
  } else if(isQuarter){
    add("Smooth Game Q",projection,safeSigma*1.1,"Для проверки плавности нужны данные нескольких четвертей",0.7);
  }
  // Дополнительные модели с прозрачными требованиями к данным.
  const remainingMinutes=Number.isFinite(remaining)?Math.max(0,remaining/60):null;
  const elapsedMinutes=elapsed/60;
  const priorRate=completed.length&&length>0?(completed.reduce((a,b)=>a+b,0)/completed.length)/(length/60):(elapsedMinutes>0?current/elapsedMinutes:NaN);
  const tools=[];
  tools.push({name:"Марковская модель владений",status:"не реализована",detail:"Для настоящей модели нужны размеченные последовательности состояний владений; количество событий само по себе не образует матрицу переходов."});
  tools.push({name:"Байесовская Gamma-Poisson матча",status:"исключена из основного расчёта",detail:"Нет независимого предматчевого prior для полного матча; прежняя формула смешивала полный тотал и средний тотал четверти."});
  tools.push({name:"Фильтр Калмана",status:"диагностика, не прогноз",detail:"Текущая реализация не имеет оценённой модели шума/состояния и не включается в итоговый прогноз."});
  tools.push({name:"CatBoost / LightGBM",status:"ожидают обучения",detail:"Нужны размеченные завершённые матчи; прогноз не подменяется эвристикой"});
  tools.push({name:"Калибровка вероятностей",status:"ожидает выборку",detail:"Нужны прогнозы и финальные исходы для Brier score и reliability calibration"});
  if(!models.length)return {models:[],consensus:null,groups:[],anomaly,mlStatus:"недостаточно данных для расчёта"};
  for(const model of models){
    const modelLine=lines.slice().sort((a,b)=>Math.abs(Number(model.projection)-Number(a.line))-Math.abs(Number(model.projection)-Number(b.line)))[0];
    model.direction=modelLine?(model.projection>Number(modelLine.line)?"центр выше линии":model.projection<Number(modelLine.line)?"центр ниже линии":"центр на линии"):"нет линии";
    model.line=modelLine?.line??null;
    model.probability=null;
  }
  tools.push({name:"Монте-Карло · 100 000",status:"способ симуляции",detail:"Используется для распределения основного прогноза, не считается отдельной независимой моделью."});
  tools.push({name:"Калибровка вероятностей",status:"не калибрована",detail:"Нет достаточной выборки подтверждённых финальных исходов с разделением по времени и матчам."});
  return {models,consensus:null,groups:[],anomaly,tools,mlStatus:"Таблица показывает альтернативные ориентиры. Они коррелируют по исходному счёту/времени и не голосуют за основной прогноз."};
}
function classicModelPanelHtml(data,title){
  if(!data)return "";
  const rows=(data.models||[]).map(m=>'<tr><td>'+escapeHtml(m.name)+'</td><td>'+m.projection+'</td><td>'+(m.line!=null?escapeHtml(m.direction+" "+m.line):"нет линии")+'</td></tr>').join("");
  const anomaly=data.anomaly?(data.anomaly.smooth?"Smooth Game: ровная траектория":"Аномалия/неровная траектория")+" · CV "+data.anomaly.cv+"%":"Проверка Smooth Game: мало завершённых четвертей";
  const toolsHtml=(data.tools||[]).map(t=>'<div class="model-tool-row"><b>'+escapeHtml(t.name)+'</b><span>'+escapeHtml(t.status)+'</span><small>'+escapeHtml(t.detail)+'</small></div>').join("");
  return '<section class="classic-model-panel"><div class="calc-section-head"><div><span class="eyebrow">'+escapeHtml(title)+'</span><h3>Альтернативные ориентиры · не голосуют за прогноз</h3></div><span class="calc-live-tag">'+(data.models.length||0)+' сравнений</span></div>'+
    '<div class="table-scroll"><table class="calc-table"><thead><tr><th>Ориентир</th><th>Тотал</th><th>Центр относительно линии</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
    '<p class="model-anomaly">'+escapeHtml(anomaly)+'</p><div class="model-tool-list">'+toolsHtml+'</div><p class="muted model-training-note">'+escapeHtml(data.mlStatus)+'</p></section>';
}

function aiSignalHtml(signal,title){
  if(!signal)return '<p class="muted">Эвристические факторы: недостаточно подтверждённых данных.</p>';
  return '<div class="ai-model-panel"><b>'+escapeHtml(title)+' · диагностические факторы</b>'+
    '<p>Это не отдельный прогноз и не независимая вероятность. Основные числа берутся из единого расчётного результата выше.</p>'+
    '<small>'+escapeHtml((signal.drivers||[]).join(" · "))+'</small></div>';
}
function findQuarterClockFromElapsed(ev,period,length){
  if(!Number.isInteger(Number(period))||!Number.isFinite(Number(length))||Number(length)<300)return null;
  const candidates=[ev?.match_elapsed_seconds,ev?.external_live?.match_elapsed_seconds,
    ev?.live_fields?.timerSeconds,ev?.live_fields?.timer_seconds,
    ev?.raw?.timerSeconds,ev?.raw?.timer_seconds];
  const raw=candidates.find(value=>value!=null&&value!=="");
  const elapsedMatch=Number(raw);
  if(!Number.isFinite(elapsedMatch)||elapsedMatch<0||elapsedMatch>4*Number(length))return null;
  const elapsedInQuarter=elapsedMatch-(Number(period)-1)*Number(length);
  if(elapsedInQuarter<0||elapsedInQuarter>Number(length))return null;
  const remaining=Math.max(0,Math.round(Number(length)-elapsedInQuarter));
  return String(Math.floor(remaining/60)).padStart(2,"0")+":"+String(remaining%60).padStart(2,"0");
}
function automaticQuarterForecast(ev) {
  const period=findLivePeriod(ev);
  const info=inferPeriodLengthForForecast(ev);
  const length=findPeriodLengthSeconds(ev)||info?.seconds;
  const directClock=findLiveClock(ev);
  const clock=directClock||(period&&length?findQuarterClockFromElapsed(ev,period,length):null);
  const recoveredClock=!directClock&&!!clock;
  const periods=Number(ev?.external_live?.period_count||info?.periods||0);
  if(periods===2)return {ready:false,reason:"Формат 2×20 минут: четвертей в этом матче нет."};
  if(!period||period<1||period>4)return {ready:false,reason:"Не подтверждён номер текущей четверти."};
  if(!length||length<300||length>1200||String(info?.method||"").includes("предварительно"))
    return {ready:false,reason:"Не подтверждена длительность четверти; прогноз не рассчитывается по предположительному формату."};
  const m=String(clock||"").match(/^(\d{1,2}):([0-5]\d)$/);
  if(!m)return {ready:false,reason:"Нет подтверждённого таймера четверти."};
  const remaining=Number(m[1])*60+Number(m[2]);
  if(remaining>length)return {ready:false,reason:"Таймер превышает длительность четверти."};
  const elapsed=length-remaining;
  const matchScore=findLiveScore(ev);
  const currentQuarter=currentQuarterScore(ev,period,matchScore);
  if(!currentQuarter)return {ready:false,reason:"Не удалось подтвердить отдельный счёт четверти: нет её счёта или полного набора предыдущих периодов."};
  const current=currentQuarter.pair[0]+currentQuarter.pair[1];
  const previousQuarterTotals=[];
  for(let i=0;i<period-1;i++){
    const total=readPeriodScore(ev,i);
    if(Number.isFinite(total)&&total>=0)previousQuarterTotals.push(total);
  }
  const quarterLines=[];
  const quarterPatterns=[
    new RegExp("\\bq\\s*"+period+"\\b","i"),
    new RegExp("\\bquarter\\s*"+period+"\\b","i"),
    new RegExp("\\bperiod\\s*"+period+"\\b","i"),
    new RegExp("\\b"+period+"\\s*[-–.]?\\s*(?:я|й|ая|ый)?\\s*четверт\\w*","i"),
    new RegExp("четверт\\w*\\s*"+period+"\\b","i")
  ];
  const seen=new Set();
  const walk=(node,label="",contextPeriod=null,depth=0)=>{
    if(!node||typeof node!=="object"||depth>8||seen.has(node))return;
    seen.add(node);
    if(Array.isArray(node)){for(const child of node)walk(child,label,contextPeriod,depth+1);return;}
    const own=String(node.name||node.kindName||node.marketName||node.market_name||node.typeName||node.description||"");
    const nextLabel=own||label;
    const rawPeriod=node.period??node.periodNumber??node.period_number??node.quarter??node.quarterNumber??node.quarter_number;
    const explicit=rawPeriod==null?contextPeriod:Number(rawPeriod);
    const target=Number.isInteger(period)&&(explicit===period||(!Number.isInteger(explicit)&&quarterPatterns.some(rx=>rx.test(nextLabel))));
    if(["930","931"].includes(String(node.f))&&target){
      const line=Number(node.pt);
      if(Number.isFinite(line)&&line>=20&&line<100&&Math.abs(line*2-Math.round(line*2))<1e-8){
        let row=quarterLines.find(x=>x.line===line);
        if(!row){row={line,prices:[],source:"PARI",price_side_status:"not_mapped"};quarterLines.push(row);}
        if(node.v!=null&&Number(node.v)>1&&!row.prices.some(x=>x.factorId===String(node.f)&&x.price===String(node.v)))
          row.prices.push({factorId:String(node.f),price:String(node.v),side:null});
        row.odds=row.prices[0]?.price??null;
      }
      return;
    }
    for(const child of Object.values(node))if(child&&typeof child==="object")walk(child,nextLabel,explicit,depth+1);
  };
  for(const root of [ev.related_factor_groups,ev.related_factors,ev.factors,ev.related_events,ev.match_total_candidates])walk(root);
  if(!window.PARIForecastCore?.simulateQuarter)
    return {ready:false,reason:"Расчётное ядро четверти не загрузилось."};
  const distribution=window.PARIForecastCore.simulateQuarter({
    current,periodSeconds:length,elapsedSeconds:elapsed,priorQuarterTotals:previousQuarterTotals,
    lines:quarterLines,seed:String(ev.id||"")+"|Q"+period+"|"+clock,simulations:100000
  });
  if(!distribution.ready)return {ready:false,reason:distribution.reason};
  const preliminary=!previousQuarterTotals.length||recoveredClock;
  const source=previousQuarterTotals.length
    ?"Gamma-Poisson: завершённые четверти этого матча + счёт текущей Q"
    :"Gamma-Poisson с prior Джеффриса: только счёт и точное время";
  const poisson={simulations:distribution.simulations,mu:distribution.expectedRemaining,
    low80:distribution.low80,high80:distribution.high80,low95:distribution.low95,high95:distribution.high95,
    probabilities:distribution.probabilities,method:distribution.distribution};
  return {
    ready:true,kind:"quarter_gamma_poisson",period,current,projection:distribution.projection,
    home:currentQuarter.pair[0],away:currentQuarter.pair[1],score_source:currentQuarter.source+" · "+source,
    elapsed:elapsed/60,elapsed_seconds:elapsed,remaining_seconds:remaining,remaining_minutes:remaining/60,
    low80:distribution.low80,high80:distribution.high80,low95:distribution.low95,high95:distribution.high95,
    expected_remaining:distribution.expectedRemaining,posterior:distribution.posterior,prior:distribution.prior,
    posterior_rate:distribution.posteriorRate,distribution:distribution.distribution,
    calibration_status:distribution.calibrationStatus,simulations:distribution.simulations,
    poisson,lines:distribution.probabilities,preliminary,
    confidence:previousQuarterTotals.length>=2?"ограниченная · prior по двум и более четвертям":previousQuarterTotals.length?"низкая · prior по одной четверти":"низкая · внешний prior отсутствует",
    context_factors:[source,"Остаток очков рассчитывается через Gamma-Poisson predictive; неопределённость темпа включена в распределение.",
      "Доступно завершённых четвертей для prior: "+previousQuarterTotals.length,
      "Калибровка вероятностей не выполнена на независимых финальных исходах."],
    overtime_included:false,model_version:"quarter-gp-v1"
  };
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
    const requestTimeout = setTimeout(() => controller.abort(), 50000);
    let response;
    try {
      response = await fetch("/api/pari/live", {cache:"no-store", signal:controller.signal});
    } catch (error) {
      if (controller.signal.aborted) throw new Error("PARI API не ответил за 50 секунд");
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
    if(selectedLiveEventId){
      const refreshedSelection=window.pariLiveEvents.find(x=>String(x.id)===selectedLiveEventId);
      if(refreshedSelection)showSelectedMatch(refreshedSelection,{silent:true});
    }
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
        try {
          const teams = escapeHtml(ev.team1 || "Команда 1") + " — " + escapeHtml(ev.team2 || "Команда 2");
          const score = eventScore(ev);
          const totalMarkets = (ev.match_total_candidates || []).map(f => f.pt ? ("Тотал " + f.pt + " @ " + f.v) : ("Коэф. " + f.v));
          const statCount = (ev.external_statistics || []).length;
          const coverage = ev.statistics_quality ? (" · покрытие " + ev.statistics_quality.available_count + "/" + ev.statistics_quality.expected_count) : "";
          const statsLabel = statCount ? ("Статистика: " + statCount + " показателей" + coverage + " · " + (ev.statistics_source || "источник не указан")) : "Статистика: нет подтверждённых значений PARI/внешнего источника";
          const details = [ev.league || ev.sport_name, score, eventPeriod(ev), eventClock(ev), statsLabel, ...totalMarkets].filter(Boolean).map(escapeHtml).join(" · ");
          const forecast = automaticForecast(ev);
          const quarterForecast = automaticQuarterForecast(ev);
          const aiAnalysis = aiModelAnalysis(ev,forecast,quarterForecast);
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
              if(forecast.kind==="period_range")lineText+=" · без точного таймера, предварительно";
              if(estimate.preliminary)lineText+=" · ранняя оценка, низкая уверенность";
            }
            forecastHtml = '<span class="live-match-meta forecast-live">Прогноз матча: ' + escapeHtml(lineText) + '</span>';
          } else {
            forecastHtml = '<span class="live-match-meta forecast-pending">Прогноз матча ожидает данные: ' + escapeHtml(forecast.reason) + '</span>';
          }
          if (quarterForecast.ready) {
            const qLines=quarterForecast.lines.length
              ? quarterForecast.lines.map(x => x.direction+" "+x.line+" · ТБ "+(x.pOver*100).toFixed(0)+"% / ТМ "+(x.pUnder*100).toFixed(0)+"%").join(" | ")
              : "линия четверти пока не найдена";
            forecastHtml += '<span class="live-match-meta forecast-live">Прогноз Q'+escapeHtml(quarterForecast.period||findLivePeriod(ev)||"?")+': '+escapeHtml(quarterForecast.projection)+' очков за четверть · '+escapeHtml(qLines)+(quarterForecast.preliminary?' · предварительно':'')+'</span>';
          } else {
            forecastHtml += '<span class="live-match-meta forecast-pending">Прогноз четверти: '+escapeHtml(quarterForecast.reason)+'</span>';
          }
          const aiBest=aiAnalysis.match?.strongest;
          if(aiAnalysis.match)forecastHtml+='<span class="live-match-meta forecast-live">ИИ · матч: '+escapeHtml(aiAnalysis.match.projection)+' · направление '+escapeHtml(aiBest?(aiBest.direction==="без преимущества"?"нет перевеса":aiBest.direction+" "+aiBest.line):"нет линии PARI")+' · '+escapeHtml(aiAnalysis.match.confidence)+'</span>';
          const aiQuarterBest=aiAnalysis.quarter?.strongest;
          if(aiAnalysis.quarter)forecastHtml+='<span class="live-match-meta forecast-live">ИИ · Q'+escapeHtml(quarterForecast.period||findLivePeriod(ev)||"?")+': '+escapeHtml(aiAnalysis.quarter.projection)+' · направление '+escapeHtml(aiQuarterBest?(aiQuarterBest.direction==="без преимущества"?"нет перевеса":aiQuarterBest.direction+" "+aiQuarterBest.line):"нет линии PARI")+' · '+escapeHtml(aiAnalysis.quarter.confidence)+'</span>';
          const quarterHighlight=quarterHighlightInfo(ev);
          const highlightTag=quarterHighlight?'<span class="quarter-highlight-tag">★ ВЫДЕЛЕН: '+quarterHighlight.minute+'–8 мин Q'+quarterHighlight.period+'</span>':"";
          return '<button type="button" class="live-match'+(quarterHighlight?' live-match-quarter-window':'')+'" data-event-id="' + escapeHtml(ev.id) + '">' +
            highlightTag + '<span class="live-match-title">' + teams + '</span><span class="live-match-meta">' + (details || "Событие получено из LIVE API PARI") + '</span>' + forecastHtml + '</button>';
  
        } catch (renderError) {
          console.error("LIVE_MATCH_RENDER_ERROR", ev?.id, renderError);
          const safe = value => escapeHtml(String(value ?? ""));
          const title = [ev?.team1 || "Команда 1", ev?.team2 || "Команда 2"].map(safe).join(" — ");
          return '<button type="button" class="live-match" data-event-id="' + safe(ev?.id) + '><span class="live-match-title">' + title + '</span><span class="live-match-meta">Матч получен от PARI · подробный анализ временно недоступен</span></button>';
        }
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
renderAnalysisHistory();
if ($("clearHistory")) $("clearHistory").addEventListener("click", clearAnalysisHistory);


async function refreshServerArchiveStatus(){
  const set=(id,value)=>{const el=$(id);if(el)el.textContent=value;};
  try{
    const response=await fetch("/api/archive/status",{cache:"no-store"});
    if(!response.ok)throw new Error("HTTP "+response.status);
    const data=await response.json();
    set("archiveMatches",String(data.matches??"—"));
    set("archiveSnapshots",String(data.snapshots??"—"));
    set("archiveSize",((Number(data.database_bytes||0)/1024/1024).toFixed(2))+" МБ");
    set("archiveLastRun",data.collector?.checked_at?new Date(data.collector.checked_at).toLocaleTimeString("ru-RU"):"ожидание");
    const persistent=!!data.persistent;
    const badge=$("archiveStorageMode");
    if(badge){badge.textContent=persistent?"ПОСТОЯННЫЙ ДИСК":"ВРЕМЕННОЕ ХРАНИЛИЩЕ";badge.className=persistent?"pill success":"pill warn";}
    const errors=data.collector?.error?(" · ошибка: "+data.collector.error):"";
    set("archiveStatusText",(persistent?"Данные записываются на постоянный диск Render.":"ВНИМАНИЕ: сейчас база на временном диске и может исчезнуть при перезапуске Render.")+
      " Сборщик работает каждые "+data.interval_seconds+" сек., пока сервер активен. Последняя проверка: "+
      (data.collector?.checked_at||"ещё не было")+". Сохранено новых снимков в последнем цикле: "+(data.collector?.saved??0)+errors+
      ". "+data.prediction_capture+" Публичные ZIP-архивы GitHub доступны по ссылке выше; очистка этой кнопкой затрагивает только локальную базу Render и не удаляет опубликованные ZIP.");
  }catch(error){set("archiveStatusText","Не удалось получить статус архива: "+error.message);}
}
if($("clearServerArchive"))$("clearServerArchive").addEventListener("click",async()=>{
  if(!window.confirm("Сначала скачай ZIP и сохрани его. Очистить серверную базу матчей?"))return;
  try{
    const response=await fetch("/api/archive/clear?confirm=true",{method:"POST"});
    const data=await response.json();
    if(!response.ok||!data.ok)throw new Error(data.error||"Ошибка очистки");
    await refreshServerArchiveStatus();
    window.alert("База очищена. Скачанный ZIP-архив не затронут.");
  }catch(error){window.alert("Не удалось очистить базу: "+error.message);}
});

refreshServerArchiveStatus();
