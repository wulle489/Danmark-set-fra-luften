# Danmark set fra luften — v2.3.1

Patch release for the existing v2.3 source folder.

- `kb_luftfoto_refresh/` — plugin source code, including custom `open_photo.png`.
- `tests/` — isolated Python unit tests.
- `Danmark_set_fra_luften_v2_3_1_QGIS_plugin.zip` — installable QGIS plugin.

`no_thumbnail.png` matches `icon.png` exactly.

Upload this `v2.3` directory to the existing GitHub repository, replacing the
previous v2.3 directory. Publish the installable ZIP as a release asset for
tag `v2.3.1`.

Security fix: `thumbnail_cache.py` restricts requests and redirects to KB
HTTP(S) URLs. Bandit B310 no longer applies.
