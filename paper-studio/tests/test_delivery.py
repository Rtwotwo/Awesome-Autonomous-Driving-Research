import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from PIL import Image

from delivery import prepare_delivery
from server import Studio, make_server
from sources import extract_pdf, write_json
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from tests.test_wechat_api import FakeAPI
from wechat_api import TransportError
from formatting import layout_markdown
from wechat_review import render_content, prepare
from model_transport import SafeModelError


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.studio = Studio(Path(self.tmp.name)/'data')
        self.studio.desktop = True
        self.api = FakeAPI()
        self.studio.wechat.request = self.api
        self.studio.wechat.save({'appid':'wx'+'1'*16, 'secret':'SYNTHETIC_NOT_REAL_12345', 'name':'Fixture'})
        self.job = self.studio.create('Fixture')
        self.folder = self.studio.folder(self.job)
        self.source = extract_pdf(fixture_pdf(), self.folder)
        self.article = fixture_article(self.source)
        self.studio.save_article(self.job, self.article)
        self.original = (self.folder/'article.json').read_bytes()

    def tearDown(self):
        self.studio.pool.shutdown(wait=True)
        self.studio.close()
        self.tmp.cleanup()

    def wait_job(self):
        for _ in range(300):
            item = self.studio.jobs[self.job]
            if item['status'] not in {'running', 'queued'}:
                return item
            time.sleep(.02)
        self.fail('Delivery did not finish')

    def run_delivery(self, options=None):
        self.studio.queue_delivery(self.job, True, options)
        return self.wait_job()

    def test_prepare_defaults_overview_and_warnings_without_external_calls(self):
        report, prepared = prepare_delivery(self.studio, self.job)
        self.assertTrue(report['ready'], report)
        self.assertEqual(prepared['options']['author'], 'Paper Studio')
        self.assertTrue(report['warnings'])  # Evidence warnings remain visible, not human approval.
        self.assertIn(report['cover']['selection_reason'], {'overview', 'figure_fallback'})
        self.assertFalse(self.api.calls)
        self.assertFalse((self.folder/'wechat-review.json').exists())
        self.assertFalse((self.folder/'automatic-draft-permit.json').exists())
        self.assertEqual((self.folder/'article.json').read_bytes(), self.original)
        self.assertTrue(self.studio.wechat_status(self.job)['automation']['ready'])

    def test_one_click_draft_preserves_body_and_does_not_fake_human_approval(self):
        self.assertEqual(self.run_delivery()['status'], 'ready')
        self.assertEqual(self.api.article['author'], 'Paper Studio')
        self.assertFalse((self.folder/'wechat-review.json').exists())
        self.assertEqual((self.folder/'article.json').read_bytes(), self.original)
        self.assertEqual(sum(p == 'draft/add' for p, _ in self.api.calls), 1)
        self.assertFalse(any('publish' in p or 'mass' in p for p, _ in self.api.calls))
        status = self.studio.wechat_status(self.job)
        self.assertEqual(status['submission']['state'], 'submitted')
        self.assertFalse(status['approved'])
        self.assertFalse(status['automation']['ready'])
        self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertEqual(sum(p == 'draft/add' for p, _ in self.api.calls), 1)
        for path in self.folder.rglob('*.json'):
            self.assertNotIn('SYNTHETIC_NOT_REAL_12345', path.read_text(encoding='utf8'))

    def test_explicit_blank_custom_byline_and_existing_cover_are_preserved(self):
        options = {'author':'', 'cover_id':self.source['figures'][0]['id'], 'theme':'blue'}
        write_json(self.folder/'wechat-options.json', options)
        with patch('delivery.ensure_overview_cover') as compose:
            report, prepared = prepare_delivery(self.studio, self.job)
            self.assertTrue(report['ready'], report)
            self.assertEqual(prepared['options']['author'], '')
            self.assertEqual(prepared['options']['cover_id'], options['cover_id'])
            self.assertEqual(prepared['options']['theme'], 'blue')
            self.assertFalse(compose.called)
        report, prepared = prepare_delivery(self.studio, self.job, {'author':'Other Editor'})
        self.assertTrue(report['ready'], report)
        self.assertEqual(prepared['options']['author'], 'Other Editor')

    def test_long_fields_adapt_only_delivery_values(self):
        self.article.update(title='超过投递限制的文章原题'*8, digest='第一句话说明研究问题和研究目的。'+'详细讨论实验证据与适用条件。'*40)
        self.studio.save_article(self.job, self.article)
        before = (self.folder/'article.json').read_bytes()
        report, prepared = prepare_delivery(self.studio, self.job)
        self.assertTrue(report['ready'], report)
        self.assertLessEqual(len(prepared['options']['title']), 32)
        self.assertLessEqual(len(prepared['options']['digest']), 120)
        self.assertEqual((self.folder/'article.json').read_bytes(), before)
        self.assertEqual(len(self.studio.wechat_status(self.job)['field_adjustments']), 2)

    def test_baoyu_fallback_is_visible_and_preserves_content(self):
        with patch.object(self.studio, 'format_skill', side_effect=ValueError('Bun not installed')):
            report, prepared = prepare_delivery(self.studio, self.job, {'theme':'baoyu'})
        self.assertTrue(report['ready'], report)
        self.assertEqual(prepared['options']['theme'], 'ink')
        self.assertTrue(any('宝玉' in warning and '内置' in warning for warning in report['warnings']))
        self.assertEqual((self.folder/'article.json').read_bytes(), self.original)

    def test_valid_baoyu_cache_survives_missing_runtime_without_rerender(self):
        prepare_delivery(self.studio, self.job)
        options = self.studio.load(self.job, 'wechat-options.json')
        options.update(theme='baoyu', baoyu_theme='simple')
        source = self.studio.load(self.job)
        write_json(self.folder/'wechat-options.json', options)
        write_json(self.folder/'baoyu-layout.json', {
            'markdown':layout_markdown(self.article, source, options), 'baoyu_theme':'simple',
            'content':render_content(self.article, source, dict(options, theme='ink'))})
        with patch.object(self.studio, 'format_skill', side_effect=ValueError('Bun missing')) as formatter:
            report, prepared = prepare_delivery(self.studio, self.job)
        self.assertTrue(report['ready'], report)
        self.assertEqual(prepared['options']['theme'], 'baoyu')
        self.assertFalse(formatter.called)
        self.assertFalse(any('不可用' in w for w in report['warnings']))

    def test_stale_baoyu_cache_is_refreshed_when_runtime_works(self):
        write_json(self.folder/'baoyu-layout.json', {'markdown':'outdated', 'baoyu_theme':'simple', 'content':'old'})
        def formatter(job, progress):
            source = self.studio.load(job)
            options = self.studio.load(job, 'wechat-options.json')
            write_json(self.folder/'baoyu-layout.json', {
                'markdown':layout_markdown(self.article, source, options), 'baoyu_theme':options['baoyu_theme'],
                'content':render_content(self.article, source, dict(options, theme='ink'))})
        with patch.object(self.studio, 'format_skill', side_effect=formatter) as applied:
            report, prepared = prepare_delivery(self.studio, self.job, {'theme':'baoyu'})
        self.assertTrue(report['ready'], report)
        self.assertEqual(prepared['options']['theme'], 'baoyu')
        self.assertEqual(applied.call_count, 1)

    def test_automatic_cover_refreshes_title_and_source_but_explicit_choice_sticks(self):
        report, _ = prepare_delivery(self.studio, self.job)
        first = report['cover']['id']
        repeated, _ = prepare_delivery(self.studio, self.job)
        self.assertEqual(repeated['cover']['id'], first)
        self.article['title'] = '编辑后论文标题'
        self.studio.save_article(self.job, self.article)
        updated, _ = prepare_delivery(self.studio, self.job)
        second = updated['cover']['id']
        self.assertNotEqual(second, first)
        path = self.folder/self.source['figures'][0]['image']
        with Image.open(path) as image:
            changed = image.copy()
        changed.putpixel((5, 5), (255, 0, 0))
        changed.save(path)
        refreshed, _ = prepare_delivery(self.studio, self.job)
        third = refreshed['cover']['id']
        self.assertNotEqual(third, second)
        prepare_delivery(self.studio, self.job, {'cover_id':third})
        self.article['title'] = '再次编辑论文标题'
        self.studio.save_article(self.job, self.article)
        deliberate, _ = prepare_delivery(self.studio, self.job)
        self.assertEqual(deliberate['cover']['id'], third)
        self.assertEqual(self.studio.load(self.job, 'delivery-cover.json')['mode'], 'manual')

    def test_new_eligible_overview_replaces_automatic_fallback_cover(self):
        for figure in self.source['figures']:
            figure['caption'] = figure['original_caption'] = 'Output samples.'
        write_json(self.folder/'source.json', self.source)
        first, _ = prepare_delivery(self.studio, self.job)
        self.assertEqual(first['cover']['selection_reason'], 'figure_fallback')
        source = self.studio.load(self.job)
        figure = copy.deepcopy(source['figures'][0])
        figure.update(id='fig-999', image='images/fig-999.png', caption='Overview of the overall architecture.',
                      original_caption='Overview of the overall architecture.')
        (self.folder/figure['image']).write_bytes((self.folder/source['figures'][0]['image']).read_bytes())
        source['figures'].append(figure)
        write_json(self.folder/'source.json', source)
        updated, _ = prepare_delivery(self.studio, self.job)
        self.assertTrue(updated['ready'], updated)
        self.assertEqual(updated['cover']['selection_reason'], 'overview')
        self.assertEqual(updated['cover']['source_figure_id'], 'fig-999')
        self.assertNotEqual(updated['cover']['id'], first['cover']['id'])

    def test_missing_references_and_corrupt_assets_still_block_automatic_drafts(self):
        for issue in ('missing_reference', 'corrupt_image'):
            with self.subTest(issue=issue):
                source = copy.deepcopy(self.source)
                article = copy.deepcopy(self.article)
                original_image = (self.folder/source['figures'][0]['image']).read_bytes()
                if issue == 'missing_reference':
                    article['sections'][2]['figures'][0]['id'] = 'missing'
                else:
                    (self.folder/source['figures'][0]['image']).write_bytes(b'not an image')
                write_json(self.folder/'source.json', source)
                write_json(self.folder/'article.json', article)
                report, _ = prepare_delivery(self.studio, self.job)
                self.assertFalse(report['ready'], report)
                self.assertTrue(report['blockers'])
                self.assertFalse(self.api.calls)
                self.assertFalse((self.folder/'wechat-review.json').exists())
                (self.folder/source['figures'][0]['image']).write_bytes(original_image)

    def test_unreviewed_assets_warn_in_automatic_mode_but_manual_prepare_stays_strict(self):
        for changes in ({'origin':'pdf_embedded'}, {'origin':'local_image'}, {'crop_status':'page_fallback'}):
            with self.subTest(changes=changes):
                source=copy.deepcopy(self.source)
                source['figures'][0].update(changes,reviewed=False)
                write_json(self.folder/'source.json',source)
                report,prepared=prepare_delivery(self.studio,self.job)
                self.assertTrue(report['ready'],report)
                self.assertTrue(any('尚未人工核对' in warning for warning in report['warnings']))
                self.assertTrue(self.studio.wechat_status(self.job)['automation']['ready'])
                saved_source=self.studio.load(self.job)
                self.assertEqual(saved_source['figures'],source['figures'])
                self.assertFalse(self.api.calls)
                self.assertFalse((self.folder/'wechat-review.json').exists())
                with self.assertRaisesRegex(ValueError,'待提交图'):
                    prepare(self.folder,self.article,saved_source,prepared['options'],self.studio.wechat.public())

    def test_unreviewed_asset_can_complete_automatic_delivery_without_faking_approval(self):
        self.source['figures'][0].update(origin='pdf_embedded',reviewed=False)
        write_json(self.folder/'source.json',self.source)
        self.assertEqual(self.run_delivery()['status'],'ready')
        receipt=self.studio.load(self.job,'wechat-submission.json')
        self.assertEqual(receipt['state'],'submitted')
        self.assertFalse(self.studio.load(self.job)['figures'][0]['reviewed'])
        self.assertFalse((self.folder/'wechat-review.json').exists())
        self.assertEqual(sum(path=='draft/add' for path,_ in self.api.calls),1)

    def test_readback_exhaustion_keeps_created_draft_then_one_click_only_rechecks(self):
        unavailable=True
        def request(path,**kwargs):
            result=self.api(path,**kwargs)
            if path=='draft/get' and unavailable:
                raise TransportError('SECRET_IN_READBACK_EXCEPTION')
            return result
        self.studio.wechat.request=request
        with patch('wechat_api.READBACK_RETRY_DELAYS',(0,0)):
            self.assertEqual(self.run_delivery()['status'],'ready')
        receipt=self.studio.load(self.job,'wechat-submission.json')
        self.assertEqual(receipt['state'],'submitted_unverified')
        self.assertTrue(receipt['media_id'])
        self.assertIn('草稿已创建',receipt['message'])
        self.assertNotIn('SECRET_IN_READBACK_EXCEPTION',json.dumps(receipt))
        self.assertEqual(receipt['verification']['status'],'unavailable')
        self.assertEqual(receipt['verification']['attempts'],3)
        self.assertEqual(sum(path=='draft/get' for path,_ in self.api.calls),3)
        before=len(self.api.calls)
        unavailable=False
        # Local editorial changes do not turn verification of an existing ID
        # into an upload of a different local payload.
        self.article['lead']+='本地修改还没有投递。'
        self.studio.save_article(self.job,self.article)
        self.assertEqual(self.run_delivery({'title':'不要创建另一篇'})['status'],'ready')
        self.assertEqual(self.studio.load(self.job,'wechat-submission.json')['state'],'submitted')
        self.assertEqual([path for path,_ in self.api.calls[before:]],['stable_token','draft/get'])
        self.assertEqual(sum(path=='draft/add' for path,_ in self.api.calls),1)

    def test_real_readback_difference_stays_unverified_without_resubmission(self):
        self.api.bad_read=True
        self.assertEqual(self.run_delivery()['status'],'ready')
        receipt=self.studio.load(self.job,'wechat-submission.json')
        self.assertEqual(receipt['state'],'submitted_unverified')
        self.assertEqual(receipt['verification']['status'],'mismatch')
        self.assertFalse(receipt['verification']['text_match'])
        self.assertEqual(receipt['verification']['attempts'],1)
        self.assertEqual(self.run_delivery()['status'],'ready')
        self.assertEqual(sum(path=='draft/add' for path,_ in self.api.calls),1)
        self.assertEqual(sum(path=='draft/get' for path,_ in self.api.calls),2)

    def test_report_invalidates_when_article_or_destination_changes(self):
        prepare_delivery(self.studio, self.job)
        self.assertTrue(self.studio.wechat_status(self.job)['automation']['ready'])
        self.article['lead'] += '编辑已修改。'
        self.studio.save_article(self.job, self.article)
        self.assertFalse(self.studio.wechat_status(self.job)['automation']['ready'])
        prepare_delivery(self.studio, self.job)
        self.studio.wechat.config['appid'] = 'wx'+'2'*16
        self.assertFalse(self.studio.wechat_status(self.job)['automation']['ready'])

    def test_missing_account_and_indivisible_overlong_table_block_without_remote_calls(self):
        config = dict(self.studio.wechat.config)
        self.studio.wechat.config = {}
        report, _ = prepare_delivery(self.studio, self.job)
        self.assertFalse(report['ready'])
        self.assertTrue(any('公众号' in blocker for blocker in report['blockers']))
        self.studio.wechat.config = config
        self.article['sections'][0]['tables'] = [{'columns':['Condition','Result'], 'rows':[['完整表格内容。'*4000,'0.5']], 'caption':'不可拆分的大表格', 'pages':[1]}]
        self.studio.save_article(self.job, self.article)
        self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertTrue(any('表格' in b for b in self.studio.load(self.job, 'delivery-checks.json')['blockers']))
        self.assertFalse(self.api.calls)

    def test_unknown_result_blocks_second_create(self):
        self.api.add_error = TransportError('Fixture timeout')
        self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertEqual(self.studio.load(self.job, 'wechat-submission.json')['state'], 'unknown')
        self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertEqual(sum(p == 'draft/add' for p, _ in self.api.calls), 1)

    def test_cancel_after_token_stops_before_asset_upload(self):
        def request(path, **kwargs):
            result = self.api(path, **kwargs)
            if path == 'stable_token':
                self.studio.jobs[self.job]['cancelled'] = True
            return result
        self.studio.wechat.request = request
        self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertEqual([p for p, _ in self.api.calls], ['stable_token'])

    def test_account_switch_during_preparation_stops_before_remote_calls(self):
        original = self.studio.update
        def update(job, **values):
            if values.get('message', '').startswith('自动准备 2/4'):
                self.studio.wechat.config['appid'] = 'wx'+'2'*16
            return original(job, **values)
        with patch.object(self.studio, 'update', side_effect=update):
            self.assertEqual(self.run_delivery()['status'], 'error')
        self.assertFalse(self.api.calls)

    def test_http_prepare_then_submit_uses_saved_article_and_sparse_options(self):
        server = make_server(self.studio, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(action, body=None):
            raw = json.dumps(body).encode() if body is not None else None
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/jobs/{self.job}/{action}',
                data=raw, headers={'X-Studio-Token':self.studio.token, 'Cookie':'studio_session='+self.studio.cookie,
                                   'Content-Type':'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        try:
            code, _ = call('wechat-auto-prepare', {'options':{'author':''}})
            self.assertEqual(code, 202)
            self.assertEqual(self.wait_job()['status'], 'ready')
            self.assertFalse(self.api.calls)
            _, status = call('wechat-review')
            self.assertTrue(status['automation']['ready'])
            self.assertEqual(status['options']['author'], '')
            code, _ = call('wechat-auto-submit', {'options':{'title':'自定义标题'}})
            self.assertEqual(code, 202)
            self.assertEqual(self.wait_job()['status'], 'ready')
            self.assertEqual(self.api.article['title'], '自定义标题')
            self.assertEqual(self.api.article['author'], '')
            self.assertEqual((self.folder/'article.json').read_bytes(), self.original)
        finally:
            server.shutdown()
            server.server_close()

    def test_http_author_save_preserves_auto_cover_and_explicit_same_selection_is_manual(self):
        prepare_delivery(self.studio, self.job)
        options = self.studio.load(self.job, 'wechat-options.json')
        original_cover = options['cover_id']
        server = make_server(self.studio, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def save(values):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/jobs/{self.job}/wechat-options',
                data=json.dumps(values).encode(), headers={'X-Studio-Token':self.studio.token,
                    'Cookie':'studio_session='+self.studio.cookie, 'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 200)
        try:
            save({**options, 'author':'Changed Author', 'cover_explicit':False})
            self.assertEqual(self.studio.load(self.job, 'delivery-cover.json')['mode'], 'automatic')
            self.article['title'] = '原稿标题已经改变'
            self.studio.save_article(self.job, self.article)
            prepare_delivery(self.studio, self.job)
            options = self.studio.load(self.job, 'wechat-options.json')
            self.assertNotEqual(options['cover_id'], original_cover)
            explicit_cover = options['cover_id']
            save({**options, 'cover_explicit':True})
            self.assertEqual(self.studio.load(self.job, 'delivery-cover.json')['mode'], 'manual')
            self.article['title'] = '原稿标题再次改变'
            self.studio.save_article(self.job, self.article)
            prepare_delivery(self.studio, self.job)
            self.assertEqual(self.studio.load(self.job, 'wechat-options.json')['cover_id'], explicit_cover)
            # Older clients can still choose a different cover without the new marker.
            save({**options, 'cover_id':self.source['figures'][0]['id']})
            self.assertEqual(self.studio.load(self.job, 'delivery-cover.json'),
                             {'mode':'manual', 'cover_id':self.source['figures'][0]['id']})
            self.assertFalse(self.api.calls)
        finally:
            server.shutdown()
            server.server_close()

    def test_generation_chains_server_side_after_client_returns(self):
        self.studio.provider = {'base_url':'http://localhost:9999/v1', 'model':'fixture', 'api_key':'SYNTHETIC_MODEL_NOT_REAL'}
        server = make_server(self.studio, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        entered = threading.Event()
        release = threading.Event()
        def generate(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Fixture release not received')
            return copy.deepcopy(self.article), [], {}
        def call(path, body):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                data=json.dumps(body).encode(), headers={'X-Studio-Token':self.studio.token,
                    'Cookie':'studio_session='+self.studio.cookie, 'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status
        try:
            with patch('server.generate_article', side_effect=generate):
                status = call(f'/api/jobs/{self.job}/generate', {'auto_draft':True, 'delivery_options':{'author':'Server Editor'}})
                self.assertEqual(status, 202)
                self.assertTrue(entered.wait(2))
                self.assertFalse(self.api.calls)
                # The caller is done; no browser completion callback requests submission.
                release.set()
                self.assertEqual(self.wait_job()['status'], 'ready')
            self.assertTrue(self.studio.jobs[self.job]['auto_draft'])
            self.assertEqual(self.api.article['author'], 'Server Editor')
            self.assertEqual(sum(p == 'draft/add' for p, _ in self.api.calls), 1)
            self.assertFalse((self.folder/'wechat-review.json').exists())
        finally:
            release.set()
            server.shutdown()
            server.server_close()

    def test_generation_failure_cancellation_and_account_change_never_deliver(self):
        self.studio.provider = {'base_url':'http://localhost:9999/v1', 'model':'fixture', 'api_key':'SYNTHETIC_MODEL_NOT_REAL'}
        server = make_server(self.studio, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def request():
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/jobs/{self.job}/generate',
                data=json.dumps({'auto_draft':True}).encode(), headers={'X-Studio-Token':self.studio.token,
                    'Cookie':'studio_session='+self.studio.cookie, 'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 202)
        try:
            for outcome in ('failure', 'cancel', 'account'):
                with self.subTest(outcome=outcome):
                    def generate(*args, **kwargs):
                        if outcome == 'failure':
                            raise SafeModelError('Fixture generation failed')
                        if outcome == 'cancel':
                            self.studio.jobs[self.job]['cancelled'] = True
                        if outcome == 'account':
                            self.studio.wechat.config['appid'] = 'wx'+'2'*16
                        return copy.deepcopy(self.article), [], {}
                    with patch('server.generate_article', side_effect=generate):
                        request()
                        self.assertEqual(self.wait_job()['status'], 'error')
                    self.assertFalse(self.api.calls)
                    self.assertFalse((self.folder/'automatic-draft-permit.json').exists())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
