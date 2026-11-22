"""Independent log-gamma oracle for the frontend predictive probabilities."""
import json
import math
from pathlib import Path
import unittest

from py_mini_racer import mini_racer


class ExactPredictiveTests(unittest.TestCase):
    def test_probabilities_and_quantiles_against_log_gamma_pmf(self):
        cases = [(.5, 3, 7, 0), (91.5, 21.05, 8.95, 1),
                 (150.5, 35, 5, 20), (2, 4, 0, 12), (500, 30, 12, 40)]
        with mini_racer() as engine:
            engine.eval((Path(__file__).parents[1]/'static/forecast-core.js').read_text(encoding='utf-8'))
            for alpha, beta, remaining, current in cases:
                posterior = dict(ready=True, alpha=alpha, beta=beta, remainingMinutes=remaining,
                                 current=current, expectedRemaining=alpha*remaining/beta,
                                 method='oracle-fixture')
                lines = [dict(line=current+k/2) for k in range(-1, 801)]
                actual = json.loads(engine.eval('JSON.stringify(PARIForecastCore.simulateQuarter('+
                                                json.dumps(posterior)+','+json.dumps(lines)+'))'))
                if remaining:
                    p = beta/(beta+remaining)
                    pmf = [math.exp(math.lgamma(k+alpha)-math.lgamma(alpha)-math.lgamma(k+1)
                                    +alpha*math.log(p)+k*math.log1p(-p)) for k in range(1500)]
                else:
                    pmf = [1.0]+[0.0]*1499
                cdf = []
                for k, mass in enumerate(pmf):
                    cdf.append(math.fsum(pmf[:k+1]))
                for row in actual['probabilities']:
                    cut = float(row['line']-current)
                    under_index = math.ceil(cut)-1
                    under = cdf[under_index] if under_index >= 0 else 0
                    push = pmf[int(cut)] if cut >= 0 and cut.is_integer() else 0
                    self.assertAlmostEqual(row['pUnder'], under, delta=2e-11)
                    self.assertAlmostEqual(row['pPush'], push, delta=2e-11)
                    self.assertAlmostEqual(row['pOver'], 1-under-push, delta=2e-11)
                for name, q in [('low80', .1), ('high80', .9), ('low95', .025), ('high95', .975)]:
                    self.assertEqual(actual[name], current+next(k for k, v in enumerate(cdf) if v >= q))
                self.assertEqual(actual['simulations'], 0)
                self.assertLess(actual['tailErrorBound'], 1e-10)

    def test_geometric_special_case_and_seed_independence(self):
        with mini_racer() as engine:
            engine.eval((Path(__file__).parents[1]/'static/forecast-core.js').read_text(encoding='utf-8'))
            result = json.loads(engine.eval('''JSON.stringify((()=>{
              const p={ready:true,current:0,alpha:1,beta:1,remainingMinutes:1,expectedRemaining:1,method:'test'};
              return ['a','b'].map(seed=>PARIForecastCore.simulateQuarter(p,[{line:2},{line:2.5}],seed));
            })())'''))
            self.assertEqual(result[0], result[1])
            integer, half = result[0]['probabilities']
            self.assertEqual(integer['pUnder'], .75)
            self.assertEqual(integer['pPush'], .125)
            self.assertEqual(integer['pOver'], .125)
            self.assertEqual(half['pPush'], 0)
            self.assertEqual(half['pOver'], .125)
