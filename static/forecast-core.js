(function(root){
  "use strict";
  function seededRandom(seedText){
    let seed=2166136261;
    for(const ch of String(seedText||"forecast"))seed=Math.imul(seed^ch.charCodeAt(0),16777619)>>>0;
    return function(){seed=(Math.imul(seed,1664525)+1013904223)>>>0;return (seed+1)/4294967297;};
  }
  function normalRandom(random){
    const u=Math.max(random(),1e-12),v=random();
    return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);
  }
  function gammaSample(shape,scale,random){
    if(!(shape>0)||!(scale>0))return 0;
    if(shape<1)return gammaSample(shape+1,scale,random)*Math.pow(Math.max(random(),1e-12),1/shape);
    const d=shape-1/3,c=1/Math.sqrt(9*d);
    for(let i=0;i<1000;i++){
      const x=normalRandom(random),base=1+c*x;
      if(base<=0)continue;
      const v=base*base*base,u=random();
      if(u<1-0.0331*x*x*x*x||Math.log(Math.max(u,1e-12))<0.5*x*x+d*(1-v+Math.log(v)))return d*v*scale;
    }
    return shape*scale;
  }
  function poissonSample(lambda,random){
    if(!(lambda>0))return 0;
    if(lambda>100)return Math.max(0,Math.round(lambda+Math.sqrt(lambda)*normalRandom(random)));
    const limit=Math.exp(-lambda);let product=1,k=0;
    do{k++;product*=random();}while(product>limit&&k<1000);
    return k-1;
  }
  function quantile(sorted,q){
    return sorted[Math.min(sorted.length-1,Math.max(0,Math.ceil(q*sorted.length)-1))];
  }
  function simulateQuarter(input){
    const current=input.current==null||input.current===""?NaN:Number(input.current),periodSeconds=Number(input.periodSeconds),elapsedSeconds=input.elapsedSeconds==null||input.elapsedSeconds===""?NaN:Number(input.elapsedSeconds);
    const scores=(Array.isArray(input.priorQuarterTotals)?input.priorQuarterTotals:[])
      .filter(x=>x!=null&&x!=="").map(Number).filter(x=>Number.isFinite(x)&&x>=0);
    const simulations=Math.max(1000,Math.min(100000,Math.floor(Number(input.simulations)||100000)));
    if(!Number.isFinite(current)||current<0||!Number.isFinite(periodSeconds)||periodSeconds<300||
       !Number.isFinite(elapsedSeconds)||elapsedSeconds<0||elapsedSeconds>periodSeconds){
      return {ready:false,reason:"Нет подтверждённого счёта четверти или корректного времени."};
    }
    const priorPoints=scores.reduce((a,b)=>a+b,0);
    const priorMinutes=scores.length*periodSeconds/60;
    if(!scores.length&&elapsedSeconds<180)
      return {ready:false,reason:"В первой четверти пока мало наблюдений, а подходящего исторического prior нет."};
    const elapsedMinutes=elapsedSeconds/60,remainingMinutes=(periodSeconds-elapsedSeconds)/60;
    // Gamma-Poisson conjugate update. Same-game completed periods provide the
    // empirical prior exposure; with no prior, use Jeffreys' Gamma(1/2, 0) prior.
    const alpha=priorPoints+current+0.5;
    const beta=priorMinutes+elapsedMinutes;
    if(!(beta>0)||!(alpha>0))return {ready:false,reason:"Недостаточно наблюдений для оценки интенсивности."};
    const posteriorRate=alpha/beta;
    const expectedRemaining=posteriorRate*remainingMinutes;
    const expectedTotal=current+expectedRemaining;
    const random=seededRandom(input.seed||"quarter");
    const outcomes=new Array(simulations);
    for(let i=0;i<simulations;i++){
      const sampledRate=gammaSample(alpha,1/beta,random);
      outcomes[i]=current+poissonSample(sampledRate*remainingMinutes,random);
    }
    outcomes.sort((a,b)=>a-b);
    const lines=(Array.isArray(input.lines)?input.lines:[]).filter(x=>Number.isFinite(Number(x.line))).map(x=>{
      const line=Number(x.line);let over=0,under=0,push=0;
      for(const total of outcomes){if(total>line)over++;else if(total<line)under++;else push++;}
      const pOver=over/simulations,pUnder=under/simulations,pPush=push/simulations;
      const resolved=pOver+pUnder;
      const conditionalOver=resolved>0?pOver/resolved:null;
      return {...x,pOver,pUnder,pPush,conditionalOver,
        direction:conditionalOver==null?"возврат":conditionalOver>=0.57?"ТБ":conditionalOver<=0.43?"ТМ":"без преимущества",
        simulation_se:Math.sqrt(pOver*(1-pOver)/simulations)};
    });
    return {
      ready:true,model:"Gamma-Poisson predictive",modelVersion:"quarter-gp-v1",
      current,expectedRemaining,projection:expectedTotal,posteriorRate,
      posterior:{shape:alpha,rate:beta},prior:{source:scores.length?"завершённые четверти этого матча":"prior Jeffreys (без внешней лиги/командной истории)",completedPeriods:scores.length,points:priorPoints,exposureMinutes:priorMinutes},
      elapsedSeconds,remainingSeconds:periodSeconds-elapsedSeconds,elapsedMinutes,remainingMinutes,
      low80:quantile(outcomes,.10),high80:quantile(outcomes,.90),
      low95:quantile(outcomes,.025),high95:quantile(outcomes,.975),
      simulations,distribution:"negative-binomial predictive (Poisson rate integrated over Gamma posterior)",
      calibrationStatus:"не калибрована на независимой исторической выборке",
      probabilities:lines
    };
  }
  root.PARIForecastCore={simulateQuarter};
  if(typeof module!=="undefined"&&module.exports)module.exports=root.PARIForecastCore;
})(typeof globalThis!=="undefined"?globalThis:this);
