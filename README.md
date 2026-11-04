# Formato Rural TOPCAT (plugin QGIS)

Autor: Ing. José Daniel Alvarado Orellana · TOPCATGEO S.A.S. · [LinkedIn](https://www.linkedin.com/in/jose-daniel-alvarado-orellana/)

Genera el plano **LEVANTAMIENTO PLANIMÉTRICO** con el formato rural de TOPCAT a partir de un polígono.

## Uso

1. Cargue en el proyecto la capa con el polígono del lote (y, si quiere, la carta topográfica IGM como ráster).
2. Si la capa tiene varios polígonos, **seleccione uno** con la herramienta de selección.
3. Menú **Complementos ▸ Formato Rural TOPCAT ▸ Generar plano rural...** (o el botón de la barra).
4. Pestaña **Predio**: provincia, cantón, parroquia, sector, propietario, predio, códigos, nombre del lote, referencia de carta, responsabilidad técnica y fecha. Use `\n` para salto de línea.
5. Pestaña **Linderos**: los lados se cargan solos (P01→P02, ...) en sentido horario, con la distancia calculada y la orientación (NORTE/SUR/ESTE/OESTE) sugerida. Edite la distancia si quiere que coincida con el plano original, escriba el colindante y marque **Camino** si ese lado da a un camino o calle (se dibuja con el ancho indicado).
   - **Eje del camino (opcional)**: elija una capa de líneas con el eje del camino digitalizado sobre la foto (Google Hybrid / ortofoto). Con eje, el camino sigue su curva real y su borde interior queda sobre el lindero; sin eje, el camino va paralelo al lado.
6. Pestaña **Plano y salida**: escala (automática o fija), área (texto en Ha o m²), carpeta de salida y PDF.
   - **Carta topográfica**: el combo lista las cartas (.tif) de las carpetas indicadas en *Carpetas de cartas* (la primera vez quedan vacías: agréguelas con el botón `...`), los rásters ya cargados en el proyecto y la opción **Automática**, que elige la carta que cubre el lote. Con **Otra carta (archivo externo)** puede buscar cualquier ráster fuera de esas carpetas; se carga al proyecto dentro del grupo del lote.
7. **Generar plano**. El plugin crea:
   - Carpeta `SHAPE` con los shapefiles: `<LOTE>_LOTE`, `_VERTICES`, `_LADOS`, `_COLINDANTES`, `_CAMINO` (bordes), `_CAMINO_EJE` (lleva el nombre del camino), `_RECT_UBICACION`.
   - Un grupo de capas con el nombre del lote, ya estilizado (medidas dentro del polígono, colindantes afuera, vértices P01…).
   - La composición `PLANO <LOTE>` (A3) con tabla de coordenadas UTM, cuadro de linderos y dimensiones, plano de ubicación 1:50.000 sobre la carta (lote y rectángulo en rojo; si el recuadro cae entre varias cartas se unen en un mosaico para cubrirlo entero) y rótulo.
   - El PDF a 300 dpi (si está marcado).
   - Abre la composición en el diseñador para retoques finales.

## Instalación en otra computadora

1. Copie la carpeta `formato_rural_topcat` completa (o descomprima el ZIP) en:
   `C:\Users\<USUARIO>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   (en QGIS: **Configuración ▸ Perfiles de usuario ▸ Abrir carpeta del perfil activo** ▸ `python\plugins`).
   También sirve **Complementos ▸ Administrar e instalar complementos ▸ Instalar a partir de ZIP**.
2. Reinicie QGIS y active **Formato Rural TOPCAT** en el administrador de complementos.
3. En la pestaña *Plano y salida* indique la carpeta donde están las cartas topográficas (.tif) de esa computadora. Si no hay cartas, el plano de ubicación sale sin fondo.

Requiere QGIS 3.22 o superior. No necesita librerías adicionales.

## Rotulado simétrico (reglas del plugin)

- **Medidas**: siempre centradas en su lado, pegadas a la línea por dentro (1,9 mm en papel), paralelas al lado y con la misma letra. Si un lado es muy corto, la letra se achica (mínimo 6 pt). Solo si choca con otro texto se corre a lo largo del lado o pasa a una segunda fila.
- **Colindantes**: un rótulo por tramo recto, centrado y a la misma distancia por fuera (2,8 mm). Si el nombre no cabe se parte en dos líneas; si el mismo colindante sigue en un lado largo contiguo, se escribe solo allí.
- **Vértices** (ordenados y a la misma distancia): si el vértice da a un camino, el número va perpendicular al camino, justo por fuera de su borde exterior (los de la vía norte arriba, los de la vía sur abajo); si no, va en la bisectriz exterior del ángulo. Si choca se aleja en la misma dirección (hasta 3 mm) y solo como último recurso gira alrededor del punto. Nunca queda dentro del lote ni tapa un punto.
- **Rótulo del lote**: busca el hueco más amplio; en lotes alargados va en una sola línea (`LOTE A - AREA: 0,5044 Ha`) siguiendo el eje.
- **Camino**: franja simétrica (dos bordes paralelos al ancho indicado) con el nombre **curvo y centrado entre los dos bordes**. Si el nombre cabe en el frente va centrado; si el frente es más corto, va al costado (del lado cuyo vecino no es camino, o al oeste) para no tapar las esquinas. Con **eje digitalizado** sigue la curva real del camino (suavizada) y el borde interior coincide con el lindero.
- **Cruces**: las bocas de las calles quedan abiertas (ningún borde entra en otro camino); una calle sin eje termina contra el camino que sí tiene eje; ningún camino se dibuja dentro del lote.
- **Escala gráfica**: acorde a la grilla del mapa. Cada tramo mide lo mismo que el intervalo de la grilla y el 0 queda exactamente bajo la primera línea de la grilla que no choca con el texto "ESCALA GRÁFICA"; lleva tantos tramos como quepan bajo el mapa (0 - 100 - 200 - 300 m...).

Estos valores se pueden cambiar por script con `params["off_dist_mm"]`, `params["off_col_mm"]`, `params["tam_dist"]`, `params["tam_vert"]`.

## Notas

- Los lados consecutivos con el mismo colindante se agrupan en el cuadro de linderos (`28,75m + 56,68m`). Con `params["agrupar_colindantes"] = True` también se agrupan en el mapa aunque no sean colineales.
- La plantilla está en `templates/formato_rural.qpt`; puede editarse en QGIS manteniendo los **ID** de los elementos (`provincia`, `canton`, `lind_norte`, `map_principal`, `row_tpl_*`, etc.).
- El logo está en `img/logo.svg`.
