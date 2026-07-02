from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .catalogo import Producto_Talla

TIPO_REQUERIMIENTO_CHOICES = [
    ('PRODUCTO_FALLADO', 'Producto Fallado / Defectuoso'),
    ('ERROR_DESPACHO', 'Error de Despacho'),
    ('GARANTIA', 'Garantia Producto'),
    ('DEVOLUCION', 'Devolucion a Proveedor'),
    ('RECLAMO', 'Reclamo'),
    ('CONSULTA', 'Consulta Tecnica'),
    ('OTROS', 'Otros'),
]

SUBTIPO_DEFECTO_CHOICES = [
    ('DEFECTO_FABRICACION', 'Defecto de Fabricacion'),
    ('CALIDAD_MATERIAL', 'Problema de Calidad de Material'),
    ('DESPEGUE_SUELA', 'Despegue de Suela'),
    ('COSTURA_DEFECTUOSA', 'Costura Defectuosa'),
    ('DECOLORACION', 'Decoloracion / Manchas de Fabrica'),
    ('PIEZA_FALTANTE', 'Pieza o Componente Faltante'),
    ('OTRO_DEFECTO', 'Otro Defecto'),
]

SUBTIPO_ERROR_CHOICES = [
    ('PRODUCTO_EQUIVOCADO', 'Producto Equivocado'),
    ('TALLA_INCORRECTA', 'Talla Incorrecta'),
    ('COLOR_INCORRECTO', 'Color Incorrecto'),
    ('CANTIDAD_INCORRECTA', 'Cantidad Incorrecta'),
    ('OTRO_ERROR', 'Otro Error'),
]

ESTADO_REQUERIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('EN_REVISION', 'En Revision'),
    ('ESPERANDO_RESPUESTA', 'Esperando Respuesta Proveedor'),
    ('APROBADO', 'Aprobado por Proveedor'),
    ('RECHAZADO', 'Rechazado por Proveedor'),
    ('EN_PROCESO', 'En Proceso de Resolucion'),
    ('COMPLETADO', 'Completado'),
    ('CANCELADO', 'Cancelado'),
]

MAX_FOTOS_POR_TIPO = {
    'PRODUCTO_FALLADO': 8,
    'GARANTIA': 8,
    'ERROR_DESPACHO': 6,
    'DEVOLUCION': 5,
    'RECLAMO': 5,
    'CONSULTA': 3,
    'OTROS': 3,
}


class TipoFotoRequerimiento(models.Model):
    """
    Configuracion de tipos de foto requeridos por tipo de requerimiento.
    Define que fotos se necesitan (obligatorias/opcionales) con guias
    de que debe mostrar cada foto.
    """
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion_guia = models.TextField(
        help_text="Instruccion mostrada al usuario sobre que debe capturar la foto"
    )
    icono = models.CharField(max_length=50, default='ri-image-line')
    tipos_requerimiento = models.JSONField(
        default=list,
        help_text="Lista de codigos TIPO_REQUERIMIENTO donde aplica esta foto"
    )
    es_obligatorio = models.BooleanField(default=False)
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden']
        verbose_name = 'Tipo de Foto de Requerimiento'
        verbose_name_plural = 'Tipos de Foto de Requerimiento'

    def __str__(self):
        obligatorio = "Obligatoria" if self.es_obligatorio else "Opcional"
        return f"{self.nombre} ({obligatorio})"


class Requerimiento(models.Model):
    """
    Modelo para gestionar requerimientos de garantias, devoluciones y reclamos
    desde cualquier sucursal
    """
    # === INFORMACION BASICA ===
    numero_requerimiento = models.CharField(
        max_length=50,
        unique=True,
        help_text="Numero unico de requerimiento (autogenerado)"
    )
    tipo = models.CharField(
        max_length=30,
        choices=TIPO_REQUERIMIENTO_CHOICES,
        default='GARANTIA'
    )
    subtipo = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Subtipo especifico (depende del tipo principal)"
    )
    estado = models.CharField(
        max_length=30,
        choices=ESTADO_REQUERIMIENTO_CHOICES,
        default='PENDIENTE'
    )

    # === SUCURSAL Y USUARIO ===
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='requerimientos',
        help_text="Sucursal que genera el requerimiento"
    )
    usuario_creador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='requerimientos_creados',
        help_text="Usuario que creo el requerimiento"
    )
    usuario_gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_gestionados',
        help_text="Usuario que gestiona/administra el requerimiento"
    )

    # === INFORMACION DEL PRODUCTO ===
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos'
    )
    sku = models.CharField(
        max_length=100,
        help_text="SKU del producto"
    )
    nombre_producto = models.CharField(
        max_length=255,
        help_text="Nombre del producto"
    )

    # === DOCUMENTO DE VENTA ===
    numero_boleta = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Numero de boleta o documento de venta"
    )
    tipo_documento = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Tipo de documento (Boleta, Factura, etc.)"
    )
    fecha_compra = models.DateField(
        blank=True,
        null=True,
        help_text="Fecha de compra del producto"
    )

    # === INFORMACION DEL CLIENTE ===
    cliente_rut = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="RUT del cliente"
    )
    cliente_nombre = models.CharField(
        max_length=255,
        help_text="Nombre completo del cliente"
    )
    cliente_telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Telefono de contacto"
    )
    cliente_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Email del cliente"
    )

    # === DESCRIPCION DEL REQUERIMIENTO ===
    motivo = models.TextField(
        help_text="Descripcion del motivo del requerimiento"
    )
    descripcion_problema = models.TextField(
        blank=True,
        null=True,
        help_text="Descripcion detallada del problema"
    )

    # === CLASIFICACION DE DEFECTO (PRODUCTO_FALLADO / GARANTIA) ===
    severidad_defecto = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('LEVE', 'Leve - Estetico, no afecta uso'),
            ('MODERADO', 'Moderado - Afecta uso parcialmente'),
            ('GRAVE', 'Grave - Producto inutilizable'),
        ],
        help_text="Severidad del defecto (solo para PRODUCTO_FALLADO)"
    )
    condicion_producto = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('NUEVO_SIN_USO', 'Nuevo sin uso'),
            ('USADO_POCO', 'Usado poco tiempo'),
            ('USADO_NORMAL', 'Uso normal'),
        ],
        help_text="Condicion del producto al momento del reclamo"
    )

    # === ERROR DE DESPACHO ===
    producto_esperado = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Producto/talla/color que se esperaba recibir (para errores de despacho)"
    )

    # === PROVEEDOR Y RESPUESTA ===
    proveedor = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_como_proveedor',
        help_text="Proveedor al que se envia el requerimiento"
    )
    correo_enviado_proveedor = models.BooleanField(
        default=False,
        help_text="Indica si se envio correo al proveedor"
    )
    fecha_envio_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha en que se envio al proveedor"
    )
    correo_proveedor_destino = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Email al que se envio el requerimiento"
    )
    intentos_envio = models.IntegerField(
        default=0,
        help_text="Numero de veces que se ha enviado al proveedor"
    )
    ultimo_recordatorio = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha del ultimo recordatorio enviado"
    )
    respuesta_proveedor = models.TextField(
        blank=True,
        null=True,
        help_text="Respuesta del proveedor"
    )
    fecha_respuesta_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de respuesta del proveedor"
    )
    decision_proveedor = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('APROBADO', 'Aprobado'),
            ('RECHAZADO', 'Rechazado'),
            ('PARCIAL', 'Aprobado Parcial'),
        ],
        help_text="Decision del proveedor"
    )
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_asignados',
        help_text="Usuario responsable de gestionar el requerimiento"
    )

    # === RESOLUCION ===
    resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripcion de la resolucion final"
    )
    motivo_resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo visible al usuario de por que se aprobo o rechazo"
    )
    fecha_resolucion = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de resolucion del requerimiento"
    )
    devolucion_garantia = models.ForeignKey(
        'app.DevolucionGarantia',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimiento_origen',
        help_text="Devolucion de dinero generada a partir de este requerimiento (si se uso el puente de UI)"
    )

    # === NOTAS INTERNAS ===
    notas_internas = models.TextField(
        blank=True,
        null=True,
        help_text="Notas internas solo visibles para admin/supervisor"
    )

    # === CONTROL DE FOTOS ===
    fotos_completas = models.BooleanField(
        default=False,
        help_text="Indica si todas las fotos obligatorias fueron subidas"
    )

    # === FECHAS ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    # === PRIORIDAD ===
    prioridad = models.CharField(
        max_length=10,
        choices=[
            ('BAJA', 'Baja'),
            ('MEDIA', 'Media'),
            ('ALTA', 'Alta'),
            ('URGENTE', 'Urgente'),
        ],
        default='MEDIA'
    )

    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Requerimiento'
        verbose_name_plural = 'Requerimientos'
        indexes = [
            models.Index(fields=['numero_requerimiento']),
            models.Index(fields=['estado', '-fecha_creacion']),
            models.Index(fields=['sucursal', '-fecha_creacion']),
            models.Index(fields=['tipo', 'subtipo']),
            models.Index(fields=['sku']),
        ]

    def __str__(self):
        return f"{self.numero_requerimiento} - {self.get_tipo_display()} - {self.get_estado_display()}"

    def save(self, *args, **kwargs):
        """Genera numero de requerimiento automaticamente"""
        if not self.numero_requerimiento:
            fecha_actual = timezone.now()
            prefijo = f"REQ-{fecha_actual.strftime('%Y%m%d')}"

            ultimo = Requerimiento.objects.filter(
                numero_requerimiento__startswith=prefijo
            ).order_by('-numero_requerimiento').first()

            if ultimo:
                ultimo_num = int(ultimo.numero_requerimiento.split('-')[-1])
                nuevo_num = ultimo_num + 1
            else:
                nuevo_num = 1

            self.numero_requerimiento = f"{prefijo}-{nuevo_num:04d}"

        super().save(*args, **kwargs)

    @property
    def dias_transcurridos(self):
        """Calcula dias desde la creacion"""
        delta = timezone.now() - self.fecha_creacion
        return delta.days

    @property
    def cantidad_fotos(self):
        """Retorna la cantidad de fotos adjuntas"""
        return self.fotos.count()

    @property
    def max_fotos(self):
        """Retorna el maximo de fotos permitidas segun tipo"""
        return MAX_FOTOS_POR_TIPO.get(self.tipo, 5)

    @property
    def dias_sin_respuesta(self):
        """Calcula dias sin respuesta del proveedor"""
        if not self.fecha_envio_proveedor:
            return 0
        if self.fecha_respuesta_proveedor:
            delta = self.fecha_respuesta_proveedor - self.fecha_envio_proveedor
        else:
            delta = timezone.now() - self.fecha_envio_proveedor
        return delta.days

    @property
    def requiere_recordatorio(self):
        """Indica si requiere enviar recordatorio al proveedor"""
        return (
            self.estado == 'ESPERANDO_RESPUESTA' and
            self.dias_sin_respuesta > 7 and
            not self.fecha_respuesta_proveedor
        )

    @property
    def nivel_urgencia(self):
        """Retorna nivel de urgencia segun dias transcurridos"""
        if self.estado in ['COMPLETADO', 'RECHAZADO', 'CANCELADO']:
            return 'CERRADO'

        dias = self.dias_transcurridos
        if self.estado == 'ESPERANDO_RESPUESTA':
            dias = self.dias_sin_respuesta

        if dias <= 3:
            return 'NORMAL'
        elif dias <= 7:
            return 'MEDIA'
        elif dias <= 14:
            return 'ALTA'
        else:
            return 'CRITICA'

    def verificar_fotos_completas(self):
        """Verifica si todas las fotos obligatorias estan presentes"""
        tipos_obligatorios = TipoFotoRequerimiento.objects.filter(
            activo=True,
            es_obligatorio=True,
            tipos_requerimiento__contains=[self.tipo],
        )
        fotos_subidas = set(
            self.fotos.filter(tipo_foto__isnull=False)
            .values_list('tipo_foto__codigo', flat=True)
        )
        for tipo_foto in tipos_obligatorios:
            if tipo_foto.codigo not in fotos_subidas:
                return False
        return True


class FotoRequerimiento(models.Model):
    """
    Modelo para almacenar fotos adjuntas a un requerimiento.
    Cada foto puede tener un tipo que indica su proposito
    (foto del defecto, etiqueta, producto general, etc.)
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='fotos'
    )
    imagen = models.ImageField(
        upload_to='requerimientos/fotos/%Y/%m/%d/',
        help_text="Foto del producto o problema"
    )
    tipo_foto = models.ForeignKey(
        TipoFotoRequerimiento,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fotos',
        help_text="Tipo/proposito de esta foto"
    )
    descripcion = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Descripcion de la foto"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden de la foto (1-8)"
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='fotos_requerimientos'
    )

    class Meta:
        ordering = ['orden', 'fecha_subida']
        verbose_name = 'Foto de Requerimiento'
        verbose_name_plural = 'Fotos de Requerimientos'

    def __str__(self):
        tipo = self.tipo_foto.nombre if self.tipo_foto else f"Foto {self.orden}"
        return f"{tipo} - {self.requerimiento.numero_requerimiento}"


class HistorialRequerimiento(models.Model):
    """
    Modelo para registrar el historial de cambios en un requerimiento
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='historial'
    )
    accion = models.CharField(
        max_length=100,
        help_text="Tipo de accion realizada"
    )
    estado_anterior = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Estado anterior del requerimiento"
    )
    estado_nuevo = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Nuevo estado del requerimiento"
    )
    comentario = models.TextField(
        blank=True,
        null=True,
        help_text="Comentario o descripcion de la accion"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='acciones_requerimientos'
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Historial de Requerimiento'
        verbose_name_plural = 'Historial de Requerimientos'

    def __str__(self):
        return f"{self.requerimiento.numero_requerimiento} - {self.accion} - {self.fecha.strftime('%d/%m/%Y %H:%M')}"
