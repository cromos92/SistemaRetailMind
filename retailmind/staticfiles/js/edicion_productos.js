/**
 * edicion_productos.js
 * Módulo JavaScript para la edición de productos y gestión de stock
 * Incluye funciones para editar productos, variaciones y ajustar stock
 */

// ========== VARIABLES GLOBALES ==========
let productoActualEdicion = null;
let variacionActualEdicion = null;

// ========== FUNCIONES DE CARGA Y MODAL ==========

/**
 * Abrir modal de edición de producto
 */
window.abrirModalEdicionProducto = function(productoId) {
    console.log('═══════════════════════════════════════');
    console.log('🟢🟢🟢 FUNCIÓN EJECUTÁNDOSE AHORA 🟢🟢🟢');
    console.log('🟢 abrirModalEdicionProducto INICIO con ID:', productoId);
    console.log('═══════════════════════════════════════');
    
    try {
        // Mostrar loading
        mostrarLoading('Cargando producto...');
        console.log('🟢 Loading mostrado');
        
        // Obtener datos del producto
        console.log('🟢 Iniciando fetch a:', `/app/productos/obtener-para-editar/${productoId}/`);
        fetch(`/app/productos/obtener-para-editar/${productoId}/`)
        .then(response => response.json())
        .then(data => {
            // DEBUG: Ver datos recibidos
            console.log('Datos recibidos del backend:', data);
            
            if (data.success) {
                console.log('Producto:', data.producto);
                console.log('Variaciones:', data.variaciones);
                
                productoActualEdicion = data.producto;
                cargarDatosProductoEnModal(data.producto, data.variaciones);
                
                // Abrir modal con Bootstrap 5
                const modalElement = document.getElementById('modalEdicionProducto');
                console.log('🔵 Modal element encontrado:', modalElement);
                
                if (!modalElement) {
                    console.error('❌ ERROR: Modal #modalEdicionProducto NO encontrado en el DOM');
                    mostrarError('El modal de edición no está disponible. Verifique que modales_edicion_producto.html esté incluido.');
                    return;
                }
                
                const modal = new bootstrap.Modal(modalElement);
                console.log('🔵 Instancia de modal creada:', modal);
                modal.show();
                console.log('🔵 Modal.show() ejecutado');
            } else {
                mostrarError(data.error || 'Error al cargar el producto');
            }
        })
        .catch(error => {
            console.error('🔴 Error en fetch:', error);
            mostrarError('Error al cargar el producto');
        })
        .finally(() => {
            console.log('🟢 Finally ejecutado, ocultando loading');
            ocultarLoading();
        });
    } catch (error) {
        console.error('🔴 Error en abrirModalEdicionProducto:', error);
        mostrarError('Error al abrir modal de edición');
    }
}

/**
 * Cargar datos del producto en el modal de edición
 */
window.cargarDatosProductoEnModal = function(producto, variaciones) {
    console.log('cargarDatosProductoEnModal llamada con:', producto, variaciones);
    
    // Datos del producto base
    $('#edit_producto_id').val(producto.id);
    $('#edit_articulo').val(producto.articulo);
    $('#edit_descripcion').val(producto.descripcion);
    $('#edit_costo').val(producto.costo);
    $('#edit_sobreprecio').val(producto.sobreprecio);
    $('#edit_precioventa').val(producto.precioventa);
    $('#edit_precioSugerido').val(producto.precioSugerido);
    
    // Seleccionar categoría
    if (producto.categoria_id) {
        $('#edit_categoria_id').val(producto.categoria_id).trigger('change');
    }
    
    // Limpiar y cargar atributos dinámicamente
    // MARCA (atributo1)
    const select1 = $('#edit_atributo1_id');
    select1.empty().append('<option value="">Seleccionar marca...</option>');
    if (producto.atributo1_id && producto.atributo1_nombre) {
        select1.append(`<option value="${producto.atributo1_id}" selected>${producto.atributo1_nombre}</option>`);
        console.log('Marca cargada:', producto.atributo1_nombre);
    } else {
        console.log('Producto sin marca asignada');
    }
    
    // COLOR (atributo2)
    const select2 = $('#edit_atributo2_id');
    select2.empty().append('<option value="">Seleccionar color...</option>');
    if (producto.atributo2_id && producto.atributo2_nombre) {
        select2.append(`<option value="${producto.atributo2_id}" selected>${producto.atributo2_nombre}</option>`);
        console.log('Color cargado:', producto.atributo2_nombre);
    } else {
        console.log('Producto sin color asignado');
    }
    
    // GÉNERO (atributo3)
    const select3 = $('#edit_atributo3_id');
    select3.empty().append('<option value="">Seleccionar género...</option>');
    if (producto.atributo3_id && producto.atributo3_nombre) {
        select3.append(`<option value="${producto.atributo3_id}" selected>${producto.atributo3_nombre}</option>`);
        console.log('Género cargado:', producto.atributo3_nombre);
    } else {
        console.log('Producto sin género asignado');
    }
    
    // OTRO ATRIBUTO (atributo4)
    const select4 = $('#edit_atributo4_id');
    select4.empty().append('<option value="">Seleccionar...</option>');
    if (producto.atributo4_id && producto.atributo4_nombre) {
        select4.append(`<option value="${producto.atributo4_id}" selected>${producto.atributo4_nombre}</option>`);
        console.log('Otro atributo cargado:', producto.atributo4_nombre);
    } else {
        console.log('Producto sin otro atributo asignado');
    }
    
    // Cargar variaciones en la tabla
    console.log('Llamando a cargarVariacionesEnTabla con', variaciones);
    cargarVariacionesEnTabla(variaciones);
};

/**
 * Cargar variaciones en la tabla
 */
window.cargarVariacionesEnTabla = function(variaciones) {
    console.log('cargarVariacionesEnTabla - Variaciones recibidas:', variaciones);
    console.log('cargarVariacionesEnTabla - Cantidad:', variaciones ? variaciones.length : 0);
    
    const tbody = $('#tablaVariacionesEdicion tbody');
    console.log('Tbody encontrado:', tbody.length > 0 ? 'Sí' : 'No');
    
    tbody.empty();
    
    if (!variaciones || variaciones.length === 0) {
        console.log('No hay variaciones para mostrar');
        tbody.append(`
            <tr>
                <td colspan="5" class="text-center text-muted">
                    No hay variaciones/tallas para este producto
                </td>
            </tr>
        `);
        return;
    }
    
    console.log(`Procesando ${variaciones.length} variaciones...`);
    variaciones.forEach((variacion, index) => {
        console.log(`Variación ${index + 1}:`, variacion);
        const stockClass = variacion.stock_total === 0 ? 'text-danger' : 
                          variacion.stock_total < 5 ? 'text-warning' : 
                          'text-success';
        
        const row = `
            <tr data-variacion-id="${variacion.id}">
                <td>${variacion.talla}</td>
                <td>
                    <span class="sku-display">${variacion.sku}</span>
                </td>
                <td class="${stockClass}">
                    <strong>${variacion.stock_total}</strong> unid.
                    ${variacion.lotes && variacion.lotes.length > 0 ? 
                        `<br><small class="text-muted">${variacion.lotes.length} lote(s)</small>` : ''}
                </td>
                <td>
                    <button type="button" class="btn btn-sm btn-primary" 
                            onclick="abrirModalAjustarStock(${variacion.id}, '${variacion.talla}', ${variacion.stock_total})"
                            title="Ajustar stock">
                        <i class="fas fa-boxes"></i> Ajustar
                    </button>
                </td>
                <td>
                    <button type="button" class="btn btn-sm btn-info" 
                            onclick="verHistorialMovimientos(${variacion.id}, '${variacion.talla}')"
                            title="Ver historial">
                        <i class="fas fa-history"></i>
                    </button>
                    <button type="button" class="btn btn-sm btn-secondary" 
                            onclick="verLotesVariacion(${variacion.id}, '${variacion.talla}')"
                            title="Ver lotes">
                        <i class="fas fa-layer-group"></i>
                    </button>
                </td>
            </tr>
        `;
        tbody.append(row);
    });
    
    console.log(`✅ ${variaciones.length} variaciones cargadas en la tabla correctamente`);
}

// ========== FUNCIONES DE GUARDADO ==========

/**
 * Guardar cambios del producto base
 */
function guardarProductoBase() {
    const productoId = $('#edit_producto_id').val();
    
    // Validar campos requeridos
    const articulo = $('#edit_articulo').val().trim();
    if (!articulo) {
        mostrarError('El nombre del producto es requerido');
        return;
    }
    
    const costo = parseInt($('#edit_costo').val()) || 0;
    const sobreprecio = parseInt($('#edit_sobreprecio').val()) || 0;
    const precioventa = parseInt($('#edit_precioventa').val()) || 0;
    
    if (precioventa <= 0) {
        mostrarError('El precio de venta debe ser mayor a 0');
        return;
    }
    
    // Construir datos
    const datos = {
        articulo: articulo,
        descripcion: $('#edit_descripcion').val().trim(),
        categoria_id: $('#edit_categoria_id').val() || null,
        atributo1_id: $('#edit_atributo1_id').val() || null,
        atributo2_id: $('#edit_atributo2_id').val() || null,
        atributo3_id: $('#edit_atributo3_id').val() || null,
        atributo4_id: $('#edit_atributo4_id').val() || null,
        costo: costo,
        sobreprecio: sobreprecio,
        precioventa: precioventa,
        precioSugerido: parseInt($('#edit_precioSugerido').val()) || 0
    };
    
    // Mostrar loading
    mostrarLoading('Guardando cambios...');
    
    // Enviar al servidor
    fetch(`/app/productos/actualizar/${productoId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify(datos)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            mostrarExito(data.message || 'Producto actualizado exitosamente');
            // Cerrar modal con Bootstrap 5
            const modalElement = document.getElementById('modalEdicionProducto');
            const modal = bootstrap.Modal.getInstance(modalElement);
            if (modal) modal.hide();
            // Recargar lista de productos si existe
            if (typeof cargarProductos === 'function') {
                cargarProductos();
            }
        } else {
            mostrarError(data.error || 'Error al actualizar el producto');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        mostrarError('Error al actualizar el producto');
    })
    .finally(() => {
        ocultarLoading();
    });
}

// ========== FUNCIONES DE AJUSTE DE STOCK ==========

/**
 * Abrir modal para ajustar stock de una variación
 */
function abrirModalAjustarStock(variacionId, talla, stockActual) {
    variacionActualEdicion = {
        id: variacionId,
        talla: talla,
        stock_actual: stockActual
    };
    
    // Resetear formulario
    $('#ajuste_variacion_id').val(variacionId);
    $('#ajuste_talla_nombre').text(talla);
    $('#ajuste_stock_actual').text(stockActual);
    $('#ajuste_cantidad').val('');
    $('#ajuste_motivo').val('');
    $('#ajuste_costo_unitario').val('');
    $('#ajuste_sobreprecio_unitario').val('');
    $('#ajuste_precio_venta_unitario').val('');
    $('#ajuste_numero_lote').val('');
    $('#ajuste_stock_resultante').text(stockActual);
    
    // Tipo por defecto: ENTRADA
    $('input[name="tipo_ajuste"][value="ENTRADA"]').prop('checked', true);
    mostrarCamposEntrada();
    
    // Abrir modal con Bootstrap 5
    const modalElement = document.getElementById('modalAjustarStock');
    const modal = new bootstrap.Modal(modalElement);
    modal.show();
}

/**
 * Cambiar tipo de ajuste (ENTRADA/SALIDA)
 */
function cambiarTipoAjuste() {
    const tipo = $('input[name="tipo_ajuste"]:checked').val();
    
    if (tipo === 'ENTRADA') {
        mostrarCamposEntrada();
    } else {
        ocultarCamposEntrada();
    }
    
    calcularStockResultante();
}

/**
 * Mostrar campos específicos para ajuste de ENTRADA
 */
function mostrarCamposEntrada() {
    $('#campos_entrada').slideDown();
}

/**
 * Ocultar campos específicos para ajuste de ENTRADA
 */
function ocultarCamposEntrada() {
    $('#campos_entrada').slideUp();
}

/**
 * Calcular stock resultante en tiempo real
 */
function calcularStockResultante() {
    const stockActual = parseInt($('#ajuste_stock_actual').text()) || 0;
    const cantidad = parseInt($('#ajuste_cantidad').val()) || 0;
    const tipo = $('input[name="tipo_ajuste"]:checked').val();
    
    let stockResultante = stockActual;
    
    if (cantidad > 0) {
        if (tipo === 'ENTRADA') {
            stockResultante = stockActual + cantidad;
        } else if (tipo === 'SALIDA') {
            stockResultante = stockActual - cantidad;
        }
    }
    
    // Mostrar stock resultante con color
    const $stockResultante = $('#ajuste_stock_resultante');
    $stockResultante.text(stockResultante);
    
    if (stockResultante < 0) {
        $stockResultante.removeClass('text-success text-warning').addClass('text-danger');
    } else if (stockResultante < 5) {
        $stockResultante.removeClass('text-success text-danger').addClass('text-warning');
    } else {
        $stockResultante.removeClass('text-warning text-danger').addClass('text-success');
    }
}

/**
 * Guardar ajuste de stock
 */
function guardarAjusteStock() {
    const variacionId = $('#ajuste_variacion_id').val();
    const tipo = $('input[name="tipo_ajuste"]:checked').val();
    const cantidad = parseInt($('#ajuste_cantidad').val());
    const motivo = $('#ajuste_motivo').val().trim();
    
    // Validaciones
    if (!cantidad || cantidad <= 0) {
        mostrarError('La cantidad debe ser mayor a 0');
        return;
    }
    
    if (!motivo || motivo.length < 10) {
        mostrarError('El motivo es obligatorio y debe tener al menos 10 caracteres');
        return;
    }
    
    // Validar stock suficiente para salida
    const stockActual = parseInt($('#ajuste_stock_actual').text()) || 0;
    if (tipo === 'SALIDA' && cantidad > stockActual) {
        mostrarError(`No hay suficiente stock. Disponible: ${stockActual}, solicitado: ${cantidad}`);
        return;
    }
    
    // Construir datos
    const datos = {
        tipo_ajuste: tipo,
        cantidad: cantidad,
        motivo: motivo
    };
    
    // Agregar datos de entrada si corresponde
    if (tipo === 'ENTRADA') {
        const costoUnitario = parseFloat($('#ajuste_costo_unitario').val());
        const sobrepreioUnitario = parseFloat($('#ajuste_sobreprecio_unitario').val()) || 0;
        const precioVentaUnitario = parseFloat($('#ajuste_precio_venta_unitario').val());
        
        if (!costoUnitario || costoUnitario <= 0) {
            mostrarError('El costo unitario es requerido para ajustes de entrada');
            return;
        }
        
        if (!precioVentaUnitario || precioVentaUnitario <= 0) {
            mostrarError('El precio de venta unitario es requerido para ajustes de entrada');
            return;
        }
        
        datos.costo_unitario = costoUnitario;
        datos.sobreprecio_unitario = sobrepreioUnitario;
        datos.precio_venta_unitario = precioVentaUnitario;
        datos.numero_lote = $('#ajuste_numero_lote').val().trim();
    }
    
    // Confirmación
    const mensaje = tipo === 'ENTRADA' 
        ? `¿Confirma el ingreso de ${cantidad} unidades?`
        : `¿Confirma la salida de ${cantidad} unidades?`;
    
    if (!confirm(mensaje)) {
        return;
    }
    
    // Mostrar loading
    mostrarLoading('Procesando ajuste...');
    
    // Enviar al servidor
    fetch(`/app/productos/variacion/ajustar-stock/${variacionId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify(datos)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            mostrarExito(data.message || 'Stock ajustado exitosamente');
            // Cerrar modal con Bootstrap 5
            const modalElement = document.getElementById('modalAjustarStock');
            const modal = bootstrap.Modal.getInstance(modalElement);
            if (modal) modal.hide();
            
            // Recargar producto en modal de edición si está abierto
            if (productoActualEdicion) {
                abrirModalEdicionProducto(productoActualEdicion.id);
            }
            
            // Recargar lista de productos si existe
            if (typeof cargarProductos === 'function') {
                cargarProductos();
            }
        } else {
            mostrarError(data.error || 'Error al ajustar el stock');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        mostrarError('Error al ajustar el stock');
    })
    .finally(() => {
        ocultarLoading();
    });
}

// ========== FUNCIONES DE HISTORIAL ==========

/**
 * Ver historial de movimientos de una variación
 */
function verHistorialMovimientos(variacionId, talla) {
    // Configurar modal
    $('#historial_talla_nombre').text(talla);
    
    // Mostrar loading en la tabla
    const tbody = $('#tablaHistorialMovimientos tbody');
    tbody.html(`
        <tr>
            <td colspan="6" class="text-center">
                <i class="fas fa-spinner fa-spin"></i> Cargando historial...
            </td>
        </tr>
    `);
    
    // Abrir modal con Bootstrap 5
    const modalElement = document.getElementById('modalHistorialMovimientos');
    const modal = new bootstrap.Modal(modalElement);
    modal.show();
    
    // Cargar historial
    fetch(`/app/productos/variacion/historial/${variacionId}/?limit=50`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                cargarHistorialEnTabla(data.movimientos);
            } else {
                tbody.html(`
                    <tr>
                        <td colspan="6" class="text-center text-danger">
                            ${data.error || 'Error al cargar el historial'}
                        </td>
                    </tr>
                `);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            tbody.html(`
                <tr>
                    <td colspan="6" class="text-center text-danger">
                        Error al cargar el historial
                    </td>
                </tr>
            `);
        });
}

/**
 * Cargar historial de movimientos en tabla
 */
function cargarHistorialEnTabla(movimientos) {
    const tbody = $('#tablaHistorialMovimientos tbody');
    tbody.empty();
    
    if (!movimientos || movimientos.length === 0) {
        tbody.append(`
            <tr>
                <td colspan="6" class="text-center text-muted">
                    No hay movimientos registrados
                </td>
            </tr>
        `);
        return;
    }
    
    movimientos.forEach(mov => {
        const cantidadClass = mov.cantidad > 0 ? 'text-success' : 'text-danger';
        const cantidadIcon = mov.cantidad > 0 ? '↑' : '↓';
        
        const row = `
            <tr>
                <td>${mov.fecha_hora}</td>
                <td>
                    <span class="badge badge-info">${mov.concepto_display || mov.concepto}</span>
                </td>
                <td class="${cantidadClass}">
                    <strong>${cantidadIcon} ${Math.abs(mov.cantidad)}</strong>
                </td>
                <td>${mov.responsable}</td>
                <td>
                    <small>${mov.observaciones || '-'}</small>
                </td>
            </tr>
        `;
        tbody.append(row);
    });
}

/**
 * Ver lotes de una variación
 */
function verLotesVariacion(variacionId, talla) {
    // Redirigir a la página de lotes del producto
    window.location.href = `/app/lotes-producto/${variacionId}/`;
}

// ========== FUNCIONES AUXILIARES ==========

/**
 * Obtener cookie (para CSRF token)
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

/**
 * Mostrar mensaje de éxito
 */
function mostrarExito(mensaje) {
    // Si existe Swal (SweetAlert2)
    if (typeof Swal !== 'undefined') {
        Swal.fire({
            icon: 'success',
            title: '¡Éxito!',
            text: mensaje,
            timer: 2000,
            showConfirmButton: false
        });
    } else {
        alert(mensaje);
    }
}

/**
 * Mostrar mensaje de error
 */
function mostrarError(mensaje) {
    // Si existe Swal (SweetAlert2)
    if (typeof Swal !== 'undefined') {
        Swal.fire({
            icon: 'error',
            title: 'Error',
            text: mensaje
        });
    } else {
        alert('Error: ' + mensaje);
    }
}

/**
 * Mostrar loading
 */
function mostrarLoading(mensaje = 'Cargando...') {
    if (typeof Swal !== 'undefined') {
        Swal.fire({
            title: mensaje,
            allowOutsideClick: false,
            allowEscapeKey: false,
            didOpen: () => {
                Swal.showLoading();
            }
        });
    }
}

/**
 * Ocultar loading
 */
function ocultarLoading() {
    if (typeof Swal !== 'undefined') {
        Swal.close();
    }
}

// ========== INICIALIZACIÓN ==========

$(document).ready(function() {
    // Event listeners para el modal de ajuste de stock
    $('input[name="tipo_ajuste"]').on('change', cambiarTipoAjuste);
    $('#ajuste_cantidad').on('input', calcularStockResultante);
    
    // Event listener para calcular precio sugerido automáticamente
    $('#edit_costo, #edit_sobreprecio').on('input', function() {
        const costo = parseInt($('#edit_costo').val()) || 0;
        const sobreprecio = parseInt($('#edit_sobreprecio').val()) || 0;
        const precioCalculado = costo + sobreprecio;
        $('#edit_precioventa').val(precioCalculado);
    });
});

