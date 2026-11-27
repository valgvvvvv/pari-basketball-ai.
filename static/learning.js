(function(root){
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const finite=n=>typeof n==='number'&&Number.isFinite(n);
  function empirical(p,line){
    if(!finite(p?.center)||!finite(line)||!finite(p?.state?.current)||!Array.isArray(p?.residual_sample)||!p.residual_sample.length||p.residual_sample.some(x=>!finite(x)))return null;
    const totals=p.residual_sample.map(x=>Math.max(p.state.current,Math.round(p.center+x)));
    const over=totals.filter(x=>x>line).length/totals.length,under=totals.filter(x=>x<line).length/totals.length;
    return {pOver:over,pUnder:under,pPush:totals.filter(x=>x===line).length/totals.length,direction:over===under?'нет выраженного направления':over>under?'ТБ':'ТМ',calibrated:false};
  }
  function comparable(rows,target,center){
    const blocked=new Set(['data','rules','overtime','prices','quote','suspended']);
    return rows.filter(x=>(target==='match'?x.target==='match':x.target!=='match')&&finite(x.line)&&!(x.reasonCodes||[]).some(c=>blocked.has(c)))
      .sort((a,b)=>Math.abs(a.line-center)-Math.abs(b.line-center))[0]||null;
  }
  function summary(ev){
    const available=Object.entries(ev.learning||{}).filter(([,p])=>p.status==='shadow');
    if(!available.length)return '';
    return '<span class="live-match-meta">Обучаемая модель · '+available.map(([t,p])=>(t==='match'?'Матч без ОТ':'Период')+': '+Number(p.center).toFixed(1)).join(' · ')+' · эксперимент</span>';
  }
  function render(ev,match,period){
    const panel=root.document?.getElementById('learningForecast');if(!panel)return;
    let evaluations=[];
    try{evaluations=root.signalEvaluations?root.signalEvaluations(ev,match,period):[];}catch(_e){}
    panel.innerHTML='<div class="two-panels">'+['match','period'].map(t=>{
      const p=ev.learning?.[t],title=t==='match'?'Матч · основное время':'Текущий период';
      if(p?.status!=='shadow')return '<div class="panel"><h3>'+title+'</h3><p>'+esc(p?.reason||'Ожидаем ответ обучаемой модели')+'</p></div>';
      const row=comparable(evaluations,t,p.center),prob=row?empirical(p,row.line):null;
      const baseline=finite(p.baseline_center)?'<p>Текущие формулы: '+p.baseline_center.toFixed(1)+' · разница '+(p.center-p.baseline_center).toFixed(1)+' оч.</p>':'';
      return '<div class="panel"><h3>'+title+'</h3><div class="forecast-number">'+Number(p.center).toFixed(1)+'</div><p>Осталось '+Number(p.expected_remaining).toFixed(1)+' оч. · диапазон '+p.interval80.map(x=>Number(x).toFixed(1)).join('–')+'</p>'+
        (prob?'<p>Линия '+row.line+' · '+esc(prob.direction)+' · оценки: ТБ '+(prob.pOver*100).toFixed(1)+'%, ТМ '+(prob.pUnder*100).toFixed(1)+'%, возврат '+(prob.pPush*100).toFixed(1)+'%. Не калиброваны.</p>':'<p>Нет подтверждённой совместимой линии. Числовой прогноз сохраняется.</p>')+
        baseline+'<p class="muted">Обучение: '+p.train_games+' матчей · проверка на более поздних: '+p.report.games+' · средняя ошибка '+p.report.mae.toFixed(1)+' оч. · простой темп '+p.report.tempo_mae.toFixed(1)+' оч.</p>'+
        '<p class="muted">'+(p.profile==='full'?'Применён профиль со статистикой бросков, потерь, подборов и командных фолов.':'Применён профиль счёта, времени, прошлых периодов и недавней результативности. Расширенная статистика пока не имеет обученных весов.')+'</p>'+
        '<details><summary>Границы проверки</summary><p>Историческое воспроизведение протоколов; это не подтверждённый результат в реальном лайве. Номинальный диапазон 80%, наблюдаемое покрытие на проверке '+(p.report.interval80_coverage*100).toFixed(1)+'%. Модель '+esc(p.model_version)+'. Диапазон не является гарантией. Все прогнозы сохраняются до исходов.</p></details></div>';
    }).join('')+'</div><p class="warn">Экспериментальная модель. Она не повышает уверенность основного сигнала автоматически.</p>';
  }
  async function status(){
    const panel=root.document?.getElementById('learningStatus');if(!panel)return;
    try{
      const r=await root.fetch('/api/learning/status',{cache:'no-store'});if(!r.ok)throw new Error();const d=await r.json();
      const labels={ncaa_m:'NCAA мужчины · 2×20',ncaa_w:'NCAA женщины · 4×10',nba:'NBA · 4×12'};
      panel.innerHTML='<p>Версия '+esc(d.version)+' · '+esc(d.method)+'. Все новые версии работают в экспериментальном режиме.</p><div class="table-wrap"><table><thead><tr><th>Лига / цель</th><th>Обучение</th><th>Калибровочная выборка</th><th>Поздняя проверка</th><th>Ошибка / простой темп</th></tr></thead><tbody>'+Object.entries(d.cohorts||{}).map(([k,m])=>'<tr><td>'+esc(labels[k.split(':')[0]]||k)+' · '+(k.split(':')[1]==='match'?'матч':'период')+' · '+esc(m.profile)+'</td><td>'+m.train_games+'</td><td>'+m.calibration_games+'</td><td>'+m.report.games+'</td><td>'+m.report.mae.toFixed(1)+' / '+m.report.tempo_mae.toFixed(1)+' оч.</td></tr>').join('')+'</tbody></table></div><p>'+esc(d.learning)+'</p><p class="muted">'+esc(d.limitation)+' '+esc(d.update_error||'')+'</p>';
      const c=d.collection||{};
      panel.innerHTML+='<p>Новые наблюдения: '+Number(c.frozen_observations||0)+' · матчей с независимо проверенным исходом: '+Number(c.independent_games||0)+'. Неподтверждённые исходы не участвуют в обучении.</p>';
      if(d.prospective_report?.length)panel.innerHTML+='<details><summary>Ошибки замороженных прогнозов в реальном лайве</summary>'+d.prospective_report.map(x=>'<p>'+esc(labels[x.cohort]||x.cohort)+' · '+esc(x.target)+' · версия '+esc(x.version)+' · матчей '+x.games+' · ошибка '+Number(x.mae).toFixed(1)+' оч.'+(finite(x.incumbent_mae)?' · на одинаковых '+x.comparable_incumbent_games+' матчах: обучаемая '+Number(x.comparable_challenger_mae).toFixed(1)+' / существующая '+Number(x.incumbent_mae).toFixed(1)+' оч.':' · сопоставимого расчёта основной модели пока нет')+'</p>').join('')+'</details>';
    }catch(_e){panel.textContent='Не удалось получить статус обучаемой модели. Основные расчёты продолжают работать.';}
  }
  const api={empirical,comparable,summary,render,status};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.PARILearning=api;
  if(root.document){status();root.setInterval(status,60000);}
})(typeof window!=='undefined'?window:globalThis);
