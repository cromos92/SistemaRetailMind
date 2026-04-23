/**
 * Helper compartido para renderizar el árbol de trazabilidad de un DTE.
 *
 * Uso:
 *    window.TrazabilidadDTE.abrir(dteId);
 *
 * Requiere en la página:
 *  - jQuery
 *  - Bootstrap 5 (modal)
 *  - SweetAlert2 (para errores)
 *
 * Incluye el modal automáticamente si no existe en el DOM.
 */
(function () {
    'use strict';

    const MODAL_ID = 'modalTrazabilidadDTE';

    function fmtPesos(v) {
        const n = Number(v) || 0;
        return '$' + n.toLocaleString('es-CL');
    }

    function asegurarModal() {
        if (document.getElementById(MODAL_ID)) return;
        const html = `
        <div class="modal fade" id="${MODAL_ID}" tabindex="-1" aria-hidden="true">
          <div class="modal-dialog modal-xl modal-dialog-scrollable">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">
                  <i class="bi bi-diagram-3 me-1"></i> Trazabilidad del DTE
                  <span id="trazDteRef" class="text-muted small ms-2"></span>
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body" id="trazModalBody">
                <div class="text-center py-5">
                  <div class="spinner-border text-primary"></div>
                  <p class="mt-2 text-muted">Cargando trazabilidad...</p>
                </div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cerrar</button>
              </div>
            </div>
          </div>
        </div>`;
        document.body.insertAdjacentHTML('beforeend', html);
    }

    function renderPadre(padre) {
        const compras = (padre && padre.compras) || [];
        if (!compras.length) {
            return `<div class="alert alert-light small mb-0">Sin compras previas vinculadas a los SKUs de este DTE.</div>`;
        }
        const filas = compras.map(c => `
            <tr>
                <td><code>#${c.correlativo || '-'}</code></td>
                <td>${(c.nombre || '').replace(/</g, '&lt;')}</td>
                <td>${c.empresa || '-'}</td>
                <td>${c.fecha || '-'}</td>
                <td><small class="badge bg-secondary">${c.estado || '-'}</small></td>
                <td><small class="text-muted">${(c.skus_aportados || []).length} SKU</small></td>
            </tr>`).join('');
        return `
            <table class="table table-sm table-striped mb-0">
                <thead class="table-light"><tr>
                    <th>Correlativo</th><th>Compra</th><th>Empresa</th>
                    <th>Fecha</th><th>Estado</th><th>SKUs aportados</th>
                </tr></thead>
                <tbody>${filas}</tbody>
            </table>`;
    }

    function renderHijos(hijos) {
        if (!hijos || !hijos.length) {
            return `<div class="alert alert-light small mb-0">Sin notas de crédito ni ajustes emitidos sobre este DTE.</div>`;
        }
        const filas = hijos.map(h => {
            const badge = h.es_nota_credito
                ? '<span class="badge bg-danger text-white">NC</span>'
                : '<span class="badge bg-warning text-dark">AJUSTE</span>';
            // Si la NC/AJUSTE quedó sin movimientos de reversa, mostrar
            // indicador + botón de reparación.
            const pendiente = h.stock_pendiente
                ? `<span class="badge bg-warning text-dark ms-1" title="Esta NC no generó los movimientos de reversa de stock. Se emitió antes del fix unificado.">
                       <i class="bi bi-exclamation-triangle me-1"></i>Stock pendiente (${h.faltantes_totales || '?'})
                   </span>`
                : '';
            const botonReparar = h.stock_pendiente
                ? `<button class="btn btn-sm btn-warning btn-reparar-nc-stock" data-nc-id="${h.id}" title="Reparar stock histórico">
                       <i class="bi bi-tools me-1"></i>Reparar
                   </button>`
                : '';
            return `
                <tr>
                    <td>${badge}${pendiente}</td>
                    <td>${h.tipo_documento}<br><small class="text-muted">#${h.numero_documento}</small></td>
                    <td>${h.fecha_emision || '-'}</td>
                    <td class="text-end"><strong>${h.unidades}</strong> uds</td>
                    <td class="text-end">${fmtPesos(h.monto_con_iva)}</td>
                    <td><span class="badge bg-secondary">${(h.estado_dte || '').replace(/_/g, ' ')}</span></td>
                    <td><small class="text-muted">${(h.motivo_nc || '').substring(0, 80)}</small></td>
                    <td class="text-end">${botonReparar}</td>
                </tr>`;
        }).join('');
        return `
            <table class="table table-sm table-striped mb-0">
                <thead class="table-light"><tr>
                    <th>Tipo</th><th>Documento</th><th>Fecha</th>
                    <th class="text-end">Unidades</th><th class="text-end">Monto</th>
                    <th>Estado</th><th>Motivo</th><th></th>
                </tr></thead>
                <tbody>${filas}</tbody>
            </table>`;
    }

    function renderMovimientos(mov) {
        const grupos = (mov && mov.por_sucursal) || [];
        if (!grupos.length) {
            return `<div class="alert alert-light small mb-0">Sin movimientos de stock registrados.</div>`;
        }
        return grupos.map(g => {
            const filas = (g.items || []).map(it => `
                <tr>
                    <td><small>${it.fecha || '-'} ${it.hora || ''}</small></td>
                    <td><small class="font-monospace">${it.sku || '-'}</small></td>
                    <td>${it.concepto}</td>
                    <td>${it.tipo_movimiento}</td>
                    <td class="text-end"><strong>${it.cantidad}</strong></td>
                    <td><small class="text-muted">${it.sucursal_origen || '-'} → ${it.sucursal_destino || '-'}</small></td>
                </tr>`).join('');
            return `
                <div class="mb-3">
                    <h6 class="small text-muted mb-1">
                        <i class="bi bi-shop me-1"></i>
                        ${g.sucursal_alias || 'Sin sucursal'}
                        <span class="text-muted">(${(g.items || []).length} mov.)</span>
                    </h6>
                    <table class="table table-sm table-bordered mb-0">
                        <thead class="table-light"><tr>
                            <th>Fecha</th><th>SKU</th><th>Concepto</th>
                            <th>Tipo</th><th class="text-end">Cant.</th><th>Origen → Destino</th>
                        </tr></thead>
                        <tbody>${filas}</tbody>
                    </table>
                </div>`;
        }).join('');
    }

    function renderCabecera(d) {
        const afectado = d.documento_afectado
            ? `<li><strong>Documento afectado:</strong> ${d.documento_afectado.tipo_documento} #${d.documento_afectado.numero_documento}</li>`
            : '';
        return `
            <div class="card mb-3">
              <div class="card-body">
                <ul class="list-unstyled mb-0 small">
                  <li><strong>${d.tipo_documento}</strong> #${d.numero_documento} &middot;
                      <span class="badge bg-primary">${d.tipo_transaccion || '-'}</span> &middot;
                      <span class="badge bg-secondary">${(d.estado_dte || '').replace(/_/g, ' ')}</span></li>
                  <li><strong>Emisor:</strong> ${d.emisor || '-'} &middot; <strong>Sucursal:</strong> ${d.sucursal || '-'}</li>
                  <li><strong>Receptor:</strong> ${d.receptor || '-'}</li>
                  <li><strong>Fecha emisión:</strong> ${d.fecha_emision || '-'}
                      &middot; <strong>Recepción:</strong> ${d.fecha_recepcion || '-'}</li>
                  <li><strong>Unidades:</strong> ${d.unidades_productos} &middot;
                      <strong>Monto:</strong> ${fmtPesos(d.monto_con_iva)}</li>
                  ${afectado}
                </ul>
              </div>
            </div>`;
    }

    function render(data) {
        const body = document.getElementById('trazModalBody');
        if (!body) return;
        document.getElementById('trazDteRef').textContent =
            '#' + (data.dte.numero_documento || data.dte.id);
        body.innerHTML = `
            ${renderCabecera(data.dte)}
            <div class="mb-4">
                <h6 class="text-primary"><i class="bi bi-arrow-up-circle me-1"></i>Origen de los productos (compras)</h6>
                ${renderPadre(data.padre)}
            </div>
            <div class="mb-4">
                <h6 class="text-danger"><i class="bi bi-arrow-down-circle me-1"></i>Documentos hijos (NC / Ajustes)</h6>
                ${renderHijos(data.hijos)}
            </div>
            <div>
                <h6 class="text-info"><i class="bi bi-box-arrow-in-right me-1"></i>Movimientos de stock asociados</h6>
                ${renderMovimientos(data.movimientos)}
            </div>
        `;
    }

    function renderError(msg) {
        const body = document.getElementById('trazModalBody');
        if (!body) return;
        body.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle me-1"></i>
                ${msg || 'No se pudo cargar la trazabilidad.'}
            </div>`;
    }

    function abrir(dteId) {
        if (!dteId) return;
        asegurarModal();
        document.getElementById('trazModalBody').innerHTML = `
            <div class="text-center py-5">
                <div class="spinner-border text-primary"></div>
                <p class="mt-2 text-muted">Cargando trazabilidad...</p>
            </div>`;
        document.getElementById('trazDteRef').textContent = '#' + dteId;

        const modalEl = document.getElementById(MODAL_ID);
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        fetch(`/app/api/dte/${dteId}/trazabilidad/`, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        })
            .then(r => r.json().then(j => ({ ok: r.ok, data: j })))
            .then(({ ok, data }) => {
                if (!ok || !data.success) {
                    renderError(data && data.error);
                    return;
                }
                window.__trazDteActual = dteId;
                render(data);
            })
            .catch(err => renderError(err && err.message));
    }

    // =====================================================================
    // Modal de REPARACIÓN de NC histórica.
    // =====================================================================
    const REPAR_MODAL_ID = 'modalRepararNcStock';

    function asegurarModalReparacion() {
        if (document.getElementById(REPAR_MODAL_ID)) return;
        const html = `
        <div class="modal fade" id="${REPAR_MODAL_ID}" tabindex="-1" aria-hidden="true">
          <div class="modal-dialog modal-lg modal-dialog-scrollable">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">
                  <i class="bi bi-tools me-1"></i> Reparar stock histórico
                  <span id="reparNcRef" class="text-muted small ms-2"></span>
                </h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body" id="reparModalBody">
                <div class="text-center py-4">
                  <div class="spinner-border text-warning"></div>
                  <p class="mt-2 text-muted">Cargando diagnóstico...</p>
                </div>
              </div>
              <div class="modal-footer">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
                <button type="button" class="btn btn-warning" id="btnAplicarReparacion" disabled>
                    <i class="bi bi-check-circle me-1"></i>Aplicar reparación
                </button>
              </div>
            </div>
          </div>
        </div>`;
        document.body.insertAdjacentHTML('beforeend', html);

        document.getElementById('btnAplicarReparacion').addEventListener('click', aplicarReparacion);
    }

    function _getCsrf() {
        const name = 'csrftoken';
        const parts = document.cookie.split(';').map(c => c.trim());
        for (const p of parts) {
            if (p.startsWith(name + '=')) return decodeURIComponent(p.substring(name.length + 1));
        }
        return '';
    }

    function _fmtBadgeFase(recepcionado) {
        return recepcionado
            ? '<span class="badge bg-danger text-white">POST-recepción</span>'
            : '<span class="badge bg-warning text-dark">PRE-recepción</span>';
    }

    function renderDiagnosticoReparacion(diag) {
        const body = document.getElementById('reparModalBody');
        if (!body) return;
        if (!diag || !diag.lineas || !diag.lineas.length) {
            body.innerHTML = `
                <div class="alert alert-success">
                    <i class="bi bi-check-circle me-1"></i>
                    No hay líneas pendientes de reparación en esta NC.
                </div>`;
            return;
        }

        const headerPost = diag.recepcionado
            ? '<th class="text-end" title="Stock actual en la sucursal destino">Stock destino</th>'
            : '';

        const filas = diag.lineas.map(l => {
            const maxRepar = l.faltantes;
            const deshabilitado = !l.reparable ? 'disabled' : '';
            const warn = !l.reparable
                ? `<div class="small text-danger"><i class="bi bi-exclamation-triangle"></i> Stock destino insuficiente (disp. ${l.stock_destino_actual ?? '?'})</div>`
                : '';
            const colDestino = diag.recepcionado
                ? `<td class="text-end"><span class="badge bg-info text-white">${l.stock_destino_actual ?? '-'}</span></td>`
                : '';
            return `
                <tr data-sku="${l.sku}" data-faltantes="${l.faltantes}" data-reparable="${l.reparable ? 1 : 0}">
                    <td><input type="checkbox" class="form-check-input chk-linea-reparar" ${l.reparable ? 'checked' : ''} ${deshabilitado}></td>
                    <td><small class="font-monospace">${l.sku}</small></td>
                    <td><small>${l.descripcion || '-'}</small></td>
                    <td class="text-center"><span class="badge bg-secondary">${l.talla || '-'}</span></td>
                    <td class="text-end"><strong>${l.cantidad_nc}</strong></td>
                    <td class="text-end"><small class="text-muted">${l.movimientos_existentes}</small></td>
                    <td class="text-end">
                        <input type="number" class="form-control form-control-sm text-end input-cantidad-reparar"
                               min="1" max="${maxRepar}" value="${maxRepar}"
                               style="width: 85px; display:inline-block;" ${deshabilitado}>
                        ${warn}
                    </td>
                    ${colDestino}
                    <td class="text-end"><span class="badge bg-light text-dark">${l.stock_origen_actual ?? '-'}</span></td>
                </tr>`;
        }).join('');

        body.innerHTML = `
            <div class="alert alert-warning small">
                <strong>Atención:</strong> esta NC fue emitida antes del fix unificado y
                no generó los movimientos de stock de reversa. Al aplicar la reparación
                ${diag.recepcionado
                    ? 'se descontarán las unidades seleccionadas del destino (<strong>' + (diag.sucursal_destino || '?') + '</strong>) y se reingresarán al origen (<strong>' + (diag.sucursal_origen || '?') + '</strong>).'
                    : 'se reingresarán las unidades al origen (<strong>' + (diag.sucursal_origen || '?') + '</strong>).'}
                La acción es idempotente: si se aplica dos veces, la segunda es bloqueada.
            </div>
            <ul class="list-unstyled small mb-3">
                <li><strong>NC:</strong> ${diag.nc_tipo} #${diag.nc_numero} (${diag.nc_fecha || '-'})</li>
                <li><strong>DTE padre:</strong> ${diag.dte_padre_tipo} #${diag.dte_padre_numero}
                    — estado: ${(diag.dte_padre_estado || '').replace(/_/g, ' ')}
                    — fase: ${_fmtBadgeFase(diag.recepcionado)}</li>
            </ul>
            <div class="mb-2">
                <label class="form-label small mb-1"><strong>Motivo / nota (opcional):</strong></label>
                <input type="text" id="inputMotivoReparacion" class="form-control form-control-sm"
                       placeholder="Ej: barrido retroactivo NCs 2026-Q1" maxlength="200">
            </div>
            <div class="table-responsive">
                <table class="table table-sm table-bordered align-middle">
                    <thead class="table-light">
                        <tr>
                            <th></th>
                            <th>SKU</th>
                            <th>Descripción</th>
                            <th class="text-center">Talla</th>
                            <th class="text-end">NC (uds)</th>
                            <th class="text-end" title="Movimientos de reversa que ya existen">Ya rev.</th>
                            <th class="text-end">Reparar (uds)</th>
                            ${headerPost}
                            <th class="text-end">Stock origen</th>
                        </tr>
                    </thead>
                    <tbody id="reparLineasBody">${filas}</tbody>
                </table>
            </div>`;

        document.getElementById('btnAplicarReparacion').disabled = !diag.lineas.some(l => l.reparable);
        // Guardar diagnóstico global para aplicar.
        window.__reparDiag = diag;
    }

    function abrirReparacion(ncId) {
        if (!ncId) return;
        asegurarModalReparacion();
        document.getElementById('reparNcRef').textContent = '#' + ncId;
        document.getElementById('btnAplicarReparacion').disabled = true;
        document.getElementById('reparModalBody').innerHTML = `
            <div class="text-center py-4">
                <div class="spinner-border text-warning"></div>
                <p class="mt-2 text-muted">Cargando diagnóstico...</p>
            </div>`;

        const modalEl = document.getElementById(REPAR_MODAL_ID);
        bootstrap.Modal.getOrCreateInstance(modalEl).show();

        // Cargamos el diagnóstico vía api/ncs_sin_stock filtrado por esa NC:
        // para evitar agregar otro endpoint, reutilizamos la info pidiendo
        // la trazabilidad del padre — pero necesitamos el detalle por línea,
        // así que usamos ncs_sin_stock sin filtro y filtramos client-side
        // por nc_id. Si hay muchas NCs pendientes esto puede ser pesado,
        // así que usamos además paginación con page_size=100 y seguimos
        // pagineando hasta encontrar la NC. Para lotes chicos es suficiente.
        _buscarDiagnosticoNc(ncId, 1);
    }

    function _buscarDiagnosticoNc(ncId, pagina) {
        fetch(`/app/api/dte/ncs_sin_stock/?pagina=${pagina}&page_size=100`, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        })
            .then(r => r.json())
            .then(data => {
                if (!data || !data.success) {
                    document.getElementById('reparModalBody').innerHTML = `
                        <div class="alert alert-danger">
                            ${(data && data.error) || 'No se pudo cargar el diagnóstico.'}
                        </div>`;
                    return;
                }
                const items = data.items || [];
                const diag = items.find(i => i.nc_id === ncId);
                if (diag) {
                    renderDiagnosticoReparacion(diag);
                    return;
                }
                if (pagina < (data.total_paginas || 1)) {
                    _buscarDiagnosticoNc(ncId, pagina + 1);
                } else {
                    document.getElementById('reparModalBody').innerHTML = `
                        <div class="alert alert-info">
                            <i class="bi bi-info-circle me-1"></i>
                            Esta NC ya está reparada o no tiene líneas pendientes.
                        </div>`;
                }
            })
            .catch(err => {
                document.getElementById('reparModalBody').innerHTML = `
                    <div class="alert alert-danger">${err && err.message}</div>`;
            });
    }

    function aplicarReparacion() {
        const diag = window.__reparDiag;
        if (!diag) return;

        const lineas = [];
        document.querySelectorAll('#reparLineasBody tr').forEach(tr => {
            const chk = tr.querySelector('.chk-linea-reparar');
            if (!chk || !chk.checked) return;
            const sku = parseInt(tr.dataset.sku, 10);
            const input = tr.querySelector('.input-cantidad-reparar');
            const cant = parseInt(input.value, 10);
            if (isNaN(cant) || cant <= 0) return;
            lineas.push({ sku, cantidad: cant });
        });
        if (!lineas.length) {
            if (window.Swal) Swal.fire('Sin líneas', 'Seleccione al menos una línea para reparar.', 'info');
            return;
        }

        const motivo = (document.getElementById('inputMotivoReparacion') || {}).value || '';
        const btn = document.getElementById('btnAplicarReparacion');
        btn.disabled = true;
        const htmlOriginal = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Procesando...';

        fetch(`/app/dte/${diag.nc_id}/reparar_stock/`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': _getCsrf(),
                'Accept': 'application/json',
            },
            body: JSON.stringify({ lineas, motivo }),
        })
            .then(r => r.json().then(j => ({ ok: r.ok, status: r.status, data: j })))
            .then(({ ok, status, data }) => {
                if (!ok || !data.success) {
                    const msg = (data && data.error) || 'Error al aplicar la reparación.';
                    if (window.Swal) {
                        Swal.fire({
                            icon: status === 409 ? 'warning' : 'error',
                            title: data && data.ya_reparado
                                ? 'Ya reparado'
                                : (data && data.stock_insuficiente ? 'Stock insuficiente' : 'Error'),
                            text: msg,
                        });
                    } else {
                        alert(msg);
                    }
                    btn.disabled = false;
                    btn.innerHTML = htmlOriginal;
                    return;
                }

                // Éxito: cerrar modal y mostrar delta de stock.
                const modalEl = document.getElementById(REPAR_MODAL_ID);
                bootstrap.Modal.getInstance(modalEl).hide();

                const deltas = data.stock_delta || [];
                let stockHtml = '';
                if (deltas.length) {
                    const filas = deltas.map(d => `
                        <tr>
                            <td><small class="font-monospace">${d.sku}</small></td>
                            <td><small>${d.talla || '-'}</small></td>
                            <td><small>${d.sucursal_alias || d.sucursal_id}</small></td>
                            <td class="text-end"><strong>${d.stock}</strong></td>
                        </tr>`).join('');
                    stockHtml = `
                        <p class="small mb-1"><strong>Stock actual tras reparación:</strong></p>
                        <table class="table table-sm table-bordered mb-0">
                            <thead class="table-light"><tr>
                                <th>SKU</th><th>Talla</th><th>Sucursal</th><th class="text-end">Stock</th>
                            </tr></thead>
                            <tbody>${filas}</tbody>
                        </table>`;
                }

                if (window.Swal) {
                    Swal.fire({
                        icon: 'success',
                        title: 'Reparación aplicada',
                        html: `
                            <p>${data.message || 'OK'}</p>
                            ${stockHtml}`,
                        width: deltas.length ? '620px' : undefined,
                    }).then(() => {
                        // Recargar trazabilidad si está abierta para refrescar flags.
                        if (window.__trazDteActual) {
                            abrir(window.__trazDteActual);
                        }
                        // Evento global para que otras vistas se refresquen.
                        document.dispatchEvent(new CustomEvent('nc-stock-reparada', {
                            detail: { nc_id: data.nc_id, stock_delta: deltas },
                        }));
                    });
                } else {
                    alert(data.message || 'Reparación aplicada.');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerHTML = htmlOriginal;
                if (window.Swal) Swal.fire('Error', err && err.message, 'error');
            });
    }

    // Delegación global del botón "Reparar" dentro del modal de trazabilidad.
    document.addEventListener('click', function (ev) {
        const btn = ev.target.closest('.btn-reparar-nc-stock');
        if (!btn) return;
        ev.preventDefault();
        const ncId = parseInt(btn.dataset.ncId, 10);
        if (ncId) abrirReparacion(ncId);
    });

    window.TrazabilidadDTE = { abrir, abrirReparacion };
})();
