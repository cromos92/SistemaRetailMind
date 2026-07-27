/**
 * despacho_sucursales.js
 * Módulo de Despacho a Sucursales: búsqueda/selección de productos con filtros,
 * carrito de despacho, envío masivo a múltiples sucursales, e historial de despachos.
 */
(function () {
    'use strict';

    const CFG = window.DESPACHO_CONFIG || {};

    // ========== ESTADO GLOBAL DEL MÓDULO ==========
    let carrito = [];
    let despachosMasivos = [];
    let sucursalesDisponibles = [];
    let marcasDisponibles = [];
    let productosPagina = [];
    let seleccionMultiple = new Map(); // producto_talla_id -> datos del producto (persiste entre páginas)
    let paginaProductosActual = 1;
    let totalPaginasProductos = 1;
    let filtrosProductos = { q: '', marca_id: '' };
    let historialFiltro = '';
    let historialPaginaActual = 1;
    let historialDias = 90;

    // Situación de cada documento de traspaso (enviadas vs recibidas).
    const SITUACION = {
        RECIBIDO: { badge: 'bg-success', texto: 'Recibido' },
        EN_TRANSITO: { badge: 'bg-warning text-dark', texto: 'En tránsito' },
        SIN_RECIBIR: { badge: 'bg-danger', texto: 'Sin recibir' },
        SOBRE_RECIBIDO: { badge: 'bg-dark', texto: 'Sobre-recibido' },
    };

    // ========== INIT ==========
    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cargarSucursales();
        cargarPendientes();
        cargarMarcas();
        cargarHistorial();

        const inputBuscar = document.getElementById('inputBuscar');
        if (inputBuscar) {
            inputBuscar.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') buscarProductos(1);
            });
        }
        const selectMarca = document.getElementById('selectMarcaFiltro');
        if (selectMarca) {
            selectMarca.addEventListener('change', () => buscarProductos(1));
        }
    }

    // ========== SUCURSALES ==========
    function cargarSucursales() {
        fetch(CFG.urls.sucursales)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) return;
                sucursalesDisponibles = data.sucursales;
                const select = document.getElementById('selectSucursal');
                if (!select) return;
                data.sucursales.forEach((s) => {
                    const opt = document.createElement('option');
                    opt.value = s.id;
                    opt.textContent = `${s.alias} - ${s.direccion || ''}`;
                    select.appendChild(opt);
                });
            });
    }

    // ========== MARCAS (FILTRO) ==========
    function cargarMarcas() {
        fetch(CFG.urls.marcas)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) return;
                marcasDisponibles = data.marcas;
                const select = document.getElementById('selectMarcaFiltro');
                if (!select) return;
                data.marcas.forEach((m) => {
                    const opt = document.createElement('option');
                    opt.value = m.id;
                    opt.textContent = `${m.nombre} (${m.productos})`;
                    select.appendChild(opt);
                });
                if (window.jQuery && jQuery.fn.select2) {
                    jQuery(select).select2({ width: '100%', placeholder: 'Todas las marcas' });
                }
            });
    }

    // ========== PENDIENTES DE DESPACHO ==========
    function cargarPendientes() {
        const container = document.getElementById('pendientesContainer');
        if (!container) return;
        container.innerHTML = '<div class="text-center text-muted py-3">Cargando pendientes...</div>';
        fetch(CFG.urls.pendientes)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) {
                    container.innerHTML = `<div class="text-center text-danger py-2">${escapeHtml(data.error || 'No se pudieron cargar los pendientes')}</div>`;
                    return;
                }
                const grupos = data.pendientes_por_sucursal || [];
                actualizarKpiPendientes(data.total_unidades || 0);
                setTextIfExists('badgePendientesLineas', `${data.total_lineas || 0} líneas`);
                if (!grupos.length) {
                    container.innerHTML = '<div class="text-center text-muted py-2">No hay pendientes de despacho</div>';
                    return;
                }
                // Un acordeón por destino: en producción hay más de mil líneas
                // abiertas y volcarlas todas de golpe hacía la tarjeta inusable.
                let html = '<div class="accordion" id="accPendientes">';
                grupos.forEach((suc, idx) => {
                    const alerta = suc.dias_mas_antiguo >= 30;
                    html += `<div class="accordion-item">
                        <h2 class="accordion-header">
                            <button class="accordion-button ${idx === 0 ? '' : 'collapsed'} py-2" type="button"
                                    data-bs-toggle="collapse" data-bs-target="#pend-${suc.sucursal_id}">
                                <span class="flex-grow-1">
                                    <i class="ri-store-2-line me-1 text-primary"></i>
                                    <strong>${escapeHtml(suc.alias)}</strong>
                                    <span class="badge bg-warning ms-2">${suc.total_unidades} uds</span>
                                    <span class="badge bg-light text-dark ms-1">${suc.total_lineas} líneas</span>
                                    <span class="badge ${alerta ? 'bg-danger' : 'bg-secondary'} ms-1"
                                          title="Antigüedad del pendiente más viejo">${suc.dias_mas_antiguo} d</span>
                                </span>
                            </button>
                        </h2>
                        <div id="pend-${suc.sucursal_id}" class="accordion-collapse collapse ${idx === 0 ? 'show' : ''}" data-bs-parent="#accPendientes">
                            <div class="accordion-body p-2">
                                <div class="table-responsive" style="max-height:260px;overflow-y:auto;">
                                <table class="table table-sm table-bordered mb-0">
                                    <thead class="table-light sticky-top"><tr><th>SKU</th><th>Artículo</th><th>Talla</th><th class="text-end">Restante</th><th class="text-end">Días</th><th></th></tr></thead>
                                    <tbody>`;
                    suc.items.forEach((item) => {
                        html += `<tr>
                            <td><code>${escapeHtml(item.sku)}</code></td>
                            <td>${escapeHtml(item.articulo)}</td>
                            <td>${escapeHtml(item.talla)}</td>
                            <td class="text-end fw-bold">${item.cantidad_restante}</td>
                            <td class="text-end ${item.dias >= 30 ? 'text-danger fw-bold' : 'text-muted'}">${item.dias}</td>
                            <td class="text-center"><button type="button" class="btn btn-sm btn-outline-primary" onclick="DespachoSucursales.agregarPendienteAlCarrito('${escapeHtml(item.sku)}', ${suc.sucursal_id})" title="Buscar este SKU para agregarlo al carrito"><i class="ri-add-line"></i></button></td>
                        </tr>`;
                    });
                    html += '</tbody></table></div>';
                    if (suc.truncado) {
                        html += `<div class="small text-muted mt-1">Se muestran las ${data.items_por_sucursal} líneas más antiguas de ${suc.total_lineas}. Descárguelas emitiendo la guía de despacho al destino.</div>`;
                    }
                    html += '</div></div></div>';
                });
                html += '</div>';
                container.innerHTML = html;
            })
            .catch(() => {
                container.innerHTML = '<div class="text-center text-danger py-2">Error de conexión al cargar pendientes</div>';
            });
    }

    function agregarPendienteAlCarrito(sku, sucursalId) {
        const selectSucursal = document.getElementById('selectSucursal');
        if (selectSucursal) selectSucursal.value = sucursalId;
        const inputBuscar = document.getElementById('inputBuscar');
        if (inputBuscar) inputBuscar.value = sku;
        buscarProductos(1);
    }

    // ========== BÚSQUEDA DE PRODUCTOS (FILTROS + PAGINACIÓN) ==========
    function buscarProductos(pagina) {
        filtrosProductos.q = (document.getElementById('inputBuscar') || {}).value?.trim() || '';
        filtrosProductos.marca_id = (document.getElementById('selectMarcaFiltro') || {}).value || '';
        paginaProductosActual = pagina || 1;

        const params = new URLSearchParams({ page: paginaProductosActual });
        if (filtrosProductos.q) params.set('q', filtrosProductos.q);
        if (filtrosProductos.marca_id) params.set('marca_id', filtrosProductos.marca_id);

        fetch(`${CFG.urls.productos}?${params.toString()}`)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) return;
                productosPagina = data.productos;
                totalPaginasProductos = data.total_paginas || 1;
                renderTablaProductos(data.productos);
                renderPaginacionProductos(data.pagina_actual, data.total_paginas, data.total_productos);
            });
    }

    function traerTodoElStock() {
        Swal.fire({
            title: '¿Cargar todo el stock disponible?',
            text: 'Se listarán todos los productos con stock de la sucursal actual (según filtros activos). Puede tardar unos segundos.',
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: 'Sí, cargar todo',
            cancelButtonText: 'Cancelar',
        }).then((result) => {
            if (!result.isConfirmed) return;

            filtrosProductos.q = (document.getElementById('inputBuscar') || {}).value?.trim() || '';
            filtrosProductos.marca_id = (document.getElementById('selectMarcaFiltro') || {}).value || '';

            const params = new URLSearchParams({ traer_todo: '1' });
            if (filtrosProductos.q) params.set('q', filtrosProductos.q);
            if (filtrosProductos.marca_id) params.set('marca_id', filtrosProductos.marca_id);

            Swal.fire({ title: 'Cargando stock...', allowOutsideClick: false, didOpen: () => Swal.showLoading() });

            fetch(`${CFG.urls.productos}?${params.toString()}`)
                .then((r) => r.json())
                .then((data) => {
                    Swal.close();
                    if (!data.success) return;
                    productosPagina = data.productos;
                    totalPaginasProductos = 1;
                    renderTablaProductos(data.productos);
                    ocultarPaginacionProductos();
                    if (data.truncado) {
                        Swal.fire('Atención', `Se muestran los primeros ${data.total_productos} productos. Hay más resultados de los que se pueden listar de una vez — use filtros para acotar la búsqueda.`, 'warning');
                    }
                });
        });
    }

    function renderTablaProductos(productos) {
        const tbody = document.getElementById('tbodyProductos');
        if (!tbody) return;
        if (!productos.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Sin resultados</td></tr>';
            return;
        }
        let html = '';
        productos.forEach((p) => {
            const checked = seleccionMultiple.has(p.producto_talla_id) ? 'checked' : '';
            html += `<tr>
                <td><input type="checkbox" class="form-check-input chk-producto" data-id="${p.producto_talla_id}" ${checked} onchange="DespachoSucursales.toggleSeleccion(${p.producto_talla_id})"></td>
                <td><code>${escapeHtml(p.sku)}</code></td>
                <td>${escapeHtml(p.articulo)}<br><small class="text-muted">${escapeHtml(p.marca)}</small></td>
                <td>${escapeHtml(p.talla)}</td>
                <td class="text-end fw-bold">${p.stock}</td>
                <td class="text-center">
                    <button type="button" class="btn btn-sm btn-primary" onclick='DespachoSucursales.agregarAlCarrito(${JSON.stringify(p)})'>
                        <i class="ri-add-line"></i>
                    </button>
                </td>
            </tr>`;
        });
        tbody.innerHTML = html;
        actualizarBotonSeleccionados();
        const chkTodos = document.getElementById('chkSeleccionarTodos');
        if (chkTodos) chkTodos.checked = productos.length > 0 && productos.every((p) => seleccionMultiple.has(p.producto_talla_id));
    }

    function toggleSeleccionarTodos(checked) {
        productosPagina.forEach((p) => {
            if (checked) {
                seleccionMultiple.set(p.producto_talla_id, p);
            } else {
                seleccionMultiple.delete(p.producto_talla_id);
            }
        });
        renderTablaProductos(productosPagina);
    }

    function toggleSeleccion(productoTallaId) {
        const producto = productosPagina.find((p) => p.producto_talla_id === productoTallaId);
        if (!producto) return;
        if (seleccionMultiple.has(productoTallaId)) {
            seleccionMultiple.delete(productoTallaId);
        } else {
            seleccionMultiple.set(productoTallaId, producto);
        }
        actualizarBotonSeleccionados();
    }

    function actualizarBotonSeleccionados() {
        const btn = document.getElementById('btnAgregarSeleccionados');
        if (!btn) return;
        const n = seleccionMultiple.size;
        btn.textContent = n > 0 ? `Agregar seleccionados (${n})` : 'Agregar seleccionados';
        btn.disabled = n === 0;
    }

    function agregarSeleccionadosAlCarrito() {
        if (!seleccionMultiple.size) return;
        seleccionMultiple.forEach((p) => {
            if (!carrito.find((c) => c.producto_talla_id === p.producto_talla_id)) {
                carrito.push({
                    producto_talla_id: p.producto_talla_id, sku: p.sku, articulo: p.articulo,
                    talla: p.talla, stock_max: p.stock, cantidad: 1,
                });
            }
        });
        seleccionMultiple.clear();
        renderTablaProductos(productosPagina);
        renderCarrito();
        Swal.fire({ toast: true, position: 'top-end', icon: 'success', title: 'Productos agregados al carrito', showConfirmButton: false, timer: 1500 });
    }

    function renderPaginacionProductos(actual, total, totalProductos) {
        const el = document.getElementById('paginacionProductos');
        if (!el) return;
        if (total <= 1) { el.style.display = 'none'; return; }
        el.style.display = '';
        document.getElementById('paginacionProductosInfo').textContent = `Página ${actual} de ${total} (${totalProductos} productos)`;
        document.getElementById('btnPaginaAnterior').disabled = actual <= 1;
        document.getElementById('btnPaginaSiguiente').disabled = actual >= total;
    }

    function ocultarPaginacionProductos() {
        const el = document.getElementById('paginacionProductos');
        if (el) el.style.display = 'none';
    }

    function paginaAnterior() {
        if (paginaProductosActual > 1) buscarProductos(paginaProductosActual - 1);
    }

    function paginaSiguiente() {
        if (paginaProductosActual < totalPaginasProductos) buscarProductos(paginaProductosActual + 1);
    }

    // ========== CARRITO ==========
    function agregarAlCarrito(p) {
        const existe = carrito.find((c) => c.producto_talla_id === p.producto_talla_id);
        if (existe) {
            Swal.fire({ toast: true, position: 'top-end', icon: 'info', title: 'Ya está en el carrito', showConfirmButton: false, timer: 1500 });
            return;
        }
        carrito.push({
            producto_talla_id: p.producto_talla_id, sku: p.sku, articulo: p.articulo,
            talla: p.talla, stock_max: p.stock, cantidad: 1,
        });
        renderCarrito();
    }

    function renderCarrito() {
        const container = document.getElementById('carritoItems');
        if (!container) return;
        if (!carrito.length) {
            container.innerHTML = '<div class="text-center text-muted py-3">Agregue productos al carrito</div>';
            document.getElementById('totalItems').textContent = '0';
            document.getElementById('totalUnidades').textContent = '0';
            return;
        }
        let html = '';
        carrito.forEach((item, idx) => {
            html += `<div class="d-flex align-items-center gap-2 mb-2 p-2 border rounded">
                <div class="flex-grow-1">
                    <strong>${escapeHtml(item.articulo)}</strong><br>
                    <small class="text-muted">SKU: ${escapeHtml(item.sku)} | Talla: ${escapeHtml(item.talla)}</small>
                </div>
                <input type="number" class="form-control form-control-sm" style="width:70px;" min="1" max="${item.stock_max}" value="${item.cantidad}"
                    onchange="DespachoSucursales.actualizarCantidad(${idx}, this.value)">
                <button type="button" class="btn btn-sm btn-outline-danger" onclick="DespachoSucursales.quitarDelCarrito(${idx})"><i class="ri-delete-bin-line"></i></button>
            </div>`;
        });
        container.innerHTML = html;
        document.getElementById('totalItems').textContent = carrito.length;
        document.getElementById('totalUnidades').textContent = carrito.reduce((a, b) => a + b.cantidad, 0);
    }

    function actualizarCantidad(idx, val) {
        const cant = Math.min(Math.max(1, parseInt(val, 10) || 1), carrito[idx].stock_max);
        carrito[idx].cantidad = cant;
        renderCarrito();
    }

    function quitarDelCarrito(idx) {
        carrito.splice(idx, 1);
        renderCarrito();
    }

    function agregarAlDespachoMasivo() {
        const sucId = document.getElementById('selectSucursal').value;
        if (!sucId) { Swal.fire('Falta información', 'Seleccione una sucursal destino.', 'warning'); return; }
        if (!carrito.length) { Swal.fire('Carrito vacío', 'Agregue productos al carrito.', 'warning'); return; }
        const suc = sucursalesDisponibles.find((s) => String(s.id) === String(sucId));
        despachosMasivos.push({
            sucursal_destino_id: parseInt(sucId, 10),
            sucursal_alias: suc ? suc.alias : 'Sucursal ' + sucId,
            items: [...carrito],
        });
        carrito = [];
        renderCarrito();
        renderDespachosMasivos();
    }

    function renderDespachosMasivos() {
        const container = document.getElementById('despachosMasivosBody');
        const card = document.getElementById('despachosMasivos');
        if (!card || !container) return;
        if (!despachosMasivos.length) { card.style.display = 'none'; return; }
        card.style.display = '';
        let html = '';
        despachosMasivos.forEach((d, idx) => {
            const totalUn = d.items.reduce((a, b) => a + b.cantidad, 0);
            html += `<div class="mb-2 p-2 border rounded bg-light">
                <div class="d-flex justify-content-between align-items-center">
                    <strong><i class="ri-store-2-line me-1"></i>${escapeHtml(d.sucursal_alias)}</strong>
                    <div>
                        <span class="badge bg-primary me-1">${d.items.length} items / ${totalUn} uds</span>
                        <button type="button" class="btn btn-sm btn-outline-danger" onclick="DespachoSucursales.quitarDespacho(${idx})"><i class="ri-close-line"></i></button>
                    </div>
                </div>
            </div>`;
        });
        container.innerHTML = html;
    }

    function quitarDespacho(idx) {
        despachosMasivos.splice(idx, 1);
        renderDespachosMasivos();
    }

    function enviarTodosDespachos() {
        if (!despachosMasivos.length) { Swal.fire('Nada que enviar', 'No hay despachos para enviar.', 'warning'); return; }

        Swal.fire({
            title: `¿Enviar ${despachosMasivos.length} despacho(s)?`,
            html: 'Se descontará el stock de inmediato de esta bodega.<br><br>'
                + '<b class="text-danger">Este envío NO emite guía ni factura.</b><br>'
                + 'Sin documento, el destino no puede recibir la mercadería y las unidades quedan '
                + 'fuera del stock de las dos sucursales. Para un traspaso normal, cancele y use '
                + '<b>Emitir guía</b>.',
            icon: 'warning',
            showCancelButton: true,
            confirmButtonText: 'Entiendo, enviar igual',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#f06548',
        }).then((result) => {
            if (!result.isConfirmed) return;

            const payload = {
                despachos: despachosMasivos.map((d) => ({
                    sucursal_destino_id: d.sucursal_destino_id,
                    items: d.items.map((i) => ({ producto_talla_id: i.producto_talla_id, cantidad: i.cantidad })),
                })),
                observaciones: document.getElementById('inputObservaciones').value,
            };

            fetch(CFG.urls.crearMasivo, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
                body: JSON.stringify(payload),
            })
                .then((r) => r.json())
                .then((data) => {
                    if (data.success) {
                        Swal.fire('Listo', data.mensaje, 'success');
                        despachosMasivos = [];
                        renderDespachosMasivos();
                        cargarPendientes();
                        cargarHistorial();
                    } else {
                        Swal.fire('Error', data.error || 'No se pudo completar el despacho.', 'error');
                    }
                })
                .catch(() => Swal.fire('Error', 'No se pudo completar el despacho.', 'error'));
        });
    }

    // ========== HISTORIAL REAL DE DESPACHOS (por documento de traspaso) ==========
    function cargarHistorial(pagina) {
        historialPaginaActual = pagina || 1;
        const params = new URLSearchParams({ page: historialPaginaActual, dias: historialDias });
        if (historialFiltro) params.set('filtro', historialFiltro);

        fetch(`${CFG.urls.historial}?${params.toString()}`)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) {
                    const tbody = document.getElementById('tbodyHistorial');
                    if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger">${escapeHtml(data.error || 'No se pudo cargar el historial')}</td></tr>`;
                    return;
                }
                renderTablaHistorial(data.despachos);
                renderPaginacionHistorial(data.pagina_actual, data.total_paginas, data.total_despachos);
                renderResumenHistorial(data.resumen, data.dias);
            });
    }

    function renderTablaHistorial(despachos) {
        const tbody = document.getElementById('tbodyHistorial');
        if (!tbody) return;
        if (!despachos.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">Sin despachos en el período seleccionado</td></tr>';
            return;
        }
        let html = '';
        despachos.forEach((d) => {
            const sit = SITUACION[d.situacion] || { badge: 'bg-secondary', texto: d.situacion };
            const alerta = d.situacion === 'SIN_RECIBIR';
            const nc = d.devueltas_nc > 0
                ? `<br><small class="text-muted">${d.devueltas_nc} devueltas por NC</small>` : '';
            html += `<tr class="${alerta ? 'table-danger' : ''}">
                <td><span class="fw-semibold">#${escapeHtml(d.numero_documento)}</span><br>
                    <small class="text-muted">${escapeHtml(d.tipo_documento)}</small></td>
                <td><i class="ri-store-2-line me-1 text-primary"></i>${escapeHtml(d.destino)}</td>
                <td>${escapeHtml(d.fecha)}<br><small class="text-muted">hace ${d.dias} d</small></td>
                <td class="text-end">${d.items}</td>
                <td class="text-end">${d.enviadas}</td>
                <td class="text-end">${d.recibidas}${nc}</td>
                <td class="text-end fw-bold ${d.pendientes > 0 ? 'text-danger' : 'text-muted'}">${d.pendientes}</td>
                <td><span class="badge ${sit.badge}">${sit.texto}</span></td>
            </tr>`;
        });
        tbody.innerHTML = html;
    }

    function renderResumenHistorial(resumen, dias) {
        if (!resumen) return;
        setTextIfExists('tab-count-total', resumen.documentos);
        setTextIfExists('tab-count-sin_recibir', resumen.docs_sin_recibir);
        setTextIfExists('tab-count-en_transito', resumen.docs_en_transito);
        setTextIfExists('tab-count-recibido', resumen.docs_recibidos);
        setTextIfExists('tab-count-sobre_recibido', resumen.docs_sobre_recibidos || 0);

        setTextIfExists('kpiDespachadas', resumen.unidades_enviadas);
        setTextIfExists('kpiEnTransito', resumen.unidades_en_transito);
        setTextIfExists('kpiSinRecibir', resumen.unidades_sin_recibir);
        document.querySelectorAll('.kpi-dias').forEach((el) => { el.textContent = dias; });

        const alerta = document.getElementById('alertaSinRecibir');
        if (alerta) {
            if (resumen.docs_sin_recibir > 0) {
                alerta.style.setProperty('display', 'flex', 'important');
                setTextIfExists('alertaSinRecibirTitulo',
                    `${resumen.unidades_sin_recibir} unidades salieron de esta bodega y nadie las recibió.`);
                setTextIfExists('alertaSinRecibirDetalle',
                    `${resumen.docs_sin_recibir} documento(s) en los últimos ${dias} días. ` +
                    'El stock ya se descontó acá y no entró en el destino: mientras nadie lo reciba, esas unidades no existen en el sistema.');
            } else {
                alerta.style.setProperty('display', 'none', 'important');
            }
        }
    }

    function setTextIfExists(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function filtrarHistorial(filtro, el) {
        historialFiltro = filtro;
        document.querySelectorAll('#tabsHistorial .nav-link').forEach((tab) => tab.classList.remove('active'));
        if (el) el.classList.add('active');
        cargarHistorial(1);
    }

    function cambiarPeriodo(dias) {
        historialDias = parseInt(dias, 10) || 90;
        cargarHistorial(1);
    }

    function renderPaginacionHistorial(actual, total, totalDespachos) {
        const el = document.getElementById('paginacionHistorial');
        if (!el) return;
        if (total <= 1) { el.style.display = 'none'; return; }
        el.style.display = '';
        document.getElementById('paginacionHistorialInfo').textContent = `Página ${actual} de ${total} (${totalDespachos} despachos)`;
        document.getElementById('btnHistorialAnterior').disabled = actual <= 1;
        document.getElementById('btnHistorialSiguiente').disabled = actual >= total;
    }

    function historialPaginaAnteriorFn() {
        if (historialPaginaActual > 1) cargarHistorial(historialPaginaActual - 1);
    }

    function historialPaginaSiguienteFn() {
        cargarHistorial(historialPaginaActual + 1);
    }

    // ========== KPIs ==========
    function actualizarKpiPendientes(totalUnidades) {
        setTextIfExists('kpiPendientes', totalUnidades);
    }

    // ========== UTILIDADES ==========
    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ========== API PÚBLICA (para onclick inline en el template) ==========
    window.DespachoSucursales = {
        buscarProductos,
        traerTodoElStock,
        paginaAnterior,
        paginaSiguiente,
        toggleSeleccionarTodos,
        toggleSeleccion,
        agregarSeleccionadosAlCarrito,
        agregarAlCarrito,
        agregarPendienteAlCarrito,
        actualizarCantidad,
        quitarDelCarrito,
        agregarAlDespachoMasivo,
        quitarDespacho,
        enviarTodosDespachos,
        cargarPendientes,
        filtrarHistorial,
        cambiarPeriodo,
        historialPaginaAnterior: historialPaginaAnteriorFn,
        historialPaginaSiguiente: historialPaginaSiguienteFn,
    };
})();
