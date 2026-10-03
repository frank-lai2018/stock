import unittest

from app.consolidation import analyze
from test_execution import bar


class ConsolidationTest(unittest.TestCase):
    def fixture(self):
        rows = [bar(i, 105, 115, 95, 105, amount=100) for i in range(25)]
        for b in rows[-5:]:
            b['raw_high'] = b['high'] = 110
        rows += [bar(i, 108, 110, 106, 108, amount=50) for i in range(25, 40)]
        rows.append(bar(40, 110, 112, 109, 111, amount=150))
        return rows

    def test_breaks_previous_high_without_including_today(self):
        result = analyze(self.fixture())
        self.assertEqual(result['status'], 'priority')
        self.assertEqual(result['pivot'], 110)
        self.assertGreater(result['max_entry'], 111)
        self.assertIsNone(result['pattern_prior'])

    def test_volume_confirmation_and_contraction_are_required(self):
        rows = self.fixture()
        rows[-1]['amount'] = 60
        result = analyze(rows)
        self.assertEqual(result['status'], 'watch')
        self.assertTrue(any('1.5' in b for b in result['blockers']))
        rows = self.fixture()
        for b in rows[-16:-1]:
            b['low'] = 94
        result = analyze(rows)
        self.assertEqual(result['status'], 'watch')
        self.assertTrue(any('振幅' in b for b in result['blockers']))

    def test_no_breakout_or_incomplete_history_is_no_signal(self):
        rows = self.fixture()
        rows[-1]['close'] = 110
        self.assertIsNone(analyze(rows))
        self.assertIsNone(analyze(rows[-30:]))

    def test_missing_price_or_amount_is_not_a_signal(self):
        rows = self.fixture()
        rows[-2]['high'] = None
        self.assertIsNone(analyze(rows))
        rows = self.fixture()
        rows[0]['amount'] = None
        self.assertIsNone(analyze(rows))
