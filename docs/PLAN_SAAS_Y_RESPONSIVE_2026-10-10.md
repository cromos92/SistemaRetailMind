# RetailMind → producto vendible (SaaS multi-cuenta) + responsive — Análisis y plan (2026-10-10)

> **Pedido:** (1) mejorar el responsive de toda la app; (2) poder vender el sistema a
> más empresas sin que compartan datos entre ellas, **salvo las empresas que ya están
> configuradas hoy**, que deben seguir compartiendo lo que comparten.
>
> Este documento es solo análisis + plan: **no cambia código ni datos**. Las referencias
> `archivo:línea` salen de leer el código el 2026-10-10. Los hallazgos críticos se
> revisaron dos veces; los conteos marcados como heurística o estimación son aproximados.
> Rutas relativas a `retailmind/` salvo que se indique otra cosa.

---

## 0. Resumen ejecutivo

### Lo que entendí

Hoy el sistema lo usa tu grupo (Paola / Realsport y las demás razones sociales del
holding) en **una sola base de datos**, y las empresas del grupo comparten catálogo,
clientes, proveedores, traspasos, gift cards, puntos, etc. Quieres venderlo a otras
empresas: cada cliente nuevo debe ver **solo sus datos**, y tu grupo debe seguir
funcionando igual que hoy, compartiendo entre sí.

Para no confundir "cliente" (el que compra en la tienda) con "cliente del software", en
este plan:

> **Cuenta** = un cliente que te compra el sistema. Una cuenta puede tener una o varias
> empresas (RUT) y sus sucursales. **Tu grupo actual = Cuenta #1.**

### Conclusiones

| # | Conclusión |
|---|---|
| 1 | 🔴 **Antes de vender nada:** el repo `cromos92/SistemaRetailMind` sigue **público** y `retailmind/.env` (credenciales de producción) sigue versionado — último cambio el 2026-09-21. Además hay **258 archivos de datos** (xlsx/csv/json) y **173 TXT de DTE** versionados. El incidente documentado en `docs/SEGURIDAD_URGENTE_2026-07-25.md` sigue abierto. → **Fase 0**. |
| 2 | Hoy la separación entre empresas es **"blanda"**: cada vista filtra a mano por sesión (`idSucursalActual` / `idEmpresaActual`) con ~60 helpers distintos. **296 de 793 vistas (37%)** que consultan la BD no filtran por empresa ni sucursal, y **129 vistas** cargan registros por id sin validar a quién pertenecen. Las auditorías de jul–ago ya encontraron fugas (ventas-global, kardex, existencias por marca, export Excel). |
| 3 | Además, **55 de los 160 modelos no tienen ningún vínculo con una empresa** (clientes, categorías, marcas, proveedores, permisos por rol, configuración, asistente IA) + la tabla `Usuario`. Y la tabla `Empresa` mezcla tus empresas con proveedores y clientes B2B. Esos datos hoy son **compartidos por diseño**. |
| 4 | Los roles maestro/administrador/jefe **ven todas las empresas de la base, por diseño** (y hay tests que lo fijan). Para un grupo con el mismo dueño está bien; si mañana entra un tercero a esta misma base, su administrador vería tu grupo y viceversa. |
| 5 | ✅ **Recomendación: un esquema PostgreSQL por cuenta** (librería `django-tenants`). Tu grupo pasa completo a un esquema ("Cuenta #1") y sigue compartiendo **todo** como hoy, incluidas las funciones cruzadas entre empresas del grupo. Cada cuenta nueva nace en su propio esquema vacío. **El aislamiento lo garantiza la base de datos, no 1.830 vistas**: aunque una vista olvide un filtro, nunca puede ver otra cuenta. Las 161 consultas SQL crudas siguen funcionando sin reescribirse. |
| 6 | El **"módulo para vender"** es una **consola de plataforma** (fuera de las cuentas): alta automática de cuentas, planes, módulos contratados, límites (sucursales/usuarios/cajas), estado de suscripción (prueba/activa/morosa/suspendida), cobros y uso. |
| 7 | **Responsive:** el problema más grande no está en cada pantalla sino en la base común. El **modo kiosko del POS queda activo por defecto para todos** (`POS_KIOSK_DEFAULT`), y eso hace que el iPhone haga zoom al tocar cualquier campo. Además: en celular todas las páginas se desbordan unos px a los lados, en tablet el menú solo abre con mouse, los modales grandes quedan de 500px y el login no hace scroll. Plan: primero arreglar la capa global (CSS + layout), que alcanza a las 107 páginas de una vez; después las pantallas que se usan en celular o tablet; al final un barrido por módulo. Se mide con capturas automáticas antes y después. |

### Hoja de ruta (estimación de orden de magnitud, 1 desarrollador + Claude)

| Fase | Qué | Duración | Depende de |
|---|---|---|---|
| **0** | Seguridad: repo privado, rotar credenciales, sacar `.env` y datos del repo | 2–3 días (+ rotación que haces tú) | — |
| **R0–R1** | Responsive: medición + capa global | 1–2 semanas | — |
| **R2** | Responsive: pantallas prioritarias (garantías, requerimientos, dashboards, documentos de venta, ecommerce, clientes, POS) | 2–3 semanas | R1 |
| **R3–R4** | Responsive: barrido por módulo + POS tablet | 3–5 semanas | R2 |
| **B0** | SaaS: preparación (sacar lo "hardcodeado" del grupo, seguridad cruzada, tests en Postgres + CI, staging) | 2–3 semanas | 0 |
| **B1** | SaaS: esquema por cuenta en staging + ensayo de migración de tu grupo | 3–4 semanas | B0 |
| **B2** | SaaS: consola de plataforma (el módulo de venta) | 3–4 semanas | B1 |
| **B3** | SaaS: integraciones por cuenta (DTE/Acepta, correo, Mercado Pago, POS escritorio, IA) | 2–4 semanas | B1 |
| **B4** | Migración real de producción + primera cuenta piloto | 1–2 semanas | B2, B3 |
| **B5** | Comercial/legal (contrato, tratamiento de datos, precios) | en paralelo | — |

El responsive (R) y el SaaS (B) son independientes y pueden avanzar en paralelo.
**Primer cliente pagando en su propio esquema: ~3–4 meses.** Si aparece un cliente
antes, ver §3.11 (plan puente).

---

## 1. Fase 0 — Seguridad (bloqueante para vender; urgente igual)

Mientras el repo sea público con `.env` dentro, **cualquiera** puede descargar la
`DATABASE_URL` de producción, la `SECRET_KEY` (que firma los JWT del POS y las apps:
con ella se pueden falsificar sesiones), SMTP, Spaces, AllConnected, la API key externa y
la clave privada de QZ. Ningún cliente nuevo debería entrar a una plataforma así.

| # | Acción | Quién |
|---|---|---|
| 0.1 | Poner el repo **privado** (GitHub → Settings → Danger Zone → Change visibility) | Tú |
| 0.2 | **Rotar todas** las credenciales de `retailmind/.env` (las variables están listadas en `docs/SEGURIDAD_URGENTE_2026-07-25.md` §2). Rotar `SECRET_KEY` cierra todas las sesiones y tokens: avisar a las tiendas | Tú (yo te preparo el checklist y los valores nuevos donde aplique) |
| 0.3 | Dejar de versionar `retailmind/.env` (`git rm --cached`) y verificar `.gitignore` | Yo, con tu OK |
| 0.4 | Sacar del repo los **258** xlsx/csv/json de datos, los **173** TXT de `exports_txt/`, `media/` y `db.sqlite3` → guardarlos en almacenamiento privado | Yo, con tu OK |
| 0.5 | Purgar el historial (`git filter-repo`) para que las credenciales viejas no queden en commits antiguos. **Requiere `push --force`**: solo con confirmación explícita y avisando a quien tenga clones | Yo, con confirmación explícita |
| 0.6 | Quitar el `SECRET_KEY` de respaldo inseguro de `retailmind/settings.py:30` (que falle si falta la variable) | Propuesta (toca settings: requiere tu OK) |

---

## 2. Parte A — Responsive

### 2.1 Diagnóstico

**Tamaño:** 150 plantillas (264 mil líneas). 107 usan el layout (`header`/`menu`/`footer`),
134 traen su propio `<style>` y suman 388 `@media` con unos 40 breakpoints distintos
(82× `768px`, 67× `576px`, 16× `767.98px`, …). Hay 5 plantillas huérfanas que ningún
código usa (~8,8 mil líneas, p. ej. `cuadraturaCaja_v2.html`).

**Los 9 problemas que más pesan** (verificados en el código):

| # | Problema | Dónde | Efecto |
|---|---|---|---|
| 1 | **El modo kiosko del POS queda activo para todos por defecto.** `POS_KIOSK_DEFAULT` vale `'True'` si no se define la variable, y el context processor lo aplica a cada página, usuario y equipo | `retailmind/settings.py:229`, `app/context_processors.py` | `pos-kiosk.css` §13 (`@media (max-height:800px),(max-width:1400px)`, `:755`) alcanza a todos los celulares y tablets, y fuerza los inputs a 15px `!important` (`:813-828`). Eso anula la regla anti-zoom de 16px de `nexo-responsive.css`, así que **el iPhone hace zoom al tocar cualquier campo**, incluido el login. *Verificar el valor en producción* |
| 2 | **Todas las páginas "bailan" hacia los lados en celular** (≤576px) | `nexo-responsive.css:788-797` | El padding baja a 0,25rem pero las `.row` mantienen el gutter negativo de 0,75rem: desborde de ~4px por lado. `buscar_productos_sucursal.html:17-24` lo tapa con `overflow-x:hidden` en todo el layout |
| 3 | **Los modales grandes quedan angostos en tablet vertical** (769–991px) | Bootstrap 5.2.3; nexo solo corrige ≤768 (`nexo-responsive.css:211-216`) | 145 modales `modal-lg`/`modal-xl` quedan en 500px. Ninguno usa `modal-fullscreen-*-down` |
| 4 | **El menú en tablet (768–1024) solo funciona con mouse** | `app.js` fuerza `data-sidebar-size="sm"` | El menú queda como una franja de íconos de 70px y sus submenús se abren solo con *hover*. El buscador del menú queda aplastado |
| 5 | **El login no hace scroll en celular** | `registration/login.html:59-63` (`height:100vh; overflow:hidden`), logo con estilo inline en `:513` | En pantallas bajas el formulario queda cortado |
| 6 | **El POS solo funciona en pantallas ≥1024 en horizontal** | `generacionVentas.html:2044` (`clamp(510px,51%,630px)`, sin quiebre), alto fijo en `:1790` | En tablet vertical la tabla de productos queda de ~150px. En celular se desborda |
| 7 | **Barra superior:** el bloque derecho no se encoge | `layout/menu.html:649` (`flex-nowrap` sin `min-width:0`) | Estimado: para un jefe de local con notificaciones, en 360–375px el menú de usuario (perfil / cerrar sesión) queda fuera de la pantalla |
| 8 | **`nexo-responsive.css` tiene reglas dañinas y otras muertas** | dañinas: `:893-898`, `:143-151`, `:127-133`; muerta: `:926` | Dañinas: ver nota abajo. Muerta: `:926` usa `body.sidebar-enable`, pero Velzon usa `vertical-sidebar-enable` |
| 9 | **El chat del asistente está roto** | `assistant/templates/assistant/chat.html:1` | Hace `{% extends 'layout/header.html' %}` sobre un archivo sin bloques: la página sale en blanco |

Detalle de las reglas dañinas del punto 8:

- `.row > .col-6` se fuerza a 100% bajo 576px (`:893-898`). Afecta a los 482 `col-6`,
  que incluyen pares de KPI pensados para verse de a dos.
- La primera columna fija de las tablas usa un fondo transparente (`:143-151`).
- El margen negativo de `.table-responsive` (`:127-133`) desborda dentro de 26
  `.card-body.p-0`.

**Patrones repetidos** (conteos en todas las plantillas):

| Patrón | Cantidad |
|---|---|
| Tablas sin contenedor con scroll | ~49 en el HTML + ~111 armadas en JS |
| Columnas fijas sin breakpoint (`col-3`, `col-4`, `col-6`…) | 247, de ellas 41 envuelven formularios. Ej.: 4 KPI `col-3` en `dashboard_fifo.html:991-1018` |
| Reglas `:hover` | 856, con solo 5 guardas `(hover:hover)` |
| Botones que solo aparecen al pasar el mouse | p. ej. `verGestionProductos.html:737-744` |
| Tamaños de fuente < 0,75rem | 1.997 |
| `select` de 11px | `gestionVentasDocumentos.html:174-228` (provoca zoom en iOS) |
| Páginas en uso sin ningún `@media` | 14, p. ej. `devolucion_garantia`, `lista_clientes`, `retiro_pedido_local`, `pedido_ecommerce_detalle`, `ficha_cliente` |
| Plantillas que redeclaran sus propios tokens `:root` | 67 |
| Plantillas que redefinen `.nexo-table` | 19 |

**Lo que ya está bien y conviene copiar:**

- widget del código de autorización (`menu.html:888-937`);
- búsqueda de stock: la tabla pasa a tarjetas bajo 768px (`buscar_productos_sucursal.html:994-1086`);
- ajuste de stock rápido, con barra fija abajo;
- requerimientos (tarjetas bajo 768px);
- `dashboard_home`;
- modal a pantalla completa con `100dvh` bajo 992px en `gestion_cambios_devoluciones.html:2611-2631`.

**Pantallas que el staff usa en el celular:**

| Uso | Estado |
|---|---|
| Código de autorización, consulta y ajuste de stock, requerimientos (listado), ventas del día | ✅ Bien |
| Tarjeta de existencia, inventarios, detalle de requerimiento, dashboards, documentos de venta, ticket de piso | 🟡 Regular |
| Garantías (`devolucion_garantia`, `detalle_devolucion_garantia`), retiro y detalle de pedidos ecommerce, ficha de cliente de fidelización | 🔴 Mal |
| POS (`generacionVentas`) | ⛔ No apto para celular ni tablet vertical |

> Relación con `docs/PLAN_APP_MOVIL_STAFF.md`: ese plan descartó la web responsive para el
> staff porque "el dolor es justamente la UX móvil". Este plan no lo reemplaza: arregla la
> base web, y la app nativa puede seguir siendo una decisión aparte.

### 2.2 Estrategia

1. **Primero la capa global** (CSS + layout + un JS chico): arregla las 107 páginas del
   layout de una vez, sin tocar plantilla por plantilla.
2. **Después las pantallas que se usan en celular/tablet**, y al final un barrido por
   módulo con checklist.
3. **Expectativa realista por tamaño:**
   - Celular: consultar y aprobar.
   - Tablet: operar.
   - Escritorio y kiosko 1920: todo.

   Las pantallas gigantes de backoffice se dejan "usables en tablet", no "perfectas en
   celular": `verGestionProductos` (19,5 mil líneas), `gestionCompras` y `recepcion_dte`.
4. **Un solo juego de breakpoints:** los de Bootstrap (575.98 / 767.98 / 991.98 / 1199.98).
   Se usan los tokens de `nexo-design-system.css` y nada de paletas nuevas.
5. **No tocar la impresión** (boletas, etiquetas Zebra, PDFs): toda regla global nueva va
   dentro de `@media screen`.
6. **Medir, no adivinar:** un script con Playwright abre ~30 pantallas en 360, 390, 768,
   1024, 1366 y 1920px. Detecta:
   - scroll horizontal (`scrollWidth > innerWidth`);
   - elementos fuera de pantalla;
   - inputs < 16px;
   - botones < 44px.

   Guarda capturas antes y después. Es una herramienta de desarrollo: agregarla a
   `requirements-dev.txt` requiere tu visto bueno. Necesita la app corriendo con datos de
   prueba (staging).

### 2.3 Fases

**R0 — Decisiones y medición** (2–3 días)

- **Modo kiosko:** dejar `POS_KIOSK_DEFAULT=False` en producción (es una variable de
  entorno, no código) y activar el modo táctil solo en los equipos POS (`?kiosk=1` queda
  guardado en la sesión).
- Línea base con el script de capturas.
- Excluir del alcance las 5 plantillas huérfanas y el CSS sin uso (`pos-transbank.css`,
  `myCss.css`, `custom.css`). Se borran solo con tu OK.

**R1 — Capa global** (1 semana)

| # | Cambio | Archivo |
|---|---|---|
| 1 | Inputs ≥ 16px también en modo kiosko, o excluir de §13 los táctiles < 992px | `pos-kiosk.css:755`, `:813-828`, `:876-879` |
| 2 | Corregir el desborde lateral en celular y quitar el parche | `nexo-responsive.css:788-797`, `buscar_productos_sucursal.html:17-24` |
| 3 | Modales: 769–991 al 90–95% de ancho; `lg`/`xl` a pantalla completa bajo 768 (solo CSS, sin editar los 145 modales); borrar la regla muerta | `nexo-responsive.css:172-223` |
| 4 | Menú en tablet: menú lateral deslizable (como en celular) entre 768 y 1024, sin submenús por *hover*; arreglar el selector muerto | JS chico después de `app.js`; `nexo-responsive.css:926` |
| 5 | Login con scroll (`min-height:100dvh`) y logo con clase | `registration/login.html:59-63`, `:513` |
| 6 | Barra superior: el bloque derecho puede encogerse; la píldora de sucursal pasa a ícono bajo 400px; el cambio de tema se mueve al menú de usuario en celular | `layout/menu.html:649`, `:1888-1895`, `:658-664` |
| 7 | `.row > .col-6` a 100% deja de ser global (pasa a una clase opcional); fondo de la primera columna fija; margen de `.table-responsive` acotado | `nexo-responsive.css:893-898`, `:143-151`, `:127-133` |
| 8 | Viewport: `width=1920`/`user-scalable=no` solo en el kiosko estricto; agregar `viewport-fit=cover` | `layout/header.html:9-15`, `login.html:8-10` |
| 9 | El botón del asistente IA no tapa las barras de acción de abajo y se oculta en modo kiosko | `assistant/templates/assistant/widget.html:90-100` |
| 10 | Componentes compartidos, sacados de las páginas que ya lo hacen bien: `.module-header` responsive, pestañas con scroll horizontal, tabla→tarjetas, barra de acción fija abajo, modal completo < 992 | `nexo-responsive.css` |
| 11 | Envolver en `.table-responsive` las tablas que no lo están, incluidas las que se arman por JS | JS de layout, a evaluar |

**R2 — Pantallas prioritarias** (2–3 semanas)

- Garantías (`devolucion_garantia`, `detalle_devolucion_garantia`) y
  `detalle_requerimiento` (botones `col-4`, tablas de .72rem).
- `dashboard_ventas_nexo` y los otros 6 dashboards con el override `col-lg` copiado; sus
  tablas sin contenedor.
- `gestionVentasDocumentos` (controles de 11px).
- Ecommerce: `retiro_pedido_local`, `pedido_ecommerce_detalle`, `pedidos_ecommerce_list`.
- Clientes y fidelización: `ficha_cliente`, `lista_clientes`, `dashboard_clientes`.
- `gestion_dte` (su `overflow:hidden` corta 7 tablas).
- **POS (`generacionVentas`):** apilar las columnas en tablet vertical (paso 3 en `:2044`,
  modal de producto en `:2707`) o mostrar el aviso "usar en horizontal ≥1024". **Decisión
  tuya.**

**R3 — Barrido por módulo** (3–4 semanas)

- Orden: ventas → existencias → requerimientos → documentos → compras → reportes →
  administración/configuración → fidelización/giftcards → empresas/clientes → usuarios.
- Checklist por pantalla: §2.4.

**R4 — POS en tablet y kiosko 1920** (1 semana)

- Que el kiosko quede sin regresiones (capturas idénticas).
- Tablet en horizontal.
- Probar en el equipo real si el bloqueador de doble toque del modo estricto
  (`menu.html:97-102`) se come los toques rápidos del teclado numérico.

### 2.4 Criterios de "listo"

| Ancho | Debe cumplir |
|---|---|
| 360–430px | Sin scroll horizontal de página (solo dentro de tablas); menú y menú de usuario alcanzables; modales a pantalla completa; formularios en una columna; inputs ≥ 16px (sin zoom en iPhone); botones ≥ 44px |
| 768–1024px | Menú táctil sin *hover*; modales ≥ 90% de ancho; POS usable en horizontal |
| 1920px kiosko | Sin regresiones |
| Impresión | Boletas, etiquetas y PDFs idénticos a hoy |
| Medición | El script reporta 0 pantallas prioritarias con scroll horizontal |

---

## 3. Parte B — SaaS multi-cuenta

### 3.1 Cómo se separan hoy las empresas

- **No existe** concepto de cuenta/tenant/plan/licencia en el código. El "holding" es
  implícito: cualquier `Empresa` que tenga filas en `EmpresaUser`.
- La empresa y sucursal actuales son **dos llaves de sesión**, `idSucursalActual` (333
  lecturas) e `idEmpresaActual` (86), escritas en el login (`retailmind/views.py:157-211`)
  y en el cambio de empresa (`app/views.py:33740-33897`). Nada asigna la cuenta al
  request: **cada vista decide** si filtra.
- `PermisosMenuMiddleware` (`app/middleware_permisos.py`) solo revisa el **rol** (qué
  puede hacer), nunca **de quién es el dato**; deja pasar URLs no mapeadas y todo `/api/`
  (`:673`, `:775-780`). `app/views_modulo_reportes.py:52-56` lo dice explícito: el límite
  entre empresas "tiene que estar DENTRO de la vista".
- Helpers de alcance: `app/utils_permisos.py` (`obtener_sucursales_usuario`,
  `ids_sucursales_alcance`, `puede_ver_sucursal`, …) lo usan solo ~13 de 45 módulos de
  vistas; hay ~40 helpers locales más con reglas distintas.
- Medición (heurística AST sobre las vistas):
  - **296 de 793** vistas que tocan el ORM no tienen ninguna referencia de alcance.
  - **129** vistas que buscan por id no validan pertenencia (candidatas a IDOR), p. ej.
    detalle de documento de venta `app/views_modulo_ventas.py:7262` y `:7320`.
  - **76** filtros del tipo `if sucursal_id: qs = qs.filter(...)`: si la sesión no tiene
    sucursal, devuelven **todas** las empresas.
- **Roles que ven todo:** maestro/administrador/jefe (+ `administracion` en algunos
  módulos, + el flag `puede_ver_todas_sucursales`) ven todas las empresas
  (`app/utils_permisos.py:173-177`, `:197-201`); `app/tests/test_scoping_reportes.py:17-23`
  lo fija como diseño. Pueden asignarse a sí mismos cualquier sucursal
  (`users/views.py:1426-1521`) y **sobrescribir el token de Mercado Pago de cualquier
  empresa** (`app/views_mercadopago.py:454-481`, recibe `empresa_id` sin validar).
- **APIs:**
  - Sync del POS de escritorio: solo `IsAuthenticated`; la sucursal sale del parámetro
    `sucursal_id` **sin validar pertenencia**, para leer productos/costos y para **subir
    tickets y arqueos** (`app/api/sync/views.py:50-63`, `:322-334`, `:377-389`).
  - Login de escritorio: falla abierto si el usuario no tiene sucursales asignadas
    (`app/api/desktop/serializers.py:109`, `if permitidas and …`).
  - API externa y API de ecommerce: **una sola API key global** (`RETAILMIND_API_KEY`,
    `retailmind/settings.py:730`) y el que llama elige la empresa por `rut_empresa`.
- `/media/` se sirve a cualquier usuario logueado (`retailmind/urls.py:60-64`).

### 3.2 Qué datos se comparten hoy (clasificación de los 160 modelos)

Análisis del grafo de FKs de todos los modelos (`app/models/*`, `models_sync.py`, `users`,
`empresa_management`, `assistant`), sin contar las FK que apuntan a un proveedor o
cliente (p. ej. `Compras.empresa` **es el proveedor**, `Cliente.empresa` es la empresa del
cliente, `Dte.emisor` en compras es el proveedor):

| Tipo | Modelos | Ejemplos |
|---|---|---|
| Raíz | 2 | `Empresa`, `Sucursal` |
| Acotados a sucursal o a empresa propia | 103 | `Producto` (¡cada fila es de una sucursal!), `Ticket`, `ArqueoCaja`, `Movimientos_Producto`, `Traspaso`, `Requerimiento`, `PedidoEcommerce`, `GiftCard`… |
| **Sin vínculo con ninguna empresa (compartidos)** | **55** (+ `Usuario`) | ver lista abajo |

Compartidos hoy (7 de ellos son duplicados muertos de `empresa_management/models.py`):

- **Catálogo:** `Categoria`, `Productos_Atributos`, `AtributoOpcion` (marcas, colores,
  género), `GuiaTalla`, `GuiaTallaItem`.
- **Terceros y clientes:** `Cliente`, `ContactoEmpresa`, `LogCliente`, `LogEmpresa`,
  `Proveedor`, y las filas de `Empresa` que son proveedores/clientes. `Compras` (la orden
  de compra solo apunta al proveedor; el dueño se infiere por la sucursal destino de sus
  líneas).
- **Fidelización / app de clientes:** `ProgramaFidelizacion`, `CuentaPuntos`,
  `DesafioPromo`, `Referido`, `DispositivoCliente`, `CuentaClienteApp`,
  `CodigoOTPCliente`, `RefreshTokenClienteApp`.
- **Permisos:** `ModuloSistema`, `OpcionMenu`, `PermisoRol` (una matriz por rol para
  todo el sistema), `PermisoUsuario`, `ConfiguracionPermisoGlobal`,
  `CodigoAutorizacionDinamico`.
- **Configuración:** `ParametroGlobal`, `ConfiguracionPrediccion`,
  `ConfiguracionRequerimientos` (fila única pk=1), `ClaveProveedorIA`, `ModeloTareaIA`,
  `CurvaTalles`, `VelocidadHistorica`, `CurvaDistribucion(+Item)`, `PerfilCargaMarca`,
  `ProductoAprendido`, `TipoFotoRequerimiento`, `CorreoProveedorRequerimiento`.
- **Otros:** `EnvioCorreo`, `RespuestaCorreo`, `MercadoPagoWebhookEvento`, los 4 modelos
  del asistente IA, `LogAcceso`, `SesionActiva`, `TokenResetPassword`.

Además, **unicidades globales** que chocarían entre clientes distintos: `username`,
`Usuario.rut` (`users/models.py:33`), `Proveedor.codigo_proveedor` (`app/models/crm.py:196`),
código de gift card, `codigo_publico` de cupones, numeración de cotizaciones `COT-` y
solicitudes `SOL-`, `(canal, número de pedido)` de ecommerce.

> **Lectura:** para tu grupo esto es correcto y deseado (comparten catálogo, clientes,
> puntos y gift cards en toda la cadena). Para un tercero es inaceptable. Por eso la
> frontera de aislamiento **no puede ser `Empresa`**: tiene que ser una capa nueva por
> encima — la **Cuenta** — dentro de la cual las empresas sí pueden compartir.

### 3.3 Configuración global vs por empresa (bloqueos para dar de alta una cuenta nueva)

| Ítem | Hoy | Para SaaS |
|---|---|---|
| DTE / SII | Se generan TXT para **Acepta** (`app/views_modulo_documentos.py:2340-3146`); no hay integración directa al SII ni certificados/CAF en el sistema. Datos del emisor por `Empresa`; folios por sucursal (`Correlativo`) | Cada cuenta trae **su propio contrato Acepta y su cliente local**. Alta: datos fiscales, rangos de folios, impresoras Acepta. Validar el emisor contra la sesión en `generar_txt_acepta_api` (`:3489-3502`) |
| Correo | SMTP y remitente globales (`settings.py:346-352`, `:388-390`); `SITE_URL` **no está definido** (los links de reseteo apuntan a `localhost:8000`, `users/views.py:521`) | Remitente, nombre, reply-to y URL base **por cuenta** |
| Mercado Pago | Cuenta MP por empresa (bien), pero fallbacks a variables de entorno y un webhook que prueba el secreto de **todas** las cuentas (`app/services/mercadopago_service.py:3142-3195`) | Webhook y secreto por cuenta; quitar fallbacks |
| Transbank POS | Por sucursal + terminal física (`ConfiguracionPOS`) | OK: cada cuenta trae su contrato y terminales |
| API externa / ecommerce | Una API key global (`settings.py:730`) | API key **por cuenta**, ligada a sus empresas |
| AllConnected | Credenciales globales por env (`settings.py:784-800`) | Por cuenta (o módulo solo para tu grupo al inicio) |
| Fidelización / app Flutter | Un programa global, branding Realsport/Paola en el OTP (`app/services/cliente_app_service.py:226-254`), un proyecto FCM | Marca blanca por cuenta (más adelante) |
| IA (Anthropic/Langfuse) | Llaves globales, sin límites ni costo por empresa | Llave de la plataforma + **medición de uso por cuenta** y cuota por plan |
| Archivos | Rutas sin prefijo de empresa (`requerimientos/fotos/…`, `carga_factura/…`, `comprobantes_bancarios/…`); un bucket Spaces compartido con el ecommerce (`settings.py:615-627`) | Prefijo por cuenta + servir por una vista que valide |
| Caché | Prefijos fijos `rm:ventas`, `rm:catalogo` (`settings.py:561`, `:587`); llaves agregadas como `pred:datos_historicos:agg` | Prefijo por cuenta (función de llave de django-tenants) |
| Tareas programadas | `run_scheduler` (cada 5 min / diario) y `views_cron.py` con una llave global; procesan todo | Recorrer cuenta por cuenta |
| Usuarios | `username` y `rut` únicos globales; login por email en toda la base (`retailmind/views.py:402`) | Usuarios por cuenta (cada esquema tiene los suyos) |

**Valores del grupo "hardcodeados" que cambian comportamiento** (hay que convertirlos en
configuración antes de tener otra cuenta):

- Alias de sucursal como reglas: `EDEL`, `GILD`, `IMP`, `PA00` (gestión de productos,
  `app/middleware_permisos.py:659-665`), `PAO1` (retiro ecommerce,
  `app/views_ecommerce.py:4214`, `layout/menu.html:2532`), `EDEL`/`GILD` en emisión DTE
  (`vistas/modulo_documentos/emisionDTE.html:2121`, `:3321`, `:3410`), producto de
  referencia siempre desde `EDEL` (`app/views.py:25123-25136`).
- Banco de depósito por nombre de empresa (`REALSPORT`/`PAOLA`/`EDELMIRA`) en
  `cuadraturaCaja.html:2245-2258` y `revisionArqueos.html:549-553`.
- Choices `REALSPORT`/`PAOLA` en canales de ecommerce (`app/models/ecommerce.py:25-26`) y
  tipos de credencial (`app/models/configuracion.py:27-31`); marca por defecto de gift
  cards `REALSPORT` (`app/views_modulo_giftcards.py:968`).
- Chequeo de depuración `NICK`/`Paola` (`app/views.py:32176-32183`); prompts de IA que
  nombran `NICK1`/`NICK2` y asumen rubro calzado
  (`app/services/carga_factura/lectura.py:684`, `perfiles.py:128-129`,
  `assistant/prompts.py:7-11`).
- Etiquetas Zebra leen la base MySQL legacy "HoldingTebes" (`app/views_etiquetas_zebra.py:50-73`).
- Defaults en settings: correo personal para alertas de folios (`settings.py:397`),
  dominios del despliegue actual.

### 3.4 Opciones evaluadas

| Opción | Aislamiento | Esfuerzo en este código | Veredicto |
|---|---|---|---|
| Columna `cuenta_id` en cada tabla + filtros automáticos (+ RLS de Postgres) | Depende de no olvidar ningún filtro en 1.830 vistas y 161 SQL crudos | Muy alto: ~150 tablas con backfill (605 mil filas solo en `Producto_Talla`), rediseñar las unicidades globales, revisar cada SQL crudo | ❌ |
| Una instancia (servidor + base) por cliente | Total | Bajo en código; alto en operación (N despliegues, N migraciones, N backups) | ⚠️ Solo como puente (§3.11) |
| **Un esquema PostgreSQL por cuenta** (`django-tenants`) | **Total, a nivel de base de datos** | Medio y acotado: settings, middleware, tareas, caché, archivos, tokens | ✅ **Recomendada** |

Por qué el esquema por cuenta calza tan bien con tu pedido:

1. **Tu grupo sigue compartiendo sin tocar nada:** todo lo de hoy (incluidos traspasos
   entre empresas, NC de traspaso, gift cards y puntos válidos en toda la cadena, cuenta MP
   de una empresa usada en sucursal de otra, inventario del holding) queda junto en la
   Cuenta #1.
2. **Las fugas actuales quedan contenidas dentro de cada cuenta:** "el administrador ve
   todas las empresas" pasa a significar "ve todas las empresas **de su cuenta**", que es
   justo lo correcto para un cliente con varias razones sociales.
3. **Las unicidades globales dejan de chocar** (cada esquema tiene su `username`, sus
   códigos, sus correlativos).
4. **No hay que reescribir** las 1.830 vistas ni las 161 consultas SQL crudas (no usan
   `public.` explícito: verificado).

### 3.5 Arquitectura propuesta

```
                        ┌────────────────── PostgreSQL (un servidor, una base) ──────────────────┐
 admin.tudominio.cl ──► │ esquema public     PLATAFORMA: Cuentas · Dominios · Planes · Cobros ·    │
                        │                    Uso mensual · usuarios del staff de plataforma        │
                        ├──────────────────────────────────────────────────────────────────────────┤
 dominio actual ──────► │ esquema <grupo>    CUENTA #1: tus empresas → comparten todo como hoy      │
 cliente1.tudominio ──► │ esquema cliente1   CUENTA #2: sus empresas, sucursales, productos, …       │
 cliente2.tudominio ──► │ esquema cliente2   CUENTA #3: …                                           │
                        └──────────────────────────────────────────────────────────────────────────┘
          Mismo código y mismo servidor. Cada petición trabaja SOLO dentro del esquema de su cuenta.
```

Cómo funciona una petición: el navegador entra a `cliente1.tudominio.cl` → un middleware
busca ese dominio en la tabla `Dominio` → activa el esquema `cliente1`
(`SET search_path`) → la vista corre igual que hoy, pero solo "ve" las tablas de
`cliente1`. Si una vista olvida un filtro, a lo más ve otra empresa **de la misma
cuenta**; nunca otra cuenta.

Detalle técnico (django-tenants):

- **Apps compartidas (`SHARED_APPS`, esquema `public`):** `django_tenants`, nueva app
  `plataforma`, `contenttypes`, `auth`, `users` (para el staff de plataforma), `sessions`,
  `admin`, `messages`, `staticfiles`.
- **Apps por cuenta (`TENANT_APPS`):** `contenttypes`, `auth`, `sessions`, `admin`,
  `users`, `app`, `empresa_management`, `assistant`, `token_blacklist`. Verificado: las
  migraciones de `users`/`assistant`/`empresa_management` no dependen de `app`, así que
  `users` puede vivir en ambos.
- `DATABASES.ENGINE = django_tenants.postgresql_backend`, `DATABASE_ROUTERS`,
  `TenantMainMiddleware` primero en `MIDDLEWARE`, `TENANT_MODEL`,
  `TENANT_DOMAIN_MODEL`, `PUBLIC_SCHEMA_URLCONF`.
- **Caché:** `KEY_FUNCTION = django_tenants.cache.make_key` (+ `REVERSE_KEY_FUNCTION`
  con django-redis).
- **Alta rápida de cuentas:** `TENANT_CREATION_FAKES_MIGRATIONS` + `TENANT_BASE_SCHEMA`
  (clonar un esquema plantilla en vez de correr 245 migraciones por cada cuenta).
- **Despliegue:** el `release` del `Procfile` pasa de `migrate` a `migrate_schemas`.
- ⚠️ **Pool de conexiones:** no usar PgBouncer en modo *transaction* (el `search_path`
  podría quedar en otra conexión). Hoy hay conexiones persistentes directas
  (`conn_max_age=600`, `settings.py:155`): compatible.
- Verificar compatibilidad de la versión elegida con Django 4.2 (el proyecto está en
  4.2.2) — **dependencia nueva: requiere tu aprobación** (CLAUDE.md).

### 3.6 Riesgos que el esquema por cuenta **no** resuelve solo (hay que tratarlos)

Todo lo que no vive en la base de datos sigue siendo compartido:

1. **Tokens JWT** (POS de escritorio, app móvil, app de clientes): se firman con la misma
   `SECRET_KEY`, HS256, y solo llevan `user_id` (`settings.py`, `SIMPLE_JWT`). Como los
   ids de usuario se repiten entre esquemas, **un token de la cuenta A sería aceptado en
   el subdominio de la cuenta B para el usuario con el mismo id**. → Agregar claim
   `cuenta` y validarlo en una clase de autenticación propia (o clave por cuenta).
2. **API keys globales** (`RETAILMIND_API_KEY`, `CRON_TRIGGER_KEY`, secretos de webhooks):
   una sola llave serviría en todos los subdominios. → Tabla de llaves por cuenta.
3. **CSRF entre subdominios:** hay ~55 vistas con sesión marcadas `@csrf_exempt`
   (19 en `users/views.py` —crear usuario, cambiar rol, resetear clave—, 16 en
   `app/views_modulo_ventas.py`, ~13 en `app/views_ecommerce.py`, 4 en
   `empresa_management/views_clientes.py`). Hoy las protege solo `SameSite=Lax`; con
   subdominios del mismo dominio, **una página en `cliente2.tudominio.cl` cuenta como
   "mismo sitio"**. → Reactivar CSRF antes de abrir subdominios.
4. **Archivos** (`MEDIA_ROOT` y Spaces): rutas sin prefijo. → Prefijo por cuenta y servir
   por una vista que valide (la Cuenta #1 mantiene sus rutas actuales).
5. **Caché, logs, correo, tareas programadas:** prefijo/etiqueta de cuenta y recorrer
   cuenta por cuenta (§3.3).
6. `ALLOWED_HOSTS` hoy cae a `'*'` (`settings.py:35`; además `'*.railway.app'` no es un
   comodín válido para Django) → `'.tudominio.cl'`. Con `'*'`, los correos que arman links
   desde el host permiten *Host header poisoning*.
7. **Login con Google:** Google no acepta comodines en los orígenes autorizados → registrar
   cada subdominio o centralizar el login con Google en un dominio único.

### 3.7 Migración de tu grupo a la Cuenta #1 (sin tocar los datos)

1. Ensayo completo sobre una **copia** de producción en staging (las veces que haga falta).
2. Ventana de mantenimiento (p. ej. domingo en la noche, ~1 hora).
3. Backup completo (`pg_dump -Fc`).
4. `CREATE SCHEMA <grupo>;` y mover las tablas de las apps de negocio con
   `ALTER TABLE … SET SCHEMA <grupo>` — **solo cambia metadatos, no copia datos**: es
   casi instantáneo aunque haya cientos de miles de filas.
5. `migrate_schemas --shared` crea las tablas de plataforma en `public`.
6. Registrar `Cuenta #1` con **los dominios actuales** (`retail.webappsolutions.cl` y el de
   DigitalOcean): usuarios, POS de escritorio, Mercado Pago y AllConnected siguen apuntando
   a la misma URL, sin reconfigurar nada.
7. Verificación: conteo de filas por tabla antes/después (idéntico), login, venta de
   prueba, TXT de DTE de prueba, reportes, POS de escritorio, webhook MP.
8. Vuelta atrás: restaurar el backup (o devolver las tablas a `public`).

### 3.8 El "módulo para vender": consola de plataforma

App nueva `plataforma` (esquema `public`), con el mismo design system NEXO, patrón de
includes y vistas FBV del proyecto.

**Modelos**

| Modelo | Para qué |
|---|---|
| `Cuenta` (TenantMixin) | Cliente del software: nombre comercial, RUT de facturación, contacto, estado (`PRUEBA` / `ACTIVA` / `MOROSA` / `SUSPENDIDA` / `CANCELADA`), plan, fechas de alta y fin de prueba |
| `Dominio` (DomainMixin) | Subdominio(s) de la cuenta |
| `Plan` | Precio (UF/mes), límites (empresas, sucursales, usuarios, cajas/dispositivos POS), cuota de IA, módulos incluidos |
| `PlanModulo` | Módulos comerciales del plan, mapeados a `ModuloSistema` / `OpcionMenu` / rutas del catálogo de permisos (`app/permisos_catalogo/`) |
| `Cobro` | Periodo, monto, estado de pago, documento emitido |
| `UsoMensual` | Sucursales y usuarios activos, dispositivos, tickets, DTE generados, tokens de IA, almacenamiento |
| `AccesoSoporte` | Auditoría de cada vez que el staff entra a una cuenta (quién, cuándo, motivo) |

**Pantallas:** listado de cuentas con KPIs (activas, en prueba, morosas, ingreso mensual),
ficha de cuenta (uso, plan, dominios, estado, cobros), **alta de cuenta (asistente)**,
planes, cobros.

**Alta de una cuenta (automática):**

1. En la consola: datos del cliente, plan y subdominio.
2. El sistema crea `Cuenta` + `Dominio` → crea el esquema (clonando la plantilla) →
   carga la **semilla**: menú/módulos, matriz de permisos por rol, taxonomía base de
   categorías (árbol v1.2), atributos (marca/color/género), guías de talla genéricas,
   parámetros por defecto.
3. Crea el primer usuario `maestro` del cliente y le envía la invitación.
4. **Asistente de primera configuración** dentro de la cuenta: empresa(s) emisora(s)
   (RUT, razón social, giro, acteco), sucursales, folios Acepta, impresoras, medios de
   pago, logo, correo remitente.
5. **Importadores** desde Excel con plantillas: productos y stock inicial, clientes,
   proveedores.

**Control de plan (dentro de cada cuenta):**

- Middleware después del de cuentas:
  - `SUSPENDIDA` → página de bloqueo (o solo lectura);
  - `MOROSA` → aviso con días de gracia;
  - ruta de un módulo no contratado → página "módulo no incluido en tu plan".
- El menú (`layout/menu.html`) oculta los módulos no contratados.
- Límites al crear sucursal / usuario / dispositivo POS.
- El plan vive en `public`: el administrador de una cuenta **no puede** auto-habilitarse
  módulos.

**Ejemplo de planes** (a definir por ti):

| Plan | Incluye |
|---|---|
| Esencial | POS y ventas, caja y arqueos, existencias y traspasos, documentos DTE (Acepta), clientes, reportes básicos |
| Profesional | + compras y recepción, requerimientos, cotizaciones, créditos, gift cards y cupones, dashboards y reportes completos, etiquetas Zebra, Mercado Pago |
| Full | + predicción e inteligencia de compra, asistente IA (con cuota), ecommerce, fidelización/app (a medida) |

**Cobro:** al principio **manual** (factura mensual —puedes emitirla desde tu propio
sistema— y la consola marca el estado). Automatizar con una pasarela de suscripciones
cuando haya más de ~10 cuentas.

### 3.9 Cambios de código por fase

**B0 — Preparación (sirve para cualquier camino, incluido el plan puente)**

- Convertir los valores hardcodeados de §3.3 en configuración. Para las sucursales ya
  existe `es_centro_distribucion`; faltan flags como "puede gestionar productos",
  "punto de retiro ecommerce" y "fuente del catálogo de referencia". Para las empresas:
  banco de depósito por defecto y marca de correos.
- `Empresa.es_propia` para distinguir empresas propias de proveedores y clientes. Hoy
  la única lista de empresas propias es `RUTS_EMPRESAS_PROPIAS`, en un comando de
  migración legacy.
- Configuración por cuenta que hoy vive en variables de entorno: URL base (`SITE_URL`),
  remitente de correo, destinatarios de alertas, credenciales AllConnected y API keys.
- Seguridad cruzada de §3.6: claim `cuenta` en los JWT, API keys por cuenta y reactivar
  CSRF en las ~55 vistas con `@csrf_exempt`.
- Recomendado igual, aunque dentro de una cuenta solo afecta a tu grupo:
  - validar la sucursal en la API sync y en el login de escritorio;
  - corregir los 129 accesos por id sin validar, empezando por los de mayor tráfico;
  - asignar la empresa activa con un orden determinista (hoy es `.first()` sin
    `order_by`).
- **Tests sobre PostgreSQL + CI:** hoy hay 175 archivos de test, unos 3.600 métodos y
  ningún CI. `test_settings_sqlite.py` no sirve para esquemas y parte del código ya es
  solo-Postgres. Montar un CI con un Postgres de servicio.
- **Staging** con una copia de producción.

**B1 — Esquema por cuenta (en staging)**

- `django-tenants` y app `plataforma` con `Cuenta`/`Dominio`; settings de §3.5.
- Caché con llave por cuenta. Revisar las llaves "agregadas" que mezclan todo.
- Archivos con prefijo por cuenta; la Cuenta #1 conserva sus rutas.
- `run_scheduler`, `views_cron.py` y los comandos programados recorren las cuentas con
  `schema_context`. Para ejecutarlos a mano: `tenant_command` / `all_tenants_command`.
- Script de §3.7 y ensayo con conteo de filas.
- Adaptar el runner de tests: los tests nuevos de aislamiento usan dos cuentas reales.

**B2 — Consola de plataforma** (§3.8).

**B3 — Integraciones por cuenta**

| Integración | Qué hacer |
|---|---|
| DTE (Acepta) | Pasos de alta y validación del emisor |
| Correo | Identidad del remitente y links con el subdominio de cada cuenta |
| Mercado Pago | Webhook y secreto por cuenta; quitar los fallbacks a variables de entorno |
| POS de escritorio (Tauri) | Configurar la URL o el "código de cuenta" en la primera instalación. Si la URL está fija en el cliente, agregar esa pantalla |
| IA | Medición de tokens y costo por cuenta, cuota por plan, etiqueta de cuenta en Langfuse |
| AllConnected y app de fidelización | Quedan como módulos de tu grupo al inicio. Marca blanca después |

**B4 — Producción**

- Dominio comodín `*.tudominio.cl` con certificado. Verificar que lo soporte el hosting,
  DigitalOcean App Platform o Railway.
- Ventana de migración (§3.7).
- Crear una cuenta "demo" interna y luego el primer cliente piloto.
- Backups por cuenta (`pg_dump -n <esquema>`), que sirven también para entregarle sus
  datos a un cliente que se va.

**B5 — Comercial / legal**

- Documentos:
  - contrato SaaS (precio en UF, soporte, SLA, permanencia);
  - acuerdo de tratamiento de datos (tú pasas a ser *encargado* de los datos de los
    clientes finales de cada cuenta);
  - política de privacidad.
- La nueva **Ley 21.719** de datos personales tiene vigencia programada para el
  **1-dic-2026**. En septiembre de 2026 ingresó un proyecto para postergarla a dic-2027:
  revisarlo con un abogado.

### 3.10 Pruebas de aislamiento (criterio de "listo")

- **Prueba canario:** se crean dos cuentas y se llena la B con textos marcados
  (`CANARIO-B-…` en productos, clientes, proveedores y documentos). Luego se recorren
  todas las rutas GET de la A con un usuario `maestro` y la prueba falla si **cualquier**
  respuesta contiene `CANARIO-B`.
- **Tokens cruzados:** un JWT de A contra un subdominio de B debe dar 401. Lo mismo con
  las API keys y las cookies.
- **Tareas:** con dos cuentas, el scheduler procesa cada una sin mezclar resultados.
- **Caché y archivos:** la misma llave lógica en dos cuentas no colisiona, y un archivo de
  B no se puede descargar desde A.
- Estas pruebas corren en CI en cada push.

### 3.11 Plan puente (si aparece un cliente antes de tiempo)

Después de la Fase 0 y B0 (sin valores hardcodeados del grupo) se le puede montar una
**instancia separada**: otro servicio y otra base, con el mismo código. Cuando B4 esté
listo, su base se importa como un esquema más (`pg_dump` → restore en `cliente_x`) y se
apaga la instancia. No conviene como solución final porque cada cliente suma un
despliegue, una migración y un backup que mantener.

---

## 4. Decisiones que necesito de ti

1. **¿Apruebas `django-tenants`** como dependencia nueva? Es la base de la recomendación.
2. **Dominio y marca** para las cuentas: `*.tudominio.cl`, y si el producto se vende como
   NEXO, RetailMind u otro nombre.
3. **Qué empresas forman la Cuenta #1.** Tú hablas de 2 empresas; en el código aparecen 4
   RUT propios: Vicent Paola, Importadora Nicolas, Edelmira Tebes y Cía. y Edelmira Gilda
   Tebes (`app/management/commands/migrate_from_laravel.py:66`). Con la recomendación, todas
   quedan juntas en la Cuenta #1 y siguen compartiendo.
4. **Qué módulos se venden y en qué planes.** ¿Ecommerce/AllConnected y la app de
   fidelización quedan solo para tu grupo al inicio?
5. **Cobro:** manual al principio (recomendado) o automático desde el día 1.
6. **Fase 0:** ¿autorizas que prepare el commit que deja de versionar `.env` y los
   archivos de datos? La purga del historial necesita además tu confirmación explícita
   para el `push --force`.
7. **Modo kiosko:** ¿en producción está definido `POS_KIOSK_DEFAULT`? Si no lo está, el
   estilo táctil del POS se aplica a todos los usuarios. Recomiendo dejarlo en `False` y
   activarlo solo en los equipos POS.
8. **POS en tablet vertical:** ¿adaptarlo (columnas apiladas) o mostrar un aviso de "usar
   en horizontal"? Adaptarlo cuesta ~1 semana más.
