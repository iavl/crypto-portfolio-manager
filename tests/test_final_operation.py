import unittest
from crypto_portfolio.engine.operation import build_final_operation


def buy(symbol, value):
    return dict(symbol=symbol, action="INCREASE", amount_usd=value)


def funding(symbol, value):
    return dict(symbol=symbol, action="REDUCE", amount_usd=value, action_reason="ALLOCATION_OVERWEIGHT")


def plan(approved, planned):
    return dict(action="INCREASE" if planned else "WAIT", approved_amount_usd=approved,
                planned_amount_usd=planned, reserve_amount_usd=approved-planned,
                reserve_policy="PULLBACK_RESERVE" if planned else "GATE_HOLD")


class FinalOperationTests(unittest.TestCase):
    def test_mixed_wait_releases_only_unused_funding(self):
        result = build_final_operation([buy("BNB",400),buy("AAVE",200),funding("USDT",600)],
                                      {"BNB":plan(400,0),"AAVE":plan(200,200)},stable_symbols=["USDT"])
        self.assertEqual(result.stable_funding_amount_usd,200)
        self.assertEqual(result.planned_buy_amount_usd,200)
        self.assertEqual(result.residual_stable_change_usd,-200)

    def test_partial_reserve_and_split_stables(self):
        result = build_final_operation([buy("BNB",400),funding("USDT",300),funding("USDC",100)],
                                      {"BNB":plan(400,100)},stable_symbols=["USDT","USDC"])
        self.assertEqual({r.symbol:r.proposed_amount_usd for r in result.execution_actions},{"BNB":100,"USDT":75,"USDC":25})

    def test_hard_sales_survive_wait(self):
        actions=[buy("BNB",400),funding("USDT",100),dict(symbol="AAVE",action="EXIT",amount_usd=300,action_reason="THESIS_BROKEN")]
        result=build_final_operation(actions,{"BNB":plan(400,0)},stable_symbols=["USDT"])
        self.assertEqual(result.independent_sale_amount_usd,300)
        self.assertEqual(result.stable_funding_amount_usd,0)
        self.assertEqual(result.decision,"PROPOSED")

    def test_stable_risk_exit_is_not_ordinary_funding(self):
        result=build_final_operation([buy("BNB",400),dict(symbol="USDT",action="EXIT",amount_usd=400,action_reason="EVENT_RISK")],
                                     {"BNB":plan(400,0)},stable_symbols=["USDT"])
        self.assertEqual(result.execution_actions[1].proposed_amount_usd,400)

    def test_duplicate_and_invalid_inputs(self):
        for actions,plans in [([buy("BNB",400)]*2,{"BNB":plan(400,0)}),
                              ([buy("BNB",400)],{"BNB":plan(401,0)}),
                              ([buy("BNB",float("nan"))],{"BNB":plan(400,0)}),
                              ([buy("BNB",400)],{}),
                              ([buy("BNB",400)],{"BNB":{**plan(400,0), "symbol":"ETH"}})]:
            with self.assertRaises(ValueError):
                build_final_operation(actions,plans)

    def test_unmatched_final_buy_funding_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "matched funding"):
            build_final_operation(
                [buy("BNB", 200), funding("USDT", 50)],
                {"BNB": plan(200, 200)},
                stable_symbols=["USDT"],
            )

    def test_all_wait_does_not_claim_fills(self):
        value=build_final_operation([buy("BNB",400),funding("USDT",400)],{"BNB":plan(400,0)},stable_symbols=["USDT"]).as_dict()
        self.assertEqual(value['decision'],'WAIT')
        self.assertEqual(value['confirmation_status'],'NOT_CONFIRMED')
        self.assertEqual(value['stable_funding_amount_usd'],0)
