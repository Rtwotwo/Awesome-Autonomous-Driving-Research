import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock

from server import Studio, make_server
from sources import extract_pdf
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article


class XhsServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.studio = Studio(Path(self.temp.name) / 'data')
        self.studio.desktop = True
        self.studio.xhs = Mock()
        self.studio.xhs.status.return_value = self.studio.xhs.check_login.return_value = {
            'installed': True, 'running': True, 'connected': True,
            'logged_in': True, 'account': {'id': 'fixture', 'name': 'Synthetic'}}
        self.studio.xhs.publish.return_value = {'state': 'submitted_unverified'}
        self.job = self.studio.create('Synthetic paper')
        manifest = extract_pdf(fixture_pdf(), self.studio.folder(self.job))
        self.studio.save_article(self.job, fixture_article(manifest))
        self.server = make_server(self.studio, 0)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        self.studio.close(); self.studio.pool.shutdown(wait=True)
        self.temp.cleanup()

    def call(self, path, body=None, cookie=True, token=True):
        headers = {'Content-Type': 'application/json', 'X-Studio-Token': self.studio.token if token else ''}
        if cookie: headers['Cookie'] = 'studio_session=' + self.studio.cookie
        request = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response: return response.read()

    def wait(self):
        for _ in range(500):
            if self.studio.jobs[self.job]['status'] not in {'queued', 'running'}:
                self.assertEqual(self.studio.jobs[self.job]['status'], 'ready', self.studio.jobs[self.job])
                return
            time.sleep(.02)
        self.fail('task did not finish')

    def test_prepare_edit_export_submit_and_no_duplicates(self):
        path = f'/api/jobs/{self.job}/'
        article_before = (self.studio.folder(self.job) / 'article.json').read_bytes()
        self.call(path + 'xhs-prepare', {}); self.wait()
        state = json.loads(self.call(path + 'xhs-note'))
        self.assertFalse(state['stale'])
        note = state['note']
        self.call(path + 'xhs-note', {'title': '研究笔记', 'content': '根据论文整理的方法介绍。',
                                    'tags': ['论文研读'], 'visibility': '仅自己可见', 'image_ids': note['image_ids']})
        self.assertTrue(self.call(path + 'xhs-export').startswith(b'PK'))
        self.assertTrue(self.call(path + 'asset?name=' + note['cards'][0]['image']).startswith(b'\x89PNG'))
        with self.assertRaises(urllib.error.HTTPError): self.call(path + 'xhs-publish', {})
        self.call(path + 'xhs-publish', {'publish': True, 'account_id': 'fixture'}); self.wait()
        receipt = json.loads(self.call(path + 'xhs-note'))['receipt']
        self.assertEqual(receipt['state'], 'submitted_unverified')
        self.assertEqual(self.studio.xhs.publish.call_args.args[0]['visibility'], '仅自己可见')
        with self.assertRaises(urllib.error.HTTPError): self.call(path + 'xhs-publish', {'publish': True, 'account_id': 'fixture'})
        self.assertEqual(self.studio.xhs.publish.call_count, 1)
        self.assertEqual(article_before, (self.studio.folder(self.job) / 'article.json').read_bytes())

    def test_desktop_auth_token_and_no_inferred_publish(self):
        with self.assertRaises(urllib.error.HTTPError) as error: self.call('/api/xhs/status', cookie=False)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error: self.call('/api/xhs/start', {}, token=False)
        self.assertEqual(error.exception.code, 403)
        self.call('/api/xhs/status')
        self.assertFalse(self.studio.xhs.publish.called)
        self.studio.desktop = False
        with self.assertRaises(urllib.error.HTTPError): self.call('/api/xhs/start', {})

    def test_startup_converts_inflight_receipt_to_recoverable_unknown(self):
        folder = self.studio.folder(self.job)
        self.studio.update(self.job, status='running', operation='xhs-publish')
        (folder / 'xhs-submission.json').write_text(json.dumps({'state':'publishing','attempt_id':'interrupted'}))
        reopened = Studio(self.studio.data_dir)
        try:
            self.assertEqual(reopened.jobs[self.job]['status'], 'error')
            receipt = json.loads((folder / 'xhs-submission.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['state'], 'outcome_unknown')
            self.assertEqual(receipt['attempt_id'], 'interrupted')
        finally: reopened.close()


if __name__ == '__main__': unittest.main()
