"""
Data migration: mueve beneficiarios de créditos de Vendedor a Cliente.

1. Para cada CreditoTrabajador cuyo vendedor es INT-* o EXT-*:
   - Crea o busca un Cliente con el mismo RUT/nombre.
   - Asigna CreditoTrabajador.beneficiario al Cliente.
   - Marca tipo_beneficiario.
2. Para créditos con vendedores reales, también crea Cliente y asigna.
3. Elimina vendedores con código INT-*/EXT-* que ya no tienen relaciones.
"""
import re
from django.db import migrations


def _normalizar_rut(rut):
    if not rut:
        return ''
    return re.sub(r'[.\-\s]', '', rut).upper().strip()


def forwards(apps, schema_editor):
    Vendedor = apps.get_model('app', 'Vendedor')
    Cliente = apps.get_model('app', 'Cliente')
    CreditoTrabajador = apps.get_model('app', 'CreditoTrabajador')

    clientes_por_rut = {}
    for c in Cliente.objects.all():
        rut_norm = _normalizar_rut(c.rut)
        if rut_norm:
            clientes_por_rut[rut_norm] = c

    creditos = CreditoTrabajador.objects.select_related('trabajador').filter(
        beneficiario__isnull=True,
        trabajador__isnull=False,
    )

    created = 0
    reused = 0
    for credito in creditos:
        vendedor = credito.trabajador
        if not vendedor:
            continue

        codigo = vendedor.codigo_vendedor or ''
        is_int = codigo.startswith('INT-')
        is_ext = codigo.startswith('EXT-')

        nombre_completo = (vendedor.nombre or '').strip()
        rut = (vendedor.rut or '').strip()
        rut_norm = _normalizar_rut(rut)

        partes = nombre_completo.split(None, 1)
        nombre = partes[0] if partes else nombre_completo
        apellido = partes[1] if len(partes) > 1 else ''

        cliente = None
        if rut_norm:
            cliente = clientes_por_rut.get(rut_norm)

        if not cliente:
            if is_ext:
                tipo_cliente = 'CREDITO_EXTERNO'
            elif is_int:
                tipo_cliente = 'EMPLEADO'
            else:
                tipo_cliente = 'INDIVIDUAL'

            cliente = Cliente.objects.create(
                nombre=nombre or 'Sin nombre',
                apellido=apellido,
                rut=rut or None,
                tipo_cliente=tipo_cliente,
                empresa_id=vendedor.empresa_id,
                activo=True,
            )
            if rut_norm:
                clientes_por_rut[rut_norm] = cliente
            created += 1
        else:
            reused += 1

        credito.beneficiario = cliente
        credito.tipo_beneficiario = 'CLIENTE_EXTERNO' if is_ext else 'EMPLEADO'
        try:
            credito.save(update_fields=['beneficiario_id', 'tipo_beneficiario'])
        except Exception:
            credito.save()

    vendedores_falsos = Vendedor.objects.filter(
        codigo_vendedor__regex=r'^(INT|EXT)-'
    )
    deleted_count = 0
    for v in vendedores_falsos:
        has_tickets = v.vendedor_ticket.exists() if hasattr(v, 'vendedor_ticket') else False
        has_dtes = v.vendedor_dte.exists() if hasattr(v, 'vendedor_dte') else False
        has_cotizaciones = v.cotizacion_set.exists() if hasattr(v, 'cotizacion_set') else False

        if not has_tickets and not has_dtes and not has_cotizaciones:
            v.delete()
            deleted_count += 1

    print(f'  Clientes creados: {created}, reutilizados: {reused}')
    print(f'  Vendedores falsos eliminados: {deleted_count}/{vendedores_falsos.count() + deleted_count}')


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0095_add_beneficiario_cliente_credito'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
