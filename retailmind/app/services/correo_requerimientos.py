"""
Correos del módulo de Requerimientos: a quién se le escribe y a dónde vuelve.

Dos datos que antes dependían de quién apretaba "Enviar" y ahora son fijos:

1. El CORREO DEL MÓDULO (`ConfiguracionRequerimientos.correo_modulo`): recibe
   la copia de control de cada envío, es el "Responder" de los correos a
   proveedores, el destino de los botones Aprobar/Rechazar del correo y el
   contacto del PDF. Antes todo eso caía en el correo personal del usuario:
   cada proveedor le respondía a una persona distinta y, si esa persona
   faltaba, las respuestas se perdían.

2. El CORREO DE CADA PROVEEDOR para requerimientos
   (`CorreoProveedorRequerimiento`): se guarda la primera vez que alguien lo
   escribe al enviar y desde ahí se usa siempre. Va aparte de la ficha
   `Empresa` porque sus campos de correo los usan Compras y el intercambio
   de DTE, y el contacto de garantías casi nunca es el mismo.

Todas las lecturas van dentro de un savepoint: si la migración 0242 todavía
no se aplicó, la tabla no existe y el módulo tiene que seguir funcionando
exactamente como antes, no caerse.
"""
import logging
import os

from django.db import DatabaseError, transaction

from ..models import ConfiguracionRequerimientos, CorreoProveedorRequerimiento

logger = logging.getLogger('app')


def correo_ficha_proveedor(empresa):
    """Primer correo configurado en la ficha del proveedor (o None).

    La ficha de Empresa tiene varios campos de correo; el envío histórico solo
    miraba correoVendedor y fallaba con proveedores que solo tienen `email`.
    """
    if not empresa:
        return None
    for campo in ('correoVendedor', 'email', 'correoIntercambio'):
        valor = (getattr(empresa, campo, '') or '').strip()
        if valor:
            return valor
    return None


def correos_guardados(proveedor_ids):
    """``{proveedor_id: correo}`` de los correos recordados para requerimientos.

    Una sola consulta para toda una página del listado.
    """
    ids = [i for i in set(proveedor_ids) if i]
    if not ids:
        return {}
    try:
        with transaction.atomic():
            return dict(CorreoProveedorRequerimiento.objects
                        .filter(proveedor_id__in=ids)
                        .values_list('proveedor_id', 'correo'))
    except DatabaseError:
        logger.warning('Requerimientos: tabla de correos por proveedor no disponible '
                       '(¿falta aplicar la migración 0242?)')
        return {}


def correo_proveedor(empresa):
    """Destino de los requerimientos a este proveedor: el recordado o la ficha."""
    if not empresa:
        return None
    return correos_guardados([empresa.id]).get(empresa.id) or correo_ficha_proveedor(empresa)


def recordar_correo_proveedor(proveedor, correo, usuario):
    """Guarda `correo` como destino fijo de los requerimientos a `proveedor`.

    Devuelve True si cambió algo. Nunca rompe el flujo que lo llama: si la
    tabla no existe (migración sin aplicar) solo queda en el log.
    """
    correo = (correo or '').strip()
    if not proveedor or not correo:
        return False
    try:
        with transaction.atomic():
            fila = CorreoProveedorRequerimiento.objects.filter(proveedor=proveedor).first()
            if fila and fila.correo.lower() == correo.lower():
                return False
            if fila:
                fila.correo = correo
                fila.actualizado_por = usuario
                fila.save(update_fields=['correo', 'actualizado_por', 'actualizado_en'])
            else:
                CorreoProveedorRequerimiento.objects.create(
                    proveedor=proveedor, correo=correo, actualizado_por=usuario)
        logger.info('Correo de requerimientos del proveedor %s: %s (por %s)',
                    proveedor.id, correo, usuario)
        return True
    except DatabaseError:
        logger.exception('No se pudo recordar el correo %s del proveedor %s',
                         correo, proveedor.id)
        return False


def correo_modulo_requerimientos():
    """El correo FIJO del módulo y de dónde sale: ``(correo, origen)``.

    `origen` es 'configurado' (lo guardó un administrador en la pantalla),
    'entorno' (variable REQUERIMIENTOS_CORREO_COPIA, el mecanismo anterior) o
    '' si no hay ninguno.
    """
    try:
        with transaction.atomic():
            config = (ConfiguracionRequerimientos.objects
                      .filter(pk=1).only('correo_modulo').first())
    except DatabaseError:
        config = None
    if config and (config.correo_modulo or '').strip():
        return config.correo_modulo.strip(), 'configurado'
    entorno = os.environ.get('REQUERIMIENTOS_CORREO_COPIA', '').strip()
    if entorno:
        return entorno, 'entorno'
    return '', ''
