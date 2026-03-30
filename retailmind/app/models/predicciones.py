from django.db import models
from django.db.models import Sum, F
from django.utils import timezone
from .catalogo import Producto, Producto_Talla, AtributoOpcion


class ConfiguracionPrediccion(models.Model):
    """Singleton de parámetros del motor predictivo."""
    nombre = models.CharField(max_length=100, default='default', unique=True)
    dias_historico_analisis = models.IntegerField(default=730,
        help_text='Días de historial a considerar (default 2 años)')
    factor_seguridad_clase_a = models.DecimalField(max_digits=4, decimal_places=2, default=1.15)
    factor_seguridad_clase_b = models.DecimalField(max_digits=4, decimal_places=2, default=1.10)
    factor_seguridad_clase_c = models.DecimalField(max_digits=4, decimal_places=2, default=1.05)
    semanas_minimas_para_alerta = models.IntegerField(default=2,
        help_text='Semanas mínimas activo antes de evaluar alertas')
    umbral_cv_x = models.DecimalField(max_digits=4, decimal_places=2, default=0.50,
        help_text='CV < este valor = clase X (demanda estable)')
    umbral_cv_y = models.DecimalField(max_digits=4, decimal_places=2, default=1.00,
        help_text='CV < este valor = clase Y (CV >= = clase Z)')
    umbral_ratio_velocidad_alta = models.DecimalField(max_digits=4, decimal_places=2, default=0.70)
    umbral_ratio_velocidad_critica = models.DecimalField(max_digits=4, decimal_places=2, default=0.50)
    umbral_quiebre_talle_critico = models.DecimalField(max_digits=4, decimal_places=2, default=0.60,
        help_text='% de demanda sin cubrir para urgencia CRITICA en quiebre de talle')
    semanas_buffer_seguridad = models.IntegerField(default=1,
        help_text='Semanas extra de buffer sobre el lead time')
    sellthrough_default = models.DecimalField(max_digits=4, decimal_places=2, default=0.78,
        help_text='Sell-through por defecto cuando no hay historial')
    velocidad_default_semanas = models.IntegerField(default=8,
        help_text='Velocidad de rotación por defecto en semanas')
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuración de Predicción'
        verbose_name_plural = 'Configuraciones de Predicción'

    def __str__(self):
        return f"Config: {self.nombre}"

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(nombre='default')
        return obj


class ClasificacionABC(models.Model):
    articulo = models.ForeignKey(Producto, related_name='clasificaciones_abc',
        on_delete=models.CASCADE)
    temporada = models.CharField(max_length=50)
    anio = models.IntegerField()
    clasificacion_abc = models.CharField(max_length=1, choices=[
        ('A', 'A — 80% ingreso'), ('B', 'B — 15% ingreso'), ('C', 'C — 5% ingreso'),
    ])
    clasificacion_xyz = models.CharField(max_length=1, choices=[
        ('X', 'X — CV < 0.5'), ('Y', 'Y — CV 0.5-1.0'), ('Z', 'Z — CV > 1.0'),
    ])
    ventas_totales_periodo = models.IntegerField(default=0)
    porcentaje_acumulado = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    coeficiente_variacion = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    demanda_censurada = models.BooleanField(default=False,
        help_text='True si el producto se agotó antes del fin de temporada')
    factor_correccion_censura = models.DecimalField(max_digits=6, decimal_places=4, default=1.0)
    ventas_corregidas = models.IntegerField(default=0,
        help_text='ventas_totales * factor_correccion_censura')
    fecha_calculo = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('articulo', 'temporada', 'anio')
        verbose_name = 'Clasificación ABC-XYZ'
        verbose_name_plural = 'Clasificaciones ABC-XYZ'
        indexes = [
            models.Index(fields=['clasificacion_abc', 'clasificacion_xyz']),
            models.Index(fields=['temporada', 'anio']),
        ]

    def __str__(self):
        return f"{self.articulo.articulo} — {self.clasificacion_abc}{self.clasificacion_xyz}"

    @property
    def clase_combinada(self):
        return f"{self.clasificacion_abc}{self.clasificacion_xyz}"


class VelocidadHistorica(models.Model):
    """Benchmarks de rotación por grupo marca+categoría+género+temporada."""
    marca = models.ForeignKey(AtributoOpcion, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='velocidades_historicas')
    categoria = models.CharField(max_length=100,
        help_text='Denormalizado desde Categoria.nombre')
    subcategoria = models.CharField(max_length=100, blank=True, default='')
    genero = models.CharField(max_length=50)
    temporada = models.CharField(max_length=50)
    velocidad_semanas_p25 = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Percentil 25: el 25%% rota más rápido que esto')
    velocidad_semanas_p50 = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Mediana de semanas para agotar stock')
    velocidad_semanas_p75 = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Percentil 75: rotación lenta')
    sellthrough_p50 = models.DecimalField(max_digits=5, decimal_places=4,
        help_text='Mediana del % de stock que se vende')
    total_articulos_base = models.IntegerField(default=0,
        help_text='Cuántos artículos históricos se usaron para calcular')
    articulos_censurados = models.IntegerField(default=0,
        help_text='Cuántos se agotaron antes de tiempo')
    fecha_calculo = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('marca', 'categoria', 'subcategoria', 'genero', 'temporada')
        verbose_name = 'Velocidad Histórica'
        verbose_name_plural = 'Velocidades Históricas'
        indexes = [
            models.Index(fields=['categoria', 'subcategoria', 'temporada']),
        ]

    def __str__(self):
        marca_str = self.marca.valor if self.marca else 'Todas'
        return f"{marca_str} / {self.categoria} / {self.genero} / {self.temporada} — p50: {self.velocidad_semanas_p50}sem"


class CurvaTalles(models.Model):
    """Distribución histórica de ventas por talle para un grupo."""
    marca = models.ForeignKey(AtributoOpcion, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='curvas_talle')
    categoria = models.CharField(max_length=100)
    subcategoria = models.CharField(max_length=100, blank=True, default='')
    genero = models.CharField(max_length=50)
    talle = models.CharField(max_length=20)
    porcentaje = models.DecimalField(max_digits=6, decimal_places=4,
        help_text='Ej: 0.2350 = 23.50%% de las ventas del grupo son este talle')
    temporada = models.CharField(max_length=50, blank=True, default='',
        help_text='Vacío = aplica a todas las temporadas')
    total_ventas_base = models.IntegerField(default=0)
    fecha_calculo = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('marca', 'categoria', 'subcategoria', 'genero', 'talle', 'temporada')
        verbose_name = 'Curva de Talles'
        verbose_name_plural = 'Curvas de Talles'

    def __str__(self):
        return f"{self.talle}: {float(self.porcentaje)*100:.1f}%"


class PrediccionDemanda(models.Model):
    articulo = models.ForeignKey(Producto, related_name='predicciones_demanda',
        on_delete=models.CASCADE)
    temporada = models.CharField(max_length=50)
    anio = models.IntegerField()
    unidades_predichas = models.IntegerField(
        help_text='Total artículo (con corrección censura), sin distribuir por talle')
    unidades_sin_correccion = models.IntegerField(default=0,
        help_text='Predicción antes de corregir censura')
    factor_censura_aplicado = models.DecimalField(max_digits=6, decimal_places=4, default=1.0)
    metodo_usado = models.CharField(max_length=50, choices=[
        ('similitud', 'Por artículos similares'),
        ('promedio_movil', 'Promedio móvil ponderado'),
        ('holt_winters', 'Holt-Winters'),
    ])
    confianza = models.DecimalField(max_digits=4, decimal_places=2, default=0.50,
        help_text='0.00 a 1.00')
    articulos_similares_usados = models.IntegerField(default=0)
    fecha_generacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('articulo', 'temporada', 'anio')
        verbose_name = 'Predicción de Demanda'
        verbose_name_plural = 'Predicciones de Demanda'

    def __str__(self):
        return f"{self.articulo.articulo} — {self.unidades_predichas}u ({self.metodo_usado})"


class SugerenciaCompra(models.Model):
    ORIGEN_CHOICES = [
        ('prediccion', 'Predicción de demanda'),
        ('alerta_velocidad', 'Alerta de velocidad'),
        ('alerta_quiebre', 'Alerta de quiebre de talle'),
        ('manual', 'Manual'),
    ]

    prediccion = models.ForeignKey(PrediccionDemanda, related_name='sugerencias',
        on_delete=models.CASCADE, null=True, blank=True)
    articulo_talle = models.ForeignKey(Producto_Talla, related_name='sugerencias_compra',
        on_delete=models.CASCADE)
    unidades_sugeridas = models.IntegerField(
        help_text='Lo que el modelo dice que hay que tener en total')
    stock_actual = models.IntegerField(
        help_text='Copiado desde Producto_Talla.stock al generar')
    unidades_en_transito = models.IntegerField(default=0,
        help_text='Suma de items en órdenes activas para este talle')
    unidades_a_pedir = models.IntegerField(
        help_text='max(0, sugeridas - stock_actual - en_transito)')
    origen = models.CharField(max_length=50, choices=ORIGEN_CHOICES, default='prediccion')
    aprobada = models.BooleanField(default=False)
    fecha_aprobacion = models.DateField(null=True, blank=True)
    aprobada_por = models.CharField(max_length=100, blank=True, default='')
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Sugerencia de Compra'
        verbose_name_plural = 'Sugerencias de Compra'
        indexes = [
            models.Index(fields=['aprobada', 'fecha_generacion']),
            models.Index(fields=['origen']),
        ]

    def __str__(self):
        return f"{self.articulo_talle} — pedir {self.unidades_a_pedir}u ({self.get_origen_display()})"

    def calcular_unidades_a_pedir(self):
        from .compras import Compras_Producto_Talla
        transito = Compras_Producto_Talla.objects.filter(
            compra_producto__compras__estado='ACTIVA',
            producto_talla=self.articulo_talle,
        ).aggregate(
            total=Sum(F('stock') - F('unidades_recibidas'))
        )['total'] or 0
        return max(0, self.unidades_sugeridas - self.stock_actual - max(0, transito))


URGENCIA_CHOICES = [
    ('CRITICA', 'Crítica'),
    ('ALTA', 'Alta'),
    ('MEDIA', 'Media'),
    ('BAJA', 'Baja'),
]


class AlertaVelocidad(models.Model):
    """Alerta: el artículo rota más rápido de lo normal y puede agotarse."""
    articulo = models.ForeignKey(Producto, related_name='alertas_velocidad',
        on_delete=models.CASCADE)
    temporada = models.CharField(max_length=50)
    anio = models.IntegerField()
    fecha_deteccion = models.DateTimeField(auto_now_add=True)

    stock_inicial = models.IntegerField()
    ventas_acumuladas = models.IntegerField()
    stock_restante = models.IntegerField()
    semanas_activo = models.DecimalField(max_digits=8, decimal_places=2)

    velocidad_real_semanas = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Unidades vendidas por semana actualmente')
    velocidad_normal_semanas = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Tomada de VelocidadHistorica.velocidad_semanas_p50')
    ratio_velocidad = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='velocidad_normal / velocidad_real — < 1 = más rápido de lo normal')

    semanas_stock_restante = models.DecimalField(max_digits=8, decimal_places=2,
        help_text='Semanas de stock al ritmo actual')
    semanas_temporada_restante = models.DecimalField(max_digits=8, decimal_places=2)

    lead_time_proveedor_dias = models.IntegerField(default=21,
        help_text='Copiado de Empresa.lead_time_dias al detectar')
    semanas_para_pedir = models.DecimalField(max_digits=5, decimal_places=2, default=4,
        help_text='lead_time/7 + buffer')

    urgencia = models.CharField(max_length=20, choices=URGENCIA_CHOICES)
    unidades_recompra_sugerida = models.IntegerField(default=0)
    unidades_en_transito = models.IntegerField(default=0)
    sugerencia_generada = models.BooleanField(default=False)
    resuelta = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Alerta de Velocidad'
        verbose_name_plural = 'Alertas de Velocidad'
        indexes = [
            models.Index(fields=['articulo', 'temporada', 'anio']),
            models.Index(fields=['urgencia', 'resuelta']),
            models.Index(fields=['fecha_deteccion']),
            models.Index(fields=['sugerencia_generada', 'resuelta']),
        ]

    def __str__(self):
        return f"{self.articulo.articulo} — {self.urgencia} ({self.semanas_stock_restante} sem restantes)"


class AlertaQuiebreTalle(models.Model):
    """Alerta: los talles populares están agotados aunque haya stock total."""
    articulo = models.ForeignKey(Producto, related_name='alertas_quiebre_talle',
        on_delete=models.CASCADE)
    fecha_deteccion = models.DateTimeField(auto_now_add=True)
    temporada = models.CharField(max_length=50)
    anio = models.IntegerField()

    talles_agotados = models.JSONField(default=list,
        help_text='Lista de talles con stock=0')
    talles_con_stock = models.JSONField(default=list,
        help_text='Lista de talles que aún tienen stock')
    talles_agotados_criticos = models.JSONField(default=list,
        help_text='Talles agotados que representan >5%% de la demanda cada uno')

    stock_total_restante = models.IntegerField(default=0)
    porcentaje_demanda_sin_cubrir = models.DecimalField(max_digits=5, decimal_places=2,
        help_text='%% de la demanda histórica que esos talles representaban')
    unidades_perdidas_semana_estimadas = models.IntegerField(default=0,
        help_text='Ventas perdidas por semana estimadas')

    urgencia = models.CharField(max_length=20, choices=URGENCIA_CHOICES)
    sugerencia_generada = models.BooleanField(default=False)
    resuelta = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Alerta de Quiebre de Talle'
        verbose_name_plural = 'Alertas de Quiebre de Talle'
        indexes = [
            models.Index(fields=['urgencia', 'resuelta']),
            models.Index(fields=['articulo', 'temporada', 'anio']),
        ]

    def __str__(self):
        return f"{self.articulo.articulo} — {self.urgencia} ({self.porcentaje_demanda_sin_cubrir}% sin cubrir)"


class PendienteReevaluacion(models.Model):
    """Tabla ligera para patrón mark-then-evaluate en señales."""
    TIPO_CHOICES = [
        ('velocidad', 'Alerta de velocidad'),
        ('quiebre_talle', 'Quiebre de talle'),
    ]
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE,
        related_name='pendientes_reevaluacion')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    fecha_marcado = models.DateTimeField(auto_now=True)
    procesado = models.BooleanField(default=False)
    fecha_procesado = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('producto', 'tipo', 'procesado')
        verbose_name = 'Pendiente de Reevaluación'
        verbose_name_plural = 'Pendientes de Reevaluación'
        indexes = [
            models.Index(fields=['procesado', 'tipo']),
            models.Index(fields=['producto', 'procesado']),
        ]

    def __str__(self):
        estado = 'pendiente' if not self.procesado else 'procesado'
        return f"{self.producto.articulo} — {self.tipo} ({estado})"


class StockInicialTemporada(models.Model):
    """Registra cuándo y cuánto stock entró por primera vez en la temporada."""
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE,
        related_name='stocks_iniciales_temporada')
    temporada = models.CharField(max_length=50)
    anio = models.IntegerField()
    fecha_primer_ingreso = models.DateField()
    stock_inicial = models.IntegerField(
        help_text='Stock del primer ingreso de la temporada')
    stock_total_ingresado = models.IntegerField(default=0,
        help_text='Acumulado de TODAS las entradas (inicial + reposiciones)')

    class Meta:
        unique_together = ('producto', 'temporada', 'anio')
        verbose_name = 'Stock Inicial Temporada'
        verbose_name_plural = 'Stocks Iniciales Temporada'
        indexes = [
            models.Index(fields=['temporada', 'anio']),
            models.Index(fields=['fecha_primer_ingreso']),
        ]

    def __str__(self):
        return f"{self.producto.articulo} — {self.temporada} {self.anio}: {self.stock_inicial}u inicial"
