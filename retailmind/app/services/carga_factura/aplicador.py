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
from django.db import connection, transaction
from django.test import RequestFactory
from django.utils import timezone

from app.models import Dte, Producto, Producto_Talla

from .planificador import ingresado_contra_dte, ingreso_por_clave

# Responsable que deja el modal en los movimientos (la sesión nunca trae
# 'nombreUsuario', así que la vista cae a 'Sistema').
RESPONSABLE = 'Sistema'

# Espacio del advisory lock de PostgreSQL para "cargar líneas contra un DTE"
# ('CFPD' en ASCII); la segunda clave es el id del DTE.
_LOCK_CARGA_DTE = 0x43465044


class _Revertir(Exception):
    """Deshace la transacción de una línea cuya carga no quedó completa."""


class _YaCargada(_Revertir):
    """La línea entró contra el DTE por otra carga mientras esta se preparaba."""


def _bloquear_dte(dte_id):
    """Serializa hasta el fin de la transacción las cargas de líneas contra un
    mismo DTE: dos sesiones del agente, un doble clic o el comando en paralelo
    esperan su turno y, ya con el lock, vuelven a medir lo ingresado."""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s, %s)',
                        [_LOCK_CARGA_DTE, int(dte_id) % 2147483647])
    else:
        list(Dte.objects.select_for_update().filter(id=dte_id).values_list('id', flat=True))


def _vigilar_ingreso(plan):
    """¿Se revisa esta línea contra otras cargas? Con --forzar (comando) no."""
    return plan.get('ingreso_clave') is not None and not plan.get('forzar')


def _registro_corrida(factura):
    """Lo que esta corrida ya dejó contra el DTE, con la MISMA forma que
    ingresado_contra_dte(): {'articulo': {art: {bodega: u}},
    'color': {(art, COLOR): {bodega: u}}}."""
    return factura.setdefault('_ingresado_en_corrida', {'articulo': {}, 'color': {}})


def _ingreso_ajeno(plan, factura, ingresado=None):
    """Unidades de esta línea que entraron contra el DTE DESPUÉS de planificar
    y que no puso esta misma corrida (otra carga simultánea). 0 si no hay.

    Lo propio se mide en el mismo espacio que lo ingresado: una línea con
    clave ('articulo', art) ve lo cargado de TODOS los colores de art, así que
    también descuenta lo que la corrida cargó en otros colores de ese código
    (p. ej. una línea ROJO y otra MULTI del mismo código). Dos líneas de la
    misma factura nunca se saltan entre sí."""
    clave = plan.get('ingreso_clave')
    if not _vigilar_ingreso(plan):
        return 0
    if ingresado is None:
        ingresado = ingresado_contra_dte(factura['dte'])
    ahora = sum(ingreso_por_clave(ingresado, clave).values())
    propias = sum(ingreso_por_clave(_registro_corrida(factura), clave).values())
    return max(0, ahora - int(plan.get('ingreso_previo') or 0) - propias)


def _anotar_ingreso(factura, antes, despues):
    """Suma a lo propio de la corrida lo que ESTA línea dejó contra el DTE:
    la diferencia de ingresado_contra_dte() antes y después de la vista,
    medida con el DTE bloqueado (nadie más carga contra él en el medio)."""
    registro = _registro_corrida(factura)
    for nivel in ('articulo', 'color'):
        for clave, bodegas in despues[nivel].items():
            previas = antes[nivel].get(clave, {})
            for alias, unidades in bodegas.items():
                delta = unidades - previas.get(alias, 0)
                if delta > 0:
                    propio = registro[nivel].setdefault(clave, {})
                    propio[alias] = propio.get(alias, 0) + delta


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
    """Carga una línea. Devuelve {'ok': bool, 'error': str|None, 'respuesta': dict|None}
    y, si otra carga ya la ingresó mientras esta se preparaba, 'ya_cargado': True.

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
    vigilar = _vigilar_ingreso(plan)
    antes = despues = None
    try:
        with transaction.atomic():
            # Idempotencia por línea: con el DTE bloqueado se vuelve a medir lo
            # ingresado; si subió por otra carga desde que se planificó, no se
            # ingresa de nuevo.
            _bloquear_dte(factura['dte'].id)
            if vigilar:
                antes = ingresado_contra_dte(factura['dte'])
            ajeno = _ingreso_ajeno(plan, factura, antes) if vigilar else 0
            if ajeno:
                raise _YaCargada(
                    f'mientras se preparaba esta carga entraron {ajeno} u de este código contra '
                    f'el mismo DTE (otra carga en curso): no se ingresó de nuevo')
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
            if vigilar:
                # Todavía con el DTE bloqueado: lo que subió es de esta línea.
                despues = ingresado_contra_dte(factura['dte'])
    except _YaCargada as exc:
        return {'ok': False, 'ya_cargado': True, 'error': str(exc), 'respuesta': None}
    except _Revertir as exc:
        return {'ok': False, 'error': str(exc), 'respuesta': None}
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'respuesta': None}
    if vigilar:
        # Solo tras el commit: una línea deshecha no deja nada propio.
        _anotar_ingreso(factura, antes, despues)
    return {'ok': True, 'error': None, 'respuesta': respuesta}
