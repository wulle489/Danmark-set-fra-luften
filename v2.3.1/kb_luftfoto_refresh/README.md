# Danmark set fra luften — v2.3.1

Qt5/Qt6-oriented QGIS 3.28–4.x plugin, using Det Kgl. Biblioteks DSfL JSON API and a **local GeoJSON point layer**.

## Install

Install the ZIP from **QGIS > Plugins > Manage and Install Plugins > Install from ZIP**. If you still see `KB Luftfoto Refresh: Layer refreshed, but the QML style could not be fully applied`, you are still running the original v1.1 plugin; check for duplicate installed plugin folders and disable that old installation.

## Toolbar

- **KB button**: reload the current map extent using the most recently used category.
- **Arrow on KB button**: choose and immediately load `Alle`, `Skråfoto`, `Lodfoto`, or `Andre`.
- **Photo-open button** (right of KB): select it, then click a KB point to open its full image (`src`/`subjectImageSrc`) in your web browser, falling back to page links and thumbnails when necessary. The tool stays active for multiple clicks. Select another map tool to exit.

The Plugins menu also contains these commands, a record limit setting (default 500, maximum 10,000), a GeoJSON export, and cancellation. Downloads are asynchronous.

## Thumbnails and HTTP 404

The original KB API can return discontinued image URLs. These are **not** failures of GeoJSON conversion. The new renderer uses *only local thumbnail files*. Thumbnails are downloaded with up to 6 concurrent requests, cached in the QGIS profile, and a missing-photo placeholder is shown when the KB URL responds with 404, is unavailable, or isn't a real image. Known 404/410 URLs are negatively cached for 7 days to avoid repeated requests. Errors appear as aggregate counts instead of one QGIS redraw warning per broken image URL.

No QML file is needed and the old QML setting is not used. GeoJSON and thumbnails remain within the QGIS profile under `kb_luftfoto_geojson` so project reopening can use them. The GeoJSON export includes the local thumbnail paths, meaning portable styling requires copying the image cache as well.

## Replacement behavior

**Every successful refresh replaces existing layers named `KB luftfoto...`**, including legacy KML layers and layers created by previous versions. The existing layer is preserved on failed or cancelled downloads or when zero matching photos are returned. The new layer must be successfully created and styled before the old layer is removed.

## Limitations

- Testing without a QGIS 4.2 installation is limited to source compilation and isolated Python tests. Run the ZIP in QGIS to verify UI placement and map interactions.
- Photo image links supplied by KB may remain broken even when metadata is valid. The plugin does not invent alternate URLs or suppress the broken-link status.
- Fetching hundreds or thousands of thumbnails adds time to initial refresh; existing cached thumbnails are reused on subsequent refreshes.
- The API returns a list of records; the plugin generates real GeoJSON rather than treating the JSON as native GeoJSON.

## Tests

From the source package root: `python -m unittest discover -s tests -v`.

## Fix in 2.2.0

KB's current DSfL GeoJSON includes `src` (full photo URL) and `thumbnail` (small image).
The old Open Photo tool only checked `subjectLink` and `subjectImageSrc`, so it wrongly
reported no valid photo URL for existing features that only had `src`. It now reads
`src` first and also handles existing v2.1 layers **without refreshing**. New
GeoJSON output includes `subjectImageSrc` as an alias for `src`.

The button hands the image URL to the system browser. If KB returns an actual
HTTP 404 for that image, the browser may still display an error; this is a
different issue from the old "no valid photo link" warning.


## Fix in 2.3.0

Routine service messages are now shown more quietly in the QGIS status bar, while actual warnings and errors still use the message bar. The Open Photo toolbar icon can be customized separately. In this GitHub-ready bundle, `no_thumbnail.png` is intentionally identical to `icon.png`.

## Security patch in 2.3.1

Thumbnail downloads use HTTP(S) only for `kb.dk` and its subdomains, and
apply the same allowlist to every HTTP redirect. This replaces the generic
`urllib.request.urlopen` call flagged as Bandit B310 by the QGIS plugin checker.
There are no changes to the GeoJSON download, layer replacement, or photo-opening
features.
