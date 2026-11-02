import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from xhs_notes import prepare_note, load_note, save_note, validate_note, export_note


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'images').mkdir()
        self.source = {'title': 'Synthetic Autonomous Driving with World Models',
                       'source_url': 'https://example.org/paper', 'authors': ['Synthetic Author'],
                       'paper_info': {'project_url': 'https://example.org/project', 'code_url': 'https://example.org/code'},
                       'figures': []}
        self.article = {'title': '世界模型怎样帮助规划', 'digest': '这项合成示例研究预测与规划的联系，实验只支持文中给出的条件。',
                        'takeaways': ['方法使用未来表示作为训练信号。', '结果来自合成测试，不代表真实部署安全性。'],
                        'sections': [
                            {'kind': 'method', 'heading': '方法的输入与输出',
                             'paragraphs': [{'text': '模型以当前观测为输入，在预测表示的帮助下生成轨迹。 pages: [1, 2]'}],
                             'figures': [{'id': 'fig-001', 'explanation': '左侧为输入，右侧为预测轨迹；结构为本测试绘制。'}]},
                            {'kind': 'experiments', 'heading': '实验条件',
                             'paragraphs': [{'text': '测试只验证软件图文流程，没有报告真实论文性能。'}], 'figures': []}]}
        (self.folder / 'article.json').write_text(json.dumps(self.article, ensure_ascii=False), encoding='utf8')
        self.manuscript = (self.folder / 'article.json').read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def figure(self, number=1, caption='Figure 1. Overview of the method', size=(1000, 400), **extra):
        name = f'images/fig-{number:03}.png'
        image = Image.new('RGB', size, 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((2, 2, size[0] - 3, size[1] - 3), outline='red', width=8)
        draw.rectangle((15, 15, size[0] - 16, size[1] - 16), outline='blue', width=6)
        image.save(self.folder / name)
        figure = {'id': f'fig-{number:03}', 'image': name, 'caption': caption, 'kind': 'figure', 'selected': True, **extra}
        self.source['figures'].append(figure)
        return figure

    def test_preparation_grounding_source_and_no_manuscript_mutation(self):
        figure = self.figure()
        image_bytes = (self.folder / figure['image']).read_bytes()
        article_before = copy.deepcopy(self.article)
        source_before = copy.deepcopy(self.source)
        note = prepare_note(self.folder, self.article, self.source)
        self.assertEqual(note['editor'], 'Paper Studio')
        self.assertEqual(note['visibility'], '公开可见')
        self.assertIn(self.article['digest'], note['content'])
        self.assertIn('https://example.org/paper', note['content'])
        self.assertIn('https://example.org/project', note['content'])
        self.assertIn('https://example.org/code', note['content'])
        self.assertIn('自动驾驶', note['tags'])
        self.assertIn('世界模型', note['tags'])
        self.assertLessEqual(len(note['cards']), 9)
        self.assertEqual(note['cards'][0]['source_figure_id'], 'fig-001')
        self.assertTrue(any(card['kind'] == 'references' for card in note['cards']))
        self.assertEqual(self.article, article_before)
        self.assertEqual(self.source, source_before)
        self.assertEqual((self.folder / 'article.json').read_bytes(), self.manuscript)
        self.assertEqual((self.folder / figure['image']).read_bytes(), image_bytes)
        self.assertEqual(load_note(self.folder), note)
        self.assertEqual(load_note(self.folder)['image_ids'], note['image_ids'])

    def test_full_contain_portrait_figure_preserves_all_frame_edges(self):
        self.figure(size=(420, 1000))
        note = prepare_note(self.folder, self.article, self.source)
        card = next(card for card in note['cards'] if card['kind'] == 'figure')
        with Image.open(self.folder / card['image']) as image:
            self.assertEqual(image.size, (1080, 1440))
            red = [(x, y) for y in range(290, 1010) for x in range(72, 1008)
                   if image.getpixel((x, y))[0] > 180 and image.getpixel((x, y))[1] < 90 and image.getpixel((x, y))[2] < 90]
        self.assertTrue(red)
        self.assertGreater(max(y for x, y in red) - min(y for x, y in red), 650)
        self.assertLess(max(x for x, y in red) - min(x for x, y in red), 310)

    def test_unreviewed_fullpage_and_unselected_excluded_with_text_fallback(self):
        self.figure(1, origin='pdf_embedded', reviewed=False)
        self.figure(2, crop_status='page_fallback')
        self.figure(3, selected=False)
        note = prepare_note(self.folder, self.article, self.source)
        self.assertFalse(any(card['kind'] == 'figure' for card in note['cards']))
        self.assertNotIn('source_figure_id', note['cards'][0])
        self.assertTrue(any('仅生成文字卡片' in warning for warning in note['warnings']))

    def test_save_unicode_limits_and_failed_save_preserves_note(self):
        note = prepare_note(self.folder, self.article, self.source)
        saved = save_note(self.folder, self.article, self.source,
                          {'title': '📚' * 20, 'content': '文' * 1000, 'tags': []})
        self.assertEqual(len(saved['title']), 20)
        self.assertEqual(len(saved['content']), 1000)
        self.assertNotEqual(saved['cards'][0]['id'], note['cards'][0]['id'])
        before = (self.folder / 'xhs-note.json').read_bytes()
        for invalid in [{'title': '文' * 21}, {'content': '文' * 1001}, {'tags': ['论文']},
                        {'visibility': 'hidden'}, {'tags': ['带 空格']}, {'image_ids': []}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                save_note(self.folder, self.article, self.source, invalid)
        self.assertEqual((self.folder / 'xhs-note.json').read_bytes(), before)
        self.assertEqual((self.folder / 'article.json').read_bytes(), self.manuscript)

    def test_save_reorders_subset_and_cover_changes_follow_selected_id(self):
        self.figure()
        note = prepare_note(self.folder, self.article, self.source)
        chosen = [note['cards'][-1]['id'], note['cards'][0]['id']]
        saved = save_note(self.folder, self.article, self.source,
                          {'title': '一篇值得讨论的方法', 'image_ids': chosen, 'visibility': '仅自己可见'})
        self.assertEqual(saved['image_ids'][0], chosen[0])
        self.assertEqual(saved['image_ids'][1], saved['cards'][0]['id'])
        self.assertEqual(saved['cards'][0]['title'], '一篇值得讨论的方法')
        for values in [{'image_ids': [chosen[0], chosen[0]]}, {'image_ids': ['missing']},
                       {'cards': []}, {'api_key': 'should-not-save'}]:
            with self.assertRaises(ValueError):
                save_note(self.folder, self.article, self.source, values)

    def test_export_only_selected_images_and_public_note(self):
        self.figure()
        note = prepare_note(self.folder, self.article, self.source)
        selected = [note['cards'][-1]['id'], note['cards'][0]['id']]
        save_note(self.folder, self.article, self.source, {'image_ids': selected})
        (self.folder / 'private-settings.json').write_text('secret', encoding='utf8')
        with zipfile.ZipFile(io.BytesIO(export_note(self.folder))) as archive:
            images = [name for name in archive.namelist() if name.endswith('.png')]
            self.assertEqual(images, [f'images/01-{selected[0]}.png', f'images/02-{selected[1]}.png'])
            self.assertNotIn('private-settings.json', archive.namelist())
            exported = json.loads(archive.read('note.json'))
            self.assertEqual(exported['image_ids'], selected)
            self.assertEqual(len(exported['cards']), 2)
            self.assertNotIn(str(self.folder), archive.read('note.json').decode('utf8'))
            self.assertIn('#论文阅读', archive.read('note.txt').decode('utf8'))

    def test_traversal_duplicate_ids_corrupt_images_and_oversized_notes_rejected(self):
        note = prepare_note(self.folder, self.article, self.source)
        tampered = copy.deepcopy(note)
        tampered['cards'][0]['image'] = '../private.png'
        with self.assertRaises(ValueError):
            validate_note(self.folder, tampered)
        tampered = copy.deepcopy(note)
        tampered['cards'].append(tampered['cards'][0])
        with self.assertRaises(ValueError):
            validate_note(self.folder, tampered)
        (self.folder / note['cards'][0]['image']).write_bytes(b'not png')
        with self.assertRaises(ValueError):
            load_note(self.folder)
        (self.folder / 'xhs-note.json').write_text('x' * 1_000_001, encoding='utf8')
        with self.assertRaises(ValueError):
            load_note(self.folder)

    def test_long_article_adaptation_and_editor_update(self):
        self.article['title'] = '如何理解一种很长很长需要特别仔细讨论的自动驾驶论文方法'
        self.article['digest'] = '每一个结论都有明确的适用条件。' * 200
        self.article['takeaways'] = ['这项研究的测试条件比较有限。' * 80] * 3
        note = prepare_note(self.folder, self.article, self.source)
        self.assertLessEqual(len(note['title']), 20)
        self.assertLessEqual(len(note['content'] + '\n\n' + ' '.join('#' + tag for tag in note['tags'])), 1000)
        self.assertTrue(any('短版' in warning for warning in note['warnings']))
        updated = save_note(self.folder, self.article, self.source, {'editor': 'A different editor'})
        self.assertTrue(all(a['id'] != b['id'] for a, b in zip(note['cards'], updated['cards'])))
        self.assertEqual(updated['editor'], 'A different editor')

    def test_diverse_figures_survive_many_method_candidates(self):
        for number in range(1, 8):
            self.figure(number, f'Figure {number}. Overview of module {number}')
        self.figure(8, 'Figure 8. Quantitative performance results')
        self.figure(9, 'Figure 9. Qualitative visualization of failure cases')
        note = prepare_note(self.folder, self.article, self.source)
        selected = [card['source_figure_id'] for card in note['cards'] if card['kind'] == 'figure']
        self.assertEqual(selected[:3], ['fig-001', 'fig-008', 'fig-009'])
        self.assertEqual(len(note['cards']), 9)
        self.assertTrue(any('精选' in warning for warning in note['warnings']))


if __name__ == '__main__':
    unittest.main()
