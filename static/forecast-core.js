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
    const {alpha,beta,remainingMinutes:t}=posterior,mu=alpha*t/beta;
    let mass=Math.exp(-alpha*Math.log1p(t/beta)),sum=mass;
    if(!(mass>0))return null;
    const cdf=[sum],pmf=[mass],ratio=t/(beta+t);
    for(let k=1;k<20000&&(sum<1-1e-13||k<=mu);k++){
      mass*=((alpha+k-1)/k)*ratio;
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
      variance:posterior.varianceRemaining,meanUncertaintyVariance:posterior.meanUncertaintyVariance,
      meanUncertaintySd:posterior.meanUncertaintySd,
      low80:distribution.quantile(.10),high80:distribution.quantile(.90),
      low95:distribution.quantile(.025),high95:distribution.quantile(.975),
      probabilities:(lines||[]).filter(x=>x.line!=null&&Number.isFinite(Number(x.line)))
        .map(x=>lineProbability(posterior,distribution,x)),tailErrorBound:distribution.tailErrorBound,
      method:posterior.method+" · точный расчёт",distribution:"negative-binomial predictive (Gamma-Poisson)"};
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
    return variants.filter(v=>v.posterior.ready).map(v=>({name:v.name,
      projection:v.posterior.projection,probabilities:simulateQuarter(v.posterior,lines)?.probabilities||[]}));
  }
  function assessSensitivity(rows,variants){
    return (rows||[]).map(row=>{
      const direction=row.pOver>=row.pUnder?"ТБ":"ТМ";
      const checks=[{name:"Основной расчёт",row},...(variants||[]).map(v=>({name:v.name,
        row:v.probabilities.find(x=>Number(x.line)===Number(row.line))})).filter(v=>v.row)];
      const probabilities=checks.map(v=>direction==="ТБ"?v.row.pOver:v.row.pUnder);
      return {...row,robustness:{variantCount:checks.length,available:checks.length>=3,
        directionStable:checks.every(v=>(v.row.pOver>=v.row.pUnder?"ТБ":"ТМ")===direction),
        probabilityMin:Math.min(...probabilities),probabilityMax:Math.max(...probabilities),
        variants:checks.map((v,i)=>({name:v.name,probability:probabilities[i]}))}};
    });
  }
  const MODEL_VERSION="regulation-research-v4-pari-markets";
  const REASONS={data:"Не подтверждены свежесть, счёт или таймер",window:"Вне окна отбора",preliminary:"Предварительный расчёт",
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
    const badData=input.ageSeconds==null||!Number.isFinite(age)||age<0||age>90||
       input.timerVerified!==true||input.scoreValid!==true||input.clockValid!==true||
       ![1,2,3,4].includes(period)||!Number.isFinite(input.periodLengthSeconds)||
       !Number.isFinite(input.elapsedSeconds)||input.elapsedSeconds<0||input.elapsedSeconds>=input.periodLengthSeconds;
    const result=[];
    for(const target of ["quarter","match"]){
      const f=input[target];
      if(!f?.ready)continue;
      for(const x of f.probabilities||[]){
        const reasons=[];
        if(badData)reasons.push("data");
        if(target==="match"?(period!==4||input.elapsedSeconds<120):(input.elapsedSeconds<240||input.elapsedSeconds>480))reasons.push("window");
        if(f.preliminary)reasons.push("preliminary");
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
          predictionIncludesOvertime:false,market,comparison,reasonCodes:reasons,reasons:reasons.map(r=>REASONS[r])});
      }
    }
    return result.sort((a,b)=>b.probability-a.probability||(a.target<b.target?-1:a.target>b.target?1:0)||a.line-b.line);
  }
  function selectPrioritySignals(input){return evaluateSignals(input).filter(x=>x.priority===true);}
  // Watchlist only. Never feeds selectPrioritySignals or Telegram delivery.
  function selectResearchCandidates(evaluations){
    const eligible=(evaluations||[]).filter(x=>!x.reasonCodes.some(r=>["data","window","preliminary","probabilities","suspended"].includes(r)));
    const selected=[];
    for(const target of ["match","quarter"]){
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
    for(const row of evaluations||[])for(const reason of new Set(row.reasonCodes||[]))reasons[reason]=(reasons[reason]||0)+1;
    const inputCodes=["data","probabilities","prices","quote","rules"];
    const modelCodes=["calibration","validation","overtime"];
    const selectionCodes=["window","preliminary","threshold","edge","suspended"];
    const counts=codes=>Object.entries(reasons).filter(([code])=>codes.includes(code))
      .map(([code,lines])=>({code,lines,label:REASONS[code]||code}));
    return {evaluatedLines:(evaluations||[]).length,
      confidentSignals:(evaluations||[]).filter(x=>x.priority).length,
      inputReadyLines:(evaluations||[]).filter(x=>!x.reasonCodes.some(c=>inputCodes.includes(c))).length,
      blockerGroups:{inputs:counts(inputCodes),model:counts(modelCodes),selection:counts(selectionCodes)},
      automaticPriorityEnabled:false,calibrated:false,
      reasons:Object.entries(reasons).map(([code,lines])=>({code,lines,label:REASONS[code]||code})).sort((a,b)=>b.lines-a.lines||(a.code<b.code?-1:a.code>b.code?1:0))};
  }
  return {quarterPosterior,simulateQuarter,quarterSensitivity,assessSensitivity,selectResearchCandidates,
    summarizeSignals,selectPrioritySignals,evaluateSignals,marketComparison,MODEL_VERSION};
});
