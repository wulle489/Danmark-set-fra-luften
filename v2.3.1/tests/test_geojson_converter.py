import json
import importlib.util
import unittest
from pathlib import Path

# Import the independent converter without triggering the QGIS-only plugin entry point.
converter_file = Path(__file__).resolve().parents[1] / "kb_luftfoto_refresh" / "geojson_converter.py"
spec = importlib.util.spec_from_file_location("geojson_converter", converter_file)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ResponseFormatError = module.ResponseFormatError
parse_page = module.parse_page
record_to_feature = module.record_to_feature
filter_feature = module.filter_feature
feature_collection = module.feature_collection


class ConverterTests(unittest.TestCase):
    def test_cop_legacy_json_and_aliases(self):
        response = {"copjects": [{"id": "object80081", "longitude": "10.299912", "latitude": "55.208552",
                                  "itemType": "Skråfoto", "photoYear": "1949",
                                  "atomLink": "https://example.org/1", "imgURLThumb": "https://example.org/thumb"}],
                    "totalResultsCount": 153, "pagesCount": 4}
        records, total, pages = parse_page(json.dumps(response))
        self.assertEqual((total, pages), (153, 4))
        feature = record_to_feature(records[0])
        self.assertEqual(feature["geometry"]["coordinates"], [10.299912, 55.208552])
        self.assertEqual(feature["properties"]["photo_id"], "object80081")
        self.assertEqual(feature["properties"]["subjectGenre"], "Skråfoto")
        self.assertTrue(filter_feature(feature, "skraafoto"))
        self.assertFalse(filter_feature(feature, "lodfoto"))
        self.assertEqual(feature["properties"]["subjectLink"], "https://example.org/1")

    def test_geojson_feature_collection(self):
        payload = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [9.5, 55.2]},
             "properties": {"subjectGenre": "Lodfoto", "year": 1936}}]}
        records, total, pages = parse_page(payload)
        self.assertIsNone(total)
        self.assertIsNone(pages)
        feat = record_to_feature(records[0])
        self.assertEqual(feat["geometry"]["coordinates"], [9.5, 55.2])
        self.assertTrue(filter_feature(feat, "lodfoto"))
        self.assertEqual(json.loads(json.dumps(feature_collection([feat])))["type"], "FeatureCollection")

    def test_nested_items_and_coordinates_dict(self):
        data = {"response": {"total": "2", "items": [
            {"coordinates": {"lng": 10.2, "lat": 55.1}, "genre": "Oblique"},
            {"position": {"longitude": "11.2", "latitude": "54.8"}, "genre": "Other"}]}}
        records, total, _ = parse_page(data)
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 2)
        f1, f2 = [record_to_feature(v) for v in records]
        self.assertEqual(f1["geometry"]["coordinates"], [10.2, 55.1])
        self.assertTrue(filter_feature(f1, "skraafoto"))
        self.assertTrue(filter_feature(f2, "andre"))

    def test_api_src_is_mapped_to_full_image_field(self):
        feat = record_to_feature({'longitude': 11.2, 'latitude': 55.6,
            'src': 'http://kb-images.kb.dk/full.jpg',
            'thumbnail': 'http://kb-images.kb.dk/small.jpg'})
        self.assertEqual(feat['properties']['src'], 'http://kb-images.kb.dk/full.jpg')
        self.assertEqual(feat['properties']['subjectImageSrc'], 'http://kb-images.kb.dk/full.jpg')
        self.assertEqual(feat['properties']['subjectThumbnailSrc'], 'http://kb-images.kb.dk/small.jpg')

    def test_numeric_photo_categories(self):
        one = record_to_feature({"longitude": 10, "latitude": 55, "type": 1})
        two = record_to_feature({"longitude": 10, "latitude": 55, "type": 2})
        self.assertTrue(filter_feature(one, "skraafoto"))
        self.assertTrue(filter_feature(two, "lodfoto"))

    def test_invalid_coordinates_rejected(self):
        self.assertIsNone(record_to_feature({"longitude": 200, "latitude": 55}))
        self.assertIsNone(record_to_feature({"longitude": 10, "latitude": "foo"}))
        self.assertIsNone(record_to_feature({"title": "No coordinates"}))
        self.assertIsNone(record_to_feature({"longitude": float("nan"), "latitude": 55}))

    def test_unknown_structure_fails_closed(self):
        with self.assertRaises(ResponseFormatError):
            parse_page({"hello": "there"})
        with self.assertRaises(ResponseFormatError):
            parse_page({"error": "Service unavailable"})

    def test_empty_results_are_valid(self):
        self.assertEqual(parse_page({"copjects": [], "totalResultsCount": 0}), ([], 0, None))

    def test_preserve_structured_properties_as_json_string(self):
        feat = record_to_feature({"longitude": 10, "latitude": 55, "tags": ["a", "b"]})
        self.assertEqual(json.loads(feat["properties"]["tags"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
