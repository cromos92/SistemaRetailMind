"""
Recordatorio automático a los proveedores que no contestaron.

Hasta ahora el recordatorio era 100% manual: alguien tenía que acordarse de
entrar al módulo, filtrar por atrasados y reenviar uno por uno. En la práctica
no pasaba, y los casos se quedaban meses en "esperando respuesta".

Regla que evita el ridículo: si el correo original REBOTÓ, no se manda ningún
recordatorio. Insistirle a una dirección que no existe no sirve de nada; lo que
hay que hacer es corregir la ficha del proveedor, y para eso el comando lo
reporta aparte.

Por defecto SIMULA. Para mandar de verdad hay que pasar --enviar.
"""
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from app.models import Requerimiento, HistorialRequerimiento, EnvioCorreo
from app.services.correo_service import enviar_correo_trazado, CorreoError
from app.services.pdf_requerimiento_proveedor import (
    generar_pdf_requerimiento, nombre_archivo_pdf,
)

logger = logging.getLogger('app')

PLAZO_RESPUESTA_DIAS = int(os.environ.get('REQUERIMIENTOS_PLAZO_RESPUESTA_DIAS', '7'))
# Días mínimos entre un recordatorio y el siguiente. Sin esto, un cron diario
# le mandaría el mismo correo al proveedor todos los días hasta hartarlo.
ESPERA_ENTRE_RECORDATORIOS = int(
    os.environ.get('REQUERIMIENTOS_ESPERA_RECORDATORIO_DIAS', '7'))
# Tope de recordatorios por caso: después de N insistencias el problema ya no
# se resuelve mandando correos, hay que llamar por teléfono.
MAX_RECORDATORIOS = int(os.environ.get('REQUERIMIENTOS_MAX_RECORDATORIOS', '3'))
# MailerSend corta la conexión SMTP al 6º mensaje.
MAX_POR_CONEXION = int(os.environ.get('CORREO_MAX_POR_CONEXION', '5'))


class Command(BaseCommand):
    help = ('Reenvía a los proveedores los requerimientos sin respuesta pasado '
            'el plazo. Por defecto solo simula; usa --enviar para mandarlos.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--enviar', action='store_true',
            help='Manda los correos de verdad (sin esto solo muestra qué haría)')
        parser.add_argument(
            '--dias', type=int, default=PLAZO_RESPUESTA_DIAS,
            help=f'Días sin respuesta para considerarlo atrasado (default {PLAZO_RESPUESTA_DIAS})')
        parser.add_argument(
            '--max', type=int, default=0,
            help='Tope de recordatorios a enviar en esta corrida (0 = sin tope)')
        parser.add_argument(
            '--sucursal', type=int, default=None,
            help='Limitar a una sucursal')

    def handle(self, *args, **options):
        enviar = options['enviar']
        dias = options['dias']
        tope = options['max']

        limite = timezone.now() - timedelta(days=dias)
        qs = (Requerimiento.objects
              .select_related('proveedor', 'sucursal', 'sucursal__empresa')
              .filter(estado='ESPERANDO_RESPUESTA',
                      correo_enviado_proveedor=True,
                      fecha_envio_proveedor__lt=limite,
                      fecha_respuesta_proveedor__isnull=True,
                      proveedor__isnull=False)
              .order_by('fecha_envio_proveedor'))
        if options['sucursal']:
            qs = qs.filter(sucursal_id=options['sucursal'])

        candidatos = list(qs)
        if not candidatos:
            self.stdout.write(self.style.SUCCESS(
                f'No hay requerimientos sin respuesta hace más de {dias} día(s).'))
            return

        # Estado de entrega del último correo de cada caso, en una consulta.
        ultimo_envio = {}
        for envio in (EnvioCorreo.objects
                      .filter(modulo='REQUERIMIENTO', es_copia_control=False,
                              objeto_id__in=[r.id for r in candidatos])
                      .order_by('objeto_id', '-creado_en')):
            ultimo_envio.setdefault(envio.objeto_id, envio)

        a_enviar, rebotados, muy_insistidos, muy_recientes = [], [], [], []
        ahora = timezone.now()
        for req in candidatos:
            envio = ultimo_envio.get(req.id)
            if envio and envio.hubo_problema:
                rebotados.append((req, envio))
                continue
            if (req.intentos_envio or 0) > MAX_RECORDATORIOS:
                muy_insistidos.append(req)
                continue
            if req.ultimo_recordatorio and (
                    ahora - req.ultimo_recordatorio
            ) < timedelta(days=ESPERA_ENTRE_RECORDATORIOS):
                muy_recientes.append(req)
                continue
            a_enviar.append(req)

        if tope:
            a_enviar = a_enviar[:tope]

        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Requerimientos sin respuesta hace más de {dias} día(s): {len(candidatos)}'))
        self.stdout.write(f'  → se les reenvía ahora ......... {len(a_enviar)}')
        self.stdout.write(f'  → correo rebotado (NO se insiste) {len(rebotados)}')
        self.stdout.write(f'  → recordatorio muy reciente .... {len(muy_recientes)}')
        self.stdout.write(f'  → ya se insistió {MAX_RECORDATORIOS}+ veces ...... {len(muy_insistidos)}')

        if rebotados:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(
                'CORREOS QUE NUNCA LLEGARON — hay que corregir la dirección '
                'en la ficha del proveedor:'))
            for req, envio in rebotados:
                self.stdout.write(self.style.ERROR(
                    f'   - {req.numero_requerimiento} · {req.proveedor.nombre} · '
                    f'{envio.destinatario} · {envio.estado}'
                    + (f' ({envio.estado_detalle})' if envio.estado_detalle else '')))

        if muy_insistidos:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f'Ya se insistió más de {MAX_RECORDATORIOS} veces (conviene llamar):'))
            for req in muy_insistidos:
                self.stdout.write(self.style.WARNING(
                    f'   - {req.numero_requerimiento} · {req.proveedor.nombre} · '
                    f'{req.intentos_envio} envíos · {req.dias_sin_respuesta} días'))

        if not a_enviar:
            self.stdout.write('')
            self.stdout.write('Nada que reenviar.')
            return

        if not enviar:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('MODO SIMULACIÓN — no se envió nada.'))
            for req in a_enviar:
                destino = (req.correo_proveedor_destino or '').strip() or '(sin correo)'
                self.stdout.write(
                    f'   · {req.numero_requerimiento} → {destino} '
                    f'({req.dias_sin_respuesta} días sin respuesta)')
            self.stdout.write('')
            self.stdout.write('Para enviarlos de verdad, repite con --enviar')
            return

        # ===== ENVÍO REAL =====
        conexion = get_connection(timeout=30)
        try:
            conexion.open()
        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f'No se pudo conectar al servidor de correo: {e}'))
            return

        enviados, fallidos = 0, []
        for i, req in enumerate(a_enviar):
            if i and i % MAX_POR_CONEXION == 0:
                # El relay corta la conexión pasados unos pocos mensajes.
                try:
                    conexion.close()
                except Exception:
                    pass
                conexion = get_connection(timeout=30)
                conexion.open()
            try:
                self._recordar(req, conexion)
                enviados += 1
                self.stdout.write(self.style.SUCCESS(
                    f'   ✓ {req.numero_requerimiento} → {req.correo_proveedor_destino}'))
            except Exception as e:
                fallidos.append((req, str(e)))
                self.stdout.write(self.style.ERROR(
                    f'   ✗ {req.numero_requerimiento}: {e}'))

        try:
            conexion.close()
        except Exception:
            pass

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Recordatorios enviados: {enviados}'))
        if fallidos:
            self.stdout.write(self.style.ERROR(f'Fallidos: {len(fallidos)}'))
        logger.info('enviar_recordatorios_requerimientos: %s enviados, %s fallidos',
                    enviados, len(fallidos))

    def _recordar(self, requerimiento, conexion):
        """Reenvía UN requerimiento con el formato PDF, sin las fotos originales.

        Las fotos ya viajaron en el envío original y van incrustadas en el PDF:
        repetirlas hace el correo pesado sin agregar información.
        """
        destino = (requerimiento.correo_proveedor_destino or '').strip()
        if not destino:
            raise CorreoError('El requerimiento no tiene correo de destino registrado')

        pdf_bytes = None
        try:
            pdf_bytes = generar_pdf_requerimiento(
                requerimiento, plazo_dias=PLAZO_RESPUESTA_DIAS)
        except Exception:
            logger.exception('No se pudo generar el PDF del recordatorio %s',
                             requerimiento.id)

        contexto = {
            'requerimiento': requerimiento,
            'empresa': requerimiento.sucursal.empresa,
            'usuario': None,
            'es_reenvio': True,
            'mensaje_adicional': (
                f'Este es un recordatorio automático: el requerimiento se envió el '
                f'{timezone.localtime(requerimiento.fecha_envio_proveedor):%d/%m/%Y} '
                f'y seguimos sin respuesta.'),
            'cantidad_fotos_adjuntas': 0,
            'fotos_solo_en_pdf': requerimiento.fotos.count() if pdf_bytes else 0,
            'fotos_no_disponibles': 0,
            'lleva_formato_pdf': pdf_bytes is not None,
            'fecha_limite_respuesta': timezone.localdate() + timedelta(days=PLAZO_RESPUESTA_DIAS),
        }
        html = render_to_string('emails/requerimiento_proveedor.html', contexto)

        referencia = requerimiento.numero_factura_compra or ''
        asunto = (f'RECORDATORIO · [{requerimiento.get_tipo_display().upper()}] '
                  f'{requerimiento.numero_requerimiento} · SKU {requerimiento.sku}'
                  + (f' · FAC {referencia}' if referencia else '')
                  + f' · {requerimiento.sucursal.empresa.nombre}')

        texto = (
            f'RECORDATORIO - Requerimiento {requerimiento.numero_requerimiento}\n'
            f'Enviado originalmente el '
            f'{timezone.localtime(requerimiento.fecha_envio_proveedor):%d/%m/%Y} '
            f'({requerimiento.dias_sin_respuesta} días sin respuesta).\n'
            f'Producto: {requerimiento.sku} - {requerimiento.nombre_producto} '
            f'(cantidad: {requerimiento.cantidad})\n'
            f'Motivo: {requerimiento.motivo}\n'
            f'Por favor responda indicando si procede.\n'
        )

        adjuntos = []
        if pdf_bytes:
            adjuntos.append((nombre_archivo_pdf(requerimiento), pdf_bytes,
                             'application/pdf'))

        enviar_correo_trazado(
            modulo='REQUERIMIENTO',
            objeto_id=requerimiento.id,
            asunto=asunto,
            texto=texto,
            html=html,
            destinatario=destino,
            adjuntos=adjuntos,
            from_email=(getattr(settings, 'REQUERIMIENTOS_FROM_EMAIL', '')
                        or settings.DEFAULT_FROM_EMAIL),
            connection=conexion,
            tags=['requerimiento', 'recordatorio'],
        )

        requerimiento.ultimo_recordatorio = timezone.now()
        requerimiento.intentos_envio = (requerimiento.intentos_envio or 0) + 1
        requerimiento.save(update_fields=['ultimo_recordatorio', 'intentos_envio'])

        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='RECORDATORIO_AUTOMATICO',
            comentario=(f'Recordatorio automático enviado a {destino} '
                        f'({requerimiento.dias_sin_respuesta} días sin respuesta) '
                        f'- intento #{requerimiento.intentos_envio}'),
        )
