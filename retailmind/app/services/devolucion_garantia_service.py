"""
Servicio de Devolución de Dinero por Garantía (function-based, estilo
`giftcard_service.py`).

Flujo de un solo actor (jefe_local/administrador): busca el DTE con el
problema, resuelve/crea el cliente real como Empresa, y genera la Nota de
Crédito en el mismo paso. NO reutiliza `CambioDevolucion` (flujo de cambio de
producto por otro) ni `Requerimiento` (flujo de reclamo a proveedor externo)
— es un dominio propio, más simple, exclusivo para devolución de DINERO.

La creación del DTE tipo NC reusa el mismo patrón que
`generar_nc_devolucion` (views_modulo_ventas.py) pero sin tocar esa función,
para no afectar el módulo de Cambios y Devoluciones.
"""
import json
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Dte_Detalle_Pago, Empresa,
    DevolucionGarantia, DevolucionGarantiaDetalle,
)

logger = logging.getLogger('app')


class DevolucionGarantiaError(Exception):
    """Error de negocio (DTE no encontrado, producto ya devuelto, RUT inválido, etc.)."""


TIPOS_DTE_VENTA_VALIDOS = ['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA', 'BOLETA PAPEL']


def buscar_dte_para_devolucion(folio, sucursal=None):
    """
    Busca una boleta/factura de venta por folio (número de documento).
    Si `sucursal` viene, restringe a esa sucursal; si no, busca en todas
    (patrón inspirado en `buscar_ticket_por_folio` de Requerimientos).

    Devuelve el Dte (con sus dte_productos precargados) o lanza
    DevolucionGarantiaError si no existe / no es un documento de venta válido.
    """
    try:
        folio_num = int(folio)
    except (TypeError, ValueError):
        raise DevolucionGarantiaError('Folio inválido.')

    qs = Dte.objects.select_related('receptor', 'sucursal').prefetch_related(
        'dte_productos__productoTalla__producto'
    ).filter(
        numero_documento=folio_num,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        tipo_documento__in=TIPOS_DTE_VENTA_VALIDOS,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
    )
    if sucursal is not None:
        qs = qs.filter(sucursal=sucursal)

    dte = qs.order_by('-id').first()
    if not dte:
        raise DevolucionGarantiaError(f'No se encontró boleta/factura de venta #{folio_num}.')

    return dte


def cantidad_disponible_para_devolucion(dte_producto):
    """
    Cantidad de la línea `dte_producto` que aún no se ha devuelto por
    garantía (cruza con DevolucionGarantiaDetalle ya existentes, mismo
    control de duplicados que usa el módulo de Cambios y Devoluciones).
    """
    ya_devuelta = DevolucionGarantiaDetalle.objects.filter(
        dte_producto=dte_producto,
        devolucion__estado__in=['REGISTRADA', 'NC_GENERADA'],
    ).aggregate(total=Sum('cantidad'))['total'] or 0
    return max(0, dte_producto.stock - ya_devuelta)


def resolver_o_crear_receptor(*, cliente_id=None, rut='', nombre='', giro='',
                               direccion='', comuna='', ciudad='',
                               email='', telefono=''):
    """
    Busca una Empresa (el único modelo válido como receptor de un DTE en
    este sistema) por `cliente_id` o `rut`; si no existe la crea con los
    datos dados. Lógica inspirada en `guardar_cliente_pos`
    (views_modulo_ventas.py), extraída/adaptada aquí para no importar una
    vista desde un service.

    A diferencia de `guardar_cliente_pos` (que solo formatea el RUT), esta
    función SÍ valida el dígito verificador antes de crear una Empresa
    nueva — es el requisito central de este módulo: la NC debe quedar con
    un RUT real y válido, no uno mal tipeado. No revalida el RUT de una
    Empresa ya existente en BD (podría tener datos legacy imperfectos que
    no corresponde romper aquí).

    Requiere como mínimo rut + nombre para crear. Lanza
    DevolucionGarantiaError si no hay datos suficientes o el RUT es inválido.
    """
    from .fidelizacion_service import es_rut_empresa

    def _formatear_rut(valor):
        limpio = ''.join(c for c in (valor or '') if c.isdigit() or c.lower() == 'k')
        if len(limpio) < 2:
            return valor
        return f"{limpio[:-1]}-{limpio[-1]}"

    nombre = (nombre or '').strip()
    rut_raw = (rut or '').strip()
    rut_norm = _formatear_rut(rut_raw) if rut_raw else ''

    empresa = None
    if cliente_id:
        empresa = Empresa.objects.filter(id=cliente_id).first()

    if empresa is None and rut_norm:
        empresa = Empresa.objects.filter(
            Q(rut__iexact=rut_norm) | Q(rut__iexact=rut_raw)
        ).order_by('-id').first()

    if empresa is not None:
        # El propósito central de este módulo es que la NC quede con un
        # cliente real, no con el receptor genérico de consumidor final —
        # aunque exista una Empresa así en la BD (heredada de boletas
        # antiguas), no se acepta como receptor de una devolución nueva.
        rut_empresa_limpio = (empresa.rut or '').replace('.', '')
        if rut_empresa_limpio in ('66666666-6', '666666666'):
            raise DevolucionGarantiaError(
                'El RUT ingresado corresponde a "Consumidor Final" (genérico). '
                'Ingrese el RUT real del cliente que recibe la devolución.'
            )
        # Actualizar solo si llegan valores nuevos (no pisar datos buenos con vacío).
        if nombre:
            empresa.nombre = nombre
            empresa.razon_social = nombre
        if giro:
            empresa.giro = giro
        if direccion:
            empresa.direccion = direccion
        if comuna:
            empresa.comuna = comuna
        if ciudad:
            empresa.ciudad = ciudad
        if email:
            empresa.correoVendedor = email
        if telefono:
            empresa.contacto1 = telefono
        empresa.save()
        return empresa

    if not rut_norm or not nombre:
        raise DevolucionGarantiaError(
            'Debe indicar RUT y nombre/razón social del cliente para registrar el receptor.'
        )

    from app.models.base import validar_rut_chileno
    if not validar_rut_chileno(rut_norm):
        raise DevolucionGarantiaError(f'RUT inválido: {rut_norm}. Verifique el dígito verificador.')

    es_juridica = es_rut_empresa(rut_norm)
    if es_juridica and not (giro and direccion and comuna and ciudad):
        raise DevolucionGarantiaError(
            'Para un cliente con RUT de empresa, giro/dirección/comuna/ciudad son obligatorios.'
        )

    empresa = Empresa.objects.create(
        nombre=nombre,
        rut=rut_norm,
        nombre_fantasia=nombre,
        razon_social=nombre,
        giro=giro or '',
        direccion=direccion or '',
        comuna=comuna or '',
        ciudad=ciudad or '',
        esProveedor=False,
        correoVendedor=email or '',
        correoIntercambio='',
        correoAdministrador='',
        contacto1=telefono or '',
        contacto2='',
    )
    return empresa


@transaction.atomic
def generar_devolucion_garantia(*, dte_original, sucursal, receptor, motivo,
                                 usuario, detalles, requerimiento=None):
    """
    Registra la devolución de garantía y genera la Nota de Crédito.

    `detalles`: lista de dicts {dte_producto_id, cantidad, precio_unitario}.
    `requerimiento`: instancia opcional de `Requerimiento` (app.models.requerimientos)
        cuando la devolución se generó desde el puente de UI en el detalle de un
        requerimiento ya aprobado/rechazado por el proveedor. Si viene, se vincula
        vía `Requerimiento.devolucion_garantia` y se registra en su historial.

    Devuelve (devolucion, nc, contenido_txt_o_None).
    """
    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_documentos import generar_txt_nota_credito_acepta, limpiar_texto

    if not detalles:
        raise DevolucionGarantiaError('Debe indicar al menos un producto a devolver.')

    lineas_validadas = []
    monto_total = Decimal('0')
    for item in detalles:
        try:
            # select_for_update: bloquea la línea del DTE mientras se valida
            # la cantidad disponible, para que dos devoluciones concurrentes
            # del mismo producto no puedan pasar ambas la validación antes
            # de que cualquiera confirme su DevolucionGarantiaDetalle.
            dte_producto = Dte_Productos.objects.select_related('productoTalla__producto').select_for_update().get(
                id=item['dte_producto_id'], dte=dte_original,
            )
        except Dte_Productos.DoesNotExist:
            raise DevolucionGarantiaError('Uno de los productos no pertenece a la boleta indicada.')

        cantidad = int(item.get('cantidad') or 0)
        if cantidad <= 0:
            raise DevolucionGarantiaError('La cantidad a devolver debe ser mayor a cero.')

        disponible = cantidad_disponible_para_devolucion(dte_producto)
        if cantidad > disponible:
            raise DevolucionGarantiaError(
                f'Solo quedan {disponible} unidades disponibles para devolver de '
                f'{dte_producto.descripcion or dte_producto.productoTalla}.'
            )

        precio_unitario = Decimal(str(item.get('precio_unitario') or dte_producto.precio))
        subtotal = precio_unitario * cantidad
        monto_total += subtotal
        lineas_validadas.append({
            'dte_producto': dte_producto,
            'cantidad': cantidad,
            'precio_unitario': precio_unitario,
            'subtotal': subtotal,
        })

    # === Número de operación ===
    # select_for_update() sobre el rango del prefijo: dos devoluciones
    # concurrentes de la misma sucursal/mes deben serializarse aquí, si no
    # ambas pueden calcular el mismo correlativo y la segunda falla contra
    # el unique=True de numero_operacion.
    fecha = timezone.now()
    prefijo = f"DG-{sucursal.id}-{fecha.strftime('%Y%m')}"
    ultimo = DevolucionGarantia.objects.select_for_update().filter(
        numero_operacion__startswith=prefijo
    ).order_by('-numero_operacion').first()
    correlativo_local = 1
    if ultimo:
        try:
            correlativo_local = int(ultimo.numero_operacion.split('-')[-1]) + 1
        except ValueError:
            correlativo_local = 1
    numero_operacion = f"{prefijo}-{correlativo_local:04d}"

    devolucion = DevolucionGarantia.objects.create(
        dte_original=dte_original,
        sucursal=sucursal,
        receptor=receptor,
        motivo=motivo or 'Garantía aprobada',
        autorizado_por=usuario,
        estado='REGISTRADA',
        monto_total=monto_total,
        numero_operacion=numero_operacion,
    )

    for linea in lineas_validadas:
        DevolucionGarantiaDetalle.objects.create(
            devolucion=devolucion,
            dte_producto=linea['dte_producto'],
            cantidad=linea['cantidad'],
            precio_unitario=linea['precio_unitario'],
            subtotal=linea['subtotal'],
        )

    # === Generar la Nota de Crédito (DTE 61) ===
    # Dte_Productos.precio cambia de significado según el documento (ver
    # Dte.calcular_totales en app/models/dte.py): en boleta es IVA-incluido,
    # en factura es neto. monto_total viene de sumar ese precio tal cual, así
    # que hay que aplicar la misma distinción al derivar neto/IVA para la NC.
    es_boleta_original = 'BOLETA' in dte_original.tipo_documento
    if es_boleta_original:
        monto_con_iva = int(monto_total)
        monto_neto = int(round(monto_con_iva / Decimal('1.19')))
        iva = monto_con_iva - monto_neto
    else:
        monto_neto = int(monto_total)
        iva = int(round(monto_neto * Decimal('0.19')))
        monto_con_iva = monto_neto + iva

    numero_nc = obtener_siguiente_correlativo(sucursal, 'NOTA DE CREDITO')

    tipo_sii_original = 33 if 'FACTURA' in dte_original.tipo_documento else 39
    referencias_json = json.dumps([{
        'tipo_documento': tipo_sii_original,
        'folio': str(dte_original.numero_documento),
        'fecha': dte_original.fecha_emision.strftime('%Y-%m-%d'),
        'razon': '1',
    }])

    nc = Dte.objects.create(
        emisor=dte_original.emisor,
        receptor=receptor,
        tipo_documento='NOTA DE CREDITO',
        numero_documento=numero_nc,
        monto_neto=monto_neto,
        monto_con_iva=monto_con_iva,
        descuento=0,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=0,
        unidades_productos=sum(l['cantidad'] for l in lineas_validadas),
        estado_dte='EMITIDO',
        estado_pago='PAGADO',
        tipo_transaccion='DEVOLUCION',
        responsable=usuario.username,
        sucursal=sucursal,
        hora=timezone.localtime().time(),
        es_nota_credito=True,
        documento_afectado=dte_original,
        motivo_nc=f"Devolución por Garantía {devolucion.numero_operacion}. Motivo: {motivo or 'Garantía aprobada'}",
        referencias=referencias_json,
    )

    for linea in lineas_validadas:
        pt = linea['dte_producto'].productoTalla
        Dte_Productos.objects.create(
            dte=nc,
            productoTalla=pt,
            descripcion=pt.producto.articulo if pt and pt.producto else linea['dte_producto'].descripcion,
            costo=0,
            sobreprecio=0,
            precio=int(linea['precio_unitario']),
            stock=linea['cantidad'],
            activo=True,
        )

    Dte_Detalle_Pago.objects.create(
        dte=nc,
        metodo_pago='EFECTIVO',
        monto=monto_con_iva,
    )

    devolucion.nota_credito = nc
    devolucion.estado = 'NC_GENERADA'
    devolucion.save(update_fields=['nota_credito', 'estado', 'updated_at'])

    if requerimiento is not None:
        from app.models import HistorialRequerimiento
        requerimiento.devolucion_garantia = devolucion
        requerimiento.save(update_fields=['devolucion_garantia'])
        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='DEVOLUCION_GARANTIA_GENERADA',
            estado_anterior=requerimiento.estado,
            estado_nuevo=requerimiento.estado,
            comentario=(
                f"Devolución de dinero {devolucion.numero_operacion} generada "
                f"(NC #{nc.numero_documento}, ${monto_con_iva:,.0f})."
            ),
            usuario=usuario,
        )

    contenido_txt = None
    try:
        empresa = dte_original.emisor
        detalle_txt = []
        for linea in lineas_validadas:
            pt = linea['dte_producto'].productoTalla
            nombre_articulo = pt.producto.articulo if pt and pt.producto else linea['dte_producto'].descripcion
            detalle_txt.append({
                'nombre': limpiar_texto(nombre_articulo or ''),
                'descripcion': '',
                'cantidad': linea['cantidad'],
                'unidad': 'UN',
                'precio_unitario': int(linea['precio_unitario']),
                'descuento_pct': 0,
                'monto_descuento': 0,
                'monto_item': int(linea['subtotal']),
                'codigo': limpiar_texto(pt.sku if pt else ''),
            })

        datos_txt = {
            'documento': {
                'tipo_documento': 61,
                'folio': nc.numero_documento,
                'fecha_emision': nc.fecha_emision.strftime('%Y-%m-%d'),
                'fecha_vencimiento': nc.fecha_vencimiento.strftime('%Y-%m-%d'),
                'forma_pago': 1,
                'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S'),
            },
            'emisor': {
                'rut': empresa.rut,
                'razon_social': limpiar_texto(empresa.razon_social or ''),
                'giro': limpiar_texto(empresa.giro or ''),
                'acteco': empresa.acteco or '',
                'direccion': limpiar_texto((sucursal.direccion if sucursal else '') or empresa.direccion or ''),
                'comuna': limpiar_texto((sucursal.comuna if sucursal else '') or empresa.comuna or ''),
                'ciudad': limpiar_texto((sucursal.ciudad if sucursal else '') or empresa.ciudad or ''),
                'codigo_vendedor': limpiar_texto(usuario.username or 'USUARIO'),
                'sucursal': limpiar_texto(sucursal.alias if sucursal else ''),
                'telefono': empresa.contacto1 or '',
            },
            'receptor': {
                'rut': receptor.rut,
                'razon_social': limpiar_texto(receptor.razon_social or receptor.nombre or ''),
                'giro': limpiar_texto(receptor.giro or ''),
                'direccion': limpiar_texto(receptor.direccion or ''),
                'comuna': limpiar_texto(receptor.comuna or ''),
                'ciudad': limpiar_texto(receptor.ciudad or ''),
            },
            'totales': {
                'monto_neto': monto_neto,
                'monto_exento': 0,
                'tasa_iva': 19,
                'iva': iva,
                'monto_total': monto_con_iva,
                'descuento_global': 0,
            },
            'detalle': detalle_txt,
            'referencias': json.loads(referencias_json),
        }

        contenido_txt = generar_txt_nota_credito_acepta(datos_txt)

        import os
        ruta_nc = os.path.join('MEDIA', 'documentos_electronicos', 'nc')
        os.makedirs(ruta_nc, exist_ok=True)
        nombre_archivo = f"NC_61_{numero_nc}_{nc.fecha_emision.strftime('%Y%m%d')}.txt"
        with open(os.path.join(ruta_nc, nombre_archivo), 'w', encoding='utf-8') as f:
            f.write(contenido_txt)
    except Exception:
        logger.exception(
            "Error generando TXT Acepta para NC #%s (DevolucionGarantia %s)",
            numero_nc, devolucion.numero_operacion,
        )
        contenido_txt = None

    return devolucion, nc, contenido_txt
