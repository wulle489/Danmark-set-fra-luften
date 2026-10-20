from .kb_luftfoto_refresh import KBLuftfotoRefreshPlugin


def classFactory(iface):
    return KBLuftfotoRefreshPlugin(iface)
