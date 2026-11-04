# -*- coding: utf-8 -*-
"""Formato Rural TOPCAT - punto de entrada del plugin."""


def classFactory(iface):
    from .plugin import FormatoRuralPlugin
    return FormatoRuralPlugin(iface)
