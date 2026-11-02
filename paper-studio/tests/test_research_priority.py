from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import Mock
from feed_sources import discover_recent, parse_feed, topic_labels, QUERIES
from research_priority import priority, ranked
from sources import parse_arxiv
from tests.test_autofeed_sources import FEED


class ResearchDiscoveryTests(unittest.TestCase):
    def test_new_topics_and_alpha_link_identity(self):
        for title,topic in [('Text-to-Video Diffusion','video'),('Generalizable 3D Reconstruction','reconstruction'),
                            ('Gaussian Splatting for Novel View Synthesis','reconstruction'),
                            ('Efficient Inference with Attention Kernels','infra')]:
            self.assertIn(topic,topic_labels(title,''))
        self.assertEqual(parse_arxiv('https://www.alphaxiv.org/abs/2610.12345v2'),'2610.12345v2')
        with self.assertRaises(ValueError):parse_arxiv('https://alphaxiv.org.evil.test/abs/2610.12345')

    def test_institution_mentions_are_not_author_affiliations(self):
        item={'title':'Robot World Model','topics':['world'],'abstract':'We compare with NVIDIA and Stanford University models.'}
        self.assertFalse(priority(item)['preferred_institutions'])
        item['affiliations']=['Stanford University']
        self.assertEqual(priority(item)['preferred_institutions'],['Stanford University'])
        item['affiliations']=['NotStanford UniversityFake']
        self.assertFalse(priority(item)['preferred_institutions'])

    def test_alpha_discovery_uses_arxiv_metadata_and_deduplicates(self):
        public=Mock(return_value=(b'<a href="/abs/2610.12345">Paper</a><a href="/abs/2610.12345v2">v2</a>', 'text/html','https://www.alphaxiv.org/'))
        fetch=Mock(return_value=FEED)
        with tempfile.TemporaryDirectory() as tmp:
            result=discover_recent(tmp,datetime(2026,10,19,tzinfo=timezone.utc),fetch,lambda _:None,sources=['arxiv','alphaxiv'],public_fetch=public)
        self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['metadata_source'],'arxiv')
        self.assertIn('alphaxiv',result['items'][0]['discovered_via'])
        self.assertEqual(fetch.call_count,len(QUERIES)+1)
        self.assertTrue(all('/pdf/' not in c.args[0] for c in fetch.call_args_list))

    def test_alpha_outage_keeps_official_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=discover_recent(tmp,datetime(2026,10,19,tzinfo=timezone.utc),Mock(return_value=FEED),lambda _:None,
                                   sources=['arxiv','alphaxiv'],public_fetch=Mock(side_effect=ValueError('offline')))
        self.assertEqual(len(result['items']),1);self.assertTrue(result['errors'])

    def test_explicit_atom_affiliation_and_priority(self):
        feed=FEED.replace(b'<name>A</name>',b'<name>A</name><arxiv:affiliation xmlns:arxiv="http://arxiv.org/schemas/atom">Stanford University</arxiv:affiliation>')
        record=parse_feed(feed,datetime(2026,10,19,tzinfo=timezone.utc))[0]
        self.assertEqual(record['affiliations'],['Stanford University'])
        unknown={**record,'id':'other','affiliations':[]};unknown['priority']=priority(unknown)
        self.assertEqual(ranked([unknown,record])[0]['id'],record['id'])

if __name__=='__main__':unittest.main()
