"""QGIS 3/Qt5 and QGIS 4/Qt6 plugin for Denmark set fra luften.

Downloads the *JSON* representation from the modern KB DSfL service and writes
local, project-usable GeoJSON. Network I/O uses Qt's asynchronous network
manager, not an OGR remote KML layer or synchronous Python requests.
"""

import json
import os
import shutil
import tempfile
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QSettings, QUrl, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QToolButton, QMenu
try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    QgsNetworkAccessManager,
    QgsProject,
    QgsVectorLayer,
    QgsMarkerSymbol,
    QgsRasterMarkerSymbolLayer,
    QgsProperty,
    QgsSymbolLayer,
)

from .photo_tool import PhotoOpenTool
from .thumbnail_cache import cache_thumbnails

from .geojson_converter import (
    ResponseFormatError,
    feature_collection,
    filter_feature,
    parse_page,
    record_to_feature,
)


class ThumbnailWorker(QThread):
    result = pyqtSignal(object, object)
    progress = pyqtSignal(int, int)

    def __init__(self, features, cache_directory, placeholder, parent=None):
        super().__init__(parent)
        self.features = features
        self.cache_directory = cache_directory
        self.placeholder = placeholder
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            stats = cache_thumbnails(
                self.features, self.cache_directory, self.placeholder,
                lambda: self._stopped,
                lambda done, total: self.progress.emit(done, total))
            self.result.emit(self.features, stats)
        except Exception as exc:
            # The plugin can still render a local placeholder if downloading fails.
            for feature in self.features:
                feature['properties']['kb_thumb_file'] = self.placeholder
            self.result.emit(self.features, {'failure': str(exc)})


class KBLuftfotoRefreshPlugin:
    MENU_NAME = "Danmark set fra luften"
    LAYER_PREFIX = "KB luftfoto"
    BASE_URL = "https://api.kb.dk/data/rest/api/dsfl"
    SETTINGS_FILTER = "KBLuftfotoRefresh/filterKey"
    SETTINGS_LIMIT = "KBLuftfotoRefresh/maxRecords"
    SETTINGS_LAST_FILE = "KBLuftfotoRefresh/lastGeoJSON"
    MANAGED_TAG = "kb_luftfoto_refresh/managed"
    PAGE_SIZE = 500
    LIMIT_DEFAULT = 500
    LIMIT_MAX = 10000
    FILTERS = {
        "all": "Alle",
        "skraafoto": "Skråfoto",
        "lodfoto": "Lodfoto",
        "andre": "Andre",
    }

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.refresh_action = None
        self.cancel_action = None
        self._reply = None
        self._cancelled = False
        self.toolbar = None
        self.refresh_button = None
        self.open_action = None
        self._open_tool = None
        self._thumb_worker = None
        self._category_actions = []
        self._reset_job()

    def _reset_job(self):
        self._records_seen = 0
        self._page = 1
        self._features = []
        self._ids_seen = set()
        self._total_results = None
        self._total_pages = None
        self._filter_key = "all"
        self._limit = self.LIMIT_DEFAULT
        self._bbox = None
        self._skipped_coords = 0
        self._last_request_url = ""
        self._reply = None

    def initGui(self):
        icon_file = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_file) if os.path.isfile(icon_file) else QIcon.fromTheme("view-refresh")

        # A dedicated toolbar keeps the split category button and photo button adjacent.
        self.toolbar = self.iface.addToolBar("Danmark set fra luften")
        self.toolbar.setObjectName("DanmarkSetFraLuftenToolbar")
        self.refresh_button = QToolButton(self.toolbar)
        self.refresh_button.setIcon(icon)
        self.refresh_button.setAutoRaise(True)
        popup_mode = getattr(getattr(QToolButton, 'ToolButtonPopupMode', QToolButton),
                             'MenuButtonPopup')
        self.refresh_button.setPopupMode(popup_mode)
        categories = QMenu(self.refresh_button)
        for key, label in self.FILTERS.items():
            action = categories.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, k=key: self.refresh_layer(k))
            self._category_actions.append((key, action))
        self.refresh_button.setMenu(categories)
        self.refresh_button.clicked.connect(self.refresh_layer)
        self.toolbar.addWidget(self.refresh_button)

        # One-click on a photo point opens subjectLink, without the generic QGIS Actions menu.
        photo_icon = QIcon(os.path.join(os.path.dirname(__file__), "open_photo.png"))
        self.open_action = QAction(photo_icon, "Åbn foto (klik på et KB-punkt)", self.iface.mainWindow())
        self.open_action.setToolTip("Åbn foto: klik på et KB-luftfotopunkt i kortet")
        self.open_action.setCheckable(True)
        self.open_action.triggered.connect(self.toggle_photo_tool)
        self.toolbar.addAction(self.open_action)
        self.iface.addPluginToMenu(self.MENU_NAME, self.open_action)
        self.actions.append((self.open_action, False))
        try:
            self.iface.mapCanvas().mapToolSet.connect(self._map_tool_changed)
        except (AttributeError, TypeError):
            pass

        self.refresh_action = self._add_action("Opdater luftfotos", self.refresh_layer)
        for key, label in self.FILTERS.items():
            self._add_action(f"Hent: {label}", lambda checked=False, k=key: self.refresh_layer(k))
        self._add_action("Maks. antal poster…", self.select_limit)
        self._add_action("Gem seneste GeoJSON som…", self.save_geojson_as)
        self.cancel_action = self._add_action("Afbryd indlæsning", self.cancel_fetch)
        self.cancel_action.setEnabled(False)
        self._update_title()

    def _add_action(self, label, callback, icon=None):
        action = QAction(icon, label, self.iface.mainWindow()) if icon else QAction(label, self.iface.mainWindow())
        action.triggered.connect(callback)
        self.iface.addPluginToMenu(self.MENU_NAME, action)
        self.actions.append((action, False))
        return action

    def unload(self):
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()
        if self._thumb_worker is not None:
            self._thumb_worker.stop()
            # QThread must finish before it can be destroyed during plugin reload.
            self._thumb_worker.wait()
        if self._open_tool is not None and self.iface.mapCanvas().mapTool() is self._open_tool:
            self.iface.mapCanvas().unsetMapTool(self._open_tool)
        try:
            self.iface.mapCanvas().mapToolSet.disconnect(self._map_tool_changed)
        except (AttributeError, TypeError, RuntimeError):
            pass
        self._open_tool = None
        for action, toolbar in self.actions:
            self.iface.removePluginMenu(self.MENU_NAME, action)
            action.deleteLater()
        self.actions.clear()
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        self.refresh_button = None
        self.open_action = None
        self.refresh_action = None
        self.cancel_action = None

    def _active_kb_layer(self):
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isValid() and (
                    layer.customProperty(self.MANAGED_TAG, False)
                    or layer.name().casefold().startswith(self.LAYER_PREFIX.casefold())):
                return layer
        return None

    def toggle_photo_tool(self, checked=False):
        canvas = self.iface.mapCanvas()
        if not checked:
            if self._open_tool is not None and canvas.mapTool() is self._open_tool:
                canvas.unsetMapTool(self._open_tool)
            return
        layer = self._active_kb_layer()
        if layer is None:
            self.open_action.setChecked(False)
            self._message("Indlæs et KB-luftfotolag, før du åbner et foto.", "warning")
            return
        if self._open_tool is None:
            self._open_tool = PhotoOpenTool(self.iface, self)
        self._open_tool.setLayer(layer)
        canvas.setMapTool(self._open_tool)
        self.open_action.setChecked(True)
        self._message("Klik på et KB-luftfotopunkt for at åbne fotoet.", "info", duration=4)

    def _map_tool_changed(self, *args):
        if self.open_action is not None:
            self.open_action.setChecked(self._open_tool is not None and
                                        self.iface.mapCanvas().mapTool() is self._open_tool)

    def _message(self, text, kind="info", duration=7):
        title = "Danmark set fra luften"
        bar = self.iface.messageBar()
        status_bar = self.iface.mainWindow().statusBar()
        timeout_ms = max(0, int(duration * 1000)) if duration is not None else 0
        if kind == "error":
            bar.pushCritical(title, text)
            QgsMessageLog.logMessage(text, title, Qgis.MessageLevel.Critical)
        elif kind == "warning":
            bar.pushWarning(title, text)
            QgsMessageLog.logMessage(text, title, Qgis.MessageLevel.Warning)
        elif kind == "success":
            status_bar.showMessage(f"{title}: {text}", timeout_ms)
            QgsMessageLog.logMessage(text, title, Qgis.MessageLevel.Info)
        else:
            status_bar.showMessage(f"{title}: {text}", timeout_ms)
            QgsMessageLog.logMessage(text, title, Qgis.MessageLevel.Info)

    def _get_filter(self):
        value = QSettings().value(self.SETTINGS_FILTER, "all", type=str)
        return value if value in self.FILTERS else "all"

    def _get_limit(self):
        try:
            value = int(QSettings().value(self.SETTINGS_LIMIT, self.LIMIT_DEFAULT))
            return max(1, min(self.LIMIT_MAX, value))
        except (ValueError, TypeError):
            return self.LIMIT_DEFAULT

    def _update_title(self):
        label = self.FILTERS[self._get_filter()]
        maximum = self._get_limit()
        if self.refresh_action:
            self.refresh_action.setText(f"Opdater luftfotos: {label} (maks. {maximum})")
        if self.refresh_button:
            self.refresh_button.setToolTip(
                f"Hent KB-luftfotos: {label} (maks. {maximum}).\n"
                "Klik på pilen for at vælge billedtype.")
        for key, action in self._category_actions:
            action.setChecked(key == self._get_filter())

    def select_limit(self, checked=False):
        limit, ok = QInputDialog.getInt(
            self.iface.mainWindow(), "Maksimalt antal KB-poster",
            "Hvor mange poster skal hentes før billedtypefiltrering?\n"
            "Store kortudsnit kan indeholde langt flere fotos.",
            self._get_limit(), 1, self.LIMIT_MAX, 100)
        if ok:
            QSettings().setValue(self.SETTINGS_LIMIT, limit)
            self._update_title()

    def save_geojson_as(self, checked=False):
        source = None
        project = QgsProject.instance()
        for layer in project.mapLayers().values():
            if layer.customProperty(self.MANAGED_TAG, False):
                if layer.providerType() == "ogr":
                    source = layer.source().split("|", 1)[0]
                break
        if not source or not os.path.isfile(source):
            source = QSettings().value(self.SETTINGS_LAST_FILE, "", type=str)
        if not source or not os.path.isfile(source):
            self._message("Intet GeoJSON at gemme endnu. Hent først nogle luftfotos.", "warning")
            return
        filename, _ = QFileDialog.getSaveFileName(self.iface.mainWindow(),
                       "Gem luftfotos som GeoJSON", "kb_luftfotos.geojson", "GeoJSON (*.geojson)")
        if not filename:
            return
        if not filename.lower().endswith(".geojson"):
            filename += ".geojson"
        try:
            if os.path.abspath(source) != os.path.abspath(filename):
                shutil.copy2(source, filename)
            self._message(f"GeoJSON gemt: {filename}", "success")
        except OSError as exc:
            self._message(f"Kunne ikke gemme filen: {exc}", "error")

    def _get_bbox(self):
        canvas = self.iface.mapCanvas()
        rect = canvas.extent()
        crs = canvas.mapSettings().destinationCrs()
        target = QgsCoordinateReferenceSystem("EPSG:4326")
        if crs != target:
            rect = QgsCoordinateTransform(crs, target, QgsProject.instance()).transformBoundingBox(rect)
        # DSfL explicitly expects east,north,west,south.
        values = (rect.xMaximum(), rect.yMaximum(), rect.xMinimum(), rect.yMinimum())
        if not all(-180 <= v <= 180 for v in (values[0], values[2])) or not all(
                  -90 <= v <= 90 for v in (values[1], values[3])):
            raise ValueError("Kortudsnit ligger uden for gyldige geografiske koordinater.")
        if values[0] <= values[2] or values[1] <= values[3]:
            raise ValueError("Kortudsnittets koordinater er ugyldige.")
        return ",".join(f"{v:.10f}" for v in values)

    def refresh_layer(self, filter_key=None):
        # A QAction.triggered(bool) may pass False in place of filter_key.
        if isinstance(filter_key, bool):
            filter_key = None
        if self._reply is not None or self._thumb_worker is not None:
            self._message("En hentning er allerede i gang. Brug 'Afbryd indlæsning' først.", "warning")
            return
        filter_key = filter_key if filter_key in self.FILTERS else self._get_filter()
        QSettings().setValue(self.SETTINGS_FILTER, filter_key)
        self._update_title()
        self._reset_job()
        self._filter_key = filter_key
        self._limit = self._get_limit()
        try:
            self._bbox = self._get_bbox()
        except Exception as exc:
            self._message(f"Kunne ikke beregne kortudsnit i WGS84: {exc}", "error")
            return
        self._cancelled = False
        self._busy(True)
        self._request_page()

    def _busy(self, fetching):
        if self.refresh_action:
            self.refresh_action.setEnabled(not fetching)
        if self.refresh_button:
            self.refresh_button.setEnabled(not fetching)
        for _, action in self._category_actions:
            action.setEnabled(not fetching)
        if self.cancel_action:
            self.cancel_action.setEnabled(fetching)

    def _request_page(self):
        remaining = self._limit - self._records_seen
        if remaining <= 0:
            self._finish_job()
            return
        size = min(self.PAGE_SIZE, remaining)
        params = {"bbo": self._bbox, "itemsPerPage": size, "page": self._page}
        url = self.BASE_URL + "?" + urlencode(params)
        self._last_request_url = url
        self._message(f"Henter side {self._page} – {self._records_seen} poster modtaget...", "info", duration=2)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"application/json")
        try:
            request.setTransferTimeout(60000)
        except (AttributeError, TypeError):
            pass
        try:
            reply = QgsNetworkAccessManager.instance().get(request)
            self._reply = reply
            reply.finished.connect(lambda r=reply: self._page_finished(r))
        except Exception as exc:
            self._fail(f"Kunne ikke starte netværksanmodning: {exc}")

    def _page_finished(self, reply):
        # The network manager is shared by QGIS; only release our own reply.
        if self._reply is not reply:
            reply.deleteLater()
            return
        self._reply = None
        try:
            if self._cancelled:
                self._message("Hentning afbrudt. Det eksisterende lag er bevaret.", "warning")
                self._busy(False)
                return
            status_key = getattr(QNetworkRequest, 'HttpStatusCodeAttribute', None)
            if status_key is None:
                status_key = QNetworkRequest.Attribute.HttpStatusCodeAttribute
            status = reply.attribute(status_key)
            code = int(status) if status is not None else None
            no_error = getattr(QNetworkReply, 'NoError', None)
            if no_error is None:
                no_error = QNetworkReply.NetworkError.NoError
            if reply.error() != no_error or (code is not None and code >= 400):
                self._fail(f"KB API svarede med HTTP {code or '?'}: {reply.errorString()}. "
                           "Eksisterende lag er bevaret.")
                return
            body = bytes(reply.readAll())
            if not body:
                self._fail("Tomt svar fra KB API. Eksisterende lag er bevaret.")
                return
            try:
                records, total, pages = parse_page(body)
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                self._fail(f"KB JSON kunne ikke læses: {exc}")
                return
            if total is not None:
                self._total_results = total
            if pages is not None:
                self._total_pages = pages
            current_count = len(records)
            remaining = self._limit - self._records_seen
            self._records_seen += min(current_count, remaining)
            for record in records[:remaining]:
                feature = record_to_feature(record)
                if feature is None:
                    self._skipped_coords += 1
                    continue
                if not filter_feature(feature, self._filter_key):
                    continue
                props = feature["properties"]
                identity = props.get("photo_id")
                # Pages occasionally contain duplicates; no ID -> retain every photo.
                if identity not in (None, ""):
                    identity = str(identity)
                    if identity in self._ids_seen:
                        continue
                    self._ids_seen.add(identity)
                self._features.append(feature)
            # Stop when API declares the last page, returns a short/empty page,
            # or our user-configured maximum has been downloaded.
            if (self._records_seen >= self._limit or current_count == 0
                    or current_count < min(self.PAGE_SIZE, remaining)
                    or (self._total_pages and self._page >= self._total_pages)
                    or (self._total_results is not None and self._records_seen >= self._total_results)):
                self._finish_job()
            else:
                self._page += 1
                self._request_page()
        except Exception as exc:
            self._fail(f"Fejl ved behandling af KB-svaret: {exc}")
        finally:
            reply.deleteLater()

    def _finish_job(self):
        if self._records_seen and self._skipped_coords >= self._records_seen:
            self._fail("API'et returnerede poster, men ingen kunne omdannes til koordinatpunkter. "
                       "Eksisterende lag er bevaret.")
            return
        if not self._features:
            self._busy(False)
            self._message(f"Ingen fotos matchede filteret '{self.FILTERS[self._filter_key]}'. "
                          "Eksisterende lag er bevaret.", "warning")
            return
        # Resolve thumbnails off the GUI thread. The raster renderer uses local files
        # only: broken KB URLs will never be requested on every map redraw.
        directory = os.path.join(QgsApplication.qgisSettingsDirPath(), "kb_luftfoto_geojson")
        cache_dir = os.path.join(directory, "thumbnails")
        os.makedirs(cache_dir, exist_ok=True)
        placeholder = os.path.join(directory, "no_thumbnail.png")
        if not os.path.isfile(placeholder):
            shutil.copy2(os.path.join(os.path.dirname(__file__), "no_thumbnail.png"), placeholder)
        self._message("Metadata hentet. Henter og gemmer miniaturer…", "info", duration=4)
        self._thumb_worker = ThumbnailWorker(self._features, cache_dir, placeholder, self.iface.mainWindow())
        self._thumb_worker.result.connect(self._thumbnails_finished)
        self._thumb_worker.progress.connect(self._thumbnail_progress)
        self._thumb_worker.finished.connect(self._thumbnail_thread_stopped)
        self._thumb_worker.start()

    def _thumbnail_progress(self, done, total):
        self.iface.mainWindow().statusBar().showMessage(
            f"KB: behandler miniature {done}/{total}…")

    def _thumbnail_thread_stopped(self):
        worker = self._thumb_worker
        if worker is not None and not worker.isRunning():
            self._thumb_worker = None
            worker.deleteLater()

    def _thumbnails_finished(self, features, stats):
        self.iface.mainWindow().statusBar().clearMessage()
        if self._cancelled:
            self._busy(False)
            self._message("Indlæsning afbrudt. Det gamle KB-lag er bevaret.", "warning")
            return
        self._features = features
        self._install_layer(stats)

    def _style_thumbnails(self, layer):
        """A minimal built-in raster marker style; no QML paths or remote fetching."""
        fallback = os.path.join(QgsApplication.qgisSettingsDirPath(),
                                "kb_luftfoto_geojson", "no_thumbnail.png")
        symbol_layer = QgsRasterMarkerSymbolLayer(fallback, 12.0)
        property_name = getattr(QgsSymbolLayer, 'PropertyName', None)
        if property_name is None:
            property_name = QgsSymbolLayer.Property.PropertyName
        symbol_layer.setDataDefinedProperty(property_name,
                                            QgsProperty.fromField('kb_thumb_file'))
        symbol = QgsMarkerSymbol()
        symbol.changeSymbolLayer(0, symbol_layer)
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()

    def _install_layer(self, thumb_stats):
        try:
            folder = os.path.join(QgsApplication.qgisSettingsDirPath(), "kb_luftfoto_geojson")
            os.makedirs(folder, exist_ok=True)
            fd, filename = tempfile.mkstemp(prefix="kb_", suffix=".geojson", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(feature_collection(self._features), stream, ensure_ascii=False)
            title = f"{self.LAYER_PREFIX} – {self.FILTERS[self._filter_key]}"
            new_layer = QgsVectorLayer(filename, title, "ogr")
            if not new_layer.isValid():
                raise RuntimeError("QGIS kunne ikke åbne den dannede GeoJSON-fil.")
            new_layer.setCustomProperty(self.MANAGED_TAG, True)
            self._style_thumbnails(new_layer)
            project = QgsProject.instance()
            # Replace both previous 2.x managed GeoJSON layers and 1.x KML layers.
            old_layers = [layer for layer in project.mapLayers().values()
                          if layer.customProperty(self.MANAGED_TAG, False)
                          or layer.name().casefold().startswith(self.LAYER_PREFIX.casefold())]
            project.addMapLayer(new_layer)
            # Redirect the map tool before deleting its old layer pointer.
            if self._open_tool is not None:
                self._open_tool.setLayer(new_layer)
            for old in old_layers:
                project.removeMapLayer(old.id())
            QSettings().setValue(self.SETTINGS_LAST_FILE, filename)
            count_info = f"{self._records_seen} API-poster undersøgt"
            if self._total_results is not None:
                count_info += f" af {self._total_results}"
            errors = sum(int(thumb_stats.get(k, 0)) for k in (
                'missing', 'known_missing', 'oversized', 'not_image', 'network_error', 'http_error'))
            successes = sum(int(thumb_stats.get(k, 0)) for k in ('cached', 'downloaded'))
            message = (f"{len(self._features)} fotos indlæst ({count_info}). "
                       f"Miniaturer: {successes} tilgængelige, {errors} utilgængelige (vises med symbol).")
            if 'failure' in thumb_stats:
                message += " Miniaturefejl: " + str(thumb_stats['failure'])
            if self._total_results is not None and self._records_seen < self._total_results:
                message += " Maksimum nået; zoom ind eller øg grænsen for flere fotos."
            # A finished load is normal plugin behaviour; keep this in the status bar
            # instead of a popup, even if some thumbnails are unavailable.
            self._message(message, 'success', duration=8)
        except Exception as exc:
            self._fail(f"Kunne ikke oprette GeoJSON-laget: {exc}. Eksisterende lag er bevaret.")
        finally:
            self._busy(False)

    def _fail(self, message):
        self._busy(False)
        QgsMessageLog.logMessage("Seneste API URL: " + self._last_request_url,
                                 self.MENU_NAME, Qgis.MessageLevel.Warning)
        self._message(message, "error")

    def cancel_fetch(self, checked=False):
        self._cancelled = True
        if self._reply is not None:
            self._reply.abort()
        if self._thumb_worker is not None:
            self._thumb_worker.stop()
        self.iface.mainWindow().statusBar().clearMessage()
        if self._reply is None and self._thumb_worker is None:
            self._busy(False)
