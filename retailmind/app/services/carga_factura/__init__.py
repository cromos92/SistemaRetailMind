"""
Motor de carga de facturas de compra: transcripción JSON → vista previa →
carga por el mismo camino que el modal "Crear Producto Manual".

Lo usan los comandos cargar_productos_factura, revisar_guias_talla y
ajustar_productos_factura, y lo usará la pantalla "Cargar desde factura".

Módulos:
  perfiles     reglas por marca (tallas y guías, siglas de género, precio)
  facturas     JSON de la factura, DTE, usuario, margen de la bodega
  planificador qué pasa con cada línea (NUEVO / EXISTE / ERROR...), sin escribir
  aplicador    carga de una línea planificada (POST del modal, todo o nada)
  guias        revisar / crear / ajustar las guías de talla
  ajustes      correcciones de precio y color después de la carga
  tallas, precios  funciones de formato y cálculo
"""
from .facturas import ErrorCarga  # noqa: F401
from .perfiles import Perfil, perfil_para  # noqa: F401
