# Re-export all models and constants so that
#   from app.models import Empresa
# keeps working everywhere without changing imports.

from .base import (  # noqa: F401
    RUT_REGEX_VALIDATOR,
    validar_rut_chileno,
)

from .organizacion import (  # noqa: F401
    Empresa,
    Sucursal,
    EmpresaUser,
    Vendedor,
    Correlativo,
)

from .catalogo import (  # noqa: F401
    TIPO_TALLA_CHOICES,
    Productos_Atributos,
    AtributoOpcion,
    ProductoAtributoValor,
    Categoria,
    GuiaTalla,
    GuiaTallaItem,
    GuiaTallaProducto,
    Producto,
    Producto_Talla,
)

from .dte import (  # noqa: F401
    TIPO_DOCUMENTO_CHOICES,
    ESTADO_PAGO_CHOICES,
    ESTADO_DTE_CHOICES,
    ESTADO_RECEPCION_PRODUCTO_CHOICES,
    TIPO_PROBLEMA_CHOICES,
    TIPO_SOLUCION_CHOICES,
    ESTADO_SOLICITUD_CHOICES,
    Dte,
    Dte_Detalle_Pago,
    Dte_Incidencia,
    Dte_Productos,
    NotificacionDTE,
    DteAlertaDescartada,
    DescuentoRecargo,
)

from .ventas import (  # noqa: F401
    METODO_PAGO_TICKET_CHOICES,
    ORIGEN_PAGO_CHOICES,
    TIPO_MOVIMIENTO_CHOICES,
    CONCEPTO_MOVIMIENTO_CHOICES,
    ESTADO_MOVIMIENTO_CHOICES,
    ESTADO_TICKET_CHOICES,
    Ticket,
    Ticket_Productos,
    TicketReferencia,
    TicketDetallePago,
    TIPO_OPERACION_CAMBIO_CHOICES,
    ESTADO_CAMBIO_CHOICES,
    MOTIVO_CAMBIO_CHOICES,
    CONDICION_PRODUCTO_CHOICES,
    CambioDevolucion,
    CambioDevolucionDetalle,
    PagoCambioDevolucion,
    HistorialCambioDevolucion,
    METODO_DEVOLUCION_NC_CHOICES,
)

from .inventario import (  # noqa: F401
    Traspaso,
    Traspaso_Detalle,
    AjusteInventario,
    AjusteInventario_Detalle,
    Movimientos_Producto,
    LoteProducto,
    TomaInventario,
    TomaInventarioDetalle,
    TomaInventarioLog,
    TareaAplicacionAjustes,
)

from .compras import (  # noqa: F401
    Compras,
    Compras_Producto,
    Compras_Producto_Talla,
    Productos_Recepcionados,
    Solicitud_Regularizacion,
    CurvaDistribucion,
    CurvaDistribucionItem,
)

from .cotizaciones import (  # noqa: F401
    Cotizacion,
    Cotizacion_Detalle,
    Cotizacion_Empresa,
    Cotizacion_Empresa_Detalle,
    Cotizacion_Empresa_Detalle_SKU,
    Historial_Cotizacion,
)

from .caja import (  # noqa: F401
    ESTADO_ARQUEO_CHOICES,
    RESULTADO_REVISION_CHOICES,
    BANCO_CHOICES,
    ESTADO_CREDITO_CHOICES,
    TIPO_CREDITO_CHOICES,
    TIPO_BENEFICIARIO_CHOICES,
    ArqueoCaja,
    GrupoDeposito,
    DepositoBancario,
    ObservacionArqueo,
    CreditoTrabajador,
    PagoCreditoTrabajador,
    FirmaCreditoTrabajador,
    LogAccionCaja,
    log_accion_caja,
)

from .pos import (  # noqa: F401
    TIPO_POS_CHOICES,
    ESTADO_TRANSACCION_POS_CHOICES,
    TIPO_TARJETA_CHOICES,
    ConfiguracionPOS,
    TransaccionPOS,
    LogPOS,
)

from .precios import (  # noqa: F401
    ParametroGlobal,
    ESTADO_CAMBIO_PRECIO_CHOICES,
    TIPO_CAMBIO_PRECIO_CHOICES,
    CambioPrecioPendiente,
    NotificacionCambioPrecio,
    HistorialCambioPrecio,
)

from .permisos import (  # noqa: F401
    ModuloSistema,
    OpcionMenu,
    PermisoRol,
    ConfiguracionPermisoGlobal,
    PermisoSucursal,
    PermisoUsuario,
    CodigoAutorizacionDinamico,
    RegistroAutorizacion,
)

from .requerimientos import (  # noqa: F401
    TIPO_REQUERIMIENTO_CHOICES,
    SUBTIPO_DEFECTO_CHOICES,
    SUBTIPO_ERROR_CHOICES,
    ESTADO_REQUERIMIENTO_CHOICES,
    MAX_FOTOS_POR_TIPO,
    TipoFotoRequerimiento,
    Requerimiento,
    FotoRequerimiento,
    HistorialRequerimiento,
)

from .etiquetas import (  # noqa: F401
    HistorialImpresionEtiqueta,
    DetalleImpresionEtiqueta,
)

from .crm import (  # noqa: F401
    TIPO_CLIENTE_CHOICES,
    ContactoEmpresa,
    Cliente,
    Proveedor,
    LogEmpresa,
    LogCliente,
)

from .predicciones import (  # noqa: F401
    ConfiguracionPrediccion,
    ClasificacionABC,
    VelocidadHistorica,
    CurvaTalles,
    PrediccionDemanda,
    SugerenciaCompra,
    AlertaVelocidad,
    AlertaQuiebreTalle,
    PendienteReevaluacion,
    StockInicialTemporada,
    URGENCIA_CHOICES,
)

from .ecommerce import (  # noqa: F401
    CANAL_ECOMMERCE_CHOICES,
    ESTADO_PEDIDO_ECOMMERCE_CHOICES,
    SUB_ESTADO_PEDIDO_CHOICES,
    PRIORIDAD_PEDIDO_CHOICES,
    TIPO_EVENTO_HISTORIAL_CHOICES,
    MOTIVO_REASIGNACION_CHOICES,
    TRANSICIONES_SUB_ESTADO,
    PedidoEcommerce,
    HistorialPedidoEcommerce,
    MetricaAsignacionPedido,
)
