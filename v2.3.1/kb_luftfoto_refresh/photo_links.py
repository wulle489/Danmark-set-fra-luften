"""Find the best usable KB photo link in a QGIS feature's attributes.

Pure Python so API schema variations and previously downloaded GeoJSON files
can be tested without a QGIS installation.
"""
from urllib.parse import urlsplit

# The DSfL API currently uses `src` for the original image. Older service
# versions and earlier plugin releases may use the normalized aliases instead.
# Open the full photo in preference to a landing page or a small thumbnail.
PHOTO_FIELDS = (
    'src', 'subjectImageSrc', 'imgUrl', 'imageUrl', 'imageURL', 'image_src',
    'subjectLink', 'atomLink', 'detailUrl', 'pageUrl', 'link', 'url',
    'subjectThumbnailSrc', 'thumbnailUrl', 'thumbnail',
)


def is_web_link(value):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme.lower() in ('http', 'https')
                and bool(parsed.hostname)
                and not parsed.username and not parsed.password)
    except ValueError:  # malformed URL, e.g. invalid IPv6 address
        return False


def preferred_photo_url(attributes):
    """Choose the image first, then the photo page, then any thumbnail.

    Names are matched case-insensitively for legacy GeoJSON compatibility.
    Empty fields and QGIS NULL QVariant values are safely ignored.
    """
    values = {str(key).casefold(): value for key, value in attributes.items()}
    for name in PHOTO_FIELDS:
        value = values.get(name.casefold())
        if is_web_link(value):
            return value.strip()
    return ''
