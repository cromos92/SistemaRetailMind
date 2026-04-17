"""
Optimiza imágenes de producto para POS:

  - Redimensiona a 400x400 máximo (manteniendo aspect ratio).
  - Genera versión WebP (calidad 82) junto al original.
  - Re-comprime el original JPEG/PNG con calidad 85.

Uso:
    python manage.py optimizar_imagenes_productos
    python manage.py optimizar_imagenes_productos --ruta media/productos --dry-run
    python manage.py optimizar_imagenes_productos --tamano 512 --calidad 90
"""
from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    from PIL import Image, ImageOps
except ImportError as e:
    Image = None
    ImageOps = None
    _PIL_IMPORT_ERROR = e
else:
    _PIL_IMPORT_ERROR = None


EXTENSIONES_ORIGEN = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}


class Command(BaseCommand):
    help = "Reduce y convierte a WebP las imágenes de producto (400x400 por defecto)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ruta",
            type=str,
            default=None,
            help="Subcarpeta dentro de MEDIA_ROOT (por defecto 'productos').",
        )
        parser.add_argument(
            "--tamano",
            type=int,
            default=400,
            help="Lado máximo en píxeles (default 400).",
        )
        parser.add_argument(
            "--calidad",
            type=int,
            default=82,
            help="Calidad WebP (0-100, default 82).",
        )
        parser.add_argument(
            "--calidad-jpeg",
            type=int,
            default=85,
            help="Calidad del JPEG recomprimido (default 85).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Solo muestra qué haría, sin escribir archivos.",
        )
        parser.add_argument(
            "--forzar",
            action="store_true",
            help="Regenera WebP aunque ya exista.",
        )

    def handle(self, *args, **opts):
        if _PIL_IMPORT_ERROR is not None:
            raise CommandError(
                f"Pillow no está disponible: {_PIL_IMPORT_ERROR}. "
                "Instálalo con `pip install Pillow`."
            )

        media_root = Path(settings.MEDIA_ROOT)
        subruta = opts["ruta"] or "productos"
        carpeta = media_root / subruta

        if not carpeta.exists():
            raise CommandError(f"No existe la carpeta: {carpeta}")

        tamano = int(opts["tamano"])
        calidad_webp = int(opts["calidad"])
        calidad_jpeg = int(opts["calidad_jpeg"])
        dry_run = bool(opts["dry_run"])
        forzar = bool(opts["forzar"])

        self.stdout.write(
            self.style.NOTICE(
                f"Carpeta: {carpeta} · tamaño máx: {tamano}px · "
                f"calidad WebP: {calidad_webp} · dry-run: {dry_run}"
            )
        )

        total = procesadas = saltadas = erroradas = 0
        bytes_antes = bytes_despues = 0

        for archivo in sorted(carpeta.rglob("*")):
            if not archivo.is_file():
                continue
            if archivo.suffix.lower() not in EXTENSIONES_ORIGEN:
                continue

            total += 1
            tam_original = archivo.stat().st_size
            bytes_antes += tam_original

            webp_target = archivo.with_suffix(".webp")

            if webp_target.exists() and not forzar:
                saltadas += 1
                bytes_despues += webp_target.stat().st_size
                continue

            try:
                with Image.open(archivo) as im:
                    im = ImageOps.exif_transpose(im)

                    modo_destino = "RGB" if archivo.suffix.lower() in {".jpg", ".jpeg"} else "RGBA"
                    if im.mode != modo_destino:
                        im = im.convert(modo_destino)

                    im.thumbnail((tamano, tamano), Image.LANCZOS)

                    if dry_run:
                        self.stdout.write(
                            f"[dry-run] {archivo.relative_to(media_root)} -> "
                            f"{webp_target.relative_to(media_root)} "
                            f"({im.size[0]}x{im.size[1]})"
                        )
                        procesadas += 1
                        continue

                    im.save(
                        webp_target,
                        format="WEBP",
                        quality=calidad_webp,
                        method=4,
                    )
                    nuevo_tam = webp_target.stat().st_size
                    bytes_despues += nuevo_tam

                    if archivo.suffix.lower() in {".jpg", ".jpeg"}:
                        im.save(archivo, format="JPEG", quality=calidad_jpeg, optimize=True)
                    elif archivo.suffix.lower() == ".png":
                        im.save(archivo, format="PNG", optimize=True)

                    procesadas += 1

            except Exception as exc:
                erroradas += 1
                self.stderr.write(
                    self.style.ERROR(f"Error procesando {archivo}: {exc}")
                )
                continue

        reduccion_pct = 0.0
        if bytes_antes > 0:
            reduccion_pct = (1 - bytes_despues / bytes_antes) * 100

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTerminado: {procesadas} procesadas, {saltadas} saltadas (ya había WebP), "
                f"{erroradas} con error, {total} totales.\n"
                f"Antes: {bytes_antes/1024:.1f} KB · Después (WebP + JPEG recomprimido): "
                f"{bytes_despues/1024:.1f} KB · Ahorro aprox: {reduccion_pct:.1f}%."
            )
        )

        if procesadas == 0 and total == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No se encontraron imágenes. Verifica --ruta o MEDIA_ROOT."
                )
            )
            sys.exit(0)
