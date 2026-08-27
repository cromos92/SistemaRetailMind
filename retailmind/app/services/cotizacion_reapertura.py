# -*- coding: utf-8 -*-
"""
Reapertura de cotizaciones facturadas cuyo documento tributario ya no existe.

El problema
-----------
El módulo de cotizaciones NO factura: la transición `VIGENTE → FACTURADA` la
hace `registrar_pagos_ticket` DESPUÉS de emitir el DTE. Cuando ese DTE se
elimina (`eliminar_documento_venta`, soft delete que devuelve el stock) o se
anula por NC total, **la cotización no se toca**: queda `facturada=True`,
`estado=FACTURADA`, `numero_factura=<folio muerto>` y `dte` apuntando a un
documento descartado.

A partir de ahí las cuatro salidas están cerradas:

* `cargar_cotizacion_como_ticket` exige `esta_vigente`, que incluye
  `not facturada` → no se puede volver a facturar.
* `editar_cotizacion` bloquea las facturadas → no se puede corregir ni renovar.
* `anular_cotizacion` bloquea las facturadas → no se puede cerrar.
* `reparar_cotizaciones_zombi` no la detecta: su criterio exige
  `numero_factura` vacío o con prefijo sintético `F-COT`, y acá hay un folio
  numérico real.

Caso que lo disparó: una cotización facturada por error con BOLETA PAPEL, que
había que re-emitir como FACTURA ELECTRONICA.

Qué hace este módulo
--------------------
`evaluar_reapertura()` decide si la cotización se puede reabrir sin descuadrar
inventario (solo lectura, sirve para pintar el botón y para el dry-run del
command). `reabrir_cotizacion()` aplica el cambio bajo lock revalidando los
mismos guards. `registrar_dte_anulado_en_cotizaciones()` deja el rastro en el
historial cuando se anula/elimina un DTE con cotizaciones enlazadas, para que
la pantalla pueda mostrar que el documento ya no respalda nada.

Ningún dato se borra: solo se revierten campos de estado y todo queda en
`Historial_Cotizacion`.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger('app')


# Cuánta validez se le devuelve a una cotización que ya venció, cuando se
# reabre sin pedir un valor explícito. No se conserva la fecha original porque
# reabrir para re-facturar con una validez pasada la deja VENCIDA e infacturable
# (`save()` degrada sola), o sea: reabrir no serviría de nada.
DIAS_VALIDEZ_POR_DEFECTO = 15


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------

def _estado_documento(cotizacion):
    """Describe el DTE enlazado y si habilita la reapertura."""
    dte = cotizacion.dte
    if dte is None:
        return {
            'tiene_dte': False,
            'liberado': True,          # zombi: FACTURADA sin documento real
            'descripcion': 'sin documento enlazado',
            'motivo': 'zombi',
        }

    descartado = bool(getattr(dte, 'descartado', False))
    anulado = (dte.estado_dte or '').upper().strip() == 'ANULADO'
    return {
        'tiene_dte': True,
        'liberado': descartado or anulado,
        'descripcion': (
            f'{dte.tipo_documento} #{dte.numero_documento} '
            f'(estado={dte.estado_dte}, descartado={descartado})'
        ),
        'motivo': 'descartado' if descartado else ('anulado' if anulado else 'vigente'),
        'dte_id': dte.id,
        'tipo_documento': dte.tipo_documento,
        'numero_documento': dte.numero_documento,
    }


def _unidades_despachadas_vivas(cotizacion):
    """Unidades que salieron por DESPACHO_COTIZACION y NO se revirtieron.

    El despacho diferido saca stock con un EGRESO (`cantidad` negativa) y la
    reversa lo devuelve con un INGRESO (positiva), ambos con
    `referencia_externa = numero_cotizacion`. El NETO es lo que sigue afuera:
    si no es 0, reabrir la cotización dejaría stock entregado sin documento que
    lo respalde.
    """
    from app.models import Movimientos_Producto

    neto = (
        Movimientos_Producto.objects
        .filter(
            concepto='DESPACHO_COTIZACION',
            referencia_externa=cotizacion.numero_cotizacion,
        )
        .aggregate(t=Sum('cantidad'))['t'] or 0
    )
    # `neto` es negativo cuando quedó stock afuera; se devuelve en positivo.
    return max(0, -int(neto))


def _tickets_del_dte(dte):
    """Tickets vinculados al DTE, desambiguados por tipo.

    Mismo criterio que `eliminar_documento_venta`: `folio_dte` no es único, así
    que hay que filtrar por el `tipo_dte` esperado (aceptando los legacy con
    tipo en NULL) antes de sacar conclusiones sobre el estado del ticket.
    """
    from app.models import Ticket
    from app.utils_ventas import tipo_ticket_contradice_dte

    if not dte or not dte.numero_documento:
        return []

    candidatos = list(
        Ticket.objects
        .filter(sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento)
        .order_by('id')
    )
    if len(candidatos) > 1:
        # Solo se descarta el candidato cuyo tipo CONTRADICE al del DTE. El
        # default del modelo ('TICKET') es neutro y lo trae el 100% de los
        # tickets con folio en producción.
        filtrados = [
            t for t in candidatos
            if not tipo_ticket_contradice_dte(t.tipo_dte, dte.tipo_documento)
        ]
        if filtrados:
            candidatos = filtrados
    return candidatos


def evaluar_reapertura(cotizacion):
    """¿Se puede devolver esta cotización a VIGENTE? (solo lectura)

    Devuelve::

        {
          'ok': bool,
          'bloqueos': [str, ...],   # razones por las que NO se puede
          'avisos':   [str, ...],   # cosas que la reapertura va a deshacer
          'contexto': {...},        # datos para el log / la UI
        }

    Un bloqueo significa que reabrir descuadraría inventario o borraría un
    documento vivo. Los avisos son efectos esperados que conviene mostrar.
    """
    from app.models import Cotizacion_Empresa

    bloqueos = []
    avisos = []

    esta_facturada = (
        cotizacion.facturada
        or cotizacion.estado == Cotizacion_Empresa.ESTADO_FACTURADA
    )
    if not esta_facturada:
        bloqueos.append(
            f'La cotización está en estado {cotizacion.estado}: no hay nada que reabrir.'
        )

    doc = _estado_documento(cotizacion)
    if doc['tiene_dte'] and not doc['liberado']:
        bloqueos.append(
            f'El documento {doc["descripcion"]} sigue vigente. Elimínelo desde '
            f'"Documentos de ventas" o anúlelo con una Nota de Crédito antes de '
            f'reabrir la cotización.'
        )

    uds_afuera = _unidades_despachadas_vivas(cotizacion)
    if uds_afuera:
        bloqueos.append(
            f'Quedan {uds_afuera} unidad(es) entregadas por despacho diferido sin '
            f'revertir. Revierta esos despachos ("Corregir SKU despachado") antes '
            f'de reabrir: si no, esa mercadería queda entregada sin documento.'
        )

    # El stock de la venta original tiene que haber vuelto. La señal es el
    # ticket anulado: `eliminar_documento_venta` lo deja en ANULADO justo
    # después de reintegrar las líneas.
    tickets_vivos = [
        t for t in _tickets_del_dte(cotizacion.dte) if t.estado != 'ANULADO'
    ]
    if tickets_vivos:
        detalle = ', '.join(
            f'#{t.correlativo} ({t.estado})' for t in tickets_vivos[:5]
        )
        bloqueos.append(
            f'El ticket de la venta sigue vivo: {detalle}. El stock no volvió a '
            f'bodega, así que reabrir la cotización permitiría venderlo dos veces.'
        )

    if cotizacion.despacho_validado:
        avisos.append('Se va a invalidar el OK de despacho del administrador.')

    if cotizacion.fecha_validez and cotizacion.fecha_validez < timezone.localdate():
        avisos.append(
            f'La validez venció el {cotizacion.fecha_validez}: se renovará para '
            f'que la cotización quede realmente facturable.'
        )

    return {
        'ok': not bloqueos,
        'bloqueos': bloqueos,
        'avisos': avisos,
        'contexto': {
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'estado': cotizacion.estado,
            'facturada': cotizacion.facturada,
            'numero_factura': cotizacion.numero_factura,
            'documento': doc,
            'unidades_despachadas_sin_revertir': uds_afuera,
            'tickets_vivos': [t.correlativo for t in tickets_vivos],
        },
    }


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------

def reabrir_cotizacion(cotizacion, usuario, motivo, dias_validez=None):
    """Devuelve la cotización a VIGENTE para poder re-facturarla.

    Revalida los guards de `evaluar_reapertura()` DENTRO de la transacción y
    con `select_for_update`: entre el chequeo del botón y el POST alguien puede
    haber despachado, restaurado el DTE o cobrado el ticket.

    Devuelve `(ok: bool, payload: dict)`. Cuando `ok` es False, `payload`
    trae `bloqueos`.
    """
    from app.models import Cotizacion_Empresa, Historial_Cotizacion

    motivo = (motivo or '').strip()
    if len(motivo) < 5:
        return False, {'bloqueos': [
            'Debe indicar un motivo de al menos 5 caracteres: reabrir una '
            'cotización facturada es una corrección sobre un documento tributario.'
        ]}

    with transaction.atomic():
        # `dte` es nullable: incluirlo en select_related genera un LEFT OUTER
        # JOIN y Postgres rechaza FOR UPDATE sobre el lado nulable de un outer
        # join ("FOR UPDATE cannot be applied to the nullable side of an outer
        # join"). Se accede después, con una consulta aparte.
        bloqueada = (
            Cotizacion_Empresa.objects
            .select_for_update()
            .select_related('sucursal', 'cliente')
            .get(pk=cotizacion.pk)
        )

        evaluacion = evaluar_reapertura(bloqueada)
        if not evaluacion['ok']:
            return False, {
                'bloqueos': evaluacion['bloqueos'],
                'contexto': evaluacion['contexto'],
            }

        datos_anteriores = {
            'estado': bloqueada.estado,
            'facturada': bloqueada.facturada,
            'numero_factura': bloqueada.numero_factura,
            'dte_id': bloqueada.dte_id,
            'fecha_facturacion': (
                bloqueada.fecha_facturacion.isoformat()
                if bloqueada.fecha_facturacion else None
            ),
            'estado_despacho': bloqueada.estado_despacho,
            'despacho_validado': bloqueada.despacho_validado,
            'fecha_validez': (
                bloqueada.fecha_validez.isoformat()
                if bloqueada.fecha_validez else None
            ),
        }
        documento_liberado = _estado_documento(bloqueada)

        # --- Limpiar el vínculo con el documento muerto ---
        bloqueada.facturada = False
        bloqueada.numero_factura = None
        bloqueada.dte = None
        bloqueada.fecha_facturacion = None
        bloqueada.estado_despacho = None
        bloqueada.despacho_validado = False
        bloqueada.despacho_validado_por = None
        bloqueada.fecha_validacion_despacho = None

        # --- Renovar vigencia ---
        # Sin esto `save()` degrada sola a VENCIDA (mira `fecha_validez`) y la
        # cotización reabierta seguiría siendo infacturable.
        hoy = timezone.localdate()
        dias = int(dias_validez) if dias_validez else DIAS_VALIDEZ_POR_DEFECTO
        if dias < 1:
            dias = DIAS_VALIDEZ_POR_DEFECTO
        renovada = False
        if not bloqueada.fecha_validez or bloqueada.fecha_validez < hoy:
            from datetime import timedelta
            bloqueada.fecha_validez = hoy + timedelta(days=dias)
            renovada = True

        bloqueada.estado = Cotizacion_Empresa.ESTADO_VIGENTE
        bloqueada.save()

        # Los ítems que se habían cerrado con SKU post-factura NO se tocan: sus
        # despachos ya fueron revertidos (lo exige el guard), y el flag
        # `sku_asignado_post_factura` es historia auditable de lo que pasó.

        descripcion = (
            f'Cotización REABIERTA para re-facturar. Documento liberado: '
            f'{documento_liberado["descripcion"]}'
            + (f' (folio {datos_anteriores["numero_factura"]})'
               if datos_anteriores['numero_factura'] else '')
            + f'. Motivo: {motivo}'
            + (f' Validez renovada hasta {bloqueada.fecha_validez}.' if renovada else '')
        )

        Historial_Cotizacion.objects.create(
            cotizacion=bloqueada,
            usuario=usuario,
            accion='MODIFICADA',
            descripcion=descripcion,
            datos_anteriores=datos_anteriores,
            datos_nuevos={
                'estado': bloqueada.estado,
                'facturada': False,
                'numero_factura': None,
                'dte_id': None,
                'fecha_validez': bloqueada.fecha_validez.isoformat(),
                'motivo': motivo,
            },
        )

    logger.info(
        'Cotizacion reabierta numero=%s documento_liberado=%s usuario=%s',
        bloqueada.numero_cotizacion,
        documento_liberado['descripcion'],
        getattr(usuario, 'username', usuario),
    )

    return True, {
        'numero_cotizacion': bloqueada.numero_cotizacion,
        'estado': bloqueada.estado,
        'fecha_validez': bloqueada.fecha_validez.isoformat(),
        'validez_renovada': renovada,
        'documento_liberado': documento_liberado,
        'avisos': evaluacion['avisos'],
    }


def registrar_dte_anulado_en_cotizaciones(dte, usuario, motivo, accion_texto):
    """Deja rastro en las cotizaciones cuyo DTE se acaba de anular/eliminar.

    Sin esto la cotización sigue diciendo FACTURADA con un `numero_factura` que
    ya no respalda nada y nadie se enteraba: era el punto ciego que dejaba la
    cotización en un callejón sin salida silencioso.

    NO cambia el estado de la cotización a propósito. La reapertura es una
    decisión explícita de un administrador (puede querer re-emitir, o puede
    querer dejar la venta caída): acá solo se registra el hecho y se devuelve
    el listado para que el llamador lo informe.

    Devuelve la lista de números de cotización afectados.
    """
    from app.models import Historial_Cotizacion

    if dte is None:
        return []

    try:
        cotizaciones = list(dte.cotizaciones.all())
    except Exception:
        logger.exception('No se pudieron leer las cotizaciones del DTE id=%s', dte.id)
        return []

    if not cotizaciones:
        return []

    afectadas = []
    for cot in cotizaciones:
        try:
            Historial_Cotizacion.objects.create(
                cotizacion=cot,
                usuario=usuario,
                accion='MODIFICADA',
                descripcion=(
                    f'El documento que la respaldaba fue {accion_texto}: '
                    f'{dte.tipo_documento} #{dte.numero_documento}. '
                    f'La cotización sigue marcada como FACTURADA y NO se puede '
                    f're-facturar hasta reabrirla.'
                    + (f' Motivo: {motivo}' if motivo else '')
                ),
                datos_nuevos={
                    'dte_id': dte.id,
                    'tipo_documento': dte.tipo_documento,
                    'numero_documento': dte.numero_documento,
                    'accion': accion_texto,
                    'requiere_reapertura': True,
                },
            )
            afectadas.append(cot.numero_cotizacion)
        except Exception:
            # Nunca hacer fallar la anulación del documento por el rastro.
            logger.exception(
                'No se pudo registrar el historial de reapertura pendiente '
                'cotizacion=%s dte=%s', cot.numero_cotizacion, dte.id,
            )

    if afectadas:
        logger.warning(
            'DTE %s #%s %s con %s cotizacion(es) enlazada(s): %s — requieren reapertura',
            dte.tipo_documento, dte.numero_documento, accion_texto,
            len(afectadas), ', '.join(afectadas),
        )
    return afectadas
