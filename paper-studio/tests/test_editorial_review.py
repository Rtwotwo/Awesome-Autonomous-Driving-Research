import copy
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from editorial_review import (MAX_PROMPT_CHARS, MAX_UNIT_CHARS, apply_review, encoded,
                              make_plan, review_prompt, unit_content, valid_state)
from model_transport import SafeModelError
from sources import extract_pdf, write_json
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from writing import generate_article, generation_fingerprint, read_generation_draft, selected_figures, validate_article, provider_config, source_chunks, SchemaResponseError, _source_signature


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = extract_pdf(fixture_pdf(), self.folder)
        self.article = fixture_article(self.manifest)
        original = copy.deepcopy(self.article['sections'])
        self.article['sections'] = []
        for index in range(11):
            section = copy.deepcopy(original[index % len(original)])
            section['heading'] = f'第 {index + 1} 个分析主题'
            section['paragraphs'] = [{'text': f'BODY_MARKER_{index}_' + '研究机制与实验条件。' * 80, 'pages': [1]},
                                     {'text': '证据支持的解释与适用边界。' * 65, 'pages': [1]}]
            self.article['sections'].append(section)
        self.options = {'base_url': 'http://127.0.0.1:9999', 'model': 'fixture', '_desktop': True}
        self.notes = {'facts': [{'claim': 'fixture', 'page': 1,
                                'quote': 'The reported success rate is 83.2 percent.'}]}

    def response(self, config, prompt, *args, **kwargs):
        if prompt.startswith('按原页码'):
            return copy.deepcopy(self.notes)
        if '\n待复核单元：\n' in prompt:
            return json.loads(prompt.rsplit('\n待复核单元：\n', 1)[1])
        return copy.deepcopy(self.article)

    def checkpoint(self):
        return json.loads((self.folder / 'generation-progress.json').read_text(encoding='utf-8'))

    def test_empty_attachment_zero_and_null_are_normalized_without_mutation(self):
        unit = make_plan(self.article)[0]
        original = unit_content(self.article, unit)
        for value in (0, None):
            result = copy.deepcopy(original)
            result['section']['tables'] = value
            self.assertFalse(original['section'].get('tables'))
            candidate = apply_review(self.article, unit, result, lambda a: validate_article(a, self.manifest))
            self.assertEqual(candidate['sections'][0]['tables'], [])
            self.assertIs(result['section']['tables'], value)
        result = copy.deepcopy(original);result['section']['tables'] = False
        with self.assertRaises(SafeModelError):
            apply_review(self.article, unit, result, lambda a: validate_article(a, self.manifest))

    def test_nonempty_figures_cannot_be_normalized_away(self):
        unit = next(u for u in make_plan(self.article) if u.get('section') == 2)
        result = unit_content(self.article, unit)
        self.assertTrue(result['section']['figures'])
        result['section']['figures'] = 0
        with self.assertRaises(SafeModelError):
            apply_review(self.article, unit, result, lambda a: validate_article(a, self.manifest))

    def test_invalid_unit_repairs_automatically_without_repeating_previous_units(self):
        attempts = []
        def response(config, prompt, *args, **kwargs):
            result = self.response(config, prompt, *args, **kwargs)
            if 'section' in result:
                attempts.append(result['section']['heading'])
                if len(attempts) == 3:
                    result['section']['tables'] = 'invalid'
            return result
        with patch('writing.chat_json', side_effect=response):
            article, _, _ = generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(attempts[2], attempts[3])
        self.assertEqual(attempts.count(attempts[0]), 1)
        self.assertTrue(self.checkpoint()['complete'])
        self.assertEqual(len(article['sections']), 11)

    def test_missing_captions_recovered_without_restarting_saved_review(self):
        self.manifest['figures'] = []
        self.manifest['warnings'] = ['未定位到标准图注；可从原文页拖选图片区域。']
        for section in self.article['sections']:
            section['figures'] = []
        options = dict(self.options, auto_figures=True, _figure_folder=self.folder)
        fp = generation_fingerprint(self.manifest, options, provider_config(options), source_chunks(self.manifest))
        plan = make_plan(self.article)
        write_json(self.folder/'generation-progress.json', {'fingerprint': fp, 'draft': self.article,
            'notes': [self.notes], 'review': {'version': 1, 'units': plan, 'completed': [u['id'] for u in plan[:5]]},
            'review_complete': False, 'stage': '编辑复核 6/12'})
        def respond(config, prompt, *args, **kwargs):
            if '仅补充漏图' in prompt:
                return {'placements': [{'id': 'fig-001', 'section_index': 2, 'after_paragraph': 1,
                                        'caption': '测试架构', 'explanation': '图注描述规划架构。'}]}
            self.assertNotIn('按原页码提取', prompt)
            return self.response(config, prompt, *args, **kwargs)
        with patch('native_figures.improve_native_figures'), patch('writing.chat_json', side_effect=respond) as chat:
            result, _, _ = generate_article(self.manifest, self.folder, options)
        self.assertEqual(len(self.manifest['figures']), 1)
        self.assertEqual(chat.call_count, 8) # Seven remaining review units + attachments.
        self.assertIn('BODY_MARKER_5_', chat.call_args_list[0].args[1])
        self.assertTrue(self.checkpoint()['complete'])
        self.assertEqual(result['sections'][2]['figures'][0]['id'], 'fig-001')
        self.assertFalse(any(w.startswith('未定位到标准图注') for w in self.manifest['warnings']))

    def test_front_matter_metadata_error_is_repaired_and_json_decode_retries(self):
        self.article['paper'] = {'title': 'Fixture', 'authors': ['Test Author']}
        front_calls = []
        decode_failed = [False]
        def response(config, prompt, *args, **kwargs):
            result = self.response(config, prompt, *args, **kwargs)
            if 'section' in result and not decode_failed[0]:
                decode_failed[0] = True
                raise SchemaResponseError('模型没有返回有效 JSON。')
            if 'front_matter' in result:
                front_calls.append(True)
                if len(front_calls) == 1: result['front_matter']['paper']['authors'] = 0
            return result
        with patch('writing.chat_json', side_effect=response):
            article, _, _ = generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(len(front_calls), 2)
        self.assertEqual(article['paper']['authors'], ['Test Author'])
        self.assertTrue(self.checkpoint()['complete'])

    def test_bad_attachment_types_raise_safe_schema_errors(self):
        for field, value in [('figures', 0), ('tables', None), ('figures', [{'id': []}])]:
            bad = copy.deepcopy(self.article)
            bad['sections'][2][field] = value
            with self.assertRaises(SafeModelError): validate_article(bad, self.manifest)

    def test_native_upgrade_cancellation_preserves_durable_checkpoint_and_source(self):
        from native_figures import improve_native_figures
        from PIL import Image
        import io
        self.manifest['arxiv_id'] = '2610.12345'
        write_json(self.folder/'source.json', self.manifest)
        original_source = (self.folder/'source.json').read_bytes()
        original_image = (self.folder/self.manifest['figures'][0]['image']).read_bytes()
        options = dict(self.options, auto_figures=True, _figure_folder=self.folder)
        fp = generation_fingerprint(self.manifest, options, provider_config(options), source_chunks(self.manifest))
        plan = make_plan(self.article)
        saved = {'fingerprint': fp, 'draft': self.article, 'notes': [self.notes],
                 'review': {'version': 1, 'units': plan, 'completed': [u['id'] for u in plan[:5]]}}
        write_json(self.folder/'generation-progress.json', saved)
        image = io.BytesIO();Image.new('RGB',(400,100),'blue').save(image,'PNG')
        def fetch(url, limit):
            return image.getvalue() if url.endswith('.png') else b'<figure><img src="x.png">Figure 1: Architecture</figure>'
        stopped = [False]
        def native(source, folder, progress, **kwargs):
            improve_native_figures(source, folder, lambda _:None, fetch=fetch, **kwargs)
            stopped[0] = True
            progress('stop after image replacement')
        with patch('native_figures.improve_native_figures', side_effect=native), patch('writing.chat_json') as chat:
            with self.assertRaisesRegex(SafeModelError, '取消'):
                generate_article(self.manifest, self.folder, options, cancelled=lambda:stopped[0])
        chat.assert_not_called()
        self.assertEqual((self.folder/'source.json').read_bytes(),original_source)
        self.assertEqual((self.folder/self.manifest['figures'][0]['image']).read_bytes(),original_image)
        self.assertEqual(self.checkpoint(),saved)
        with patch('native_figures.improve_native_figures'), patch('writing.chat_json', side_effect=self.response) as chat:
            generate_article(self.manifest, self.folder, options)
        self.assertEqual(chat.call_count,7)

    def test_chained_asset_transition_write_failure_keeps_original_checkpoint_bridge(self):
        first = copy.deepcopy(self.manifest);first['figures'] = []
        for section in self.article['sections']: section['figures'] = []
        options = dict(self.options, auto_figures=True, _figure_folder=self.folder)
        fp = generation_fingerprint(first, options, provider_config(options), source_chunks(first))
        plan = make_plan(self.article)
        write_json(self.folder/'generation-progress.json', {'fingerprint':fp, 'draft':self.article, 'notes':[self.notes],
            'review':{'version':1,'units':plan,'completed':[u['id'] for u in plan[:5]]}})
        # A -> B committed, but generation has not yet adopted B.
        second = copy.deepcopy(self.manifest)
        write_json(self.folder/'source.json',second)
        write_json(self.folder/'generation-asset-migration.json',{'previous_manifest':first,'current_signature':_source_signature(second)})
        def upgrade(source, *args, **kwargs): source['native_figures_checked'] = {'replaced':[]}
        real_write = write_json
        def interrupt(path, value):
            if Path(path).name == 'source.json': raise OSError('synthetic disk interruption')
            real_write(path, value)
        with patch('native_figures.improve_native_figures',side_effect=upgrade), patch('writing.write_json',side_effect=interrupt):
            with self.assertRaises(OSError): generate_article(second,self.folder,options)
        durable = json.loads((self.folder/'source.json').read_text('utf8'))
        self.assertNotIn('native_figures_checked',durable)
        def respond(config,prompt,*args,**kwargs):
            if '仅补充漏图' in prompt:
                return {'placements':[{'id':'fig-001','section_index':2,'after_paragraph':1,'caption':'架构','explanation':'原图注说明。'}]}
            self.assertNotIn('按原页码提取',prompt)
            return self.response(config,prompt,*args,**kwargs)
        with patch('native_figures.improve_native_figures'), patch('writing.chat_json',side_effect=respond) as chat:
            generate_article(durable,self.folder,options)
        self.assertEqual(chat.call_count,8)
        self.assertIn('BODY_MARKER_5_',chat.call_args_list[0].args[1])
        self.assertTrue(self.checkpoint()['complete'])



    def test_long_review_resumes_only_remaining_units_with_user_cap(self):
        unit_calls = []
        def disconnect(config, prompt, *args, **kwargs):
            if '\n待复核单元：\n' in prompt:
                unit_calls.append(prompt)
                if len(unit_calls) == 3:
                    raise http.client.RemoteDisconnected('PRIVATE_TRANSPORT_VALUE')
            return self.response(config, prompt, *args, **kwargs)
        existing = {'original': 'existing final article must not change'}
        write_json(self.folder / 'article.json', existing)
        with patch('writing.chat_json', side_effect=disconnect):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, self.options)
        checkpoint = self.checkpoint()
        self.assertEqual(len(checkpoint['review']['completed']), 2)
        self.assertFalse(checkpoint['complete'])
        self.assertEqual(len(checkpoint['draft']['sections']), 11)
        view = read_generation_draft(self.folder, self.manifest)
        self.assertEqual((view['completed_units'], view['total_units']), (2, 12))
        self.assertFalse(view['complete']);self.assertFalse(view['review_complete'])
        self.assertNotIn('PRIVATE_TRANSPORT_VALUE', json.dumps(checkpoint))
        notices = []
        with patch('writing.chat_json', side_effect=self.response) as chat:
            result, _, _ = generate_article(self.manifest, self.folder,
                                            dict(self.options, output_tokens=4096, request_timeout=900, response_mode='json'),
                                            progress=notices.append)
        self.assertEqual(chat.call_count, 10)
        self.assertIn('BODY_MARKER_2_', chat.call_args_list[0].args[1])
        self.assertNotIn('BODY_MARKER_0_', chat.call_args_list[0].args[1])
        self.assertTrue(all(c.kwargs['max_tokens'] <= 4096 for c in chat.call_args_list))
        self.assertTrue(all(len(c.args[1]) <= MAX_PROMPT_CHARS for c in chat.call_args_list))
        self.assertEqual(len(result['sections']), 11)
        self.assertTrue(self.checkpoint()['review_complete']);self.assertTrue(self.checkpoint()['complete'])
        self.assertEqual(json.loads((self.folder / 'article.json').read_text()), existing)
        self.assertTrue(any('实际用时' in message for message in notices))
        self.assertTrue(all(type(t['elapsed_seconds']) in (int, float) for t in self.checkpoint()['timings']))

    def test_cancel_after_saved_unit_resumes_next_unit(self):
        cancel = [False]
        def progress(message):
            if '1/12 已校验并保存' in message:
                cancel[0] = True
        with patch('writing.chat_json', side_effect=self.response):
            with self.assertRaisesRegex(SafeModelError, '取消'):
                generate_article(self.manifest, self.folder, self.options, progress, lambda: cancel[0])
        self.assertEqual(len(self.checkpoint()['review']['completed']), 1)
        self.assertFalse(self.checkpoint()['complete'])
        with patch('writing.chat_json', side_effect=self.response) as chat:
            generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(chat.call_count, 11)
        self.assertIn('BODY_MARKER_1_', chat.call_args_list[0].args[1])

    def test_dropped_section_or_paragraph_is_rejected_without_advancing(self):
        def invalid(config, prompt, *args, **kwargs):
            response = self.response(config, prompt, *args, **kwargs)
            if 'section' in response:
                response['section']['paragraphs'] = []
            return response
        with patch('writing.chat_json', side_effect=invalid):
            with self.assertRaisesRegex(SafeModelError, '遗漏段落'):
                generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(self.checkpoint()['review']['completed'], [])
        self.assertEqual(len(self.checkpoint()['draft']['sections']), 11)
        self.assertFalse(self.checkpoint()['review_complete'])
        self.assertFalse((self.folder / 'article.json').exists())

    def test_prompt_selects_relevant_pages_and_notes_with_explicit_excerpt(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['pages'][0]['text'] = 'RELEVANT_SOURCE ' * 3000
        manifest['pages'].append({'number': 99, 'text': 'UNRELATED_SOURCE'})
        notes = [{'facts': [{'claim': 'RELEVANT_NOTE', 'page': 1, 'quote': 'x'}]},
                 {'facts': [{'claim': 'UNRELATED_NOTE', 'page': 99, 'quote': 'x'}]}]
        unit = make_plan(self.article)[0]
        prompt, _, budget = review_prompt(self.article, unit, manifest, notes, [{1}, {99}])
        self.assertIn('RELEVANT_SOURCE', prompt);self.assertIn('RELEVANT_NOTE', prompt)
        self.assertNotIn('UNRELATED_SOURCE', prompt);self.assertNotIn('UNRELATED_NOTE', prompt)
        self.assertIn('原页摘录', prompt)
        self.assertLessEqual(len(prompt), MAX_PROMPT_CHARS);self.assertLessEqual(budget, 12000)

    def test_oversized_chapter_splits_without_losing_figures_or_paragraphs(self):
        article = copy.deepcopy(self.article)
        section = article['sections'][2]
        section['paragraphs'] *= 8
        section['figures'][0]['after_paragraph'] = 10
        plan = make_plan(article)
        units = [u for u in plan if u.get('section') == 2]
        self.assertGreater(len(units), 1)
        self.assertTrue(all(len(encoded(unit_content(article, u))) <= MAX_UNIT_CHARS for u in units))
        for unit in units:
            response = unit_content(article, unit)
            article = apply_review(article, unit, response, lambda a: validate_article(a, self.manifest))
        self.assertEqual(len(article['sections'][2]['paragraphs']), 16)
        self.assertEqual(article['sections'][2]['figures'][0]['after_paragraph'], 10)
        self.assertTrue(valid_state({'version': 1, 'units': plan, 'completed': []}, article))
        self.assertFalse(valid_state({'version': 1, 'units': plan[1:], 'completed': []}, article))

    def test_read_only_helper_legacy_validation_and_private_field_exclusion(self):
        draft = copy.deepcopy(self.article)
        draft['api_key'] = 'PRIVATE_KEY'
        draft['sections'][0]['paragraphs'][0]['reasoning_content'] = 'PRIVATE_REASONING'
        write_json(self.folder / 'generation-progress.json', {'draft': draft, 'notes': ['PRIVATE_NOTES'], 'complete': False})
        write_json(self.folder / 'generation-partial.json', {'text': 'PRIVATE_PARTIAL'})
        view = read_generation_draft(self.folder, self.manifest)
        self.assertIsNotNone(view);self.assertFalse(view['complete'])
        self.assertNotIn('PRIVATE_', json.dumps(view))
        self.assertEqual(view['total_units'], 1)
        write_json(self.folder / 'generation-progress.json', {'draft': {'title': 'missing chapters'}})
        self.assertIsNone(read_generation_draft(self.folder, self.manifest))

    def test_review_saved_before_figure_repair_and_not_repeated(self):
        self.article = fixture_article(self.manifest)
        self.article['sections'][2]['figures'] = []
        options = dict(self.options, auto_figures=True)
        missing = ['图示待补充']
        with patch('writing.coverage', return_value=missing), patch('native_figures.improve_native_figures'), \
                patch('writing.chat_json', side_effect=[self.notes, self.article, self.article, http.client.RemoteDisconnected()]):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, options)
        self.assertTrue(self.checkpoint()['review_complete']);self.assertFalse(self.checkpoint()['complete'])
        view = read_generation_draft(self.folder, self.manifest)
        self.assertTrue(view['review_complete']);self.assertEqual(view['stage'], '图文补全')
        placements = {'placements': [{'id': self.manifest['figures'][0]['id'], 'section_index': 2,
                      'after_paragraph': 1, 'caption': '示例架构', 'explanation': '来自测试论文图注。'}]}
        with patch('writing.coverage', return_value=missing), patch('native_figures.improve_native_figures'), \
                patch('writing.chat_json', return_value=placements) as chat:
            generate_article(self.manifest, self.folder, options)
        self.assertEqual(chat.call_count, 1)
        self.assertIn('漏图', chat.call_args.args[1])

    def test_saved_draft_rejected_after_material_changes(self):
        with patch('writing.chat_json', side_effect=[self.notes, self.article, http.client.RemoteDisconnected()]):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, self.options)
        changed = copy.deepcopy(self.manifest)
        changed['pages'][0]['text'] += ' changed'
        self.assertIsNone(read_generation_draft(self.folder, changed))

    def test_invalid_completion_flag_cannot_skip_unfinished_units(self):
        def fail(config, prompt, *args, **kwargs):
            if '\n待复核单元：\n' in prompt:
                raise http.client.RemoteDisconnected()
            return self.response(config, prompt, *args, **kwargs)
        with patch('writing.chat_json', side_effect=fail):
            with self.assertRaisesRegex(SafeModelError, '实际用时'):
                generate_article(self.manifest, self.folder, self.options)
        saved = self.checkpoint()
        saved['review_complete'] = True
        write_json(self.folder / 'generation-progress.json', saved)
        self.assertFalse(read_generation_draft(self.folder, self.manifest)['review_complete'])
        with patch('writing.chat_json', side_effect=self.response) as chat:
            generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(chat.call_count, 12)

    def test_front_matter_must_retain_all_requested_fields(self):
        unit = make_plan(self.article)[-1]
        response = unit_content(self.article, unit)
        del response['front_matter']['title']
        with self.assertRaisesRegex(SafeModelError, '遗漏'):
            apply_review(self.article, unit, response, lambda a: validate_article(a, self.manifest))
        self.assertIn('title', self.article)

    def test_automatic_draft_accepts_fallback_as_warning_without_forging_review(self):
        figure = self.manifest['figures'][0]
        figure.update(crop_status='page_fallback', reviewed=False, selected=True)
        for value in (False, None, 'true'):
            with self.assertRaisesRegex(SafeModelError, '整页回退'):
                selected_figures(self.manifest, dict(self.options, automatic_draft=value))
        self.article = fixture_article(self.manifest)
        notices = []
        with patch('writing.chat_json', side_effect=self.response):
            article, _, audit = generate_article(self.manifest, self.folder,
                                                 dict(self.options, automatic_draft=True), notices.append)
        self.assertFalse(figure['reviewed'])
        self.assertTrue(any('整页配图' in message for message in notices))
        self.assertTrue(any('尚未确认裁剪' in item['message'] for item in audit['issues']))
        self.assertEqual(article['sections'][2]['figures'][0]['id'], figure['id'])

    def test_completed_checkpoint_recovers_exact_final_without_model_request(self):
        self.article = fixture_article(self.manifest)
        with patch('writing.chat_json', side_effect=self.response):
            expected = generate_article(self.manifest, self.folder, self.options)
        saved = self.checkpoint()
        self.assertEqual(saved['draft'], expected[0])
        self.assertIn('generated_at', saved['draft']['generation'])
        self.assertFalse((self.folder / 'article.json').exists())
        view = read_generation_draft(self.folder, self.manifest)
        self.assertTrue(view['complete']);self.assertTrue(view['review_complete'])
        self.assertEqual(view['article']['generation'], expected[0]['generation'])
        with patch('writing.chat_json') as chat:
            result = generate_article(self.manifest, self.folder, self.options)
            chat.assert_not_called()
        self.assertEqual(result, expected)

    def test_force_regenerate_explicitly_bypasses_completed_cache(self):
        self.article = fixture_article(self.manifest)
        with patch('writing.chat_json', side_effect=self.response):
            generate_article(self.manifest, self.folder, self.options)
        self.article['title'] = '明确重新生成的文章'
        with patch('writing.chat_json', side_effect=self.response) as chat:
            result, _, _ = generate_article(self.manifest, self.folder, dict(self.options, force_regenerate=True))
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(result['title'], self.article['title'])

    def test_legacy_complete_checkpoint_reuses_initial_draft_but_still_reviews(self):
        self.article = fixture_article(self.manifest)
        with patch('writing.chat_json', side_effect=self.response):
            generate_article(self.manifest, self.folder, self.options)
        saved = self.checkpoint()
        saved.pop('review_complete')
        saved['draft'].pop('generation')
        write_json(self.folder / 'generation-progress.json', saved)
        with patch('writing.chat_json', side_effect=self.response) as chat:
            generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(chat.call_count, 1)
        self.assertIn('请审校', chat.call_args.args[1])

    def test_long_paragraph_slices_resume_after_length_change_without_losing_text(self):
        text = '机制解释与实验条件。' * 2600
        self.article['sections'][0]['paragraphs'][0]['text'] = text
        calls = []
        def edit_then_fail(config, prompt, *args, **kwargs):
            response = self.response(config, prompt, *args, **kwargs)
            if '\n待复核单元：\n' in prompt:
                calls.append(prompt)
                if len(calls) == 2:
                    raise http.client.RemoteDisconnected()
                response['section']['paragraphs'][0]['text'] = '已核对：' + response['section']['paragraphs'][0]['text']
            return response
        with patch('writing.chat_json', side_effect=edit_then_fail):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, self.options)
        saved = self.checkpoint()
        self.assertEqual(len(saved['review']['completed']), 1)
        self.assertTrue(valid_state(saved['review'], saved['draft']))
        self.assertEqual(saved['review']['units'][1]['char_start'], saved['review']['units'][0]['char_stop'])
        with patch('writing.chat_json', side_effect=self.response) as chat:
            result, _, _ = generate_article(self.manifest, self.folder, self.options)
        self.assertEqual(chat.call_count, len(saved['review']['units']) - 1)
        self.assertEqual(result['sections'][0]['paragraphs'][0]['text'], '已核对：' + text)
        self.assertEqual(len(result['sections']), len(self.article['sections']))
        self.assertEqual(len(result['sections'][0]['paragraphs']), 2)

    def test_long_paragraph_preserves_attached_figure_position(self):
        article = copy.deepcopy(self.article)
        section = article['sections'][2]
        section['paragraphs'][0]['text'] = '图示机制支持了文中解释。' * 1800
        section['figures'][0]['after_paragraph'] = 1
        before = copy.deepcopy(article)
        plan = make_plan(article)
        units = [u for u in plan if u.get('section') == 2]
        self.assertGreater(len(units), 2)
        for unit in units:
            self.assertLessEqual(len(encoded(unit_content(article, unit))), MAX_UNIT_CHARS)
            article = apply_review(article, unit, unit_content(article, unit), lambda a: validate_article(a, self.manifest))
        self.assertEqual(article['sections'][2]['paragraphs'], before['sections'][2]['paragraphs'])
        self.assertEqual(article['sections'][2]['figures'], before['sections'][2]['figures'])

    def test_large_claim_list_is_grouped_and_all_items_remain_in_order(self):
        article = copy.deepcopy(self.article)
        article['claims'] = [{'claim': f'Claim {index}', 'page': 1, 'quote': 'Synthetic evidence text. ' * 18}
                             for index in range(60)]
        original = copy.deepcopy(article['claims'])
        plan = make_plan(article)
        grouped = [u for u in plan if u['type'] == 'front_list']
        self.assertGreater(len(grouped), 1)
        self.assertTrue(valid_state({'version': 1, 'units': plan, 'completed': []}, article))
        for unit in grouped:
            content = unit_content(article, unit)
            self.assertLessEqual(len(encoded(content)), MAX_UNIT_CHARS)
            article = apply_review(article, unit, content, lambda a: validate_article(a, self.manifest))
        self.assertEqual(article['claims'], original)
        invalid = unit_content(article, grouped[0])
        invalid['front_matter']['claims'].pop()
        with self.assertRaisesRegex(SafeModelError, '项数'):
            apply_review(article, grouped[0], invalid, lambda a: validate_article(a, self.manifest))

    def large_attached_tables(self):
        section = self.article['sections'][2]
        section['figures'][0]['after_paragraph'] = 1
        tables = []
        for index in range(2):
            table = {'title': f'Table {index + 1}', 'columns': ['Method', 'Result'],
                     'rows': [[f'Method {index + 1}', 'x' * 5900]], 'note': '',
                     'source_label': f'Table {index + 1}', 'pages': [1], 'after_paragraph': 1}
            table['note'] = 'n' * (6175 - len(encoded(table)))
            self.assertEqual(len(encoded(table)), 6175)
            tables.append(table)
        section['tables'] = tables
        validate_article(self.article, self.manifest)
        return tables

    def test_two_legal_tables_over_combined_cap_are_separate_bounded_units(self):
        original_tables = copy.deepcopy(self.large_attached_tables())
        before = copy.deepcopy(self.article)
        plan = make_plan(self.article)
        state = {'version': 1, 'units': plan, 'completed': []}
        groups = [u for u in plan if u['type'] == 'attachments']
        self.assertEqual(len(groups), 2)
        self.assertTrue(valid_state(state, self.article))
        self.assertTrue(all(len(encoded(unit_content(self.article, u))) <= MAX_UNIT_CHARS for u in plan))
        article = copy.deepcopy(self.article)
        for unit in plan:
            article = apply_review(article, unit, unit_content(article, unit), lambda a: validate_article(a, self.manifest))
        self.assertEqual(article['sections'][2]['tables'], original_tables)
        self.assertEqual(article['sections'][2]['figures'], before['sections'][2]['figures'])
        self.assertEqual(article['sections'][2]['paragraphs'], before['sections'][2]['paragraphs'])
        self.assertNotIn('_review_id', encoded(article))
        self.assertFalse(valid_state(dict(state, units=[u for u in plan if u is not groups[-1]]), article))
        duplicated = copy.deepcopy(plan)
        last = next(u for u in duplicated if u['id'] == groups[-1]['id'])
        last['items'] = groups[0]['items']
        self.assertFalse(valid_state(dict(state, units=duplicated), article))
        for change in ('position', 'identity', 'missing'):
            response = unit_content(article, groups[-1])
            if change == 'position':
                response['attachments']['tables'][0]['after_paragraph'] = 2
            elif change == 'identity':
                response['attachments']['tables'][0]['_review_id'] = 'tables-0'
            else:
                response['attachments']['tables'] = []
            with self.assertRaises(SafeModelError):
                apply_review(article, groups[-1], response, lambda a: validate_article(a, self.manifest))

    def test_attachment_groups_resume_without_repeating_saved_table(self):
        original_tables = copy.deepcopy(self.large_attached_tables())
        attachment_calls = []
        def first_table_then_disconnect(config, prompt, *args, **kwargs):
            response = self.response(config, prompt, *args, **kwargs)
            if 'attachments' in response:
                attachment_calls.append(prompt)
                if len(attachment_calls) == 2:
                    raise http.client.RemoteDisconnected()
                response['attachments']['tables'][0]['note'] += ' 已复核'
            return response
        with patch('writing.chat_json', side_effect=first_table_then_disconnect):
            with self.assertRaises(SafeModelError):
                generate_article(self.manifest, self.folder, self.options)
        saved = self.checkpoint()
        self.assertFalse(saved['complete'])
        self.assertTrue(valid_state(saved['review'], saved['draft']))
        groups = [u for u in saved['review']['units'] if u['type'] == 'attachments']
        self.assertIn(groups[0]['id'], saved['review']['completed'])
        self.assertNotIn(groups[1]['id'], saved['review']['completed'])
        with patch('writing.chat_json', side_effect=self.response) as chat:
            article, _, _ = generate_article(self.manifest, self.folder, self.options)
        first = json.loads(chat.call_args_list[0].args[1].rsplit('\n待复核单元：\n', 1)[1])
        self.assertEqual(first['attachments']['tables'][0]['source_label'], 'Table 2')
        result = article['sections'][2]['tables']
        self.assertEqual([t['title'] for t in result], [t['title'] for t in original_tables])
        self.assertEqual([t['after_paragraph'] for t in result], [1, 1])
        self.assertEqual(result[0]['note'], original_tables[0]['note'] + ' 已复核')
        self.assertEqual(result[1], original_tables[1])

    def test_one_individually_oversized_table_still_has_explicit_atomic_limit(self):
        self.large_attached_tables()
        self.article['sections'][2]['tables'][0]['note'] = 'x' * MAX_UNIT_CHARS
        with self.assertRaisesRegex(SafeModelError, '单个图表超过'):
            make_plan(self.article)


if __name__ == '__main__':
    unittest.main()
