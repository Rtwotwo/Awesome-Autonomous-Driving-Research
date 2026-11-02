import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from model_options import endpoint, request_budget
from model_transport import network_failure
from server import Studio
from writing import provider_config, chat_json


class GatewayTests(unittest.TestCase):
    def cfg(self, **values):
        return provider_config({'base_url':'https://gateway.example/v1/chat/completions?openai.azure=1',
                                'model':'example-text-model','api_key':'SYNTHETIC_KEY',
                                'auth_mode':'x-api-key','proxy_mode':'direct',**values}, False)

    def test_endpoint_query_preserved_and_secrets_rejected(self):
        url='https://gateway.example/v1/chat/completions?openai.azure=1'
        self.assertEqual(endpoint(f'[{url}]({url})'),url)
        self.assertEqual(endpoint('https://gateway.example/v1?api-version=2026-01-01'),
                         'https://gateway.example/v1/chat/completions?api-version=2026-01-01')
        for value in ['https://gateway.example/v1?api_key=SYNTHETIC_KEY','https://name:password@gateway.example/v1']:
            with self.assertRaises(ValueError) as caught:endpoint(value)
            self.assertNotIn('SYNTHETIC_KEY',str(caught.exception))

    def test_exact_auth_header_and_explicit_direct_mode(self):
        response=io.BytesIO(json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}).encode())
        response.headers={'Content-Type':'application/json'}
        with patch('writing.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            self.assertTrue(chat_json(self.cfg(),'test',max_tokens=4096)['ok'])
            request=build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,self.cfg()['url'])
            headers={k.lower():v for k,v in request.header_items()}
            self.assertEqual(headers['x-api-key'],'SYNTHETIC_KEY')
            self.assertNotIn('authorization',headers)
            self.assertTrue(any(getattr(h,'proxies',None)=={} for h in build.call_args.args))

    def test_capabilities_roundtrip_and_no_key_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio=Studio(Path(tmp)/'data',Path(tmp)/'settings/models.dpapi')
            profile={'base_url':'https://gateway.example/v1?openai.azure=1','model':'example-text-model',
                     'api_key':'SYNTHETIC_KEY','auth_mode':'x-api-key','proxy_mode':'direct',
                     'context_window':1000000,'max_output_tokens':131072,'vision_capability':False,
                     'tool_calling':True,'remember':True,'profile_id':''}
            try:
                studio.configure_models(profile)
                studio.configure_models({**profile,'profile_id':studio.active_profile,'api_key':''})
                self.assertNotIn('SYNTHETIC_KEY',json.dumps(studio.model_session()))
                reopened=Studio(Path(tmp)/'data',Path(tmp)/'settings/models.dpapi')
                try:
                    self.assertEqual(reopened.provider['auth_mode'],'x-api-key')
                    self.assertEqual(reopened.provider['context_window'],1000000)
                    self.assertFalse(reopened.provider['vision_capability'])
                finally:reopened.close()
            finally:studio.close()

    def test_text_only_profile_never_sends_images(self):
        with patch('writing.urllib.request.build_opener') as build:
            with self.assertRaisesRegex(ValueError,'仅支持文本'):
                chat_json(self.cfg(vision_capability=False),'test',[('figure',b'image')])
            build.assert_not_called()
        with self.assertRaises(ValueError):self.cfg(output_tokens=200000,max_output_tokens=131072)
        with self.assertRaises(ValueError):self.cfg(api_key='${input:key}')

    def test_each_request_respects_declared_capacity_and_prompt_reserve(self):
        response=io.BytesIO(json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}).encode())
        response.headers={'Content-Type':'application/json'}
        with patch('writing.urllib.request.build_opener') as build:
            build.return_value.open.return_value=response
            self.assertTrue(chat_json(self.cfg(max_output_tokens=1024),'test',max_tokens=4096)['ok'])
            payload=json.loads(build.return_value.open.call_args.args[0].data)
            self.assertEqual(payload['max_tokens'],1024)
        config=self.cfg(context_window=8192,max_output_tokens=8192)
        messages=[{'role':'user','content':'正文'*300}]
        first=request_budget(config,messages,8192)
        self.assertLess(first,8192-len(messages[0]['content'].encode('utf-8')))
        messages.append({'role':'assistant','content':'more'*250})
        self.assertLess(request_budget(config,messages,8192),first)
        with patch('writing.urllib.request.build_opener') as build:
            with self.assertRaisesRegex(ValueError,'上下文'):
                chat_json(config,'正文'*3000,max_tokens=8192)
            build.return_value.open.assert_not_called()

    def test_safe_dns_tls_and_timeout_diagnostics(self):
        for failure,code in [(socket.gaierror('PRIVATE'),'dns_resolution'),
                             (ssl.SSLEOFError('PRIVATE'),'tls_connection'),
                             (TimeoutError('PRIVATE'),'network_timeout')]:
            actual,hint,retry=network_failure(urllib.error.URLError(failure))
            self.assertEqual(actual,code);self.assertNotIn('PRIVATE',hint);self.assertTrue(retry)
        code,hint,retry=network_failure(ssl.SSLCertVerificationError('PRIVATE'))
        self.assertEqual(code,'tls_certificate');self.assertFalse(retry)

    def test_encrypted_replace_retry_does_not_repeat_encryption(self):
        from model_store import ModelStore
        import os
        real_replace=os.replace
        with tempfile.TemporaryDirectory() as tmp:
            with patch('model_store.os.replace',side_effect=[PermissionError(),None]) as replace,patch('model_store.time.sleep'):
                ModelStore(Path(tmp)/'models.dpapi').save({'version':1})
                self.assertEqual(replace.call_count,2)

if __name__=='__main__':unittest.main()
