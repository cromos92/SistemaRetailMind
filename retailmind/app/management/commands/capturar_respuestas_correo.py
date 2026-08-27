"""
Lee el buzón de respuestas y pega lo que contestó el proveedor en su ficha.

Antes esto era trabajo manual: la respuesta llegaba a una casilla, alguien
tenía que abrir el requerimiento y copiarla a mano. Cuando nadie lo hacía, el
caso se quedaba en "esperando respuesta" para siempre aunque el proveedor
hubiera contestado el mismo día.

Pensado para correr por cron cada 10 minutos. Solo mira los correos NO LEÍDOS y
marca como leído únicamente lo que pudo procesar: si algo falla, el correo
queda sin leer y la corrida siguiente lo reintenta.

    python manage.py capturar_respuestas_correo --dry-run   # ver qué haría
    python manage.py capturar_respuestas_correo             # procesar
"""
import imaplib
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.models import HistorialRequerimiento, Requerimiento, RespuestaCorreo
from app.services import captura_respuestas

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = ('Lee el buzón de respuestas por IMAP y registra en cada ficha lo '
            'que contestó el proveedor.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Muestra qué haría sin escribir nada ni marcar correos como leídos')
        parser.add_argument(
            '--max', type=int, default=50,
            help='Tope de correos a procesar por corrida (default 50)')
        parser.add_argument(
            '--carpeta', default=None,
            help='Carpeta IMAP a leer (default: la de CORREO_IMAP_CARPETA)')

    def handle(self, *args, **options):
        simulacion = options['dry_run']
        tope = options['max']
        carpeta = options['carpeta'] or settings.CORREO_IMAP_CARPETA

        buzon = (getattr(settings, 'CORREO_BUZON_RESPUESTAS', '') or '').strip()
        usuario = (getattr(settings, 'CORREO_IMAP_USER', '') or '').strip() or buzon
        clave = getattr(settings, 'CORREO_IMAP_PASSWORD', '') or ''

        if not buzon:
            raise CommandError(
                'Falta CORREO_BUZON_RESPUESTAS. Sin la casilla base no se puede '
                'saber qué direcciones del correo llevan el token.')
        if not clave:
            raise CommandError(
                'Falta CORREO_IMAP_PASSWORD. En Gmail es una "contraseña de '
                'aplicación" de 16 caracteres (requiere 2FA activo en la cuenta), '
                'no la contraseña normal del correo.')

        self.stdout.write(f'Leyendo {usuario} en {settings.CORREO_IMAP_HOST} '
                          f'(carpeta {carpeta})')

        try:
            conexion = imaplib.IMAP4_SSL(settings.CORREO_IMAP_HOST,
                                         settings.CORREO_IMAP_PORT)
            conexion.login(usuario, clave)
        except imaplib.IMAP4.error as e:
            raise CommandError(
                f'No se pudo entrar al buzón: {e}. Si es Gmail, revisa que la '
                f'cuenta tenga 2FA y que la clave sea una contraseña de aplicación.')

        procesados = ignorados = fallidos = 0
        sin_identificar = []
        try:
            estado, _ = conexion.select(carpeta)
            if estado != 'OK':
                raise CommandError(f'No existe la carpeta {carpeta} en el buzón')

            estado, datos = conexion.search(None, 'UNSEEN')
            if estado != 'OK':
                raise CommandError('No se pudo listar el buzón')

            ids = (datos[0] or b'').split()
            if not ids:
                self.stdout.write(self.style.SUCCESS('No hay correos nuevos.'))
                return

            self.stdout.write(f'{len(ids)} correo(s) sin leer'
                              + (f' — se procesan los primeros {tope}'
                                 if len(ids) > tope else ''))

            for num in ids[:tope]:
                estado, datos = conexion.fetch(num, '(BODY.PEEK[])')
                if estado != 'OK' or not datos or not datos[0]:
                    fallidos += 1
                    continue
                crudo = datos[0][1]

                try:
                    info = captura_respuestas.parsear(crudo, buzon)
                except Exception as e:
                    logger.exception('No se pudo interpretar un correo del buzón')
                    self.stdout.write(self.style.ERROR(f'   ✗ correo ilegible: {e}'))
                    fallidos += 1
                    continue

                envio = info['envio']
                if not envio:
                    # No es una respuesta a un requerimiento (newsletter, spam,
                    # un correo suelto). Se deja SIN LEER para que una persona
                    # lo vea: descartarlo en silencio sería peor.
                    ignorados += 1
                    sin_identificar.append(
                        f"{info['remitente']} — {info['asunto'][:60]}")
                    continue

                etiqueta = (f"{info['remitente']} → envío #{envio.id} "
                            f"(por {info['via']})")
                if simulacion:
                    self.stdout.write(f'   · {etiqueta}')
                    procesados += 1
                    continue

                try:
                    creada = self._registrar(envio, info)
                except Exception as e:
                    logger.exception('Error al registrar la respuesta del envío %s',
                                     envio.id)
                    self.stdout.write(self.style.ERROR(f'   ✗ {etiqueta}: {e}'))
                    fallidos += 1
                    continue

                # Recién acá se marca leído: si algo reventó más arriba, el
                # correo sigue sin leer y la próxima corrida lo reintenta.
                conexion.store(num, '+FLAGS', '\\Seen')
                procesados += 1
                self.stdout.write(self.style.SUCCESS(
                    f'   ✓ {etiqueta}' + ('' if creada else ' (ya estaba registrada)')))
        finally:
            try:
                conexion.close()
            except Exception:
                pass
            try:
                conexion.logout()
            except Exception:
                pass

        self.stdout.write('')
        if simulacion:
            self.stdout.write(self.style.WARNING(
                'MODO SIMULACIÓN — no se escribió nada ni se marcó ningún correo.'))
        self.stdout.write(self.style.SUCCESS(f'Respuestas registradas: {procesados}'))
        if ignorados:
            self.stdout.write(self.style.WARNING(
                f'Correos que no corresponden a ningún requerimiento: {ignorados} '
                f'(quedan SIN LEER en el buzón para que alguien los mire)'))
            for linea in sin_identificar[:10]:
                self.stdout.write(f'   - {linea}')
        if fallidos:
            self.stdout.write(self.style.ERROR(f'Fallidos: {fallidos}'))

        logger.info('capturar_respuestas_correo: %s registradas, %s ignoradas, %s fallidas',
                    procesados, ignorados, fallidos)

    @transaction.atomic
    def _registrar(self, envio, info):
        """Guarda la respuesta y actualiza envío + requerimiento. True si es nueva."""
        try:
            RespuestaCorreo.objects.create(
                envio=envio,
                remitente=info['remitente'],
                asunto=info['asunto'],
                cuerpo=info['cuerpo'],
                recibido_en=info['recibido_en'],
                message_id=info['message_id'],
                in_reply_to=info['in_reply_to'],
                adjuntos=info['adjuntos'],
            )
        except IntegrityError:
            # Ya la habíamos leído (el buzón se marcó como no leído a mano, o
            # una corrida anterior murió después de guardar).
            return False

        if envio.registrar_estado('RESPONDIDO', cuando=info['recibido_en']):
            envio.save(update_fields=['estado', 'estado_en'])

        if envio.modulo != 'REQUERIMIENTO' or not envio.objeto_id:
            return True

        requerimiento = (Requerimiento.objects
                         .select_for_update()
                         .filter(pk=envio.objeto_id).first())
        if not requerimiento:
            return True

        # NO se decide por el proveedor: interpretar "lo vemos" o "mándalo a
        # revisión" como APROBADO sería inventar. Se deja la respuesta escrita
        # y la clasificación (APROBADO/RECHAZADO/PARCIAL) al humano.
        resumen = info['cuerpo'][:2000] or '(respuesta sin texto)'
        adjuntos = info['adjuntos']
        if adjuntos:
            nombres = ', '.join(a['nombre'] for a in adjuntos[:5])
            resumen += f'\n\n[Adjuntos en el correo: {nombres}]'

        anterior = (requerimiento.respuesta_proveedor or '').strip()
        marca = (f'--- Respuesta de {info["remitente"]} el '
                 f'{timezone.localtime(info["recibido_en"]):%d/%m/%Y %H:%M} ---')
        requerimiento.respuesta_proveedor = (
            f'{anterior}\n\n{marca}\n{resumen}'.strip() if anterior
            else f'{marca}\n{resumen}')
        # Marcar la fecha detiene los recordatorios automáticos: el proveedor
        # ya contestó, insistirle sería el ridículo que este comando evita.
        if not requerimiento.fecha_respuesta_proveedor:
            requerimiento.fecha_respuesta_proveedor = info['recibido_en']
        requerimiento.save(update_fields=['respuesta_proveedor',
                                          'fecha_respuesta_proveedor'])

        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='RESPUESTA_RECIBIDA',
            comentario=(f'Respuesta de {info["remitente"]} capturada del buzón '
                        f'(por {info["via"]})'
                        + (f' con {len(adjuntos)} adjunto(s)' if adjuntos else '')
                        + '. Falta clasificarla como aprobada/rechazada.'),
        )
        return True
