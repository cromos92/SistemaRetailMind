"""
Carga de una línea planificada por el MISMO camino que el modal "Crear
Producto Manual" (views.crear_producto_manual): arma su POST y la llama.

Por qué la vista y no escritura directa: la vista deja el ingreso en cinco
tablas (movimiento, lote FIFO, líneas del DTE, recepción y la "Compra
Manual"). Copiar esa lógica aquí sería una segunda versión que se
desincroniza con el tiempo.

Cada línea va en su propia transacción: si la vista falla (o no alcanza a
registrar la compra/DTE, cosa que ella misma se traga) se deshace ENTERA.
"""
import json
from importlib import import_module

from django.conf import settings
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from app.models import Producto, Producto_Talla

# Responsable que deja el modal en los movimientos (la sesión nunca trae
# 'nombreUsuario', así que la vista cae a 'Sistema').
RESPONSABLE = 'Sistema'


class _Revertir(Exception):
    """Deshace la transacción de una línea cuya carga no quedó completa."""


def precios_para_opcion(plan, opcion):
    """(precios, actualizar_precios, sincronizar_otras_bodegas) según la opción.

    Código nuevo: los de la factura. Código existente: [s] stock + costo +
    venta (también en esa variante de otras tiendas) · [c] stock + costo, la
    venta sigue · [t] solo stock con los precios vigentes. [c] y [t] no tocan
    otras tiendas.
    """
    precios, actualizar, sincronizar = plan['factura'], False, False
    if plan['vigentes'] is not None:
        c1 = plan['factura'][0]
        _c0, s0, v0 = plan['vigentes']
        if opcion == 't':
            precios = plan['vigentes']
        elif opcion == 'c':
            precios = (c1, s0, v0)
            actualizar = True
        else:  # 's'
            actualizar, sincronizar = True, True
        actualizar = actualizar and plan['destino'] is not None
    return precios, actualizar, sincronizar


def payload_linea(plan, factura, precios, actualizar, sincronizar):
    """POST que el modal enviaría para esta línea."""
    dte = factura['dte']
    destino = plan['destino']
    payload = {
        'es_manual': 'true',
        'proveedor': str(dte.emisor_id),
        'dte_manual': str(dte.id),
        'articulo': plan['articulo'],
        # En una ficha existente se conserva su descripción (la vista la pisa
        # cuando actualiza precios).
        'descripcion': destino.descripcion if destino else plan['linea'].get('descripcion', ''),
        'atributo1': str(plan['marca'].id),
        'atributo2': str(plan['color'].id),
        'atributo3': str(plan['genero'].id),
        'categoria': str(plan['categoria'].id),
        'tipo_talla': plan['tipo_talla'],
        'guia_talla': str(plan['guia'].id) if plan['guia'] else '',
        'costo': str(precios[0]),
        'sobreprecio': str(precios[1]),
        'precioventa': str(precios[2]),
        'actualizar_precios': 'true' if actualizar else 'false',
        'sincronizar_otras_bodegas': 'true' if sincronizar else 'false',
        'aplicar_todas_bodegas': 'false',
        'talla[]': [t[1] for t in plan['tallas']],
        'stock[]': [str(t[2]) for t in plan['tallas']],
        'sku[]': ['' for _ in plan['tallas']],
        'especialidad[]': [str(o.id) for o in plan['especialidades']],
    }
    if destino is not None:
        payload['producto_id_destino'] = str(destino.id)
        payload['confirmar_duplicado'] = 'true'  # ya validado en el plan
    return payload


def aplicar_linea(plan, factura, user, opcion='s'):
    """Carga una línea. Devuelve {'ok': bool, 'error': str|None, 'respuesta': dict|None}.

    `opcion` solo importa si el código ya existe ('s', 'c' o 't'; ver
    precios_para_opcion). Si falla, no queda nada de la línea.
    """
    from app.views import crear_producto_manual

    sucursal = factura['sucursal']
    precios, actualizar, sincronizar = precios_para_opcion(plan, opcion)
    payload = payload_linea(plan, factura, precios, actualizar, sincronizar)

    request = RequestFactory().post('/app/crear_producto_manual/', data=payload)
    request.user = user
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    request.session['idSucursalActual'] = sucursal.id
    request.session['idEmpresaActual'] = sucursal.empresa_id
    request.session['nombreUsuario'] = RESPONSABLE

    # Todo o nada por línea: la vista no es atómica y, si falla a mitad (o no
    # alcanza a registrar la compra/DTE, cosa que ella misma se traga),
    # quedaría stock sin factura o tallas a medias.
    try:
        with transaction.atomic():
            for _pid, _alias, pt_id, viejo, nuevo in plan.get('renombres', []):
                # .update() no dispara auto_now: updated_at explícito.
                Producto_Talla.objects.filter(id=pt_id, talla=viejo).update(
                    talla=nuevo, updated_at=timezone.now())
            if plan.get('fichas_formato'):
                Producto.objects.filter(id__in=[f.id for f in plan['fichas_formato']]).update(
                    tipo_talla='US', guia_talla=plan['guia'])
            respuesta = json.loads(crear_producto_manual(request).content)
            if not respuesta.get('success'):
                raise _Revertir(respuesta.get('error') or 'la vista respondió error')
            if not respuesta.get('compra_id'):
                raise _Revertir('no se pudo registrar la línea en la compra/DTE')
            # Un error de BD que la vista capturó sin savepoint deja la
            # transacción marcada: al salir se desharía en silencio y aquí se
            # informaría OK.
            if transaction.get_rollback():
                raise _Revertir('la vista tuvo un error de base de datos a mitad de camino')
    except _Revertir as exc:
        return {'ok': False, 'error': str(exc), 'respuesta': None}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'respuesta': None}
    return {'ok': True, 'error': None, 'respuesta': respuesta}
