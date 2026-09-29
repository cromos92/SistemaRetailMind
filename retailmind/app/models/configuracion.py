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
from django.conf import settings
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

    # Resultado de la última VERIFICACIÓN (cobertura + liveness de URL). Espeja
    # ``ultima_sync_*``: el sync dice cuántos SKU matchearon; la verificación dice
    # si esas URLs realmente devuelven una imagen viva.
    ultima_verif_at = models.DateTimeField(null=True, blank=True)
    ultima_verif_resultado = models.CharField(max_length=255, blank=True, default='')
    ultima_verif_detalle = models.TextField(
        blank=True, default='',
        help_text='JSON con la muestra de URLs muertas de la última verificación.',
    )

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


# ---------------------------------------------------------------------------
# Inteligencia Artificial (Configuración → Inteligencia Artificial)
#
# Claves de API y modelo por tarea de los agentes de IA (lectura de facturas,
# chat de la carga, búsqueda en internet, asistente). Lo guardado aquí manda
# sobre las variables de entorno (ANTHROPIC_API_KEY, OPENAI_API_KEY,
# CARGA_FACTURA_MODELO…), que quedan de respaldo: sin nada guardado, todo
# funciona como antes. Ver app/utils_ia.py.

PROVEEDOR_IA_CHOICES = [
    ('anthropic', 'Anthropic (Claude)'),
    ('openai', 'OpenAI (ChatGPT)'),
    ('gemini', 'Google Gemini'),
    ('deepseek', 'DeepSeek'),
    ('openrouter', 'OpenRouter'),
    ('compatible', 'API compatible (Ollama, vLLM, Groq…)'),
]

TAREA_IA_CHOICES = [
    ('lectura', 'Lectura de facturas PDF'),
    ('lectura_opciones', 'Otros lectores que se ofrecen al subir'),
    ('verificacion', 'Verificación de líneas dudosas'),
    ('rapido', 'Enderezar páginas escaneadas'),
    ('chat', 'Chat de la carga por factura'),
    ('busqueda', 'Búsqueda en internet'),
    ('asistente', 'Asistente'),
]


class ClaveProveedorIA(models.Model):
    """Clave de API de un proveedor de IA, CIFRADA en reposo (Fernet, ver
    services/mp_credenciales.py: la clave de cifrado vive en el entorno,
    nunca en la BD). Escribir SIEMPRE con ``set_clave`` y leer con
    ``get_clave``; jamás asignar ``clave_cifrada`` a mano con texto plano.
    La pantalla solo muestra ``ultimos4``."""

    proveedor = models.CharField(max_length=20, choices=PROVEEDOR_IA_CHOICES, unique=True)
    clave_cifrada = models.TextField(blank=True, help_text='Clave de API CIFRADA — usar set_clave()')
    ultimos4 = models.CharField(max_length=8, blank=True,
                                help_text='Últimos caracteres, para reconocerla en pantalla.')
    url_base = models.CharField(
        max_length=255, blank=True,
        help_text='Solo «API compatible»: URL base (p. ej. http://localhost:11434/v1 para Ollama).')
    workspace_id = models.CharField(
        max_length=100, blank=True,
        help_text='Solo Anthropic: id del workspace (wrkspc_…) si la clave es de organización.')
    activa = models.BooleanField(default=True, help_text='Apagada = se usa la variable de entorno (si hay).')
    modelos = models.JSONField(default=list, blank=True,
                               help_text='Modelos que listó el proveedor en la última prueba.')
    probada_en = models.DateTimeField(null=True, blank=True)
    prueba_ok = models.BooleanField(null=True, blank=True)
    prueba_detalle = models.CharField(max_length=300, blank=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='claves_ia_actualizadas')
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Clave de proveedor de IA'
        verbose_name_plural = 'Claves de proveedores de IA'
        ordering = ['proveedor']

    def __str__(self):
        if self.ultimos4:
            return f'{self.get_proveedor_display()} (…{self.ultimos4})'
        return self.get_proveedor_display()

    def set_clave(self, valor):
        from app.services.mp_credenciales import cifrar
        valor = (valor or '').strip()
        self.clave_cifrada = cifrar(valor)
        # Solo si la clave es larga: en una corta, 4 caracteres dirían demasiado.
        self.ultimos4 = valor[-4:] if len(valor) >= 16 else ''

    def get_clave(self):
        from app.services.mp_credenciales import descifrar
        return descifrar(self.clave_cifrada, donde='IA',
                         como_rearmar='Volver a guardar la clave en Configuración → Inteligencia Artificial')


class ModeloTareaIA(models.Model):
    """Modelo (o cadena de respaldo «a,b») que usa una tarea de IA. Sin fila
    para una tarea = variable de entorno o el modelo por defecto."""

    tarea = models.CharField(max_length=30, choices=TAREA_IA_CHOICES, unique=True)
    modelo = models.CharField(
        max_length=255,
        help_text='«proveedor:modelo»; varios separados por coma = respaldo en orden '
                  '(en «otros lectores», cada lector separado por punto y coma).')
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='modelos_ia_actualizados')
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Modelo de IA por tarea'
        verbose_name_plural = 'Modelos de IA por tarea'
        ordering = ['tarea']

    def __str__(self):
        return f'{self.get_tarea_display()}: {self.modelo}'
