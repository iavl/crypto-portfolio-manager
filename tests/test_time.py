import unittest

from crypto_portfolio.models.time import normalize_timestamp, parse_timestamp


class TimestampTests(unittest.TestCase):
    def test_date_only_input_is_explicitly_normalized_to_utc(self):
        self.assertEqual(normalize_timestamp("2026-09-01"), "2026-09-01T00:00:00Z")
        self.assertEqual(
            normalize_timestamp("2026-09-01T08:00:00+08:00"),
            "2026-09-01T00:00:00Z",
        )

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_timestamp("2026-09-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
