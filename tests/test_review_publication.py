import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from crypto_portfolio.engine.confidence import calculate_decision_confidence, canonical_decision_scope, validate_confidence_calculation
from crypto_portfolio.models.confidence import DecisionConfidence
from crypto_portfolio.models.decision import Decision
from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.state.execution_artifacts import cache_execution_inputs, validate_execution_artifacts
from crypto_portfolio.state.review import finalize_review, superseded_status_events
from crypto_portfolio.engine.entry import build_entry_plan
from test_execution_engine import series, SpotPrice


class ReviewPublicationTests(unittest.TestCase):
    def test_confidence_receipt_rejects_forgery_and_wrong_scope(self):
        policy=resolve_policy()
        actions=[{'symbol':'BNB','action':'INCREASE','amount_usd':100}]
        weights={'BTC':.6,'USDT':.4}
        targets={'BTC':.6,'BNB':.1,'USDT':.3}
        scope=canonical_decision_scope(actions,weights,targets,stable_symbols=policy.stable_symbols)
        self.assertEqual(scope.exposure_weights,{'BNB':1})
        result=calculate_decision_confidence({name:.9 for name in policy.confidence['decision_component_weights']},scope=scope,policy=policy)
        validate_confidence_calculation(result,policy=policy,actions=actions,current_weights=weights,target_weights=targets)
        forged = result.as_dict()
        forged.update(score=.85, raw_score=.85)
        with self.assertRaisesRegex(ValueError,'components'):
            DecisionConfidence.from_mapping(forged)
        forged = result.as_dict()
        forged['scope'].update(
            relevant_assets=['BTC', 'BNB'], exposure_weights={'BTC': 1, 'BNB': 0}
        )
        with self.assertRaisesRegex(ValueError,'canonical'):
            validate_confidence_calculation(forged,policy=policy,actions=actions,current_weights=weights,target_weights=targets)
        forged = result.as_dict()
        forged['calculation_inputs']['policy_hash'] = '0' * 64
        with self.assertRaisesRegex(ValueError,'policy'):
            validate_confidence_calculation(forged,policy=policy,actions=actions,current_weights=weights,target_weights=targets)

    def test_raw_artifacts_rebuild_in_an_independent_process(self):
        policy=resolve_policy()
        spot=SpotPrice('ETH',282,'2026-01-01T08:00:00Z','synthetic','2026-01-01T08:00:00Z')
        with tempfile.TemporaryDirectory() as directory:
            snapshot=cache_execution_inputs(series(),spot,policy=policy,root=directory)
            plan=build_entry_plan('ETH',100,snapshot,'NORMAL','HIGH')
            validate_execution_artifacts({'ETH':plan},policy=policy,root=directory)
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan.as_dict()))
            code="""import json,sys
from crypto_portfolio.models.execution import ExecutionPlan
from crypto_portfolio.models.policy import resolve_policy
from crypto_portfolio.state.execution_artifacts import validate_execution_artifacts
p=ExecutionPlan.from_mapping(json.load(open(sys.argv[1])))
validate_execution_artifacts({'ETH':p},policy=resolve_policy(),root=sys.argv[2])
"""
            subprocess.run([sys.executable,'-c',code,str(path),directory],check=True,capture_output=True)
            (Path(directory)/'market-data'/'sha256'/f'{plan.ohlcv_hash}.json').unlink()
            with self.assertRaisesRegex(ValueError,'unable to load'):
                validate_execution_artifacts({'ETH':plan},policy=policy,root=directory)

    def test_formal_publication_uses_snapshot_value_and_same_operation(self):
        snapshot={'snapshot_id':'synthetic-snapshot','timestamp':'2026-01-01T00:00:00Z',
                  'positions':[{'symbol':'BTC','value_usd':600},{'symbol':'USDT','value_usd':400}]}
        decision=Decision('2026-01-01T01:00:00Z','NORMAL',{'BTC':.6,'USDT':.4},{'BTC':.6,'USDT':.4},based_on_snapshot_id='synthetic-snapshot',
                          nav_performance={'status':'AVAILABLE','current_drawdown':-.01})
        with tempfile.TemporaryDirectory() as directory:
            result=finalize_review(decision,snapshot,acquisition={'finalized':True},artifact_root=directory)
            self.assertEqual(result['review_diagnostics']['scenarios']['CURRENT']['portfolio_value_usd'],1000)
            self.assertEqual(result['decision_record']['operation'],result['operation'])
            self.assertEqual(result['operation']['decision'],'NO_TRADE')
            bundle=Path(directory)/'bundle.json'
            bundle.write_text(json.dumps({'decision':decision.as_dict(),'snapshot':snapshot,'acquisition':{'finalized':True}}))
            subprocess.run([sys.executable,'scripts/finalize_review.py',str(bundle),'--artifact-root',directory,'--output',str(Path(directory)/'output.json')],check=True,capture_output=True)
            bad = copy.deepcopy(snapshot)
            bad['snapshot_id'] = 'wrong'
            with self.assertRaisesRegex(ValueError,'identity'):
                finalize_review(decision,bad,acquisition={'finalized':True},artifact_root=directory)

    def test_superseded_plan_event_is_explicit_and_not_implicitly_written(self):
        decision = SimpleNamespace(
            timestamp='2026-01-02T00:00:00Z',
            execution_plans={'BNB': object()},
        )
        history = [
            {
                'decision_id': 'prior',
                'timestamp': '2026-01-01T00:00:00Z',
                'status': 'PENDING',
                'execution_plans': {'BNB': {'action': 'WAIT'}},
            }
        ]
        events = superseded_status_events(decision, history)
        self.assertEqual(events[0]['status'], 'NOT_EXECUTED')
        self.assertIn('SUPERSEDED_BY_REVIEW', events[0]['reason'])
