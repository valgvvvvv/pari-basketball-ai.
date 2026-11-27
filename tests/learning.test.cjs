const assert=require('node:assert/strict');
const ml=require('../static/learning.js');
const p={center:10,state:{current:9},residual_sample:[-100,0,1]};
assert.deepEqual(ml.empirical(p,10),{pOver:1/3,pUnder:1/3,pPush:1/3,direction:'нет выраженного направления',calibrated:false});
assert.equal(ml.empirical(p,9.5).pPush,0);
assert.equal(ml.empirical({...p,center:NaN},10),null);
for(const code of ['data','rules','overtime','prices','quote','suspended']){
  assert.equal(ml.comparable([{target:'match',line:10,reasonCodes:[code]}],'match',10),null);
}
assert.equal(ml.comparable([{target:'match',line:9,reasonCodes:['calibration']},{target:'match',line:11,reasonCodes:[]}],'match',11).line,11);
assert.equal(ml.summary({learning:{match:{status:'waiting',reason:'<img>'}}}), '');
console.log('learning probabilities, push, market compatibility and isolation: OK');
