"""
Modelos de configuración para integraciones con ecommerces externos.

Diseño:
  * ``CredencialesEcommerce`` guarda en BD las URL/API key de cada ecommerce
    (realsport.cl, paola.cl, ...) por Empresa. Se gestiona desde la pantalla
    de Configuración → Integraciones Ecommerce. NO se usan variables de
    entorno: se agrega una nueva integración desde la UI sin redeploy.

  * ``FotoPortadaArticulo`` enlaza la URL de portada al CAMPO ``articulo``
    (el código del producto, que se repite N veces en la tabla Producto —
    una fila por sucursal). Así una sola foto sirve para todas las sucursales
    sin duplicar datos. Soporta múltiples ecommerces de origen: cuando un
    ``articulo`` existe en varios, el template tag elige primero el ecommerce
    de la misma Empresa que el producto, y como fallback el de mayor
    ``prioridad``.
"""
from django.db import models

from .organizacion import Empresa


class CredencialesEcommerce(models.Model):
    """Credenciales de API de un ecommerce externo asociado a una Empresa."""

    TIPO_CHOICES = [
        ('realsport', 'realsport.cl'),
        ('paola', 'paola.cl'),
        ('otro', 'Otro'),
    ]

    codigo = models.SlugField(
        max_length=50, unique=True,
        help_text='Identificador interno único (ej: realsport, paola).',
    )
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='credenciales_ecommerce',
        help_text='Empresa propietaria del ecommerce (ej: IMPORTADORA NICOL para realsport.cl).',
    )
    url_api = models.URLField(
        max_length=255,
        help_text='URL base sin /api/v1/ (ej: https://realsport.cl).',
    )
    api_key = models.CharField(
        max_length=255,
        help_text='Valor que se manda en el header.',
    )
    header_name = models.CharField(
        max_length=50, default='X-AllConnected-Key',
        help_text='Nombre del header HTTP que lleva la API key.',
    )
    activo = models.BooleanField(default=True)
    prioridad = models.IntegerField(
        default=0,
        help_text='En caso de conflicto entre ecommerces para un mismo articulo, gana el de mayor prioridad.',
    )
    ultima_sync_at = models.DateTimeField(null=True, blank=True)
    ultima_sync_resultado = models.CharField(max_length=255, blank=True, default='')

    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Credencial ecommerce'
        verbose_name_plural = 'Credenciales ecommerce'
        indexes = [
            models.Index(fields=['activo']),
            models.Index(fields=['tipo']),
        ]
        ordering = ['-prioridad', 'nombre']

    def __str__(self):
        return f'{self.nombre} ({self.empresa.nombre})'


class FotoPortadaArticulo(models.Model):
    """URL de portada por código de articulo y ecommerce de origen.

    La foto vive a nivel de ``articulo`` (string del SKU), NO a nivel de
    ``Producto`` (que se repite por sucursal). Esto evita duplicar la misma
    URL N veces y simplifica el sync.
    """

    articulo = models.CharField(max_length=200, db_index=True)
    url_foto = models.URLField(max_length=500)
    origen = models.ForeignKey(
        CredencialesEcommerce,
        on_delete=models.CASCADE,
        related_name='fotos',
    )
    es_principal = models.BooleanField(
        default=True,
        help_text='Si en el futuro guardamos galería, esta es la portada.',
    )
    sync_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Foto portada de articulo'
        verbose_name_plural = 'Fotos portada de articulos'
        unique_together = ('articulo', 'origen')
        indexes = [
            models.Index(fields=['articulo', 'es_principal']),
        ]

    def __str__(self):
        return f'{self.articulo} ← {self.origen.codigo}'
