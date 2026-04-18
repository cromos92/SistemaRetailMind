from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Agrega el campo `origen_pago` a TicketDetallePago para distinguir
    explícitamente pagos manuales (voucher digitado) de pagos ejecutados
    por el SDK Transbank (POS integrado), aunque ambos compartan el mismo
    `metodo_pago` (TBK_DEBITO_POS, TBK_CREDITO_POS, TBK_POS_INTEGRADO).

    No modifica registros históricos: queda NULL en todos los pagos
    existentes (interpretable como "desconocido/legacy"). A partir de esta
    migración los nuevos pagos se etiquetan en el momento de creación.
    """

    dependencies = [
        ('app', '0099_add_excluir_de_analitica'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketdetallepago',
            name='origen_pago',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=20,
                choices=[
                    ('MANUAL', 'Ingreso Manual (voucher digitado)'),
                    ('POS_INTEGRADO', 'POS Integrado (SDK Transbank)'),
                    ('POS_WEB', 'POS Web / Webpay'),
                    ('EXTERNO', 'Sistema Externo'),
                ],
                help_text=(
                    "Origen/canal del pago. NULL = histórico (pre-migración). "
                    "Permite distinguir un pago con voucher digitado a mano (MANUAL) "
                    "de uno ejecutado por el SDK del POS Transbank (POS_INTEGRADO), "
                    "aunque ambos tengan el mismo metodo_pago (TBK_DEBITO_POS, etc.)."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name='ticketdetallepago',
            index=models.Index(
                fields=['metodo_pago', 'origen_pago'],
                name='ticketpago_met_ori_idx',
            ),
        ),
    ]
