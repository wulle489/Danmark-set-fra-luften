"""QGIS map tool to open a KB photo by clicking its GeoJSON point."""

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.gui import QgsMapToolIdentifyFeature
from .photo_links import preferred_photo_url


class PhotoOpenTool(QgsMapToolIdentifyFeature):
    def __init__(self, iface, plugin):
        canvas = iface.mapCanvas()
        super().__init__(canvas)
        self.plugin = plugin
        self.iface = iface
        self.setToolName('Åbn KB-luftfoto')
        from qgis.core import QgsFeature
        try:
            self.featureIdentified[QgsFeature].connect(self._open_feature)
        except (KeyError, TypeError, AttributeError):
            self.featureIdentified.connect(self._open_feature)

    def _open_feature(self, feature):
        if not hasattr(feature, 'fields'):
            return
        # Resolve from the selected feature itself, including fields in v2.1's
        # already-loaded GeoJSON. No new API download is needed for this fix.
        attrs = {name: feature.attribute(name) for name in feature.fields().names()}
        url = preferred_photo_url(attrs)
        if not url:
            self.plugin._message('Der er intet gyldigt fotolink på dette punkt.', 'warning')
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.plugin._message('Browseren kunne ikke åbne fotolinket.', 'warning')
