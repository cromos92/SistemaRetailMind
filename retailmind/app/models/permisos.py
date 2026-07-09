from django.db import models
from django.utils import timezone
from django.conf import settings

class ModuloSistema(models.Model):
    """
    Módulos principales del sistema (Dashboard, Ventas, Documentos, etc.)
    """
    codigo = models.CharField(
        max_length=50, 
        unique=True,
        help_text="Código único del módulo (ej: dashboard, ventas, compras)"
    )
    nombre = models.CharField(
        max_length=100,
        help_text="Nombre descriptivo del módulo"
    )
    descripcion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción del módulo"
    )
    icono = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Clase CSS del ícono (ej: ri-dashboard-line)"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden de visualización en el menú"
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si el módulo está activo en el sistema"
    )
    
    class Meta:
        ordering = ['orden', 'nombre']
        verbose_name = 'Módulo del Sistema'
        verbose_name_plural = 'Módulos del Sistema'
    
    def __str__(self):
        return self.nombre


class OpcionMenu(models.Model):
    """
    Opciones individuales dentro de cada módulo
    """
    modulo = models.ForeignKey(
        ModuloSistema,
        on_delete=models.CASCADE,
        related_name='opciones'
    )
    codigo = models.CharField(
        max_length=50,
        unique=True,
        help_text="Código único de la opción (ej: dashboard_ventas, pos_dashboard)"
    )
    nombre = models.CharField(
        max_length=100,
        help_text="Nombre de la opción en el menú"
    )
    url_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Nombre de la URL en Django (para reverse)"
    )
    url_path = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Path directo de la URL (ej: /app/pos-dashboard/)"
    )
    icono = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Clase CSS del ícono"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden dentro del módulo"
    )
    es_submenu = models.BooleanField(
        default=False,
        help_text="Si es un submenú con opciones hijas"
    )
    padre = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='hijos',
        help_text="Opción padre si es submenú"
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si la opción está activa"
    )
    
    class Meta:
        ordering = ['modulo', 'orden', 'nombre']
        verbose_name = 'Opción de Menú'
        verbose_name_plural = 'Opciones de Menú'
    
    def __str__(self):
        return f"{self.modulo.nombre} - {self.nombre}"


class PermisoRol(models.Model):
    """
    Define qué roles tienen acceso a qué opciones del menú
    """
    # Roles del sistema - todos los usuarios usan estos roles para permisos
    # is_superuser de Django NO otorga privilegios. Solo importa el rol asignado.
    ROLES_CHOICES = [
        ('administrador', 'Administrador'),
        ('administracion', 'Administración'),
        ('jefe_local', 'Jefe Local'),
        ('cajero', 'Cajero'),
        ('vendedor', 'Vendedor'),
    ]
    
    rol = models.CharField(
        max_length=50,
        choices=ROLES_CHOICES,
        help_text="Rol de usuario"
    )
    opcion_menu = models.ForeignKey(
        OpcionMenu,
        on_delete=models.CASCADE,
        related_name='permisos'
    )
    puede_ver = models.BooleanField(
        default=True,
        help_text="Puede ver la opción en el menú"
    )
    puede_crear = models.BooleanField(
        default=False,
        help_text="Puede crear nuevos registros"
    )
    puede_editar = models.BooleanField(
        default=False,
        help_text="Puede editar registros existentes"
    )
    puede_eliminar = models.BooleanField(
        default=False,
        help_text="Puede eliminar registros"
    )
    puede_exportar = models.BooleanField(
        default=False,
        help_text="Puede exportar datos"
    )
    puede_aprobar = models.BooleanField(
        default=False,
        help_text="Puede aprobar operaciones (cambios de precio, devoluciones, etc.)"
    )
    limite_descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Límite máximo de descuento en porcentaje (0-100) que puede aplicar este rol"
    )
    
    class Meta:
        unique_together = ('rol', 'opcion_menu')
        verbose_name = 'Permiso por Rol'
        verbose_name_plural = 'Permisos por Rol'
    
    def __str__(self):
        return f"{self.get_rol_display()} - {self.opcion_menu.nombre}"
    
    @classmethod
    def tiene_permiso(cls, usuario, codigo_opcion, tipo_permiso='puede_ver', sucursal_id=None):
        """
        Verifica si un usuario tiene un permiso específico para una opción.
        Orden de prioridad:
        1. PermisoUsuario (override por usuario) - si no es None, prevalece
        2. PermisoRol (permiso por rol)
        3. PermisoSucursal (restricción por sucursal)

        Args:
            usuario: Instancia del usuario
            codigo_opcion: Código de la opción del menú
            tipo_permiso: puede_ver, puede_crear, puede_editar, puede_eliminar, puede_exportar, puede_aprobar
            sucursal_id: ID de la sucursal actual del usuario (opcional)

        Returns:
            bool: True si tiene permiso, False en caso contrario
        """
        try:
            opcion = OpcionMenu.objects.get(codigo=codigo_opcion, activo=True)

            # 1. Verificar override por usuario (PermisoUsuario)
            permiso_usuario = PermisoUsuario.objects.filter(
                usuario=usuario,
                opcion_menu=opcion
            ).first()

            if permiso_usuario:
                override_valor = getattr(permiso_usuario, tipo_permiso, None)
                if override_valor is not None:
                    # Si hay override explícito, usarlo directamente
                    # (pero aún verificar sucursal si es True)
                    if not override_valor:
                        return False
                    # Override es True, verificar sucursal y retornar
                    if sucursal_id:
                        tipo_permiso_sucursal = 'habilitado' if tipo_permiso == 'puede_ver' else tipo_permiso
                        permiso_sucursal = PermisoSucursal.objects.filter(
                            sucursal_id=sucursal_id,
                            opcion_menu=opcion
                        ).first()
                        if permiso_sucursal:
                            if not getattr(permiso_sucursal, tipo_permiso_sucursal, True):
                                return False
                    return True

            # 2. Verificar permiso por rol
            permiso = cls.objects.filter(
                rol=usuario.rol,
                opcion_menu=opcion
            ).first()

            tiene_permiso_rol = False
            if permiso:
                tiene_permiso_rol = getattr(permiso, tipo_permiso, False)

            if not tiene_permiso_rol:
                return False

            # 3. Verificar permiso por sucursal (si se proporcionó sucursal_id)
            if sucursal_id:
                tipo_permiso_sucursal = 'habilitado' if tipo_permiso == 'puede_ver' else tipo_permiso

                permiso_sucursal = PermisoSucursal.objects.filter(
                    sucursal_id=sucursal_id,
                    opcion_menu=opcion
                ).first()

                if permiso_sucursal:
                    tiene_permiso_sucursal = getattr(permiso_sucursal, tipo_permiso_sucursal, True)
                    if not tiene_permiso_sucursal:
                        return False

            return True
        except OpcionMenu.DoesNotExist:
            return False
    
    @classmethod
    def opciones_disponibles_para_usuario(cls, usuario):
        """
        Retorna todas las opciones del menú disponibles para un usuario
        """
        # Todos los usuarios respetan los permisos configurados por rol
        opciones_ids = cls.objects.filter(
            rol=usuario.rol,
            puede_ver=True,
            opcion_menu__activo=True
        ).values_list('opcion_menu_id', flat=True)
        
        return OpcionMenu.objects.filter(id__in=opciones_ids, activo=True)


class ConfiguracionPermisoGlobal(models.Model):
    """
    Configuración global de permisos y restricciones del sistema
    """
    clave = models.CharField(
        max_length=100,
        unique=True,
        help_text="Clave de configuración"
    )
    valor = models.TextField(
        help_text="Valor de la configuración"
    )
    descripcion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción de la configuración"
    )
    tipo_dato = models.CharField(
        max_length=20,
        choices=[
            ('string', 'Texto'),
            ('integer', 'Número Entero'),
            ('float', 'Número Decimal'),
            ('boolean', 'Booleano'),
            ('json', 'JSON'),
        ],
        default='string'
    )
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    usuario_modificacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='configs_modificadas'
    )
    
    class Meta:
        verbose_name = 'Configuración Global de Permisos'
        verbose_name_plural = 'Configuraciones Globales de Permisos'
    
    def __str__(self):
        return f"{self.clave}: {self.valor}"
    
    def get_valor(self):
        """Retorna el valor convertido según el tipo de dato"""
        import json
        
        if self.tipo_dato == 'boolean':
            return self.valor.lower() in ('true', '1', 'yes', 'si')
        elif self.tipo_dato == 'integer':
            return int(self.valor)
        elif self.tipo_dato == 'float':
            return float(self.valor)
        elif self.tipo_dato == 'json':
            return json.loads(self.valor)
        else:
            return self.valor


class PermisoSucursal(models.Model):
    """
    Define qué opciones del menú están habilitadas para cada sucursal.
    Permite restringir funcionalidades específicas por sucursal.
    Por ejemplo: Una sucursal vendedora no puede crear productos ni hacer compras.
    """
    sucursal = models.ForeignKey(
        'app.Sucursal',
        on_delete=models.CASCADE,
        related_name='permisos_sucursal',
        help_text="Sucursal a la que aplica este permiso"
    )
    opcion_menu = models.ForeignKey(
        OpcionMenu,
        on_delete=models.CASCADE,
        related_name='permisos_sucursal'
    )
    habilitado = models.BooleanField(
        default=True,
        help_text="Si esta opción está habilitada para la sucursal"
    )
    puede_crear = models.BooleanField(
        default=True,
        help_text="Puede crear nuevos registros desde esta sucursal"
    )
    puede_editar = models.BooleanField(
        default=True,
        help_text="Puede editar registros desde esta sucursal"
    )
    puede_eliminar = models.BooleanField(
        default=False,
        help_text="Puede eliminar registros desde esta sucursal"
    )
    puede_exportar = models.BooleanField(
        default=True,
        help_text="Puede exportar datos desde esta sucursal"
    )
    puede_aprobar = models.BooleanField(
        default=False,
        help_text="Puede aprobar operaciones desde esta sucursal"
    )
    notas = models.TextField(
        blank=True,
        null=True,
        help_text="Notas sobre por qué esta restricción está aplicada"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('sucursal', 'opcion_menu')
        verbose_name = 'Permiso por Sucursal'
        verbose_name_plural = 'Permisos por Sucursal'
        ordering = ['sucursal', 'opcion_menu']
    
    def __str__(self):
        estado = "Habilitado" if self.habilitado else "Deshabilitado"
        return f"{self.sucursal.alias} - {self.opcion_menu.nombre} ({estado})"
    
    @classmethod
    def sucursal_tiene_permiso(cls, sucursal, codigo_opcion, tipo_permiso='habilitado'):
        """
        Verifica si una sucursal tiene un permiso específico para una opción.
        
        Args:
            sucursal: Instancia de la sucursal o ID de la sucursal
            codigo_opcion: Código de la opción del menú
            tipo_permiso: habilitado, puede_crear, puede_editar, puede_eliminar, puede_exportar, puede_aprobar
        
        Returns:
            bool: True si tiene permiso, False en caso contrario
        """
        try:
            if isinstance(sucursal, int):
                sucursal_id = sucursal
            else:
                sucursal_id = sucursal.id
            
            opcion = OpcionMenu.objects.get(codigo=codigo_opcion, activo=True)
            permiso = cls.objects.filter(
                sucursal_id=sucursal_id,
                opcion_menu=opcion
            ).first()
            
            if permiso:
                return getattr(permiso, tipo_permiso, True)
            
            # Si no hay permiso definido para la sucursal, está permitido por defecto
            return True
        except OpcionMenu.DoesNotExist:
            return True
    
    @classmethod
    def opciones_disponibles_para_sucursal(cls, sucursal):
        """
        Retorna las opciones del menú habilitadas para una sucursal
        """
        if isinstance(sucursal, int):
            sucursal_id = sucursal
        else:
            sucursal_id = sucursal.id
        
        # Obtener opciones deshabilitadas para la sucursal
        opciones_deshabilitadas = cls.objects.filter(
            sucursal_id=sucursal_id,
            habilitado=False
        ).values_list('opcion_menu_id', flat=True)
        
        # Retornar todas las opciones activas excepto las deshabilitadas
        return OpcionMenu.objects.filter(activo=True).exclude(id__in=opciones_deshabilitadas)


class PermisoUsuario(models.Model):
    """
    Permisos específicos por usuario que sobreescriben los permisos del rol.
    Permite dar o quitar acceso a opciones específicas para un usuario individual.

    Lógica de override:
    - True = otorgar permiso (aunque el rol no lo tenga)
    - False = denegar permiso (aunque el rol lo tenga)
    - None = usar permiso del rol (por defecto)
    """
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='permisos_usuario'
    )
    opcion_menu = models.ForeignKey(
        OpcionMenu,
        on_delete=models.CASCADE,
        related_name='permisos_usuario'
    )
    puede_ver = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    puede_crear = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    puede_editar = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    puede_eliminar = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    puede_exportar = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    puede_aprobar = models.BooleanField(
        null=True, blank=True, default=None,
        help_text="True=otorgar, False=denegar, None=usar rol"
    )
    limite_descuento_override = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Override del límite de descuento. None=usar límite del rol"
    )
    puede_ver_todas_sucursales = models.BooleanField(
        default=False,
        help_text="Si True, el usuario puede ver datos de todas las sucursales en reportes y listados"
    )
    notas = models.TextField(
        blank=True, null=True,
        help_text="Razón del override o notas administrativas"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('usuario', 'opcion_menu')
        verbose_name = 'Permiso por Usuario'
        verbose_name_plural = 'Permisos por Usuario'
        ordering = ['usuario', 'opcion_menu']

    def __str__(self):
        return f"{self.usuario.username} - {self.opcion_menu.nombre}"

    @classmethod
    def obtener_override(cls, usuario, codigo_opcion, tipo_permiso='puede_ver'):
        """
        Obtiene el override de permiso para un usuario y opción específicos.
        Returns: True/False si hay override, None si debe usar rol.
        """
        try:
            opcion = OpcionMenu.objects.get(codigo=codigo_opcion, activo=True)
            permiso = cls.objects.filter(
                usuario=usuario,
                opcion_menu=opcion
            ).first()

            if permiso:
                return getattr(permiso, tipo_permiso, None)
            return None
        except OpcionMenu.DoesNotExist:
            return None

    @classmethod
    def usuario_ve_todas_sucursales(cls, usuario):
        """Verifica si el usuario tiene flag de ver todas las sucursales en algún permiso."""
        return cls.objects.filter(
            usuario=usuario,
            puede_ver_todas_sucursales=True
        ).exists()


class PermisoTemporalCambio(models.Model):
    """Autorización temporal y acotada para acciones destructivas de cambios."""

    ACCION_CANCELAR = 'CANCELAR'
    ACCION_REVERTIR = 'REVERTIR'
    ACCIONES = [
        (ACCION_CANCELAR, 'Cancelar solicitud de cambio'),
        (ACCION_REVERTIR, 'Revertir cambio ejecutado'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='permisos_temporales_cambios',
    )
    empresa = models.ForeignKey(
        'app.Empresa',
        on_delete=models.CASCADE,
        related_name='permisos_temporales_cambios',
    )
    sucursal = models.ForeignKey(
        'app.Sucursal',
        on_delete=models.CASCADE,
        related_name='permisos_temporales_cambios',
    )
    accion = models.CharField(max_length=20, choices=ACCIONES)
    otorgado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='permisos_temporales_cambios_otorgados',
    )
    codigo_autorizacion = models.ForeignKey(
        'app.CodigoAutorizacionDinamico',
        on_delete=models.PROTECT,
        related_name='permisos_temporales_cambios',
    )
    motivo = models.TextField()
    vigente_desde = models.DateTimeField(default=timezone.now)
    vigente_hasta = models.DateTimeField(db_index=True)
    revocado_en = models.DateTimeField(null=True, blank=True)
    revocado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='permisos_temporales_cambios_revocados',
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Permiso temporal de cambios'
        verbose_name_plural = 'Permisos temporales de cambios'
        ordering = ['-creado_en']
        indexes = [
            models.Index(
                fields=['usuario', 'empresa', 'sucursal', 'accion', 'vigente_hasta'],
                name='app_ptc_vigencia_idx',
            ),
            models.Index(
                fields=['otorgado_por', 'creado_en'],
                name='app_ptc_otorgado_idx',
            ),
        ]

    @property
    def esta_vigente(self):
        ahora = timezone.now()
        return (
            self.revocado_en is None
            and self.vigente_desde <= ahora < self.vigente_hasta
        )

    @classmethod
    def vigente_para(cls, usuario, empresa_id, sucursal_id, accion, ahora=None):
        ahora = ahora or timezone.now()
        return cls.objects.filter(
            usuario=usuario,
            empresa_id=empresa_id,
            sucursal_id=sucursal_id,
            accion=accion,
            vigente_desde__lte=ahora,
            vigente_hasta__gt=ahora,
            revocado_en__isnull=True,
        ).select_related('otorgado_por').order_by('-vigente_hasta').first()


# ========== SISTEMA DE CÓDIGOS DE AUTORIZACIÓN DINÁMICOS ==========

class CodigoAutorizacionDinamico(models.Model):
    """
    Códigos dinámicos que cambian cada hora para autorizar operaciones críticas.
    Cada supervisor (administrador/jefe_local) recibe su propio código único.
    Los códigos son de un solo uso: tras validarse, se marca como usado y el
    supervisor debe generar uno nuevo.
    """
    codigo = models.CharField(max_length=6, db_index=True, verbose_name='Código de 6 dígitos')
    fecha_hora_inicio = models.DateTimeField(verbose_name='Fecha y hora de inicio de validez')
    fecha_hora_fin = models.DateTimeField(verbose_name='Fecha y hora de fin de validez')
    creado_en = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False, verbose_name='¿Código usado?')
    activo = models.BooleanField(default=True, verbose_name='¿Código activo?')
    tipo_codigo = models.CharField(
        max_length=20,
        choices=[('HORARIO', 'Horario'), ('TRANSACCION', 'Por Transacción')],
        default='HORARIO',
        verbose_name='Tipo de código'
    )
    operacion_asociada = models.CharField(
        max_length=50, null=True, blank=True,
        verbose_name='Operación asociada (para códigos por transacción)'
    )
    generado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='codigos_autorizacion_generados',
        verbose_name='Supervisor que generó el código'
    )
    
    class Meta:
        verbose_name = 'Código de Autorización Dinámico'
        verbose_name_plural = 'Códigos de Autorización Dinámicos'
        ordering = ['-fecha_hora_inicio']
        indexes = [
            models.Index(fields=['codigo', 'activo', 'usado']),
            models.Index(fields=['fecha_hora_inicio', 'fecha_hora_fin']),
            models.Index(fields=['generado_por', 'activo', 'usado']),
        ]
    
    def __str__(self):
        estado = "USADO" if self.usado else "VIGENTE"
        supervisor = self.generado_por.get_full_name() if self.generado_por else "Sistema"
        return f"{self.codigo} ({estado}) - {supervisor} - {self.fecha_hora_inicio.strftime('%H:%M')} a {self.fecha_hora_fin.strftime('%H:%M')}"
    
    def es_valido(self):
        """Verifica si el código está dentro del rango de tiempo válido y no ha sido usado"""
        import pytz
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        return (
            self.activo
            and not self.usado
            and self.fecha_hora_inicio <= ahora <= self.fecha_hora_fin
        )

    def marcar_como_usado(self):
        """Marca el código como usado de forma atómica"""
        self.usado = True
        self.save(update_fields=['usado'])
    
    @classmethod
    def obtener_codigo_actual(cls, usuario):
        """Obtiene o crea el código válido para la hora actual de un supervisor específico"""
        import pytz
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        codigo_valido = cls.objects.filter(
            fecha_hora_inicio__lte=ahora,
            fecha_hora_fin__gte=ahora,
            activo=True,
            usado=False,
            generado_por=usuario
        ).first()
        
        if codigo_valido:
            return codigo_valido
        
        return cls.generar_codigo_horario(usuario)
    
    @classmethod
    def _generar_codigo_aleatorio(cls):
        """Genera un código numérico de 6 dígitos criptográficamente seguro"""
        import secrets
        return f"{secrets.randbelow(1000000):06d}"

    @classmethod
    def generar_codigo_horario(cls, usuario):
        """Genera un código aleatorio único para la hora actual, vinculado a un supervisor"""
        import pytz
        
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        hora_inicio = ahora.replace(minute=0, second=0, microsecond=0)
        hora_fin = hora_inicio + timezone.timedelta(hours=1)
        
        max_intentos = 10
        for intento in range(max_intentos):
            codigo_numerico = cls._generar_codigo_aleatorio()
            
            if not cls.objects.filter(
                codigo=codigo_numerico,
                activo=True,
                usado=False,
                fecha_hora_inicio__lte=ahora,
                fecha_hora_fin__gte=ahora
            ).exists():
                break
        else:
            raise ValueError("No se pudo generar un código único tras múltiples intentos")
        
        codigo_obj = cls.objects.create(
            codigo=codigo_numerico,
            fecha_hora_inicio=hora_inicio,
            fecha_hora_fin=hora_fin,
            activo=True,
            generado_por=usuario
        )

        return codigo_obj

    @classmethod
    def generar_codigo_transaccion(cls, supervisor, operacion_id):
        """
        Genera un código de un solo uso vinculado a una operación específica.
        Expira en 10 minutos.
        """
        import pytz

        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)

        hora_inicio = ahora
        hora_fin = ahora + timezone.timedelta(minutes=10)

        max_intentos = 10
        for _ in range(max_intentos):
            codigo_numerico = cls._generar_codigo_aleatorio()
            if not cls.objects.filter(
                codigo=codigo_numerico, activo=True, usado=False,
                fecha_hora_inicio__lte=ahora, fecha_hora_fin__gte=ahora
            ).exists():
                break

        codigo_obj = cls.objects.create(
            codigo=codigo_numerico,
            fecha_hora_inicio=hora_inicio,
            fecha_hora_fin=hora_fin,
            activo=True,
            tipo_codigo='TRANSACCION',
            operacion_asociada=str(operacion_id),
            generado_por=supervisor
        )

        return codigo_obj

    @classmethod
    def validar_codigo(cls, codigo_ingresado):
        """
        Valida si un código ingresado es correcto y vigente.
        
        Returns:
            tuple: (es_valido, mensaje, codigo_obj)
        """
        if not codigo_ingresado or len(str(codigo_ingresado).strip()) != 6:
            return False, 'El código debe tener 6 dígitos', None
        
        import pytz
        codigo_ingresado = str(codigo_ingresado).strip()
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        codigo_obj = cls.objects.filter(
            codigo=codigo_ingresado,
            activo=True,
            usado=False
        ).first()
        
        if not codigo_obj:
            codigo_usado = cls.objects.filter(
                codigo=codigo_ingresado,
                activo=True,
                usado=True
            ).first()
            if codigo_usado:
                return False, 'Este código ya fue utilizado. Solicite uno nuevo al supervisor.', None
            return False, 'Código de autorización inválido', None
        
        if not codigo_obj.es_valido():
            return False, 'Código de autorización expirado', None
        
        return True, 'Código válido', codigo_obj


class RegistroAutorizacion(models.Model):
    """
    Auditoría de todas las autorizaciones realizadas con códigos dinámicos
    """
    TIPO_OPERACION_CHOICES = [
        ('APROBACION_CAMBIO', 'Aprobación de Cambio/Devolución'),
        ('DESCUENTO_ESPECIAL', 'Descuento Especial'),
        ('ANULACION_VENTA', 'Anulación de Venta'),
        ('AJUSTE_PRECIO', 'Ajuste de Precio'),
        ('OTRO', 'Otro'),
    ]
    
    codigo_usado = models.ForeignKey(
        CodigoAutorizacionDinamico,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Código usado'
    )
    usuario_solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='autorizaciones_solicitadas',
        verbose_name='Usuario que solicitó la autorización'
    )
    tipo_operacion = models.CharField(
        max_length=50,
        choices=TIPO_OPERACION_CHOICES,
        verbose_name='Tipo de operación'
    )
    descripcion = models.TextField(
        blank=True,
        verbose_name='Descripción de la operación'
    )
    fecha_hora = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha y hora de la autorización'
    )
    ip_origen = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP de origen'
    )
    datos_adicionales = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Datos adicionales'
    )
    exitoso = models.BooleanField(
        default=True,
        verbose_name='¿Autorización exitosa?'
    )
    
    # Referencia a la operación autorizada (opcional)
    cambio_devolucion = models.ForeignKey(
        'CambioDevolucion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='autorizaciones'
    )

    # === TRAZABILIDAD CROSS-BRANCH ===
    usuario_autorizador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='autorizaciones_otorgadas',
        verbose_name='Supervisor que autorizó'
    )
    sucursal_solicitante = models.ForeignKey(
        'Sucursal',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='autorizaciones_solicitadas_sucursal',
        verbose_name='Sucursal que solicitó'
    )
    sucursal_autorizador = models.ForeignKey(
        'Sucursal',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='autorizaciones_otorgadas_sucursal',
        verbose_name='Sucursal del autorizador'
    )
    es_cross_branch = models.BooleanField(
        default=False,
        verbose_name='¿Autorización entre sucursales?'
    )
    requiere_revision = models.BooleanField(
        default=False,
        verbose_name='¿Requiere revisión gerencial?'
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='autorizaciones_revisadas',
        verbose_name='Revisado por'
    )
    fecha_revision = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha de revisión'
    )
    notas_revision = models.TextField(
        blank=True, default='',
        verbose_name='Notas de revisión'
    )

    class Meta:
        verbose_name = 'Registro de Autorización'
        verbose_name_plural = 'Registros de Autorizaciones'
        ordering = ['-fecha_hora']
        indexes = [
            models.Index(fields=['-fecha_hora']),
            models.Index(fields=['usuario_solicitante', '-fecha_hora']),
            models.Index(fields=['tipo_operacion', '-fecha_hora']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_operacion_display()} - {self.fecha_hora.strftime('%d/%m/%Y %H:%M')} - {self.usuario_solicitante}"
