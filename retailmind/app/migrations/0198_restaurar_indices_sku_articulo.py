"""Restaura los índices de Producto.articulo y Producto_Talla.sku.

La migración 0109 los creó a propósito. La 0110, autogenerada por
`makemigrations` el 2026-04-02 (porque app/models/catalogo.py nunca declaró los
índices), los revirtió sin que nadie lo notara. Desde entonces producción no los
tiene: cada búsqueda por SKU recorre las ~605.000 filas de Producto_Talla, y cada
resolución de identidad de producto recorre las ~140.000 de Producto.

En PostgreSQL se crean con CREATE INDEX CONCURRENTLY para no bloquear escrituras
mientras se construyen: el POS y el ecommerce siguen vendiendo durante la
migración. Eso obliga a `atomic = False`. En otros backends (SQLite de los tests)
se crean de forma normal, porque CONCURRENTLY no existe fuera de PostgreSQL.

REVERSIÓN: `python manage.py migrate app 0197`. Las operaciones son reversibles y
solo sueltan los índices. No toca ningún dato.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class AddIndexConcurrentlySiPostgres(AddIndexConcurrently):
    """CONCURRENTLY en PostgreSQL; índice normal en el resto.

    Sin esto la suite de tests (SQLite) revienta con
    `add_index() got an unexpected keyword argument 'concurrently'`.
    """

    def _es_postgres(self, schema_editor):
        return schema_editor.connection.vendor == 'postgresql'

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if self._es_postgres(schema_editor):
            return super().database_forwards(app_label, schema_editor, from_state, to_state)
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            schema_editor.add_index(model, self.index)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if self._es_postgres(schema_editor):
            return super().database_backwards(app_label, schema_editor, from_state, to_state)
        model = from_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            schema_editor.remove_index(model, self.index)


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY no puede correr dentro de una transacción.
    atomic = False

    dependencies = [
        ('app', '0197_pedido_ecommerce_picking_tienda'),
    ]

    operations = [
        AddIndexConcurrentlySiPostgres(
            model_name='producto',
            index=models.Index(fields=['articulo'], name='producto_articulo_idx'),
        ),
        AddIndexConcurrentlySiPostgres(
            model_name='producto_talla',
            index=models.Index(fields=['sku'], name='prodtalla_sku_idx'),
        ),
    ]
