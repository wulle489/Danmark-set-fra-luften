import importlib.util
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'kb_luftfoto_refresh' / 'photo_links.py'
spec = importlib.util.spec_from_file_location('photo_links', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
preferred_photo_url = module.preferred_photo_url
is_web_link = module.is_web_link


class PhotoLinkTests(unittest.TestCase):
    def test_real_kb_src_and_thumbnail(self):
        attributes = {
            'name': '- 1958 - Lundby Hovedgade 153',
            'thumbnail': 'http://kb-images.kb.dk/DAMJP2/DAM/LuftfotoOM/0000/785/447/B03252_013/full/,100/0/native.jpg',
            'src': 'http://kb-images.kb.dk/DAMJP2/DAM/LuftfotoOM/0000/785/447/B03252_013/full/full/0/native.jpg',
        }
        self.assertEqual(preferred_photo_url(attributes), attributes['src'])

    def test_works_with_preexisting_v21_layer_without_alias(self):
        self.assertEqual(preferred_photo_url({'src': 'http://kb-images.kb.dk/a.jpg'}),
                         'http://kb-images.kb.dk/a.jpg')

    def test_original_image_wins_over_page_link(self):
        self.assertEqual(preferred_photo_url({
            'subjectLink': 'https://www.kb.dk/item',
            'subjectImageSrc': 'https://www.kb.dk/image.jpg',
        }), 'https://www.kb.dk/image.jpg')

    def test_page_link_fallback(self):
        self.assertEqual(preferred_photo_url({'src': None, 'subjectLink': 'https://www.kb.dk/item'}),
                         'https://www.kb.dk/item')

    def test_thumbnail_last_fallback(self):
        self.assertEqual(preferred_photo_url({'src': '', 'thumbnail': 'https://www.kb.dk/small.jpg'}),
                         'https://www.kb.dk/small.jpg')

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(preferred_photo_url({'SRC': ' https://www.kb.dk/A.jpg  '}),
                         'https://www.kb.dk/A.jpg')

    def test_invalid_fields_are_skipped(self):
        self.assertEqual(preferred_photo_url({'src': 'javascript:alert(1)',
                                              'subjectImageSrc': 'file:///etc/passwd',
                                              'url': 'https://www.kb.dk/real'}),
                         'https://www.kb.dk/real')
        self.assertFalse(is_web_link('https://www.kb.dk/space in url'))
        self.assertFalse(is_web_link('https://fake.example@www.kb.dk/x'))
        self.assertEqual(preferred_photo_url({'name': 'no links'}), '')

    def test_malformed_url_ignored(self):
        self.assertEqual(preferred_photo_url({'src': 'http://[not_ipv6/index'}), '')


if __name__ == '__main__':
    unittest.main()
