# -*- coding: utf-8 -*-
"""Clase principal del plugin: menu, boton y ejecucion."""
import os
import traceback

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QDialog
from qgis.core import Qgis, QgsMessageLog

from .dialog import FormatoRuralDialog
from .generator import generar_plano

MENU = "&Formato Rural TOPCAT"


class FormatoRuralPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dlg = None

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, "img", "icon.png"))
        self.action = QAction(icon, "Generar plano rural...", self.iface.mainWindow())
        self.action.setObjectName("formatoRuralTopcatAction")
        self.action.setToolTip("Genera el plano de levantamiento planimetrico (formato rural TOPCAT)")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)

    def unload(self):
        if self.action is not None:
            self.action.triggered.disconnect(self.run)
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.dlg is not None:
            self.dlg.deleteLater()
            self.dlg = None

    def run(self):
        if self.dlg is None:
            self.dlg = FormatoRuralDialog(self.iface, self.iface.mainWindow())
        dlg = self.dlg
        dlg.refrescar_capas()
        if dlg.exec_() != QDialog.Accepted:
            return
        try:
            params = dlg.parametros()
        except ValueError as e:
            QMessageBox.warning(self.iface.mainWindow(), "Formato Rural TOPCAT", str(e))
            return
        try:
            res = generar_plano(params, self.iface)
        except Exception as e:  # noqa: BLE001 - se muestra al usuario
            QgsMessageLog.logMessage(traceback.format_exc(), "Formato Rural TOPCAT", Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Formato Rural TOPCAT",
                                 "Error al generar el plano:\n%s" % e)
            return
        dlg.guardar_ajustes()
        msg = "Composicion '%s' creada." % res["layout"].name()
        if res.get("pdf"):
            msg += " PDF: %s" % res["pdf"]
        self.iface.messageBar().pushMessage("Formato Rural TOPCAT", msg, level=Qgis.Success, duration=8)
        self.iface.openLayoutDesigner(res["layout"])
