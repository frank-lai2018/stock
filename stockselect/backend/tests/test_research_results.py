import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import research_results as research


class ResearchTest(unittest.TestCase):
    def test_drawdown_includes_starting_cash_peak(self):
        result = research.metrics([{'equity': 90}, {'equity': 110}, {'equity': 99}], 100)
        self.assertAlmostEqual(result['max_drawdown'], -.1)
        self.assertAlmostEqual(result['total_return'], -.01)

    def test_old_code_results_are_not_served(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'research.json'
            with patch.object(research, 'RESULT_PATH', path):
                self.assertEqual(research.load_report()['status'], 'unvalidated')
                path.write_text(json.dumps({'fingerprint': 'old', 'variants': [1]}))
                result = research.load_report()
                self.assertEqual(result['status'], 'outdated')
                self.assertEqual(result['variants'], [])
                path.write_text(json.dumps({'fingerprint': research.fingerprint(), 'status': 'in_sample'}))
                self.assertEqual(research.load_report()['status'], 'in_sample')
