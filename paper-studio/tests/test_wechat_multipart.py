import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server import Studio
from sources import extract_pdf
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from tests.test_wechat_api import FakeAPI
from delivery import prepare_delivery, deliver_article
from wechat_api import Account, TransportError, submit_draft, recheck
from wechat_review import prepare, approve, CHECKS, is_approved
from wechat_content import Signature


class MultiAPI(FakeAPI):
    def __init__(self):
        super().__init__(); self.articles=[]; self.mutate=None; self.long_url=False

    def __call__(self,path,**kw):
        if path=='draft/get':
            self.calls.append((path,kw)); result=copy.deepcopy(self.articles)
            if self.mutate:self.mutate(result)
            return {'news_item':result}
        result=super().__call__(path,**kw)
        if path=='draft/add':self.articles=copy.deepcopy(kw['payload']['articles'])
        if path=='media/uploadimg' and self.long_url:return {'url':'https://mmbiz.qpic.cn/'+'a'*22000}
        return result


class MultipartTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.api=MultiAPI();self.account=Account(request=self.api)
        self.account.save({'appid':'wx'+'1'*16,'secret':'SYNTHETIC_NOT_REAL_12345','name':'Fixture'})
        self.source=extract_pdf(fixture_pdf(),self.root/'paper')
        self.article=fixture_article(self.source)
        self.article['lead']='完整研究机制、指标条件和结果边界。'*1500
        self.options={'title':'长文自动投递','author':'Editor','digest':'摘要','cover_id':self.source['figures'][0]['id']}
        self.prepared=prepare(self.root/'paper',self.article,self.source,self.options,self.account.public())
        approve(self.root/'paper',self.prepared,{k:True for k in CHECKS})

    def tearDown(self):
        self.account.close();self.tmp.cleanup()

    def receipt(self):return json.loads((self.root/'paper/wechat-submission.json').read_text('utf-8'))
    def adds(self):return sum(p=='draft/add' for p,_ in self.api.calls)
    def send(self):return submit_draft(self.root/'paper',self.prepared,self.account)

    def test_one_create_shared_cover_each_part_verified_and_repeat_blocked(self):
        record=self.send();self.assertEqual(record['state'],'submitted')
        self.assertEqual(self.adds(),1)
        self.assertEqual(len(self.api.articles),len(self.prepared['parts']))
        self.assertGreater(len(self.api.articles),1)
        self.assertTrue(record['verification']['part_count_match'])
        self.assertTrue(all(p['text_match'] for p in record['verification']['parts']))
        self.assertEqual(sum(p=='material/add_material' for p,_ in self.api.calls),1)
        self.assertEqual(sum(p=='media/uploadimg' for p,_ in self.api.calls),len(self.prepared['inline_ids']))
        for index,a in enumerate(self.api.articles,1):
            self.assertTrue(a['title'].endswith(f'（{index}/{len(self.api.articles)}）'))
            self.assertEqual(a['digest'],'');self.assertEqual(a['thumb_media_id'],'FIXTURE_COVER')
            self.assertLess(len(a['content']),20000)
        original=Signature(self.prepared['content']);sent=Signature(''.join(a['content'] for a in self.api.articles))
        self.assertEqual(''.join(original.text),''.join(sent.text))
        self.assertEqual(len(original.images),len(sent.images))
        with self.assertRaises(ValueError):self.send()
        self.assertEqual(self.adds(),1)

    def test_missing_extra_reordered_or_changed_later_part_cannot_verify(self):
        self.send()
        def corrupt_text(items):items[-1]['content']+='<p>Unexpected change</p>'
        def corrupt_title(items):items[-1]['title']='Wrong'
        def corrupt_image(items):items[-1]['content']+='<img src="https://mmbiz.qpic.cn/wrong.png">'
        mutations=[lambda a:a.pop(),lambda a:a.append(copy.deepcopy(a[-1])),lambda a:a.reverse(),corrupt_text,corrupt_title,corrupt_image]
        for mutate in mutations:
            self.api.mutate=mutate
            with self.assertRaises(ValueError):recheck(self.root/'paper',self.account)
            self.assertEqual(self.receipt()['state'],'submitted_unverified')
            self.assertEqual(self.adds(),1)
        self.api.mutate=None
        self.assertEqual(recheck(self.root/'paper',self.account)['state'],'submitted')
        self.assertEqual(self.adds(),1)

    def test_unknown_create_never_retries_or_loses_all_part_payloads(self):
        self.api.add_error=TransportError('Fixture timeout')
        with self.assertRaises(ValueError):self.send()
        self.assertEqual(self.receipt()['state'],'unknown')
        self.assertEqual(len(self.receipt()['payloads']),len(self.prepared['parts']))
        with self.assertRaises(ValueError):self.send()
        self.assertEqual(self.adds(),1)

    def test_actual_uploaded_url_overflow_stops_before_cover_and_create(self):
        self.api.long_url=True
        with self.assertRaisesRegex(ValueError,'图片地址'):self.send()
        self.assertEqual(self.adds(),0)
        self.assertFalse(any(p=='material/add_material' for p,_ in self.api.calls))
        self.assertEqual(self.receipt()['state'],'failed')

    def test_approval_binds_every_part(self):
        self.assertTrue(is_approved(self.root/'paper',self.prepared))
        changed=copy.deepcopy(self.prepared)
        changed['parts'][-1]['content']+='<p>Modified</p>'
        self.assertFalse(is_approved(self.root/'paper',changed))

    def test_automatic_pipeline_keeps_original_without_human_or_model_call(self):
        studio=Studio(self.root/'data');studio.desktop=True
        try:
            studio.wechat.request=self.api;studio.wechat.config=dict(self.account.config)
            job=studio.create('Fixture');folder=studio.folder(job)
            source=extract_pdf(fixture_pdf(),folder);article=fixture_article(source)
            article['lead']=self.article['lead'];studio.save_article(job,article)
            before=(folder/'article.json').read_bytes()
            with patch('server.generate_article',side_effect=AssertionError('Must not generate')):
                record=deliver_article(studio,job,studio.wechat)
            self.assertEqual(record['state'],'submitted')
            self.assertEqual((folder/'article.json').read_bytes(),before)
            self.assertFalse((folder/'wechat-review.json').exists())
            self.assertEqual(self.adds(),1)
            status=studio.wechat_status(job)
            self.assertGreater(len(status['parts']),1)
            self.assertFalse(status['blocker'])
            self.assertTrue(any('分为' in s for s in status['automation']['warnings']))
        finally:studio.close();studio.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
