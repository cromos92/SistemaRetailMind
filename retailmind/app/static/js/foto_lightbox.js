/**
 * Lightbox simple para fotos de portada de productos.
 *
 * Cualquier <img> con clase `.producto-portada-img` (o cualquier elemento con
 * atributo `data-foto-zoom`) muestra cursor zoom y al hacer click abre la
 * foto en grande sobre un backdrop oscuro. Cerrar con click fuera, botón X,
 * tecla Esc.
 *
 * No depende de librerías externas — vanilla JS + jQuery. El backdrop se
 * crea una sola vez en DOM al cargar y se reusa.
 *
 * Uso: incluir este script al final de cualquier template que tenga fotos
 * de productos. El binding es global y delegated, así sirve para filas
 * renderizadas dinámicamente.
 */
(function () {
    'use strict';

    if (window.__FotoLightboxLoaded) return;
    window.__FotoLightboxLoaded = true;

    var CSS = '' +
        '#foto-lightbox-backdrop{' +
            'position:fixed;inset:0;background:rgba(0,0,0,.85);' +
            'display:none;align-items:center;justify-content:center;' +
            'z-index:99999;cursor:zoom-out;padding:20px;' +
            'animation:foto-fade-in .15s ease-out;' +
        '}' +
        '#foto-lightbox-backdrop.is-open{display:flex;}' +
        '#foto-lightbox-img{' +
            'max-width:92vw;max-height:88vh;border-radius:8px;' +
            'box-shadow:0 10px 40px rgba(0,0,0,.5);background:#fff;' +
            'object-fit:contain;' +
        '}' +
        '#foto-lightbox-close{' +
            'position:absolute;top:18px;right:24px;color:#fff;' +
            'background:rgba(0,0,0,.5);border:none;border-radius:50%;' +
            'width:40px;height:40px;font-size:24px;line-height:1;' +
            'cursor:pointer;display:flex;align-items:center;justify-content:center;' +
        '}' +
        '#foto-lightbox-close:hover{background:rgba(255,255,255,.2);}' +
        '@keyframes foto-fade-in{from{opacity:0}to{opacity:1}}' +
        '.producto-portada-img,[data-foto-zoom]{cursor:zoom-in;transition:transform .12s;}' +
        '.producto-portada-img:hover,[data-foto-zoom]:hover{transform:scale(1.05);}';

    function injectStyles() {
        var style = document.createElement('style');
        style.id = 'foto-lightbox-styles';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    function buildBackdrop() {
        var backdrop = document.createElement('div');
        backdrop.id = 'foto-lightbox-backdrop';
        backdrop.innerHTML =
            '<button type="button" id="foto-lightbox-close" aria-label="Cerrar">&times;</button>' +
            '<img id="foto-lightbox-img" src="" alt="">';
        document.body.appendChild(backdrop);
        return backdrop;
    }

    function isPlaceholder(url) {
        if (!url) return true;
        return url.indexOf('producto_placeholder') !== -1;
    }

    function init() {
        injectStyles();
        var backdrop = buildBackdrop();
        var img = backdrop.querySelector('#foto-lightbox-img');

        function open(url) {
            if (!url) return;
            img.src = url;
            backdrop.classList.add('is-open');
            document.body.style.overflow = 'hidden';
        }

        function close() {
            backdrop.classList.remove('is-open');
            document.body.style.overflow = '';
            img.src = '';
        }

        // Delegated click — funciona con elementos renderizados dinámicamente.
        document.addEventListener('click', function (e) {
            var target = e.target;
            if (!target) return;
            // Match en la imagen miniatura o cualquier elemento marcado con data-foto-zoom.
            if (target.matches && (target.matches('img.producto-portada-img') ||
                                   target.matches('[data-foto-zoom]'))) {
                var url = target.tagName === 'IMG'
                    ? target.getAttribute('src')
                    : target.getAttribute('data-foto-zoom');
                if (isPlaceholder(url)) return;  // no abrir si es placeholder
                e.preventDefault();
                e.stopPropagation();
                open(url);
            }
        }, true);

        backdrop.addEventListener('click', function (e) {
            // Click en el backdrop o en la X cierra. Click sobre la imagen no cierra.
            if (e.target === backdrop || e.target.id === 'foto-lightbox-close') {
                close();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && backdrop.classList.contains('is-open')) {
                close();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
