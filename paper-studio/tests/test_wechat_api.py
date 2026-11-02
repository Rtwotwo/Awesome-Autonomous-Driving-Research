import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from sources import extract_pdf
from wechat_review import prepare,approve,CHECKS
from wechat_api import Account,WechatError,TransportError,submit_draft,recheck,reset_submission,transport,verify_image_url,whitelist_ip,verify_draft

class FakeAPI:
    def __init__(self):self.calls=[];self.article=None;self.add_error=None;self.bad_read=False
    def __call__(self,path,**kw):
        self.calls.append((path,kw))
        if path=='stable_token':return {'access_token':'FIXTURE_TOKEN','expires_in':7200}
        if path=='draft/count':return {'total_count':0}
        if path=='media/uploadimg':return {'url':'https://mmbiz.qpic.cn/fixture/image.png'}
        if path=='material/add_material':return {'media_id':'FIXTURE_COVER'}
        if path=='draft/add':
            if self.add_error:raise self.add_error
            self.article=copy.deepcopy(kw['payload']['articles'][0]);return {'media_id':'FIXTURE_DRAFT'}
        if path=='draft/get':
            a=copy.deepcopy(self.article)
            if self.bad_read:a['content']='missing text'
            return {'news_item':[a]}
        raise AssertionError(path)

class APITests(unittest.TestCase):
    def test_cancel_after_token_does_not_start_image_upload(self):
        stopped=False
        def request(path,**kw):
            nonlocal stopped
            result=self.api(path,**kw)
            if path=='stable_token':stopped=True
            return result
        self.account.request=request
        def progress(_):
            if stopped:raise ValueError('Fixture cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):submit_draft(self.root/'paper',self.p,self.account,progress)
        self.assertEqual([p for p,_ in self.api.calls],['stable_token'])
        self.assertEqual(json.loads((self.root/'paper/wechat-submission.json').read_text())['state'],'failed')

    def test_cancel_after_cover_is_retryable_without_unknown_receipt(self):
        stopped=False
        def request(path,**kw):
            nonlocal stopped
            result=self.api(path,**kw)
            if path=='material/add_material':stopped=True
            return result
        self.account.request=request
        def progress(_):
            if stopped:raise ValueError('Fixture cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):submit_draft(self.root/'paper',self.p,self.account,progress)
        self.assertNotIn('draft/add',[p for p,_ in self.api.calls])
        self.assertEqual(json.loads((self.root/'paper/wechat-submission.json').read_text())['state'],'failed')
        self.assertEqual(submit_draft(self.root/'paper',self.p,self.account)['state'],'submitted')

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.api=FakeAPI()
        self.account=Account(self.root/'wechat.dpapi',self.api)
        self.values={'appid':'wx'+'1'*16,'secret':'FIXTURE_SECRET_NOT_REAL_123','name':'测试公众号','remember':False}
        self.account.save(self.values)
        s=extract_pdf(fixture_pdf(),self.root/'paper');a=fixture_article(s)
        self.p=prepare(self.root/'paper',a,s,{'title':'测试文章','digest':'摘要','cover_id':s['figures'][0]['id']},self.account.public())
        approve(self.root/'paper',self.p,{k:True for k in CHECKS})
    def tearDown(self):self.account.close();self.tmp.cleanup()
    def test_encrypted_config_no_browser_secret_and_new_account_needs_secret(self):
        self.account.save({**self.values,'remember':True})
        self.assertNotIn(self.values['secret'].encode(),(self.root/'wechat.dpapi').read_bytes())
        restored=Account(self.root/'wechat.dpapi',self.api);self.assertEqual(restored.config['secret'],self.values['secret'])
        self.assertNotIn('secret',restored.public());self.assertNotIn('FIXTURE_SECRET',json.dumps(restored.public()))
        with self.assertRaises(ValueError):restored.save({'appid':'wx'+'2'*16,'secret':''})
        restored.save({'clear':True});self.assertFalse((self.root/'wechat.dpapi').exists())
    def test_probe_only_reads_then_uploads_images_cover_draft_and_verifies(self):
        self.account.check();self.assertEqual([x[0] for x in self.api.calls],['stable_token','draft/count'])
        record=submit_draft(self.root/'paper',self.p,self.account)
        self.assertEqual(record['state'],'submitted')
        self.assertNotIn('src="images/',self.api.article['content']);self.assertIn('https://mmbiz.qpic.cn/',self.api.article['content'])
        with self.assertRaises(ValueError):submit_draft(self.root/'paper',self.p,self.account)
        self.assertEqual(sum(x[0]=='draft/add' for x in self.api.calls),1)
        for file in self.root.rglob('*.json'):self.assertNotIn(self.values['secret'],file.read_text(encoding='utf8'))
    def test_timeout_does_not_resend_until_human_resolves(self):
        self.api.add_error=TransportError('测试超时')
        with self.assertRaises(ValueError):submit_draft(self.root/'paper',self.p,self.account)
        path=self.root/'paper/wechat-submission.json';self.assertEqual(json.loads(path.read_text())['state'],'unknown')
        with self.assertRaises(ValueError):submit_draft(self.root/'paper',self.p,self.account)
        with self.assertRaises(ValueError):reset_submission(self.root/'paper',False)
        reset_submission(self.root/'paper',True);self.api.add_error=None
        self.assertEqual(submit_draft(self.root/'paper',self.p,self.account)['state'],'submitted')
    def test_known_rejection_and_readback_mismatch_are_distinct(self):
        self.api.add_error=WechatError(48001)
        with self.assertRaisesRegex(ValueError,'48001'):submit_draft(self.root/'paper',self.p,self.account)
        self.assertEqual(json.loads((self.root/'paper/wechat-submission.json').read_text())['state'],'failed')
        self.api.add_error=None;self.api.bad_read=True
        with self.assertRaises(ValueError):submit_draft(self.root/'paper',self.p,self.account)
        self.assertEqual(json.loads((self.root/'paper/wechat-submission.json').read_text())['state'],'submitted_unverified')
        self.api.bad_read=False;self.assertEqual(recheck(self.root/'paper',self.account)['state'],'submitted')
    def test_endpoint_restrictions_and_errors_redact_sensitive_details(self):
        with self.assertRaises(ValueError):transport('https://evil.example')
        with self.assertRaises(ValueError):verify_image_url('https://evil.example/image.png')
        with patch('urllib.request.OpenerDirector.open',side_effect=RuntimeError('SECRET_IN_EXCEPTION')):
            with self.assertRaises(ValueError) as e:transport('stable_token',payload={'secret':'SECRET_IN_EXCEPTION'})
        self.assertNotIn('SECRET_IN_EXCEPTION',str(e.exception))
    def test_readback_rejects_changed_image_or_digest_without_creating_again(self):
        record=submit_draft(self.root/'paper',self.p,self.account)
        original=copy.deepcopy(self.api.article)
        self.api.article['content']=self.api.article['content'].replace('fixture/image.png','fixture/wrong.png')
        with self.assertRaises(ValueError):recheck(self.root/'paper',self.account)
        self.assertEqual(json.loads((self.root/'paper/wechat-submission.json').read_text())['state'],'submitted_unverified')
        self.api.article=copy.deepcopy(original);self.api.article['digest']='different digest'
        with self.assertRaises(ValueError):recheck(self.root/'paper',self.account)
        self.assertEqual(sum(x[0]=='draft/add' for x in self.api.calls),1)

    def test_transient_readback_retries_only_existing_draft(self):
        reads=0
        def request(path,**kwargs):
            nonlocal reads
            result=self.api(path,**kwargs)
            if path=='draft/get':
                reads+=1
                if reads<3:raise TransportError('FIXTURE_READBACK_DISCONNECTED')
            return result
        self.account.request=request
        with patch('wechat_api.READBACK_RETRY_DELAYS',(0,0)):
            record=submit_draft(self.root/'paper',self.p,self.account)
        self.assertEqual(record['state'],'submitted')
        self.assertEqual(record['verification']['attempts'],3)
        self.assertEqual(sum(path=='draft/add' for path,_ in self.api.calls),1)
        self.assertEqual(sum(path=='draft/get' for path,_ in self.api.calls),3)

    def test_non_transient_readback_error_keeps_id_and_safe_error_code(self):
        record=submit_draft(self.root/'paper',self.p,self.account)
        def request(path,**kwargs):
            result=self.api(path,**kwargs)
            if path=='draft/get':raise WechatError(40007,'SECRET_ERROR_TEXT')
            return result
        self.account.request=request
        before=len(self.api.calls)
        record=recheck(self.root/'paper',self.account,allow_unverified=True)
        self.assertEqual(record['state'],'submitted_unverified')
        self.assertEqual(record['verification']['error_code'],40007)
        self.assertEqual(record['verification']['attempts'],1)
        self.assertEqual([path for path,_ in self.api.calls[before:]],['draft/get'])
        self.assertIn('40007',record['message'])
        self.assertNotIn('SECRET_ERROR_TEXT',json.dumps(record))
        self.assertEqual(sum(path=='draft/add' for path,_ in self.api.calls),1)

    def test_readback_normalizes_html_entities_placeholder_images_and_fragments(self):
        content='<p>Fixture &amp; proof.</p><img src="https://mmbiz.qpic.cn/fixture/0?from=fixture&amp;wx_fmt=png">'
        expected={'title':'Title','author':'Editor','digest':'Digest','content_source_url':'https://example.test/paper',
                  'thumb_media_id':'FIXTURE_COVER','content':content}
        variants=[content.replace('&amp; proof','&#38; proof'),content.replace('https:','http:'),
                  content.replace('src=','data-src='),
                  content.replace('src=','src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" data-src='),
                  content.replace('src=','src="about:blank" data-src='),
                  content.replace('wx_fmt=png','wx_fmt=png#display')]
        for actual in variants:
            with self.subTest(actual=variants.index(actual)):
                self.api.article={**expected,'content':actual}
                record={'media_id':'FIXTURE_DRAFT','payload':expected}
                verify_draft(self.account,record)
                self.assertEqual(record['state'],'submitted')
                checks=record['verification']
                self.assertTrue(all(checks['field_matches'].values()))
                self.assertTrue(checks['text_match'])
                self.assertTrue(checks['image_count_match'])
                self.assertTrue(checks['image_addresses_match'])
                self.assertEqual(checks['expected_image_count'],1)
                self.assertEqual(checks['actual_image_count'],1)
        self.assertFalse(any(path=='draft/add' for path,_ in self.api.calls))

    def test_readback_diagnostics_keep_meaningful_differences_and_no_contents(self):
        content='<p>SECRET_BODY_MARKER</p><img src="https://mmbiz.qpic.cn/fixture/0?from=fixture&amp;wx_fmt=png">'
        expected={'title':'SECRET_TITLE','author':'SECRET_AUTHOR','digest':'SECRET_DIGEST',
                  'content_source_url':'https://example.test/SECRET_LINK',
                  'thumb_media_id':'SECRET_COVER','content':content}
        changes={**{key:{key:'changed'} for key in expected if key!='content'},
            'text':{'content':content.replace('SECRET_BODY_MARKER','changed')},
            'missing_image':{'content':'<p>SECRET_BODY_MARKER</p>'},
            'image':{'content':content.replace('/fixture/','/different/')},
            'host':{'content':content.replace('mmbiz.qpic.cn','evil.example')},
            'query':{'content':content.replace('?from=fixture&amp;wx_fmt=png','')},
            'visible_src':{'content':content.replace('src=','src="https://mmbiz.qpic.cn/different/0" data-src=')},
            'unproven_data_placeholder':{'content':content.replace('src=','src="data:image/png;base64,UNKNOWN" data-src=')}}
        for name,change in changes.items():
            with self.subTest(change=name):
                self.api.article={**expected,**change}
                record={'media_id':'SECRET_DRAFT_ID','payload':expected}
                with self.assertRaises(ValueError):verify_draft(self.account,record)
                checks=record['verification']
                self.assertEqual(checks['status'],'mismatch')
                if name in expected:self.assertFalse(checks['field_matches'][name])
                elif name=='text':self.assertFalse(checks['text_match'])
                else:self.assertFalse(checks['image_addresses_match'])
                if name=='missing_image':
                    self.assertFalse(checks['image_count_match'])
                    self.assertEqual(checks['actual_image_count'],0)
                diagnostic=json.dumps(checks)
                for private in ('SECRET_','https:','qpic.cn','example.test','SECRET_DRAFT_ID'):
                    self.assertNotIn(private,diagnostic)

    def test_whitelist_error_keeps_only_official_validated_ip(self):
        import io
        raw={'errcode':40164,'errmsg':'invalid ip 8.8.4.4 ipv6 ::ffff:8.8.4.4, not in whitelist rid:SECRET_MUST_NOT_ESCAPE'}
        with patch('urllib.request.OpenerDirector.open',return_value=io.BytesIO(json.dumps(raw).encode())):
            with self.assertRaises(WechatError) as caught:transport('stable_token',payload={'secret':'FIXTURE'})
        self.assertEqual(caught.exception.observed_ip,'8.8.4.4')
        self.assertIn('8.8.4.4',str(caught.exception))
        self.assertNotIn('SECRET_MUST_NOT_ESCAPE',str(caught.exception))
        self.assertEqual(whitelist_ip('invalid ip ::ffff:8.8.4.4, not in whitelist'),'8.8.4.4')
        self.assertEqual(whitelist_ip('invalid ip 2606:4700:4700::1111, not in whitelist'),'2606:4700:4700::1111')
        for invalid in ['invalid ip 127.0.0.1,','invalid ip 999.1.1.1,','invalid ip https://evil.example','secret=8.8.4.4','invalid ip 8.8.4.4.evil']:
            self.assertEqual(whitelist_ip(invalid),'')
        self.assertNotIn('8.8.4.4',str(WechatError(40001,raw['errmsg'])))

    def test_connection_error_diagnostic_persists_in_memory_without_secret(self):
        error=WechatError(40164,'invalid ip 8.8.4.4, not in whitelist SECRET_MUST_NOT_ESCAPE')
        with patch.object(self.account,'call',side_effect=error):
            with self.assertRaises(WechatError):self.account.check()
        check=self.account.public()['check']
        self.assertEqual(check['code'],40164)
        self.assertEqual(check['observed_ip'],'8.8.4.4')
        self.assertFalse(check['draft_read'])
        self.assertNotIn('SECRET_MUST_NOT_ESCAPE',json.dumps(check))
        self.account.check()
        self.assertTrue(self.account.public()['check']['draft_read'])

if __name__=='__main__':unittest.main()
