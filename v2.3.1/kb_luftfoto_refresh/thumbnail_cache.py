"""Download KB thumbnails once into a per-profile cache, with a 404 fallback.

No Qt/QGIS imports: downloader runs in a background QThread and is unit testable.
The QGIS map renderer only sees *local paths*, never remote URLs.
"""
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_IMAGE_BYTES = 3 * 1024 * 1024
BAD_CACHE_SECONDS = 7 * 24 * 3600
MAX_WORKERS = 6


def image_extension(data):
    """Restrict cache to common actual image formats (not HTML error pages)."""
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return '.webp'
    return None


def valid_thumbnail_url(url):
    """Restrict requests to normal HTTP(S) URLs on Royal Library hosts.

    Refuse credentials, non-web schemes, unusual ports and embedded whitespace.
    This validator is applied to BOTH API URLs and HTTP redirect targets.
    """
    if not isinstance(url, str) or not url or any(c.isspace() for c in url):
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in ('https', 'http'):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if host != 'kb.dk' and not host.endswith('.kb.dk'):
        return False
    # Nonstandard ports could point at an unrelated service on a trusted host.
    if port is not None and port != (443 if parsed.scheme == 'https' else 80):
        return False
    return True


class KBOnlyRedirectHandler(HTTPRedirectHandler):
    """Disallow redirects outside KB's HTTP(S) image-service hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_thumbnail_url(newurl):
            raise HTTPError(newurl, 403, 'Blocked unsafe KB thumbnail redirect', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_kb_thumbnail(request, timeout=9):
    """Open only an approved KB URL, including any redirect hops.

    An ordinary urllib opener supports file:/ and FTP handlers. We instead
    validate the starting URL, override HTTP redirect handling, and use a
    normal HTTP(S) opener so QGIS/enterprise proxy settings still apply.
    """
    if not valid_thumbnail_url(request.full_url):
        raise ValueError('Unsafe KB thumbnail URL')
    return build_opener(KBOnlyRedirectHandler()).open(request, timeout=timeout)


def thumbnail_url(feature):
    props = feature.get('properties', {})
    for key in ('subjectThumbnailSrc', 'thumbnailUrl', 'thumbnail'):
        value = props.get(key)
        if valid_thumbnail_url(value):
            return value.strip()
    return ''


def _file_path_for_url(url, folder):
    fingerprint = hashlib.sha256(url.encode('utf-8')).hexdigest()
    return os.path.join(folder, fingerprint)


def _download_one(url, folder, stopped):
    # Defence in depth: this helper must be safe even when called directly.
    if not valid_thumbnail_url(url):
        return (url, '', 'invalid_url')
    stem = _file_path_for_url(url, folder)
    for ext in ('.jpg', '.png', '.gif', '.webp'):
        path = stem + ext
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return (url, path, 'cached')
    missing_marker = stem + '.missing'
    if os.path.isfile(missing_marker):
        try:
            if time.time() - os.path.getmtime(missing_marker) < BAD_CACHE_SECONDS:
                return (url, '', 'known_missing')
        except OSError:
            pass
    if stopped():
        return (url, '', 'cancelled')
    try:
        # KB sometimes returns HTML even for a successful status: check magic bytes.
        request = Request(url, headers={'User-Agent': 'QGIS-KB-Luftfoto/2.3.1', 'Accept': 'image/*'})
        with _open_kb_thumbnail(request, timeout=9) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > MAX_IMAGE_BYTES:
                return (url, '', 'oversized')
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return (url, '', 'oversized')
        ext = image_extension(data)
        if ext is None:
            return (url, '', 'not_image')
        path = stem + ext
        temp_path = path + '.part'
        with open(temp_path, 'wb') as output:
            output.write(data)
        os.replace(temp_path, path)
        if os.path.exists(missing_marker):
            os.unlink(missing_marker)
        return (url, path, 'downloaded')
    except HTTPError as exc:
        if exc.code in (404, 410):
            # Negative cache prevents a repeat of these permanently missing URLs.
            with open(missing_marker, 'w', encoding='utf-8') as stream:
                stream.write(str(exc.code))
            return (url, '', 'missing')
        return (url, '', 'http_error')
    except (OSError, ValueError, URLError):
        # Don't negatively cache transient failures (proxy, connectivity, 503).
        return (url, '', 'network_error')


def cache_thumbnails(features, folder, placeholder, stopped=lambda: False, progress=None):
    """Set kb_thumb_file on every feature; report aggregated per-URL statuses."""
    os.makedirs(folder, exist_ok=True)
    urls = list(dict.fromkeys(filter(None, (thumbnail_url(f) for f in features))))
    found = {}
    statuses = {}
    if urls:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_download_one, url, folder, stopped): url for url in urls}
            for i, future in enumerate(as_completed(futures), 1):
                if stopped():
                    for remaining in futures:
                        remaining.cancel()
                    break
                url, path, status = future.result()
                found[url] = path
                statuses[status] = statuses.get(status, 0) + 1
                if progress and (i % 25 == 0 or i == len(urls)):
                    progress(i, len(urls))
    for feature in features:
        url = thumbnail_url(feature)
        feature['properties']['kb_thumb_file'] = found.get(url) or placeholder
    return statuses
