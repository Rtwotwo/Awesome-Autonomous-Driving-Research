import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from covers import ensure_overview_cover


class OverviewCoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.folder = Path(self.temp.name)
        (self.folder/'images').mkdir()
        self.source = {'title':'Synthetic Research', 'figures':[], 'pages':[]}
        self.article = {'title':'一种可验证的方法'}

    def tearDown(self): self.temp.cleanup()

    def figure(self, number, caption, size=(900, 400), **extra):
        name = f'images/fig-{number:03}.png'
        image = Image.new('RGB', size, 'white'); draw = ImageDraw.Draw(image)
        draw.rectangle((2,2,size[0]-3,size[1]-3), outline='red', width=5)
        draw.rectangle((20,20,size[0]-21,size[1]-21), outline='blue', width=5)
        image.save(self.folder/name)
        value = {'id':f'fig-{number:03}', 'image':name, 'caption':caption, 'kind':'figure', **extra}
        self.source['figures'].append(value); return value

    def test_overview_preferred_and_full_figure_preserved(self):
        self.figure(1, 'Figure 1. Results comparison')
        chosen = self.figure(2, 'Figure 2. Overall architecture overview', size=(500,900))
        original = (self.folder/chosen['image']).read_bytes()
        cover = ensure_overview_cover(self.folder,self.source,self.article)
        self.assertEqual(cover['source_figure_id'], 'fig-002')
        self.assertEqual(cover['editor'], 'Paper Studio')
        self.assertEqual(cover['selection_reason'], 'overview')
        with Image.open(self.folder/cover['image']) as im:
            self.assertEqual(im.size,(1200,510))
            # All four colored frame edges survive a portrait-to-landscape fit.
            red=[(x,y) for y in range(116,461) for x in range(48,1152) if im.getpixel((x,y))[0]>180 and im.getpixel((x,y))[1]<90 and im.getpixel((x,y))[2]<90]
            self.assertTrue(red)
            self.assertGreater(max(y for x,y in red)-min(y for x,y in red),250)
            self.assertLess(max(x for x,y in red)-min(x for x,y in red),200)
        self.assertEqual((self.folder/chosen['image']).read_bytes(),original)
        self.assertEqual(ensure_overview_cover(self.folder,self.source,self.article)['id'],cover['id'])
        self.assertEqual(len(self.source['covers']),1)

    def test_unreviewed_embedded_and_full_page_are_not_used(self):
        self.figure(1,'Overview',origin='pdf_embedded',reviewed=False)
        self.figure(2,'Overview',crop_status='page_fallback')
        result=ensure_overview_cover(self.folder,self.source,self.article)
        self.assertEqual(result['selection_reason'],'editorial_fallback')
        self.assertNotIn('source_figure_id',result)
        self.assertFalse(self.source['figures'][0]['reviewed'])

    def test_non_overview_fallback_is_explicit_and_editor_change_refreshes(self):
        self.figure(1,'Qualitative results')
        first=ensure_overview_cover(self.folder,self.source,self.article)
        second=ensure_overview_cover(self.folder,self.source,self.article,editor='Custom editor')
        self.assertEqual(first['selection_reason'],'figure_fallback')
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(second['editor'],'Custom editor')


if __name__=='__main__': unittest.main()
