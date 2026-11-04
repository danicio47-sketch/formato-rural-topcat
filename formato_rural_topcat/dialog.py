# -*- coding: utf-8 -*-
"""Dialogo de entrada de datos del formato rural."""
import os
import re

from qgis.PyQt.QtCore import Qt, QDate
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QCheckBox, QPushButton, QDoubleSpinBox,
    QDateEdit, QLabel, QTabWidget, QWidget, QHeaderView, QFileDialog,
)
from qgis.core import QgsMapLayerProxyModel, QgsSettings, QgsProject, QgsRasterLayer
from qgis.gui import QgsMapLayerComboBox, QgsFileWidget

from .generator import lados_desde_geometria, buscar_cartas, ORIENTACIONES, ESCALAS, fmt_area

SETTINGS = "FormatoRuralTopcat/"
CARPETAS_CARTAS_DEFAULT = []
CAMPOS_TEXTO = ("provincia", "canton", "parroquia", "sector", "propietario", "predio",
                "cod_prov", "cod_cat", "nombre_lote", "ref_carta", "tecnico")
DEFAULTS = {
    "provincia": "GUAYAS", "canton": "SAN JACINTO\nDE YAGUACHI", "parroquia": "", "sector": "",
    "propietario": "", "predio": "", "cod_prov": "", "cod_cat": "", "nombre_lote": "LOTE",
    "ref_carta": "", "tecnico": "",
}


class FormatoRuralDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = QgsSettings()
        self.setWindowTitle("Formato Rural TOPCAT - Levantamiento planimetrico")
        self.setMinimumWidth(760)
        self._lados_geom = None
        self._build()
        self.cargar_ajustes()

    # ------------------------------------------------------------------ UI
    def _build(self):
        main = QVBoxLayout(self)
        tabs = QTabWidget()
        main.addWidget(tabs)

        # --- Tab 1: Predio
        t1 = QWidget()
        f1 = QFormLayout(t1)
        self.cboCapa = QgsMapLayerComboBox()
        self.cboCapa.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.cboCapa.layerChanged.connect(self._capa_cambiada)
        f1.addRow("Capa del poligono:", self.cboCapa)
        self.lblEntidad = QLabel("")
        f1.addRow("Entidad:", self.lblEntidad)
        self.txt = {}
        etiquetas = {"provincia": "Provincia", "canton": "Canton", "parroquia": "Parroquia", "sector": "Sector",
                     "propietario": "Nombre del propietario", "predio": "Nombre del predio",
                     "cod_prov": "Codigo provisional", "cod_cat": "Codigo catastral",
                     "nombre_lote": "Nombre del lote (etiqueta y composicion)", "ref_carta": "Ref. carta topografica",
                     "tecnico": "Responsabilidad tecnica"}
        for k in CAMPOS_TEXTO:
            w = QLineEdit()
            if k in ("canton", "parroquia", "sector", "predio", "tecnico"):
                w.setPlaceholderText("Use \\n para salto de linea")
            self.txt[k] = w
            f1.addRow(etiquetas[k] + ":", w)
        self.dteFecha = QDateEdit(QDate.currentDate())
        self.dteFecha.setCalendarPopup(True)
        self.dteFecha.setDisplayFormat("dd/MM/yyyy")
        f1.addRow("Fecha:", self.dteFecha)
        tabs.addTab(t1, "Predio")

        # --- Tab 2: Linderos
        t2 = QWidget()
        v2 = QVBoxLayout(t2)
        h = QHBoxLayout()
        self.btnCargar = QPushButton("Cargar lados desde el poligono")
        self.btnCargar.clicked.connect(self.cargar_lados)
        h.addWidget(self.btnCargar)
        h.addStretch()
        v2.addLayout(h)
        self.tbl = QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["Lado", "Distancia (m)", "Colindante", "Orientacion", "Camino"])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        v2.addWidget(self.tbl)
        v2.addWidget(QLabel("La distancia se precarga desde las coordenadas; puede editarla para que coincida "
                            "con el plano original. Marque 'Camino' para dibujar el camino de ese lado (dos "
                            "bordes simetricos con el nombre centrado)."))
        hc = QHBoxLayout()
        hc.addWidget(QLabel("Ancho del camino (m):"))
        self.spnCamino = QDoubleSpinBox()
        self.spnCamino.setRange(1, 50)
        self.spnCamino.setValue(6)
        hc.addWidget(self.spnCamino)
        hc.addStretch()
        v2.addLayout(hc)
        he = QHBoxLayout()
        he.addWidget(QLabel("Eje del camino (opcional):"))
        self.cboEje = QgsMapLayerComboBox()
        self.cboEje.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.cboEje.setAllowEmptyLayer(True)
        self.cboEje.setLayer(None)
        self.cboEje.setToolTip("Linea del eje del camino digitalizada sobre la foto. Si se indica, el camino "
                               "sigue su curva y su borde interior queda sobre el lindero. Si se deja vacio, "
                               "el camino se dibuja paralelo al lado.")
        he.addWidget(self.cboEje, 1)
        v2.addLayout(he)
        tabs.addTab(t2, "Linderos")

        # --- Tab 3: Plano y salida
        t3 = QWidget()
        f3 = QFormLayout(t3)
        self.cboEscala = QComboBox()
        self.cboEscala.addItem("Automatica", 0)
        for s in ESCALAS:
            self.cboEscala.addItem("1:%s" % "{:,}".format(s).replace(",", "."), s)
        f3.addRow("Escala del plano:", self.cboEscala)
        ha = QHBoxLayout()
        self.txtArea = QLineEdit()
        self.txtArea.setPlaceholderText("Vacio = calcular (ej. 1,1999 Ha)")
        self.cboUnidad = QComboBox()
        self.cboUnidad.addItems(["Ha", "m2"])
        self.btnArea = QPushButton("Calcular")
        self.btnArea.clicked.connect(self._calcular_area)
        ha.addWidget(self.txtArea)
        ha.addWidget(self.cboUnidad)
        ha.addWidget(self.btnArea)
        f3.addRow("Area (texto):", ha)
        gb = QGroupBox("Carta topografica para el plano de ubicacion")
        fc = QFormLayout(gb)
        self.txtCarpetas = QLineEdit()
        self.txtCarpetas.setPlaceholderText("Carpetas con cartas (.tif) separadas por ;")
        self.txtCarpetas.editingFinished.connect(self.refrescar_cartas)
        btnCarp = QPushButton("...")
        btnCarp.setFixedWidth(28)
        btnCarp.clicked.connect(self._agregar_carpeta)
        hcp = QHBoxLayout()
        hcp.addWidget(self.txtCarpetas)
        hcp.addWidget(btnCarp)
        fc.addRow("Carpetas de cartas:", hcp)
        self.cboCarta = QComboBox()
        fc.addRow("Carta:", self.cboCarta)
        self.fileCarta = QgsFileWidget()
        self.fileCarta.setStorageMode(QgsFileWidget.GetFile)
        self.fileCarta.setFilter("Raster (*.tif *.tiff *.ecw *.jp2 *.img *.sid *.png *.jpg);;Todos (*.*)")
        self.fileCarta.fileChanged.connect(self._carta_externa)
        fc.addRow("Otra carta (archivo externo):", self.fileCarta)
        f3.addRow(gb)
        self.dirSalida = QgsFileWidget()
        self.dirSalida.setStorageMode(QgsFileWidget.GetDirectory)
        f3.addRow("Carpeta de salida:", self.dirSalida)
        self.chkPdf = QCheckBox("Exportar PDF (300 dpi)")
        self.chkPdf.setChecked(True)
        f3.addRow("", self.chkPdf)
        self.txtPdf = QLineEdit()
        self.txtPdf.setPlaceholderText("Vacio = PLANO <lote>.pdf")
        f3.addRow("Nombre del PDF:", self.txtPdf)
        tabs.addTab(t3, "Plano y salida")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Generar plano")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        main.addWidget(bb)

    # ------------------------------------------------------------------ cartas
    def _carpetas(self):
        return [c.strip() for c in self.txtCarpetas.text().split(";") if c.strip()]

    def _agregar_carpeta(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta con cartas topograficas")
        if d:
            carps = self._carpetas()
            if d not in carps:
                carps.append(d)
            self.txtCarpetas.setText(";".join(carps))
            self.refrescar_cartas()

    def refrescar_cartas(self):
        """Llena el combo: Ninguna / Automatica / rasters del proyecto / archivos de las carpetas."""
        actual = self.cboCarta.currentData()
        self.cboCarta.blockSignals(True)
        self.cboCarta.clear()
        self.cboCarta.addItem("(Ninguna)", None)
        self.cboCarta.addItem("Automatica: la carta que cubre el lote", "AUTO")
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsRasterLayer):
                self.cboCarta.addItem("Proyecto: %s" % lyr.name(), "LAYER:" + lyr.id())
        for ruta in buscar_cartas(self._carpetas()):
            self.cboCarta.addItem("Archivo: %s" % os.path.basename(ruta), ruta)
        ext = self.fileCarta.filePath().strip()
        if ext and os.path.isfile(ext):
            self.cboCarta.addItem("Externa: %s" % os.path.basename(ext), ext)
        idx = self.cboCarta.findData(actual) if actual else -1
        self.cboCarta.setCurrentIndex(idx if idx >= 0 else 1)
        self.cboCarta.blockSignals(False)

    def _carta_externa(self, ruta):
        ruta = ruta.strip()
        if ruta and os.path.isfile(ruta):
            self.refrescar_cartas()
            self.cboCarta.setCurrentIndex(self.cboCarta.findData(ruta))

    # ------------------------------------------------------------------ datos
    def refrescar_capas(self):
        self._capa_cambiada(self.cboCapa.currentLayer())
        self.refrescar_cartas()

    def _entidad(self):
        layer = self.cboCapa.currentLayer()
        if layer is None:
            return None, None
        sel = layer.selectedFeatures()
        if len(sel) == 1:
            return layer, sel[0]
        if len(sel) > 1:
            return layer, None
        if layer.featureCount() == 1:
            return layer, next(layer.getFeatures())
        return layer, None

    def _capa_cambiada(self, layer):
        layer, feat = self._entidad()
        if layer is None:
            self.lblEntidad.setText("Sin capa")
        elif feat is None:
            self.lblEntidad.setText("<span style='color:#b00'>Seleccione UNA entidad en la capa</span>")
        else:
            self.lblEntidad.setText("id %s  (%d vertices)" % (feat.id(), len(lados_desde_geometria(feat.geometry())[0])))
        if feat is not None:
            self.cargar_lados()

    def cargar_lados(self):
        layer, feat = self._entidad()
        self.tbl.setRowCount(0)
        if feat is None:
            return
        ring, lados = lados_desde_geometria(feat.geometry())
        self._lados_geom = lados
        for r, ld in enumerate(lados):
            self.tbl.insertRow(r)
            it = QTableWidgetItem("P%02d - P%02d" % (ld["desde"], ld["hasta"]))
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tbl.setItem(r, 0, it)
            self.tbl.setItem(r, 1, QTableWidgetItem("%.2f" % ld["dist"]))
            self.tbl.setItem(r, 2, QTableWidgetItem(""))
            cbo = QComboBox()
            cbo.addItems(ORIENTACIONES)
            cbo.setCurrentText(ld["orientacion"])
            self.tbl.setCellWidget(r, 3, cbo)
            chk = QCheckBox()
            self.tbl.setCellWidget(r, 4, chk)
        if not self.txtArea.text():
            self._calcular_area()

    def _calcular_area(self):
        layer, feat = self._entidad()
        if feat is None:
            return
        self.txtArea.setText(fmt_area(feat.geometry().area(), self.cboUnidad.currentText()))

    def _texto(self, k):
        return self.txt[k].text().replace("\\n", "\n").strip()

    def parametros(self):
        layer, feat = self._entidad()
        if layer is None or feat is None:
            raise ValueError("Seleccione una capa de poligonos y UNA sola entidad.")
        if self.tbl.rowCount() == 0:
            self.cargar_lados()
        lados = []
        for r in range(self.tbl.rowCount()):
            base = self._lados_geom[r]
            try:
                dist = float(self.tbl.item(r, 1).text().replace(",", "."))
            except (ValueError, AttributeError):
                raise ValueError("Distancia invalida en el lado %d." % (r + 1))
            col = self.tbl.item(r, 2).text().strip() if self.tbl.item(r, 2) else ""
            lados.append({
                "desde": base["desde"], "hasta": base["hasta"], "dist": dist, "colindante": col,
                "orientacion": self.tbl.cellWidget(r, 3).currentText(),
                "camino": self.tbl.cellWidget(r, 4).isChecked(),
            })
        nombre = self._texto("nombre_lote") or "LOTE"
        out_dir = self.dirSalida.filePath().strip()
        if not out_dir:
            home = QgsProject.instance().homePath() or os.path.expanduser("~")
            out_dir = os.path.join(home, nombre.upper())
        p = {k: self._texto(k) for k in CAMPOS_TEXTO}
        p.update({
            "layer": layer, "feature": feat, "lados": lados,
            "nombre_lote": nombre,
            "prefijo": re.sub(r"[^A-Za-z0-9]+", "_", nombre.upper()).strip("_") or "LOTE",
            "fecha": self.dteFecha.date().toString("dd/ MM / yyyy"),
            "escala": self.cboEscala.currentData(),
            "area_text": self.txtArea.text().strip(),
            "unidad_area": self.cboUnidad.currentText(),
            "carta": self.cboCarta.currentData(),
            "carpetas_cartas": self._carpetas(),
            "ancho_camino": self.spnCamino.value(),
            "eje_camino": self.cboEje.currentLayer(),
            "out_dir": out_dir,
            "exportar_pdf": self.chkPdf.isChecked(),
            "pdf_nombre": self.txtPdf.text().strip(),
        })
        return p

    # ------------------------------------------------------------------ ajustes
    def guardar_ajustes(self):
        for k in CAMPOS_TEXTO:
            if k != "nombre_lote":
                self.settings.setValue(SETTINGS + k, self.txt[k].text())
        self.settings.setValue(SETTINGS + "out_dir", self.dirSalida.filePath())
        self.settings.setValue(SETTINGS + "ancho_camino", self.spnCamino.value())
        self.settings.setValue(SETTINGS + "carpetas_cartas", self.txtCarpetas.text())

    def cargar_ajustes(self):
        # las carpetas por defecto solo se usan si existen en esta computadora
        por_defecto = ";".join(c for c in CARPETAS_CARTAS_DEFAULT if os.path.isdir(c))
        self.txtCarpetas.setText(str(self.settings.value(SETTINGS + "carpetas_cartas", por_defecto)))
        for k in CAMPOS_TEXTO:
            v = self.settings.value(SETTINGS + k, DEFAULTS[k])
            self.txt[k].setText(str(v).replace("\n", "\\n"))
        self.txt["nombre_lote"].setText(DEFAULTS["nombre_lote"])
        self.dirSalida.setFilePath(str(self.settings.value(SETTINGS + "out_dir", "")))
        self.spnCamino.setValue(float(self.settings.value(SETTINGS + "ancho_camino", 6)))
