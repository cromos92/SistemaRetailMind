"""
Conjuntos canónicos de conceptos del kardex, compartidos por reportes,
predicción y analítica.

Regla: ningún reporte debe declarar su propia lista de conceptos inline —
cuando un concepto nuevo se agrega a CONCEPTO_MOVIMIENTO_CHOICES hay que
clasificarlo aquí una sola vez y todos los consumidores quedan al día.
(Los reportes existentes se irán migrando a estos sets en la fase 3 de la
auditoría; los nuevos deben usarlos desde el día uno.)
"""

# Ventas reales al cliente final o mayorista. Incluye los conceptos escritos
# por fallbacks históricos (VENTA_DIRECTA, VENTA) para no perder ventas en
# los reportes aunque queden filas antiguas sin remapear.
CONCEPTOS_VENTA = (
    'VENTA_PUBLICO',
    'VENTA_MAYORISTA',
    'VENTA_DIRECTA',
    'VENTA',
    'VENTA_TICKET',
)

# Abastecimiento real de una sucursal (compras y reposiciones externas).
CONCEPTOS_ABASTECIMIENTO = (
    'INGRESO_INICIAL',
    'INGRESO_MANUAL',
    'RECEPCION_COMPRA',
    'REPOSICION_STOCK',
)

# Movimiento interno entre sucursales/bodegas (no es venta ni compra).
CONCEPTOS_TRASPASO_ENTRADA = ('TRASPASO_ENTRADA', 'REGULARIZACION_TRASPASO')
CONCEPTOS_TRASPASO_SALIDA = ('TRASPASO_SALIDA',)
CONCEPTOS_TRASPASO_LEGACY = (
    # Traspasos migrados del legacy en una sola pierna, dirección según signo.
    'TRASPASO_SUCURSAL',
    'TRASPASO_BODEGA',
    'TRASPASO_VITRINA',
)

# Reingresos post-venta (devoluciones, cambios, NC, anulaciones).
CONCEPTOS_REINGRESO = (
    'DEVOLUCION_CLIENTE',
    'CAMBIO_PRODUCTO_ENTRADA',
    'DEVOLUCION_NC',
    'DEVOLUCION_NC_POST_RECEPCION',
    'ANULACION',
    'ANULACION_TICKET',
    'REPARACION_STOCK_HISTORICO',
)

# Ajustes y correcciones (ambas direcciones; clasificar por signo).
CONCEPTOS_AJUSTE = (
    'AJUSTE_POSITIVO',
    'AJUSTE_NEGATIVO',
    'AJUSTE_INVENTARIO',
    'AJUSTE_INVENTARIO_ENTRADA',
    'AJUSTE_INVENTARIO_SALIDA',
    'CORRECCION_STOCK',
)

# Marca del SALDO DE APERTURA de la migración Laravel (≈2026-01-22).
#
# Ese saldo se cargó como ``INGRESO_INICIAL`` con la fecha de la carga, no la de
# la llegada real del stock. NO es una recepción: es la foto inicial con la que
# arranca el kardex de RetailMind. Los movimientos legacy ANTERIORES (referencia
# ``MIG:<id>``) también se importaron, así que si la apertura se cuenta como
# entrada el mismo stock queda sumado dos veces y ``SUM(cantidad)`` deja de
# cuadrar contra ``Producto_Talla.stock``.
#
# Todo cálculo de SALDOS debe excluirla (queda absorbida en el saldo inicial);
# los cálculos de FLUJO BRUTO ("todo lo que entró alguna vez") pueden incluirla.
REF_SALDO_INICIAL_SINTETICO = 'MIGRACION_LARAVEL'


# Pérdidas y salidas sin contraparte comercial.
CONCEPTOS_PERDIDA = (
    'PERDIDA_ROBO',
    'PERDIDA_DETERIORO',
    'DONACION_ENTREGADA',
    'DEVOLUCION_PROVEEDOR',
    'SOBRANTE_DEVUELTO',
    'CAMBIO_PRODUCTO_SALIDA',
    'DESPACHO_COTIZACION',
)
