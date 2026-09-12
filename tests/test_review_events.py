"""Artificial log fixtures only; these are not investment results."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import review_events as review


class ReviewEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case = 'long_history__user_rules__tqqq70'
        folder = self.root / self.case
        folder.mkdir()
        (folder / 'config.json').write_text('{"initial":80000}', encoding='utf-8')
        events = [dict(event_id=1, execution_date='2008-09-15', signal_date='2008-09-12', event='STATE_CHANGE'),
                  dict(event_id=2, execution_date='2008-09-16', signal_date='2008-09-15', event='BULL_DIP_BUY', asset='TQQQ')]
        (folder / 'events.jsonl').write_text('\n'.join(json.dumps(x) for x in events) + '\n', encoding='utf-8')
        (folder / 'trades.csv').write_text('execution_id,asset,side,notional\n2,SGOV,SELL,4000\n2,TQQQ,BUY,3996\n', encoding='utf-8-sig')
        self.refresh()

    def refresh(self):
        files = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (self.root / self.case).iterdir()}
        (self.root / 'manifest.json').write_text(json.dumps({'status':'passed', 'output_sha256':files}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_join_and_preserve_signal_date(self):
        cfg, records = review.collect(self.root, self.case)
        self.assertEqual(cfg['initial'], 80000)
        self.assertEqual(records[1]['signal_date'], '2008-09-15')
        self.assertEqual(len(records[1]['trades']), 2)
        self.assertEqual(records[0]['trades'], [])

    def test_inclusive_date_and_linked_asset_filter(self):
        _, records = review.collect(self.root, self.case, from_date='2008-09-16', to_date='2008-09-16', asset='sgov', event='dip')
        self.assertEqual(len(records), 1)

    def test_corruption_rejected(self):
        with (self.root / self.case / 'trades.csv').open('a') as out:
            out.write('\n')
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            review.collect(self.root, self.case)

    def test_failed_manifest_rejected(self):
        path = self.root / 'manifest.json'
        data = json.loads(path.read_text()); data['status'] = 'failed'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'completed'):
            review.collect(self.root, self.case)

    def test_bad_case_or_dates_rejected(self):
        with self.assertRaises(ValueError):
            review.collect(self.root, '../escape')
        with self.assertRaises(ValueError):
            review.collect(self.root, self.case, from_date='2009-01-01', to_date='2008-01-01')

    def test_orphan_trade_rejected(self):
        path = self.root / self.case / 'trades.csv'
        path.write_text('execution_id,asset,side,notional\n99,TQQQ,BUY,100\n')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'absent'):
            review.collect(self.root, self.case)

    def test_duplicate_event_rejected(self):
        path = self.root / self.case / 'events.jsonl'
        text = path.read_text(); path.write_text(text + text.splitlines()[0] + '\n')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            review.collect(self.root, self.case)

    def test_cli_list_limit_and_no_mutation(self):
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(review.main(['--run', str(self.root), '--list-cases']), 0)
        self.assertIn(self.case, out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(review.main(['--run', str(self.root), '--case', self.case, '--limit', '1']), 0)
        header = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(header['matched_events'], 2)
        self.assertEqual(header['shown_events'], 1)
        before_after = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, before_after)


if __name__ == '__main__':
    unittest.main()
