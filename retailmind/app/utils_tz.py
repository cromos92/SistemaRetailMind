"""Helpers de fecha/hora en zona horaria local (Chile).

Reglas del proyecto:
- NUNCA usar `date.today()`, `datetime.now()` o `timezone.now().date()` para
  obtener "hoy"/"ahora" porque dependen del huso del SO (UTC en producción).
- Para fechas/horas de negocio usar siempre `timezone.localdate()` y
  `timezone.localtime()` (respetan `TIME_ZONE = America/Santiago`).
- Estos helpers existen como atajo y para que el patrón quede explícito.

`timezone.now()` (aware UTC) sigue siendo correcto para asignar a campos
`DateTimeField`, ya que Django lo convierte a hora local al renderizar.
"""
from django.utils import timezone


def hoy():
    """Fecha actual en hora de Chile (`date`)."""
    return timezone.localdate()


def ahora_local():
    """Datetime aware en hora de Chile (`datetime`)."""
    return timezone.localtime()


def hora_actual():
    """Hora actual en Chile (`time`)."""
    return timezone.localtime().time()
