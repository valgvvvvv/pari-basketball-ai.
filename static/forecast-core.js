(function(root,factory){
  const api=factory();
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  if(root)root.PARIForecastCore=api;
})(typeof globalThis!=="undefined"?globalThis:null,function(){
  "use strict";
  function quarterPosterior(input){
    const count=x=>x!=null&&typeof x!=="boolean"&&String(x).trim()!==""&&Number.isInteger(Number(x))&&Number(x)>=0;
    const seconds=x=>x!=null&&typeof x!=="boolean"&&String(x).trim()!==""&&Number.isFinite(Number(x));
    const priorRows=input.completedQuarterTotals??[];
    if(!count(input.current)||!seconds(input.elapsedSeconds)||!seconds(input.periodLengthSeconds)||
       !Array.isArray(priorRows)||!priorRows.every(count))return {ready:false,reason:"неполный или некорректный счёт/экспозиция"};
    const aggregateProvided=input.priorPoints!=null||input.priorExposureSeconds!=null;
    if(aggregateProvided&&(!count(input.priorPoints)||!seconds(input.priorExposureSeconds)||Number(input.priorExposureSeconds)<=0||priorRows.length))
      return {ready:false,reason:"некорректный исторический ориентир"};
    if(input.priorQuarterCount!=null&&(!count(input.priorQuarterCount)||Number(input.priorQuarterCount)<1))
      return {ready:false,reason:"некорректное число завершённых периодов"};
    const current=Number(input.current),elapsedSeconds=Number(input.elapsedSeconds),periodLengthSeconds=Number(input.periodLengthSeconds);
    const completed=priorRows.map(Number);
    const aggregatePriorPoints=Number(input.priorPoints),aggregatePriorExposureSeconds=Number(input.priorExposureSeconds);
    const hasAggregatePrior=aggregateProvided&&Number.isFinite(aggregatePriorPoints)&&aggregatePriorPoints>=0&&
      Number.isFinite(aggregatePriorExposureSeconds)&&aggregatePriorExposureSeconds>0;
    if(!Number.isFinite(current)||current<0||!Number.isFinite(elapsedSeconds)||!Number.isFinite(periodLengthSeconds)||
       periodLengthSeconds<300||periodLengthSeconds>1200||elapsedSeconds<0||elapsedSeconds>periodLengthSeconds)
      return {ready:false,reason:"некорректный счёт или таймер"};
    if(elapsedSeconds<180&&completed.length===0&&!hasAggregatePrior)
      return {ready:false,reason:"первые 3 минуты без исторического ориентира — недостаточно данных"};
    const periodMinutes=periodLengthSeconds/60,elapsedMinutes=elapsedSeconds/60,remainingMinutes=(periodLengthSeconds-elapsedSeconds)/60;
    // Jeffreys Gamma(1/2, 0) prior; completed quarters contribute observed points
    // and their exact playing-time exposure. Current-quarter points/time update it.
    const completedPoints=hasAggregatePrior?aggregatePriorPoints:completed.reduce((sum,x)=>sum+x,0);
    const priorAlpha=0.5+completedPoints;
    const priorExposure=hasAggregatePrior?aggregatePriorExposureSeconds/60:completed.length*periodMinutes;
    const priorQuarterCount=Number.isInteger(Number(input.priorQuarterCount))?Number(input.priorQuarterCount):completed.length;
    const alpha=priorAlpha+current,beta=priorExposure+elapsedMinutes;
    if(!(alpha>0)||!(beta>0))return {ready:false,reason:"недостаточная экспозиция для оценки темпа"};
    const expectedRemaining=alpha/beta*remainingMinutes;
    const meanUncertaintyVariance=(expectedRemaining*expectedRemaining)/alpha;
    const meanUncertaintySd=Math.sqrt(meanUncertaintyVariance);
    const varianceRemaining=expectedRemaining+meanUncertaintyVariance;
    const priorMean=completed.length?completed.reduce((sum,x)=>sum+x,0)/completed.length:null;
    return {ready:true,current,elapsedSeconds,remainingSeconds:periodLengthSeconds-elapsedSeconds,
      alpha,beta,remainingMinutes,expectedRemaining,varianceRemaining,meanUncertaintyVariance,meanUncertaintySd,
      projection:current+expectedRemaining,priorQuarterCount,priorQuarterMean:priorMean,
      priorAlpha,priorExposure,completedPoints,completedQuarterTotals:completed.slice(),priorIsAggregate:hasAggregatePrior,periodMinutes,
      quality:priorQuarterCount>=2&&elapsedSeconds>=180?"ограниченная · prior по сумме завершённых периодов":
        priorQuarterCount===1?"низкая · prior по одному периоду":"низкая · недостаточно истории",
      method:"Gamma-Poisson posterior predictive · Jeffreys prior"};
  }
  // Exact Gamma-Poisson predictive PMF. No normal approximation or random seed.
  // Tail truncation is checked rather than silently returning a partial distribution.
  function predictiveDistribution(posterior){
    if(!posterior?.ready||!Number.isInteger(posterior.current)||posterior.current<0||
       ![posterior.alpha,posterior.beta,posterior.remainingMinutes].every(Number.isFinite)||
       posterior.alpha<=0||posterior.beta<=0||posterior.remainingMinutes<0)return null;
    const {alpha,beta,remainingMinutes:t}=posterior,unit=posterior.scoringUnit??1;
    if(![1,2,3].includes(unit))return null;
    const mu=alpha*t/beta,exposure=beta*unit;
    let mass=Math.exp(-alpha*Math.log1p(t/exposure)),sum=mass;
    if(!(mass>0))return null;
    const cdf=[sum],pmf=[mass],ratio=t/(exposure+t);
    for(let k=1;k<20000&&(sum<1-1e-13||k<=mu/unit);k++){
      mass*=((alpha+k-1)/k)*ratio;
      for(let j=1;j<unit;j++){pmf.push(0);cdf.push(Math.min(1,sum));}
      sum+=mass;pmf.push(mass);cdf.push(Math.min(1,sum));
    }
    if(!Number.isFinite(sum)||Math.abs(1-sum)>1e-10)return null;
    const at=k=>k<0?0:k>=cdf.length?1:cdf[Math.floor(k)];
    const quantile=q=>posterior.current+cdf.findIndex(x=>x>=q);
    return {at,pmf,quantile,tailErrorBound:Math.max(0,1-sum)};
  }
  function lineProbability(posterior,distribution,row){
    const line=Number(row.line),cut=line-posterior.current;
    const pUnder=distribution.at(Math.ceil(cut)-1);
    const pPush=Number.isInteger(cut)&&cut>=0?(distribution.pmf[cut]||0):0;
    const pOver=Math.max(0,1-pUnder-pPush),decisive=pOver+pUnder;
    const conditionalOver=decisive?pOver/decisive:.5;
    return {...row,pOver,pUnder,pPush,
      direction:conditionalOver>=.57?"ТБ":conditionalOver<=.43?"ТМ":"без выраженного преимущества",
      edge:Math.abs(conditionalOver-.5)};
  }
  // Keep the public entry point for archive replay compatibility; there are no simulations now.
  function simulateQuarter(posterior,lines,seedText,n){
    const distribution=predictiveDistribution(posterior);
    if(!distribution)return null;
    return {simulations:0,calculation:"analytic_negative_binomial",mu:posterior.expectedRemaining,
      variance:(posterior.scoringUnit??1)*posterior.expectedRemaining+posterior.meanUncertaintyVariance,meanUncertaintyVariance:posterior.meanUncertaintyVariance,
      meanUncertaintySd:posterior.meanUncertaintySd,
      low80:distribution.quantile(.10),high80:distribution.quantile(.90),
      low95:distribution.quantile(.025),high95:distribution.quantile(.975),
      probabilities:(lines||[]).filter(x=>x.line!=null&&Number.isFinite(Number(x.line)))
        .map(x=>lineProbability(posterior,distribution,x)),tailErrorBound:distribution.tailErrorBound,
      scoringUnit:posterior.scoringUnit??1,stressOnly:(posterior.scoringUnit??1)>1,
      method:posterior.method+" · точный расчёт",distribution:(posterior.scoringUnit??1)>1?
        "marked negative-binomial scoring stress":"negative-binomial predictive (Gamma-Poisson)"};
  }
  // Sensitivity, not independent model votes or calibration: the same observed game
  // with full history, the last completed period, and current-period-only exposure.
  function quarterSensitivity(posterior,lines){
    if(!posterior?.ready)return [];
    const base={current:posterior.current,elapsedSeconds:posterior.elapsedSeconds,
      periodLengthSeconds:posterior.periodMinutes*60};
    const variants=[{name:"Вся история матча",posterior}];
    const last=posterior.completedQuarterTotals?.slice(-1);
    if(last?.length)variants.push({name:"Последний завершённый период",
      posterior:quarterPosterior({...base,completedQuarterTotals:last})});
    variants.push({name:"Только текущий период",posterior:quarterPosterior({...base,completedQuarterTotals:[]})});
    // Marked scoring stress tests preserve the mean. They do not fit a multiplier
    // to losses: an event contributes 2 or 3 points, rather than one Poisson point.
    for(const scoringUnit of [2,3])variants.push({name:"Серии по "+scoringUnit+" очка",posterior:{...posterior,scoringUnit}});
    const history=posterior.completedQuarterTotals||[];
    if(history.length){
      // A low current segment need not persist. Test a return to the highest
      // already observed complete-period rate, without looking at any final.
      const alpha=.5+Math.max(...history),beta=posterior.periodMinutes;
      const expectedRemaining=alpha/beta*posterior.remainingMinutes;
      const meanUncertaintyVariance=expectedRemaining**2/alpha;
      variants.push({name:"Возврат к высокой наблюдавшейся результативности",posterior:{...posterior,alpha,beta,
        projection:posterior.current+expectedRemaining,expectedRemaining,meanUncertaintyVariance,meanUncertaintySd:Math.sqrt(meanUncertaintyVariance),
        varianceRemaining:3*expectedRemaining+meanUncertaintyVariance,scoringUnit:3}});
    }
    const signature=p=>[p.alpha,p.beta,p.scoringUnit??1].join(":");
    const seen=new Set([signature(posterior)]);
    return variants.filter(v=>{
      if(!v.posterior.ready||seen.has(signature(v.posterior)))return false;
      seen.add(signature(v.posterior));return true;
    }).map(v=>{
      const result=simulateQuarter(v.posterior,lines);
      return {name:v.name,projection:v.posterior.projection,interval80:[result.low80,result.high80],
        interval95:[result.low95,result.high95],probabilities:result.probabilities};
    });
  }
  function assessSensitivity(rows,variants){
    return (rows||[]).map(row=>{
      const direction=row.pOver>=row.pUnder?"ТБ":"ТМ";
      const checks=[{name:"Основной расчёт",row},...(variants||[]).map(v=>({name:v.name,
        row:v.probabilities.find(x=>Number(x.line)===Number(row.line))})).filter(v=>v.row)];
      const probabilities=checks.map(v=>direction==="ТБ"?v.row.pOver:v.row.pUnder);
      return {...row,robustness:{variantCount:checks.length,available:checks.length>=3,
        independentModels:false,calibrated:false,policy:"observed-rate-marked-scoring-stress-v1",
        directionStable:checks.every(v=>(v.row.pOver>=v.row.pUnder?"ТБ":"ТМ")===direction),
        probabilityMin:Math.min(...probabilities),probabilityMax:Math.max(...probabilities),
        variants:checks.map((v,i)=>({name:v.name,probability:probabilities[i]}))}};
    });
  }
  // Diagnostic change detection on two disjoint 90-second playing-time windows.
  // A score rate is not possession pace. Thresholds are research policy, not win probabilities.
  function detectScoringRegime(input){
    const unavailable=reason=>({available:false,status:"unknown",reason,calibrated:false});
    if(input.timerVerified!==true)return unavailable("таймер не подтверждён");
    const length=input.periodLengthSeconds,period=input.period,now=input.nowMs;
    if(!Number.isFinite(now)||!Number.isFinite(length)||length<300||!Number.isInteger(period)||period<1)
      return unavailable("не подтверждён формат");
    const rows=[];
    for(const r of input.snapshots||[]){
      if(Number(r.period)!==period)continue;
      const m=/^(\d{1,2}):([0-5]\d)$/.exec(String(r.clock||""));
      const t=Date.parse(r.captured_at||"");
      let pair=r.score;if(typeof pair==="string"){try{pair=JSON.parse(pair);}catch(_){continue;}}
      if(!m||!Number.isFinite(t)||t>now||!Array.isArray(pair)||pair.length!==2||
         !pair.every(x=>typeof x==="number"&&Number.isInteger(x)&&x>=0))continue;
      const remaining=Number(m[1])*60+Number(m[2]);
      if(remaining>length)continue;
      rows.push({t,elapsed:length-remaining,pair,total:pair[0]+pair[1],possessions:r.possessions});
    }
    rows.sort((a,b)=>a.t-b.t);
    const latest=rows.at(-1);
    if(!latest||now-latest.t>90000)return unavailable("нет свежих снимков");
    // Retain a local segment only; never bridge periods, clock reversals or score corrections.
    const segment=[];
    for(const r of rows.filter(r=>latest.t-r.t<=900000)){
      const prev=segment.at(-1);
      if(prev&&(r.elapsed<prev.elapsed||r.pair.some((x,j)=>x<prev.pair[j])||
         r.t-prev.t>90000||r.elapsed-prev.elapsed>(r.t-prev.t)/1000+5))segment.length=0;
      const last=segment.at(-1);
      if(last&&r.elapsed===last.elapsed){
        if(r.total!==last.total)segment[segment.length-1]=r;
        continue;
      }
      segment.push(r);
    }
    const nearest=(elapsed,limit=15)=>segment.filter(r=>Math.abs(r.elapsed-elapsed)<=limit)
      .sort((a,b)=>Math.abs(a.elapsed-elapsed)-Math.abs(b.elapsed-elapsed))[0];
    const end=segment.at(-1),mid=end&&nearest(end.elapsed-90),start=end&&nearest(end.elapsed-180),split=end&&nearest(end.elapsed-45);
    if(!end||now-end.t>90000)return unavailable("таймер приостановлен; нет нового игрового отрезка");
    if(segment.length<5||!start||!mid||!split)return unavailable("нужны два окна по 90 игровых секунд");
    const rate=(a,b)=>(b.total-a.total)*60/(b.elapsed-a.elapsed);
    const previousRate=rate(start,mid),recentRate=rate(mid,end),halfA=rate(mid,split),halfB=rate(split,end);
    const change=recentRate-previousRate,ratio=previousRate>0?recentRate/previousRate:null;
    // Compound scoring noise: allow three points per scoring event. This is a warning statistic.
    const se=Math.sqrt(3*((mid.total-start.total)/(mid.elapsed-start.elapsed)**2+
      (end.total-mid.total)/(end.elapsed-mid.elapsed)**2))*60;
    const z=se>0?Math.abs(change)/se:0;
    const relative=previousRate>0?Math.abs(change)/previousRate:recentRate>0?1:0;
    const candidate=relative>=.3&&z>=1.96;
    const persistent=change>0?halfA>previousRate&&halfB>previousRate:halfA<previousRate&&halfB<previousRate;
    const status=candidate?(persistent?(change>0?"rising":"falling"):"burst"):"stable";
    let possessionChange=null;
    if([start,mid,end].every(r=>typeof r.possessions==="number"&&Number.isFinite(r.possessions))&&
       start.possessions<=mid.possessions&&mid.possessions<=end.possessions){
      possessionChange={previous:(mid.possessions-start.possessions)*60/(mid.elapsed-start.elapsed),
        recent:(end.possessions-mid.possessions)*60/(end.elapsed-mid.elapsed)};
    }
    return {available:true,status,previousRate,recentRate,ratio,z,persistent,candidate,
      previousPoints:mid.total-start.total,recentPoints:end.total-mid.total,
      windowSeconds:90,metric:possessionChange?"scoring_and_possessions":"scoring_only",possessionChange,
      calibrated:false,policy:"disjoint-90s-marked-noise-v1"};
  }
  function regimeSensitivity(posterior,lines,regime){
    if(!posterior?.ready||!regime?.available||!regime.candidate||!Number.isFinite(posterior.remainingMinutes)||posterior.remainingMinutes<0)return [];
    return [["Недавняя результативность",regime.recentRate],["Возврат к предыдущему окну",regime.previousRate]].map(([name,rate])=>{
      const alpha=.5+rate*1.5,beta=1.5,expectedRemaining=alpha/beta*posterior.remainingMinutes;
      const variance=expectedRemaining**2/alpha;
      const result=simulateQuarter({...posterior,alpha,beta,expectedRemaining,
        meanUncertaintyVariance:variance,meanUncertaintySd:Math.sqrt(variance),scoringUnit:3},lines);
      return {name,projection:posterior.current+expectedRemaining,interval80:[result.low80,result.high80],
        interval95:[result.low95,result.high95],probabilities:result.probabilities};
    });
  }
  // Separate future shot volume from conversion. Completed periods are a disjoint
  // historical sample; cumulative totals are never counted twice.
  function possessionRemainder(input){
    const no=reason=>({ready:false,reason,calibrated:false});
    const keys=["twoMade","twoAtt","threeMade","threeAtt","ftm","fta","oreb","tov"];
    const valid=b=>b&&keys.every(k=>typeof b[k]==="number"&&Number.isInteger(b[k])&&b[k]>=0)&&
      b.twoMade<=b.twoAtt&&b.threeMade<=b.threeAtt&&b.ftm<=b.fta;
    const points=b=>2*b.twoMade+3*b.threeMade+b.ftm;
    const poss=b=>b.twoAtt+b.threeAtt+.44*b.fta-b.oreb+b.tov;
    if(!Array.isArray(input.score)||input.score.length!==2||!input.score.every(x=>Number.isInteger(x)&&x>=0)||
       !Number.isFinite(input.elapsedSeconds)||!Number.isFinite(input.totalSeconds)||
       input.elapsedSeconds<=0||input.elapsedSeconds>=input.totalSeconds)return no("неверный счёт или горизонт");
    if(!valid(input.home)||!valid(input.away)||points(input.home)!==input.score[0]||points(input.away)!==input.score[1])
      return no("бросковая статистика не согласована со счётом");
    const periods=input.completedPeriods;
    if(!Array.isArray(periods)||!periods.length)return no("нет полных бросковых данных завершённых периодов");
    const prior={home:Object.fromEntries(keys.map(k=>[k,0])),away:Object.fromEntries(keys.map(k=>[k,0]))};
    let priorSeconds=0;
    for(let i=0;i<periods.length;i++){
      const r=periods[i];
      if(r.period!==i+1||!Number.isFinite(r.durationSeconds)||r.durationSeconds<=0||
         !valid(r.home)||!valid(r.away)||!Array.isArray(r.score)||points(r.home)!==r.score[0]||points(r.away)!==r.score[1])
        return no("пробел или конфликт статистики завершённых периодов");
      priorSeconds+=r.durationSeconds;
      for(const side of ["home","away"])for(const k of keys)prior[side][k]+=r[side][k];
    }
    const segmentSeconds=input.elapsedSeconds-priorSeconds;
    if(segmentSeconds<180)return no("нужно не менее 3 минут текущего периода для отдельной оценки владений");
    const remainingMinutes=(input.totalSeconds-input.elapsedSeconds)/60,teams={};
    for(const side of ["home","away"]){
      const b=input[side],a=prior[side],segment=Object.fromEntries(keys.map(k=>[k,b[k]-a[k]]));
      if(!valid(segment))return no("накопительная статистика меньше завершённых периодов");
      const n=poss(segment),rate=n/(segmentSeconds/60);
      if(!(n>0)||rate<.15||rate>2.25)return no("некорректный темп отдельного периода");
      // Jeffreys beta posterior; prior + current disjoint sample equals cumulative
      // attempts once. Recent shot mix can change without assuming hot shooting persists.
      const probability=(made,attempts)=>(made+.5)/(attempts+1);
      const rates={two:probability(b.twoMade,b.twoAtt),three:probability(b.threeMade,b.threeAtt),free:probability(b.ftm,b.fta)};
      const ppp=(2*segment.twoAtt*rates.two+3*segment.threeAtt*rates.three+segment.fta*rates.free)/n;
      if(!Number.isFinite(ppp)||ppp<.2||ppp>2.5)return no("эффективность команды вне проверяемой области");
      teams[side]={rate,ppp,conversion:rates,segmentPossessions:n,segmentPoints:points(segment),
        priorPossessions:poss(a),priorPoints:points(a)};
    }
    const rate=(teams.home.rate+teams.away.rate)/2;
    if(Math.abs(teams.home.rate-teams.away.rate)/Math.max(teams.home.rate,teams.away.rate)>.3)
      return no("несогласованные владения команд в текущем периоде");
    const expectedRemainingPossessions=rate*remainingMinutes;
    const expectedRemaining=expectedRemainingPossessions*(teams.home.ppp+teams.away.ppp);
    return {ready:true,current:input.score[0]+input.score[1],projection:input.score[0]+input.score[1]+expectedRemaining,
      expectedRemaining,expectedRemainingPossessions,pointsPerPossession:teams.home.ppp+teams.away.ppp,
      possPerMinute:rate,teams,segmentSeconds,priorSeconds,calibrated:false,predictionIncludesOvertime:false,
      method:"recent-period shot volume × pooled beta shooting conversion",policy:"possession-shotmix-beta-v1"};
  }
  const MODEL_VERSION="regulation-research-v8-possession-shotmix";
  const REASONS={context:"Не подтверждены дополнительные данные для уверенного допуска",regime:"Обнаружено изменение результативности; прежний темп нестабилен",data:"Не подтверждены свежесть, счёт или таймер",window:"Вне окна отбора",preliminary:"Предварительный расчёт",
    probabilities:"Некорректное распределение",rules:"Правила рынка не подтверждены",overtime:"Прогноз основного времени несовместим с рынком с ОТ",
    prices:"Не подтверждены обе цены ТБ/ТМ",suspended:"Рынок приостановлен PARI",quote:"Линия устарела или время котировки неизвестно",
    calibration:"Вероятности не калиброваны на независимых матчах",validation:"Преимущество не подтверждено независимой проверкой",
    edge:"Нет положительного преимущества с учётом погрешности",threshold:"Оценка модели ниже 65%"};
  // These are diagnostic prices, not calibrated probabilities or demonstrated edge.
  function marketComparison(row,market){
    if(!market||market.pricesVerified!==true||![market.overOdds,market.underOdds].every(x=>typeof x==="number"&&Number.isFinite(x)&&x>1))return null;
    const overImplied=1/market.overOdds,underImplied=1/market.underOdds,sum=overImplied+underImplied;
    const direction=row.pOver>=row.pUnder?"ТБ":"ТМ",pWin=Math.max(row.pOver,row.pUnder),pPush=row.pPush||0;
    const odds=direction==="ТБ"?market.overOdds:market.underOdds;
    return {direction,odds,pWin,pPush,overround:sum-1,noVigOver:overImplied/sum,noVigUnder:underImplied/sum,
      breakEvenConditional:1/odds,modelExpectedReturn:pWin*odds+pPush-1};
  }
  function evaluateSignals(input){
    const age=Number(input.ageSeconds),period=Number(input.period);
    const periodCount=Number(input.periodCount??4),isHalves=periodCount===2&&input.periodLengthSeconds===1200;
    const badData=input.ageSeconds==null||!Number.isFinite(age)||age<0||age>90||
       input.timerVerified!==true||input.scoreValid!==true||input.clockValid!==true||
       ![2,4].includes(periodCount)||period<1||period>periodCount||!Number.isInteger(period)||
       (periodCount===2&&!isHalves)||!Number.isFinite(input.periodLengthSeconds)||
       !Number.isFinite(input.elapsedSeconds)||input.elapsedSeconds<0||input.elapsedSeconds>=input.periodLengthSeconds;
    const result=[];
    for(const target of [isHalves?"half":"quarter","match"]){
      const f=input[target];
      if(!f?.ready)continue;
      for(const x of f.probabilities||[]){
        const reasons=[];
        if(badData)reasons.push("data");
        const modelInputsReady=f.requiredStatisticsReady!==false;
        if(!modelInputsReady)reasons.push('data');
        // Preserve the final-eight-minutes match horizon in a 2x20 game.
        // Half watchlists use 8-16 minutes; this is a research policy, not a
        // transfer of validated quarter performance to halves.
        if(target==="match"?(period!==periodCount||input.elapsedSeconds<(isHalves?720:120)):
           (input.elapsedSeconds<(isHalves?480:240)||input.elapsedSeconds>(isHalves?960:480)))reasons.push("window");
        if(f.preliminary)reasons.push("preliminary");
        if(input.statisticsReady!==true)reasons.push("context");
        if(input.regime?.candidate)reasons.push("regime");
        const values=[x.pOver,x.pUnder,x.pPush??0];
        const valid=x.line!=null&&Number.isFinite(Number(x.line))&&!x.stale&&
          (f.current==null||Number(x.line)>Number(f.current))&&
          values.every(v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1)&&
          Math.abs(values.reduce((a,b)=>a+b,0)-1)<0.002;
        if(!valid)continue;
        const market=x.market||{},quoteAge=(Number(input.nowMs)-Date.parse(market.quotedAtUtc||""))/1000;
        if(market.rulesConfirmed!==true||!market.ruleSource||typeof market.includesOvertime!=="boolean")reasons.push("rules");
        else if(market.includesOvertime!==false)reasons.push("overtime");
        // Both current models target regulation only. No unvalidated OT adjustment is invented.
        const priceComparison=marketComparison(x,market);
        if(!priceComparison)reasons.push("prices");
        const comparison=market.rulesConfirmed===true&&market.includesOvertime===false?priceComparison:null;
        if(!Number.isFinite(quoteAge)||quoteAge<0||quoteAge>90||market.suspended==null)reasons.push("quote");
        if(market.suspended===true)reasons.push("suspended");
        // Current model has no independently fitted calibrator. A feed-supplied flag cannot promote it.
        reasons.push("calibration","validation");
        const probability=Math.max(x.pOver,x.pUnder);
        if(probability<.65)reasons.push("threshold");
        if(comparison&&comparison.modelExpectedReturn<=0)reasons.push("edge");
        const critical=reasons.some(r=>!["calibration","validation","threshold","edge"].includes(r));
        result.push({target,period,line:Number(x.line),direction:x.pOver>=x.pUnder?"ТБ":"ТМ",probability,
          pOver:x.pOver,pUnder:x.pUnder,pPush:x.pPush||0,push:x.pPush||0,robustness:x.robustness||null,
          status:critical?"shadow":"research",priority:false,calibrated:false,modelVersion:MODEL_VERSION,
          calculationAvailable:!badData&&modelInputsReady,calculationModel:f.model||'score_clock',
          calculationMissing:f.requiredStatisticsMissing||[],
          predictionIncludesOvertime:false,regime:input.regime||null,statisticsReady:input.statisticsReady===true,statisticsMissing:input.statisticsMissing||[],market,comparison,reasonCodes:reasons,reasons:reasons.map(r=>REASONS[r])});
      }
    }
    return result.sort((a,b)=>b.probability-a.probability||(a.target<b.target?-1:a.target>b.target?1:0)||a.line-b.line);
  }
  function selectPrioritySignals(input){return evaluateSignals(input).filter(x=>x.priority===true);}
  function selectCalculatedDirections(evaluations){
    const selected=[];
    for(const target of ['quarter','half','match']){
      const rows=(evaluations||[]).filter(x=>x.target===target&&x.calculationAvailable===true&&
        !x.reasonCodes.some(r=>['data','preliminary','probabilities','rules','overtime','prices','quote','suspended'].includes(r)))
        .sort((a,b)=>a.line-b.line);
      if(rows.length)selected.push({...rows[Math.floor((rows.length-1)/2)],role:'model_calculation',priority:false,calibrated:false});
    }
    return selected;
  }
  // Watchlist only. Never feeds selectPrioritySignals or Telegram delivery.
  function selectResearchCandidates(evaluations){
    const eligible=(evaluations||[]).filter(x=>!x.reasonCodes.some(r=>["data","window","preliminary","probabilities","suspended"].includes(r)));
    const selected=[];
    for(const target of ["match","quarter","half"]){
      const rows=eligible.filter(x=>x.target===target).sort((a,b)=>a.line-b.line);
      if(!rows.length)continue;
      // Pick the central quoted line BEFORE inspecting probabilities. Alternative
      // lines far from the score must not win a ranking just because they pay less.
      const row=rows[Math.floor((rows.length-1)/2)],r=row.robustness;
      if(!r?.available||!r.directionStable||r.probabilityMin<.65)continue;
      if(row.comparison&&row.comparison.modelExpectedReturn<=0)continue;
      if(row.comparison&&r.probabilityMin*row.comparison.odds-1<=0)continue;
      selected.push({...row,watchlist:true,priority:false,calibrated:false,
        rankingProbability:r.probabilityMin,marketReady:!row.reasonCodes.some(x=>["rules","overtime","prices","quote","suspended"].includes(x))});
    }
    return selected.sort((a,b)=>b.rankingProbability-a.rankingProbability);
  }
  function summarizeSignals(evaluations){
    const reasons={};
    const missing={};
    for(const row of evaluations||[])for(const metric of new Set(row.statisticsMissing||[]))missing[metric]=(missing[metric]||0)+1;
    for(const row of evaluations||[])for(const reason of new Set(row.reasonCodes||[]))reasons[reason]=(reasons[reason]||0)+1;
    const inputCodes=["data","probabilities","prices","quote","rules"];
    const modelCodes=["calibration","validation","overtime","context","regime"];
    const selectionCodes=["window","preliminary","threshold","edge","suspended"];
    const counts=codes=>Object.entries(reasons).filter(([code])=>codes.includes(code))
      .map(([code,lines])=>({code,lines,label:REASONS[code]||code}));
    return {evaluatedLines:(evaluations||[]).length,
      calculationReadyLines:(evaluations||[]).filter(x=>x.calculationAvailable===true).length,
      comparableCalculationLines:(evaluations||[]).filter(x=>x.calculationAvailable===true&&!x.reasonCodes.some(r=>['data','rules','overtime','prices','quote','suspended'].includes(r))).length,
      missingStatistics:Object.entries(missing).map(([metric,lines])=>({metric,lines})),
      confidentSignals:(evaluations||[]).filter(x=>x.priority).length,
      inputReadyLines:(evaluations||[]).filter(x=>!x.reasonCodes.some(c=>inputCodes.includes(c))).length,
      blockerGroups:{inputs:counts(inputCodes),model:counts(modelCodes),selection:counts(selectionCodes)},
      automaticPriorityEnabled:false,calibrated:false,
      reasons:Object.entries(reasons).map(([code,lines])=>({code,lines,label:REASONS[code]||code})).sort((a,b)=>b.lines-a.lines||(a.code<b.code?-1:a.code>b.code?1:0))};
  }
  return {selectCalculatedDirections,possessionRemainder,detectScoringRegime,regimeSensitivity,quarterPosterior,simulateQuarter,quarterSensitivity,assessSensitivity,selectResearchCandidates,
    summarizeSignals,selectPrioritySignals,evaluateSignals,marketComparison,MODEL_VERSION};
});
