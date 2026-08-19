"""
Recupera el N° de factura de compra que la migración desde Laravel dejó afuera.

`migrate_from_laravel.migrate_movimientos()` SELECT-ea `N_documento` de la
tabla `movimiento_productos` de MySQL, pero nunca lo escribe. El kardex sabe
QUÉ entró, cuándo y a qué costo — pero no con qué factura, que es justo lo que
el proveedor exige para cursar una garantía.

QUÉ TOCA ESTE COMANDO
---------------------
Escribe en UNA tabla nueva y solo suya: `app_documentocompralegacy`.

NO toca `Movimientos_Producto` (ni su FK `dte` ni `observaciones`) y NO toca
`Dte`. Eso es deliberado: enlazar el movimiento al DTE cambiaría el kardex, el
costeo FIFO y los reportes de compras; escribir en `observaciones` cambiaría la
columna "referencia" de la tarjeta de movimientos. Todo eso hoy cuadra y no se
toca.

El único consumidor de la tabla es el buscador de facturas del módulo de
Requerimientos ("Completar datos" → "Proveedor y respaldo de compra"), que la
usa para mostrar el N° que hay que mandarle al proveedor.

Es descartable: `DELETE FROM app_documentocompralegacy` deja todo como estaba.

CORRE EN SECO POR DEFECTO. Sin `--aplicar` no escribe nada.

    python manage.py backfill_documento_movimientos_laravel               # simulación
    python manage.py backfill_documento_movimientos_laravel --limite 5000 # muestra
    python manage.py backfill_documento_movimientos_laravel --aplicar     # escribe
    python manage.py backfill_documento_movimientos_laravel --revertir    # borra la tabla

Requiere MYSQL_HOST / MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD y el paquete
mysql-connector-python (requirements-dev.txt).
"""

import os
import re
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Movimientos_Producto, Dte, DocumentoCompraLegacy

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

# Mismos conceptos que usa el buscador de requerimientos: ingresos por compra.
# Las VENTAS quedan fuera a propósito — en Laravel el N_documento de una venta
# es el folio de la boleta, y cruzarlo contra facturas de compra ataría cosas
# que no tienen nada que ver.
CONCEPTOS_INGRESO_COMPRA = (
    'RECEPCION_COMPRA', 'INGRESO_INICIAL', 'INGRESO_MANUAL', 'REPOSICION_STOCK',
)

TANDA = 20_000
LOTE_ESCRITURA = 2_000

# Solo estos documentos pueden ser "la factura con la que se compró". Sin este
# filtro el match por número traía notas de crédito, guías y cotizaciones.
TIPOS_DOCUMENTO_COMPRA = ('FACTURA ELECTRONICA', 'FACTURA EXENTA', 'FACTURA')

# `numero_documento` es un IntegerField: un folio legacy más largo que int4
# reventaba la consulta.
MAX_INT4 = 2_147_483_647


def _folio(texto):
    """Folio numérico de un N_documento legacy, o None si no es utilizable.

    El texto legacy es libre ("F-1234", "1234/A", "VARIOS"). Antes se
    concatenaban TODOS los dígitos, así que "12-34" terminaba buscando el
    folio 1234, que puede existir y ser de otro proveedor.
    """
    if not texto:
        return None
    limpio = texto.strip()
    # Se acepta un número entero, opcionalmente con un prefijo de letras o
    # símbolos separado ("F-1234"). Cualquier otra forma es ambigua.
    digitos = ''.join(c for c in limpio if c.isdigit())
    if not digitos or len(digitos) > 10:
        return None
    # Si el texto tiene dígitos en más de un bloque ("12-34", "1/2"), no se
    # puede afirmar cuál es el folio.
    bloques = [b for b in re.split(r'\D+', limpio) if b]
    if len(bloques) != 1:
        return None
    numero = int(bloques[0])
    return numero if 0 < numero <= MAX_INT4 else None


def _corrobora(dte, marca_legacy, datos_movimiento):
    """¿Hay algo, además del número, que respalde atribuir este documento?

    Basta con una de dos señales:
      · la marca que traía el movimiento legacy se parece al nombre del emisor
        (en este catálogo la marca ES el proveedor casi siempre), o
      · el documento fue recibido por la misma empresa a la que pertenece la
        sucursal donde entró la mercadería.
    """
    if marca_legacy and dte.emisor_id and dte.emisor.nombre:
        marca = str(marca_legacy).strip().upper()
        nombre = dte.emisor.nombre.upper()
        if len(marca) >= 3 and (marca in nombre or nombre.startswith(marca[:6])):
            return True

    empresa_movimiento = datos_movimiento.get('empresa_id')
    # `receptor` está poblado en todos los DTE de compra; `sucursal` casi
    # siempre viene NULL, así que anclar ahí no serviría de nada.
    if empresa_movimiento and dte.receptor_id == empresa_movimiento:
        return True

    return False


class Command(BaseCommand):
    help = ('Recupera desde MySQL el N° de factura de compra de los movimientos '
            'migrados, en una tabla aparte que no afecta reportes (dry-run por defecto)')

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true',
                            help='Escribe los cambios. Sin esto solo simula.')
        parser.add_argument('--limite', type=int, default=0,
                            help='Procesa solo N movimientos (0 = todos)')
        parser.add_argument('--tolerancia-dias', type=int, default=180,
                            help='Máxima distancia entre la fecha del movimiento y la '
                                 'del DTE para dar por buena la referencia (default 180)')
        parser.add_argument('--revertir', action='store_true',
                            help='Vacía la tabla de documentos legacy y termina')

    def handle(self, *args, **opciones):
        if opciones['revertir']:
            return self._revertir(opciones['aplicar'])

        aplicar = opciones['aplicar']
        limite = opciones['limite']
        tolerancia = timedelta(days=opciones['tolerancia_dias'])

        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            raise CommandError(
                'Faltan las variables MYSQL_HOST / MYSQL_DATABASE / MYSQL_USER. '
                'Sin la base legacy este dato no se puede recuperar: no existe '
                'en PostgreSQL.')

        try:
            import mysql.connector
        except ImportError:
            raise CommandError(
                'Falta mysql-connector-python. Instálelo con:\n'
                '    pip install -r requirements-dev.txt')

        self.stdout.write(self.style.SUCCESS(
            '\n═══ N° DE FACTURA DE COMPRA DESDE EL SISTEMA ANTERIOR ═══'))
        self.stdout.write(
            self.style.WARNING('MODO SIMULACIÓN — no se escribe nada. Use --aplicar para guardar.')
            if not aplicar else
            self.style.ERROR('MODO ESCRITURA — se llena app_documentocompralegacy.'))
        self.stdout.write(self.style.HTTP_INFO(
            '  Destino: SOLO la tabla app_documentocompralegacy.\n'
            '  No se modifica Movimientos_Producto ni Dte: kardex, FIFO y\n'
            '  reportes quedan exactamente igual.'))

        # ── Universo: ingresos por compra migrados, aún no rescatados ────
        ya_rescatados = set(
            DocumentoCompraLegacy.objects.values_list('movimiento_origen_id', flat=True))

        pendientes = (Movimientos_Producto.objects
                      .filter(referencia_externa__startswith='MIG:',
                              concepto__in=CONCEPTOS_INGRESO_COMPRA,
                              cantidad__gt=0)
                      .order_by('id'))

        total = pendientes.count()
        self.stdout.write(f'\n  Ingresos por compra migrados      : {total:,}')
        if ya_rescatados:
            self.stdout.write(f'  Ya rescatados en corridas previas : {len(ya_rescatados):,}')

        filas = pendientes.values_list(
            'id', 'referencia_externa', 'fecha', 'cantidad', 'costo', 'concepto',
            'ProductoTalla_id', 'ProductoTalla__sku',
            'sucursal_destino_id', 'sucursal_origen_id',
            # Empresa de la sucursal: es lo que permite corroborar que el DTE
            # candidato fue recibido por quien realmente compró.
            'sucursal_destino__empresa_id', 'sucursal_origen__empresa_id')
        if limite:
            filas = filas[:limite]
            total = min(total, limite)

        self.stdout.write(f'  Conectando a MySQL {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}...')
        conexion = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
            user=MYSQL_USER, password=MYSQL_PASSWORD,
            connection_timeout=600, autocommit=True,
        )

        marcador = {'con_numero': 0, 'sin_numero': 0, 'ya_estaban': 0,
                    'con_dte_identificado': 0, 'solo_numero': 0,
                    'ambiguos': 0, 'fuera_de_fecha': 0, 'sin_corroborar': 0}
        ejemplos = []
        procesados = 0

        try:
            for tanda in self._tandas(filas.iterator(chunk_size=TANDA), TANDA):
                procesados += len(tanda)
                self._procesar_tanda(conexion, tanda, ya_rescatados, tolerancia,
                                     aplicar, marcador, ejemplos)
                self.stdout.write(
                    f'    · {procesados:,}/{total:,} revisados — '
                    f'{marcador["con_numero"]:,} con N° recuperado', ending='\r')
        finally:
            conexion.close()

        self.stdout.write('\n')
        self.stdout.write(f'  N° de factura recuperado          : {marcador["con_numero"]:,}')
        self.stdout.write(f'    · con el DTE identificado       : {marcador["con_dte_identificado"]:,}')
        self.stdout.write(f'    · solo el número (sin cabecera) : {marcador["solo_numero"]:,}')
        self.stdout.write(f'    · N° repetido en varios DTE     : {marcador["ambiguos"]:,}')
        self.stdout.write(f'    · DTE descartado por fecha      : {marcador["fuera_de_fecha"]:,}')
        self.stdout.write(f'    · DTE hallado pero sin corroborar: {marcador["sin_corroborar"]:,}')
        self.stdout.write(f'  Sin N° en el sistema anterior     : {marcador["sin_numero"]:,}')
        if marcador['ya_estaban']:
            self.stdout.write(f'  Omitidos (ya rescatados)          : {marcador["ya_estaban"]:,}')

        for linea in ejemplos[:8]:
            self.stdout.write(f'    ej. {linea}')

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\n  Simulación terminada. Repita con --aplicar para escribir.\n'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n  ✓ {DocumentoCompraLegacy.objects.count():,} documentos en la tabla.\n'
                f'    Verifique con: python manage.py diagnosticar_respaldo_compras\n'))

    # ── helpers ─────────────────────────────────────────────────────────

    def _revertir(self, aplicar):
        cantidad = DocumentoCompraLegacy.objects.count()
        self.stdout.write(f'\n  Documentos legacy cargados: {cantidad:,}')
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  Simulación. Repita con --revertir --aplicar para borrarlos.\n'))
            return
        DocumentoCompraLegacy.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {cantidad:,} registros borrados. Nada más fue tocado.\n'))

    @staticmethod
    def _tandas(iterable, tamano):
        """Agrupa un iterable en listas de `tamano` elementos."""
        tanda = []
        for elemento in iterable:
            tanda.append(elemento)
            if len(tanda) >= tamano:
                yield tanda
                tanda = []
        if tanda:
            yield tanda

    def _procesar_tanda(self, conexion, tanda, ya_rescatados, tolerancia,
                        aplicar, marcador, ejemplos):
        # mysql_id → datos del movimiento en Django
        por_id_mysql = {}
        for fila in tanda:
            (pk, referencia, fecha, cantidad, costo, concepto,
             talla_id, sku, suc_destino, suc_origen,
             emp_destino, emp_origen) = fila
            if pk in ya_rescatados:
                marcador['ya_estaban'] += 1
                continue
            try:
                id_mysql = int(referencia.split(':', 1)[1])
            except (ValueError, IndexError, AttributeError):
                continue
            por_id_mysql[id_mysql] = {
                'pk': pk, 'fecha': fecha, 'cantidad': cantidad, 'costo': costo,
                'concepto': concepto, 'talla_id': talla_id, 'sku': sku,
                'sucursal_id': suc_destino or suc_origen,
                'empresa_id': emp_destino or emp_origen,
            }
        if not por_id_mysql:
            return

        # ── N_documento del legacy, solo para los ids de esta tanda ──────
        # `<> ''` con comillas SIMPLES: con dobles, MySQL en modo ANSI_QUOTES
        # lo interpreta como un identificador y responde
        # "Unknown column '' in 'where clause'".
        marcadores = ','.join(['%s'] * len(por_id_mysql))
        cursor = conexion.cursor()
        cursor.execute(
            f'SELECT id, N_documento, marca FROM movimiento_productos '
            f'WHERE id IN ({marcadores}) '
            f"AND N_documento IS NOT NULL AND TRIM(N_documento) <> ''",
            list(por_id_mysql.keys()),
        )
        documentos = {fila[0]: (str(fila[1]).strip(), fila[2]) for fila in cursor}
        cursor.close()

        marcador['sin_numero'] += len(por_id_mysql) - len(documentos)
        if not documentos:
            return
        marcador['con_numero'] += len(documentos)

        # ── ¿Existe la cabecera del documento en el sistema? ─────────────
        numeros = set()
        for texto, _marca in documentos.values():
            numero = _folio(texto)
            if numero is not None:
                numeros.add(numero)

        dte_por_numero = {}
        if numeros:
            # `numero_documento` NO es único: la unicidad real es
            # (emisor, tipo_documento, numero_documento). Buscar solo por el
            # número traía notas de crédito, guías y cotizaciones del
            # proveedor equivocado. Se restringe a documentos que puedan ser
            # "la factura con la que se compró" y se guarda el emisor para
            # poder corroborarlo después contra la marca.
            candidatos_qs = (
                Dte.objects
                .filter(tipo_transaccion='COMPRA',
                        numero_documento__in=numeros,
                        tipo_documento__in=TIPOS_DOCUMENTO_COMPRA)
                .select_related('emisor')
            )
            for dte in candidatos_qs:
                dte_por_numero.setdefault(dte.numero_documento, []).append(dte)

        nuevos = []
        for id_mysql, (texto, marca) in documentos.items():
            datos = por_id_mysql[id_mysql]
            numero = _folio(texto)
            candidatos = dte_por_numero.get(numero, []) if numero is not None else []

            # Atribuir el documento a un proveedor exige CORROBORACIÓN, no
            # solo que el número coincida: 97 de cada 100 folios tienen un
            # único DTE en la base, y la premisa del rescate es que la factura
            # verdadera muchas veces NO está cargada. Sin esta guarda el caso
            # frecuente era el peligroso: quedarse con la única factura que
            # lleva ese número aunque sea de OTRO proveedor, y mostrarle al
            # usuario el N° correcto junto al proveedor equivocado.
            dte_id = proveedor_id = None
            if len(candidatos) > 1:
                marcador['ambiguos'] += 1
            elif len(candidatos) == 1:
                dte = candidatos[0]
                if (dte.fecha_emision and datos['fecha']
                        and abs(dte.fecha_emision - datos['fecha']) > tolerancia):
                    marcador['fuera_de_fecha'] += 1
                elif not _corrobora(dte, marca, datos):
                    marcador['sin_corroborar'] += 1
                else:
                    dte_id, proveedor_id = dte.id, dte.emisor_id
                    marcador['con_dte_identificado'] += 1
                    if len(ejemplos) < 8:
                        ejemplos.append(
                            f'SKU {datos["sku"]} → factura N° {texto} '
                            f'({dte.fecha_emision}) — {dte.emisor.nombre if dte.emisor_id else "?"}')
            else:
                marcador['solo_numero'] += 1
                if len(ejemplos) < 8:
                    ejemplos.append(
                        f'SKU {datos["sku"]} → factura N° {texto} '
                        f'(ingreso {datos["fecha"]}) — solo el número')

            nuevos.append(DocumentoCompraLegacy(
                producto_talla_id=datos['talla_id'],
                sku=str(datos['sku'] or ''),
                numero_documento=texto[:50],
                fecha_movimiento=datos['fecha'],
                cantidad=datos['cantidad'] or 0,
                costo=datos['costo'] or 0,
                concepto=datos['concepto'],
                marca_legacy=(str(marca)[:100] if marca else None),
                sucursal_id=datos['sucursal_id'],
                dte_id=dte_id,
                proveedor_id=proveedor_id,
                movimiento_origen_id=datos['pk'],
            ))

        if aplicar and nuevos:
            with transaction.atomic():
                for i in range(0, len(nuevos), LOTE_ESCRITURA):
                    DocumentoCompraLegacy.objects.bulk_create(
                        nuevos[i:i + LOTE_ESCRITURA], ignore_conflicts=True)
