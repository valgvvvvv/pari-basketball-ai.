(function(root,factory){
  const api=factory();
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  if(root)root.PARIForecastCore=api;
})(typeof globalThis!=="undefined"?globalThis:null,function(){
  "use strict";
  function quarterPosterior(input){
    const current=Number(input.current),elapsedSeconds=Number(input.elapsedSeconds),periodLengthSeconds=Number(input.periodLengthSeconds);
    const completed=(Array.isArray(input.completedQuarterTotals)?input.completedQuarterTotals:[])
      .map(Number).filter(x=>Number.isFinite(x)&&x>=0);
    const aggregatePriorPoints=Number(input.priorPoints),aggregatePriorExposureSeconds=Number(input.priorExposureSeconds);
    const hasAggregatePrior=Number.isFinite(aggregatePriorPoints)&&aggregatePriorPoints>=0&&
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
  function makeRng(seedText){
    let seed=2166136261;
    for(const ch of String(seedText||""))seed=Math.imul(seed^ch.charCodeAt(0),16777619)>>>0;
    return ()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed+1)/4294967297;};
  }
  function sampleGamma(shape,random){
    if(shape<1)return sampleGamma(shape+1,random)*Math.pow(Math.max(random(),1e-15),1/shape);
    const d=shape-1/3,c=1/Math.sqrt(9*d);
    for(let i=0;i<100;i++){
      const u1=Math.max(random(),1e-15),u2=random();
      const x=Math.sqrt(-2*Math.log(u1))*Math.cos(2*Math.PI*u2);
      const v0=1+c*x;if(v0<=0)continue;
      const v=v0*v0*v0,u=random();
      if(u<1-0.0331*x*x*x*x||Math.log(Math.max(u,1e-15))<0.5*x*x+d*(1-v+Math.log(v)))return d*v;
    }
    return shape;
  }
  function samplePoisson(lambda,random){
    if(lambda<=0)return 0;
    if(lambda>35){
      const u=Math.max(random(),1e-15),v=random(),z=Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);
      return Math.max(0,Math.round(lambda+Math.sqrt(lambda)*z));
    }
    const limit=Math.exp(-lambda);let product=1,k=0;
    do{k++;product*=random();}while(product>limit&&k<1000);
    return k-1;
  }
  function quantile(sorted,q){return sorted[Math.min(sorted.length-1,Math.max(0,Math.ceil(q*sorted.length)-1))];}
  function simulateQuarter(posterior,lines,seedText,n){
    if(!posterior||!posterior.ready)return null;
    const count=Math.max(1000,Math.min(100000,Number(n)||100000)),random=makeRng(seedText),outcomes=new Array(count);
    for(let i=0;i<count;i++){
      const rate=sampleGamma(posterior.alpha,random)/posterior.beta;
      outcomes[i]=posterior.current+samplePoisson(rate*posterior.remainingMinutes,random);
    }
    outcomes.sort((a,b)=>a-b);
    const probabilities=(Array.isArray(lines)?lines:[]).filter(x=>Number.isFinite(Number(x.line))).map(x=>{
      const line=Number(x.line);let over=0,under=0,push=0;
      for(const total of outcomes){if(total>line)over++;else if(total<line)under++;else push++;}
      const pOver=over/count,pUnder=under/count,pPush=push/count,decisive=over+under;
      const conditionalOver=decisive?over/decisive:0.5;
      return {...x,pOver,pUnder,pPush,
        direction:conditionalOver>=0.57?"ТБ":conditionalOver<=0.43?"ТМ":"без выраженного преимущества",
        edge:Math.abs(conditionalOver-0.5)};
    });
    return {simulations:count,mu:posterior.expectedRemaining,variance:posterior.varianceRemaining,
      meanUncertaintyVariance:posterior.meanUncertaintyVariance,meanUncertaintySd:posterior.meanUncertaintySd,
      low80:quantile(outcomes,.10),high80:quantile(outcomes,.90),low95:quantile(outcomes,.025),high95:quantile(outcomes,.975),
      probabilities,method:posterior.method,distribution:"negative-binomial predictive (Gamma-Poisson)"};
  }
  function selectPrioritySignals(input){
    const age=Number(input.ageSeconds),period=Number(input.period);
    if(input.ageSeconds==null||!Number.isFinite(age)||age<0||age>90||
       input.timerVerified!==true||input.scoreValid!==true||input.clockValid!==true||
       ![1,2,3,4].includes(period))return [];
    const result=[];
    for(const target of ["quarter","match"]){
      const f=input[target];
      if(!f?.ready||f.preliminary)continue;
      if(target==="match"&&(period!==4||!Number.isFinite(input.elapsedSeconds)||input.elapsedSeconds<120||
          !Number.isFinite(input.periodLengthSeconds)||input.elapsedSeconds>=input.periodLengthSeconds))continue;
      if(target==="quarter"&&(!Number.isFinite(input.elapsedSeconds)||input.elapsedSeconds<240||input.elapsedSeconds>480))continue;
      const candidates=(f.probabilities||[]).filter(x=>{
        const values=[x.pOver,x.pUnder,x.pPush??0];
        return x.line!=null&&Number.isFinite(Number(x.line))&&!x.stale&&
          (f.current==null||Number(x.line)>Number(f.current))&&
          values.every(v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1)&&
          Math.abs(values.reduce((a,b)=>a+b,0)-1)<0.002;
      }).map(x=>({target,period,line:Number(x.line),direction:x.pOver>=x.pUnder?"ТБ":"ТМ",
        probability:Math.max(x.pOver,x.pUnder),push:x.pPush||0}));
      candidates.sort((a,b)=>b.probability-a.probability||a.line-b.line);
      if(candidates[0]?.probability>=0.65)result.push(candidates[0]);
    }
    return result.sort((a,b)=>b.probability-a.probability||a.target.localeCompare(b.target));
  }
  return {quarterPosterior,simulateQuarter,selectPrioritySignals};
});
