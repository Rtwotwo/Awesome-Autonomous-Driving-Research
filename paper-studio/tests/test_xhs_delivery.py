import json
from pathlib import Path
import tempfile
import unittest

from sources import extract_pdf
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from xhs_notes import prepare_note
from xhs_delivery import publish_note, record_source, note_status, reset_submission, recover_interrupted


class FakeConnector:
    def __init__(self):
        self.calls = []
        self.user = 'fixture-account'
        self.error = False

    def check_login(self):
        return {'logged_in': True, 'account': {'id': self.user, 'name': 'Synthetic Editor'}}

    def publish(self, payload):
        self.calls.append(payload)
        if self.error: raise TimeoutError('simulated disconnect')
        return {'state': 'submitted_unverified'}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = extract_pdf(fixture_pdf(), self.folder)
        self.article = fixture_article(self.source)
        (self.folder / 'article.json').write_text(json.dumps(self.article), encoding='utf-8')
        prepare_note(self.folder, self.article, self.source)
        record_source(self.folder)
        self.connector = FakeConnector()

    def tearDown(self): self.temp.cleanup()

    def test_single_attempt_receipt_and_unchanged_original(self):
        before = (self.folder / 'article.json').read_bytes()
        receipt = publish_note(self.folder, self.connector, 'fixture-account')
        self.assertEqual(receipt['state'], 'submitted_unverified')
        self.assertNotIn('url', receipt)
        self.assertEqual(len(self.connector.calls), 1)
        self.assertFalse(self.connector.calls[0]['is_original'])
        self.assertTrue(all(Path(p).is_file() for p in self.connector.calls[0]['images']))
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account')
        self.assertEqual(len(self.connector.calls), 1)
        self.assertEqual(before, (self.folder / 'article.json').read_bytes())

    def test_unknown_outcome_stays_locked_until_explicit_reset(self):
        self.connector.error = True
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account')
        self.assertEqual(note_status(self.folder)['receipt']['state'], 'outcome_unknown')
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account')
        with self.assertRaises(ValueError): reset_submission(self.folder, False)
        self.assertEqual(len(self.connector.calls), 1)
        reset_submission(self.folder, True)
        self.assertTrue(list((self.folder / 'xhs-receipts').glob('*.json')))
        self.assertEqual(note_status(self.folder)['receipt'], {})

    def test_source_change_or_account_change_blocks_without_submission(self):
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'different-account')
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account', cancelled=lambda: True)
        self.article['title'] += ' revised'
        (self.folder / 'article.json').write_text(json.dumps(self.article), encoding='utf-8')
        self.assertTrue(note_status(self.folder)['stale'])
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account')
        self.assertFalse(self.connector.calls)

    def test_interrupted_publish_can_be_explicitly_recovered_without_retry(self):
        (self.folder / 'xhs-submission.json').write_text(json.dumps({'state': 'publishing', 'attempt_id': 'old'}))
        recover_interrupted(self.folder)
        receipt = note_status(self.folder)['receipt']
        self.assertEqual(receipt['state'], 'outcome_unknown')
        self.assertEqual(receipt['attempt_id'], 'old')
        with self.assertRaises(ValueError): publish_note(self.folder, self.connector, 'fixture-account')
        self.assertFalse(self.connector.calls)
        reset_submission(self.folder, True)
        self.assertEqual(note_status(self.folder)['receipt'], {})


if __name__ == '__main__': unittest.main()
