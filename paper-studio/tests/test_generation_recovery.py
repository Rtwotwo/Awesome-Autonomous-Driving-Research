"""Loopback HTTP recovery coverage with a synthetic PDF and a mocked model."""
import copy
import http.client
import json
import unittest
import urllib.error
from unittest.mock import patch

from sources import write_json
from tests import test_server as server_fixtures
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article


class GenerationRecoveryTests(unittest.TestCase):
    # Reuse fixture utilities, without inheriting unrelated server test methods.
    setUp = server_fixtures.ServerTests.setUp
    tearDown = server_fixtures.ServerTests.tearDown
    call = server_fixtures.ServerTests.call
    ready = server_fixtures.ServerTests.ready

    def import_fixture(self):
        raw, _ = self.call('/api/import/pdf', fixture_pdf())
        job = json.loads(raw)['id']
        self.assertEqual(self.ready(job)['status'], 'ready')
        return job, self.studio.load(job), self.studio.folder(job)

    def recovery(self, job):
        raw, _ = self.call(f'/api/jobs/{job}/generation-draft')
        return json.loads(raw)

    def failed_review(self, job, article, options=None):
        options = dict(options or {})
        options.update(base_url='http://127.0.0.1:9999', model='fixture',
                       api_key='PRIVATE_KEY_SENTINEL', _desktop=True)
        calls = []
        def model(config, prompt, *args, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                return {'facts': [], 'internal': 'PRIVATE_NOTES_SENTINEL'}
            if len(calls) == 2:
                return copy.deepcopy(article)
            kwargs['partial']('PRIVATE_PARTIAL_SENTINEL', 0, 'streaming')
            raise http.client.RemoteDisconnected('PRIVATE_PROVIDER_SENTINEL')
        with patch('writing.chat_json', side_effect=model), patch('writing.urllib.request.build_opener') as network:
            self.call(f'/api/jobs/{job}/generate', options)
            self.assertEqual(self.ready(job)['status'], 'error')
            network.assert_not_called()
        self.assertEqual(len(calls), 3)

    def assert_no_preview(self, job):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.call(f'/api/jobs/{job}/generation-preview')
        self.assertEqual(caught.exception.code, 400)

    def test_failed_review_has_pending_draft_and_image_preview_without_final_article(self):
        job, manifest, folder = self.import_fixture()
        article = fixture_article(manifest)
        self.failed_review(job, article)
        payload = self.recovery(job)
        draft = payload['recovery']
        self.assertEqual(draft['article']['title'], article['title'])
        self.assertEqual(draft['stage'], '编辑复核')
        self.assertFalse(draft['complete']);self.assertFalse(draft['review_complete'])
        self.assertEqual((draft['completed_units'], draft['total_units']), (0, 1))
        self.assertGreaterEqual(draft['elapsed_seconds'], 0)
        self.assertFalse((folder / 'article.json').exists())
        with self.assertRaises(urllib.error.HTTPError):
            self.call(f'/api/jobs/{job}/article')
        raw, headers = self.call(f'/api/jobs/{job}/generation-preview')
        html = raw.decode('utf-8')
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn('待复核草稿', html)
        self.assertIn('尚未完成生成', html)
        self.assertIn('不会自动提交此版本', html)
        self.assertTrue(article['lead'] in html, 'Preview must contain the recovered draft body')
        self.assertIn('data:image/png;base64', html)
        # Both recovery endpoints are read-only, even when a full draft is available.
        self.assertFalse((folder / 'article.json').exists())
        self.assertEqual(self.studio.jobs[job]['status'], 'error')

    def test_failed_review_and_recovery_leave_existing_final_unchanged(self):
        job, manifest, folder = self.import_fixture()
        final = fixture_article(manifest)
        final['title'] = '已经审定的正式文章'
        self.call(f'/api/jobs/{job}/article', final)
        before = (folder / 'article.json').read_bytes()
        draft = fixture_article(manifest)
        draft['title'] = '新生成但尚未复核的草稿'
        draft['lead'] = '这里是新草稿的正文，不是已有正式文章。'
        self.failed_review(job, draft)
        self.assertEqual(self.recovery(job)['recovery']['article']['title'], draft['title'])
        preview, _ = self.call(f'/api/jobs/{job}/generation-preview')
        self.assertTrue(draft['lead'] in preview.decode('utf-8'), 'Recovery preview must show the new draft body')
        raw, _ = self.call(f'/api/jobs/{job}/article')
        self.assertEqual(json.loads(raw)['title'], final['title'])
        self.assertEqual((folder / 'article.json').read_bytes(), before)
        self.assertFalse((folder / 'history').exists())

    def test_recovery_whitelists_article_and_options_without_private_checkpoint_fields(self):
        job, manifest, folder = self.import_fixture()
        manifest['pages'][0]['text'] += '\nRAW_SOURCE_SENTINEL'
        write_json(folder / 'source.json', manifest)
        article = fixture_article(manifest)
        article['api_key'] = 'PRIVATE_TEST_ARTICLE'
        article['sections'][0]['paragraphs'][0]['reasoning_content'] = 'PRIVATE_REASONING_SENTINEL'
        options = {'style': 'technical', 'length': 4000, 'brief': '保留必要实验条件',
                   'auto_figures': False, 'vision': False, 'figure_ids': [manifest['figures'][0]['id']],
                   'output_tokens': 8000, 'request_timeout': 900, 'response_mode': 'json'}
        self.failed_review(job, article, options)
        expected = {k: options[k] for k in ('style', 'length', 'brief', 'auto_figures', 'vision', 'figure_ids')}
        saved_options = json.loads((folder / 'writing-options.json').read_text(encoding='utf-8'))
        self.assertEqual(saved_options, expected)
        # Also defend against older option files that contain provider fields.
        write_json(folder / 'writing-options.json', dict(expected, api_key='PRIVATE_TEST_OPTIONS',
                                                       base_url='PRIVATE_URL_SENTINEL', model='PRIVATE_MODEL_SENTINEL'))
        raw, _ = self.call(f'/api/jobs/{job}/generation-draft')
        payload = json.loads(raw)
        self.assertEqual(payload['options'], expected)
        self.assertNotIn('PRIVATE_', raw.decode('utf-8'))
        self.assertNotIn('RAW_SOURCE_SENTINEL', raw.decode('utf-8'))
        self.assertNotIn('notes', payload['recovery'])
        self.assertNotIn('api_key', payload['recovery']['article'])
        preview, _ = self.call(f'/api/jobs/{job}/generation-preview')
        self.assertNotIn('PRIVATE_', preview.decode('utf-8'))
        self.assertNotIn('RAW_SOURCE_SENTINEL', preview.decode('utf-8'))
        # The test actually populated the private checkpoint/partial values.
        checkpoint = (folder / 'generation-progress.json').read_text(encoding='utf-8')
        self.assertIn('PRIVATE_NOTES_SENTINEL', checkpoint)
        self.assertIn('PRIVATE_TEST_ARTICLE', checkpoint)
        self.assertIn('PRIVATE_PARTIAL_SENTINEL', (folder / 'generation-partial.json').read_text(encoding='utf-8'))

    def test_legacy_complete_checkpoint_stays_pending_until_identical_article_is_saved(self):
        job, manifest, folder = self.import_fixture()
        article = fixture_article(manifest)
        saved = {'fingerprint': 'legacy-format', 'draft': article, 'notes': [], 'complete': False}
        write_json(folder / 'generation-progress.json', saved)
        pending = self.recovery(job)
        self.assertEqual(pending['options'], {})
        self.assertEqual(pending['recovery']['article']['title'], article['title'])
        self.assertFalse(pending['recovery']['complete'])
        raw, _ = self.call(f'/api/jobs/{job}/generation-preview')
        self.assertIn('待复核草稿', raw.decode('utf-8'))
        saved['complete'] = True
        write_json(folder / 'generation-progress.json', saved)
        completed_legacy = self.recovery(job)['recovery']
        self.assertIsNotNone(completed_legacy)
        self.assertFalse(completed_legacy['review_complete'])
        self.assertFalse(completed_legacy['complete'])
        self.assertFalse((folder / 'article.json').exists())
        self.call(f'/api/jobs/{job}/article', article)
        self.assertIsNone(self.recovery(job)['recovery'])
        self.assert_no_preview(job)

    def test_completed_generation_recovers_after_final_article_save_failure(self):
        job, manifest, folder = self.import_fixture()
        article = fixture_article(manifest)
        options = {'base_url': 'http://127.0.0.1:9999', 'model': 'fixture', '_desktop': True}
        with patch('writing.chat_json', side_effect=[{'facts': []}, article, article]), \
                patch.object(self.studio, 'save_article', side_effect=OSError('Synthetic disk failure')):
            self.call(f'/api/jobs/{job}/generate', options)
            self.assertEqual(self.ready(job)['status'], 'error')
        self.assertFalse((folder / 'article.json').exists())
        recovery = self.recovery(job)['recovery']
        self.assertTrue(recovery['complete']);self.assertTrue(recovery['review_complete'])
        with patch('writing.chat_json') as chat:
            self.call(f'/api/jobs/{job}/generate', dict(options, force_regenerate=False))
            self.assertEqual(self.ready(job)['status'], 'ready')
            chat.assert_not_called()
        self.assertEqual(self.studio.load(job, 'article.json'), recovery['article'])
        self.assertIsNone(self.recovery(job)['recovery'])

    def test_source_signature_change_hides_stale_recovery(self):
        job, manifest, folder = self.import_fixture()
        self.failed_review(job, fixture_article(manifest))
        self.assertIsNotNone(self.recovery(job)['recovery'])
        manifest['pages'][0]['text'] += '\nCorrected source version.'
        write_json(folder / 'source.json', manifest)
        self.assertIsNone(self.recovery(job)['recovery'])
        self.assert_no_preview(job)
        self.assertTrue((folder / 'generation-progress.json').exists())

    def test_missing_or_invalid_checkpoint_has_no_pending_preview(self):
        job, _, folder = self.import_fixture()
        self.assertIsNone(self.recovery(job)['recovery'])
        self.assert_no_preview(job)
        write_json(folder / 'generation-progress.json', {'draft': {'title': 'missing sections'}, 'complete': False})
        self.assertIsNone(self.recovery(job)['recovery'])
        self.assert_no_preview(job)


if __name__ == '__main__':
    unittest.main()
