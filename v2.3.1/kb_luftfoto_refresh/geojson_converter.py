"""Turn DSfL's JSON response (which is not necessarily GeoJSON) into GeoJSON.

This module deliberately has no QGIS/Qt dependencies, so the conversion can be
unit-tested without QGIS installed. The old COP 'copjects' response, a GeoJSON
FeatureCollection, and standard list-based REST responses are supported.
"""

import json
import math


RECORD_KEYS = ("features", "copjects", "items", "results", "photos", "records", "objects", "documents", "images")
WRAPPER_KEYS = ("data", "response", "result", "payload")
TOTAL_KEYS = ("totalResultsCount", "totalResults", "totalCount", "total", "numberMatched", "count")
PAGES_KEYS = ("pagesCount", "totalPages", "pageCount", "pages")


class ResponseFormatError(ValueError):
    """The service returned something we do not recognize as a photo collection."""


def _integer(value):
    try:
        return max(0, int(value)) if value is not None and not isinstance(value, bool) else None
    except (ValueError, TypeError):
        return None


def _first_dict_value(data, keys):
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if v is not None and not isinstance(v, (dict, list)):
                return v
    return None


def _find_collection(root, depth=0):
    """Return (records, associated wrappers) without mistaking an error for 0 records."""
    if isinstance(root, list):
        return root, []
    if not isinstance(root, dict) or depth > 5:
        raise ResponseFormatError("Svaret indeholder ingen genkendelig liste af fotos.")
    if root.get("status") in ("ERROR", "FAIL", "FAILED") or root.get("error"):
        raise ResponseFormatError(str(root.get("message") or root.get("error") or "Fejl fra KB API"))
    for key in RECORD_KEYS:
        candidate = root.get(key)
        if isinstance(candidate, list):
            return candidate, [root]
        if isinstance(candidate, dict):
            try:
                result, wrappers = _find_collection(candidate, depth + 1)
                return result, [root] + wrappers
            except ResponseFormatError:
                pass
    for key in WRAPPER_KEYS:
        if isinstance(root.get(key), (dict, list)):
            try:
                result, wrappers = _find_collection(root[key], depth + 1)
                return result, [root] + wrappers
            except ResponseFormatError:
                pass
    keys = ", ".join(sorted(str(k) for k in root)[:15])
    raise ResponseFormatError("Ukendt JSON-struktur fra KB API. Topfelter: " + keys)


def parse_page(raw):
    """Return records, total_records (optional), total_pages (optional)."""
    payload = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    records, wrappers = _find_collection(payload)
    total = None
    pages = None
    for wrapper in wrappers:
        if total is None:
            total = _integer(_first_dict_value(wrapper, TOTAL_KEYS))
        if pages is None:
            pages = _integer(_first_dict_value(wrapper, PAGES_KEYS))
    return records, total, pages


def _as_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def _from_xy_mapping(d):
    if not isinstance(d, dict):
        return None
    # Explicit lon/lat avoids guessing between (lat,lon) and (lon,lat).
    lon = _first_dict_value(d, ("longitude", "Longitude", "lon", "lng", "long", "x", "X"))
    lat = _first_dict_value(d, ("latitude", "Latitude", "lat", "y", "Y"))
    if lon is not None and lat is not None:
        return _valid_coords(lon, lat)
    return None


def _valid_coords(lon, lat):
    x, y = _as_number(lon), _as_number(lat)
    if x is None or y is None or not (-180 <= x <= 180 and -90 <= y <= 90):
        return None
    return [x, y]


def _coords_from_record(record):
    if not isinstance(record, dict):
        return None
    geometry = record.get("geometry")
    if isinstance(geometry, dict) and str(geometry.get("type", "")).lower() == "point":
        coords = geometry.get("coordinates")
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            return _valid_coords(coords[0], coords[1])
    # GeoJSON-in-GeoJSON, DSfL COP, and common REST coordinate representations.
    sources = [record]
    if isinstance(record.get("properties"), dict):
        sources.append(record["properties"])
    for source in sources:
        found = _from_xy_mapping(source)
        if found is not None:
            return found
        for key in ("coordinates", "point", "position", "location", "geo", "geolocation", "geoLocation", "wgs84"):
            obj = source.get(key)
            if isinstance(obj, dict):
                found = _from_xy_mapping(obj)
                if found is not None:
                    return found
                obj = obj.get("coordinates")
            if isinstance(obj, (list, tuple)) and len(obj) >= 2:
                found = _valid_coords(obj[0], obj[1])
                if found is not None:
                    return found
            # String long,lat: only accept if key explicitly says coordinates.
            if key == "coordinates" and isinstance(obj, str) and "," in obj:
                xy = obj.split(",")
                if len(xy) >= 2:
                    found = _valid_coords(xy[0], xy[1])
                    if found is not None:
                        return found
    return None


def _get(props, *keys):
    for key in keys:
        value = props.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return value
    return None


def _flatten_props(record):
    props = dict(record.get("properties") or {}) if isinstance(record.get("properties"), dict) else {}
    # Preserve all top-level metadata, except geometry, properties, and nested values.
    for key, value in record.items():
        # GeoJSON's type="Feature" is a structural marker, not the photo genre.
        if key == "type" and value == "Feature":
            continue
        if key not in ("geometry", "properties") and key not in props:
            props[key] = value
    aliases = {
        "photo_id": ("photo_id", "id", "identifier", "objectId", "object_id", "uuid"),
        "subjectName": ("subjectName", "photoName", "name", "title"),
        "subjectCreationDate": ("subjectCreationDate", "photoYear", "year", "date"),
        "subjectGenre": ("subjectGenre", "itemType", "type", "genre", "photoType", "category"),
        "subjectGeographic": ("subjectGeographic", "location", "place", "geographic"),
        "subjectLink": ("subjectLink", "atomLink", "link", "url", "detailUrl", "pageUrl"),
        "subjectImageSrc": ("subjectImageSrc", "src", "imgUrl", "imageUrl", "imageURL", "image_src"),
        "subjectThumbnailSrc": ("subjectThumbnailSrc", "imgURLThumb", "thumbnailUrl", "thumbnail"),
        "subjectCreatorName": ("subjectCreatorName", "creator", "photographer"),
    }
    for target, fields in aliases.items():
        if props.get(target) in (None, ""):
            value = _get(props, *fields)
            if value is not None:
                props[target] = value
    # OGR reads flat attributes reliably; nested metadata is retained as JSON text.
    flat = {}
    for key, value in props.items():
        if isinstance(value, (dict, list)):
            flat[str(key)] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            flat[str(key)] = value
        else:
            flat[str(key)] = str(value)
    return flat


def record_to_feature(record):
    if not isinstance(record, dict):
        return None
    coords = _coords_from_record(record)
    if coords is None:
        return None
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
            "properties": _flatten_props(record)}


def filter_feature(feature, filter_key):
    if filter_key == "all":
        return True
    p = feature.get("properties", {})
    genre = str(p.get("subjectGenre") or "").strip().casefold()
    # Titles returned by the library have usually been Skråfoto and Lodfoto.
    # Older KB search documentation also used numeric type values 1/2/3.
    is_lod = genre == "2" or "lodfoto" in genre or "vertical" in genre
    is_skraa = genre == "1" or "skråfoto" in genre or "skraafoto" in genre or "oblique" in genre
    if filter_key == "lodfoto":
        return is_lod
    if filter_key == "skraafoto":
        return is_skraa
    if filter_key == "andre":
        return not (is_lod or is_skraa)
    raise ValueError("Ukendt filter: " + str(filter_key))


def feature_collection(features):
    return {"type": "FeatureCollection", "name": "Danmark set fra luften", "features": features}
