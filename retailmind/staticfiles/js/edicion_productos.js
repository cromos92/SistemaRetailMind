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
    
    // Guardar producto para reinicialización
    window.productoActualEdicion = producto;
    
    // Datos del producto base
    $('#edit_producto_id').val(producto.id);
    $('#edit_articulo').val(producto.articulo);
    $('#edit_descripcion').val(producto.descripcion);
    $('#edit_costo').val(producto.costo);
    $('#edit_sobreprecio').val(producto.sobreprecio);
    $('#edit_precioventa').val(producto.precioventa);
    $('#edit_precioSugerido').val(producto.precioSugerido);
    
    // Mostrar sucursal en el modal
    const $sucBadge = $('#editSucursalBadge');
    if ($sucBadge.length) {
        $sucBadge.text(producto.sucursal_nombre || 'Sin bodega');
    }
    
    // Cargar todas las opciones con Select2
    cargarOpcionesConSelect2(producto);
    
    // Cargar variaciones en la tabla
    console.log('Llamando a cargarVariacionesEnTabla con', variaciones);
    cargarVariacionesEnTabla(variaciones);
};

/**
 * Función auxiliar para inicializar Select2 en modal de edición
 */
function inicializarSelect2EnModalEdicion($select, placeholder) {
    if ($select.hasClass('select2-hidden-accessible')) {
        try {
            $select.select2('destroy');
        } catch(e) {
            console.log('Select2 no pudo ser destruido:', e);
        }
    }
    $select.select2({
        dropdownParent: $('#modalEdicionProducto'),
        width: '100%',
        placeholder: placeholder || 'Seleccionar...',
        allowClear: true,
        language: {
            noResults: function () {
                return "Sin resultados";
            }
        }
    });
}

/**
 * Cargar todas las opciones con Select2 y búsqueda
 */
function cargarOpcionesConSelect2(producto) {
    const $modal = $('#modalEdicionProducto');
    console.log('🔄 Cargando opciones con Select2 para producto:', producto.articulo);
    
    // Obtener IDs de atributos desde variables globales o desde el DOM
    const ID_ATRIBUTO_MARCA = window.ID_ATRIBUTO_MARCA || parseInt($('#id_atributo_marca').val()) || 1;
    const ID_ATRIBUTO_COLOR = window.ID_ATRIBUTO_COLOR || parseInt($('#id_atributo_color').val()) || 2;
    const ID_ATRIBUTO_GENERO = window.ID_ATRIBUTO_GENERO || parseInt($('#id_atributo_genero').val()) || 3;
    const ID_ATRIBUTO_OTRO = window.ID_ATRIBUTO_OTRO || parseInt($('#id_atributo_otro').val()) || 0;
    
    console.log('📋 IDs de atributos:', { marca: ID_ATRIBUTO_MARCA, color: ID_ATRIBUTO_COLOR, genero: ID_ATRIBUTO_GENERO, otro: ID_ATRIBUTO_OTRO });
    
    // Cargar CATEGORÍAS
    $.ajax({
        url: '/app/api/categorias/listar/',
        method: 'GET',
        success: function(response) {
            const $select = $('#edit_categoria_id');
            $select.empty().append('<option value="">Seleccionar categoría...</option>');
            
            const categorias = response.categorias || response;
            console.log('📦 Categorías cargadas:', Array.isArray(categorias) ? categorias.length : 0);
            if (Array.isArray(categorias)) {
                categorias.forEach(cat => {
                    const selected = producto.categoria_id == cat.id ? 'selected' : '';
                    $select.append(`<option value="${cat.id}" ${selected}>${cat.nombre}</option>`);
                });
            }
            
            inicializarSelect2EnModalEdicion($select, 'Buscar categoría...');
        },
        error: function(xhr, status, error) {
            console.error('❌ Error cargando categorías:', error);
        }
    });
    
    // Cargar MARCAS (atributo1)
    $.ajax({
        url: `/app/opciones_atributo/?atributo_id=${ID_ATRIBUTO_MARCA}`,
        method: 'GET',
        success: function(response) {
            const $select = $('#edit_atributo1_id');
            $select.empty().append('<option value="">Seleccionar marca...</option>');
            
            const opciones = Array.isArray(response) ? response : (response.opciones || []);
            console.log('🏷️ Marcas cargadas:', opciones.length);
            opciones.forEach(opt => {
                const selected = producto.atributo1_id == opt.id ? 'selected' : '';
                $select.append(`<option value="${opt.id}" ${selected}>${opt.valor}</option>`);
            });
            
            inicializarSelect2EnModalEdicion($select, 'Buscar marca...');
        },
        error: function(xhr, status, error) {
            console.error('❌ Error cargando marcas:', error);
        }
    });
    
    // Cargar COLORES (atributo2)
    $.ajax({
        url: `/app/opciones_atributo/?atributo_id=${ID_ATRIBUTO_COLOR}`,
        method: 'GET',
        success: function(response) {
            const $select = $('#edit_atributo2_id');
            $select.empty().append('<option value="">Seleccionar color...</option>');
            
            const opciones = Array.isArray(response) ? response : (response.opciones || []);
            console.log('🎨 Colores cargados:', opciones.length);
            opciones.forEach(opt => {
                const selected = producto.atributo2_id == opt.id ? 'selected' : '';
                $select.append(`<option value="${opt.id}" ${selected}>${opt.valor}</option>`);
            });
            
            inicializarSelect2EnModalEdicion($select, 'Buscar color...');
        },
        error: function(xhr, status, error) {
            console.error('❌ Error cargando colores:', error);
        }
    });
    
    // Cargar GÉNEROS (atributo3)
    $.ajax({
        url: `/app/opciones_atributo/?atributo_id=${ID_ATRIBUTO_GENERO}`,
        method: 'GET',
        success: function(response) {
            const $select = $('#edit_atributo3_id');
            $select.empty().append('<option value="">Seleccionar género...</option>');
            
            const opciones = Array.isArray(response) ? response : (response.opciones || []);
            console.log('👤 Géneros cargados:', opciones.length);
            opciones.forEach(opt => {
                const selected = producto.atributo3_id == opt.id ? 'selected' : '';
                $select.append(`<option value="${opt.id}" ${selected}>${opt.valor}</option>`);
            });
            
            inicializarSelect2EnModalEdicion($select, 'Buscar género...');
        },
        error: function(xhr, status, error) {
            console.error('❌ Error cargando géneros:', error);
        }
    });
    
    // Cargar OTRO ATRIBUTO (atributo4) - ocultar si no existe
    const $select4Container = $('#edit_atributo4_id').closest('.form-group');
    if (ID_ATRIBUTO_OTRO && ID_ATRIBUTO_OTRO > 0) {
        $select4Container.show();
        $.ajax({
            url: `/app/opciones_atributo/?atributo_id=${ID_ATRIBUTO_OTRO}`,
            method: 'GET',
            success: function(response) {
                const $select = $('#edit_atributo4_id');
                $select.empty().append('<option value="">Seleccionar...</option>');
                
                const opciones = Array.isArray(response) ? response : (response.opciones || []);
                console.log('📋 Otros atributos cargados:', opciones.length);
                opciones.forEach(opt => {
                    const selected = producto.atributo4_id == opt.id ? 'selected' : '';
                    $select.append(`<option value="${opt.id}" ${selected}>${opt.valor}</option>`);
                });
                
                inicializarSelect2EnModalEdicion($select, 'Buscar...');
            },
            error: function(xhr, status, error) {
                console.error('❌ Error cargando otros atributos:', error);
            }
        });
    } else {
        $select4Container.hide();
        console.log('ℹ️ Atributo 4 no configurado, ocultando campo');
    }
    
    console.log('✅ Iniciando carga de opciones Select2');
}

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
        const stockColor = variacion.stock_total === 0 ? '#dc3545' : 
                          variacion.stock_total < 5 ? '#f0a500' : '#0ab39c';
        const stockBg = variacion.stock_total === 0 ? '#ffeaea' : 
                       variacion.stock_total < 5 ? '#fff8e1' : '#e9f7ef';
        
        const row = `
            <tr data-variacion-id="${variacion.id}">
                <td><strong>${variacion.talla}</strong></td>
                <td><code style="font-size:.82rem;">${variacion.sku}</code></td>
                <td class="text-center">
                    <span style="display:inline-block;padding:3px 12px;border-radius:8px;font-weight:700;font-size:.9rem;background:${stockBg};color:${stockColor};">
                        ${variacion.stock_total}
                    </span>
                    ${variacion.lotes && variacion.lotes.length > 0 ? 
                        `<div class="text-muted" style="font-size:.68rem;">${variacion.lotes.length} lote(s)</div>` : ''}
                </td>
                <td class="text-center">
                    <button type="button" class="btn btn-sm btn-primary rounded-pill px-3" 
                            onclick="abrirModalAjustarStock(${variacion.id}, '${variacion.talla}', ${variacion.stock_total})"
                            title="Ajustar stock" style="font-size:.8rem;">
                        <i class="bi bi-box-seam me-1"></i>Ajustar
                    </button>
                </td>
                <td class="text-center">
                    <div class="d-flex gap-1 justify-content-center">
                        <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill" 
                                onclick="verHistorialMovimientos(${variacion.id}, '${variacion.talla}')"
                                title="Ver historial" style="font-size:.75rem;">
                            <i class="bi bi-clock-history"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-info rounded-pill" 
                                onclick="verLotesVariacion(${variacion.id}, '${variacion.talla}')"
                                title="Ver lotes FIFO" style="font-size:.75rem;">
                            <i class="bi bi-layers"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
        tbody.append(row);
        console.log(`Fila ${index + 1} agregada:`, row.substring(0, 100) + '...');
    });
    
    console.log(`✅ ${variaciones.length} variaciones cargadas en la tabla correctamente`);
    console.log('HTML final del tbody:', tbody.html().substring(0, 200) + '...');
};

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
    
    const propagar = $('#edit_propagar_sucursales').is(':checked');
    
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
        precioSugerido: parseInt($('#edit_precioSugerido').val()) || 0,
        propagar_sucursales: propagar,
        excluir_de_analitica: $('#edit_excluir_de_analitica').is(':checked'),
    };
    
    // Confirmación si va a propagar
    const guardar = () => {
        mostrarLoading('Guardando cambios...');
        
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
                let htmlMsg = `<div>${data.message}</div>`;
                const sync = data.sincronizacion;
                if (sync && sync.productos_actualizados > 1) {
                    htmlMsg += `
                        <div class="mt-2 small text-muted">
                            <i class="bi bi-arrow-repeat me-1"></i>
                            Sincronizado en <strong>${sync.productos_actualizados}</strong> bodega(s):
                            ${sync.sucursales_afectadas.map(s => `<span class="badge bg-secondary ms-1">${s}</span>`).join('')}
                        </div>`;
                    if (sync.lotes_actualizados > 0) {
                        htmlMsg += `<div class="small text-muted"><i class="bi bi-layers me-1"></i>${sync.lotes_actualizados} lote(s) FIFO actualizados</div>`;
                    }
                }
                if (typeof Swal !== 'undefined') {
                    Swal.fire({
                        icon: 'success',
                        title: '¡Guardado!',
                        html: htmlMsg,
                        timer: 3500,
                        showConfirmButton: false,
                        timerProgressBar: true
                    });
                }
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
    };

    if (propagar && typeof Swal !== 'undefined') {
        const sucursal = $('#editSucursalBadge').text() || 'todas las bodegas';
        Swal.fire({
            icon: 'question',
            title: 'Confirmar cambios globales',
            html: `Los cambios se aplicarán en <strong>todas las bodegas</strong> donde exista el artículo <em>"${articulo}"</em>.<br><small class="text-muted">Precio, descripción, categoría y atributos se sincronizarán.</small>`,
            showCancelButton: true,
            confirmButtonColor: '#0d6efd',
            cancelButtonColor: '#6c757d',
            confirmButtonText: '<i class="bi bi-save me-1"></i>Guardar en todas',
            cancelButtonText: 'Cancelar'
        }).then(result => {
            if (result.isConfirmed) guardar();
        });
    } else {
        guardar();
    }
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

// ========== FUNCIONES DE RECATEGORIZACIÓN ==========

/**
 * Cargar datos de impacto de recategorización
 */
function cargarImpactoRecategorizacion(productoId) {
    if (!productoId) return;
    
    $('#recat_total_sucursales, #recat_total_movimientos, #recat_total_tickets, #recat_total_dtes').text('...');
    
    fetch(`/app/productos/impacto-recategorizacion/${productoId}/`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const imp = data.impacto;
                $('#recat_total_sucursales').text(imp.total_sucursales.toLocaleString());
                $('#recat_total_movimientos').text(imp.total_movimientos.toLocaleString());
                $('#recat_total_tickets').text(imp.total_tickets.toLocaleString());
                $('#recat_total_dtes').text(imp.total_dtes.toLocaleString());
            } else {
                $('#recat_total_sucursales, #recat_total_movimientos, #recat_total_tickets, #recat_total_dtes').text('—');
            }
        })
        .catch(() => {
            $('#recat_total_sucursales, #recat_total_movimientos, #recat_total_tickets, #recat_total_dtes').text('—');
        });
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
    
    // Cargar impacto cuando se abre la pestaña Recategorizar
    $(document).on('shown.bs.tab', '#tab-recategorizacion', function() {
        const productoId = $('#edit_producto_id').val();
        if (productoId) {
            cargarImpactoRecategorizacion(productoId);
        }
    });
    
    // Limpiar Select2 cuando se cierra el modal de edición
    $('#modalEdicionProducto').on('hidden.bs.modal', function() {
        const $modal = $(this);
        
        $modal.find('select').each(function() {
            if ($(this).hasClass('select2-hidden-accessible')) {
                try {
                    $(this).select2('destroy');
                } catch(e) {
                    console.warn('Error al destruir Select2:', e);
                }
            }
        });
        
        $('.select2-container--open').remove();
        
        $('#edit_producto_id').val('');
        $('#edit_articulo').val('');
        $('#edit_descripcion').val('');
        $('#edit_costo').val('');
        $('#edit_sobreprecio').val('');
        $('#edit_precioventa').val('');
        $('#edit_precioSugerido').val('');
        $('#tablaVariacionesEdicion tbody').html('<tr><td colspan="5" class="text-center text-muted">Selecciona un producto para editar</td></tr>');
        
        // Reset recategorization stats
        $('#recat_total_sucursales, #recat_total_movimientos, #recat_total_tickets, #recat_total_dtes').text('—');
        
        // Reset to first tab
        $('#tab-datos-generales').tab('show');
    });
    
    // Reinicializar Select2 cuando el modal de edición está completamente visible
    $('#modalEdicionProducto').on('shown.bs.modal', function() {
        if (window.productoActualEdicion) {
            setTimeout(() => {
                cargarOpcionesConSelect2(window.productoActualEdicion);
            }, 100);
        }
    });
});

