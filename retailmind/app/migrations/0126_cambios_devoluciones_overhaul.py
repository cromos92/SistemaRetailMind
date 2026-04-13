"""
Migration: Overhaul del sistema de cambios y devoluciones.
- Nuevos campos de trazabilidad de autorización en CambioDevolucion
- Campos cross-branch en RegistroAutorizacion
- Mejoras a CodigoAutorizacionDinamico para códigos por transacción
- Soporte para cambios por concepto (legacy)
- Campos de escalamiento y revisión gerencial
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0125_populate_ecommerce_substates'),
    ]

    operations = [
        # === CambioDevolucion: Trazabilidad de autorización ===
        migrations.AddField(
            model_name='cambiodevolucion',
            name='autorizado_por_usuario',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Supervisor que autorizó la operación',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cambios_autorizados_excepcion',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='sucursal_autorizador',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Sucursal del supervisor que autorizó',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cambios_autorizados_desde',
                to='app.sucursal',
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='es_autorizacion_cross_branch',
            field=models.BooleanField(default=False, help_text='Si la autorización fue desde otra sucursal'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='es_fuera_de_plazo',
            field=models.BooleanField(default=False, help_text='Si el cambio se realizó fuera del plazo de 30 días'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='dias_fuera_de_plazo',
            field=models.IntegerField(default=0, help_text='Días transcurridos después del plazo límite'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='tipo_cambio_especial',
            field=models.CharField(
                choices=[('NORMAL', 'Normal'), ('FUERA_PLAZO', 'Fuera de Plazo'), ('CONCEPTO', 'Por Concepto'), ('LEGACY', 'Legacy')],
                default='NORMAL', max_length=20,
                help_text='Tipo especial de cambio',
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='registro_autorizacion',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Registro de autorización vinculado',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cambios_autorizados_registro',
                to='app.registroautorizacion',
            ),
        ),

        # === CambioDevolucion: Cambios por concepto (legacy) ===
        migrations.AddField(
            model_name='cambiodevolucion',
            name='es_cambio_concepto',
            field=models.BooleanField(default=False, help_text='Si es un cambio por concepto'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='concepto_descripcion',
            field=models.TextField(blank=True, null=True, help_text='Descripción del concepto del cambio'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='concepto_monto_original',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, help_text='Monto original del concepto'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='documento_referencia_legacy',
            field=models.CharField(blank=True, max_length=100, null=True, help_text='Número de documento del sistema antiguo'),
        ),

        # === CambioDevolucion: Escalamiento y revisión gerencial ===
        migrations.AddField(
            model_name='cambiodevolucion',
            name='requiere_revision_gerencial',
            field=models.BooleanField(default=False, help_text='Si requiere revisión de gerencia'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='revisado_por_gerencia',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Gerente que revisó el cambio',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='cambios_revisados_gerencia',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='fecha_revision_gerencia',
            field=models.DateTimeField(blank=True, null=True, help_text='Fecha de revisión gerencial'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='notas_revision_gerencia',
            field=models.TextField(blank=True, default='', help_text='Notas de la revisión gerencial'),
        ),
        migrations.AddField(
            model_name='cambiodevolucion',
            name='score_riesgo',
            field=models.IntegerField(default=0, help_text='Score de riesgo calculado (0-100)'),
        ),

        # === CambioDevolucionDetalle: Cambios por concepto ===
        migrations.AddField(
            model_name='cambiodevoluciondetalle',
            name='es_linea_concepto',
            field=models.BooleanField(default=False, help_text='Si es una línea de cambio por concepto'),
        ),
        migrations.AddField(
            model_name='cambiodevoluciondetalle',
            name='descripcion_concepto',
            field=models.CharField(blank=True, max_length=500, null=True, help_text='Descripción del ítem por concepto'),
        ),

        # === CodigoAutorizacionDinamico: Soporte por transacción ===
        migrations.AddField(
            model_name='codigoautorizaciondinamico',
            name='tipo_codigo',
            field=models.CharField(
                choices=[('HORARIO', 'Horario'), ('TRANSACCION', 'Por Transacción')],
                default='HORARIO', max_length=20,
                verbose_name='Tipo de código',
            ),
        ),
        migrations.AddField(
            model_name='codigoautorizaciondinamico',
            name='operacion_asociada',
            field=models.CharField(blank=True, max_length=50, null=True, verbose_name='Operación asociada'),
        ),
        migrations.AddField(
            model_name='codigoautorizaciondinamico',
            name='generado_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='codigos_autorizacion_generados',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Supervisor que generó el código',
            ),
        ),

        # === RegistroAutorizacion: Trazabilidad cross-branch ===
        migrations.AddField(
            model_name='registroautorizacion',
            name='usuario_autorizador',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='autorizaciones_otorgadas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Supervisor que autorizó',
            ),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='sucursal_solicitante',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='autorizaciones_solicitadas_sucursal',
                to='app.sucursal',
                verbose_name='Sucursal que solicitó',
            ),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='sucursal_autorizador',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='autorizaciones_otorgadas_sucursal',
                to='app.sucursal',
                verbose_name='Sucursal del autorizador',
            ),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='es_cross_branch',
            field=models.BooleanField(default=False, verbose_name='¿Autorización entre sucursales?'),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='requiere_revision',
            field=models.BooleanField(default=False, verbose_name='¿Requiere revisión gerencial?'),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='revisado_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='autorizaciones_revisadas',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Revisado por',
            ),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='fecha_revision',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de revisión'),
        ),
        migrations.AddField(
            model_name='registroautorizacion',
            name='notas_revision',
            field=models.TextField(blank=True, default='', verbose_name='Notas de revisión'),
        ),

        # === Nuevos índices para consultas de detección de fraude ===
        migrations.AddIndex(
            model_name='cambiodevolucion',
            index=models.Index(fields=['es_fuera_de_plazo', 'fecha_solicitud'], name='idx_cambio_fuera_plazo'),
        ),
        migrations.AddIndex(
            model_name='cambiodevolucion',
            index=models.Index(fields=['es_autorizacion_cross_branch', 'fecha_solicitud'], name='idx_cambio_cross_branch'),
        ),
        migrations.AddIndex(
            model_name='cambiodevolucion',
            index=models.Index(fields=['tipo_cambio_especial', 'fecha_solicitud'], name='idx_cambio_tipo_especial'),
        ),
        migrations.AddIndex(
            model_name='cambiodevolucion',
            index=models.Index(fields=['requiere_revision_gerencial', 'revisado_por_gerencia'], name='idx_cambio_revision_ger'),
        ),
        migrations.AddIndex(
            model_name='registroautorizacion',
            index=models.Index(fields=['es_cross_branch', '-fecha_hora'], name='idx_auth_cross_branch'),
        ),
        migrations.AddIndex(
            model_name='registroautorizacion',
            index=models.Index(fields=['requiere_revision', 'revisado_por'], name='idx_auth_revision'),
        ),

        # === Actualizar tipo_operacion max_length para nuevos tipos ===
        migrations.AlterField(
            model_name='cambiodevolucion',
            name='tipo_operacion',
            field=models.CharField(
                choices=[
                    ('CAMBIO_SIMPLE', 'Cambio Simple'),
                    ('CAMBIO_CON_DIFERENCIA', 'Cambio con Diferencia de Precio'),
                    ('DEVOLUCION_TOTAL', 'Devolución Total'),
                    ('DEVOLUCION_PARCIAL', 'Devolución Parcial'),
                    ('CAMBIO_CONCEPTO', 'Cambio por Concepto'),
                    ('DEVOLUCION_CONCEPTO', 'Devolución por Concepto'),
                ],
                help_text='Tipo de operación realizada',
                max_length=30,
            ),
        ),
    ]
