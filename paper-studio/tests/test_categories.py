import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from categories import TOPICS, catalog, classify, topic_labels, validate_override, with_override
from server import Studio, make_server
from sources import write_json, extract_pdf, arxiv_metadata
from tests.test_sources import fixture_pdf


class ClassificationTests(unittest.TestCase):
    def test_all_directions_and_cross_field_labels(self):
        cases={'driving':'End-to-End Autonomous Driving', 'world':'A World Model for Planning',
               'embodied':'Robot Learning with Vision-Language-Action Models',
               'infra':'An LLM Serving Engine with KV Cache Compression',
               'foundation':'Scaling Large Language Model Pre-training',
               'agents':'Tool Use for Language Model Agents',
               'rsi':'Harness^2: Recursive Agent Harnessing for an Open World',
               'video':'Text-to-Video Generation with Video Diffusion',
               'reconstruction':'3D Reconstruction with Gaussian Splatting'}
        self.assertEqual(set(cases),set(TOPICS))
        for topic,title in cases.items():
            with self.subTest(topic=topic):self.assertIn(topic,classify({'title':title})['topics'])
        self.assertEqual(set(classify({'title':'World Models for Autonomous Driving'})['topics']),{'world','driving'})
        self.assertEqual(classify({'title':'A generic research study','arxiv_categories':['cs.AI','cs.CV']})['topics'],[])
        self.assertEqual(classify({'title':'A robotics study','arxiv_categories':['cs.RO']})['topics'],['embodied'])

    def test_metadata_abstract_and_explicit_pdf_abstract_only(self):
        record={'title':'Research','abstract':'A new method for video generation.',
                'pages':[{'text':'References\nWorld models for autonomous driving'}]}
        self.assertEqual(classify(record)['topics'],['video'])
        record.pop('abstract')
        self.assertEqual(classify(record)['topics'],[])
        record['pages']=[{'text':'Title\nAbstract\nOur method accelerates LLM serving.\n1 Introduction\nOther work uses video generation.\nReferences\n3D reconstruction'}]
        self.assertEqual(classify(record)['topics'],['infra'])
        self.assertEqual(classify({'title':'自动驾驶与世界模型'})['topics'],['driving','world'])

    def test_pdf_body_headings_and_unclosed_abstract_are_not_evidence(self):
        for heading in ('1 Overview','1. Task Setup','I. PRELIMINARIES','Method','Related Work','Introduction'):
            with self.subTest(heading=heading):
                source={'title':'A sampling method','pages':[{'text':'Abstract\nWe propose a fast sampling technique.\n'+heading+'\nRelated work includes video generation and autonomous driving.\n2 Method\nOur method follows.'}]}
                self.assertEqual(classify(source)['topics'],[])
        source={'title':'A sampling method','pages':[{'text':'Abstract\nMissing boundary: body discusses autonomous driving.'}]}
        self.assertEqual(classify(source)['topics'],[])

    def test_manual_override_strict_and_empty_is_preserved(self):
        auto=classify({'title':'World models'})
        self.assertEqual(with_override(auto,[])['topics'],[])
        self.assertEqual(with_override(auto,[])['category_mode'],'manual')
        self.assertEqual(with_override(auto,None),auto)
        self.assertEqual(validate_override(['rsi','world','world']),['world','rsi'])
        for value in ('world',False,0,{},[[]],['bad'],['world']*10):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_override(value)
        self.assertEqual(with_override(auto,['obsolete']),auto)

    def test_shared_discovery_taxonomy(self):
        from feed_sources import TOPICS as feed_topics, topic_labels as feed_labels
        self.assertIs(feed_topics,TOPICS)
        self.assertIs(feed_labels,topic_labels)
        self.assertEqual(len(catalog()),9)

    def test_import_preserves_official_abstract_and_subjects(self):
        raw=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2601.00001</id><title>A test</title><summary>Robot learning.</summary><category term="cs.RO"/></entry></feed>'
        with patch('sources.fetch_arxiv_bytes',return_value=raw):metadata=arxiv_metadata('2601.00001')
        with tempfile.TemporaryDirectory() as tmp:
            source=extract_pdf(fixture_pdf(),tmp,metadata=metadata)
            self.assertEqual(source['abstract'],'Robot learning.')
            self.assertEqual(source['arxiv_categories'],['cs.RO'])


class LibraryCategoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.studio=Studio(self.root/'data')
        self.job=self.studio.create('World Models for Autonomous Driving')
        self.folder=self.studio.folder(self.job)
        write_json(self.folder/'source.json',{'title':'World Models for Autonomous Driving','pages':[]})
        write_json(self.folder/'article.json',{'title':'Existing draft'})
        write_json(self.folder/'generation-progress.json',{'completed_units':[0,1]})
        self.studio.update(self.job,status='ready')

    def tearDown(self):
        self.studio.close();self.tmp.cleanup()

    def test_read_old_papers_is_non_mutating_and_updates_with_source(self):
        before=(self.folder/'status.json').read_bytes()
        self.assertEqual(self.studio.library_item(self.job)['topics'],['driving','world'])
        self.assertEqual((self.folder/'status.json').read_bytes(),before)
        with patch('server.read_json',side_effect=AssertionError('cached file must not reread')):
            self.assertEqual(self.studio.library_item(self.job)['topics'],['driving','world'])
        write_json(self.folder/'source.json',{'title':'Video generation with diffusion','pages':[]})
        self.assertEqual(self.studio.library_item(self.job)['topics'],['video'])

    def test_override_restart_restore_and_independence(self):
        paths=[self.folder/n for n in ('source.json','article.json','generation-progress.json')]
        before=[p.read_bytes() for p in paths]
        config=dict(self.studio.autofeed.config)
        self.studio.library_update(self.job,{'category_override':['rsi','agents']})
        self.studio.library_update(self.job,{'display_name':'Friendly alias','pinned':True,'trashed':True})
        reopened=Studio(self.root/'data')
        try:
            self.assertEqual(reopened.library_item(self.job)['topics'],['agents','rsi'])
            reopened.library_update(self.job,{'category_override':[],'trashed':False})
            self.assertEqual(reopened.library_item(self.job)['topics'],[])
            self.assertEqual(reopened.library_item(self.job)['category_mode'],'manual')
            self.assertEqual(reopened.library_update(self.job,{'category_override':None})['topics'],['driving','world'])
        finally:reopened.close()
        self.assertEqual([p.read_bytes() for p in paths],before)
        self.assertEqual(self.studio.autofeed.config,config)

    def test_invalid_or_failed_edit_does_not_mutate_saved_or_in_memory_state(self):
        before=(self.folder/'status.json').read_bytes();job=dict(self.studio.jobs[self.job])
        with self.assertRaises(ValueError):self.studio.library_update(self.job,{'category_override':['bad'],'pinned':True})
        with patch('server.write_json',side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.studio.library_update(self.job,{'category_override':['rsi']})
        self.assertEqual((self.folder/'status.json').read_bytes(),before)
        self.assertEqual(self.studio.jobs[self.job],job)
        self.studio.update(self.job,status='running')
        with self.assertRaises(ValueError):self.studio.library_update(self.job,{'category_override':[]})

    def test_real_http_catalog_list_update_and_auth(self):
        server=make_server(self.studio,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,data=None,token=True):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type':'application/json','X-Studio-Token':self.studio.token if token else ''})
            with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
        try:
            self.assertEqual(len(request('/api/categories')),9)
            self.assertEqual(request('/api/jobs')[0]['topics'],['driving','world'])
            self.assertEqual(request(f'/api/jobs/{self.job}/library',{'category_override':['infra']})['topics'],['infra'])
            with self.assertRaises(urllib.error.HTTPError) as error:request(f'/api/jobs/{self.job}/library',{'category_override':[]},False)
            self.assertEqual(error.exception.code,403)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
