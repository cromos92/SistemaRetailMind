/**
 * MÓDULO GENERADOR DE ARCHIVOS TXT PARA ACEPTA
 * 
 * Este módulo facilita la generación de archivos TXT para el sistema Acepta
 * de facturación electrónica en Chile.
 * 
 * Uso:
 *   1. Importar este archivo en tu HTML
 *   2. Usar las funciones según tus necesidades
 */

const GeneradorTXTAcepta = {
    
    /**
     * Tipos de documentos disponibles
     */
    TIPOS_DOCUMENTO: {
        FACTURA_ELECTRONICA: 33,
        FACTURA_EXENTA: 34,
        BOLETA_ELECTRONICA: 39,
        BOLETA_EXENTA: 41,
        GUIA_DESPACHO: 52,
        NOTA_DEBITO: 56,
        NOTA_CREDITO: 61
    },

    /**
     * Formas de pago
     */
    FORMAS_PAGO: {
        CONTADO: 1,
        CREDITO: 2,
        SIN_COSTO: 3
    },

    /**
     * Indicadores de traslado (para guías de despacho)
     */
    INDICADORES_TRASLADO: {
        OPERACION_VENTA: 1,
        VENTAS_POR_EFECTUAR: 2,
        CONSIGNACIONES: 3,
        ENTREGA_GRATUITA: 4,
        TRASLADOS_INTERNOS: 5,
        OTROS_NO_VENTA: 6,
        GUIA_DEVOLUCION: 7,
        TRASLADO_EXPORTACION: 8,
        VENTA_EXPORTACION: 9
    },

    /**
     * Obtener CSRF token de Django
     */
    getCSRFToken() {
        const cookieValue = document.cookie
            .split('; ')
            .find(row => row.startsWith('csrftoken='))
            ?.split('=')[1];
        return cookieValue || '';
    },

    /**
     * Formatear fecha a YYYY-MM-DD
     */
    formatearFecha(fecha) {
        if (!fecha) return '';
        if (typeof fecha === 'string') return fecha;
        
        const d = new Date(fecha);
        const year = d.getFullYear();
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        
        return `${year}-${month}-${day}`;
    },

    /**
     * Formatear RUT al formato requerido (sin puntos, con guión)
     */
    formatearRUT(rut) {
        if (!rut) return '';
        
        // Eliminar puntos, guiones y espacios
        rut = rut.toString().replace(/\./g, '').replace(/-/g, '').replace(/\s/g, '').toUpperCase();
        
        // Separar cuerpo y dígito verificador
        if (rut.length >= 2) {
            const cuerpo = rut.slice(0, -1);
            const dv = rut.slice(-1);
            return `${cuerpo}-${dv}`;
        }
        
        return rut;
    },

    /**
     * Calcular IVA (19%)
     */
    calcularIVA(montoNeto) {
        return Math.round(montoNeto * 0.19);
    },

    /**
     * Crear estructura básica de datos para una factura electrónica
     */
    crearFacturaElectronica(params) {
        const {
            folio,
            fechaEmision,
            formaPago = this.FORMAS_PAGO.CONTADO,
            fechaVencimiento = '',  // ✅ Fecha de vencimiento (para crédito)
            emisor,
            receptor,
            productos,
            descuentoGlobal = 0,
            referencias = []  // ✅ Referencias a otros documentos
        } = params;

        // Calcular totales
        let subtotal = 0;
        const detalle = productos.map(p => {
            const cantidad = parseFloat(p.cantidad) || 0;
            const precioUnitario = parseFloat(p.precio_unitario) || 0;
            const descuentoUnitario = parseFloat(p.descuento_unitario) || 0;
            const montoItem = cantidad * (precioUnitario - descuentoUnitario);
            
            subtotal += montoItem;
            
            return {
                nombre: p.nombre,
                descripcion: p.descripcion || '',
                cantidad: cantidad,
                unidad: p.unidad || 'UN',
                precio_unitario: precioUnitario,
                descuento_pct: p.descuento_pct || 0,
                monto_descuento: descuentoUnitario * cantidad,
                monto_item: montoItem
            };
        });

        const montoNeto = subtotal - descuentoGlobal;
        const iva = this.calcularIVA(montoNeto);
        const total = montoNeto + iva;

        return {
            documento: {
                tipo_documento: this.TIPOS_DOCUMENTO.FACTURA_ELECTRONICA,
                folio: folio,
                fecha_emision: this.formatearFecha(fechaEmision),
                forma_pago: formaPago,
                fecha_vencimiento: fechaVencimiento ? this.formatearFecha(fechaVencimiento) : '',  // ✅ Fecha vencimiento
                timestamp: new Date().toISOString().slice(0, 19)
            },
            emisor: {
                rut: this.formatearRUT(emisor.rut),
                razon_social: emisor.razon_social,
                giro: emisor.giro,
                acteco: emisor.acteco || '',
                direccion: emisor.direccion || '',
                comuna: emisor.comuna || '',
                ciudad: emisor.ciudad || '',
                telefono: emisor.telefono || ''
            },
            receptor: {
                rut: this.formatearRUT(receptor.rut),
                razon_social: receptor.razon_social,
                giro: receptor.giro || '',
                direccion: receptor.direccion || '',
                comuna: receptor.comuna || '',
                ciudad: receptor.ciudad || ''
            },
            totales: {
                monto_neto: montoNeto,
                tasa_iva: 19.00,
                iva: iva,
                monto_total: total,
                descuento_global: descuentoGlobal  // ✅ Descuento global
            },
            detalle: detalle,
            referencias: referencias  // ✅ Referencias a otros documentos
        };
    },

    /**
     * Crear estructura para una boleta electrónica
     */
    crearBoletaElectronica(params) {
        // ✅ crearFacturaElectronica ya incluye referencias y descuentoGlobal
        const datos = this.crearFacturaElectronica(params);
        
        // Cambiar tipo de documento
        datos.documento.tipo_documento = this.TIPOS_DOCUMENTO.BOLETA_ELECTRONICA;
        
        // Para consumidor final, usar RUT genérico
        if (!params.receptor || !params.receptor.rut) {
            datos.receptor.rut = '66666666-6';
            datos.receptor.razon_social = 'CONSUMIDOR FINAL';
        }
        
        return datos;
    },

    /**
     * Crear estructura para una guía de despacho
     */
    crearGuiaDespacho(params) {
        const {
            folio,
            fechaEmision,
            indicadorTraslado,
            emisor,
            receptor,
            transporte,
            productos
        } = params;

        return {
            documento: {
                tipo_documento: this.TIPOS_DOCUMENTO.GUIA_DESPACHO,
                folio: folio,
                fecha_emision: this.formatearFecha(fechaEmision),
                tipo_despacho: 2, // Por cuenta del emisor al receptor
                ind_traslado: indicadorTraslado || this.INDICADORES_TRASLADO.OPERACION_VENTA
            },
            emisor: {
                rut: this.formatearRUT(emisor.rut),
                razon_social: emisor.razon_social,
                giro: emisor.giro,
                direccion: emisor.direccion || '',
                comuna: emisor.comuna || '',
                ciudad: emisor.ciudad || ''
            },
            receptor: {
                rut: this.formatearRUT(receptor.rut),
                razon_social: receptor.razon_social,
                direccion: receptor.direccion || ''
            },
            transporte: {
                patente: transporte?.patente || '',
                rut_transportista: this.formatearRUT(transporte?.rut_transportista || ''),
                direccion_destino: transporte?.direccion_destino || receptor.direccion || '',
                comuna_destino: transporte?.comuna_destino || receptor.comuna || '',
                ciudad_destino: transporte?.ciudad_destino || receptor.ciudad || ''
            },
            totales: {
                monto_neto: 0,
                tasa_iva: 0,
                iva: 0,
                monto_total: 0
            },
            detalle: productos.map(p => ({
                nombre: p.nombre,
                descripcion: p.descripcion || '',
                cantidad: parseFloat(p.cantidad) || 0,
                unidad: p.unidad || 'UN',
                precio_unitario: 0,
                monto_item: 0
            }))
        };
    },

    /**
     * Crear estructura para una nota de crédito
     */
    crearNotaCredito(params) {
        // ✅ crearFacturaElectronica ya incluye referencias y descuentoGlobal
        const datos = this.crearFacturaElectronica(params);
        
        // Cambiar tipo de documento
        datos.documento.tipo_documento = this.TIPOS_DOCUMENTO.NOTA_CREDITO;
        
        // ✅ En Chile las NC usan montos POSITIVOS (el tipo 61 indica que es NC)
        // NO convertir a negativos - mantener valores positivos
        datos.totales.monto_neto = Math.abs(datos.totales.monto_neto);
        datos.totales.iva = Math.abs(datos.totales.iva);
        datos.totales.monto_total = Math.abs(datos.totales.monto_total);
        
        // Mantener productos con valores positivos
        datos.detalle = datos.detalle.map(item => ({
            ...item,
            cantidad: Math.abs(item.cantidad),
            precio_unitario: Math.abs(item.precio_unitario),
            monto_item: Math.abs(item.monto_item)
        }));
        
        // ✅ Referencias y descuento_global ya están incluidos
        return datos;
    },

    /**
     * Enviar datos al servidor para generar TXT
     */
    async generarTXT(datos) {
        try {
            const response = await fetch('/app/documentos/generar-txt-acepta/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify(datos)
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Error al generar archivo TXT');
            }

            // Descargar el archivo
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = response.headers.get('Content-Disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'documento.txt';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            return { success: true };

        } catch (error) {
            console.error('Error al generar TXT:', error);
            return { success: false, error: error.message };
        }
    },

    /**
     * Generar TXT desde un DTE existente en la base de datos
     */
    async generarTXTDesdeDTE(dteId) {
        try {
            const response = await fetch('/app/documentos/generar-txt-desde-dte/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCSRFToken()
                },
                body: JSON.stringify({ dte_id: dteId })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Error al generar archivo TXT');
            }

            // Descargar el archivo
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = response.headers.get('Content-Disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'documento.txt';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            return { success: true };

        } catch (error) {
            console.error('Error al generar TXT desde DTE:', error);
            return { success: false, error: error.message };
        }
    },

    /**
     * Validar datos antes de enviar
     */
    validarDatos(datos) {
        const errores = [];

        // Validar documento
        if (!datos.documento) {
            errores.push('Falta la sección documento');
        } else {
            if (!datos.documento.tipo_documento) errores.push('Falta tipo de documento');
            if (!datos.documento.folio) errores.push('Falta folio');
            if (!datos.documento.fecha_emision) errores.push('Falta fecha de emisión');
        }

        // Validar emisor
        if (!datos.emisor) {
            errores.push('Falta la sección emisor');
        } else {
            if (!datos.emisor.rut) errores.push('Falta RUT del emisor');
            if (!datos.emisor.razon_social) errores.push('Falta razón social del emisor');
            if (!datos.emisor.giro) errores.push('Falta giro del emisor');
        }

        // Validar receptor
        if (!datos.receptor) {
            errores.push('Falta la sección receptor');
        } else {
            if (!datos.receptor.rut) errores.push('Falta RUT del receptor');
            if (!datos.receptor.razon_social) errores.push('Falta razón social del receptor');
        }

        // Validar detalle
        if (!datos.detalle || datos.detalle.length === 0) {
            errores.push('Debe incluir al menos un producto');
        } else {
            datos.detalle.forEach((item, index) => {
                if (!item.nombre) errores.push(`Falta nombre del producto en línea ${index + 1}`);
                if (item.cantidad === undefined || item.cantidad === null) {
                    errores.push(`Falta cantidad en línea ${index + 1}`);
                }
                if (item.precio_unitario === undefined || item.precio_unitario === null) {
                    errores.push(`Falta precio unitario en línea ${index + 1}`);
                }
            });
        }

        return {
            valido: errores.length === 0,
            errores: errores
        };
    }
};

// Exportar para uso en módulos ES6 si está disponible
if (typeof module !== 'undefined' && module.exports) {
    module.exports = GeneradorTXTAcepta;
}

