"""Risk-tier hysteresis (Strategy V2 Phase 4)."""

import unittest

from crypto_portfolio.engine.risk_tier import deterministic_risk_tier
from crypto_portfolio.models.policy import load_policy


def _tier(vol=0.8, btc_vol=0.5, beta=1.0, previous=None):
    return deterministic_risk_tier(
        asset_volatility=vol, btc_volatility=btc_vol, beta_to_btc=beta,
        previous_tier=previous, policy=load_policy(),
    )


class HysteresisTests(unittest.TestCase):
    def test_threshold_oscillation_does_not_flip_the_tier_daily(self):
        # beta crosses 1.5 -> 1.49 -> 1.51 around the enter threshold:
        # without hysteresis this flips daily; with the exit threshold at
        # 1.3 the tier entered once and stays.
        first = _tier(vol=0.5, btc_vol=0.5, beta=1.5)          # enters
        self.assertEqual(first["tier"], "high_beta")
        dip = _tier(vol=0.5, btc_vol=0.5, beta=1.49, previous="high_beta")
        self.assertEqual(dip["tier"], "high_beta")
        back = _tier(vol=0.5, btc_vol=0.5, beta=1.51, previous="high_beta")
        self.assertEqual(back["tier"], "high_beta")
        # Leaving requires BOTH signals below their exit thresholds.
        still_high = _tier(vol=1.4, btc_vol=0.5, beta=1.2, previous="high_beta")
        self.assertEqual(still_high["tier"], "high_beta")      # vol ratio 2.8 holds it
        exits = _tier(vol=0.6, btc_vol=0.5, beta=1.2, previous="high_beta")
        self.assertEqual(exits["tier"], "normal")              # ratio 1.2 < 1.3, beta < 1.3

    def test_volatility_band_has_the_same_hysteresis(self):
        entered = _tier(vol=0.85, btc_vol=0.5, beta=1.0)
        self.assertEqual(entered["tier"], "high_beta")          # ratio 1.7 >= 1.6
        held = _tier(vol=0.7, btc_vol=0.5, beta=1.0, previous="high_beta")
        self.assertEqual(held["tier"], "high_beta")             # ratio 1.4 > exit 1.3
        released = _tier(vol=0.62, btc_vol=0.5, beta=1.0, previous="high_beta")
        self.assertEqual(released["tier"], "normal")            # ratio 1.24 < exit

    def test_long_oscillation_sequence_is_stable(self):
        tier = None
        flips = 0
        for step in range(60):
            beta = 1.4 + (0.12 if step % 2 == 0 else -0.02)
            result = _tier(vol=0.55, btc_vol=0.5, beta=beta, previous=tier)
            if tier is not None and result["tier"] != tier:
                flips += 1
            tier = result["tier"]
        # The sequence enters high_beta once (from unmeasured) and never
        # flips again: 1.38 sits inside the 1.3..1.5 hysteresis band.
        self.assertEqual(tier, "high_beta")
        self.assertEqual(flips, 0)


if __name__ == "__main__":
    unittest.main()
