"""Compresión de las fotos de evidencia antes de adjuntarlas al correo.

Lo que se protege acá es que la compresión NUNCA rompa un envío: ante cualquier
cosa rara devuelve el original en vez de levantar excepción. Una garantía que
no sale es peor que una foto pesada.
"""
from io import BytesIO
from unittest import mock

from django.test import SimpleTestCase

from PIL import Image

from app.services import fotos_evidencia


def _imagen(ancho, alto, formato='JPEG', modo='RGB', calidad=95):
    """Imagen sintética con degradado suave, que es como se comporta una foto.

    A propósito NO se usa ruido: el ruido de alta frecuencia es el peor caso
    para JPEG y sale más pesado que el PNG, con lo que la función devolvería el
    original y el test mediría otra cosa.
    """
    img = Image.new(modo, (ancho, alto))
    px = img.load()
    for x in range(ancho):
        for y in range(alto):
            canal = (x * 255 // max(ancho - 1, 1),
                     y * 255 // max(alto - 1, 1),
                     (x + y) * 255 // max(ancho + alto - 2, 1))
            px[x, y] = canal + (255,) if modo == 'RGBA' else canal
    buf = BytesIO()
    if formato == 'JPEG':
        img.save(buf, format=formato, quality=calidad)
    else:
        img.save(buf, format=formato)
    return buf.getvalue()


def _captura_pantalla(ancho, alto):
    """PNG de colores planos con líneas finas: lo que JPEG comprime PEOR."""
    img = Image.new('RGB', (ancho, alto), (255, 255, 255))
    px = img.load()
    for x in range(0, ancho, 20):
        for y in range(alto):
            px[x, y] = (0, 0, 0)
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


class ComprimirParaAdjuntoTest(SimpleTestCase):

    def test_reescala_foto_de_celular_y_baja_el_peso(self):
        original = _imagen(4032, 3024)
        nombre, contenido = fotos_evidencia.comprimir_para_adjunto('IMG_0626.jpg', original)

        self.assertLess(len(contenido), len(original))
        self.assertEqual(nombre, 'IMG_0626.jpg')
        img = Image.open(BytesIO(contenido))
        self.assertEqual(max(img.size), fotos_evidencia.LADO_MAX)
        # La proporción se conserva: una foto deformada no sirve como evidencia.
        self.assertAlmostEqual(img.size[0] / img.size[1], 4032 / 3024, places=2)

    def test_png_con_transparencia_no_revienta_y_pasa_a_jpg(self):
        # JPEG no tiene canal alfa: sin aplanar contra blanco, save() explota.
        original = _imagen(1200, 900, formato='PNG', modo='RGBA')
        nombre, contenido = fotos_evidencia.comprimir_para_adjunto('captura.png', original)

        self.assertEqual(nombre, 'captura.jpg')
        self.assertEqual(Image.open(BytesIO(contenido)).format, 'JPEG')

    def test_no_reescala_una_foto_ya_chica(self):
        original = _imagen(800, 600)
        _, contenido = fotos_evidencia.comprimir_para_adjunto('chica.jpg', original)

        self.assertEqual(Image.open(BytesIO(contenido)).size, (800, 600))

    def test_devuelve_el_original_si_comprimir_no_ayuda(self):
        # Una captura de pantalla (colores planos y líneas finas) es el caso en
        # que PNG le gana a JPEG: pasarla a JPEG la dejaría más pesada Y peor.
        # Se devuelve intacta.
        original = _captura_pantalla(1000, 800)
        nombre, contenido = fotos_evidencia.comprimir_para_adjunto('captura.png', original)

        self.assertEqual(nombre, 'captura.png')
        self.assertIs(contenido, original)

    def test_archivo_ilegible_pasa_intacto_sin_levantar(self):
        basura = b'esto no es una imagen'
        nombre, contenido = fotos_evidencia.comprimir_para_adjunto('roto.jpg', basura)

        self.assertEqual((nombre, contenido), ('roto.jpg', basura))

    def test_contenido_vacio_no_rompe(self):
        self.assertEqual(fotos_evidencia.comprimir_para_adjunto('x.jpg', b''), ('x.jpg', b''))

    def test_se_puede_apagar_por_configuracion(self):
        original = _imagen(4032, 3024)
        with mock.patch.object(fotos_evidencia, 'COMPRIMIR', False):
            nombre, contenido = fotos_evidencia.comprimir_para_adjunto('IMG.jpg', original)

        self.assertIs(contenido, original)
        self.assertEqual(nombre, 'IMG.jpg')

    def test_sin_pillow_devuelve_el_original(self):
        original = _imagen(4032, 3024)
        real_import = __builtins__['__import__'] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def sin_pillow(nombre, *args, **kwargs):
            if nombre == 'PIL':
                raise ImportError('sin Pillow')
            return real_import(nombre, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=sin_pillow):
            _, contenido = fotos_evidencia.comprimir_para_adjunto('IMG.jpg', original)

        self.assertIs(contenido, original)
