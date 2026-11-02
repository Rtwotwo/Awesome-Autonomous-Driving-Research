import copy
import unittest
from html import escape
from wechat_content import (plan_parts, preview_parts, Signature, Tree, planned_size,
                            check_final_content, PLAN_LIMIT, CHAR_LIMIT)


class ContentPartsTests(unittest.TestCase):
    def setUp(self):
        self.options = {'title': '标题'*16, 'digest': '原始摘要', 'author': 'Editor',
                        'cover_id': 'fig-1', 'source_url': 'https://example.test/paper'}

    def assert_lossless(self, original, parts):
        before, after = Signature(original), Signature(''.join(p['content'] for p in parts))
        self.assertEqual(''.join(before.text), ''.join(after.text))
        self.assertEqual(before.images, after.images)
        for part in parts:
            Tree(part['content'])
            check_final_content(part['content'])
            self.assertLess(planned_size(part['content']), PLAN_LIMIT)
            self.assertLessEqual(len(part['options']['title']), 32)

    def test_short_body_and_options_unchanged(self):
        content='<section><h2>标题</h2><p>全文 &amp; 实验。</p><img src="images/a.png"></section>'
        parts=plan_parts(content,self.options)
        self.assertEqual(parts,[{'content':content,'options':self.options}])
        self.assertIsNot(parts[0]['options'],self.options)
        self.assertEqual(preview_parts({'content':content,'options':self.options}),content)

    def test_rich_article_preserves_text_links_tables_and_image_captions(self):
        figure='<section><img src="images/a.png"><p>图一：完整架构</p></section>'
        table='<table><tr><th>PDMS</th><th>Score</th></tr><tr><td>A</td><td>90.2</td></tr></table><p>表一：评测条件。</p>'
        link='<a href="https://example.test/?a=1&amp;b=2">论文源码</a>'
        content='<section style="color:#234d43">'+('<h2>方法机制</h2><p>'+('研究正文，机制解释。'*85)+link+'</p>'+figure+table)*20+'</section>'
        options=copy.deepcopy(self.options)
        parts=plan_parts(content,self.options)
        self.assertGreater(len(parts),1)
        self.assert_lossless(content,parts)
        joined=''.join(p['content'] for p in parts)
        self.assertEqual(joined.count(table),20)
        self.assertEqual(joined.count(figure),20)
        self.assertEqual(joined.count(link),20)
        self.assertEqual(self.options,options)
        self.assertTrue(all(p['options']['digest']=='' for p in parts))
        preview=preview_parts({'parts':parts})
        self.assertIn('第 1/',preview)
        self.assertNotIn('第 1/',parts[0]['content'])

    def test_oversized_plain_paragraph_splits_without_truncation(self):
        content='<section><p>'+('句子。'*10000)+'</p></section>'
        parts=plan_parts(content,self.options)
        self.assert_lossless(content,parts)
        self.assertEqual(len(parts),2)

    def test_entities_unicode_and_nested_emphasis(self):
        content='<section><p>'+('🌏汉字 &amp; &lt;数据&gt; &#x4e2d; &nbsp; <b>强调</b>。'*1200)+'</p></section>'
        self.assert_lossless(content,plan_parts(content,self.options))

    def test_image_url_growth_is_reserved_and_final_size_checked(self):
        content='<section><p>'+('x'*17400)+'</p>'+('<img src="images/a.png">'*3)+'</section>'
        parts=plan_parts(content,self.options)
        self.assertGreater(len(parts),1)
        self.assert_lossless(content,parts)
        for p in parts:
            grown=p['content'].replace('images/a.png','https://mmbiz.qpic.cn/'+'a'*900)
            check_final_content(grown)
        with self.assertRaises(ValueError):check_final_content('x'*CHAR_LIMIT)

    def test_huge_indivisible_table_and_more_than_eight_parts_stop(self):
        with self.assertRaisesRegex(ValueError,'表格'):
            plan_parts('<table><tr><td>'+'x'*21000+'</td></tr></table>',self.options)
        with self.assertRaisesRegex(ValueError,'8'):
            plan_parts(''.join('<p>'+('x'*18000)+'</p>' for _ in range(9)),self.options)

    def test_table_just_below_actual_limit_fits_on_its_own_part(self):
        table='<table><tr><td>'+('x'*19500)+'</td></tr></table>'
        content='<section><p>'+('before'*400)+'</p>'+table+'</section>'
        parts=plan_parts(content,self.options)
        self.assertEqual(len(parts),2)
        self.assertIn(table,parts[1]['content'])
        self.assert_lossless(content,parts)

    def test_malformed_long_html_is_not_silently_repaired(self):
        with self.assertRaisesRegex(ValueError,'标签'):
            plan_parts('<p>'+'x'*22000+'</section>',self.options)


if __name__=='__main__':unittest.main()
