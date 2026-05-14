#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

# En Windows el stdout/stderr default es cp1252 y rompe con emojis usados
# en prints de debug del proyecto (📊, ✓, ❌, etc.). Reconfiguramos a UTF-8
# con errors='replace' para que ningún print de debug pueda tumbar un endpoint.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main():
    """Run administrative tasks."""
    os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
