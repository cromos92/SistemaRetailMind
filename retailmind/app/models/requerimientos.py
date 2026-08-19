from django.db import models
from django.utils import timezone
from django.conf import settings
from ..storage_backends import storage_evidencias
from .organizacion import Empresa, Sucursal
from .catalogo import Producto_Talla  # noqa: F401  (usado por los FK de abajo)

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

ORIGEN_REQUERIMIENTO_CHOICES = [
    ('CLIENTE', 'Reclamo de un cliente'),
    ('STOCK', 'Detectado en stock / bodega (sin cliente)'),
]

# El requerimiento pasa por DOS aprobaciones distintas y hasta ahora ambas
# caían en el mismo par APROBADO/RECHAZADO: no se distinguía "la empresa
# decidió no reclamarlo" de "el proveedor lo rechazó". Son cosas opuestas —
# la primera es una decisión propia, la segunda es plata que se pierde— y
# mezclarlas hacía imposible medir cuánto rechaza cada proveedor.
#
#   tienda crea ─▶ PENDIENTE ─▶ EN_REVISION ─┬─▶ VALIDADO ──▶ ESPERANDO_RESPUESTA ─┬─▶ APROBADO
#                                            │                                     └─▶ RECHAZADO
#                                            └─▶ RECHAZADO_INTERNO
#
ESTADO_REQUERIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente de revision'),
    ('EN_REVISION', 'En revision'),
    ('VALIDADO', 'Validado (listo para el proveedor)'),
    ('RECHAZADO_INTERNO', 'Rechazado internamente'),
    ('ESPERANDO_RESPUESTA', 'Esperando respuesta del proveedor'),
    ('APROBADO', 'Aprobado por el proveedor'),
    ('RECHAZADO', 'Rechazado por el proveedor'),
    ('EN_PROCESO', 'En proceso de resolucion'),
    ('COMPLETADO', 'Completado'),
    ('CANCELADO', 'Cancelado'),
]

# Agrupación para la UI: qué etapa del circuito está viviendo el caso. Es lo
# que permite que el listado ofrezca "en la empresa" vs "en el proveedor" sin
# que el usuario tenga que memorizar los 10 estados.
ETAPA_POR_ESTADO = {
    'PENDIENTE': 'EMPRESA',
    'EN_REVISION': 'EMPRESA',
    'VALIDADO': 'EMPRESA',
    'ESPERANDO_RESPUESTA': 'PROVEEDOR',
    'APROBADO': 'RESOLUCION',
    'RECHAZADO': 'RESOLUCION',
    'EN_PROCESO': 'RESOLUCION',
    'RECHAZADO_INTERNO': 'CERRADO',
    'COMPLETADO': 'CERRADO',
    'CANCELADO': 'CERRADO',
}

# Estados en los que el caso ya no avanza solo: nadie espera nada de él.
ESTADOS_CERRADOS = ('COMPLETADO', 'CANCELADO', 'RECHAZADO_INTERNO')

DECISION_INTERNA_CHOICES = [
    ('APROBADO', 'Procede: se reclama al proveedor'),
    ('RECHAZADO', 'No procede: no se reclama'),
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


class DocumentoCompraLegacy(models.Model):
    """N° de factura de compra recuperado del sistema anterior (Laravel).

    Vive en SU PROPIA TABLA a propósito. El dato sirve para una sola cosa:
    decirle al proveedor con qué factura se le compró el producto que se le
    está reclamando. Escribirlo dentro de `Movimientos_Producto` —en el FK
    `dte` o en `observaciones`— cambiaría lo que ven el kardex, el costeo
    FIFO, la tarjeta de movimientos y los reportes de compras, que hoy
    cuadran. Acá no toca nada: ninguna otra vista lee esta tabla.

    Se llena con `manage.py backfill_documento_movimientos_laravel`, que
    relee `movimiento_productos.N_documento` del MySQL legacy — la columna
    que la migración leía pero nunca guardaba.

    Es descartable: `DELETE FROM app_documentocompralegacy` deja el sistema
    exactamente como estaba.
    """
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.CASCADE,
        related_name='documentos_compra_legacy',
        null=True, blank=True,
    )
    sku = models.CharField(
        max_length=100,
        help_text="SKU denormalizado: el buscador entra por acá"
    )
    numero_documento = models.CharField(
        max_length=50,
        help_text="N° tal como lo escribió el sistema anterior"
    )
    fecha_movimiento = models.DateField(
        null=True, blank=True,
        help_text="Fecha del ingreso en el kardex legacy"
    )
    cantidad = models.IntegerField(default=0)
    costo = models.IntegerField(default=0)
    concepto = models.CharField(max_length=50, blank=True, null=True)
    marca_legacy = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Marca que traía el movimiento: pista del proveedor"
    )
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_compra_legacy',
    )
    # Si la cabecera del documento SÍ está en el sistema, se deja anotada.
    # Es informativo y vive solo acá: no se toca el DTE ni el movimiento.
    dte = models.ForeignKey(
        'app.Dte', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_compra_legacy',
    )
    proveedor = models.ForeignKey(
        Empresa, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_compra_legacy',
    )
    # PK del Movimientos_Producto de origen. Es un entero suelto y no un FK
    # para que esta tabla no le agregue ni una restricción al kardex.
    movimiento_origen_id = models.BigIntegerField(
        unique=True,
        help_text="Movimiento del que se recuperó el dato (evita duplicar)"
    )
    fecha_carga = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Documento de compra legacy'
        verbose_name_plural = 'Documentos de compra legacy'
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['producto_talla', '-fecha_movimiento']),
            models.Index(fields=['numero_documento']),
        ]

    def __str__(self):
        return f"N° {self.numero_documento} · SKU {self.sku}"


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

    @classmethod
    def tipos_para(cls, tipo_requerimiento, solo_obligatorios=False):
        """Tipos de foto aplicables a un tipo de requerimiento, ya ordenados.

        El filtrado de `tipos_requerimiento` (JSONField) se hace en Python y no
        con `__contains`: ese lookup solo existe en PostgreSQL y hacía estallar
        cualquier flujo del módulo en otros backends (la suite de tests, por
        ejemplo, no podía ni crear un requerimiento). La tabla tiene menos de
        20 filas, así que traerla entera es más barato que el operador JSON.
        """
        qs = cls.objects.filter(activo=True)
        if solo_obligatorios:
            qs = qs.filter(es_obligatorio=True)
        return [
            tf for tf in qs.order_by('orden')
            if tipo_requerimiento in (tf.tipos_requerimiento or [])
        ]


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
    origen = models.CharField(
        max_length=10,
        choices=ORIGEN_REQUERIMIENTO_CHOICES,
        default='CLIENTE',
        help_text=(
            "CLIENTE: lo reclama un cliente final (lo ideal, hay documento y "
            "persona detrás). STOCK: la tienda detecta la falla en mercadería "
            "sin vender, por lo que no hay cliente al que exigirle datos."
        )
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
    cantidad = models.PositiveIntegerField(
        default=1,
        help_text="Unidades reclamadas. El proveedor necesita este dato para su NC."
    )

    # === RESPALDO DE COMPRA AL PROVEEDOR ===
    # Es lo que el proveedor exige para aceptar una garantía: la factura con la
    # que ESE producto le fue comprado. La boleta de venta al cliente (más
    # abajo) le sirve para la fecha, pero no para identificar la partida.
    dte_compra = models.ForeignKey(
        'app.Dte',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_garantia',
        help_text="Factura de compra al proveedor, cuando está en el sistema"
    )
    numero_factura_compra = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="N° de la factura de compra (permite tipearlo si no está en el sistema)"
    )
    fecha_factura_compra = models.DateField(
        blank=True,
        null=True,
        help_text="Fecha de la factura de compra al proveedor"
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
        blank=True,
        help_text="Nombre completo del cliente. Vacío solo si origen='STOCK'."
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

    # === DECISION INTERNA (paso previo al proveedor) ===
    # Quien revisa decide primero si el reclamo procede. Antes esta decisión
    # no se guardaba en ninguna parte: se marcaba el estado y listo, así que
    # no había forma de saber quién la tomó ni por qué.
    decision_interna = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=DECISION_INTERNA_CHOICES,
        help_text="Decisión de la empresa antes de escalar al proveedor"
    )
    motivo_decision_interna = models.TextField(
        blank=True,
        null=True,
        help_text="Por qué se validó o se rechazó internamente"
    )
    fecha_decision_interna = models.DateTimeField(blank=True, null=True)
    usuario_decision_interna = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_decididos',
        help_text="Quién tomó la decisión interna"
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
    def subtipo_display(self):
        """Etiqueta legible del subtipo.

        `subtipo` es un CharField libre (el valor depende del tipo principal),
        así que no hay `get_subtipo_display`. Sin esto el correo al proveedor
        imprimía el código crudo: "DESPEGUE_SUELA".
        """
        if not self.subtipo:
            return ''
        etiquetas = dict(SUBTIPO_DEFECTO_CHOICES + SUBTIPO_ERROR_CHOICES)
        return etiquetas.get(self.subtipo, self.subtipo.replace('_', ' ').title())

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
    def etapa(self):
        """En qué tramo del circuito está: EMPRESA, PROVEEDOR, RESOLUCION o CERRADO."""
        return ETAPA_POR_ESTADO.get(self.estado, 'EMPRESA')

    @property
    def esta_cerrado(self):
        return self.estado in ESTADOS_CERRADOS

    @property
    def tiene_respaldo_compra(self):
        """¿Está identificada la factura con la que se le compró al proveedor?

        Es el dato que la mayoría de los proveedores exige para cursar una
        garantía, y el que la tienda no tiene cómo saber.
        """
        return bool(self.numero_factura_compra or self.dte_compra_id)

    @property
    def faltantes_para_enviar(self):
        """Lista legible de lo que impide mandarle el caso al proveedor.

        Una sola fuente de verdad para el botón del detalle, el badge del
        listado y el aviso del modal de envío: antes cada pantalla decidía por
        su cuenta qué era "estar completo" y se contradecían entre sí.
        """
        faltantes = []
        if not self.proveedor_id:
            faltantes.append('proveedor')
        if not self.tiene_respaldo_compra:
            faltantes.append('factura de compra')
        # `.all()` y no `.exists()`: en el listado las fotos vienen con
        # prefetch_related y `.exists()` dispara igual una consulta por fila.
        if not len(self.fotos.all()):
            faltantes.append('fotos')
        elif not self.fotos_completas and self.tipo in (
                'PRODUCTO_FALLADO', 'GARANTIA', 'ERROR_DESPACHO'):
            faltantes.append('fotos obligatorias')
        return faltantes

    @property
    def listo_para_proveedor(self):
        return not self.faltantes_para_enviar

    @property
    def nivel_urgencia(self):
        """Retorna nivel de urgencia segun dias transcurridos"""
        # RECHAZADO (por el proveedor) NO está cerrado: la tienda todavía
        # tiene que resolverlo con el cliente, y marcarlo como cerrado lo
        # sacaba de los tableros de urgencia justo cuando hay que actuar.
        if self.esta_cerrado:
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
        tipos_obligatorios = TipoFotoRequerimiento.tipos_para(
            self.tipo, solo_obligatorios=True)
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
        storage=storage_evidencias,
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
