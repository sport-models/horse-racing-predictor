"""Offline smoke tests for v0.6.1g (no secrets/API/database needed)."""
import ast
import unittest
from pathlib import Path
from install_v061g import updated, OLD, NEW, OLD_WARNING

class TestLiveTrackPatch(unittest.TestCase):
    def test_idempotent(self):
        example = f"{OLD}\n\ndef score_runner(x):\n    return x\n\ndef analysis_tab():\n    st.warning('{OLD_WARNING}')\n"
        output = updated(example)
        self.assertEqual(updated(output), output)
        self.assertIn(NEW,output)
        self.assertIn('def score_runner(x):\n    return x',output)
        ast.parse(output)

    def test_matches_variants(self):
        source = Path('app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        targets = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TARGET_TRACKS' for t in node.targets):
                targets = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(targets)
        for name in ['Flemington','Caulfield','Moonee Valley','Geelong','Sandown Hillside','Sandown Lakeside','Ballarat Synthetic','Seymour','Pakenham Synthetic']:
            normalized = ' '.join(name.lower().replace('-', ' ').replace('_',' ').split())
            self.assertTrue(any(normalized == t or normalized.startswith(t+' ') for t in targets),name)

if __name__ == '__main__':
    unittest.main(verbosity=2)
