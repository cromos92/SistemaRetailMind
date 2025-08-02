# Configuración para el sistema de gestión de usuarios de Olagreetings

# Configuración del modelo de usuario personalizado
AUTH_USER_MODEL = 'usuarios.Usuario'

# Configuración de correo electrónico
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'  # Cambiar según tu proveedor
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tu-email@gmail.com'  # Cambiar por tu email
EMAIL_HOST_PASSWORD = 'tu-password-app'  # Cambiar por tu contraseña de aplicación
DEFAULT_FROM_EMAIL = 'Olagreetings <tu-email@gmail.com>'

# Configuración de seguridad
PASSWORD_RESET_TIMEOUT = 86400  # 24 horas en segundos
SESSION_COOKIE_AGE = 3600  # 1 hora en segundos
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Configuración de paginación
USUARIOS_POR_PAGINA = 10
LOGS_POR_PAGINA = 20

# Configuración de validación
RUT_MIN_LENGTH = 7
RUT_MAX_LENGTH = 8

# Configuración de permisos
PERMISOS_USUARIOS = {
    'crear': 'puede_crear_usuarios',
    'editar': 'puede_editar_usuarios',
    'eliminar': 'puede_eliminar_usuarios',
}

# Configuración de logs
REGISTRAR_LOGS_ACCESO = True
MANTENER_LOGS_DIAS = 90  # Días para mantener logs de acceso

# Configuración de exportación
EXPORTACION_CSV_ENCODING = 'utf-8-sig'  # Para Excel
EXPORTACION_CSV_DELIMITER = ','

# Configuración de notificaciones
NOTIFICAR_CREACION_USUARIO = True
NOTIFICAR_RESET_PASSWORD = True
NOTIFICAR_CAMBIO_ESTADO = True

# Configuración de contraseñas temporales
PASSWORD_TEMP_LENGTH = 12
PASSWORD_TEMP_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*'

# Configuración de búsqueda
BUSQUEDA_USUARIOS_CAMPOS = [
    'username',
    'first_name',
    'last_name',
    'email',
    'rut',
    'empresa',
    'cargo',
    'departamento'
]

# Configuración de filtros
FILTROS_USUARIOS_DISPONIBLES = [
    'estado',
    'permisos',
    'fecha_creacion',
    'ultimo_acceso',
    'empresa',
    'departamento'
]

# Configuración de métricas
METRICAS_USUARIOS = [
    'total_usuarios',
    'usuarios_activos',
    'usuarios_inactivos',
    'superusuarios',
    'usuarios_nuevos_mes',
    'usuarios_ultimo_acceso'
]

# Configuración de seguridad adicional
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

# Configuración de middleware personalizado (opcional)
MIDDLEWARE_PERSONALIZADO = [
    'usuarios.middleware.LogAccesoMiddleware',  # Si implementas middleware personalizado
]

# Configuración de templates de correo
TEMPLATES_EMAIL = {
    'credenciales_usuario': {
        'html': 'usuarios/emails/credenciales_usuario.html',
        'txt': 'usuarios/emails/credenciales_usuario.txt',
        'subject': 'Bienvenido a Olagreetings - Tus Credenciales de Acceso'
    },
    'nueva_password': {
        'html': 'usuarios/emails/nueva_password.html',
        'txt': 'usuarios/emails/nueva_password.txt',
        'subject': 'Olagreetings - Nueva Contraseña Generada'
    }
}

# Configuración de validación de RUT
VALIDACION_RUT = {
    'permitir_formato_con_guion': True,
    'permitir_formato_con_puntos': True,
    'validar_digito_verificador': True,
    'mensajes_error': {
        'formato_invalido': 'El RUT debe tener 7 u 8 dígitos seguidos de un dígito verificador (0-9 o K)',
        'digito_incorrecto': 'El dígito verificador es incorrecto. Debería ser {digito}',
        'ya_existe': 'Ya existe un usuario con este RUT'
    }
}

# Configuración de permisos por defecto
PERMISOS_POR_DEFECTO = {
    'puede_crear_usuarios': False,
    'puede_editar_usuarios': False,
    'puede_eliminar_usuarios': False,
    'is_staff': False,
    'es_activo': True
}

# Configuración de auditoría
AUDITORIA_USUARIOS = {
    'registrar_creacion': True,
    'registrar_edicion': True,
    'registrar_eliminacion': True,
    'registrar_cambios_password': True,
    'registrar_cambios_estado': True,
    'mantener_historial_dias': 365
}

# Configuración de exportación avanzada
EXPORTACION_AVANZADA = {
    'formato_fecha': '%d/%m/%Y %H:%M',
    'incluir_campos_sensibles': False,  # No incluir contraseñas en exportación
    'comprimir_archivos': True,
    'limite_exportacion': 10000  # Máximo usuarios a exportar
}

# Configuración de notificaciones por correo
NOTIFICACIONES_EMAIL = {
    'crear_usuario': {
        'enviar_credenciales': True,
        'template': 'credenciales_usuario',
        'asunto_personalizado': 'Bienvenido a {sistema} - Tus Credenciales'
    },
    'reset_password': {
        'enviar_nueva_password': True,
        'template': 'nueva_password',
        'asunto_personalizado': '{sistema} - Nueva Contraseña Generada'
    },
    'cambio_estado': {
        'notificar_usuario': True,
        'template': 'cambio_estado',
        'asunto_personalizado': '{sistema} - Estado de Cuenta Actualizado'
    }
}

# Configuración de seguridad de contraseñas
SEGURIDAD_PASSWORD = {
    'longitud_minima': 8,
    'requerir_mayusculas': True,
    'requerir_minusculas': True,
    'requerir_numeros': True,
    'requerir_simbolos': True,
    'no_permitir_usuarios_comunes': True,
    'no_permitir_secuencias': True,
    'max_intentos_fallidos': 5,
    'tiempo_bloqueo_minutos': 30
}

# Configuración de sesiones
CONFIGURACION_SESIONES = {
    'tiempo_expiracion_horas': 24,
    'renovar_automaticamente': True,
    'cerrar_al_cerrar_navegador': True,
    'max_sesiones_por_usuario': 3,
    'registrar_actividad': True
}

# Configuración de logs de acceso
LOGS_ACCESO = {
    'registrar_ip': True,
    'registrar_user_agent': True,
    'registrar_exitos': True,
    'registrar_fallos': True,
    'registrar_intentos_fallidos': True,
    'alertar_intentos_sospechosos': True,
    'limite_intentos_sospechosos': 10,
    'tiempo_ventana_minutos': 15
}

# Configuración de backup automático
BACKUP_AUTOMATICO = {
    'habilitado': True,
    'frecuencia_dias': 7,
    'mantener_backups': 4,  # Número de backups a mantener
    'incluir_usuarios': True,
    'incluir_logs': True,
    'comprimir_backup': True
}

# Configuración de reportes
REPORTES_USUARIOS = {
    'reporte_usuarios_activos': True,
    'reporte_usuarios_inactivos': True,
    'reporte_ultimos_accesos': True,
    'reporte_permisos': True,
    'reporte_creaciones_mes': True,
    'formato_fecha_reporte': '%d/%m/%Y',
    'incluir_graficos': True
}

# Configuración de integración con otros sistemas
INTEGRACION_SISTEMAS = {
    'ldap': {
        'habilitado': False,
        'servidor': 'ldap://localhost',
        'puerto': 389,
        'base_dn': 'dc=example,dc=com',
        'usuario_admin': 'admin',
        'password_admin': 'password'
    },
    'active_directory': {
        'habilitado': False,
        'servidor': 'ldap://dc.example.com',
        'dominio': 'example.com',
        'base_dn': 'DC=example,DC=com'
    },
    'oauth': {
        'google': {
            'habilitado': False,
            'client_id': '',
            'client_secret': '',
            'redirect_uri': ''
        },
        'microsoft': {
            'habilitado': False,
            'client_id': '',
            'client_secret': '',
            'redirect_uri': ''
        }
    }
}

# Configuración de monitoreo
MONITOREO_SISTEMA = {
    'verificar_usuarios_inactivos': True,
    'dias_inactividad_limite': 90,
    'notificar_administradores': True,
    'verificar_permisos_duplicados': True,
    'verificar_emails_duplicados': True,
    'verificar_ruts_duplicados': True,
    'generar_reportes_automaticos': True,
    'frecuencia_revision_horas': 24
}

# Configuración de desarrollo
DESARROLLO = {
    'debug_emails': True,  # En desarrollo, mostrar emails en consola
    'crear_superusuario_automatico': True,
    'datos_prueba': True,
    'log_detallado': True
} 