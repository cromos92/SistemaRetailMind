from django.db import models
from .organizacion import Sucursal

TIPO_TALLA_CHOICES = [
    ('CL', 'CL'),
    ('US', 'US'),
    ('EU', 'EU'),
    ('UK', 'UK'),
    ('BR', 'BR'),
    ('CM', 'CM'),
]

class Productos_Atributos(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=250)
    fecha_actualizacion = models.DateField(auto_now=True)
    def __str__(self):
        return f"Productos_Atributos {self.nombre} - {self.descripcion}"
class AtributoOpcion(models.Model):
    atributo = models.ForeignKey(Productos_Atributos, related_name='opciones', on_delete=models.CASCADE)
    valor = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.atributo.nombre}: {self.valor}"

class ProductoAtributoValor(models.Model):
    producto = models.ForeignKey('Producto', on_delete=models.CASCADE, related_name='atributos')
    atributo = models.ForeignKey(Productos_Atributos, on_delete=models.CASCADE)
    opcion = models.ForeignKey(AtributoOpcion, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.producto.articulo} - {self.atributo.nombre}: {self.opcion.valor}"


class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    padre = models.ForeignKey(
        'self', null=True, blank=True,
        related_name='subcategorias',
        on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.nombre}"

    def es_raiz(self):
        return self.padre is None

class GuiaTalla(models.Model):
    marca = models.ForeignKey('AtributoOpcion', on_delete=models.CASCADE, related_name='guia_tallas')  # Cambiado
    nombre = models.CharField(max_length=100)
    orden = models.IntegerField(default=0)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    productos = models.ManyToManyField('Producto', through='GuiaTallaProducto', related_name='guias_de_talla', blank=True)

    def __str__(self):
        return f"{self.marca.valor} - {self.nombre}"




class GuiaTallaItem(models.Model):
    guia = models.ForeignKey(GuiaTalla, on_delete=models.CASCADE, related_name='items')
    cl = models.CharField(max_length=20, blank=True, null=True)
    us = models.CharField(max_length=20, blank=True, null=True)
    eu = models.CharField(max_length=20, blank=True, null=True)
    uk = models.CharField(max_length=20, blank=True, null=True)
    br = models.CharField(max_length=20, blank=True, null=True)
    cm = models.CharField(max_length=20, blank=True, null=True)
    orden = models.IntegerField(default=0)

    def __str__(self):
        return f"Item {self.cl or ''} / {self.us or ''} / {self.cm or ''}"


class GuiaTallaProducto(models.Model):
    guia = models.ForeignKey(GuiaTalla, on_delete=models.CASCADE)
    producto = models.ForeignKey('Producto', on_delete=models.CASCADE)
    fecha_asociacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('guia', 'producto')

class Producto(models.Model):
    articulo      = models.CharField(max_length=200)
    descripcion   = models.CharField(max_length=250)

    # ──────────────────── CAMBIO CLAVE ────────────────────
    #  Ahora cada FK va a AtributoOpcion (Nike, Azul, Mujer…)
    atributo1 = models.ForeignKey(
        AtributoOpcion,                     # ← ya no Productos_Atributos
        related_name='productos_marca',     # usa el related_name que prefieras
        on_delete=models.CASCADE,
        null=True, blank=True               # opcional
    )
    atributo2 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_color',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    atributo3 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_genero',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    atributo4 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_otro',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    # ────────────────── FIN DE CAMBIOS ────────────────────

    categoria = models.ForeignKey(
        Categoria,
        related_name='categoria_productos',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    sucursal       = models.ForeignKey(Sucursal, related_name='sucursal_producto', on_delete=models.CASCADE)
    costo          = models.IntegerField()
    sobreprecio    = models.IntegerField()
    precioventa    = models.IntegerField()
    precioSugerido = models.IntegerField(null=True, blank=True)
    tipo_talla     = models.CharField(max_length=5, choices=TIPO_TALLA_CHOICES, default='CL')
    guia_talla = models.ForeignKey(
        'GuiaTalla',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='productos_principales'
    )
    temporada = models.CharField(max_length=50, blank=True, null=True,
        verbose_name='Temporada', help_text='verano / invierno / transicion')
    anio_temporada = models.IntegerField(null=True, blank=True,
        verbose_name='Año temporada')
    semanas_temporada = models.IntegerField(default=8,
        verbose_name='Semanas temporada',
        help_text='Duración esperada en punto de venta')
    rango_precio = models.CharField(max_length=50, blank=True, null=True,
        verbose_name='Rango de precio',
        help_text='economico / medio / alto / premium — calculado desde precioventa')
    excluir_de_analitica = models.BooleanField(
        default=False,
        verbose_name='Excluir de analitica',
        help_text='No aparece en dashboards, predicciones ni KPIs. Usar para exhibición, consignación o catálogo histórico.'
    )

    def __str__(self):
        return f"Producto {self.articulo} - {self.precioventa}"


class Producto_Talla(models.Model):

    producto =   models.ForeignKey(Producto, related_name='producto_talla', on_delete=models.CASCADE)
    sku = models.BigIntegerField()  # Cambiado a BigIntegerField para soportar codigo_asociado de MySQL
    stock =   models.IntegerField( )
    talla =   models.CharField(max_length=50)
  
    def __str__(self):
        return f"Producto_Talla {self.sku} - {self.stock}"
    
    def stock_sucursal(self, sucursal_id):
        """
        Retorna el stock disponible en una sucursal específica.
        
        IMPORTANTE: Usa directamente el campo 'stock' de la tabla Producto_Talla
        que está sincronizado desde MySQL (producción).
        
        El stock solo se muestra si el producto pertenece a la sucursal consultada.
        """
        # ✅ Asegurar que sucursal_id sea int para comparaciones correctas
        if sucursal_id is not None:
            sucursal_id = int(sucursal_id)
        
        # ✅ Usar stock directo de la tabla (sincronizado desde MySQL)
        # Solo retornar stock si el producto pertenece a esta sucursal
        if self.producto and self.producto.sucursal_id == sucursal_id:
            return max(0, self.stock or 0)
        
        # Producto no pertenece a esta sucursal
        return 0
    
    def stock_total(self):
        """
        Retorna el stock total del producto.
        
        IMPORTANTE: Usa directamente el campo 'stock' de la tabla Producto_Talla
        que está sincronizado desde MySQL (producción).
        """
        return max(0, self.stock or 0)
