/**
 * carga_factura.js — agente "Cargar desde factura" de Gestión de Productos.
 *
 * Chat con el agente: se adjunta la factura del proveedor (PDF), el servidor
 * la lee con Claude en segundo plano (se consulta /app/carga-factura/<id>/
 * cada pocos segundos), después se pide la vista previa
 * (/planificar/), que se muestra como una tarjeta editable por factura, y
 * al confirmar se carga (/cargar/) por el mismo camino que Crear Producto
 * Manual. Nada se escribe hasta apretar «Cargar».
 *
 * Requiere jQuery, Bootstrap 5 y SweetAlert2 (ya cargados por la página).
 */
(function () {
    'use strict';

    const BASE = '/app/carga-factura/';
    const INTERVALO_MS = 3000;
    // La sesión vive en el servidor (PDF, lectura, chat y correcciones); esto
    // solo recuerda cuál estaba abierta para retomarla si recargan la página.
    const CLAVE_SESION = 'cargaFactura.sesion';
    const TEXTO_OPCION = {
        s: 'stock + costo + venta',
        c: 'stock + costo (la venta sigue)',
        t: 'solo stock',
        n: 'saltar esta línea',
    };

    const st = {
        sesion: null,        // id de la sesión abierta
        resumen: null,       // último resumen del servidor
        opciones: null,      // catálogos para los editores
        timer: null,
        mensajesVistos: 0,   // cuántos mensajes de la sesión ya están en pantalla
        estadoPrevio: null,
        previas: {},         // idx → último item de vista previa
        enviando: false,     // subiendo un PDF
        hablando: false,     // esperando la respuesta del chat
        cronometro: null,    // intervalo del reloj de la burbuja de progreso
        progresoDesde: null,
    };

    // ------------------------------------------------------------ utilidades

    function recordarSesion(id) {
        try {
            if (id) localStorage.setItem(CLAVE_SESION, String(id));
            else localStorage.removeItem(CLAVE_SESION);
        } catch (e) { /* modo privado o storage bloqueado: sin memoria, nada más */ }
    }

    function sesionRecordada() {
        try {
            const v = parseInt(localStorage.getItem(CLAVE_SESION) || '', 10);
            return v > 0 ? v : null;
        } catch (e) { return null; }
    }

    function cookie(name) {
        const m = document.cookie.match('(?:^|; )' + name + '=([^;]*)');
        return m ? decodeURIComponent(m[1]) : null;
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function fmt(n) {
        if (n === null || n === undefined || n === '') return '—';
        return Number(n).toLocaleString('es-CL');
    }

    function hora(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d)) return '';
        return d.toLocaleString('es-CL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    }

    async function api(path, opciones) {
        const opts = Object.assign({ headers: {} }, opciones || {});
        opts.headers['X-Requested-With'] = 'XMLHttpRequest';
        if (opts.method && opts.method !== 'GET') opts.headers['X-CSRFToken'] = cookie('csrftoken');
        if (opts.json !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(opts.json);
            delete opts.json;
        }
        const resp = await fetch(BASE + path, opts);
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok || !data || data.success === false) {
            const msg = (data && (data.error || data.mensaje)) || ('Error ' + resp.status);
            throw new Error(msg);
        }
        return data;
    }

    function avisar(titulo, texto, icono) {
        if (window.Swal) Swal.fire(titulo, texto, icono || 'error');
        else alert(titulo + ': ' + texto);
    }

    // ---------------------------------------------------------------- chat

    const $chat = () => document.getElementById('cfChat');

    function bajar() {
        const body = document.querySelector('#modalCargaFactura .modal-body');
        if (body) body.scrollTop = body.scrollHeight;
    }

    function kb(bytes) {
        if (bytes === null || bytes === undefined || bytes === '') return '';
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }

    function burbuja(quien, html, extra) {
        const div = document.createElement('div');
        div.className = 'cf-msg ' + (quien === 'usuario' ? 'cf-usuario' : 'cf-agente') + (extra ? ' ' + extra : '');
        div.innerHTML =
            '<div class="cf-avatar"><i class="bi ' + (quien === 'usuario' ? 'bi-person' : 'bi-robot') + '"></i></div>' +
            '<div class="flex-grow-1 min-w-0"><div class="cf-burbuja">' + html + '</div><div class="cf-hora"></div></div>';
        $chat().appendChild(div);
        bajar();
        return div;
    }

    /** Hito del proceso (factura enviada, lectura terminada, carga…): fila centrada con icono. */
    function evento(icono, titulo, html, extra) {
        const div = document.createElement('div');
        div.className = 'cf-evento' + (extra ? ' ' + extra : '');
        div.innerHTML =
            '<div class="cf-evento-icono"><i class="bi ' + icono + '"></i></div>' +
            '<div class="cf-evento-cuerpo"><div class="cf-evento-titulo">' + titulo + '</div>' +
            (html ? '<div class="cf-evento-texto">' + html + '</div>' : '') +
            '<div class="cf-hora"></div></div>';
        $chat().appendChild(div);
        bajar();
        return div;
    }

    /** Tarjeta «Factura enviada a leer»: exactamente lo que se mandó. */
    function htmlEnvio(e) {
        const filas = [
            ['bi-file-earmark-pdf', 'Archivo', esc(e.archivo) + (e.bytes ? ' <small>(' + kb(e.bytes) + ')</small>' : '')],
            ['bi-shop', 'Bodega', esc(e.bodega)],
            ['bi-tag', 'Marca', e.marca ? esc(e.marca) : '<span class="cf-muted">la detecta el lector</span>'],
            ['bi-arrow-repeat', 'Lecturas', esc(e.lecturas) + (Number(e.lecturas) > 1 ? ' (se comparan entre sí)' : ' (rápida)')],
        ];
        if (e.indicaciones) filas.push(['bi-chat-left-text', 'Indicaciones', esc(e.indicaciones)]);
        return '<div class="cf-envio"><div class="cf-envio-titulo"><i class="bi bi-cloud-arrow-up me-1"></i>Factura enviada a leer</div>' +
            filas.map(f => '<div class="cf-envio-fila"><i class="bi ' + f[0] + '"></i><span>' + f[1] + '</span><b>' + f[2] + '</b></div>').join('') +
            '</div>';
    }

    /** Debajo de una respuesta del agente: qué aplicó, qué no existe y qué falló. */
    function htmlCambios(m) {
        let h = '';
        if (m.cambios && m.cambios.length) {
            h += '<div class="cf-aplicado"><div class="cf-aplicado-titulo"><i class="bi bi-check2-circle me-1"></i>Apliqué en la vista previa</div>' +
                m.cambios.map(c => '<span class="cf-chip nueva">' + esc(c) + '</span>').join(' ') + '</div>';
        }
        if (m.rechazos && m.rechazos.length) {
            h += '<div class="cf-aplicado cf-aplicado-no"><div class="cf-aplicado-titulo"><i class="bi bi-exclamation-triangle me-1"></i>No apliqué: no existe en el sistema</div>' +
                m.rechazos.map(c => '<span class="cf-chip no">' + esc(c) + '</span>').join(' ') +
                '<div class="cf-muted mt-1">Se crea en Gestión de Productos y me lo vuelves a pedir.</div></div>';
        }
        if (m.fallo) {
            h += '<div class="cf-aplicado cf-aplicado-no"><div class="cf-aplicado-titulo"><i class="bi bi-x-circle me-1"></i>No pude aplicar el cambio</div>' + esc(m.fallo) + '</div>';
        }
        return h;
    }

    function mensaje(m) {
        const texto = esc(m.texto);
        let div;
        switch (m.tipo) {
            case 'subida':
                div = burbuja('usuario', m.envio ? htmlEnvio(m.envio) : texto, 'cf-envio-msg');
                break;
            case 'indicaciones':
                div = burbuja('usuario', '<div class="cf-etiqueta"><i class="bi bi-chat-left-text me-1"></i>Indicaciones para la lectura</div>' + texto);
                break;
            case 'lectura':
                div = evento('bi-file-earmark-check', 'Lectura terminada', texto, 'cf-evento-ok');
                break;
            case 'carga':
                div = m.quien === 'usuario'
                    ? evento('bi-box-arrow-in-down', 'Orden de carga', texto)
                    : evento('bi-check-circle', 'Carga terminada', texto, 'cf-evento-ok');
                break;
            case 'error':
                div = evento('bi-exclamation-octagon', 'Problema', texto, 'cf-evento-error');
                break;
            default:
                div = burbuja(m.quien, texto + (m.quien === 'agente' ? htmlCambios(m) : ''));
        }
        const h = div.querySelector('.cf-hora');
        if (h) h.textContent = hora(m.fecha);
        return div;
    }

    function limpiarChat() {
        setTyping('');
        $chat().innerHTML = '';
        st.mensajesVistos = 0;
        st.previas = {};
        st.estadoPrevio = null;
    }

    function bienvenida() {
        burbuja('agente',
            'Hola. Así funciona:' +
            '<ol class="cf-pasos">' +
            '<li><b>Adjunta el PDF</b> de la factura del proveedor (escaneado o el del SII) y elige la <b>bodega</b>. ' +
            'Si quieres, escríbeme indicaciones (marca, tipo de talla, dónde vienen las tallas).</li>' +
            '<li>Aprieta <b>Enviar PDF</b>: lo leo en el servidor (puedes cerrar esta ventana) y te muestro la ' +
            '<b>vista previa</b>: códigos nuevos, los que ya existen y en qué ficha entran, tallas y precios.</li>' +
            '<li>Corrige en la tabla o <b>escríbeme</b> («la marca es CHALADA», «línea 3 es de mujer, color plata») ' +
            'y aprieta <b>Cargar</b>. Nada se escribe hasta ese clic.</li>' +
            '</ol>' +
            '<div class="cf-muted">Cada carga queda guardada: si recargas la página la retomo, y me puedes preguntar ' +
            'por facturas ya leídas («¿a cuánto compré el 126511-02?») sin volver a subirlas.</div>');
    }

    /** Estado en curso (subiendo, leyendo, pensando, cargando…): una sola burbuja
     *  con spinner y reloj, siempre al final del chat. setTyping('') la quita. */
    function setTyping(texto, detalle) {
        let t = document.getElementById('cfTyping');
        if (!texto) {
            if (t) t.remove();
            if (st.cronometro) { clearInterval(st.cronometro); st.cronometro = null; }
            st.progresoDesde = null;
            return;
        }
        if (!t) {
            t = burbuja('agente', '', 'cf-typing');
            t.id = 'cfTyping';
            t.querySelector('.cf-hora').remove();
            st.progresoDesde = Date.now();
        }
        t.querySelector('.cf-burbuja').innerHTML =
            '<div class="cf-progreso"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>' +
            '<div class="flex-grow-1"><div class="cf-progreso-texto">' + esc(texto) + '</div>' +
            (detalle ? '<div class="cf-muted">' + esc(detalle) + '</div>' : '') + '</div>' +
            '<span class="cf-crono" id="cfCrono">0:00</span></div>';
        if (!st.cronometro) {
            st.cronometro = setInterval(function () {
                const el = document.getElementById('cfCrono');
                if (!el || !st.progresoDesde) return;
                const seg = Math.floor((Date.now() - st.progresoDesde) / 1000);
                el.textContent = Math.floor(seg / 60) + ':' + String(seg % 60).padStart(2, '0');
            }, 1000);
        }
        $chat().appendChild(t);
        bajar();
    }

    // ------------------------------------------------------ estado y compositor

    const ESTADOS = {
        LEYENDO: ['Leyendo el PDF…', 'cf-pill-run'],
        CARGANDO: ['Cargando productos…', 'cf-pill-run'],
        LEIDA: ['Vista previa lista', 'cf-pill-ok'],
        CERRADA: ['Cerrada', 'cf-pill-muted'],
        ERROR: ['Error de lectura', 'cf-pill-error'],
    };

    /** Píldora del encabezado: en qué está la carga abierta. */
    function pintarEstado() {
        const pill = document.getElementById('cfEstadoPill');
        if (!pill) return;
        let texto = 'Sin carga abierta', clase = 'cf-pill-muted';
        if (st.enviando) {
            texto = 'Subiendo el PDF…'; clase = 'cf-pill-run';
        } else if (st.sesion && st.resumen) {
            const e = ESTADOS[st.resumen.estado] || [st.resumen.estado, 'cf-pill-muted'];
            texto = 'Carga #' + st.sesion + ' · ' + e[0]; clase = e[1];
        } else if (st.sesion) {
            texto = 'Carga #' + st.sesion;
        }
        pill.className = 'cf-pill ' + clase;
        pill.innerHTML = (clase === 'cf-pill-run' ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>' : '') + esc(texto);
    }

    function ocupado() {
        return st.enviando || st.hablando ||
            !!(st.resumen && (st.resumen.estado === 'LEYENDO' || st.resumen.estado === 'CARGANDO'));
    }

    /** Habilita lo que corresponde y explica, debajo de la caja, qué pasa al apretar. */
    function actualizarComposer() {
        const $texto = document.getElementById('cfTexto');
        const $hablar = document.getElementById('cfBtnHablar');
        const $enviar = document.getElementById('cfBtnEnviar');
        const $ayuda = document.getElementById('cfAyuda');
        const $suc = document.getElementById('cfSucursal');
        if (!$texto || !$hablar || !$enviar) return;
        const archivo = document.getElementById('cfArchivo').files[0];
        const bodega = $suc && $suc.options[$suc.selectedIndex] ? $suc.options[$suc.selectedIndex].text : '';
        const configurada = !!(st.opciones && st.opciones.configurada);
        const conPrevia = !!(st.sesion && st.resumen && st.resumen.estado === 'LEIDA');
        const bloqueado = ocupado();
        let ayuda;
        if (bloqueado) {
            $texto.placeholder = st.enviando ? 'Subiendo el PDF…'
                : st.hablando ? 'Pensando…'
                : (st.resumen && st.resumen.estado === 'CARGANDO') ? 'Cargando… espera a que termine'
                : 'Leyendo el PDF… espera a que termine';
            ayuda = st.hablando ? 'Estoy ajustando la vista previa con lo que escribiste.'
                : 'Sigo trabajando en el servidor. Puedes cerrar esta ventana y volver después: la carga queda guardada.';
        } else if (conPrevia) {
            $texto.placeholder = 'Corrige o pregunta: «la marca es CHALADA», «línea 3 es de mujer, color plata», «¿a cuánto compré el 126511-02 antes?»';
            ayuda = archivo
                ? '«Enviar PDF» empieza OTRA carga con ' + archivo.name + '; la actual queda en «Cargas recientes».'
                : 'Lo que escribas se aplica a la vista previa de arriba (Enter envía). Para otra factura, adjunta su PDF.';
        } else {
            $texto.placeholder = 'Indicaciones para leer la factura (opcional): «marca CHALADA, tallas CL, el color va en la descripción»';
            if (st.opciones && !configurada) {
                ayuda = 'La lectura no está configurada en este servidor (falta la clave de Anthropic).';
            } else if (archivo) {
                ayuda = 'Listo: «Enviar PDF» sube ' + archivo.name + ' (' + kb(archivo.size) + ') a la bodega ' + bodega +
                    ($texto.value.trim() ? ' con tus indicaciones' : '') + ' y empieza la lectura.';
            } else {
                ayuda = 'Adjunta el PDF de la factura para empezar.';
            }
        }
        $texto.disabled = bloqueado;
        $hablar.disabled = bloqueado || !conPrevia;
        $hablar.title = conPrevia ? 'Enviar mensaje (Enter)' : 'Primero envía una factura: cuando tenga la vista previa me puedes escribir';
        $hablar.innerHTML = st.hablando ? '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>' : '<i class="bi bi-send"></i>';
        $enviar.disabled = !archivo || !configurada || bloqueado;
        $enviar.innerHTML = st.enviando
            ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Subiendo…'
            : '<i class="bi bi-cloud-arrow-up me-1"></i>Enviar PDF';
        if ($ayuda) $ayuda.textContent = ayuda;
    }

    function autoAlto(el) {
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }

    function mostrarArchivo() {
        const f = document.getElementById('cfArchivo').files[0];
        document.getElementById('cfNombreArchivo').textContent = f ? f.name : '';
        document.getElementById('cfTamanoArchivo').textContent = f ? kb(f.size) : '';
        document.getElementById('cfArchivoChip').classList.toggle('d-none', !f);
        document.getElementById('cfBtnAdjuntar').classList.toggle('d-none', !!f);
        actualizarComposer();
    }

    function quitarArchivo() {
        document.getElementById('cfArchivo').value = '';
        mostrarArchivo();
    }

    // ------------------------------------------------------------- sesiones

    async function cargarOpciones() {
        if (st.opciones) return st.opciones;
        const data = await api('opciones/');
        st.opciones = data;
        actualizarComposer();
        const $suc = document.getElementById('cfSucursal');
        $suc.innerHTML = data.sucursales.map(s => '<option value="' + s.id + '">' + esc(s.alias) + '</option>').join('');
        const actual = (window.SUCURSAL_ACTUAL_ID || '').toString();
        if (actual && data.sucursales.some(s => String(s.id) === actual)) $suc.value = actual;
        document.getElementById('cfMarcas').innerHTML = data.marcas.map(m => '<option value="' + esc(m) + '">').join('');
        if (!data.configurada) {
            burbuja('agente', esc('En este servidor falta configurar ANTHROPIC_API_KEY: puedo mostrar cargas ' +
                'anteriores, pero no leer facturas nuevas hasta que se configure.'), 'cf-error');
            document.getElementById('cfBtnEnviar').disabled = true;
        }
        return data;
    }

    async function cargarRecientes() {
        try {
            const data = await api('lista/');
            const $sel = document.getElementById('cfRecientes');
            $sel.innerHTML = '<option value="">Cargas recientes…</option>' + data.sesiones.map(s =>
                '<option value="' + s.id + '"' + (st.sesion === s.id ? ' selected' : '') + '>#' + s.id + ' · ' +
                esc(s.nombre_archivo) + ' · ' + esc(s.sucursal) + ' · ' + esc(s.estado.toLowerCase()) + ' · ' +
                esc(hora(s.creado_en)) + '</option>').join('');
        } catch (e) { /* la lista es informativa */ }
    }

    function detenerSondeo() {
        if (st.timer) { clearTimeout(st.timer); st.timer = null; }
    }

    function nuevaConversacion() {
        detenerSondeo();
        st.sesion = null;
        st.resumen = null;
        recordarSesion(null);
        limpiarChat();
        bienvenida();
        document.getElementById('cfRecientes').value = '';
        quitarArchivo();
        pintarEstado();
        actualizarComposer();
    }

    async function abrirSesion(id) {
        detenerSondeo();
        st.sesion = id;
        recordarSesion(id);
        limpiarChat();
        await refrescar(true);
    }

    /** Tras recargar la página: retoma la última sesión abierta (si sigue existiendo). */
    async function retomarSesion() {
        const id = sesionRecordada();
        if (!id || st.sesion) return;
        try {
            const data = await api(id + '/');
            if (data.sesion.estado === 'CERRADA') { recordarSesion(null); return; }
        } catch (e) {
            recordarSesion(null);   // la sesión ya no existe o no es de sus bodegas
            return;
        }
        burbuja('agente', esc('Retomé la carga #' + id + ' que tenías abierta. Para empezar otra, «Nueva».'));
        await abrirSesion(id);
    }

    async function enviar() {
        if (st.enviando) return;
        const archivo = document.getElementById('cfArchivo').files[0];
        if (!archivo) { avisar('Falta la factura', 'Adjunta el PDF de la factura.', 'warning'); return; }
        const $suc = document.getElementById('cfSucursal');
        const $texto = document.getElementById('cfTexto');
        const envio = {
            archivo: archivo.name, bytes: archivo.size,
            bodega: $suc.options[$suc.selectedIndex] ? $suc.options[$suc.selectedIndex].text : '',
            marca: document.getElementById('cfMarca').value.trim().toUpperCase(),
            lecturas: parseInt(document.getElementById('cfLecturas').value, 10) || 2,
            // Lo escrito en la caja del chat va como indicaciones para el lector.
            indicaciones: $texto.value.trim(),
        };
        const fd = new FormData();
        fd.append('archivo', archivo);
        fd.append('sucursal', $suc.value);
        fd.append('marca', envio.marca);
        fd.append('lecturas', envio.lecturas);
        fd.append('indicaciones', envio.indicaciones);
        st.enviando = true;
        pintarEstado();
        actualizarComposer();
        // Lo que se está enviando queda a la vista desde el primer segundo.
        const local = burbuja('usuario', htmlEnvio(envio), 'cf-envio-msg');
        setTyping('Subiendo el PDF…', kb(archivo.size) + ' · en cuanto llegue empiezo a leerlo');
        try {
            const data = await api('subir/', { method: 'POST', body: fd });
            detenerSondeo();
            st.sesion = data.id;
            st.resumen = data.sesion || null;
            recordarSesion(data.id);
            limpiarChat();   // se repinta desde el servidor (incluye esta tarjeta de envío)
            quitarArchivo();
            $texto.value = '';
            autoAlto($texto);
            cargarRecientes();
            await refrescar(true);
        } catch (e) {
            setTyping('');
            local.classList.add('cf-error');
            local.querySelector('.cf-burbuja').insertAdjacentHTML('beforeend',
                '<div class="cf-nota error mt-2"><i class="bi bi-x-circle me-1"></i>No se pudo enviar: ' + esc(e.message) + '</div>');
            avisar('No se pudo enviar', e.message);
        } finally {
            st.enviando = false;
            pintarEstado();
            actualizarComposer();
        }
    }

    /** Un mensaje al agente sobre la vista previa (marca, tallas, precios, dudas…). */
    async function hablar() {
        const $texto = document.getElementById('cfTexto');
        const texto = $texto.value.trim();
        if (!texto) return;
        if (!st.sesion) {
            avisar('Agente', 'Primero adjunta la factura y aprieta «Enviar PDF». Lo que escribas aquí ' +
                'antes de enviarla me sirve como indicación para leerla (marca, tipo de talla…).', 'info');
            return;
        }
        if (!st.resumen || st.resumen.estado !== 'LEIDA') {
            avisar('Agente', 'Espera a que termine la lectura o la carga para conversar.', 'info');
            return;
        }
        const id = st.sesion;
        st.hablando = true;
        actualizarComposer();
        // Tu mensaje aparece al tiro; el servidor lo devuelve después con su hora.
        const local = burbuja('usuario', esc(texto));
        $texto.value = '';
        autoAlto($texto);
        setTyping('Leyendo tu mensaje y ajustando la vista previa…');
        try {
            const data = await api(id + '/conversar/', { method: 'POST', json: { texto: texto } });
            if (st.sesion !== id) return;
            local.remove();
            await refrescar(false);   // pinta tu mensaje y la respuesta
            (data.facturas || []).forEach(item => { st.previas[item.idx] = item; pintarPrevia(item); });
        } catch (e) {
            setTyping('');
            local.remove();
            if (st.sesion === id) await refrescar(false);   // si el servidor alcanzó a guardar tu mensaje
            $texto.value = texto;   // que no se pierda lo escrito
            autoAlto($texto);
            avisar('Agente', e.message);
        } finally {
            st.hablando = false;
            actualizarComposer();
            $texto.focus();
        }
    }

    /** Estado de la sesión: mensajes nuevos, progreso y, cuando pasa a vista previa, la planificación. */
    async function refrescar(inicial) {
        if (!st.sesion) return;
        const id = st.sesion;
        let data;
        try {
            data = await api(id + '/');
        } catch (e) {
            setTyping('');
            burbuja('agente', esc('No pude consultar la sesión: ' + e.message), 'cf-error');
            return;
        }
        if (st.sesion !== id) return;   // cambiaron de sesión mientras tanto
        st.resumen = data.sesion;
        pintarEstado();
        actualizarComposer();
        const mensajes = data.mensajes || [];
        setTyping('');
        for (let i = st.mensajesVistos; i < mensajes.length; i++) mensaje(mensajes[i]);
        st.mensajesVistos = mensajes.length;

        const estado = data.sesion.estado;
        if (estado === 'LEYENDO' || estado === 'CARGANDO') {
            setTyping(data.sesion.progreso || (estado === 'LEYENDO' ? 'Leyendo el PDF…' : 'Cargando…'),
                estado === 'LEYENDO'
                    ? 'Puedes cerrar esta ventana: sigo en el servidor y aquí queda todo guardado.'
                    : 'Escribiendo stock, lotes, DTE y compra…');
        }
        const terminoAlgo = st.estadoPrevio && st.estadoPrevio !== estado && estado === 'LEIDA';
        if ((inicial || terminoAlgo) && (estado === 'LEIDA' || estado === 'CERRADA' || estado === 'CARGANDO')) {
            await planificar(null, null);
        }
        if (terminoAlgo && st.estadoPrevio === 'CARGANDO') {
            // Los productos recién cargados aparecen en «Actividad reciente».
            const $r = document.getElementById('btnRefrescarActividad');
            if ($r) $r.click();
        }
        st.estadoPrevio = estado;
        if (estado === 'LEYENDO' || estado === 'CARGANDO') {
            st.timer = setTimeout(() => refrescar(false), INTERVALO_MS);
        }
    }

    // ---------------------------------------------------------- vista previa

    async function planificar(cambios, idx) {
        if (!st.sesion) return;
        const id = st.sesion;
        const cuerpo = {};
        if (cambios) cuerpo.facturas = cambios;
        if (idx !== null && idx !== undefined) cuerpo.idx = idx;
        setTyping(cambios ? 'Recalculando con tus correcciones…' : 'Armando la vista previa…');
        let data;
        try {
            data = await api(id + '/planificar/', { method: 'POST', json: cuerpo });
        } catch (e) {
            setTyping('');
            avisar('Vista previa', e.message);
            return;
        }
        if (st.sesion !== id) return;
        setTyping('');
        data.facturas.forEach(item => {
            st.previas[item.idx] = item;
            pintarPrevia(item);
        });
        if (st.resumen && (st.resumen.estado === 'LEYENDO' || st.resumen.estado === 'CARGANDO')) {
            setTyping(st.resumen.progreso || '…');
        }
    }

    function tarjeta(idx) {
        return document.querySelector('.cf-card[data-idx="' + idx + '"]');
    }

    function pintarPrevia(item) {
        let card = tarjeta(item.idx);
        if (!card) {
            card = burbuja('agente', '', 'cf-card');
            card.dataset.idx = item.idx;
            card.querySelector('.cf-hora').remove();
        }
        card.querySelector('.cf-burbuja').innerHTML = htmlPrevia(item);
        $chat().appendChild(card);   // la vista previa vigente siempre al final del chat
        bajar();
    }

    function estadoBadge(estado) {
        return '<span class="cf-estado ' + esc(estado) + '">' + esc(estado.replace('_', ' ')) + '</span>';
    }

    function opcionesSelect(lista, actual, vacio) {
        let html = vacio !== undefined ? '<option value="">' + esc(vacio) + '</option>' : '';
        const valorActual = actual == null ? '' : String(actual);
        let visto = false;
        lista.forEach(v => {
            const sel = String(v).toUpperCase() === valorActual.toUpperCase();
            if (sel) visto = true;
            html += '<option value="' + esc(v) + '"' + (sel ? ' selected' : '') + '>' + esc(v) + '</option>';
        });
        if (valorActual && !visto) html += '<option value="' + esc(valorActual) + '" selected>' + esc(valorActual) + ' (no existe)</option>';
        return html;
    }

    function htmlPrevia(item) {
        const bloqueada = item.estado === 'CARGADA' || item.estado === 'CARGANDO';
        const dis = bloqueada ? ' disabled' : '';
        const suc = item.sucursal || '';
        let h = '<div class="cf-card-head">' +
            '<div><strong>FACTURA N° ' + esc(item.folio) + '</strong> · ' + esc(item.proveedor || item.proveedor_rut) +
            (item.proveedor ? ' <span class="opacity-75">(' + esc(item.proveedor_rut) + ')</span>' : '') +
            ' · ' + esc(item.fecha_emision || '') + '</div>' +
            '<div>Bodega <strong>' + esc(suc) + '</strong></div>' +
            '<div>Marca <input class="form-control" data-campo="marca" value="' + esc(item.marca || '') + '" list="cfMarcas" style="width:120px"' + dis + '></div>' +
            '<div>Color por defecto <input class="form-control" data-campo="color" value="' + esc(item.color || '') + '" style="width:100px"' + dis + '></div>' +
            '<div title="Tipo de talla y guías por género (se cambian por el chat)">Tallas <strong>' + esc(item.tipo_talla || 'CL') + '</strong>' +
            (item.guias_talla && Object.keys(item.guias_talla).length ? ' · guías: ' + esc(Object.entries(item.guias_talla).map(([g, n]) => g + '→' + n).join(', ')) : ' · sin guías') + '</div>' +
            (item.descuento_global ? '<div>Descuento global <strong>' + esc(item.descuento_global) + '</strong> (ya en los costos)</div>' : '') +
            '<div class="ms-auto">' + estadoBadge(item.estado) + '</div>' +
            '</div><div class="cf-card-body">';

        if (item.error) {
            h += '<p class="cf-nota error mb-2"><i class="bi bi-exclamation-triangle me-1"></i>' + esc(item.error) + '</p>';
            h += '<div class="d-flex flex-wrap gap-2 align-items-center mb-2">' +
                '<span class="small">Folio <input class="form-control form-control-sm d-inline-block" style="width:110px" data-campo="folio" value="' + esc(item.folio || '') + '"' + dis + '></span>' +
                '<span class="small">RUT proveedor <input class="form-control form-control-sm d-inline-block" style="width:130px" data-campo="proveedor_rut" value="' + esc(item.proveedor_rut || '') + '"' + dis + '></span>' +
                '<span class="small">DTE <select class="form-select form-select-sm d-inline-block w-auto" data-campo="dte_id"' + dis + '>' +
                '<option value="">buscar por folio + RUT</option>' +
                (item.candidatos_dte || []).map(d => '<option value="' + d.id + '"' + (String(item.dte_id) === String(d.id) ? ' selected' : '') + '>id ' + d.id + ' · ' + esc(d.tipo) + ' ' + esc(d.numero) + ' · ' + esc(d.transaccion) + ' · ' + esc(d.emisor) + ' · ' + esc(d.fecha) + ' · neto $' + fmt(d.neto) + '</option>').join('') +
                '</select></span></div>';
            if (item.revisar && item.revisar.length) h += item.revisar.map(r => '<p class="cf-nota aviso">! ' + esc(r) + '</p>').join('');
        } else {
            const d = item.dte, t = item.totales;
            h += '<div class="cf-resumen">' +
                '<span><i class="bi bi-receipt me-1"></i>DTE id ' + d.id + ' · ' + esc(d.tipo) + ' ' + esc(d.numero) + ' · ' + esc(d.transaccion) + ' · neto $' + fmt(d.neto) + (d.descartado ? ' · <b class="text-danger">DESCARTADO</b>' : '') + '</span>' +
                '<span><i class="bi bi-boxes me-1"></i>' + fmt(t.unidades) + ' unidades' + (t.unidades_factura ? ' (factura: ' + fmt(t.unidades_factura) + ')' : '') + '</span>' +
                '<span><i class="bi bi-cash me-1"></i>neto líneas $' + fmt(t.neto) + (t.neto_factura ? ' (factura: $' + fmt(t.neto_factura) + ')' : '') + '</span>' +
                '<span>' + Object.keys(t.por_estado).sort().map(k => estadoBadge(k) + ' ' + t.por_estado[k]).join(' ') + '</span>' +
                (item.regla ? '<span class="cf-muted">venta = costo × ' + esc(item.regla.factor_bajo) + ' (&lt; $' + fmt(item.regla.umbral) + ') / × ' + esc(item.regla.factor_alto) + ' → …990 · sobreprecio nuevos ' + esc(item.regla.margen_sobreprecio) + '%</span>' : '') +
                '</div>';
            (t.avisos || []).forEach(a => { h += '<p class="cf-nota aviso">! ' + esc(a) + '</p>'; });
            (item.revisar || []).forEach(r => { h += '<p class="cf-nota aviso">! lectura: ' + esc(r) + '</p>'; });
            h += '<div class="table-responsive mt-2"><table class="table table-sm table-hover cf-tabla"><thead><tr>' +
                '<th>#</th><th>Estado</th><th>Artículo</th><th>Descripción</th><th>Tallas</th><th class="text-end">Uds</th>' +
                '<th>Costo</th><th>Venta</th><th>Género · Categoría · Color</th><th>Decisión</th></tr></thead><tbody>';
            item.planes.forEach(p => { h += htmlFila(item, p, bloqueada); });
            h += '</tbody></table></div>';
        }
        if (item.resultado) h += htmlResultado(item);
        h += '</div>';

        // Pie: opciones de la factura + acciones
        h += '<div class="cf-card-foot">';
        h += '<label class="small d-flex align-items-center gap-1 mb-0"><input type="checkbox" class="form-check-input mt-0" data-campo="_renombrar_tallas"' + (item.renombrar_tallas ? ' checked' : '') + dis + '> pasar las tallas de fichas existentes al formato de la guía (US con C/Y)</label>';
        if (item.fuente) h += '<span class="cf-muted">' + esc(item.fuente) + '</span>';
        h += '<span class="ms-auto d-flex gap-2">';
        if (!bloqueada) {
            h += '<button type="button" class="btn btn-outline-primary btn-sm" data-accion="recalcular"><i class="bi bi-arrow-repeat me-1"></i>Volver a calcular</button>';
            const puede = !item.error && item.totales && !item.totales.bloqueantes && item.totales.a_cargar > 0 && st.resumen && st.resumen.estado === 'LEIDA';
            const txt = item.error ? 'Cargar' : 'Cargar ' + (item.totales ? item.totales.a_cargar : 0) + ' línea(s) en ' + esc(suc);
            h += '<button type="button" class="btn btn-success btn-sm" data-accion="cargar"' + (puede ? '' : ' disabled') + '><i class="bi bi-cloud-upload me-1"></i>' + txt + '</button>';
        } else if (item.estado === 'CARGANDO') {
            h += '<span class="cf-nota aviso"><i class="bi bi-hourglass-split me-1"></i>cargando…</span>';
        } else {
            h += '<span class="cf-nota ok"><i class="bi bi-check-circle me-1"></i>factura cargada</span>';
        }
        h += '</span></div>';
        return h;
    }

    function htmlFila(item, p, bloqueada) {
        const o = st.opciones || { generos: [], categorias: [], colores: [], especialidades: [] };
        const j = p.json || {};
        const dis = bloqueada ? ' disabled' : '';
        const n = p.n - 1;   // posición en el JSON (las líneas se emparejan por posición)
        const estado = p.omitida ? 'OMITIDA' : p.estado;
        let h = '<tr data-n="' + n + '" data-articulo="' + esc(p.articulo) + '" class="' + (p.omitida ? 'cf-omitida' : '') + '">';
        h += '<td>' + p.n + '</td>';
        h += '<td>' + estadoBadge(estado) + '<br><label class="cf-muted d-flex align-items-center gap-1 mt-1"><input type="checkbox" class="form-check-input mt-0" data-campo="_omitir"' + (p.omitida ? ' checked' : '') + dis + '>omitir</label></td>';
        // Artículo + ficha
        h += '<td><input class="form-control" data-campo="articulo" value="' + esc(j.articulo || p.articulo) + '"' + dis + '>';
        if (p.destino) {
            h += '<div class="cf-muted mt-1">entra en ficha #' + p.destino.id + ' ' + esc(p.destino.sucursal) + ' «' + esc(p.destino.descripcion) + '»<br>' +
                esc([p.destino.marca, p.destino.color, p.destino.genero, p.destino.categoria].filter(Boolean).join(' / ')) +
                ' · ' + p.destino.tallas + ' tallas · stock ' + fmt(p.destino.stock) + '</div>';
        } else if (p.referencia) {
            h += '<div class="cf-muted mt-1">existe en ' + esc(p.referencia.sucursal) + ' (#' + p.referencia.id + ') como ' +
                esc([p.referencia.marca, p.referencia.color, p.referencia.genero, p.referencia.categoria].filter(Boolean).join(' / ')) + ': se crea igual</div>';
        }
        h += '</td>';
        h += '<td><input class="form-control" style="min-width:170px" data-campo="descripcion" value="' + esc(j.descripcion || p.descripcion) + '"' + dis + '></td>';
        // Tallas
        h += '<td><div>' + p.tallas.map(t => '<span class="cf-chip' + (t.existe ? '' : ' nueva') + '" title="factura: ' + esc(t.factura) + (t.existe ? ' (suma en la ficha)' : ' (nueva)') + '">' + esc(t.ficha) + '×' + t.cantidad + '</span>').join('') + '</div>' +
            '<div class="cf-muted">' + esc(p.tipo_talla || '') + (p.guia ? ' · guía «' + esc(p.guia.nombre) + '»' : ' · sin guía') +
            (bloqueada ? '' : ' · <a href="#" data-accion="editar-tallas">editar</a>') + '</div>' +
            '<textarea class="form-control d-none mt-1" rows="4" data-campo="tallas" placeholder="talla cantidad" ' + dis + '>' +
            esc(Object.entries(j.tallas || {}).map(([t, c]) => t + ' ' + c).join('\n')) + '</textarea></td>';
        h += '<td class="text-end"><div class="fw-semibold">' + fmt(p.unidades) + '</div>' +
            '<div class="cf-muted">factura dice</div><input class="form-control text-end" style="min-width:60px" data-campo="cantidad" title="Cantidad impresa en la factura" value="' + esc(j.cantidad == null ? '' : j.cantidad) + '"' + dis + '>' +
            '<div class="cf-muted">importe</div><input class="form-control text-end" style="min-width:80px" data-campo="importe" title="Importe impreso en la factura" value="' + esc(j.importe == null ? '' : j.importe) + '"' + dis + '></td>';
        // Costo
        h += '<td><input class="form-control text-end" data-campo="costo" value="' + esc(j.costo == null ? '' : j.costo) + '"' + dis + '>' +
            (p.precio_lista ? '<div class="cf-muted" title="Costo neto = importe ÷ unidades">lista $' + fmt(p.precio_lista) + ' − ' + esc(p.descuento) + '</div>' : '') +
            (p.vigentes ? '<div class="cf-muted">vigente $' + fmt(p.vigentes.costo) + '</div>' : '<div class="cf-muted">sobreprecio $' + fmt(p.sobreprecio) + '</div>') + '</td>';
        // Venta
        const sentido = p.vigentes ? (p.precioventa < p.vigentes.precioventa ? ' <b class="text-danger">BAJA</b>' : p.precioventa > p.vigentes.precioventa ? ' <span class="text-success">sube</span>' : ' igual') : '';
        h += '<td><input class="form-control text-end" data-campo="precioventa" value="' + esc(j.precioventa == null ? '' : j.precioventa) + '" placeholder="' + esc(p.precioventa) + '"' + dis + '>' +
            '<div class="cf-muted">' + (j.precioventa ? esc(p.fuente_pv) : 'regla: $' + fmt(p.precioventa)) +
            (p.vigentes ? '<br>vigente $' + fmt(p.vigentes.precioventa) + sentido : '') + '</div></td>';
        // Identidad
        if (p.destino) {
            h += '<td><div class="cf-muted">la ficha manda:<br>' + esc([p.genero && p.genero.valor, p.categoria && p.categoria.nombre, p.color && p.color.valor].filter(Boolean).join(' · ')) + '</div>' +
                '<div class="cf-muted">' + (p.especialidades.length ? 'esp: ' + esc(p.especialidades.join(', ')) : '') + '</div></td>';
        } else {
            h += '<td>' +
                '<select class="form-select mb-1" data-campo="genero"' + dis + '>' + opcionesSelect(o.generos, j.genero, '— género —') + '</select>' +
                '<select class="form-select mb-1" data-campo="categoria"' + dis + '>' + opcionesSelect(o.categorias, j.categoria, '— categoría —') + '</select>' +
                '<select class="form-select mb-1" data-campo="color"' + dis + '>' + opcionesSelect(o.colores, j.color || item.color, '— color —') + '</select>' +
                '<select class="form-select" multiple data-campo="especialidades" title="Especialidades (Ctrl para varias)"' + dis + '>' +
                o.especialidades.map(e => '<option value="' + esc(e) + '"' + ((j.especialidades || []).includes(e) ? ' selected' : '') + '>' + esc(e) + '</option>').join('') +
                '</select></td>';
        }
        // Decisión
        h += '<td class="cf-col-decision">';
        if (p.candidatas && p.candidatas.length) {
            h += '<div class="cf-muted">ficha en la que entra</div><select class="form-select mb-1" data-campo="ficha_id"' + dis + '>' +
                '<option value="">' + (p.estado === 'DUPLICADAS' ? '— elige una —' : 'automático') + '</option>' +
                p.candidatas.map(f => '<option value="' + f.id + '"' + (String(j.ficha_id) === String(f.id) ? ' selected' : '') + '>#' + f.id + ' · ' +
                    esc([f.marca, f.color, f.genero, f.categoria].filter(Boolean).join('/')) + ' · ' + f.tallas + ' tallas · stock ' + fmt(f.stock) + '</option>').join('') +
                '</select>';
        }
        if (p.opciones && !p.omitida) {
            const baja = p.vigentes && p.precioventa < p.vigentes.precioventa;
            h += '<div class="cf-muted">ya existe: ¿qué hago?</div>';
            if (baja) h += '<div class="cf-nota error">la venta de la factura ($' + fmt(p.precioventa) + ') es MENOR que la vigente ($' + fmt(p.vigentes.precioventa) + '): se sugiere mantener la venta</div>';
            p.opciones.forEach((op, i) => {
                const marcada = p.opcion_sugerida ? op === p.opcion_sugerida : i === 0;
                h += '<label class="d-block small"><input type="radio" class="form-check-input me-1" name="cfOp-' + item.idx + '-' + n + '" value="' + op + '"' + (marcada ? ' checked' : '') + dis + '>' + esc(TEXTO_OPCION[op] || op) + '</label>';
            });
        } else if (p.vigentes && !p.omitida) {
            h += '<div class="cf-muted">mismos precios: solo suma stock</div>';
        }
        if (p.gemelas && p.gemelas.length) {
            h += '<div class="cf-muted mt-1">también en: ' + p.gemelas.map(g => esc(g.sucursal) + ' ($' + fmt(g.precioventa) + (g.misma_identidad ? '' : ', otra identidad') + ')').join(', ') + '</div>';
        }
        h += '</td></tr>';
        // Notas de la línea
        const notas = [];
        p.errores.forEach(e => notas.push('<p class="cf-nota error">✗ ' + esc(e) + '</p>'));
        p.avisos.forEach(a => notas.push('<p class="cf-nota aviso">! ' + esc(a) + '</p>'));
        if (p.fichas_formato && p.fichas_formato.length) {
            notas.push('<p class="cf-nota aviso">! ficha(s) ' + esc(p.fichas_formato.join(', ')) + ' pasan a tipo US con guía «' + esc(p.guia ? p.guia.nombre : '') + '»' +
                (p.renombres.length ? ': renombra ' + esc(p.renombres.map(r => r.sucursal + ' ' + r.de + '→' + r.a).join('  ')) : ' (las tallas ya estaban en ese formato)') + '</p>');
        }
        p.conflictos.forEach(c => notas.push('<p class="cf-nota aviso">! ' + esc(c.sucursal) + ': «' + esc(c.de) + '» no se renombra a «' + esc(c.a) + '» porque esa talla ya existe en la ficha (fila duplicada, stock ' + c.stock + '); fusionar aparte</p>'));
        p.sin_resolver.forEach(s => notas.push('<p class="cf-nota aviso">! ' + esc(s.sucursal) + ': «' + esc(s.talla) + '» no calza con la guía: queda igual</p>'));
        if (notas.length) h += '<tr class="cf-detalle"><td></td><td colspan="9">' + notas.join('') + '</td></tr>';
        return h;
    }

    function htmlResultado(item) {
        const r = item.resultado;
        let h = '<div class="mt-3"><div class="fw-semibold small mb-1" style="color:#405189"><i class="bi bi-clipboard-check me-1"></i>Resultado de la carga: ' +
            r.ok + ' línea(s) OK · ' + fmt(r.unidades) + ' unidades' + (r.saltadas ? ' · ' + r.saltadas + ' saltada(s)' : '') + (r.fallidas ? ' · <span class="text-danger">' + r.fallidas + ' fallida(s)</span>' : '') + '</div>';
        h += '<div class="table-responsive"><table class="table table-sm cf-tabla mb-0"><thead><tr><th>#</th><th>Artículo</th><th>Estado</th><th class="text-end">Uds</th><th>Producto</th><th>Detalle</th></tr></thead><tbody>';
        (r.lineas || []).forEach(l => {
            h += '<tr><td>' + l.n + '</td><td>' + esc(l.articulo) + '</td><td>' + estadoBadge(l.estado) + (l.opcion ? ' <span class="cf-muted">[' + esc(l.opcion) + ']</span>' : '') + '</td>' +
                '<td class="text-end">' + fmt(l.cargadas != null ? l.cargadas : l.unidades) + '</td>' +
                '<td>' + (l.producto_id ? '#' + l.producto_id : '—') + (l.renombradas ? ' <span class="cf-muted">· ' + l.renombradas + ' talla(s) renombradas</span>' : '') + '</td>' +
                '<td>' + esc(l.detalle || '') + (l.tallas && l.tallas.length ? '<div class="cf-muted">' + esc(l.tallas.map(t => t.talla + ': sku ' + t.sku + ' → ' + t.stock_final).join('  ')) + '</div>' : '') + '</td></tr>';
        });
        h += '</tbody></table></div></div>';
        return h;
    }

    /** Lee de la tarjeta lo que la persona corrigió (todos los campos editables). */
    function leerCorrecciones(idx) {
        const card = tarjeta(idx);
        if (!card) return null;
        const cambio = { idx: idx, lineas: [] };
        // Campos de la factura = los que no están dentro de una fila de línea.
        card.querySelectorAll('[data-campo]').forEach(el => {
            if (el.closest('tr[data-n]')) return;
            cambio[el.dataset.campo] = el.type === 'checkbox' ? el.checked : el.value;
        });
        card.querySelectorAll('tr[data-n]').forEach(tr => {
            const n = parseInt(tr.dataset.n, 10);
            const linea = {};
            tr.querySelectorAll('[data-campo]').forEach(el => {
                const campo = el.dataset.campo;
                if (campo === 'tallas') {
                    if (!el.classList.contains('d-none')) linea.tallas = el.value;
                } else if (el.multiple) {
                    linea[campo] = Array.from(el.selectedOptions).map(op => op.value);
                } else if (el.type === 'checkbox') {
                    linea[campo] = el.checked;
                } else {
                    linea[campo] = el.value;
                }
            });
            cambio.lineas[n] = linea;
        });
        for (let i = 0; i < cambio.lineas.length; i++) if (!cambio.lineas[i]) cambio.lineas[i] = {};
        return cambio;
    }

    function leerOpciones(idx) {
        const card = tarjeta(idx);
        const opciones = {};
        if (!card) return opciones;
        // Por N° de línea (1-based): dos líneas pueden compartir código (dos colores).
        card.querySelectorAll('tr[data-n]').forEach(tr => {
            const r = tr.querySelector('input[type=radio]:checked');
            if (r) opciones[String(parseInt(tr.dataset.n, 10) + 1)] = r.value;
        });
        return opciones;
    }

    async function cargar(idx) {
        const item = st.previas[idx];
        if (!item || !st.sesion) return;
        // Primero se guardan las correcciones y se recalcula, por si cambió algo.
        await planificar([leerCorrecciones(idx)], idx);
        const previa = st.previas[idx];
        if (!previa || previa.error || !previa.totales) return;
        if (previa.totales.bloqueantes) {
            avisar('Hay líneas con error', 'Corrígelas u omítelas antes de cargar.', 'warning');
            return;
        }
        const opciones = leerOpciones(idx);
        const existentes = previa.planes.filter(p => p.opciones && !p.omitida);
        const detalle = existentes.length
            ? '<br><br><small>Existentes: ' + existentes.map(p => 'línea ' + p.n + ' ' + esc(p.articulo) + ' → ' + esc(TEXTO_OPCION[opciones[String(p.n)] || p.opcion_sugerida || 's'])).join('; ') + '</small>'
            : '';
        const ok = await Swal.fire({
            title: 'Cargar la factura N° ' + esc(previa.folio) + '?',
            html: previa.totales.a_cargar + ' línea(s), ' + fmt(previa.totales.unidades) + ' unidades en <b>' + esc(previa.sucursal) + '</b>, contra el DTE id ' + previa.dte.id + '.' +
                '<br>Cada línea queda registrada como Crear Producto Manual (movimiento, lote, DTE y compra).' + detalle,
            icon: 'question', showCancelButton: true, confirmButtonText: 'Sí, cargar', cancelButtonText: 'Cancelar',
        });
        if (!ok.isConfirmed) return;
        try {
            await api(st.sesion + '/cargar/', { method: 'POST', json: { idx: idx, opciones: opciones } });
        } catch (e) {
            avisar('No se pudo iniciar la carga', e.message);
            return;
        }
        detenerSondeo();
        st.estadoPrevio = 'CARGANDO';
        await refrescar(false);
    }

    // --------------------------------------------------------------- eventos

    function abrir() {
        const el = document.getElementById('modalCargaFactura');
        if (!el) return;
        if (typeof window.mostrarModal === 'function') window.mostrarModal('#modalCargaFactura');
        else bootstrap.Modal.getOrCreateInstance(el).show();
        if (!st.sesion && !$chat().children.length) bienvenida();
        pintarEstado();
        actualizarComposer();
        cargarOpciones().catch(e => avisar('Agente', e.message));
        cargarRecientes();
        retomarSesion();
    }

    document.addEventListener('DOMContentLoaded', function () {
        const $btn = document.getElementById('btnCargarFacturaIA');
        if ($btn) $btn.addEventListener('click', abrir);
        const modal = document.getElementById('modalCargaFactura');
        if (!modal) return;
        modal.addEventListener('hidden.bs.modal', detenerSondeo);
        modal.addEventListener('shown.bs.modal', function () { if (st.sesion) { detenerSondeo(); refrescar(false); } });

        document.getElementById('cfBtnAdjuntar').addEventListener('click', () => document.getElementById('cfArchivo').click());
        document.getElementById('cfArchivo').addEventListener('change', mostrarArchivo);
        document.getElementById('cfQuitarArchivo').addEventListener('click', quitarArchivo);
        document.getElementById('cfSucursal').addEventListener('change', actualizarComposer);
        document.getElementById('cfBtnEnviar').addEventListener('click', enviar);
        document.getElementById('cfBtnHablar').addEventListener('click', hablar);
        const $texto = document.getElementById('cfTexto');
        $texto.addEventListener('input', function () { autoAlto(this); actualizarComposer(); });
        $texto.addEventListener('keydown', function (ev) {
            if (ev.key !== 'Enter' || ev.shiftKey) return;
            ev.preventDefault();
            // Con vista previa, Enter le escribe al agente; antes, Enter envía el PDF adjunto.
            if (st.sesion && st.resumen && st.resumen.estado === 'LEIDA') hablar();
            else if (document.getElementById('cfArchivo').files[0]) enviar();
        });
        document.getElementById('cfBtnNueva').addEventListener('click', nuevaConversacion);
        document.getElementById('cfRecientes').addEventListener('change', function () {
            if (this.value) abrirSesion(parseInt(this.value, 10));
        });

        // Acciones dentro de las tarjetas (se pintan dinámicamente)
        modal.addEventListener('click', function (ev) {
            const a = ev.target.closest('[data-accion]');
            if (!a) return;
            const card = a.closest('.cf-card');
            if (!card) return;
            const idx = parseInt(card.dataset.idx, 10);
            if (a.dataset.accion === 'editar-tallas') {
                ev.preventDefault();
                const ta = a.closest('td').querySelector('textarea[data-campo="tallas"]');
                if (ta) ta.classList.toggle('d-none');
            } else if (a.dataset.accion === 'recalcular') {
                planificar([leerCorrecciones(idx)], idx);
            } else if (a.dataset.accion === 'cargar') {
                cargar(idx);
            }
        });
    });

    window.CargaFactura = { abrir: abrir, abrirSesion: abrirSesion };
})();
