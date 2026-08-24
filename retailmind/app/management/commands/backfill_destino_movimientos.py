# -*- coding: utf-8 -*-
"""
Backfill de ``Movimientos_Producto.sucursal_destino`` en los INGRESOS legacy
(deuda declarada en AUDITORIA_REPORTES_2026-08 §11, "Writer destino NULL").

TODAS LAS CIFRAS DE ESTE ARCHIVO VAN FECHADAS
---------------------------------------------
Prod sigue vendiendo: un número sin fecha envejece mal y despista al que
decide. Salvo que se diga otra cosa, todo lo de acá está medido el
**22-ago-2026 entre las 17:08 y las 17:20 (hora Santiago)**. El propio command
vuelve a medir en cada dry-run: si algo no calza con esta cabecera, manda el
dry-run del día, no el docstring.

CONTEXTO
--------
El writer ACTIVO ya se corrigió en Fase C (``anular_ticket_pendiente``, 21-ago:
puebla el destino desde la sucursal dueña del SKU). Lo que queda es el daño
histórico: **546.130 movimientos de INGRESO / 2.809.217 u** con
``sucursal_destino IS NULL``, fechas 2018-06-04 a 2026-08-21, 100 % en estado
COMPLETADO (medido 22-ago 17:08). Los EGRESOS con destino NULL —1.809.485 a las
17:11, y suben cada día— NO se tocan: un egreso no tiene destino que derivar, y
hay **0 egresos con ``sucursal_origen`` NULL** en toda la historia.

REGLA CANÓNICA (la del sistema, no una inventada acá)
-----------------------------------------------------
La sucursal RECEPTORA de un ingreso es SIEMPRE la dueña del SKU
(``ProductoTalla.producto.sucursal_id``): en este ERP el stock vive por sucursal
en la propia fila ``Producto``, así que un movimiento sobre un SKU sólo puede
afectar el stock de esa sucursal. ``sucursal_origen``/``sucursal_destino``
describen el traslado, no dónde quedó el saldo. Está documentada en
``views_modulo_reportes.py::_mapa_entrada`` y ya es la base de
``views_resumen_existencias._ING_PROPIA``.

EVIDENCIA 1 — qué escribieron los writers sanos (filas que YA tienen destino,
medido 22-ago 17:11; sólo ``tipo_movimiento='INGRESO'``):

    concepto                        pobladas    ==dueña
    INGRESO_INICIAL                   43.001    42.999   99,995 %
    INGRESO_MANUAL                     4.090     4.090  100,00 %
    DEVOLUCION_CLIENTE                   526       525    99,81 %
    DEVOLUCION_NC                        378       378   100,00 %
    CAMBIO_PRODUCTO_ENTRADA              304       304   100,00 %
    AJUSTE_POSITIVO                      117       117   100,00 %
    ANULACION                             52        52   100,00 %
    AJUSTE_INVENTARIO_ENTRADA             47        47   100,00 %
    ANULACION_TICKET                      14        14   100,00 %
    ANULACION_REGULARIZACION              10        10   100,00 %
    DEVOLUCION_NC_POST_RECEPCION           7         7   100,00 %
    REVERSION_CAMBIO                       4         4   100,00 %
    CORRECCION_STOCK                       2         2   100,00 %
    ------------------------------------------------------------------
    TRASPASO_ENTRADA                 162.541   144.789    89,08 %  <- EXCLUIDO
    REASIGNACION_DESTINO                   1         0     0,00 %  <- EXCLUIDO

**HASTA DÓNDE ALCANZA ESTA TABLA**: sólo cubre el 56,19 % del universo
(306.860 de 546.130 filas). Tres conceptos NO tienen ni una sola fila poblada
en toda la historia, así que su regla NO puede validarse por acá:

    TRASPASO_SUCURSAL   220.697 filas   933.487 u   40,41 % del universo
    AJUSTE_INVENTARIO    18.507 filas   194.854 u    3,39 %
    VENTA_PUBLICO            66 filas        66 u    0,01 %

(AJUSTE_INVENTARIO sí tiene primos poblados al 100 %: AJUSTE_INVENTARIO_ENTRADA
47/47 y AJUSTE_POSITIVO 117/117. VENTA_PUBLICO son 66 filas de +1 u entre el
2026-04-23 y el 2026-05-11, ruido documental.)

Para esos 239.270 (43,81 %) el respaldo es EVIDENCIA 2 (estructura) +
EVIDENCIA 3 (oráculo independiente), no la tabla de arriba.

EVIDENCIA 2 — TRASPASO_SUCURSAL: por qué SÍ entra (y no es "un traspaso de dos
piernas" como TRASPASO_ENTRADA/SALIDA). Medido 22-ago 17:09:

    concepto                  tipo      filas   c/destino   c/origen   rango
    TRASPASO_SUCURSAL         INGRESO 220.697           0    220.697   2018-06-04..2026-04-16
    TRASPASO_SUCURSAL         EGRESO  225.528           0    225.528   2018-08-18..2026-04-16
    TRASPASO_ENTRADA          INGRESO 162.541     162.541    162.541   2019-10-02..2026-08-22
    TRASPASO_SALIDA           EGRESO    9.061       9.061      9.061   2026-04-18..2026-08-22

En sus 446.225 filas ``sucursal_destino`` está poblado **cero** veces: nunca
fue un campo de ruteo acá, no hay dato ajeno que pisar. Y ``sucursal_origen``
NO es la contraparte sino la sucursal DEL MOVIMIENTO: ya coincide con la dueña
del SKU en el 80,32 % de los INGRESOS (177.262/220.697) y en el 97,59 % de los
EGRESOS (220.097/225.528). Es el traspaso legacy de UNA pierna que
``constants_kardex.CONCEPTOS_TRASPASO_LEGACY`` ya declara como tal y al que
``views_modulo_reportes._mapa_entrada`` ya le aplica la regla dueña-del-SKU.
Murió el 2026-04-16, reemplazado por el par TRASPASO_ENTRADA/SALIDA.

EVIDENCIA 3 — el oráculo lo confirma, y además mide cuánto aporta (ver más
abajo "QUÉ ARREGLA"): backfillear TODO menos TRASPASO_SUCURSAL deja el corte
2025-12-31 en 53.006 u contra un oráculo de 26.440 (+100,48 % de error);
incluyéndolo queda en 26.446 (+0,02 %). O sea TRASPASO_SUCURSAL aporta 26.560
de las 86.982 u que se corrigen en ese corte (30,5 %) y en el corte 2023-12-31
aporta 260.145 de 557.562 (46,7 %). Sin él el arreglo queda a medias: el error
residual sería +100,48 % en 2025-12-31 y +92,38 % en 2023-12-31.
Este contrafactual lo recalcula el propio dry-run, no hay que creerle a esta
cabecera.

POR QUÉ LA LISTA NEGRA ES LA QUE ES (corregido: no es "dos piernas")
---------------------------------------------------------------------
``CONCEPTOS_PROHIBIDOS`` no separa por "una pierna vs dos", separa por **si el
writer usa o no la columna**: TRASPASO_ENTRADA (162.541/162.541),
TRASPASO_SALIDA (9.061/9.061), REGULARIZACION_TRASPASO (4/4) y
REASIGNACION_DESTINO (1/1) tienen destino poblado en el **100 %** de sus filas
→ hoy tienen 0 filas con destino NULL, no hay nada que rellenar, y lo que hay
escrito no se toca.

OJO, esto CORRIGE la versión anterior de este docstring: decía que el 10,92 %
de TRASPASO_ENTRADA que no cumple la regla era "ruteo real". Es falso. De los
17.752 incumplimientos, **17.750 tienen ``sucursal_destino == sucursal_origen``**
(el destino apunta a la EMISORA) y sólo 2 apuntan a una tercera sucursal: es un
BUG del writer de TRASPASO_ENTRADA, no un destino legítimo. La exclusión sigue
siendo correcta (no hay filas NULL que tocar y no se pisa dato ajeno), pero el
motivo es el de arriba. El bug del writer queda REPORTADO, no arreglado acá.

QUÉ ARREGLA DE VERDAD
---------------------
``views_resumen_existencias`` reconstruye el corte histórico como
``stock_hoy − ingresos_post + |egresos_post|``, contando sólo los movimientos
"propios" (ingreso con ``destino == dueña``, egreso con ``origen == dueña``).
Hoy eso es ASIMÉTRICO: el 100 % de los EGRESOS post-corte tiene
``origen == dueña``, pero sólo el 70,50 % de los INGRESOS tiene
``destino == dueña`` (corte 2025-12-31). Resultado: se resta cada salida y se
ignora un tercio de las entradas → el pasado queda inflado.

Medido contra un oráculo INDEPENDIENTE (kardex puro: ``stock_hoy − Σcantidad``
de todo movimiento post-corte, válido porque cada talla pertenece a una sola
sucursal). Medido 22-ago 17:15:

    corte 2025-12-31 (70.247 tallas)      total u   error vs oráculo   tallas que cuadran
      HOY                                 113.428          +86.988            72,19 %
      TRAS BACKFILL COMPLETO               26.446               +6            99,99 %
      TRAS BACKFILL SIN TRASPASO_SUCURSAL  53.006          +26.566            86,02 %
      KARDEX PURO (oráculo)                26.440

    corte 2023-12-31 (173.962 tallas)
      HOY                                 839.189         +557.567            26,74 %
      TRAS BACKFILL COMPLETO              281.627               +5           100,00 %
      TRAS BACKFILL SIN TRASPASO_SUCURSAL 541.772         +260.150            54,49 %
      KARDEX PURO (oráculo)               281.622

El backfill no cambia de opinión, CORRIGE: deja el corte histórico a 5-6
unidades del oráculo contra +86.988 u de error hoy.

(Ejemplo de por qué se fecha todo: a las 17:40 el mismo corte 2025-12-31 ya
tenía 70.292 tallas post-corte en vez de 70.247, con los totales intactos. Prod
vende mientras se mide; el conteo del dry-run del día manda sobre esta tabla.)

QUÉ MÁS CAMBIA — el listado movimientos-sucursal (MIGRA, no suma)
------------------------------------------------------------------
``views.py`` (~:22273) filtra el kardex de una sucursal X con
``Q(origen=X, tipo=EGRESO) | Q(destino=X, tipo=INGRESO) | Q(origen=X, destino IS NULL)``.
La 3ª cláusula EXIGE ``destino IS NULL``: al poblar el destino, la fila deja de
matchear por ahí. Entonces (medido 22-ago 17:08, y el command lo recalcula en
cada dry-run):

    487.223 filas (2.625.984 u)  SIN CAMBIO   (origen == dueña: pasan de la
                                              3ª cláusula a la 2ª, misma sucursal)
     57.605 filas (  181.761 u)  MIGRAN       de la EMISORA a la dueña. NO se
                                              suman a las dos: DESAPARECEN del
                                              kardex de la emisora. 53.288 de
                                              ellas cruzan empresa.
      1.302 filas (    1.472 u)  APARECEN     hoy son invisibles en todas las
                                              sucursales (origen Y destino NULL):
                                              1.300 ANULACION_TICKET 2026 +
                                              2 AJUSTE_INVENTARIO 2021.

Neto por sucursal en ESE LISTADO (filas, no unidades de stock): EDEL +50.344;
PAO4 −9.274; NICK1 −8.544; NICK2 −8.395; PAO3 −6.661; NICK3 −5.972;
PAO2 −5.352; PAO1 −3.161; EDEL FALLADOS −861; GILD −685; PA00 −143; IMP +6.
Es la corrección buscada (esas entradas son de SKUs que nunca fueron de la
tienda), pero **borra 57.605 filas de una pantalla de producción**: hay que
avisarle a quien mire el kardex de tienda.

Radio en otros lectores, medido 22-ago 17:18: el scoping por DTE
(``Q(dte_movimientos__sucursal_destino_id=X)``, ≥10 llamadores) toca sólo 16
filas / 15 DTE / 15 pares (dte, sucursal) nuevos; por ticket, 66 filas /
63 tickets. Despreciable.

QUÉ **NO** ARREGLA (medido, contra lo que decía el backlog)
------------------------------------------------------------
El bucket "Sin asignar" de **diferencias-recepción NO se mueve**: ese reporte
agrupa por ``Productos_Recepcionados.sucursal_destino``, que es OTRA tabla, y
los 36 faltantes históricos con destino NULL (57 u, 2026-04-20 a 2026-06-23,
medido 22-ago 17:20) tienen ``movimiento_ingreso=NULL`` los 36 (no hay puente).
Además la regla de acá sería VENENO allá: en las recepciones de traspaso,
``destino == dueña`` se cumple en **0,00 %** de los 2.721 casos poblados (el
``producto_talla`` de la línea es el SKU de la EMISORA). Los 36 faltantes son
los 36 de tipo TRASPASO, y su dueña es exactamente la sucursal que DESPACHÓ.
Escribirles "destino = dueña" les pondría la sucursal equivocada. Por eso este
command NO toca ``Productos_Recepcionados``.

Tampoco arregla el writer de TRASPASO_ENTRADA (17.750 filas con
destino == origen). Eso es un fix de código aparte, no un backfill.

COSTO (medido 22-ago 17:16, ver ``_estimar_tiempo``)
-----------------------------------------------------
``_aplicar`` emite DOS sentencias por lote (re-lectura ``WHERE id IN`` +
``bulk_update`` con un CASE de N ramas), no una. Con chunk 2.000 y 274 lotes el
PISO medido fue 3,5 min (17:16), 4,5 min (17:40) y 4,8 min (17:49) del 22-ago:
la latencia contra la BD remota varía entre corridas, así que el dry-run lo
vuelve a medir cada vez y ese número manda sobre éste. Es reloj dentro de UNA
transacción atómica que mantiene bloqueadas
las 546.130 filas hasta el commit, y el UPDATE real está por encima del piso
(escribe tuplas, mantiene el índice ``(sucursal_destino, fecha)`` y genera WAL).

El chunk por defecto (2.000) es el óptimo medido: el CASE de ``bulk_update``
escala peor que lineal (piso proyectado 22-ago 17:16: 250→12,9 min, 500→7,0,
1.000→5,2, 2.000→3,5, 4.000→4,3, 8.000→7,4). Correr fuera de horario de venta.
El snapshot CSV pesa 31,9 MB (546.131 líneas, medido 22-ago 17:40).

La estimación ANTERIOR de este command proyectaba 1 RTT vacío por lote y daba
"~0,7-1,9 min": subestimaba entre 2x y 6x, y era justo el número que sostenía
la recomendación de ventana. Corregido.

MEMORIA: ``_analizar`` materializa TODO el universo en Python (546.130 dicts de
10 claves ≈ 0,6-0,8 GB de RSS durante la corrida) y con ``--aplicar`` esa lista
convive con el CSV de 32 MB y la transacción abierta. En una máquina justa de
RAM hay que trocear por fecha y aplicar por tramos, cada uno con su propio
snapshot (son independientes y reversibles uno por uno):

    python manage.py backfill_destino_movimientos --hasta 2021-12-31 --aplicar
    python manage.py backfill_destino_movimientos --desde 2022-01-01 --hasta 2024-12-31 --aplicar
    python manage.py backfill_destino_movimientos --desde 2025-01-01 --aplicar

SEGURIDAD
---------
- DRY-RUN POR DEFECTO: sin ``--aplicar`` no escribe NADA en la BD.
- Guardas duras (no configurables): sólo ``tipo_movimiento='INGRESO'``, sólo
  filas con ``sucursal_destino IS NULL``, nunca los conceptos que ya usan la
  columna como ruteo, y se SALTA (reportando) toda fila cuya
  talla/producto/sucursal no se pueda resolver (hoy: 0).
- Con ``--aplicar``: snapshot CSV ANTES de escribir + transacción atómica +
  re-lectura del destino actual de cada lote justo antes de escribirlo +
  verificación post-escritura.
- ``--revertir <csv>`` deja en NULL sólo lo que siga teniendo el valor que este
  command escribió; si cambió, se niega (``--forzar`` para pisar igual).
- El CSV NO se escribe dentro del repo: ``--snapshot-dir`` cae por defecto en
  la carpeta temporal del sistema (son ~32 MB y el .gitignore no filtra *.csv).

Uso:
    python manage.py backfill_destino_movimientos                       # dry-run completo
    python manage.py backfill_destino_movimientos --limite 5000         # piloto
    python manage.py backfill_destino_movimientos --conceptos ANULACION_TICKET
    python manage.py backfill_destino_movimientos --desde 2026-01-01
    python manage.py backfill_destino_movimientos --aplicar
    python manage.py backfill_destino_movimientos --snapshot-dir D:/respaldos --aplicar
    python manage.py backfill_destino_movimientos --revertir <ruta>/_backfill_destino_20260822_101500.csv
"""
import collections
import csv
import datetime
import os
import tempfile
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from app.models import Movimientos_Producto, Producto_Talla, Sucursal

# --------------------------------------------------------------------------- #
# Guardas duras — NO se exponen como flags a propósito.
# --------------------------------------------------------------------------- #

#: Único tipo que se toca. Un EGRESO no tiene "sucursal destino" que derivar:
#: su contraparte es ``sucursal_origen``, y en prod hay 0 egresos con origen
#: NULL (medido 22-ago), así que no hay nada que arreglar de ese lado.
TIPO_PERMITIDO = 'INGRESO'

#: Conceptos donde el writer YA USA la columna ``sucursal_destino``: la puebla
#: en el 100 % de sus filas (medido 22-ago 17:09: TRASPASO_ENTRADA
#: 162.541/162.541, TRASPASO_SALIDA 9.061/9.061, REGULARIZACION_TRASPASO 4/4,
#: REASIGNACION_DESTINO 1/1). Consecuencia: hoy tienen 0 filas con destino NULL,
#: no hay nada que rellenar, y lo escrito no se pisa. La lista queda como guarda
#: defensiva para que un import futuro no arrastre la regla dueña-del-SKU a un
#: campo que acá significa otra cosa.
#:
#: NO es "traspasos de dos piernas": TRASPASO_SUCURSAL también tiene dos piernas
#: (220.697 INGRESO + 225.528 EGRESO) y SÍ entra al backfill, porque en sus
#: 446.225 filas el destino está poblado CERO veces — nunca fue ruteo ahí.
#:
#: Y ojo: el 10,92 % de TRASPASO_ENTRADA que no cumple la regla NO es ruteo
#: legítimo. De los 17.752 incumplimientos, 17.750 tienen destino == origen (la
#: EMISORA) y 2 apuntan a una tercera sucursal: es un bug del writer, reportado
#: y no arreglado por este command.
CONCEPTOS_PROHIBIDOS = (
    'TRASPASO_ENTRADA',
    'TRASPASO_SALIDA',
    'REGULARIZACION_TRASPASO',
    'REASIGNACION_DESTINO',
)

CSV_CAMPOS = [
    'id', 'concepto', 'fecha', 'tipo_movimiento', 'ProductoTalla_id', 'cantidad',
    'origen_old', 'destino_old', 'destino_new', 'sucursal_duena',
]

#: Óptimo MEDIDO contra prod el 22-ago 17:16 (piso por lote = re-lectura +
#: proxy del CASE): 250→12,9 min · 500→7,0 · 1.000→5,2 · 2.000→3,5 · 4.000→4,3
#: · 8.000→7,4. Bajo 2.000 manda el RTT (169 ms medidos); sobre 2.000 el CASE de
#: ``bulk_update`` escala peor que lineal. No subirlo "para ir más rápido".
CHUNK_DEFAULT = 2000
CHUNK_LOOKUP = 20000  # tamaño del IN(...) al resolver tallas → dueña


def _fmt(n):
    return f'{n:,}'.replace(',', '.')


def _raiz_repo():
    """Carpeta con el ``.git`` (o None). Sirve para no dejar CSV versionables."""
    p = os.path.abspath(str(settings.BASE_DIR))
    while True:
        if os.path.isdir(os.path.join(p, '.git')):
            return p
        padre = os.path.dirname(p)
        if padre == p:
            return None
        p = padre


def _dentro_de(ruta, raiz):
    """¿`ruta` cuelga de `raiz`? Tolera discos distintos y mayúsculas.

    `os.path.commonpath` lanza ValueError en Windows cuando las rutas están en
    unidades distintas (--snapshot-dir D:/... con el repo en C:), y comparar el
    resultado con == falla en silencio si el usuario escribe la unidad en
    minúsculas. Con normcase + os.path.relpath ninguna de las dos ocurre.
    """
    try:
        rel = os.path.relpath(os.path.normcase(ruta), os.path.normcase(raiz))
    except ValueError:  # unidades distintas en Windows → no cuelga de la raíz
        return False
    return rel == os.curdir or not rel.startswith(os.pardir + os.sep) and rel != os.pardir


class Command(BaseCommand):
    help = ('Backfill de sucursal_destino en INGRESOS legacy (= sucursal dueña '
            'del SKU). Dry-run por defecto; --aplicar para escribir.')

    #: cache de `_conceptos_con_destino_poblado` (query cara, se pedía 11 veces)
    _cache_conceptos_evidencia = None

    # ------------------------------------------------------------------ #

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Escribe de verdad (sin esto es dry-run: 0 escrituras en BD)')
        parser.add_argument('--desde', default=None,
                            help='fecha mínima del movimiento (YYYY-MM-DD)')
        parser.add_argument('--hasta', default=None,
                            help='fecha máxima del movimiento (YYYY-MM-DD)')
        parser.add_argument('--conceptos', default=None,
                            help='Lista de conceptos separados por coma (default: todos '
                                 'los elegibles). Se valida contra los choices del modelo.')
        parser.add_argument('--limite', type=int, default=None,
                            help='Procesa a lo más N movimientos (los de id más bajo). '
                                 'Para pilotos: el CSV cubre sólo esos N.')
        parser.add_argument('--chunk', type=int, default=CHUNK_DEFAULT,
                            help=f'Filas por bulk_update (default {CHUNK_DEFAULT})')
        parser.add_argument('--snapshot-dir', default=None,
                            help='Carpeta del CSV de snapshot/preview. Default: '
                                 'una carpeta en el TEMP del sistema — el CSV pesa '
                                 '~32 MB y el .gitignore no filtra *.csv, así que '
                                 'NUNCA cae por defecto dentro del repo.')
        parser.add_argument('--sin-impacto', action='store_true',
                            help='Omite la sección de impacto medido (ahorra ~5s)')
        parser.add_argument('--impacto-corte', default='2025-12-31',
                            help='Fecha de corte para medir el impacto en '
                                 'resumen-existencias histórico (default 2025-12-31)')
        parser.add_argument('--revertir', default=None, metavar='SNAPSHOT.CSV',
                            help='Revierte una aplicación anterior usando su CSV de snapshot')
        parser.add_argument('--forzar', action='store_true',
                            help='Con --revertir: revierte aunque el valor haya cambiado después')

    # ------------------------------------------------------------------ #

    def handle(self, *args, **opts):
        if opts['revertir']:
            return self._revertir(opts['revertir'], opts['forzar'], opts['chunk'])

        aplicar = opts['aplicar']
        desde, hasta = self._fechas(opts)
        conceptos = self._conceptos(opts['conceptos'])
        limite = opts['limite']
        chunk = max(1, int(opts['chunk']))
        snap_dir = self._snapshot_dir(opts['snapshot_dir'])

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{'APLICANDO' if aplicar else 'DRY-RUN (0 escrituras en BD)'} — backfill de "
            f"sucursal_destino en INGRESOS legacy (= sucursal dueña del SKU)"
        ))
        self.stdout.write(f'  medido el : {timezone.localtime():%Y-%m-%d %H:%M} '
                          f'(prod sigue vivo: las cifras son de ESTE minuto)')
        self._imprimir_filtros(desde, hasta, conceptos, limite, chunk)
        self.stdout.write(f'  snapshots  : {snap_dir}')

        t0 = time.time()
        candidatos, huerfanos = self._analizar(desde, hasta, conceptos, limite)
        self.stdout.write(f'\nSelección: {_fmt(len(candidatos))} candidatos '
                          f'en {time.time() - t0:.1f}s')

        self._imprimir_resumen(candidatos, huerfanos)
        self._imprimir_impacto_listado(candidatos)
        if not opts['sin_impacto']:
            self._imprimir_impacto(opts['impacto_corte'])

        ts = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        if not aplicar:
            if candidatos:
                ruta = os.path.join(snap_dir, f'_backfill_destino_{ts}_preview.csv')
                self._escribir_csv(ruta, candidatos)
                self.stdout.write(f'\nPreview CSV (lo que ESCRIBIRÍA): {ruta} '
                                  f'({os.path.getsize(ruta) / 1024 / 1024:.1f} MB)')
            self._estimar_tiempo(candidatos, chunk)
            self.stdout.write(self.style.WARNING(
                '\nDry-run. Para ejecutar de verdad:\n'
                f'    python manage.py backfill_destino_movimientos{self._eco_filtros(opts)} --aplicar'
            ))
            return

        if not candidatos:
            self.stdout.write(self.style.SUCCESS('\nNada que aplicar: 0 candidatos.'))
            return

        # ------------------------- APLICAR ------------------------------ #
        ruta = os.path.join(snap_dir, f'_backfill_destino_{ts}.csv')
        self._escribir_csv(ruta, candidatos)  # snapshot ANTES de escribir
        self.stdout.write(f'\nSnapshot de reversión escrito: {ruta} '
                          f'({os.path.getsize(ruta) / 1024 / 1024:.1f} MB)')

        escritos, saltados_poblados, desaparecidos = self._aplicar(candidatos, chunk)

        # Verificación post-escritura sobre el MISMO universo
        restantes = self._queryset_base(desde, hasta, conceptos).count()
        marca = (self.style.SUCCESS('OK: 0 siguen con destino NULL')
                 if restantes == 0 else
                 self.style.WARNING(f'{_fmt(restantes)} siguen con destino NULL '
                                    f'(esperado si usaste --limite)'))
        self.stdout.write(f'\nVerificación post-escritura: {marca}')
        if saltados_poblados:
            self.stdout.write(self.style.WARNING(
                f'  {_fmt(saltados_poblados)} filas se SALTARON porque otro proceso '
                f'les pobló el destino durante la corrida (guarda respetada).'))
        if desaparecidos:
            self.stdout.write(self.style.WARNING(
                f'  {_fmt(desaparecidos)} filas ya no existían al escribir.'))
        self.stdout.write(self.style.SUCCESS(
            f'\nListo. {_fmt(escritos)} movimientos con sucursal_destino poblado.\n'
            f'Para revertir: python manage.py backfill_destino_movimientos --revertir {ruta}'))

    # ------------------------------------------------------------------ #
    # Parseo de argumentos
    # ------------------------------------------------------------------ #

    def _fechas(self, opts):
        try:
            desde = datetime.date.fromisoformat(opts['desde']) if opts['desde'] else None
            hasta = datetime.date.fromisoformat(opts['hasta']) if opts['hasta'] else None
        except ValueError as e:
            raise CommandError(f'Fecha inválida: {e}')
        if desde and hasta and desde > hasta:
            raise CommandError(f'--desde ({desde}) es posterior a --hasta ({hasta})')
        return desde, hasta

    def _conceptos(self, crudo):
        if not crudo:
            return None
        validos = {c[0] for c in Movimientos_Producto._meta.get_field('concepto').choices}
        pedidos = [c.strip().upper() for c in str(crudo).split(',') if c.strip()]
        if not pedidos:
            raise CommandError('--conceptos venía vacío')
        desconocidos = [c for c in pedidos if c not in validos]
        if desconocidos:
            raise CommandError(
                f"Conceptos no declarados en el modelo: {', '.join(desconocidos)}")
        prohibidos = [c for c in pedidos if c in CONCEPTOS_PROHIBIDOS]
        if prohibidos:
            raise CommandError(
                f"Conceptos donde el writer YA usa sucursal_destino ({', '.join(prohibidos)}): "
                f"lo pueblan en el 100 % de sus filas, así que no hay ninguna en NULL que "
                f"rellenar y lo escrito no se pisa. Este command no los toca.")
        return pedidos

    def _snapshot_dir(self, valor):
        """
        Resuelve la carpeta del CSV.

        El default JAMÁS puede ser el directorio actual: el snapshot pesa ~32 MB,
        el command se corre desde ``retailmind/`` y el ``.gitignore`` de la raíz
        no filtra ``*.csv`` (el snapshot del command hermano
        ``restaurar_cabeceras_boletas_nc`` terminó trackeado en git por eso).
        Default → carpeta propia en el TEMP del sistema.
        """
        if valor:
            ruta = os.path.abspath(valor)
            if not os.path.isdir(ruta):
                raise CommandError(f'--snapshot-dir no existe o no es carpeta: {ruta}')
        else:
            ruta = os.path.join(tempfile.gettempdir(), 'retailmind_backfill_destino')
            os.makedirs(ruta, exist_ok=True)

        raiz = _raiz_repo()
        if raiz and _dentro_de(ruta, raiz):
            self.stdout.write(self.style.WARNING(
                f'  OJO: {ruta} está DENTRO del repo ({raiz}) y el CSV pesa ~32 MB. '
                f'El .gitignore no filtra *.csv → se puede commitear sin querer. '
                f'Usa --snapshot-dir con una ruta fuera del árbol versionado.'))
        return ruta

    def _imprimir_filtros(self, desde, hasta, conceptos, limite, chunk):
        self.stdout.write(
            f"  universo   : tipo_movimiento={TIPO_PERMITIDO}, sucursal_destino IS NULL\n"
            f"  excluidos  : {', '.join(CONCEPTOS_PROHIBIDOS)}\n"
            f"  fechas     : {desde or '(sin tope)'} .. {hasta or '(sin tope)'}\n"
            f"  conceptos  : {', '.join(conceptos) if conceptos else '(todos los elegibles)'}\n"
            f"  límite     : {_fmt(limite) if limite else '(sin límite)'}   chunk: {_fmt(chunk)}")

    def _eco_filtros(self, opts):
        p = ''
        for flag in ('desde', 'hasta', 'conceptos', 'limite'):
            if opts.get(flag):
                p += f" --{flag} {opts[flag]}"
        if opts.get('chunk') != CHUNK_DEFAULT:
            p += f" --chunk {opts['chunk']}"
        if opts.get('snapshot_dir'):
            p += f" --snapshot-dir {opts['snapshot_dir']}"
        return p

    # ------------------------------------------------------------------ #
    # Selección y análisis (todo SELECT, cero escrituras)
    # ------------------------------------------------------------------ #

    def _queryset_base(self, desde, hasta, conceptos):
        """Universo elegible. Las guardas duras viven acá y no se pueden apagar."""
        qs = (Movimientos_Producto.objects
              .filter(sucursal_destino__isnull=True, tipo_movimiento=TIPO_PERMITIDO)
              .exclude(concepto__in=CONCEPTOS_PROHIBIDOS))
        if desde:
            qs = qs.filter(fecha__gte=desde)
        if hasta:
            qs = qs.filter(fecha__lte=hasta)
        if conceptos:
            qs = qs.filter(concepto__in=conceptos)
        return qs

    def _analizar(self, desde, hasta, conceptos, limite):
        """
        Devuelve (candidatos, huerfanos).

        Se lee SIN join (todas las columnas son locales a la tabla) y la dueña se
        resuelve aparte contra ``Producto_Talla``: así una talla borrada aparece
        como huérfana en vez de desaparecer silenciosamente en un INNER JOIN.
        ``order_by('id')`` usa la pkey y evita el sort de la ordenación default
        del modelo (-fecha, -hora) sobre medio millón de filas.
        """
        qs = (self._queryset_base(desde, hasta, conceptos)
              .order_by('id')
              .values_list('id', 'concepto', 'fecha', 'tipo_movimiento',
                           'ProductoTalla_id', 'cantidad', 'sucursal_origen_id'))
        if limite:
            qs = qs[:limite]
        filas = list(qs)

        talla_ids = sorted({f[4] for f in filas if f[4] is not None})
        duenas = {}
        for i in range(0, len(talla_ids), CHUNK_LOOKUP):
            duenas.update(dict(
                Producto_Talla.objects
                .filter(id__in=talla_ids[i:i + CHUNK_LOOKUP],
                        producto__sucursal__isnull=False)
                .values_list('id', 'producto__sucursal_id')))
        # El lookup de arriba lee `producto.sucursal_id` como columna local: si
        # esa FK apuntara a una sucursal borrada devolvería un id fantasma. Se
        # valida contra la tabla real (son una docena de sucursales) para que
        # esa fila caiga en "huérfanos" en vez de escribirse.
        sucursales_reales = set(
            Sucursal.objects.filter(id__in=set(duenas.values()))
            .values_list('id', flat=True))
        duenas = {t: s for t, s in duenas.items() if s in sucursales_reales}

        candidatos, huerfanos = [], []
        for mid, concepto, fecha, tipo, pt_id, cantidad, origen in filas:
            duena = duenas.get(pt_id) if pt_id is not None else None
            fila = {
                'id': mid, 'concepto': concepto, 'fecha': fecha,
                'tipo_movimiento': tipo, 'ProductoTalla_id': pt_id,
                'cantidad': cantidad or 0, 'origen_old': origen,
                'destino_old': None, 'destino_new': duena, 'sucursal_duena': duena,
            }
            if duena is None:
                huerfanos.append(fila)
            else:
                candidatos.append(fila)
        return candidatos, huerfanos

    # ------------------------------------------------------------------ #
    # Salida: resumen e impacto
    # ------------------------------------------------------------------ #

    def _imprimir_resumen(self, candidatos, huerfanos):
        alias = {s.id: (s.alias or f'Sucursal {s.id}') for s in Sucursal.objects.all()}

        por_concepto = collections.defaultdict(lambda: {'n': 0, 'u': 0})
        por_anio = collections.defaultdict(lambda: {'n': 0, 'u': 0})
        por_destino = collections.defaultdict(lambda: {'n': 0, 'u': 0})
        por_tipo = collections.Counter()
        origen_null = origen_igual = origen_distinto = cant_cero = 0
        for c in candidatos:
            por_concepto[c['concepto']]['n'] += 1
            por_concepto[c['concepto']]['u'] += c['cantidad']
            por_anio[c['fecha'].year]['n'] += 1
            por_anio[c['fecha'].year]['u'] += c['cantidad']
            por_destino[c['destino_new']]['n'] += 1
            por_destino[c['destino_new']]['u'] += c['cantidad']
            por_tipo[c['tipo_movimiento']] += 1
            if c['origen_old'] is None:
                origen_null += 1
            elif c['origen_old'] == c['destino_new']:
                origen_igual += 1
            else:
                origen_distinto += 1
            if c['cantidad'] == 0:
                cant_cero += 1

        tn = sum(v['n'] for v in por_concepto.values())
        tu = sum(v['u'] for v in por_concepto.values())

        self.stdout.write('\n=== CANDIDATOS por CONCEPTO ===')
        for k, v in sorted(por_concepto.items(), key=lambda x: -x[1]['n']):
            self.stdout.write(f"  {k:<28} n={_fmt(v['n']):>9}  u={_fmt(v['u']):>11}")
        self.stdout.write(f"  {'TOTAL':<28} n={_fmt(tn):>9}  u={_fmt(tu):>11}")

        self.stdout.write('\n=== CANDIDATOS por AÑO ===')
        for k in sorted(por_anio):
            v = por_anio[k]
            self.stdout.write(f"  {k}  n={_fmt(v['n']):>9}  u={_fmt(v['u']):>11}")

        self.stdout.write('\n=== DESTINO que se escribiría (= sucursal dueña del SKU) ===')
        for k, v in sorted(por_destino.items(), key=lambda x: -x[1]['n']):
            self.stdout.write(f"  suc {str(k):<4} {alias.get(k, '?')[:20]:<22} "
                              f"n={_fmt(v['n']):>9}  u={_fmt(v['u']):>11}")

        self.stdout.write('\n=== CONTROL (por qué la regla es segura acá) ===')
        self.stdout.write(f"  tipo_movimiento presente : "
                          f"{', '.join(f'{k}={_fmt(v)}' for k, v in por_tipo.items())} "
                          f"(sólo se toca {TIPO_PERMITIDO})")
        self.stdout.write(f"  origen NULL              : {_fmt(origen_null):>9} "
                          f"(hoy INVISIBLES en movimientos-sucursal: sin origen ni destino)")
        self.stdout.write(f"  origen == dueña          : {_fmt(origen_igual):>9} "
                          f"(el destino sólo confirma lo que ya se veía)")
        self.stdout.write(f"  origen != dueña          : {_fmt(origen_distinto):>9} "
                          f"(hoy se atribuyen a la sucursal EMISORA; MIGRAN a la dueña)")
        self.stdout.write(f"  cantidad == 0            : {_fmt(cant_cero):>9} "
                          f"(filas documentales; no mueven ningún Sum)")

        con_evidencia = self._conceptos_con_destino_poblado()
        sin_evidencia = [(k, v) for k, v in por_concepto.items()
                         if k not in con_evidencia]
        if sin_evidencia:
            n_se = sum(v['n'] for _, v in sin_evidencia)
            self.stdout.write(self.style.WARNING(
                f"\n=== ALCANCE DE LA EVIDENCIA POR CONCEPTO ===\n"
                f"  {_fmt(n_se)} de {_fmt(tn)} candidatos ({100 * n_se / (tn or 1):.2f} %) son "
                f"de conceptos SIN ninguna fila con destino poblado en toda la\n"
                f"  historia: la regla NO se puede validar ahí contra lo que ya escribieron "
                f"los writers. Su respaldo es estructural + el oráculo de más abajo."))
            for k, v in sorted(sin_evidencia, key=lambda x: -x[1]['n']):
                self.stdout.write(f"    {k:<28} n={_fmt(v['n']):>9}  u={_fmt(v['u']):>11}")

        if huerfanos:
            self.stdout.write(self.style.ERROR(
                f'\n=== SALTADOS: {_fmt(len(huerfanos))} sin talla/producto/sucursal '
                f'resoluble (NO se tocan) ==='))
            for h in huerfanos[:15]:
                self.stdout.write(f"    mov={h['id']} concepto={h['concepto']} "
                                  f"fecha={h['fecha']} ProductoTalla_id={h['ProductoTalla_id']}")
            if len(huerfanos) > 15:
                self.stdout.write(f'    ... y {_fmt(len(huerfanos) - 15)} más')
        else:
            self.stdout.write('\n  Huérfanos (talla/producto/sucursal irresoluble): 0')

    def _conceptos_con_destino_poblado(self):
        """Conceptos que hoy tienen >=1 INGRESO con destino poblado (evidencia).

        Cacheado y con ``order_by()`` explícito: el ordering default del modelo
        (-fecha, -hora) se cuela en el DISTINCT y Django emite
        ``SELECT DISTINCT concepto, fecha, hora`` → 70.230 filas en vez de 16
        (5,9 s por llamada). Antes se invocaba dentro de la condición de un
        list-comprehension, o sea una vez por concepto candidato: ~64 s de
        reloj regalados en cada dry-run.
        """
        if self._cache_conceptos_evidencia is None:
            self._cache_conceptos_evidencia = set(
                Movimientos_Producto.objects
                .filter(sucursal_destino__isnull=False, tipo_movimiento=TIPO_PERMITIDO)
                .order_by()
                .values_list('concepto', flat=True).distinct()
            )
        return self._cache_conceptos_evidencia

    def _imprimir_impacto_listado(self, candidatos):
        """
        Impacto en el LISTADO movimientos-sucursal (``views.py`` ~:22273).

        Ese listado arma el kardex de la sucursal X con
        ``Q(origen=X, EGRESO) | Q(destino=X, INGRESO) | Q(origen=X, destino IS NULL)``.
        La 3ª cláusula EXIGE destino NULL, así que al poblarlo la fila deja de
        matchear por ahí. Las filas con ``origen != dueña`` NO se suman a las dos
        sucursales: MIGRAN — desaparecen del kardex de la emisora. Es la
        corrección buscada, pero borra filas de una pantalla de producción, así
        que se mide y se muestra fila a fila en vez de describirse a mano.
        """
        if not candidatos:
            return
        sucursales = {s.id: s for s in Sucursal.objects.select_related('empresa')}
        alias = {i: (s.alias or f'Sucursal {i}') for i, s in sucursales.items()}

        migran = aparecen = igual = cruzan_empresa = 0
        u_migran = u_aparecen = u_igual = 0
        pierde = collections.Counter()
        gana = collections.Counter()
        for c in candidatos:
            o, d, u = c['origen_old'], c['destino_new'], c['cantidad']
            if o is None:
                aparecen += 1
                u_aparecen += u
                gana[d] += 1
            elif o == d:
                igual += 1
                u_igual += u
            else:
                migran += 1
                u_migran += u
                pierde[o] += 1
                gana[d] += 1
                so, sd = sucursales.get(o), sucursales.get(d)
                if so and sd and so.empresa_id != sd.empresa_id:
                    cruzan_empresa += 1

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== IMPACTO MEDIDO — listado movimientos-sucursal (MIGRA, no suma) ==='))
        self.stdout.write(
            f"  {_fmt(igual):>9} filas ({_fmt(u_igual)} u)  SIN CAMBIO  origen == dueña: "
            f"pasan de la 3ª cláusula a la 2ª, misma sucursal\n"
            f"  {_fmt(migran):>9} filas ({_fmt(u_migran)} u)  MIGRAN      salen del kardex de "
            f"la EMISORA y entran al de la dueña ({_fmt(cruzan_empresa)} cruzan empresa)\n"
            f"  {_fmt(aparecen):>9} filas ({_fmt(u_aparecen)} u)  APARECEN    hoy invisibles en "
            f"toda sucursal (origen Y destino NULL)")
        if migran or aparecen:
            self.stdout.write(f"\n  {'sucursal':<20}{'pierde filas':>14}{'gana filas':>12}{'neto':>10}")
            for sid in sorted(set(pierde) | set(gana),
                              key=lambda x: gana[x] - pierde[x]):
                neto = gana[sid] - pierde[sid]
                self.stdout.write(f"  {alias.get(sid, '?')[:18]:<20}"
                                  f"{_fmt(pierde[sid]):>14}{_fmt(gana[sid]):>12}"
                                  f"{('+' if neto >= 0 else '-') + _fmt(abs(neto)):>10}")
            self.stdout.write(self.style.WARNING(
                f'  Son filas de PANTALLA, no unidades de stock. Avisar a quien mire el '
                f'kardex de tienda: {_fmt(migran)} filas dejan de aparecer donde aparecen hoy.'))

    def _imprimir_impacto(self, corte_str):
        """
        Impacto MEDIDO, no estimado. Tres cosas:

        1) resumen-existencias histórico (``_ING_PROPIA``): se compara el corte
           de hoy contra el que quedaría, y ambos contra un oráculo
           independiente (kardex puro).
        2) un TERCER escenario, "backfill sin TRASPASO_SUCURSAL": es la única
           forma de validar ese concepto, que se lleva el 40 % del universo y no
           tiene NI UNA fila con destino poblado en la historia contra la cual
           contrastar la regla. Si al sacarlo el corte se aleja del oráculo,
           entonces su inclusión está justificada por el oráculo y no por fe.
        3) diferencias-recepción: se deja explícito que NO se mueve, porque lee
           otra tabla.
        """
        try:
            corte = datetime.date.fromisoformat(corte_str)
        except ValueError as e:
            raise CommandError(f'--impacto-corte inválido: {e}')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== IMPACTO MEDIDO — resumen-existencias histórico, corte {corte} ==='))

        duena = F('ProductoTalla__producto__sucursal_id')
        ing_q = Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA')
        egr_q = Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA')
        ing_hoy = ing_q & Q(sucursal_destino_id=duena)
        # Tras el backfill, todo INGRESO hoy NULL pasa a tener destino == dueña.
        ing_fut = ing_q & (Q(sucursal_destino_id=duena) | Q(sucursal_destino__isnull=True))
        # Contrafactual: el mismo backfill dejando TRASPASO_SUCURSAL sin tocar.
        ing_sts = ing_q & (Q(sucursal_destino_id=duena)
                           | (Q(sucursal_destino__isnull=True)
                              & ~Q(concepto='TRASPASO_SUCURSAL')))
        egr = egr_q & Q(sucursal_origen_id=duena)

        t0 = time.time()
        # OJO: el GROUP BY tiene que ser por TALLA. El clamp `stock_hist <= 0` de
        # `_acumulados_historicos` se aplica talla a talla; agrupar por
        # (stock, sucursal) juntaría tallas distintas y el clamp mentiría.
        filas = list(Movimientos_Producto.objects
                     .filter(fecha__gt=corte, estado='COMPLETADO')
                     .values('ProductoTalla_id', 'ProductoTalla__stock',
                             'ProductoTalla__producto__sucursal_id')
                     .annotate(ing_hoy=Sum('cantidad', filter=ing_hoy),
                               ing_fut=Sum('cantidad', filter=ing_fut),
                               ing_sts=Sum('cantidad', filter=ing_sts),
                               egr=Sum('cantidad', filter=egr),
                               todo=Sum('cantidad')))
        alias = {s.id: (s.alias or f'Sucursal {s.id}') for s in Sucursal.objects.all()}
        hoy_s, fut_s, ora_s = (collections.Counter() for _ in range(3))
        total_sts = 0
        cuadran_fut = cuadran_hoy = cuadran_sts = 0
        for r in filas:
            sid = r['ProductoTalla__producto__sucursal_id']
            stock = r['ProductoTalla__stock'] or 0
            sal = abs(int(r['egr'] or 0))
            v_hoy = stock - int(r['ing_hoy'] or 0) + sal
            v_fut = stock - int(r['ing_fut'] or 0) + sal
            v_sts = stock - int(r['ing_sts'] or 0) + sal
            v_ora = stock - int(r['todo'] or 0)       # kardex puro
            if v_hoy > 0:
                hoy_s[sid] += v_hoy
            if v_fut > 0:
                fut_s[sid] += v_fut
            if v_ora > 0:
                ora_s[sid] += v_ora
            if v_sts > 0:
                total_sts += v_sts
            cuadran_fut += (v_fut == v_ora)
            cuadran_hoy += (v_hoy == v_ora)
            cuadran_sts += (v_sts == v_ora)

        n = len(filas) or 1
        self.stdout.write(f"  (tallas con movimientos post-corte: {_fmt(len(filas))}, "
                          f"{time.time() - t0:.1f}s)")
        self.stdout.write(f"  {'sucursal':<20}{'hoy':>12}{'tras backfill':>16}"
                          f"{'kardex puro':>14}{'delta':>12}")
        for sid in sorted(set(hoy_s) | set(fut_s), key=lambda x: -hoy_s.get(x, 0)):
            a, b, o = hoy_s.get(sid, 0), fut_s.get(sid, 0), ora_s.get(sid, 0)
            self.stdout.write(f"  {alias.get(sid, '?')[:18]:<20}{_fmt(a):>12}"
                              f"{_fmt(b):>16}{_fmt(o):>14}{_fmt(b - a):>12}")
        ta, tb, to = sum(hoy_s.values()), sum(fut_s.values()), sum(ora_s.values())
        self.stdout.write(f"  {'TOTAL':<20}{_fmt(ta):>12}{_fmt(tb):>16}"
                          f"{_fmt(to):>14}{_fmt(tb - ta):>12}")
        self.stdout.write(
            f"  error vs oráculo: HOY {_fmt(ta - to)} u "
            f"({100 * (ta - to) / to if to else 0:+.2f}%)  →  "
            f"TRAS BACKFILL {_fmt(tb - to)} u "
            f"({100 * (tb - to) / to if to else 0:+.2f}%)")
        self.stdout.write(
            f"  tallas que cuadran con el oráculo: hoy {100 * cuadran_hoy / n:.2f}% "
            f"→ tras backfill {100 * cuadran_fut / n:.2f}%")

        # --- ¿y si TRASPASO_SUCURSAL se dejara fuera? ------------------- #
        self.stdout.write(
            f"\n  Contrafactual TRASPASO_SUCURSAL (40 % del universo, 0 filas con destino "
            f"poblado en la historia:\n  su regla NO se puede validar contra los writers, "
            f"así que se valida contra el oráculo):\n"
            f"    {'escenario':<34}{'total u':>12}{'err vs oráculo':>18}{'tallas cuadran':>17}\n"
            f"    {'HOY':<34}{_fmt(ta):>12}{_fmt(ta - to):>18}"
            f"{100 * cuadran_hoy / n:>16.2f}%\n"
            f"    {'BACKFILL COMPLETO':<34}{_fmt(tb):>12}{_fmt(tb - to):>18}"
            f"{100 * cuadran_fut / n:>16.2f}%\n"
            f"    {'BACKFILL SIN TRASPASO_SUCURSAL':<34}{_fmt(total_sts):>12}"
            f"{_fmt(total_sts - to):>18}{100 * cuadran_sts / n:>16.2f}%\n"
            f"    {'KARDEX PURO (oráculo)':<34}{_fmt(to):>12}")

        # --- lo que NO se mueve --------------------------------------- #
        from app.models import Productos_Recepcionados
        fal = Productos_Recepcionados.objects.filter(
            sucursal_destino__isnull=True, dte__isnull=False, cantidad_faltante__gt=0)
        n_fal = fal.count()
        u_fal = fal.aggregate(u=Sum('cantidad_faltante'))['u'] or 0
        con_puente = fal.filter(movimiento_ingreso__isnull=False).count()
        self.stdout.write(self.style.WARNING(
            f"\n  diferencias-recepción: NO se mueve. Sus {n_fal} faltantes 'Sin asignar' "
            f"({u_fal} u) viven en Productos_Recepcionados.sucursal_destino (otra tabla) "
            f"y {con_puente} de ellos tienen movimiento_ingreso vinculado."))

    def _estimar_tiempo(self, candidatos, chunk):
        """
        Piso de tiempo de ``--aplicar``, medido — no proyectado desde un RTT.

        ``_aplicar`` emite DOS sentencias por lote, no una:
          1) la re-lectura ``SELECT id, sucursal_destino_id WHERE id IN (chunk)``
          2) el ``bulk_update``: ``UPDATE ... SET x = CASE WHEN id=.. THEN ..
             (chunk ramas) END WHERE id IN (chunk)``

        La (1) se cronometra de verdad acá (es un SELECT, no escribe nada). La
        (2) no se puede cronometrar sin escribir, así que se acota por abajo con
        un SELECT que evalúa EL MISMO CASE de ``chunk`` ramas sobre las MISMAS
        filas: el UPDATE real cuesta eso **más** escribir tuplas, mantener el
        índice ``(sucursal_destino, fecha)`` y generar WAL. Lo que sale es un
        PISO, no una estimación centrada.

        (La versión anterior proyectaba 1 RTT vacío por lote y daba ~0,7–1,9 min
        para el universo completo; el piso medido el 22-ago 17:16 era 3,5–4 min.)
        """
        n = len(candidatos)
        if not n:
            return
        lotes = (n + chunk - 1) // chunk
        ids = [c['id'] for c in candidatos[:chunk]]

        t0 = time.time()
        Movimientos_Producto.objects.filter(id=0).exists()
        rtt = time.time() - t0

        t0 = time.time()
        dict(Movimientos_Producto.objects.filter(id__in=ids)
             .values_list('id', 'sucursal_destino_id'))
        t_lectura = time.time() - t0

        t_case = self._cronometrar_case(ids)

        piso = lotes * (t_lectura + t_case)
        self.stdout.write(
            f"\n  Costo de --aplicar (PISO MEDIDO, {timezone.localtime():%Y-%m-%d %H:%M}):\n"
            f"    {_fmt(lotes)} lotes de {_fmt(chunk)} × 2 sentencias por lote\n"
            f"    RTT vacío                  {rtt * 1000:>7.0f} ms   (lo único que medía la "
            f"versión anterior)\n"
            f"    1) re-lectura del lote     {t_lectura * 1000:>7.0f} ms   medida\n"
            f"    2) UPDATE con CASE         {t_case * 1000:>7.0f} ms   PISO (proxy SELECT del "
            f"mismo CASE; el UPDATE además escribe)\n"
            f"    → piso total: {piso / 60:.1f} min de reloj, y el real queda por encima.")
        if chunk > CHUNK_DEFAULT:
            self.stdout.write(self.style.WARNING(
                f"    OJO: el CASE de bulk_update escala PEOR que lineal. Con chunk "
                f"{_fmt(chunk)} el lote cuesta {(t_lectura + t_case) * 1000:.0f} ms; el óptimo "
                f"medido en prod es {_fmt(CHUNK_DEFAULT)}."))
        self.stdout.write(self.style.WARNING(
            f"  Esa transacción mantiene bloqueadas las {_fmt(n)} filas hasta el commit. "
            f"Correr fuera de horario de venta."))

    def _cronometrar_case(self, ids):
        """
        Piso del UPDATE: mismo CASE de ``len(ids)`` ramas, pero dentro de un
        SELECT (read-only). Si el backend no soporta el SQL, cae al costo de la
        re-lectura, que es igualmente un piso válido (el UPDATE toca las mismas
        filas por la misma pkey).
        """
        if not ids:
            return 0.0
        casos = ' '.join(f'WHEN id={int(i)} THEN 1' for i in ids)
        inlist = ','.join(str(int(i)) for i in ids)
        tabla = Movimientos_Producto._meta.db_table
        sql = f'SELECT count(CASE {casos} END) FROM {tabla} WHERE id IN ({inlist})'
        try:
            t0 = time.time()
            with connection.cursor() as cur:
                cur.execute(sql)
                cur.fetchall()
            return time.time() - t0
        except Exception:                                    # pragma: no cover
            t0 = time.time()
            Movimientos_Producto.objects.filter(id__in=ids).count()
            return time.time() - t0

    # ------------------------------------------------------------------ #
    # Escritura
    # ------------------------------------------------------------------ #

    def _aplicar(self, candidatos, chunk):
        escritos = saltados = desaparecidos = 0
        t0 = time.time()
        lotes = (len(candidatos) + chunk - 1) // chunk
        with transaction.atomic():
            for i in range(0, len(candidatos), chunk):
                lote = candidatos[i:i + chunk]
                ids = [c['id'] for c in lote]
                # Guarda atómica-en-la-práctica: se re-lee el destino ACTUAL
                # justo antes de escribir. Si otro proceso lo pobló, se salta
                # (jamás se pisa un destino existente).
                vivos = dict(Movimientos_Producto.objects
                             .filter(id__in=ids)
                             .values_list('id', 'sucursal_destino_id'))
                objs = []
                for c in lote:
                    if c['id'] not in vivos:
                        desaparecidos += 1
                        continue
                    if vivos[c['id']] is not None:
                        saltados += 1
                        continue
                    objs.append(Movimientos_Producto(
                        id=c['id'], sucursal_destino_id=c['destino_new']))
                if objs:
                    # bulk_update NO llama a save(): no re-deriva tipo_movimiento
                    # ni pisa updated_at (auto_now). Es justo lo que queremos:
                    # el único campo que cambia es sucursal_destino_id.
                    Movimientos_Producto.objects.bulk_update(
                        objs, ['sucursal_destino'], batch_size=chunk)
                    escritos += len(objs)
                nlote = i // chunk + 1
                if nlote % 25 == 0 or nlote == lotes:
                    self.stdout.write(
                        f'    lote {nlote}/{lotes} — {_fmt(escritos)} escritos '
                        f'({time.time() - t0:.0f}s)')
        self.stdout.write(f'  Escritura terminada en {time.time() - t0:.0f}s')
        return escritos, saltados, desaparecidos

    def _escribir_csv(self, ruta, candidatos):
        with open(ruta, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=CSV_CAMPOS)
            w.writeheader()
            for c in candidatos:
                w.writerow({
                    'id': c['id'],
                    'concepto': c['concepto'],
                    'fecha': c['fecha'].isoformat() if c['fecha'] else '',
                    'tipo_movimiento': c['tipo_movimiento'],
                    'ProductoTalla_id': c['ProductoTalla_id'],
                    'cantidad': c['cantidad'],
                    'origen_old': '' if c['origen_old'] is None else c['origen_old'],
                    'destino_old': '',           # siempre NULL: es la premisa del backfill
                    'destino_new': c['destino_new'],
                    'sucursal_duena': c['sucursal_duena'],
                })

    # ------------------------------------------------------------------ #
    # Reversión
    # ------------------------------------------------------------------ #

    def _revertir(self, ruta, forzar, chunk):
        if not os.path.exists(ruta):
            raise CommandError(f'No existe el snapshot {ruta}')
        if ruta.endswith('_preview.csv'):
            raise CommandError('Ese CSV es un PREVIEW de dry-run, no un snapshot aplicado.')
        with open(ruta, encoding='utf-8', newline='') as f:
            filas = list(csv.DictReader(f))
        if not filas:
            raise CommandError(f'Snapshot vacío: {ruta}')
        faltan = [c for c in ('id', 'destino_new', 'destino_old') if c not in filas[0]]
        if faltan:
            raise CommandError(f'El CSV no tiene las columnas {faltan}: no es un '
                               f'snapshot de backfill_destino_movimientos.')

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Revirtiendo backfill de {_fmt(len(filas))} movimientos ({ruta})'))

        chunk = max(1, int(chunk))
        revertidos = 0
        discrepancias, desaparecidos = [], 0
        t0 = time.time()
        with transaction.atomic():
            for i in range(0, len(filas), chunk):
                lote = filas[i:i + chunk]
                # id -> (destino_new esperado, destino_old a restaurar).
                # destino_old del backfill es SIEMPRE NULL (esa es su premisa),
                # pero se lee de la columna igual: si un snapshot futuro trajera
                # un valor previo distinto, la reversión lo respeta.
                esperado = {}
                for r in lote:
                    try:
                        mid = int(r['id'])
                        dnew = int(r['destino_new'])
                    except (TypeError, ValueError):
                        discrepancias.append(f"fila con id/destino_new ilegible: {r.get('id')}")
                        continue
                    crudo = (r.get('destino_old') or '').strip()
                    try:
                        dold = int(crudo) if crudo else None
                    except ValueError:
                        discrepancias.append(f'mov={mid}: destino_old ilegible ({crudo!r})')
                        continue
                    esperado[mid] = (dnew, dold)
                vivos = dict(Movimientos_Producto.objects
                             .filter(id__in=list(esperado))
                             .values_list('id', 'sucursal_destino_id'))
                objs = []
                for mid, (dnew, dold) in esperado.items():
                    if mid not in vivos:
                        desaparecidos += 1
                        continue
                    if vivos[mid] != dnew:
                        discrepancias.append(
                            f'mov={mid}: destino actual {vivos[mid]} != aplicado {dnew}')
                        if not forzar:
                            continue
                    objs.append(Movimientos_Producto(id=mid, sucursal_destino_id=dold))
                if objs:
                    Movimientos_Producto.objects.bulk_update(
                        objs, ['sucursal_destino'], batch_size=chunk)
                    revertidos += len(objs)

            if discrepancias and not forzar:
                detalle = '; '.join(discrepancias[:5])
                raise CommandError(
                    f'{_fmt(len(discrepancias))} movimientos cambiaron después de la '
                    f'aplicación (ej: {detalle}). NADA se revirtió (transacción abortada). '
                    f'Usa --forzar para revertirlos igual.')

        if discrepancias:
            self.stdout.write(self.style.WARNING(
                f'--forzar: {_fmt(len(discrepancias))} filas habían cambiado y se '
                f'revirtieron igual (ej: {discrepancias[0]})'))
        if desaparecidos:
            self.stdout.write(self.style.WARNING(
                f'  {_fmt(desaparecidos)} movimientos ya no existen.'))
        self.stdout.write(self.style.SUCCESS(
            f'Revertido: {_fmt(revertidos)} movimientos vuelven a sucursal_destino NULL '
            f'({time.time() - t0:.0f}s).'))
