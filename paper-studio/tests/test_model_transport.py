import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from diagnostics import save_failure
from model_transport import ModelProtocolError, SafeModelError, read_completion
from writing import chat_json, provider_config, generate_article
from tests.test_sources import fixture_pdf
from tests.test_writing import fixture_article
from sources import extract_pdf


def event(delta, finish=None):
    return ('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':finish}]},ensure_ascii=False)+'\n\n').encode()


class Response(io.BytesIO):
    def __init__(self, body, stream=True):
        super().__init__(body)
        self.headers={'Content-Type':'text/event-stream; charset=utf-8' if stream else 'application/json'}


def config(**options):
    return provider_config({'base_url':'http://127.0.0.1:9999/v1','model':'deepseek-v4-flash-0731',**options},use_environment=False)


class TransportTests(unittest.TestCase):
    def test_real_loopback_stream_chinese_and_reasoning(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                for body in [event({'reasoning_content':'PRIVATE_REASONING'}),event({'content':'{"text":"'}),event({'content':'研究配图"}'},'stop'),b'data: [DONE]\n\n']:
                    self.wfile.write(body);self.wfile.flush()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        fragments=[];notices=[]
        try:
            cfg=provider_config({'base_url':f'http://127.0.0.1:{server.server_port}','model':'deepseek-v4-flash-0731'},False)
            result=chat_json(cfg,'Synthetic JSON fixture',partial=lambda t,*_:fragments.append(t),event=notices.append)
        finally:server.shutdown();server.server_close();thread.join()
        self.assertEqual(result,{'text':'研究配图'})
        self.assertTrue(requests[0]['stream']);self.assertNotIn('thinking',requests[0])
        self.assertNotIn('PRIVATE_REASONING',json.dumps(fragments+notices))
        self.assertTrue(any('推理' in v for v in notices))

    def test_stream_eof_retries_without_duplicate_prefix(self):
        responses=[Response(event({'content':'{"text":"discard'})),
                   Response(event({'content':'{"text":"replacement"}'},'stop')+b'data: [DONE]\n\n')]
        partial=[]
        with patch('writing.urllib.request.build_opener') as op,patch('writing.time.sleep'):
            op.return_value.open.side_effect=responses
            result=chat_json(config(),'fixture',partial=lambda t,*_:partial.append(t))
        self.assertEqual(result,{'text':'replacement'})
        self.assertEqual(op.return_value.open.call_count,2)
        self.assertTrue(any('discard' in t for t in partial))
        self.assertEqual(partial[-1],'{"text":"replacement"}')

    def test_incomplete_read_and_connection_reset_are_bounded(self):
        for failure in [http.client.IncompleteRead(b'PRIVATE_BODY'),ConnectionResetError('PRIVATE_BODY'),
                        http.client.RemoteDisconnected('PRIVATE_BODY')]:
            with patch('writing.urllib.request.build_opener') as op,patch('writing.time.sleep'):
                op.return_value.open.side_effect=failure
                with self.assertRaises(ModelProtocolError) as caught:chat_json(config(),'fixture')
                self.assertEqual(op.return_value.open.call_count,3)
                self.assertIn(type(failure).__name__,str(caught.exception))
                self.assertNotIn('PRIVATE_BODY',str(caught.exception))

    def test_length_continuation_then_stream_retry_preserves_only_complete_prefix(self):
        responses=[Response(event({'content':'{"text":"A'},'length')),
                   Response(event({'content':'discard'})),
                   Response(event({'content':'B"}'},'stop'))]
        with patch('writing.urllib.request.build_opener') as op,patch('writing.time.sleep'):
            op.return_value.open.side_effect=responses
            result=chat_json(config(),'fixture')
            final=json.loads(op.return_value.open.call_args.args[0].data)
        self.assertEqual(result,{'text':'AB'})
        self.assertEqual(final['messages'][-2]['content'],'{"text":"A')

    def test_json_compatibility_and_explicit_controls(self):
        body=json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}).encode()
        with patch('writing.urllib.request.build_opener') as op:
            op.return_value.open.return_value=Response(body,False)
            result=chat_json(config(response_mode='json',request_timeout=900,json_mode='enabled'),'fixture',max_tokens=393216)
            payload=json.loads(op.return_value.open.call_args.args[0].data)
        self.assertTrue(result['ok']);self.assertFalse(payload['stream'])
        self.assertEqual(payload['max_tokens'],393216)
        self.assertEqual(payload['response_format'],{'type':'json_object'})
        self.assertEqual(op.return_value.open.call_args.kwargs['timeout'],900)

    def test_malformed_envelopes_are_safe_protocol_errors(self):
        for body in [[],{'choices':None},{'choices':[None]},{'choices':[{'message':'PRIVATE_BODY'}]}, {'error':{'message':'PRIVATE_BODY'}}]:
            with patch('writing.urllib.request.build_opener') as op:
                op.return_value.open.return_value=Response(json.dumps(body).encode(),False)
                with self.assertRaises(ModelProtocolError) as caught:chat_json(config(),'fixture')
            self.assertNotIn('PRIVATE_BODY',str(caught.exception))

    def test_text_only_images_blocked_before_network(self):
        with patch('writing.urllib.request.build_opener') as op:
            with self.assertRaisesRegex(ValueError,'仅支持文本'):chat_json(config(),'fixture',images=[('test',b'not sent')])
        op.assert_not_called()

    def test_stage_failure_diagnostics_and_completed_evidence_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);manifest=extract_pdf(fixture_pdf(),folder);article=fixture_article(manifest)
            options={'base_url':'http://127.0.0.1:9999','model':'deepseek-v4-flash-0731'}
            with patch('writing.chat_json',side_effect=[{'facts':[]},AttributeError('PRIVATE_BODY')]):
                with self.assertRaisesRegex(ValueError,'完整写稿.*AttributeError'):
                    generate_article(manifest,folder,options)
            record=json.loads((folder/'generation-error.json').read_text(encoding='utf8'))
            self.assertEqual(record['stage'],'完整写稿');self.assertNotIn('PRIVATE_BODY',json.dumps(record))
            self.assertFalse((folder/'article.json').exists())
            with patch('writing.chat_json',side_effect=[article,article]) as chat:
                result,notes,_=generate_article(manifest,folder,{**options,'request_timeout':900})
            self.assertEqual(chat.call_count,2);self.assertEqual(len(notes),1)
            self.assertFalse(result['generation']['vision'])

    def test_diagnostics_never_include_exception_values_or_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            try:raise RuntimeError('SYNTHETIC_PRIVATE_KEY_AND_RESPONSE')
            except Exception as exc:detail=save_failure(temp,'job-error.json',exc,'evidence')
            text=json.dumps(detail)
            self.assertNotIn('SYNTHETIC_PRIVATE',text);self.assertNotIn(str(Path(__file__).parent),text)
            self.assertEqual(detail['error_type'],'RuntimeError')

    def test_stream_cancel_does_not_retry(self):
        calls=0
        def cancel():
            nonlocal calls
            calls+=1;return calls>1
        with patch('writing.urllib.request.build_opener') as op:
            op.return_value.open.return_value=Response(event({'content':'{}'},'stop'))
            with self.assertRaisesRegex(ValueError,'取消'):chat_json(config(),'fixture',cancelled=cancel)
        self.assertEqual(op.return_value.open.call_count,1)

    def test_invalid_header_and_arbitrary_value_errors_never_echo_credentials(self):
        secret='SYNTHETIC_SECRET\nSECOND_LINE'
        with self.assertRaises(SafeModelError) as caught:config(api_key=secret)
        self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception))
        with patch('writing.urllib.request.build_opener') as op:
            op.return_value.open.side_effect=ValueError('Invalid header value: Bearer SYNTHETIC_SECRET')
            with self.assertRaises(ModelProtocolError) as caught:chat_json(config(),'fixture')
        self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception))
        self.assertEqual(op.return_value.open.call_count,1)

    def test_real_httpresponse_short_content_length_retries(self):
        class Socket:
            def makefile(self,*args):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{"choices":[')
        truncated=http.client.HTTPResponse(Socket());truncated.begin()
        complete=json.dumps({'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}).encode()
        with patch('writing.urllib.request.build_opener') as op,patch('writing.time.sleep'):
            op.return_value.open.side_effect=[truncated,Response(complete,False)]
            result=chat_json(config(response_mode='json'),'fixture')
        self.assertTrue(result['ok']);self.assertEqual(op.return_value.open.call_count,2)

    def test_stream_metadata_overhead_does_not_consume_content_limit(self):
        chunk=event({'content':'x'})
        chunk=chunk.replace(b'{"choices"',b'{"id":"'+b'a'*180+b'","choices"')
        payload=chunk*40000+event({},'stop')+b'data: [DONE]\n\n'
        self.assertGreater(len(payload),8_000_000)
        result=read_completion(Response(payload),lambda _:None,lambda:False,lambda _:None)
        self.assertEqual(result['choices'][0]['message']['content'],'x'*40000)

    def test_json_requires_terminal_marker_and_malformed_complete_body_not_retried(self):
        for body in [b'{broken',json.dumps({'choices':[{'message':{'content':'{}'},'finish_reason':None}]}).encode()]:
            with patch('writing.urllib.request.build_opener') as op:
                op.return_value.open.return_value=Response(body,False)
                with self.assertRaises(ModelProtocolError):chat_json(config(),'fixture')
                self.assertEqual(op.return_value.open.call_count,1)

    def test_untrusted_value_error_is_not_saved_in_stage_message(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);manifest=extract_pdf(fixture_pdf(),folder)
            with patch('writing.chat_json',side_effect=ValueError('SYNTHETIC_SECRET')):
                with self.assertRaises(SafeModelError) as caught:
                    generate_article(manifest,folder,{'base_url':'http://127.0.0.1:9999','model':'test'})
            self.assertNotIn('SYNTHETIC_SECRET',str(caught.exception))
            self.assertNotIn('SYNTHETIC_SECRET',(folder/'generation-error.json').read_text())

    def test_generation_job_status_does_not_echo_arbitrary_value_error(self):
        from server import Studio
        with tempfile.TemporaryDirectory() as temp:
            studio=Studio(temp);job=studio.create('synthetic')
            def work(progress):raise ValueError('SYNTHETIC_SECRET')
            studio.submit(job,'generate',work);studio.pool.shutdown(wait=True)
            self.assertEqual(studio.jobs[job]['status'],'error')
            for path in studio.folder(job).glob('*.json'):
                self.assertNotIn('SYNTHETIC_SECRET',path.read_text(encoding='utf-8'))

    def test_invalid_stream_finish_is_not_normalized_to_success(self):
        for marker in ['',False,0,[],{},'unknown']:
            with patch('writing.urllib.request.build_opener') as op:
                op.return_value.open.return_value=Response(event({'content':'{"partial":true}'},marker))
                with self.assertRaises(ModelProtocolError) as caught:chat_json(config(),'fixture')
                self.assertEqual(caught.exception.code,'stream_finish')
                self.assertEqual(op.return_value.open.call_count,1)


if __name__=='__main__':unittest.main()
