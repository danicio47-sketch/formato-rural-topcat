# -*- coding: utf-8 -*-
"""Logica de generacion: capas, composicion y PDF del formato rural TOPCAT."""
import math
import os

from qgis.PyQt.QtCore import QVariant, QFile, QIODevice
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtXml import QDomDocument
from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsFields, QgsField,
    QgsGeometry, QgsPointXY, QgsRectangle, QgsWkbTypes, QgsVectorFileWriter,
    QgsSingleSymbolRenderer, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsPalLayerSettings, QgsTextFormat, QgsTextBufferSettings, QgsVectorLayerSimpleLabeling,
    QgsProperty, QgsPrintLayout, QgsReadWriteContext, QgsLayoutItemLabel, QgsLayoutItemMap,
    QgsLayoutItemShape, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsLayoutExporter,
    QgsUnitTypes, QgsLayerTreeGroup, QgsCoordinateTransform, QgsScaleBarSettings, QgsLayoutUtils,
    QgsTextRenderer, QgsLabeling, QgsLabelLineSettings,
)

PLUGIN_DIR = os.path.dirname(__file__)
TEMPLATE = os.path.join(PLUGIN_DIR, "templates", "formato_rural.qpt")
LOGO = os.path.join(PLUGIN_DIR, "img", "logo.svg")

ESCALAS = [250, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000, 7500, 10000, 15000, 20000]
ORIENTACIONES = ["NORTE", "SUR", "ESTE", "OESTE"]
ROW_H = 3.8  # alto de fila de la tabla de coordenadas (mm)
MAPA_W, MAPA_H = 243.0, 205.0  # tamano del mapa principal en la plantilla (mm)
ESCALA_UBIC = 50000  # escala del plano de ubicacion (cartas IGM 1:50.000)
GRILLA_UBIC = 1000  # intervalo de la grilla del plano de ubicacion (m)


# ----------------------------------------------------------------------------- utilidades
def fmt_coord(v):
    """649768.99 -> '649.768,990'"""
    s = "%.3f" % v
    ent, dec = s.split(".")
    ent = "{:,}".format(int(ent)).replace(",", ".")
    return "%s,%s" % (ent, dec)


def fmt_dist(v):
    return ("%.2f" % v).replace(".", ",") + "m"


def fmt_area(area_m2, unidad):
    if unidad == "Ha":
        return ("%.4f" % (area_m2 / 10000.0)).replace(".", ",") + " Ha"
    s = "{:,.2f}".format(area_m2)  # 35,281.41
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " m2"


def nombre_punto(i):
    return "P%02d" % i


def nombre_lado(i, j):
    return "P%d-P%d" % (i, j)


def vertices_horarios(geom):
    """Devuelve los vertices del anillo exterior en sentido horario, sin el punto de cierre."""
    if geom.isMultipart():
        polys = geom.asMultiPolygon()
        poly = max(polys, key=lambda p: QgsGeometry.fromPolygonXY(p).area())
    else:
        poly = geom.asPolygon()
    ring = list(poly[0])
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    # area con signo (shoelace): >0 antihorario
    s = 0.0
    for k in range(len(ring)):
        a, b = ring[k], ring[(k + 1) % len(ring)]
        s += a.x() * b.y() - b.x() * a.y()
    if s > 0:
        ring.reverse()
    return ring


def orientacion_lado(a, b, poly):
    """NORTE/SUR/ESTE/OESTE segun la normal exterior del lado."""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = math.hypot(dx, dy) or 1.0
    nx, ny = dy / L, -dx / L
    mx, my = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
    if poly.contains(QgsGeometry.fromPointXY(QgsPointXY(mx + nx, my + ny))):
        nx, ny = -nx, -ny
    ang = math.degrees(math.atan2(ny, nx))  # 0=este, 90=norte
    if -45 <= ang < 45:
        return "ESTE"
    if 45 <= ang < 135:
        return "NORTE"
    if ang >= 135 or ang < -135:
        return "OESTE"
    return "SUR"


def lados_desde_geometria(geom):
    """Lista de lados con datos por defecto para precargar el dialogo."""
    ring = vertices_horarios(geom)
    poly = QgsGeometry.fromPolygonXY([ring])
    lados = []
    n = len(ring)
    for k in range(n):
        a, b = ring[k], ring[(k + 1) % n]
        lados.append({
            "desde": k + 1, "hasta": (k + 1) % n + 1,
            "dist": round(math.hypot(b.x() - a.x(), b.y() - a.y()), 2),
            "colindante": "",
            "orientacion": orientacion_lado(a, b, poly),
            "camino": False,
        })
    return ring, lados


def escala_automatica(bbox):
    for sc in ESCALAS:
        if bbox.width() <= MAPA_W * 0.78 / 1000 * sc and bbox.height() <= MAPA_H * 0.55 / 1000 * sc:
            return sc
    return ESCALAS[-1]


def intervalo_grilla(sc):
    objetivo = sc * 0.04  # ~40 mm en papel
    for v in [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]:
        if v >= objetivo:
            return v
    return 5000


def segmento_barra(sc):
    objetivo = sc / 125.0
    for v in [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500]:
        if v >= objetivo:
            return v
    return 1000


def _caja(cx, cy, rot, w, h):
    """Rectangulo (terreno) que ocupa un rotulo centrado en cx,cy con giro `rot` (grados horario)."""
    t = math.radians(-rot)
    ux, uy = math.cos(t), math.sin(t)
    vx, vy = -uy, ux
    pts = [QgsPointXY(cx + sx * ux * w / 2 + sy * vx * h / 2, cy + sx * uy * w / 2 + sy * vy * h / 2)
           for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return QgsGeometry.fromPolygonXY([pts + [pts[0]]])


def _rot_texto(dx, dy):
    rot = -math.degrees(math.atan2(dy, dx))
    while rot <= -90:
        rot += 180
    while rot > 90:
        rot -= 180
    return rot


# ----------------------------------------------------------------------------- caminos
def _desvanecer(x):
    """1 -> 0 con transicion coseno (x en tramos de transicion)."""
    return 0.5 * (1 + math.cos(math.pi * x)) if x < 1 else 0.0


def _linea_simple(g):
    """Devuelve la parte lineal mas larga como LineString simple (o None)."""
    if g is None or g.isEmpty() or g.type() != QgsWkbTypes.LineGeometry:
        return None
    if g.isMultipart():
        g = g.mergeLines()
    if g.isMultipart():
        partes = [QgsGeometry.fromPolylineXY(pl) for pl in g.asMultiPolyline()]
        g = max(partes, key=lambda x: x.length())
    return g


def ejes_de_capa(capa, crs_destino):
    """Lineas del eje del camino (digitalizado sobre la foto) en el CRS del lote."""
    if capa is None:
        return []
    tr = None
    if capa.crs() != crs_destino:
        tr = QgsCoordinateTransform(capa.crs(), crs_destino, QgsProject.instance())
    out = []
    for f in capa.getFeatures():
        g = QgsGeometry(f.geometry())
        if g.isEmpty():
            continue
        if tr is not None:
            g.transform(tr)
        g = _linea_simple(g)
        if g is not None and g.length() > 0:
            out.append(g)
    return out


def _eje_corregido(ejes, tr, ancho, mm):
    """Eje digitalizado ajustado para que el borde interior del camino coincida con el lindero del tramo;
    fuera del frente vuelve al eje real con una transicion suave. None si ningun eje cubre el tramo."""
    cadena = tr["cadena"]
    a, b, L = tr["a"], tr["b"], tr["L"]
    ux, uy, ox, oy = tr["ux"], tr["uy"], tr["ox"], tr["oy"]
    lin = QgsGeometry.fromPolylineXY(cadena)
    cands = [g for g in ejes if g.distance(lin) <= max(3 * ancho, 15.0)]
    if not cands:
        return None
    mid = QgsGeometry.fromPointXY(QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
    eje = min(cands, key=lambda g: g.distance(mid))
    s = eje.simplify(max(0.5, 1.25 * mm))  # sin las ondulaciones de la digitalizacion
    eje = (s if s is not None and not s.isEmpty() else eje).smooth(5, 0.25).densifyByDistance(max(0.5, 0.5 * mm))
    eje = _linea_simple(eje)
    if eje is None:
        return None
    pts = eje.asPolyline()
    ss = [(v.x() - a.x()) * ux + (v.y() - a.y()) * uy for v in pts]
    if len(pts) < 2 or min(ss) > 0 or max(ss) < L:
        return None  # el eje no cubre todo el frente
    segs = list(zip(cadena[:-1], cadena[1:]))
    nrm = []
    for p, q in segs:
        lg = p.distance(q) or 1.0
        nx, ny = (q.y() - p.y()) / lg, -(q.x() - p.x()) / lg
        if nx * ox + ny * oy < 0:
            nx, ny = -nx, -ny
        nrm.append((nx, ny))
    lineas_seg = [QgsGeometry.fromPolylineXY([p, q]) for p, q in segs]
    D = max(30.0, 45.0 * mm)  # largo de la transicion
    out = []
    for v, sv in zip(pts, ss):
        if sv < 0:
            k, w = 0, _desvanecer(-sv / D)
        elif sv > L:
            k, w = len(segs) - 1, _desvanecer((sv - L) / D)
        else:
            pv = QgsGeometry.fromPointXY(v)
            k = min(range(len(segs)), key=lambda i: lineas_seg[i].distance(pv))
            w = 1.0
        p = segs[k][0]
        nx, ny = nrm[k]
        d = (v.x() - p.x()) * nx + (v.y() - p.y()) * ny
        c = w * (ancho / 2.0 - d)
        out.append(QgsPointXY(v.x() + c * nx, v.y() + c * ny))
    return QgsGeometry.fromPolylineXY(out)


def _partes(g, tipo):
    if g is None or g.isEmpty():
        return []
    partes = g.asGeometryCollection() if (g.isMultipart() or g.wkbType() == QgsWkbTypes.GeometryCollection) else [g]
    return [x for x in partes if not x.isEmpty() and x.type() == tipo]


def construir_caminos(tramos, lados, ancho, mm, ejes, tam, lote=None):
    """Arma cada camino como una franja simetrica: dos bordes paralelos a `ancho` y el eje para el
    rotulo. Con eje digitalizado sigue su curva (borde interior = lindero); si no, va paralelo al tramo.
    Nunca se dibuja dentro del lote. Devuelve dicts: nombre, eje (bool), bordes, banda, rotulo."""
    n_l = len(lados)
    adentro = lote.buffer(-0.05, 4) if lote is not None else None
    vias = []
    for tr in tramos:
        a, b = tr["a"], tr["b"]
        ux, uy, ox, oy, sal = tr["ux"], tr["uy"], tr["ox"], tr["oy"], tr["sal"]
        c = _eje_corregido(ejes, tr, ancho, mm) if ejes else None
        con_eje = c is not None
        off = ancho / 2.0 if con_eje else sal + ancho / 2.0
        if not con_eje:
            E = 1000.0 * mm
            c = QgsGeometry.fromPolylineXY([
                QgsPointXY(a.x() + ox * off - ux * E, a.y() + oy * off - uy * E),
                QgsPointXY(b.x() + ox * off + ux * E, b.y() + oy * off + uy * E)])

        def _loc(x, y):
            return c.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(x + ox * off, y + oy * off)))

        sA, sB = _loc(a.x(), a.y()), _loc(b.x(), b.y())
        s_lo, s_hi = min(sA, sB), max(sA, sB)
        sm = _loc(*tr["pm"])
        largo = len(tr["nombre"]) * tam * 0.21 * mm
        holg = 4.0 * mm
        Lc = c.length()
        cl = c.constGet()
        centro = min(max(sm, s_lo + holg + largo / 2), s_hi - holg - largo / 2)
        # al costado: primero hacia el extremo cuyo lado vecino no es camino (o hacia el oeste)
        prev_cam = lados[(tr["idx"][0] - 1) % n_l]["camino"]
        next_cam = lados[(tr["idx"][-1] + 1) % n_l]["camino"]
        en_a = (not prev_cam) if prev_cam != next_cam else (a.x() <= b.x())
        costados = []
        for s_end in ((sA, sB) if en_a else (sB, sA)):
            sg = -1.0 if s_end == s_lo else 1.0
            costados.append(s_end + sg * (7.0 * mm + largo / 2))
        cands = ([centro] if largo + 2 * holg <= s_hi - s_lo else []) + costados + [sm]

        def _invade(lc_):  # cuanto del rotulo cae dentro del lote (0 = libre)
            if adentro is None:
                return 0.0
            g = QgsGeometry(cl.curveSubstring(max(0.0, lc_ - largo / 2), min(Lc, lc_ + largo / 2)))
            return g.buffer(min(tam * 0.18 * mm, ancho * 0.45), 4).intersection(adentro).area()

        lc = min(cands, key=lambda x: (round(_invade(x), 1), cands.index(x)))
        E = max(20.0 * mm, s_lo - (lc - largo / 2) + 3 * mm, (lc + largo / 2) - s_hi + 3 * mm)
        tramo = QgsGeometry(cl.curveSubstring(max(0.0, s_lo - E), min(Lc, s_hi + E)))
        bordes = []
        for d in (ancho / 2.0, -ancho / 2.0):
            g = tramo.offsetCurve(d, 8, Qgis.JoinStyle.Round, 2)
            if adentro is not None and g is not None and not g.isEmpty():
                g = g.difference(adentro)  # el camino no entra al lote
            bordes += _partes(g, QgsWkbTypes.LineGeometry)
        banda = tramo.buffer(ancho / 2.0, 8, Qgis.EndCapStyle.Flat, Qgis.JoinStyle.Round, 2)
        rot = QgsGeometry(cl.curveSubstring(max(0.0, lc - largo / 2 - 4 * mm), min(Lc, lc + largo / 2 + 4 * mm)))
        vias.append({"nombre": tr["nombre"], "eje": con_eje, "bordes": bordes, "banda": banda, "rotulo": rot,
                     "medio": c.interpolate(min(max(sm, 0.0), Lc))})

    # cruces: una calle sin eje termina contra el camino con eje (no lo atraviesa)
    con = [v["banda"] for v in vias if v["eje"]]
    if con:
        u_eje = QgsGeometry.unaryUnion(con)
        for v in vias:
            if v["eje"]:
                continue
            m = v["medio"]

            def _unida(pz):
                q = pz.nearestPoint(m)
                seg = QgsGeometry.fromPolylineXY([m.asPoint(), q.asPoint()])
                return seg.intersection(u_eje).length() < 0.05

            v["bordes"] = [pz for g in v["bordes"] for pz in _partes(g.difference(u_eje), QgsWkbTypes.LineGeometry)
                           if _unida(pz)]
            pb = [pz for pz in _partes(v["banda"].difference(u_eje), QgsWkbTypes.PolygonGeometry)
                  if pz.distance(m) < 0.01]
            if pb:
                v["banda"] = QgsGeometry.unaryUnion(pb)
    # bocas abiertas: ningun borde entra en la franja de otro camino
    for v in vias:
        otras = [o["banda"] for o in vias if o is not v]
        if otras:
            u = QgsGeometry.unaryUnion(otras).buffer(0.02, 4)
            v["bordes"] = [pz for g in v["bordes"] for pz in _partes(g.difference(u), QgsWkbTypes.LineGeometry)]
    return vias


# ----------------------------------------------------------------------------- capas
try:  # QGIS >= 3.38 usa QMetaType en vez de QVariant.Type (evita DeprecationWarning)
    from qgis.PyQt.QtCore import QMetaType
    _TIPO = {QVariant.String: QMetaType.Type.QString, QVariant.Double: QMetaType.Type.Double,
             QVariant.Int: QMetaType.Type.Int}
    QgsField("prueba", _TIPO[QVariant.String])
except Exception:  # noqa: BLE001 - versiones antiguas
    _TIPO = None


def _campos(*defs):
    fs = QgsFields()
    for n, t in defs:
        fs.append(QgsField(n, _TIPO[t]) if _TIPO else QgsField(n, t))
    return fs


def _ruta_libre(path):
    """Quita del proyecto las capas que usan ese shapefile, lo borra y, si Windows lo tiene
    bloqueado, devuelve un nombre alternativo (_2, _3, ...)."""
    project = QgsProject.instance()
    norm = os.path.normcase(os.path.normpath(path))
    for lyr in list(project.mapLayers().values()):
        src = lyr.source().split("|")[0]
        if os.path.normcase(os.path.normpath(src)) == norm:
            project.removeMapLayer(lyr.id())
    if os.path.exists(path):
        QgsVectorFileWriter.deleteShapeFile(path)
    if os.path.exists(path):
        base, k = os.path.splitext(path)[0], 2
        while os.path.exists("%s_%d.shp" % (base, k)):
            k += 1
        path = "%s_%d.shp" % (base, k)
    return path


def _escribir_shape(path, fields, gtype, crs, feats):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "ESRI Shapefile"
    opts.fileEncoding = "UTF-8"
    path = _ruta_libre(path)
    w = QgsVectorFileWriter.create(path, fields, gtype, crs, QgsProject.instance().transformContext(), opts)
    if w.hasError() != QgsVectorFileWriter.NoError:
        raise RuntimeError("No se pudo escribir %s: %s" % (path, w.errorMessage()))
    for g, attrs in feats:
        f = QgsFeature(fields)
        f.setGeometry(g)
        f.setAttributes(attrs)
        w.addFeature(f)
    del w
    lyr = QgsVectorLayer(path, os.path.splitext(os.path.basename(path))[0], "ogr")
    if not lyr.isValid():
        raise RuntimeError("Capa invalida: %s" % path)
    return lyr


def _etiquetar(layer, expr, size, color, placement, extra=None):
    s = QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    s.displayAll = True
    tf = QgsTextFormat()
    f = QFont("Arial")
    f.setBold(True)
    tf.setFont(f)
    tf.setSize(size)
    tf.setColor(QColor(color))
    bf = QgsTextBufferSettings()
    bf.setEnabled(True)
    bf.setSize(0.8)
    bf.setColor(QColor("white"))
    tf.setBuffer(bf)
    s.setFormat(tf)
    s.placement = placement
    try:
        s.multilineAlign = Qgis.LabelMultiLineAlignment.Center
    except AttributeError:  # QGIS antiguo
        s.multilineAlign = QgsPalLayerSettings.MultiCenter
    if extra:
        extra(s)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


def _dd_posicion(s):
    p = s.dataDefinedProperties()
    p.setProperty(QgsPalLayerSettings.PositionX, QgsProperty.fromField("LX"))
    p.setProperty(QgsPalLayerSettings.PositionY, QgsProperty.fromField("LY"))
    p.setProperty(QgsPalLayerSettings.LabelRotation, QgsProperty.fromField("ROT"))
    p.setProperty(QgsPalLayerSettings.Hali, QgsProperty.fromValue("Center"))
    p.setProperty(QgsPalLayerSettings.Vali, QgsProperty.fromValue("Half"))
    s.setDataDefinedProperties(p)


def _dd_colindantes(s):
    p = s.dataDefinedProperties()
    p.setProperty(QgsPalLayerSettings.LabelRotation, QgsProperty.fromField("ROT"))
    p.setProperty(QgsPalLayerSettings.Size, QgsProperty.fromField("TAM"))
    p.setProperty(QgsPalLayerSettings.Hali, QgsProperty.fromValue("Center"))
    p.setProperty(QgsPalLayerSettings.Vali, QgsProperty.fromValue("Half"))
    s.setDataDefinedProperties(p)


def _etiquetar_camino(layer):
    """Nombre del camino curvo, siguiendo el eje, centrado en la linea (anclaje estricto al 50 %)."""
    s = QgsPalLayerSettings()
    s.fieldName = "NOMBRE"
    s.placement = Qgis.LabelPlacement.Curved
    tf = QgsTextFormat()
    f = QFont("Arial")
    f.setBold(True)
    tf.setFont(f)
    tf.setSize(8)
    tf.setColor(QColor("#8b0000"))
    bf = QgsTextBufferSettings()
    bf.setEnabled(True)
    bf.setSize(0.3)
    bf.setColor(QColor("white"))
    tf.setBuffer(bf)
    s.setFormat(tf)
    ls = s.lineSettings()
    ls.setPlacementFlags(QgsLabeling.LinePlacementFlag.OnLine)
    try:
        ls.setLineAnchorPercent(0.5)
        ls.setAnchorType(QgsLabelLineSettings.AnchorType.Strict)
        ls.setAnchorTextPoint(QgsLabelLineSettings.AnchorTextPoint.CenterOfText)
    except AttributeError:  # QGIS antiguo
        pass
    s.setLineSettings(ls)
    p = s.dataDefinedProperties()
    p.setProperty(QgsPalLayerSettings.Size, QgsProperty.fromField("TAM"))
    s.setDataDefinedProperties(p)
    s.displayAll = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


def crear_capas(params, ring, lados, sc, out_dir):
    """Crea los shapefiles del lote y devuelve un dict con las capas ya estilizadas."""
    crs = params["layer"].crs()
    poly = QgsGeometry.fromPolygonXY([ring])
    cen = poly.centroid().asPoint()
    n = len(ring)
    pref = params["prefijo"]
    LP = Qgis.LabelPlacement
    sdir = os.path.join(out_dir, "SHAPE")
    os.makedirs(sdir, exist_ok=True)
    capas = {}

    # LOTE
    lote = _escribir_shape(
        os.path.join(sdir, "%s_LOTE.shp" % pref),
        _campos(("LOTE", QVariant.String), ("PROPIET", QVariant.String), ("AREA_M2", QVariant.Double),
                ("LX", QVariant.Double), ("LY", QVariant.Double), ("ROT", QVariant.Double)),
        QgsWkbTypes.Polygon, crs, [(poly, [params["nombre_lote"], params["propietario"], round(poly.area(), 2),
                                           cen.x(), cen.y(), 0.0])])
    lote.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "178,230,190,200", "outline_color": "230,200,0", "outline_width": "0.6"})))
    texto_lote = params["nombre_lote"].replace("'", "''")
    area_txt = params["area_text"].replace("'", "''")
    _etiquetar(lote, "'%s' || '\\n' || 'AREA: %s'" % (texto_lote, area_txt), 10, "black", LP.OverPoint,
               _dd_posicion)
    capas["lote"] = lote

    # VERTICES
    vert = _escribir_shape(
        os.path.join(sdir, "%s_VERTICES.shp" % pref),
        _campos(("PUNTO", QVariant.String), ("X", QVariant.Double), ("Y", QVariant.Double),
                ("LX", QVariant.Double), ("LY", QVariant.Double), ("ROT", QVariant.Double)),
        QgsWkbTypes.Point, crs,
        [(QgsGeometry.fromPointXY(p), [nombre_punto(k + 1), round(p.x(), 3), round(p.y(), 3), p.x(), p.y(), 0.0])
         for k, p in enumerate(ring)])
    vert.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "color": "30,60,120", "outline_color": "0,0,0", "size": "2"})))
    tam_vert = float(params.get("tam_vert", 9.0))
    _etiquetar(vert, '"PUNTO"', tam_vert, "#c00000", LP.OverPoint, _dd_posicion)
    capas["vertices"] = vert

    # LADOS (distancias dentro del poligono) y COLINDANTES (fuera)
    # Todo SIMETRICO: misma separacion del lindero (en mm de papel) y mismo tamano en todos los lados,
    # cada rotulo centrado en su lado/tramo y paralelo a el.
    mm = sc / 1000.0  # metros de terreno por mm de papel
    tam_dist = float(params.get("tam_dist", 8.0))
    # medidas justo bajo la linea (adentro) y colindantes justo sobre ella (afuera)
    off_dist = params.get("off_dist_mm", 1.9) * mm
    off_col = params.get("off_col_mm", 2.8) * mm
    fila = 3.4 * mm  # salto a la segunda fila cuando un lado es mas corto que su texto
    ancho_cam = params["ancho_camino"]
    n_l = len(lados)
    feats_lados, feats_col, feats_cam = [], [], []

    def _largo(texto, tam):  # largo aproximado del texto en el terreno (m)
        return len(texto) * tam * 0.21 * mm

    def _colineal(i, j):
        a1, b1 = ring[lados[i]["desde"] - 1], ring[lados[i]["hasta"] - 1]
        a2, b2 = ring[lados[j]["desde"] - 1], ring[lados[j]["hasta"] - 1]
        h1 = math.atan2(b1.y() - a1.y(), b1.x() - a1.x())
        h2 = math.atan2(b2.y() - a2.y(), b2.x() - a2.x())
        return abs(math.degrees((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)) < 3.0

    def _afuera(a, b):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        L = math.hypot(dx, dy) or 1.0
        mx, my = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
        ox, oy = dy / L, -dx / L
        if poly.contains(QgsGeometry.fromPointXY(QgsPointXY(mx + ox * 0.5, my + oy * 0.5))):
            ox, oy = -ox, -oy
        return dx, dy, L, mx, my, ox, oy

    # distancias: centradas en su lado (simetrico). Solo si chocan con otra medida o con un vertice
    # se corren a lo largo del lado o pasan a una segunda fila hacia adentro.
    ocupado = [QgsGeometry.fromPointXY(p).buffer(1.0 * mm, 8) for p in ring]  # simbolo del vertice
    for k, ld in enumerate(lados):
        a, b = ring[ld["desde"] - 1], ring[ld["hasta"] - 1]
        dx, dy, L, mx, my, ox, oy = _afuera(a, b)
        rot = _rot_texto(dx, dy)
        txt = fmt_dist(float(ld["dist"])) + " "
        # la medida debe caber entre los dos vertices (con el espacio que ocupa su simbolo);
        # si el lado es corto se achica la letra (minimo 6 pt) antes que moverla de fila
        libre = max(L - 2 * 1.3 * mm, 1.0)
        tam_l = tam_dist
        if _largo(txt, tam_l) > libre:
            tam_l = max(6.0, tam_dist * libre / _largo(txt, tam_dist))
        w, h = _largo(txt, tam_l), tam_l * 0.42 * mm
        elegido = None
        # f = fila (0 pegada al lindero, 1 mas adentro, -1 justo por fuera); t = posicion a lo largo.
        # Primero se intenta pegada y centrada, luego corrida a lo largo y solo al final en otra fila.
        for f, t in ((0, .5), (0, .42), (0, .58), (0, .34), (0, .66), (1, .5), (1, .4), (1, .6), (-1, .5)):
            o = off_dist + f * fila if f >= 0 else -2.2 * mm
            cx, cy = a.x() + dx * t - ox * o, a.y() + dy * t - oy * o
            caja = _caja(cx, cy, rot, w, h)
            if not any(caja.intersects(z) for z in ocupado):
                elegido = (cx, cy, caja)
                break
        if elegido is None:
            cx, cy = mx - ox * off_dist, my - oy * off_dist
            elegido = (cx, cy, _caja(cx, cy, rot, w, h))
        ocupado.append(elegido[2])
        feats_lados.append((QgsGeometry.fromPolylineXY([a, b]),
                            [nombre_lado(ld["desde"], ld["hasta"]), float(ld["dist"]), ld["colindante"],
                             elegido[0], elegido[1], rot, tam_l]))

    # rotulo del lote: en la parte libre mas ancha, girado segun el eje si el lote es alargado
    tam_lote = 10.0
    lineas = [params["nombre_lote"], "AREA: %s" % params["area_text"]]
    obb = poly.orientedMinimumBoundingBox()[0].asPolygon()[0]
    e1 = (obb[1].x() - obb[0].x(), obb[1].y() - obb[0].y())
    e2 = (obb[2].x() - obb[1].x(), obb[2].y() - obb[1].y())
    eje, corto_e = (e1, e2) if math.hypot(*e1) >= math.hypot(*e2) else (e2, e1)
    alargado = math.hypot(*eje) > 2.5 * math.hypot(*corto_e)
    rot_l = _rot_texto(*eje) if alargado else 0.0
    w_l = max(len(s) for s in lineas) * tam_lote * 0.21 * mm
    h_l = len(lineas) * tam_lote * 0.45 * mm
    Lx = math.hypot(*eje)
    ux, uy = eje[0] / Lx, eje[1] / Lx
    ocx = sum(q.x() for q in obb[:4]) / 4
    ocy = sum(q.y() for q in obb[:4]) / 4
    dentro = poly.buffer(-0.8 * mm, 4)
    cands = []
    for fr in [.5] + [.5 + s * d / 100.0 for d in range(5, 46, 5) for s in (-1, 1)]:
        px, py = ocx + ux * Lx * (fr - 0.5), ocy + uy * Lx * (fr - 0.5)
        corte = QgsGeometry.fromPolylineXY([QgsPointXY(px + uy * 1e4, py - ux * 1e4),
                                            QgsPointXY(px - uy * 1e4, py + ux * 1e4)]).intersection(poly)
        if not corte.isEmpty():
            # centro del tramo de corte mas largo (en lotes con forma de L)
            partes = corte.asGeometryCollection() if corte.isMultipart() else [corte]
            cands.append(max(partes, key=lambda x: x.length()).centroid().asPoint())
    cands.append(poly.poleOfInaccessibility(0.1)[0].asPoint())
    # lote alargado: primero en una sola linea (NOMBRE - AREA), queda centrado y con aire
    # respecto de las medidas; si no cabe, en dos lineas y luego mas pequeno
    holgura = [z.buffer(1.5 * mm, 4) for z in ocupado]
    formatos = ([" - "] if alargado else []) + ["\n"]
    pos_l = None
    for tam_lote in (10.0, 8.0, 7.0):
        for sep in formatos:
            lin = [lineas[0] + sep + lineas[1]] if sep != "\n" else lineas
            w_l = max(len(s) for s in lin) * tam_lote * 0.21 * mm
            h_l = len(lin) * tam_lote * 0.45 * mm
            for c in cands:
                caja = _caja(c.x(), c.y(), rot_l, w_l, h_l)
                if dentro.contains(caja) and not any(caja.intersects(z) for z in holgura):
                    pos_l = c
                    break
            if pos_l is not None:
                break
        if pos_l is not None:
            break
    if pos_l is None:
        pos_l, tam_lote, sep = cands[0], 10.0, "\n"
        w_l = max(len(s) for s in lineas) * tam_lote * 0.21 * mm
        h_l = len(lineas) * tam_lote * 0.45 * mm
    ocupado.append(_caja(pos_l.x(), pos_l.y(), rot_l, w_l, h_l))
    sep_expr = "'\\n'" if sep == "\n" else "' - '"
    _etiquetar(lote, "'%s' || %s || 'AREA: %s'" % (texto_lote, sep_expr, area_txt), tam_lote, "black",
               LP.OverPoint, _dd_posicion)
    lote.dataProvider().changeAttributeValues({next(lote.getFeatures()).id(): {
        lote.fields().indexOf("LX"): pos_l.x(), lote.fields().indexOf("LY"): pos_l.y(),
        lote.fields().indexOf("ROT"): rot_l}})

    # colindantes: un rotulo centrado por tramo recto (lados consecutivos colineales con el mismo
    # colindante). Si el texto no cabe se reduce (minimo 6 pt); si aun asi no cabe: se omite cuando el
    # tramo vecino ya lleva el mismo nombre, o se escribe en una segunda fila hacia afuera.
    # Con agrupar_colindantes=True se agrupan los lados consecutivos aunque no sean colineales.
    agrupar = params.get("agrupar_colindantes", False)

    def _mismo(i, j):
        return lados[i]["colindante"].strip().upper() == lados[j]["colindante"].strip().upper() \
            and lados[i]["camino"] == lados[j]["camino"]

    def _une(i, j):
        return _mismo(i, j) and (agrupar or _colineal(i, j))

    # arranca en un lado que inicia tramo (para no partir un tramo que cruza el P01)
    ini = 0
    if n_l > 1 and not all(_une(k, (k + 1) % n_l) for k in range(n_l)):
        while _une((ini - 1) % n_l, ini):
            ini += 1
    tramos, k = [], 0
    while k < n_l:
        idx = [(ini + k) % n_l]
        while k + 1 < n_l and _une(idx[-1], (ini + k + 1) % n_l):
            k += 1
            idx.append((ini + k) % n_l)
        tramos.append(idx)
        k += 1

    tam_cam = 8.0 if ancho_cam / mm >= 5.0 else (6.5 if ancho_cam / mm >= 2.6 else 6.0)
    caminos_tr = []  # tramos con camino: se arman despues con construir_caminos()
    info = []
    for idx in tramos:
        a, b = ring[lados[idx[0]]["desde"] - 1], ring[lados[idx[-1]]["hasta"] - 1]
        dx, dy, L, mx, my, ox, oy = _afuera(a, b)
        sal = 0.0  # lo que sobresale la cadena respecto de la cuerda a-b
        for i in idx:
            p = ring[lados[i]["hasta"] - 1]
            sal = max(sal, (p.x() - a.x()) * ox + (p.y() - a.y()) * oy)
        ld = lados[idx[0]]
        if ld["camino"]:
            ux, uy = dx / L, dy / L
            if len(idx) > 1:
                # el nombre del camino va al centro del lado mas largo del tramo, lejos de los
                # vertices intermedios (sus numeros quedan libres)
                largo_i = max(idx, key=lambda i: float(lados[i]["dist"]))
                pa, pb = ring[lados[largo_i]["desde"] - 1], ring[lados[largo_i]["hasta"] - 1]
                cm = ((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2)
                s_ = (cm[0] - a.x()) * ux + (cm[1] - a.y()) * uy
                mx, my = a.x() + ux * s_, a.y() + uy * s_
            caminos_tr.append({"idx": idx, "a": a, "b": b, "ux": ux, "uy": uy, "ox": ox, "oy": oy, "L": L,
                               "sal": sal, "nombre": ld["colindante"], "pm": (mx, my),
                               "cadena": [ring[lados[i]["desde"] - 1] for i in idx] + [b]})
            o_c, tam = sal + ancho_cam / 2.0, tam_cam
            corto = False  # el nombre del camino va en su propia capa (rotulo curvo centrado)
        else:
            o_c, tam = sal + off_col, 8.0
            texto = ld["colindante"]
            palabras = texto.split()
            partido = False
            if _largo(texto, tam) > L * 0.9 and len(palabras) > 1:
                partido = True
                # no cabe en una linea: se parte en dos lineas parejas y se aleja media linea
                cortes = [(" ".join(palabras[:i]), " ".join(palabras[i:])) for i in range(1, len(palabras))]
                l1, l2 = min(cortes, key=lambda c: max(len(c[0]), len(c[1])))
                texto = l1 + "\n" + l2
                o_c += tam * 0.22 * mm
            largo = max(_largo(s, tam) for s in texto.split("\n"))
            if largo > L * 0.95:
                tam = max(6.0, tam * L * 0.95 / largo)
            # "corto": no cabe, o tuvo que partirse en dos lineas (si el mismo colindante sigue en un
            # lado largo contiguo, el nombre se escribe solo alli)
            corto = partido or max(_largo(s, tam) for s in texto.split("\n")) > L
        info.append({"nombre": ld["colindante"], "texto": texto if not ld["camino"] else ld["colindante"],
                     "a": (mx, my, ox, oy), "o": o_c,
                     "rot": _rot_texto(dx, dy), "tam": tam, "corto": corto, "camino": ld["camino"]})
    nt = len(info)

    def _nom(t):
        return info[t]["nombre"].strip().upper()

    # cadena de tramos seguidos con el mismo colindante (aunque no sean colineales): si alguno de la
    # cadena tiene espacio para el nombre, los cortos no se rotulan (el nombre va una sola vez)
    def _cadena_tiene_largo(t):
        if nt < 2:
            return False
        for paso in (1, -1):
            v = (t + paso) % nt
            while v != t and _nom(v) == _nom(t):
                if not info[v]["corto"]:
                    return True
                v = (v + paso) % nt
        return False

    for t, it in enumerate(info):
        if not it["nombre"] or it["camino"]:
            continue
        o = it["o"]
        if it["corto"]:
            if _cadena_tiene_largo(t):
                continue
            if "\n" not in it["texto"]:
                o += fila
        mx, my, ox, oy = it["a"]
        feats_col.append((QgsGeometry.fromPointXY(QgsPointXY(mx + ox * o, my + oy * o)),
                          [it["texto"], it["rot"], it["tam"]]))

    # caminos: franjas simetricas (siguiendo el eje digitalizado si se indico) y rotulo curvo centrado
    ejes = ejes_de_capa(params.get("eje_camino"), crs)
    vias = construir_caminos(caminos_tr, lados, ancho_cam, mm, ejes, tam_cam, poly) if caminos_tr else []
    feats_eje = []
    for v in vias:
        for g in v["bordes"]:
            if g.length() > 0.2 * mm:
                feats_cam.append((QgsGeometry.fromPolylineXY(g.asPolyline()), [v["nombre"]]))
        feats_eje.append((v["rotulo"], [v["nombre"], tam_cam]))

    # numeros de vertice: ordenados y a la misma distancia. Si el vertice da a UN camino van
    # perpendiculares a el, por fuera de su franja; si no, en la bisectriz exterior. Si chocan se
    # alejan en la misma direccion y solo como ultimo recurso se giran alrededor del punto.
    obst = list(ocupado[n:])  # medidas + rotulo del lote (sin los circulos de los vertices)
    for g_, (nom, rot_c, tam_c) in feats_col:
        c = g_.asPoint()
        lineas_c = nom.split("\n")
        obst.append(_caja(c.x(), c.y(), rot_c, max(_largo(s, tam_c) for s in lineas_c),
                          len(lineas_c) * tam_c * 0.42 * mm))
    for v in vias:
        obst.append(v["banda"].buffer(0.4 * mm, 4))
        obst.append(v["rotulo"].buffer(tam_cam * 0.25 * mm, 4))
    borde = QgsGeometry.fromPolylineXY(ring + [ring[0]]).buffer(0.35 * mm, 4)
    pos_v = {}
    for k, p in enumerate(ring):
        txt = nombre_punto(k + 1)
        w_v, h_v = len(txt) * tam_vert * 0.22 * mm, tam_vert * 0.40 * mm
        prev, nxt = ring[k - 1], ring[(k + 1) % n]
        d1x, d1y = prev.x() - p.x(), prev.y() - p.y()
        d2x, d2y = nxt.x() - p.x(), nxt.y() - p.y()
        l1, l2 = math.hypot(d1x, d1y) or 1, math.hypot(d2x, d2y) or 1
        bx, by = -(d1x / l1 + d2x / l2), -(d1y / l1 + d2y / l2)
        if math.hypot(bx, by) < 1e-3:  # vertice colineal: normal del lado
            bx, by = d2y / l2, -d2x / l2
        lb = math.hypot(bx, by)
        bx, by = bx / lb, by / lb
        if poly.contains(QgsGeometry.fromPointXY(QgsPointXY(p.x() + bx * 0.3, p.y() + by * 0.3))):
            bx, by = -bx, -by
        cams = [ld for ld in lados if ld["camino"] and k + 1 in (ld["desde"], ld["hasta"])]
        if len(cams) == 1:
            bx, by = _afuera(ring[cams[0]["desde"] - 1], ring[cams[0]["hasta"] - 1])[5:7]
        otros = [QgsGeometry.fromPointXY(q).buffer(1.6 * mm, 8) for j, q in enumerate(ring) if j != k]
        elegido = None
        r0 = 1.6 * mm + abs(bx) * w_v / 2 + abs(by) * h_v / 2
        # se aleja en su direccion: hasta salir de la franja del camino, o como mucho 3 mm si no da a camino
        for i in range(int(((ancho_cam / mm + 4.0) if cams else 3.0) / 0.5) + 1):
            r = r0 + i * 0.5 * mm
            cx, cy = p.x() + bx * r, p.y() + by * r
            caja = _caja(cx, cy, 0.0, w_v, h_v)
            if not caja.intersects(borde) and not caja.intersects(poly) and not any(caja.intersects(z) for z in obst + otros):
                elegido = (cx, cy, caja)
                break
        base = math.atan2(by, bx)
        for extra in (() if elegido else (0.0, 1.5, 3.0)):
            for dang in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180):
                t = base + math.radians(dang)
                ux, uy = math.cos(t), math.sin(t)
                r = (1.6 + extra) * mm + abs(ux) * w_v / 2 + abs(uy) * h_v / 2
                cx, cy = p.x() + ux * r, p.y() + uy * r
                caja = _caja(cx, cy, 0.0, w_v, h_v)
                if not caja.intersects(borde) and not caja.intersects(poly) and not any(caja.intersects(z) for z in obst + otros):
                    elegido = (cx, cy, caja)
                    break
            if elegido:
                break
        if elegido is None:
            r = 1.6 * mm + abs(bx) * w_v / 2 + abs(by) * h_v / 2
            cx, cy = p.x() + bx * r, p.y() + by * r
            elegido = (cx, cy, _caja(cx, cy, 0.0, w_v, h_v))
        obst.append(elegido[2])
        pos_v[k] = elegido
    fx, fy = vert.fields().indexOf("LX"), vert.fields().indexOf("LY")
    cambios = {}
    for ft in vert.getFeatures():
        k = int(ft["PUNTO"][1:]) - 1
        cambios[ft.id()] = {fx: pos_v[k][0], fy: pos_v[k][1]}
    vert.dataProvider().changeAttributeValues(cambios)

    lad = _escribir_shape(
        os.path.join(sdir, "%s_LADOS.shp" % pref),
        _campos(("LADO", QVariant.String), ("DIST", QVariant.Double), ("LINDERO", QVariant.String),
                ("LX", QVariant.Double), ("LY", QVariant.Double), ("ROT", QVariant.Double),
                ("TAM", QVariant.Double)),
        QgsWkbTypes.LineString, crs, feats_lados)
    lad.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
        {"color": "230,200,0", "width": "0.6"})))

    def _dd_pos_tam(s):
        _dd_posicion(s)
        p = s.dataDefinedProperties()
        p.setProperty(QgsPalLayerSettings.Size, QgsProperty.fromField("TAM"))
        s.setDataDefinedProperties(p)
    _etiquetar(lad, "format_number(\"DIST\",2) || ' m'", tam_dist, "black", LP.OverPoint, _dd_pos_tam)
    capas["lados"] = lad

    col = _escribir_shape(
        os.path.join(sdir, "%s_COLINDANTES.shp" % pref),
        _campos(("NOMBRE", QVariant.String), ("ROT", QVariant.Double), ("TAM", QVariant.Double)),
        QgsWkbTypes.Point, crs, feats_col)
    col.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "circle", "size": "0.01", "color": "0,0,0,0", "outline_style": "no"})))
    _etiquetar(col, '"NOMBRE"', 8, "#8b0000", LP.OverPoint, _dd_colindantes)
    capas["colindantes"] = col

    if feats_cam:
        cam = _escribir_shape(os.path.join(sdir, "%s_CAMINO.shp" % pref),
                              _campos(("NOMBRE", QVariant.String)), QgsWkbTypes.LineString, crs, feats_cam)
        cam.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "139,0,0", "width": "0.4"})))
        capas["camino"] = cam
    if feats_eje:
        # eje invisible del camino: solo lleva el nombre, curvo y centrado entre los dos bordes
        cej = _escribir_shape(os.path.join(sdir, "%s_CAMINO_EJE.shp" % pref),
                              _campos(("NOMBRE", QVariant.String), ("TAM", QVariant.Double)),
                              QgsWkbTypes.LineString, crs, feats_eje)
        cej.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_style": "no"})))
        _etiquetar_camino(cej)
        capas["camino_eje"] = cej

    # UBICACION: lote en rojo + rectangulo rojo
    bb = poly.boundingBox()
    half = max(bb.width(), bb.height()) / 2.0 + 150
    rect = _escribir_shape(os.path.join(sdir, "%s_RECT_UBICACION.shp" % pref),
                           _campos(("ID", QVariant.Int)), QgsWkbTypes.Polygon, crs,
                           [(QgsGeometry.fromRect(QgsRectangle(cen.x() - half, cen.y() - half * 0.75,
                                                               cen.x() + half, cen.y() + half * 0.75)), [1])])
    rect.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"style": "no", "outline_color": "255,0,0", "outline_width": "0.6"})))
    capas["rect"] = rect
    ubi = QgsVectorLayer(lote.source(), "%s_UBICACION" % pref, "ogr")
    ubi.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "255,0,0,255", "outline_color": "255,0,0", "outline_width": "0.3"})))
    capas["ubicacion"] = ubi
    return capas


# ----------------------------------------------------------------------------- cartas topograficas
EXT_RASTER = (".tif", ".tiff", ".ecw", ".jp2", ".img", ".sid")


def buscar_cartas(carpetas):
    """Lista (sin repetir nombre) los rasters encontrados en las carpetas, de forma recursiva."""
    vistos, out = set(), []
    for c in carpetas:
        if not c or not os.path.isdir(c):
            continue
        for raiz, _dirs, files in os.walk(c):
            for fn in sorted(files):
                if fn.lower().endswith(EXT_RASTER) and fn.lower() not in vistos:
                    vistos.add(fn.lower())
                    out.append(os.path.join(raiz, fn))
    return out


def carta_que_cubre(ring, crs, rutas):
    """Devuelve la ruta de la primera carta cuya extension contiene el lote (o None)."""
    poly = QgsGeometry.fromPolygonXY([ring])
    for ruta in rutas:
        r = QgsRasterLayer(ruta, os.path.basename(ruta))
        if not r.isValid():
            continue
        g = QgsGeometry(poly)
        if r.crs() != crs:
            g.transform(QgsCoordinateTransform(crs, r.crs(), QgsProject.instance()))
        if r.extent().contains(g.boundingBox()):
            return ruta
    return None


def resolver_carta(params, ring):
    """Convierte params['carta'] (None / 'AUTO' / 'LAYER:id' / ruta) en una QgsRasterLayer o None."""
    sel = params.get("carta")
    if not sel:
        return None
    project = QgsProject.instance()
    if isinstance(sel, QgsRasterLayer):
        return sel
    if sel == "AUTO":
        ruta = carta_que_cubre(ring, params["layer"].crs(), buscar_cartas(params.get("carpetas_cartas") or []))
        if ruta is None:
            return None
        sel = ruta
    if isinstance(sel, str) and sel.startswith("LAYER:"):
        return project.mapLayer(sel[6:])
    # ruta de archivo: reutiliza la capa si ya esta en el proyecto
    norm = os.path.normcase(os.path.normpath(sel))
    for lyr in project.mapLayers().values():
        if isinstance(lyr, QgsRasterLayer) and os.path.normcase(os.path.normpath(lyr.source())) == norm:
            return lyr
    lyr = QgsRasterLayer(sel, "CARTA " + os.path.splitext(os.path.basename(sel))[0])
    if not lyr.isValid():
        raise ValueError("No se pudo abrir la carta topografica: %s" % sel)
    params["_carta_nueva"] = lyr
    return lyr


def mosaico_cartas(params, carta, extent):
    """Si `carta` no cubre todo `extent` (CRS del lote), une en un VRT las cartas de las carpetas
    que tocan ese recuadro. Devuelve la capa del mosaico, o None si no hace falta."""
    crs = params["layer"].crs()
    rutas = buscar_cartas(params.get("carpetas_cartas") or [])
    if carta is not None and os.path.isfile(carta.source()):
        rutas = [carta.source()] + [r for r in rutas if os.path.normcase(r) != os.path.normcase(carta.source())]

    def _ext_en(r):
        if r.crs() == crs:
            return extent
        return QgsCoordinateTransform(crs, r.crs(), QgsProject.instance()).transformBoundingBox(extent)

    if carta is not None and carta.extent().contains(_ext_en(carta)):
        return None
    tocan, crs_mos = [], None
    for ruta in rutas:
        r = QgsRasterLayer(ruta, os.path.basename(ruta))
        if not r.isValid() or not r.extent().intersects(_ext_en(r)):
            continue
        if crs_mos is None:
            crs_mos = r.crs()
        if r.crs() == crs_mos:
            tocan.append(ruta)
    if len(tocan) < 2:
        return None
    from osgeo import gdal
    vrt = os.path.join(params["out_dir"], "%s_CARTA_UBICACION.vrt" % params["prefijo"])
    ds = gdal.BuildVRT(vrt, tocan)
    if ds is None:
        return None
    ds = None
    lyr = QgsRasterLayer(vrt, "CARTA %s" % " + ".join(os.path.splitext(os.path.basename(t))[0] for t in tocan))
    if not lyr.isValid():
        return None
    params["_carta_nueva"] = lyr
    return lyr


# ----------------------------------------------------------------------------- linderos
def texto_linderos(lados):
    """Devuelve dict orientacion -> texto del cuadro 'LINDEROS Y DIMENSIONES'."""
    out = {}
    for o in ORIENTACIONES:
        grupo = [ld for ld in lados if ld["orientacion"] == o]
        if not grupo:
            out[o] = ""
            continue
        partes = []
        k = 0
        while k < len(grupo):
            ld = grupo[k]
            j = k
            # une lados consecutivos con el mismo colindante
            while (j + 1 < len(grupo) and grupo[j + 1]["colindante"].strip().upper() == ld["colindante"].strip().upper()
                   and grupo[j + 1]["desde"] == grupo[j]["hasta"]):
                j += 1
            dists = " + ".join(fmt_dist(float(g["dist"])) for g in grupo[k:j + 1])
            partes.append("%s DEL %s AL %s CON:  %s" % (
                ld["colindante"].upper(), nombre_punto(ld["desde"]), nombre_punto(grupo[j]["hasta"]), dists))
            k = j + 1
        out[o] = " ; ".join(partes)
    return out


# ----------------------------------------------------------------------------- composicion
def _item(layout, id_):
    it = layout.itemById(id_)
    if it is None:
        raise RuntimeError("La plantilla no tiene el elemento '%s'" % id_)
    return it


def _set_text(layout, id_, text):
    it = layout.itemById(id_)
    if isinstance(it, QgsLayoutItemLabel):
        it.setText(text)


def _clonar_label(layout, src, text, dy):
    n = QgsLayoutItemLabel(layout)
    n.setTextFormat(src.textFormat())
    n.setHAlign(src.hAlign())
    n.setVAlign(src.vAlign())
    n.setMarginX(src.marginX())
    n.setMarginY(src.marginY())
    n.setFrameEnabled(src.frameEnabled())
    n.setFrameStrokeColor(src.frameStrokeColor())
    n.setFrameStrokeWidth(src.frameStrokeWidth())
    n.setBackgroundEnabled(src.hasBackground())
    n.setBackgroundColor(src.backgroundColor())
    n.setText(text)
    layout.addLayoutItem(n)
    p = src.positionWithUnits()
    n.attemptResize(src.sizeWithUnits())
    n.attemptMove(QgsLayoutPoint(p.x(), p.y() + dy, p.units()))
    n.setZValue(src.zValue())
    return n


def alinear_escala_con_grilla(layout, mapa, sb, gi, max_tramos=6):
    """Escala grafica acorde a la grilla del mapa: un tramo = intervalo de la grilla y el 0 justo bajo
    la primera linea de la grilla que no choca con el texto "ESCALA GRAFICA"; tantos tramos como
    quepan bajo el mapa, asi cada marca de la escala coincide con una coordenada de la grilla."""
    e = mapa.extent()
    if e.width() <= 0:
        return
    mx, mw = mapa.pos().x(), mapa.rect().width()
    k = mw / e.width()  # mm de papel por metro
    x_min = mapa.pos().x() + 15.0
    for it in layout.items():
        if (isinstance(it, QgsLayoutItemLabel) and "ESCALA" in it.text().upper() and "GR" in it.text().upper()
                and abs(it.pos().y() - sb.pos().y()) < 15):
            x_min = it.pos().x() + it.sizeForText().width() + 3.0
    xs = []
    g = math.ceil(e.xMinimum() / gi) * gi
    while g <= e.xMaximum():
        x = mx + (g - e.xMinimum()) * k
        if x >= x_min:
            xs.append(x)
        g += gi
    seg = gi * k
    if not xs:
        return
    x0 = xs[0]
    n = max(1, min(max_tramos, int((mx + mw - 8.0 - x0) / seg + 1e-6)))
    sb.setNumberOfSegmentsLeft(0)
    sb.setSegmentSizeMode(QgsScaleBarSettings.SegmentSizeMode.Fixed)
    sb.setUnitsPerSegment(gi)
    sb.setNumberOfSegments(n)
    sb.setUnitLabel("m")
    sb.refreshItemSize()
    # la barra empieza despues del margen de la caja + medio "0" + el grosor de linea
    ctx = QgsLayoutUtils.createRenderContextForLayout(layout, None)
    w0 = QgsTextRenderer.textWidth(ctx, sb.textFormat(), ["0"]) / ctx.convertToPainterUnits(
        1, Qgis.RenderUnit.Millimeters)
    try:
        lw = sb.lineSymbol().width()
    except AttributeError:
        lw = 0.3
    off = sb.boxContentSpace() + w0 / 2 + lw
    p = sb.positionWithUnits()
    sb.attemptMove(QgsLayoutPoint(x0 - off, p.y(), p.units()))


def construir_layout(params, ring, lados, capas, sc, carta):
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    doc = QDomDocument()
    f = QFile(TEMPLATE)
    if not f.open(QIODevice.ReadOnly | QIODevice.Text):
        raise RuntimeError("No se encontro la plantilla %s" % TEMPLATE)
    doc.setContent(f.readAll())
    f.close()
    ctx = QgsReadWriteContext()
    layout.loadFromTemplate(doc, ctx, True)

    nombre = "PLANO %s" % params["nombre_lote"].upper()
    lm = project.layoutManager()
    base, k = nombre, 2
    while lm.layoutByName(nombre):
        nombre = "%s (%d)" % (base, k)
        k += 1
    layout.setName(nombre)

    # logo
    logo = layout.itemById("logo")
    if logo is not None:
        logo.setPicturePath(LOGO)

    # textos
    for key in ("provincia", "canton", "parroquia", "sector", "propietario", "predio",
                "cod_prov", "cod_cat", "ref_carta"):
        _set_text(layout, key, params.get(key, ""))
    _set_text(layout, "fecha", params["fecha"])
    _set_text(layout, "escala_pie", "ESCALA 1:%s" % "{:,}".format(sc).replace(",", "."))
    _set_text(layout, "escala_ubic", "ESCALA 1:%s" % "{:,}".format(ESCALA_UBIC).replace(",", "."))
    _set_text(layout, "area_pie", "ÁREA: %s" % params["area_text"])
    _set_text(layout, "area_cuadro", params["area_text"])
    if params.get("tecnico"):
        _set_text(layout, "tecnico", params["tecnico"])
    lind = texto_linderos(lados)
    _set_text(layout, "lind_norte", lind["NORTE"])
    _set_text(layout, "lind_sur", lind["SUR"])
    _set_text(layout, "lind_este", lind["ESTE"])
    _set_text(layout, "lind_oeste", lind["OESTE"])

    # tabla de coordenadas
    n = len(ring)
    tpl = {c: _item(layout, "row_tpl_" + c) for c in ("PUNTO", "LADO", "DIST", "X", "Y")}
    dist_por_lado = {(ld["desde"], ld["hasta"]): float(ld["dist"]) for ld in lados}
    for k in range(n):
        i, j = k + 1, (k + 1) % n + 1
        p = ring[k]
        vals = {"PUNTO": nombre_punto(i), "LADO": nombre_lado(i, j),
                "DIST": fmt_dist(dist_por_lado.get((i, j), 0.0)),
                "X": fmt_coord(p.x()), "Y": fmt_coord(p.y())}
        for c, src in tpl.items():
            if k == 0:
                src.setText(vals[c])
                src.setId("row_%d_%s" % (i, c))
            else:
                _clonar_label(layout, src, vals[c], ROW_H * k).setId("row_%d_%s" % (i, c))
    extra = ROW_H * (n - 1)  # la plantilla trae 1 fila
    for id_ in ("tabla_datum1", "tabla_datum2", "tabla_datum3"):
        it = _item(layout, id_)
        p = it.positionWithUnits()
        it.attemptMove(QgsLayoutPoint(p.x(), p.y() + extra, p.units()))
    fr = _item(layout, "tabla_frame")
    s = fr.sizeWithUnits()
    fr.attemptResize(QgsLayoutSize(s.width(), s.height() + extra, s.units()))
    # si la tabla se sale del marco del mapa, crece hacia arriba
    mp = _item(layout, "map_principal")
    # borde real del mapa (sceneBoundingRect incluye las coordenadas de la grilla)
    sobra = fr.sceneBoundingRect().bottom() - (mp.pos().y() + mp.rect().height()) + 1.5
    if sobra > 0:
        zona = fr.sceneBoundingRect()
        for it in layout.items():
            if hasattr(it, "attemptMove") and it is not mp and zona.contains(it.sceneBoundingRect()):
                p = it.positionWithUnits()
                it.attemptMove(QgsLayoutPoint(p.x(), p.y() - sobra, p.units()))

    # mapa principal
    poly = QgsGeometry.fromPolygonXY([ring])
    bb = poly.boundingBox()
    cx, cy = bb.center().x(), bb.center().y()
    mapa = _item(layout, "map_principal")
    orden = [capas["vertices"], capas["lados"], capas["colindantes"]]
    for key in ("camino_eje", "camino"):
        if key in capas:
            orden.append(capas[key])
    orden.append(capas["lote"])
    mapa.setLayers(orden)
    mapa.setKeepLayerSet(True)
    w = MAPA_W / 1000.0 * sc
    h = MAPA_H / 1000.0 * sc
    top = cy + h * 0.40
    mapa.zoomToExtent(QgsRectangle(cx - w / 2, top - h, cx + w / 2, top))
    mapa.setScale(sc)
    gi = intervalo_grilla(sc)
    for g in mapa.grids().asList():
        g.setIntervalX(gi)
        g.setIntervalY(gi)
    mapa.setCrs(params["layer"].crs())

    # mapa de ubicacion
    ubic = _item(layout, "map_ubicacion")
    ubic.setCrs(params["layer"].crs())
    e = ubic.extent()
    ubic.zoomToExtent(QgsRectangle(cx - e.width() / 2, cy - e.height() / 2, cx + e.width() / 2, cy + e.height() / 2))
    ubic.setScale(ESCALA_UBIC)
    for g in ubic.grids().asList():
        g.setIntervalX(GRILLA_UBIC)
        g.setIntervalY(GRILLA_UBIC)
    # si el recuadro cae entre varias cartas, se unen en un mosaico para cubrirlo entero
    if carta is not None or params.get("carpetas_cartas"):
        carta = mosaico_cartas(params, carta, ubic.extent()) or carta
    base_layers = [capas["rect"], capas["ubicacion"]]
    if carta is not None:
        base_layers.append(carta)
    ubic.setLayers(base_layers)
    ubic.setKeepLayerSet(True)

    # barra de escala
    sb = layout.itemById("escala_grafica")
    if isinstance(sb, QgsLayoutItemScaleBar):
        sb.setLinkedMap(mapa)
        sb.setUnits(QgsUnitTypes.DistanceMeters)
        sb.setUnitsPerSegment(segmento_barra(sc))
        alinear_escala_con_grilla(layout, mapa, sb, gi)
        sb.update()

    layout.refresh()
    lm.addLayout(layout)
    return layout


# ----------------------------------------------------------------------------- principal
def generar_plano(params, iface=None):
    """Genera capas + composicion (+PDF). `params` viene de FormatoRuralDialog.parametros()."""
    layer = params["layer"]
    feat = params["feature"]
    geom = feat.geometry()
    if geom is None or geom.isEmpty():
        raise ValueError("La entidad no tiene geometria.")
    ring, lados_auto = lados_desde_geometria(geom)
    lados = params.get("lados") or lados_auto
    if len(lados) != len(ring):
        raise ValueError("El numero de lados (%d) no coincide con los vertices (%d)." % (len(lados), len(ring)))

    poly = QgsGeometry.fromPolygonXY([ring])
    if not params.get("area_text"):
        params["area_text"] = fmt_area(poly.area(), params.get("unidad_area", "Ha"))
    sc = int(params.get("escala") or 0) or escala_automatica(poly.boundingBox())

    out_dir = params["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    capas = crear_capas(params, ring, lados, sc, out_dir)

    carta = resolver_carta(params, ring)
    layout = construir_layout(params, ring, lados, capas, sc, carta)

    # agrega las capas al proyecto en un grupo
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    gname = params["nombre_lote"].upper()
    grp = root.findGroup(gname)
    if grp is None:
        grp = root.insertGroup(0, gname)
    for key in ("vertices", "lados", "colindantes", "camino_eje", "camino", "lote", "rect", "ubicacion"):
        if key in capas:
            project.addMapLayer(capas[key], False)
            node = grp.addLayer(capas[key])
            if key in ("rect", "ubicacion"):
                node.setItemVisibilityChecked(False)
    if params.get("_carta_nueva") is not None:
        project.addMapLayer(params["_carta_nueva"], False)
        grp.addLayer(params["_carta_nueva"]).setItemVisibilityChecked(False)
        params.pop("_carta_nueva")

    pdf = None
    if params.get("exportar_pdf", True):
        pdf = os.path.join(out_dir, params.get("pdf_nombre") or ("%s.pdf" % layout.name()))
        ex = QgsLayoutExporter(layout)
        st = QgsLayoutExporter.PdfExportSettings()
        st.dpi = 300
        r = ex.exportToPdf(pdf, st)
        if r != QgsLayoutExporter.Success:
            raise RuntimeError("No se pudo exportar el PDF (codigo %s)." % r)
    return {"layout": layout, "capas": capas, "pdf": pdf, "escala": sc, "carta": carta}
