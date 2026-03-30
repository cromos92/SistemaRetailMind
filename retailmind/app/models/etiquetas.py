from django.db import models
from django.conf import settings
from .organizacion import Sucursal
from .catalogo import Producto_Talla

class HistorialImpresionEtiqueta(models.Model):
    """
    Registra cada impresión de etiquetas para trazabilidad.
    Permite saber qué productos ya fueron impresos desde qué documento.
    """
    
    TIPO_ORIGEN_CHOICES = [
        ('DTE_COMPRA', 'DTE Compra'),
        ('DTE_TRASPASO', 'DTE Traspaso'),
        ('TRASPASO_INTERNO', 'Traspaso Interno'),
        ('MANUAL', 'Impresión Manual'),
    ]
    
    # === ORIGEN ===
    tipo_origen = models.CharField(max_length=20, choices=TIPO_ORIGEN_CHOICES)
    documento_id = models.IntegerField(null=True, blank=True, help_text='ID del documento origen')
    numero_documento = models.CharField(max_length=50, blank=True, help_text='Número visible del documento')
    
    # === DATOS DE IMPRESIÓN ===
    sucursal = models.ForeignKey(Sucursal, on_delete=models.SET_NULL, null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_impresion = models.DateTimeField(auto_now_add=True)
    
    # === ESTADÍSTICAS ===
    total_productos = models.IntegerField(default=0)
    total_etiquetas = models.IntegerField(default=0)
    
    # === ESTADO ===
    completado = models.BooleanField(default=True)
    observaciones = models.TextField(blank=True, null=True)
    
    class Meta:
        ordering = ['-fecha_impresion']
        verbose_name = 'Historial de Impresión'
        verbose_name_plural = 'Historial de Impresiones'
    
    def __str__(self):
        return f"{self.tipo_origen} #{self.numero_documento} - {self.total_etiquetas} etiquetas"



class DetalleImpresionEtiqueta(models.Model):
    """
    Detalle de productos impresos en cada sesión de impresión.
    """
    historial = models.ForeignKey(
        HistorialImpresionEtiqueta, 
        on_delete=models.CASCADE, 
        related_name='detalles'
    )
    producto_talla = models.ForeignKey(
        Producto_Talla, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    # === SNAPSHOT DE DATOS (para mantener registro aunque cambie el producto) ===
    sku = models.CharField(max_length=50)
    articulo = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=200, blank=True)
    marca = models.CharField(max_length=50, blank=True)
    talla = models.CharField(max_length=20, blank=True)
    color = models.CharField(max_length=50, blank=True)
    precio_impreso = models.IntegerField(default=0, help_text='Precio que se imprimió en la etiqueta')
    
    # === CANTIDAD ===
    cantidad_etiquetas = models.IntegerField(default=1)
    
    class Meta:
        verbose_name = 'Detalle de Impresión'
        verbose_name_plural = 'Detalles de Impresión'
    
    def __str__(self):
        return f"{self.sku} - {self.cantidad_etiquetas} etiquetas"
