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
    let historialEstadoFiltro = '';
    let historialPaginaActual = 1;

    const ESTADO_BADGE = {
        PENDIENTE: 'bg-warning',
        APROBADO: 'bg-info',
        EN_TRANSITO: 'bg-primary',
        RECIBIDO: 'bg-success',
        RECHAZADO: 'bg-danger',
        ANULADO: 'bg-secondary',
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
        fetch(CFG.urls.pendientes)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success || !data.pendientes_por_sucursal.length) {
                    container.innerHTML = '<div class="text-center text-muted py-2">No hay pendientes de despacho</div>';
                    actualizarKpiPendientes(0);
                    return;
                }
                let totalPendientes = 0;
                let html = '';
                data.pendientes_por_sucursal.forEach((suc) => {
                    totalPendientes += suc.total_unidades;
                    html += `<div class="mb-3">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <h6 class="mb-0"><i class="ri-store-2-line me-1 text-primary"></i>${escapeHtml(suc.alias)}</h6>
                            <span class="badge bg-warning">${suc.total_unidades} unidades</span>
                        </div>
                        <div class="table-responsive"><table class="table table-sm table-bordered mb-0">
                            <thead class="table-light"><tr><th>SKU</th><th>Artículo</th><th>Talla</th><th class="text-end">Restante</th><th></th></tr></thead>
                            <tbody>`;
                    suc.items.forEach((item) => {
                        html += `<tr>
                            <td><code>${escapeHtml(item.sku)}</code></td><td>${escapeHtml(item.articulo)}</td><td>${escapeHtml(item.talla)}</td>
                            <td class="text-end fw-bold">${item.cantidad_restante}</td>
                            <td class="text-center"><button type="button" class="btn btn-sm btn-outline-primary" onclick="DespachoSucursales.agregarPendienteAlCarrito('${escapeHtml(item.sku)}', ${suc.sucursal_id})"><i class="ri-add-line"></i></button></td>
                        </tr>`;
                    });
                    html += '</tbody></table></div></div>';
                });
                container.innerHTML = html;
                actualizarKpiPendientes(totalPendientes);
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
            text: 'Se descontará el stock de inmediato de la sucursal actual.',
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: 'Sí, enviar',
            cancelButtonText: 'Cancelar',
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

    // ========== HISTORIAL DE DESPACHOS ==========
    function cargarHistorial(pagina) {
        historialPaginaActual = pagina || 1;
        const params = new URLSearchParams({ page: historialPaginaActual });
        if (historialEstadoFiltro) params.set('estado', historialEstadoFiltro);

        fetch(`${CFG.urls.historial}?${params.toString()}`)
            .then((r) => r.json())
            .then((data) => {
                if (!data.success) return;
                renderTablaHistorial(data.despachos);
                renderPaginacionHistorial(data.pagina_actual, data.total_paginas, data.total_despachos);
                renderTabsHistorial(data.conteos_por_estado);
                actualizarKpisHistorial(data.conteos_por_estado);
            });
    }

    function renderTablaHistorial(despachos) {
        const tbody = document.getElementById('tbodyHistorial');
        if (!tbody) return;
        if (!despachos.length) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Sin despachos registrados</td></tr>';
            return;
        }
        let html = '';
        despachos.forEach((d) => {
            const badge = ESTADO_BADGE[d.estado] || 'bg-secondary';
            html += `<tr>
                <td>#${d.numero_traspaso}</td>
                <td><i class="ri-store-2-line me-1 text-primary"></i>${escapeHtml(d.destino)}</td>
                <td>${escapeHtml(d.fecha_solicitud)}</td>
                <td class="text-end">${d.total_items}</td>
                <td class="text-end">${d.total_unidades}</td>
                <td><span class="badge ${badge}">${escapeHtml(d.estado_display)}</span></td>
                <td>${escapeHtml(d.solicitante)}</td>
            </tr>`;
        });
        tbody.innerHTML = html;
    }

    function renderTabsHistorial(conteos) {
        const total = Object.values(conteos || {}).reduce((a, b) => a + b, 0);
        setTextIfExists('tab-count-total', total);
        setTextIfExists('tab-count-pendiente', conteos.PENDIENTE || 0);
        setTextIfExists('tab-count-en_transito', conteos.EN_TRANSITO || 0);
        setTextIfExists('tab-count-recibido', conteos.RECIBIDO || 0);
        setTextIfExists('tab-count-rechazado', (conteos.RECHAZADO || 0) + (conteos.ANULADO || 0));
    }

    function setTextIfExists(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function filtrarHistorialPorEstado(estado, el) {
        historialEstadoFiltro = estado;
        document.querySelectorAll('#tabsHistorial .nav-link').forEach((tab) => tab.classList.remove('active'));
        if (el) el.classList.add('active');
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

    function actualizarKpisHistorial(conteos) {
        const enTransito = conteos.EN_TRANSITO || 0;
        const recibidos = conteos.RECIBIDO || 0;
        setTextIfExists('kpiEnTransito', enTransito);
        setTextIfExists('kpiRecibidos', recibidos);
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
        filtrarHistorialPorEstado,
        historialPaginaAnterior: historialPaginaAnteriorFn,
        historialPaginaSiguiente: historialPaginaSiguienteFn,
    };
})();
