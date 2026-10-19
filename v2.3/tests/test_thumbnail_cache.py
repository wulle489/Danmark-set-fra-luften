import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

# Load the pure-Python module without importing the QGIS plugin entry point.
import importlib.util
from pathlib import Path
source = Path(__file__).resolve().parents[1] / 'kb_luftfoto_refresh' / 'thumbnail_cache.py'
spec = importlib.util.spec_from_file_location('thumbnail_cache', source)
cache_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_module)
image_extension = cache_module.image_extension
valid_thumbnail_url = cache_module.valid_thumbnail_url
cache_thumbnails = cache_module.cache_thumbnails


class FakeResponse:
    headers = {'Content-Length': '12'}

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.data


class ThumbnailCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = self.temp.name
        self.placeholder = os.path.join(self.cache, 'missing.png')
        self.photo_url = 'https://www.kb.dk/imageService/w150/h150/example.jpg'

    def feature(self, url):
        return {'properties': {'subjectThumbnailSrc': url}}

    def test_mime_magic(self):
        self.assertEqual(image_extension(b'\xff\xd8\xfftest'), '.jpg')
        self.assertEqual(image_extension(b'\x89PNG\r\n\x1a\nrest'), '.png')
        self.assertIsNone(image_extension(b'<html>404</html>'))

    def test_only_kb_hosts_and_http_urls(self):
        self.assertTrue(valid_thumbnail_url(self.photo_url))
        self.assertFalse(valid_thumbnail_url('file:///etc/passwd'))
        self.assertFalse(valid_thumbnail_url('https://kb.dk.evil.com/a.jpg'))
        self.assertFalse(valid_thumbnail_url('http://127.0.0.1/a.jpg'))

    @patch.object(cache_module, 'urlopen')
    def test_success_caches_local_image(self, mock_open):
        mock_open.return_value = FakeResponse(b'\xff\xd8\xfftestjpeg')
        photos = [self.feature(self.photo_url)]
        stats = cache_thumbnails(photos, self.cache, self.placeholder)
        self.assertEqual(stats['downloaded'], 1)
        self.assertTrue(os.path.isfile(photos[0]['properties']['kb_thumb_file']))
        other = [self.feature(self.photo_url)]
        stats = cache_thumbnails(other, self.cache, self.placeholder)
        self.assertEqual(stats['cached'], 1)
        self.assertEqual(mock_open.call_count, 1)

    @patch.object(cache_module, 'urlopen')
    def test_404_negative_cache_and_placeholder(self, mock_open):
        mock_open.side_effect = HTTPError(self.photo_url, 404, 'not found', {}, None)
        photos = [self.feature(self.photo_url)]
        self.assertEqual(cache_thumbnails(photos, self.cache, self.placeholder)['missing'], 1)
        self.assertEqual(photos[0]['properties']['kb_thumb_file'], self.placeholder)
        stats = cache_thumbnails([self.feature(self.photo_url)], self.cache, self.placeholder)
        self.assertEqual(stats['known_missing'], 1)
        self.assertEqual(mock_open.call_count, 1)

    @patch.object(cache_module, 'urlopen')
    def test_non_image_does_not_enter_cache(self, mock_open):
        mock_open.return_value = FakeResponse(b'<html>Not an image</html>')
        photos = [self.feature(self.photo_url)]
        self.assertEqual(cache_thumbnails(photos, self.cache, self.placeholder)['not_image'], 1)
        self.assertEqual(photos[0]['properties']['kb_thumb_file'], self.placeholder)

    def test_missing_url_uses_placeholder(self):
        photos = [self.feature('')]
        self.assertEqual(cache_thumbnails(photos, self.cache, self.placeholder), {})
        self.assertEqual(photos[0]['properties']['kb_thumb_file'], self.placeholder)


if __name__ == '__main__':
    unittest.main()
