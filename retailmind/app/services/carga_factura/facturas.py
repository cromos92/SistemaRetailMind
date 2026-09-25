"""
Factura de compra transcrita a JSON: lectura, DTE al que se asocia, bodega,
usuario del ingreso y margen de sobreprecio de la bodega.
"""
import glob
import json
import re
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model

from app.models import Dte, EmpresaUser, Sucursal

from .perfiles import perfil_para


class ErrorCarga(Exception):
    """Error que impide planificar o cargar una factura (mensaje para el usuario)."""


def archivos_de_patrones(patrones):
    """Rutas de los JSON: acepta comodines ('compras/facturas/EQUINOX_*.json')."""
    rutas = []
    for patron in patrones:
        encontrados = sorted(glob.glob(patron)) if any(c in patron for c in '*?[') else [patron]
        if not encontrados:
            raise ErrorCarga(f'Ningún archivo calza con {patron}')
        rutas.extend(encontrados)
    return rutas


def variantes_rut(rut):
    """'77402098-5' → {'77402098-5', '77.402.098-5', ...} (el RUT se guarda de varias formas)."""
    limpio = re.sub(r'[^0-9Kk]', '', str(rut))
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    return {str(rut).strip(), f'{cuerpo}-{dv}', f'{cuerpo}{dv}',
            f'{int(cuerpo):,}'.replace(',', '.') + f'-{dv}', f'{cuerpo}-{dv.lower()}'}


def resolver_usuario(username=None):
    """Usuario del ingreso: el pedido, o 'sistema', o el primer superusuario."""
    User = get_user_model()
    if username:
        user = User.objects.filter(username=username).first()
        if user is None:
            raise ErrorCarga(f'No existe el usuario {username!r}')
        return user
    user = (User.objects.filter(username__iexact='sistema', is_active=True).first()
            or User.objects.filter(is_superuser=True, is_active=True).order_by('id').first())
    if user is None:
        raise ErrorCarga('No hay usuario "sistema" ni superusuario activo: usa --usuario')
    return user


def resolver_dte(data, dte_id=None, nombre=''):
    """DTE de la factura: por "dte_id" o por folio + RUT del emisor (solo facturas)."""
    qs = Dte.objects.select_related('emisor', 'receptor')
    if dte_id:
        dte = qs.filter(id=dte_id).first()
        if dte is None:
            raise ErrorCarga(f'{nombre}: no existe el DTE id={dte_id}')
        return dte
    candidatos = list(qs.filter(numero_documento=data['folio'],
                                emisor__rut__in=variantes_rut(data['proveedor_rut'])))
    # Solo facturas: una NC o una guía del mismo proveedor puede tener el
    # mismo número. Para colgar de otro tipo hay que dar el "dte_id".
    facturas = [d for d in candidatos
                if 'FACTURA' in str(d.tipo_documento or '').upper()
                and not getattr(d, 'es_nota_credito', False)]
    compras = [d for d in facturas if d.tipo_transaccion == 'COMPRA']
    dtes = compras or facturas
    if not dtes:
        otros = ', '.join(f'id={d.id} {d.tipo_documento} {d.tipo_transaccion}' for d in candidatos)
        raise ErrorCarga(
            f'{nombre}: no está en el sistema la FACTURA {data["folio"]} del RUT '
            f'{data["proveedor_rut"]}'
            + (f' (con ese número solo hay: {otros})' if otros else '')
            + '. Si la creaste con otro proveedor o tipo, pon su "dte_id" en el JSON.')
    if len(dtes) > 1:
        detalle = ', '.join(f'id={d.id} ({d.emisor.nombre}, {d.fecha_emision}, '
                            f'{d.tipo_transaccion})' for d in dtes)
        raise ErrorCarga(f'{nombre}: el folio {data["folio"]} calza con varios DTE: '
                         f'{detalle}. Pon el correcto como "dte_id" en el JSON.')
    return dtes[0]


def margen_sobreprecio(user, sucursal):
    """% de sobreprecio que usaría el modal en esa bodega (/app/margenes_usuario/).

    Si el usuario no tiene márgenes ahí, se toma el de cualquier usuario de la
    bodega que sí los tenga configurados.
    """
    eu = (EmpresaUser.objects.filter(user=user, sucursal=sucursal, status=True)
          .exclude(margenSobreprecio__isnull=True).first()
          or EmpresaUser.objects.filter(sucursal=sucursal, status=True, margenSobreprecio__gt=0)
          .order_by('id').first())
    return Decimal(str(eu.margenSobreprecio)) if eu else Decimal('10')


def leer_factura(ruta, user, dte_id=None, margen=None):
    """Factura lista para planificar: {'ruta','data','sucursal','dte','margen','perfil'}."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorCarga(f'No existe el archivo {ruta}')
    with ruta.open(encoding='utf-8') as fh:
        data = json.load(fh)
    return factura_desde_datos(data, user, ruta=ruta, dte_id=dte_id, margen=margen)


def factura_desde_datos(data, user, ruta=None, dte_id=None, margen=None):
    """Igual que leer_factura() pero desde un dict ya cargado (p.ej. extraído del PDF)."""
    nombre = Path(ruta).name if ruta else f'factura {data.get("folio")}'
    sucursal = Sucursal.objects.select_related('empresa').filter(
        alias__iexact=data['sucursal']).first()
    if sucursal is None:
        raise ErrorCarga(f'{nombre}: no existe la sucursal {data["sucursal"]!r}')
    dte = resolver_dte(data, dte_id or data.get('dte_id'), nombre)
    if margen is None:
        margen = margen_sobreprecio(user, sucursal)
    return {'ruta': Path(ruta) if ruta else Path(nombre), 'data': data, 'sucursal': sucursal,
            'dte': dte, 'margen': margen, 'perfil': perfil_para(data.get('marca'))}
