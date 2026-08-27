"""
Emite gift cards a partir de una LISTA (CSV) de beneficiarios y, opcionalmente,
envía los códigos por correo.

Pensado para lotes corporativos: "26 gift cards de $50.000, una por hijo de
trabajador, avisadas al correo del trabajador".

    # 1) Ver el plan (NO emite ni envía nada)
    python manage.py emitir_giftcards_desde_lista --csv giftcards_albemarle_2026-08.csv \
        --monto 50000 --sucursal NICK1 --empresa 76104936-4 --vigencia-dias 60 \
        --descripcion "NAVIDAD ALBEMARLE 2026"

    # 2) Emitir (sin enviar correos todavía)
    ... --aplicar

    # 3) Emitir y enviar los correos: UN correo por destinatario con TODAS sus
    #    tarjetas (un papá con 4 hijos recibe 1 correo con 4 códigos)
    ... --aplicar --enviar

    # Variante: un correo POR TARJETA (4 correos separados a ese papá)
    ... --aplicar --enviar --correo-por-tarjeta

Formato del CSV (separador `;`, con encabezado):

    n;beneficiario;trabajador;correo
    1;ISIDORA DIAZ;JAVIER DIAZ;javier.diaz@empresa.com

`beneficiario` puede ir vacío (se emite igual, sin nombre en el correo).
"""
import csv
import logging
import os
import time

from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from app.models import Empresa, GiftCard, Sucursal, MOTIVO_GIFTCARD_CHOICES
from app.services import giftcard_service
from app.views_modulo_giftcards import (
    CorreoGiftCardError, enviar_codigos_por_correo,
)

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = ('Emite gift cards desde un CSV de beneficiarios y (opcional) envía los '
            'códigos por correo. Dry-run por defecto: usa --aplicar.')

    def add_arguments(self, parser):
        parser.add_argument('--csv', required=True,
                            help='Ruta del CSV (columnas: n;beneficiario;trabajador;correo).')
        parser.add_argument('--monto', type=int, required=True,
                            help='Monto de cada gift card en pesos.')
        parser.add_argument('--sucursal', required=True,
                            help='Sucursal de emisión (alias, nombre o id).')
        parser.add_argument('--empresa', default=None,
                            help='Ámbito de canje: RUT o id de empresa. Omitir = todas.')
        parser.add_argument('--vigencia-dias', type=int, default=60,
                            help='Días de vigencia desde hoy (default 60).')
        parser.add_argument('--motivo', default='REGALO_CORP',
                            help='Motivo (default REGALO_CORP).')
        parser.add_argument('--descripcion', default='',
                            help='Etiqueta del lote. Al beneficiario se le concatena.')
        parser.add_argument('--aplicar', action='store_true',
                            help='Emite de verdad (sin esta flag solo muestra el plan).')
        parser.add_argument('--enviar', action='store_true',
                            help='Además de emitir, envía los códigos por correo.')
        parser.add_argument('--correo-por-tarjeta', action='store_true',
                            help='Un correo por cada tarjeta (default: uno por destinatario '
                                 'con todas sus tarjetas juntas).')
        parser.add_argument('--solo-pendientes', action='store_true',
                            help='NO emite nada: reenvía solo a los destinatarios del CSV '
                                 'cuyas gift cards del lote quedaron sin correo enviado. '
                                 'Usa --descripcion para identificar el lote.')

    # --- helpers de resolución (mismos criterios que emitir_giftcards_lote) ---
    def _resolver_sucursal(self, valor):
        qs = Sucursal.objects.all()
        if str(valor).isdigit():
            suc = qs.filter(id=int(valor)).first()
            if suc:
                return suc
        suc = qs.filter(Q(alias__iexact=valor) | Q(nombre__iexact=valor)).first()
        if not suc:
            raise CommandError(f'Sucursal no encontrada: {valor!r}')
        return suc

    def _resolver_empresa(self, valor):
        if valor is None:
            return None
        qs = Empresa.objects.all()
        if str(valor).isdigit():
            emp = qs.filter(id=int(valor)).first()
            if emp:
                return emp
        emp = qs.filter(rut__iexact=str(valor).strip()).first()
        if not emp:
            raise CommandError(f'Empresa no encontrada (RUT o id): {valor!r}')
        return emp

    def _leer_csv(self, ruta):
        if not os.path.exists(ruta):
            raise CommandError(f'No existe el archivo: {ruta}')
        filas = []
        with open(ruta, newline='', encoding='utf-8-sig') as f:
            for i, row in enumerate(csv.DictReader(f, delimiter=';'), start=2):
                correo = (row.get('correo') or '').strip()
                if not correo:
                    raise CommandError(f'Fila {i} del CSV sin correo.')
                try:
                    validate_email(correo)
                except ValidationError:
                    raise CommandError(f'Fila {i}: correo inválido {correo!r}')
                filas.append({
                    'beneficiario': (row.get('beneficiario') or '').strip(),
                    'trabajador': (row.get('trabajador') or '').strip(),
                    'correo': correo.lower(),
                })
        if not filas:
            raise CommandError('El CSV no tiene filas de datos.')
        return filas

    def _emitir(self, filas, monto, sucursal, empresa, vencimiento, motivo,
                descripcion_lote):
        """Emite una gift card por fila del CSV. Devuelve [(fila, giftcard)]."""
        emitidas = []
        for f in filas:
            desc = ' - '.join(x for x in (descripcion_lote, f['beneficiario']) if x)
            gc = giftcard_service.emitir(
                monto,
                sucursal=sucursal,
                empresa=empresa,
                vencimiento=vencimiento,
                tipo_tarjeta='DIGITAL',
                motivo=motivo,
                descripcion=desc,
                observaciones=(f"Trabajador: {f['trabajador']}" if f['trabajador'] else ''),
            )
            emitidas.append((f, gc))
            self.stdout.write(f"  {len(emitidas):>3}/{len(filas)}  {gc.codigo}  "
                              f"{f['beneficiario'] or '(sin nombre)'}  → {f['correo']}")
        return emitidas

    def _escribir_csv(self, emitidas, empresa):
        """Respaldo con los códigos ANTES de intentar enviar los correos."""
        stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
        ruta_csv = os.path.join(os.getcwd(), f'_giftcards_emitidas_{stamp}.csv')
        with open(ruta_csv, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh, delimiter=';')
            w.writerow(['codigo', 'monto', 'beneficiario', 'trabajador', 'correo',
                        'vencimiento', 'ambito'])
            for f, gc in emitidas:
                w.writerow([gc.codigo, gc.saldo_actual, f['beneficiario'], f['trabajador'],
                            f['correo'], gc.fecha_vencimiento,
                            empresa.nombre if empresa else 'TODAS'])
        return ruta_csv

    def handle(self, *args, **options):
        monto = options['monto']
        if monto <= 0:
            raise CommandError('El monto debe ser mayor a 0.')
        motivo = (options['motivo'] or 'REGALO_CORP').upper()
        if motivo not in {c[0] for c in MOTIVO_GIFTCARD_CHOICES}:
            raise CommandError(f'Motivo inválido: {motivo}')
        vigencia_dias = options['vigencia_dias']
        if vigencia_dias <= 0:
            raise CommandError('La vigencia debe ser positiva (días).')

        filas = self._leer_csv(options['csv'])
        sucursal = self._resolver_sucursal(options['sucursal'])
        empresa = self._resolver_empresa(options['empresa'])
        vencimiento = timezone.localdate() + timezone.timedelta(days=vigencia_dias)
        descripcion_lote = (options['descripcion'] or '').strip()
        agrupar = not options['correo_por_tarjeta']

        # Agrupación por destinatario (un trabajador puede tener varios hijos).
        por_correo = {}
        for f in filas:
            por_correo.setdefault(f['correo'], []).append(f)

        pasivo = len(filas) * monto
        ambito_txt = (f'solo sucursales de {empresa.nombre} (RUT {empresa.rut})'
                      if empresa else 'TODAS las empresas de la cadena')
        n_correos = len(por_correo) if agrupar else len(filas)

        self.stdout.write('')
        self.stdout.write(f'  Gift cards   : {len(filas)} de ${monto:,}'.replace(',', '.'))
        self.stdout.write(self.style.WARNING(
            f'  PASIVO TOTAL : ${pasivo:,}'.replace(',', '.') + '  (deuda que asume el negocio)'
        ))
        self.stdout.write(f'  Emisión      : {sucursal.alias} (id {sucursal.id})')
        self.stdout.write(f'  Ámbito canje : {ambito_txt}')
        self.stdout.write(f'  Vencimiento  : {vencimiento} ({vigencia_dias} días)')
        self.stdout.write(f'  Destinatarios: {len(por_correo)} correos distintos')
        if options['enviar']:
            self.stdout.write(
                f'  Correos      : {n_correos} '
                + ('(uno por destinatario, con todas sus tarjetas)' if agrupar
                   else '(uno por cada tarjeta)')
            )
        else:
            self.stdout.write('  Correos      : NO se enviarán (falta --enviar)')
        self.stdout.write('')
        for correo, items in sorted(por_correo.items()):
            nombres = ', '.join(i['beneficiario'] or 's/n' for i in items)
            self.stdout.write(f'    {correo:<40} {len(items)} → {nombres}')
        self.stdout.write('')

        # El dry-run de EMISIÓN no aplica al modo reenvío: ese tiene su propio
        # resumen (qué tarjetas quedaron sin avisar) más abajo.
        if not options['aplicar'] and not options['solo_pendientes']:
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se emitió ni envió nada. Agrega --aplicar para emitir'
                + (' y --enviar para mandar los correos.' if not options['enviar']
                   else ' (los correos se enviarán).')
            ))
            return

        # ===== REENVÍO DE PENDIENTES (no emite nada) =====
        # Sirve cuando el proveedor cortó la conexión a mitad del lote: las
        # tarjetas ya existen y solo falta avisarle a su destinatario.
        if options['solo_pendientes']:
            if not descripcion_lote:
                raise CommandError(
                    '--solo-pendientes necesita --descripcion para identificar el lote.')
            pendientes = list(
                GiftCard.objects
                .filter(descripcion__startswith=descripcion_lote,
                        correo_enviado_en__isnull=True)
                .exclude(estado='ANULADA')
                .order_by('id')
            )
            if not pendientes:
                self.stdout.write(self.style.SUCCESS(
                    '>> No hay gift cards pendientes de envío en este lote.'))
                return
            # Se cruza cada tarjeta con su destinatario usando el nombre del
            # beneficiario que quedó grabado en la descripción.
            por_beneficiario = {}
            for f in filas:
                clave = (f['beneficiario'] or '').strip().upper()
                por_beneficiario.setdefault(clave, []).append(f)
            emitidas = []
            sin_cruce = []
            usados = set()
            for gc in pendientes:
                nombre_benef = (gc.descripcion or '').replace(descripcion_lote, '', 1)
                nombre_benef = nombre_benef.lstrip(' -').strip().upper()
                candidatos = [f for f in por_beneficiario.get(nombre_benef, [])
                              if id(f) not in usados]
                if not candidatos:
                    sin_cruce.append(gc)
                    continue
                f = candidatos[0]
                usados.add(id(f))
                emitidas.append((f, gc))
            self.stdout.write(self.style.WARNING(
                f'>> MODO REENVÍO: {len(emitidas)} gift card(s) sin correo enviado.'))
            if sin_cruce:
                self.stdout.write(self.style.ERROR(
                    f'   {len(sin_cruce)} tarjeta(s) no se pudieron cruzar con el CSV '
                    '(envíalas desde la pantalla): '
                    + ', '.join(g.codigo for g in sin_cruce)))
            if not options['aplicar']:
                for f, gc in emitidas:
                    self.stdout.write(f"   {gc.codigo}  {f['beneficiario'] or '(sin nombre)'}"
                                      f"  → {f['correo']}")
                self.stdout.write(self.style.WARNING(
                    '[DRY-RUN] Agrega --aplicar --enviar para reenviarlos.'))
                return
            if not options['enviar']:
                raise CommandError('--solo-pendientes requiere también --enviar.')
            ruta_csv = '(reenvío: no se generó CSV nuevo)'
            self.stdout.write('')
        else:
            # ===== EMISIÓN =====
            emitidas = self._emitir(filas, monto, sucursal, empresa, vencimiento,
                                    motivo, descripcion_lote)
            ruta_csv = self._escribir_csv(emitidas, empresa)
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'>> {len(emitidas)} gift cards emitidas.'))
            self.stdout.write(self.style.SUCCESS(f'>> Códigos en: {ruta_csv}'))

        if not options['enviar']:
            self.stdout.write('Para enviarlas por correo: repite con --enviar, o usa el '
                              'botón "Enviar por correo" en la pantalla de Gift Cards.')
            return

        # ===== ENVÍO DE CORREOS =====
        # Una sola conexión SMTP para todo el lote: si la abre cada send(),
        # Django la cierra al terminar y cada correo paga otro handshake TLS.
        # (Se reabre automáticamente si el proveedor la corta — ver más abajo.)
        connection = get_connection(
            timeout=int(os.environ.get('GIFTCARD_EMAIL_TIMEOUT', '30'))
        )
        try:
            connection.open()
        except Exception as e:
            raise CommandError(
                f'No se pudo conectar al servidor de correo: {e}. '
                f'Las gift cards YA fueron emitidas (ver {ruta_csv}); '
                'reintenta el envío desde la pantalla de Gift Cards.'
            )

        enviados_ok, fallidos = 0, []
        # Una respuesta 421 del servidor ("Service not available", típico tope de
        # envío) DEJA MUERTA la conexión: los mensajes siguientes reventaban con
        # "please run connect() first" y un solo fallo se llevaba todo el lote
        # (medido en prod 22-ago: 1 rechazo -> 10 correos perdidos). Por eso cada
        # destinatario se reintenta reabriendo la conexión, y se hace una pausa
        # entre envíos para no gatillar el límite.
        pausa = float(os.environ.get('GIFTCARD_EMAIL_PAUSA_SEG', '1'))
        intentos_max = int(os.environ.get('GIFTCARD_EMAIL_REINTENTOS', '3'))
        # MailerSend acepta 5 mensajes por conexión y cierra el sexto con un
        # 421. El reintento de abajo lo resuelve, pero pagando un envío fallido
        # + su espera cada 6 destinatarios. Reabrir ANTES de llegar al tope
        # convierte ese fallo previsible en una reconexión limpia.
        max_por_conexion = int(os.environ.get('GIFTCARD_EMAIL_MAX_POR_CONEXION', '5'))
        estado = {'conn': connection}

        def _reabrir():
            try:
                estado['conn'].close()
            except Exception:
                pass
            nueva = get_connection(
                timeout=int(os.environ.get('GIFTCARD_EMAIL_TIMEOUT', '30'))
            )
            nueva.open()
            estado['conn'] = nueva

        def _enviar_con_reintento(gcs, correo, nombre, benef):
            ultimo_error = None
            for intento in range(1, intentos_max + 1):
                try:
                    return enviar_codigos_por_correo(
                        gcs, correo, nombre_destino=nombre,
                        sucursal=sucursal, beneficiarios=benef,
                        connection=estado['conn'],
                    )
                except CorreoGiftCardError as e:
                    ultimo_error = e
                    if intento < intentos_max:
                        espera = pausa * (intento * 2)
                        self.stdout.write(self.style.WARNING(
                            f'  … reintento {intento + 1}/{intentos_max} para {correo} '
                            f'(esperando {espera:.0f}s)'
                        ))
                        time.sleep(espera)
                        try:
                            _reabrir()
                        except Exception as e2:
                            ultimo_error = CorreoGiftCardError(
                                f'No se pudo reabrir la conexión: {e2}')
                            break
            raise ultimo_error

        try:
            if agrupar:
                grupos = {}
                for f, gc in emitidas:
                    grupos.setdefault(f['correo'], []).append((f, gc))
                lotes = [
                    (correo, [gc for _, gc in items],
                     items[0][0]['trabajador'],
                     {gc.id: f['beneficiario'] for f, gc in items})
                    for correo, items in grupos.items()
                ]
            else:
                lotes = [
                    (f['correo'], [gc], f['trabajador'], {gc.id: f['beneficiario']})
                    for f, gc in emitidas
                ]

            for i, (correo, gcs, nombre, benef) in enumerate(lotes):
                if i and pausa:
                    time.sleep(pausa)   # no gatillar el tope de envío del proveedor
                if i and max_por_conexion and i % max_por_conexion == 0:
                    try:
                        _reabrir()
                    except Exception as e:
                        # Si no se puede reabrir acá, el reintento de
                        # `_enviar_con_reintento` lo vuelve a intentar; no
                        # tiene sentido abortar el lote entero por esto.
                        self.stdout.write(self.style.WARNING(
                            f'  … no se pudo renovar la conexión SMTP: {e}'
                        ))
                try:
                    _enviar_con_reintento(gcs, correo, nombre, benef)
                    enviados_ok += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ {correo} ({len(gcs)} tarjeta(s))'
                    ))
                except CorreoGiftCardError as e:
                    # Un correo caído no detiene el lote: las demás tarjetas se
                    # envían igual y al final se listan las que quedaron sin avisar.
                    fallidos.append((correo, str(e)))
                    self.stdout.write(self.style.ERROR(f'  ✗ {correo}: {e}'))
        finally:
            try:
                estado['conn'].close()
            except Exception:
                pass

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'>> Correos enviados: {enviados_ok}'))
        if fallidos:
            self.stdout.write(self.style.ERROR(
                f'>> Correos NO enviados: {len(fallidos)} — reenvíalos desde la '
                'pantalla de Gift Cards (filtro "Sin enviar"):'
            ))
            for correo, err in fallidos:
                self.stdout.write(self.style.ERROR(f'   - {correo}: {err}'))
        logger.info(
            "emitir_giftcards_desde_lista: modo=%s, %s gift cards, %s correos ok, %s fallidos",
            'REENVIO' if options['solo_pendientes'] else 'EMISION',
            len(emitidas), enviados_ok, len(fallidos),
        )
