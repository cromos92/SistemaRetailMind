"""Compresión de las fotos de evidencia antes de mandarlas por correo.

Las fotos llegan tal como salen del celular: 4032x3024 y hasta 4,4MB cada una.
El correo al proveedor las adjuntaba sin tocar, y cuatro fotos así arman un
mensaje de 15,7MB en el cable (el base64 infla un 35%). Ese mensaje se sube al
relay con un solo `sendall`, y el timeout del socket corre sobre la operación
COMPLETA: si la subida no alcanza a terminar dentro del plazo, Python corta con
`SMTPServerDisconnected('Server not connected')` y la garantía nunca sale.

Reescaladas a 2400px con calidad 85 las mismas cuatro fotos pesan 2,4MB —78%
menos— y el correo queda en 3,9MB, que sube en un par de segundos. A 2400px
quedan 4,3 megapíxeles: alcanza de sobra para que el proveedor amplíe una
partidura fina en la planta, que es de lo que depende que curse la garantía.

Distinto del reescalado del PDF (`pdf_requerimiento_proveedor.FOTO_LADO_MAX`,
1000px y calidad 72): ese tamaño es para que la foto se vea en la hoja, no para
hacerle zoom. Por eso las originales van ADEMÁS como adjunto.
"""
import logging
import os
from io import BytesIO

logger = logging.getLogger('app')

# Ajustables por entorno sin tocar código. Subir LADO_MAX mejora el detalle y
# engorda el correo; bajarlo hace lo contrario. Medido sobre el requerimiento
# 12 (4 fotos, 11,22MB en original): 3000px→3,4MB, 2400px→2,4MB, 2000px→1,8MB,
# 1600px→1,2MB.
LADO_MAX = int(os.environ.get('REQUERIMIENTOS_FOTO_LADO_MAX', '2400'))
CALIDAD = int(os.environ.get('REQUERIMIENTOS_FOTO_CALIDAD', '85'))
# Válvula de escape: si algún proveedor exige el archivo intacto, se apaga con
# REQUERIMIENTOS_COMPRIMIR_FOTOS=false sin necesidad de deploy.
COMPRIMIR = os.environ.get('REQUERIMIENTOS_COMPRIMIR_FOTOS', 'true').lower() != 'false'


def comprimir_para_adjunto(nombre, contenido):
    """Devuelve (nombre, bytes) listos para adjuntar al correo.

    Nunca levanta excepción y nunca devuelve algo más pesado que lo que
    recibió: si Pillow falla, si el formato es raro o si la foto ya venía
    optimizada, se devuelve el original. Perder el envío de una garantía por un
    problema de compresión sería peor que mandar la foto pesada.
    """
    if not COMPRIMIR or not contenido:
        return nombre, contenido

    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow es dependencia del proyecto
        logger.warning('Pillow no disponible: las fotos van al proveedor sin comprimir')
        return nombre, contenido

    try:
        img = Image.open(BytesIO(contenido))
        # La rotación de las fotos de celular vive en el EXIF, no en los
        # píxeles: al reescribir el archivo se pierde, y la evidencia le
        # llegaría acostada al proveedor.
        img = ImageOps.exif_transpose(img)
        if img.mode in ('RGBA', 'LA', 'P'):
            # JPEG no tiene canal alfa: sin aplanar contra blanco, un PNG con
            # transparencia revienta al guardar.
            fondo = Image.new('RGB', img.size, (255, 255, 255))
            fondo.paste(img, mask=img.convert('RGBA').split()[-1])
            img = fondo
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        if max(img.size) > LADO_MAX:
            img.thumbnail((LADO_MAX, LADO_MAX), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=CALIDAD, optimize=True, progressive=True)
        comprimido = buf.getvalue()
    except Exception as e:
        logger.warning('No se pudo comprimir la foto %s, va en su formato original: %s',
                       nombre, e)
        return nombre, contenido

    if len(comprimido) >= len(contenido):
        # Ya venía liviana: reescribirla solo agregaría pérdida de calidad.
        return nombre, contenido

    return f'{os.path.splitext(nombre)[0]}.jpg', comprimido
