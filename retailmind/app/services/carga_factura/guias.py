"""
Guías de talla que usan las facturas: revisar que existan, que la INFANTIL
tenga la Y en las juveniles y que estén todas las tallas de las facturas; y
crearlas o ajustarlas.

Las tallas de los productos que YA usan una guía no se tocan: el ajuste solo
cambia la guía (que también sale por la API externa al ecommerce).
"""
import json
import re
from decimal import Decimal
from pathlib import Path

from django.db.models import Max

from app.models import AtributoOpcion, GuiaTalla, GuiaTallaItem, GuiaTallaProducto, Producto

from .perfiles import perfil_para

_RE_TALLA = re.compile(r'^(\d+(?:[.,]\d+)?)\s*([CY]?)$')


def clave(talla):
    """(número, sufijo) de una talla US: '1.5Y' → ('1.5', 'Y'), '11C' → ('11', 'C')."""
    s = str(talla or '').strip().upper()
    m = _RE_TALLA.match(s)
    if not m:
        return (s, '')
    return (format(Decimal(m.group(1).replace(',', '.')).normalize(), 'f'), m.group(2))


def clave_busqueda(talla):
    """Para buscar una talla en la guía: la C separa bebé de adulto; la Y no
    cambia la talla ('1.5Y' es la misma fila que '1.5')."""
    num, suf = clave(talla)
    return (num, 'C' if suf == 'C' else '')


def referencia(marca, nombre):
    """Tabla del perfil de la marca para crear la guía `nombre`, o None."""
    return perfil_para(marca).referencia_guias.get(str(nombre).strip().upper())


def guias_requeridas(facturas):
    """{(marca, NOMBRE GUÍA): {'nombre', 'tipos', 'tallas'}} de las facturas (dicts JSON)."""
    requeridas = {}
    for data in facturas:
        perfil = perfil_para(data.get('marca'))
        guias = {str(k).upper(): v
                 for k, v in (data.get('guias_talla') or perfil.guias or {}).items()}
        if not guias:
            continue
        for linea in data['lineas']:
            marca = (linea.get('marca') or data.get('marca') or '').strip().upper()
            tallas = [str(t).strip().upper() for t in linea.get('tallas') or {}]
            genero = str(linea.get('genero') or '').upper()
            if linea.get('guia'):
                tipo, nombre = 'LINEA', linea['guia']
            elif (any(t[-1:] in perfil.sufijos_nino for t in tallas)
                  or genero in ('NIÑO', 'NIÑA', 'NINO', 'NINA')):
                tipo, nombre = 'INFANTIL', guias.get('INFANTIL')
            else:
                tipo, nombre = genero, guias.get(genero) or guias.get('DEFAULT')
            if not nombre:
                continue
            req = requeridas.setdefault((marca, nombre.strip().upper()),
                                        {'nombre': nombre.strip(), 'tipos': set(), 'tallas': {}})
            req['tipos'].add(tipo)
            for t in tallas:
                req['tallas'].setdefault(clave(t), t)
    return requeridas


def leer_facturas(rutas):
    return [json.loads(Path(r).read_text(encoding='utf-8')) for r in rutas]


def planificar_guia(marca, req):
    nombre = req['nombre']
    plan = {'marca': marca, 'nombre': nombre, 'guia': None, 'crear': None,
            'agregar_sufijo': [], 'agregar_filas': [], 'faltan': [], 'errores': [],
            'avisos': [], 'es_infantil': 'INFANTIL' in req['tipos']}
    ref = referencia(marca, nombre)
    guias = list(GuiaTalla.objects.filter(marca__valor__iexact=marca, nombre__iexact=nombre)
                 .select_related('marca').order_by('id'))
    if len(guias) > 1:
        plan['errores'].append(f'hay {len(guias)} guías «{nombre}» para {marca} '
                               f'(ids {", ".join(str(g.id) for g in guias)}): deja una sola')
        return plan
    if not guias:
        opcion = (AtributoOpcion.objects.filter(atributo__nombre__iexact='Marca', valor__iexact=marca)
                  .order_by('id').first())
        if opcion is None:
            plan['errores'].append(f'no existe la marca {marca}')
        elif ref is None:
            otras = ', '.join(GuiaTalla.objects.filter(marca__valor__iexact=marca)
                              .values_list('nombre', flat=True)) or 'ninguna'
            plan['errores'].append(f'no existe la guía «{nombre}» y no tengo tabla para crearla '
                                   f'(guías de {marca}: {otras})')
        else:
            plan['crear'] = (opcion, ref)
            en_ref = {clave_busqueda(r[2]) for r in ref}
            plan['faltan'] = sorted(t for c, t in req['tallas'].items()
                                    if clave_busqueda(t) not in en_ref)
            if plan['faltan']:
                plan['errores'].append(f'la tabla para crearla no tiene estas tallas de las '
                                       f'facturas: {", ".join(plan["faltan"])}')
        return plan

    guia = plan['guia'] = guias[0]
    items = list(guia.items.order_by('orden', 'id'))
    plan['items'] = items
    plan['usan'] = (Producto.objects.filter(guia_talla=guia).count()
                    + GuiaTallaProducto.objects.filter(guia=guia).count())

    # Juveniles sin Y (solo en la guía INFANTIL).
    if plan['es_infantil']:
        for it in items:
            num, suf = clave(it.us)
            if it.us and not suf and _RE_TALLA.match(str(it.us).strip()):
                plan['agregar_sufijo'].append((it, f'{num}Y'))

    # Tallas de las facturas que no están en la guía (C distingue bebé; Y no).
    en_guia = {clave_busqueda(it.us) for it in items}
    ref_por_clave = {clave_busqueda(fila[2]): fila for fila in ref or []}
    for _c, texto in sorted(req['tallas'].items()):
        clave_t = clave_busqueda(texto)
        if clave_t in en_guia:
            continue
        if clave_t in ref_por_clave:
            plan['agregar_filas'].append(ref_por_clave[clave_t])
        else:
            plan['faltan'].append(texto)
    if plan['faltan']:
        plan['errores'].append(f'tallas de las facturas que no están en la guía y no sé sus '
                               f'equivalencias: {", ".join(plan["faltan"])} — agrégalas a mano')
    return plan


def tiene_cambios(plan):
    return bool(plan['crear'] or plan['agregar_sufijo'] or plan['agregar_filas'])


def aplicar_guia(plan):
    """Crea o ajusta la guía. Devuelve el texto de lo que hizo."""
    if plan['crear']:
        opcion, ref = plan['crear']
        orden = (GuiaTalla.objects.filter(marca=opcion).aggregate(m=Max('orden'))['m'] or 0) + 1
        guia = GuiaTalla.objects.create(marca=opcion, nombre=plan['nombre'], orden=orden)
        GuiaTallaItem.objects.bulk_create([
            GuiaTallaItem(guia=guia, orden=o, cl=cl, us=us, eu=eu, uk=uk, br=br, cm=cm)
            for o, cl, us, eu, uk, br, cm in ref])
        return f'  creada «{guia.nombre}» id {guia.id} ({len(ref)} tallas)'
    guia = plan['guia']
    for it, nuevo in plan['agregar_sufijo']:
        it.us = nuevo
        it.save(update_fields=['us'])
    if plan['agregar_filas']:
        base = (guia.items.aggregate(m=Max('orden'))['m'] or 0) + 1
        GuiaTallaItem.objects.bulk_create([
            GuiaTallaItem(guia=guia, orden=base + i, cl=cl, us=us, eu=eu, uk=uk, br=br, cm=cm)
            for i, (_o, cl, us, eu, uk, br, cm) in enumerate(plan['agregar_filas'])])
    return (f'  ajustada «{guia.nombre}» id {guia.id}: {len(plan["agregar_sufijo"])} con Y, '
            f'{len(plan["agregar_filas"])} talla(s) agregadas')
