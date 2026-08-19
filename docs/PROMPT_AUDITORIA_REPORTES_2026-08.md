# PROMPT · Auditoría integral del módulo de Reportes — SistemaRetailMind

> Uso: pegar TODO este documento como prompt en una sesión nueva de Claude Code
> abierta en la raíz del repo (`SistemaRetailMind/`). Si quieres fan-out
> multi-agente, anteponer: "usa un workflow para esto".

---

## Rol y pregunta central

Eres auditor del módulo de **Reportes** de SistemaRetailMind (ERP Django 4.2 /
PostgreSQL, retail chileno multi-empresa). La pregunta que debes responder,
reporte por reporte, es una sola:

> **¿La información que muestra este reporte es CORRECTA?**

Cada KPI, total, fila, gráfico y export (Excel/PDF) debe cuadrar contra un
**oráculo**: una consulta SQL/ORM independiente escrita por ti, sobre la misma
BD, que calcule el mismo número por otro camino. Si el reporte dice $X y el
oráculo dice $Y, documenta la diferencia con el dato medido (no estimado).
Secundariamente: permisos/scoping multi-empresa, rendimiento y honestidad de
rótulos (que el KPI se llame como lo que realmente mide).

**No es la primera auditoría.** Hubo dos en julio 2026 (más una de dashboards)
con fixes aplicados y backlog abierto. Tu trabajo NO es repetirlas, sino:
(a) verificar qué fixes siguen vivos en el código actual y qué backlog sigue
abierto, (b) auditar a fondo lo NUEVO que nunca se auditó, (c) re-verificar los
números HOY, con datos de julio-agosto 2026.

---

## REGLA DE ORO — la BD por defecto es PRODUCCIÓN

El `.env` del repo apunta a la base de datos **productiva**. Por lo tanto:

- **Solo lecturas.** SELECT / ORM sin `save()`, `update()`, `delete()`,
  `create()`, `bulk_*`. Nada de escrituras "inofensivas".
- **PROHIBIDO**: `migrate`, `makemigrations` (aplicar), `flush`, `runserver`,
  `collectstatic`, cualquier comando `clean_*` / `_limpiar_*`, y los scripts
  sueltos `_fix_*.py` / `_reconciliacion_*.py`.
- **Tests SOLO con BD desechable forzada en la misma línea**:
  `$env:DATABASE_URL="sqlite:///C:/temp/t.sqlite3"; python manage.py test ...`
- Scripts de verificación: en el scratchpad de la sesión, con guarda
  anti-escritura al estilo de `retailmind/_test_reportes_readonly.py`
  (esa suite ya existe: RequestFactory + sesión simulada + oráculos;
  flags `--confirmo-prod --rapido --solo a,b,c`). Reúsala y extiéndela.
- Comandos que YO deba correr (deploy, commit, migrar, probar en navegador):
  entrégalos como lista copy-paste al final, no los ejecutes tú.

---

## Lee PRIMERO (para no partir de cero)

1. `CLAUDE.md` (raíz) — convenciones duras del proyecto.
2. `docs/AUDITORIA_MODULO_REPORTES_2026-07.md` — auditoría 22-jul, fixes F-01..F-18.
3. `docs/PLAN_REPORTES_2026-07-25.md` + `docs/ANEXO_REPORTES_2026-07-25.md` —
   auditoría 25-jul: 34 reportes, veredicto por reporte, 70 problemas, backlog P0-P3.
4. `retailmind/_test_reportes_readonly.py` — suite de regresión existente.
5. `retailmind/app/middleware_permisos.py` — `URL_PERMISO_MAP` y cómo matchea.

Trata lo que digan esos documentos como **hipótesis, no como hechos**: verifica
contra el código actual antes de afirmar nada (hubo muchos cambios sin commitear
y sesiones posteriores).

---

## Alcance — inventario a auditar

Código: `retailmind/app/views_modulo_reportes.py` (~11.000 líneas),
`views_modulo_reportes_diferencias.py`, `views_modulo_reportes_tallas.py`,
`views_resumen_existencias.py`, `views_inteligencia_compra.py`, y los reportes
legacy que viven en `views.py` (kardex, FIFO, despachos por proveedor,
existencias). Rutas en `app/urls.py` bajo `# === REPORTES ===` y bloques
`reportes/...`.

**A. Nunca auditados o posteriores a las auditorías de julio (prioridad máxima):**
1. `reportes/quiebre-talla/` + API (nuevo 05-ago; reconstruye disponibilidad desde kardex)
2. `reportes/diferencias-recepcion/` + API (nuevo; además verificar que ya no dé 403 a todos — dependía de `inicializar_permisos` pendiente al 05-ago)
3. `reportes/mercaderia-transito/` + API + detalle (mismo caso)
4. `reportes/ventas-internet/` + API + export
5. `reportes/productos-origen/` + API
6. `api/reportes/comisiones-vendedor/` + export
7. `reportes/movimientos-sucursal/` — re-verificar tras la columna Descripción opcional (13-ago)
8. `resumen-existencias` — re-verificar tras el vaciado de EDEL FALLADOS/NICK3 y la exclusión analítica de 772 productos (14-ago): esas 2 sucursales NO deben aparecer con corte actual, y con corte histórico la vista debe ser coherente con la exclusión
9. `inteligencia-compra` y `plan-liquidacion` (+ campañas): auditados solo de pasada; verificar GMROI/WOS/dead-stock contra oráculo y que usen `constants_kardex`

**B. Re-verificación de lo ya auditado (delta + números de hoy):**
ventas-sucursal (y diagnóstico-cuadratura), ventas-global, ventas-comparativo,
productos-vendidos (+comparativa-mensual, documentos-vendedor),
documentos-emitidos, compras (reporte integral + rendimiento-compras),
rendimiento-proveedor, recepciones/despachos-detallado, existencias-marca,
existencias-sucursal, despachos-tiendas, y los legacy de `views.py`
(movimientos_kardex, kardex_agrupado, fifo_general, despachos_por_proveedor,
`reportes/existencias/`).

**C. Exports:** cada Excel/PDF debe traer LOS MISMOS números que la pantalla
(mismos filtros, mismo universo) y respetar los mismos permisos que la API.

---

## Checklist transversal — las trampas conocidas de ESTE sistema

Aplica esta lista a CADA reporte; son bugs reales ya encontrados aquí:

1. **Fechas**: usar `created_at`, NUNCA `Ticket.fecha` (es `auto_now`, se pisa).
   Timezone América/Santiago con `USE_TZ` — cuidado con cortes de día.
2. **NC de venta**: se emiten como `tipo_transaccion` DEVOLUCION/ANULACION (no
   existe intersección con `tipo_documento='NOTA DE CREDITO'` + VENTA). Un
   "neto" que no resta NC miente con plata.
3. **Anti-doble-conteo POS**: excluir `dte_generado=True` donde corresponda
   (la venta POS + su DTE son EL MISMO peso).
4. **`BOLETA PAPEL`**: ~21% de las boletas; verificar si el universo del
   reporte las incluye o excluye, y que el rótulo lo diga.
5. **`excluir_de_analitica`**: qué filas excluye cada reporte y si el total y
   el detalle usan el MISMO filtro (hubo modales vacíos por un `elif`).
6. **Saldo de apertura de la migración**: movimientos INGRESO_INICIAL /
   MIGRACION_LARAVEL (~2026-01-22) se cuentan DOBLE si se suman junto al
   kardex legacy. `SUM(movimientos) != stock` es esperable; los saldos deben
   excluir la apertura.
7. **`TRASPASO_SUCURSAL` legacy**: miles de movimientos en 2026 que varios
   reportes no ven. Traspasos reales = INGRESO/EGRESO detectados por concepto
   (`constants_kardex`), y regla despachos = `TRASPASO_SALIDA` + `es_compradora` + abs.
8. **Ambos lados del mismo universo**: toda resta/división (margen, ROI,
   cobertura, %) debe tener numerador y denominador con la MISMA sucursal, el
   MISMO filtro analítica y el MISMO tratamiento de IVA. (El "ROI 19%" era la
   tasa de IVA; el "margen 608%" era red-completa vs una-sucursal.)
9. **IVA y redondeo**: `monto_con_iva` puede traer decimales; redondear
   half-up, nunca `int()`. No comparar neto contra bruto (facturas vs boletas).
10. **`Count` con joins reversos**: `.distinct()` del queryset NO salva la
    agregación; usar `Count('x', distinct=True)` (despachos inflados x30).
11. **`estado_pago`**: comparación case-sensitive ('Pendiente' vs 'PENDIENTE'),
    'VENCIDO' no lo escribe nadie, boletas POS nacen PENDIENTE, y compras
    legacy tienen `sucursal=NULL` (un filtro por sucursal las borra).
12. **Permisos**: la página puede estar en `URL_PERMISO_MAP` pero la API no —
    el segmento `/api/` rompe el match por substring → JSON abierto a
    cualquier autenticado. Revisar página + API + export, endpoint por
    endpoint. OJO: NO proponer mapear el prefijo `/app/api/ventas/` completo
    (ahí viven endpoints que usa el cajero).
13. **Scoping multi-empresa**: todo queryset debe intersectarse con
    EmpresaUser/alcance del usuario; probar con usuario no-admin de otra
    empresa (403 o vacío, jamás datos ajenos). Incluye los exports.
14. **Atributos v1.2**: categorías = árbol padre/hija v1.2 (expandir rama al
    filtrar por padre); `atributo3` = Género; `atributo4` = columna muerta.
15. **Rendimiento**: medir queries y tiempo por endpoint (patrón
    `benchmark_ventas` / CaptureQueriesContext). Umbral de alerta: >50 queries
    o >5s. Antecedentes: compras-integral 9.000q/44min, FIFO general se cae,
    rendimiento-proveedor atribuye ventas por match de TEXTO (ceros falsos).
16. **Sucursales**: EDEL (id 1) es bodega/CD y abastece a MÁS de una empresa;
    EDEL FALLADOS (id 11) ≠ EDEL; NICK4 no existe; `es_compradora` es property
    de Python (replicar en SQL como `es_centro_distribucion` OR
    `tipo_sucursal='CENTRO_DISTRIBUCION'`).

---

## Metodología — fases

**Fase 0 · Delta vs julio.** Con el código actual en mano, tabla de estado:
cada fix de las auditorías previas (F-01..F-18 + los 4 del 25-jul) →
¿sigue aplicado? ¿se commiteó? Cada ítem del backlog (P0 pendiente de
compras-integral, documentos-por-vendedor, productos-vendidos, F-12/F-13/F-14/
F-17, P1 de permisos, P3 de eliminar redundantes) → ¿sigue abierto?

**Fase 1 · Ficha por reporte.** Para cada pantalla/API del inventario:
- Qué DICE medir (rótulos de UI) vs qué MIDE (código): fuente de datos,
  filtros, universo, joins.
- Checklist transversal (los 16 puntos) con veredicto por punto.
- Permisos: página, API y export en `URL_PERMISO_MAP` + scoping empresa.
- Rendimiento: nº queries + tiempo con datos reales.

**Fase 2 · Veracidad contra oráculos (lo central).** Para los KPIs con plata:
elegir un período cerrado (ej. julio 2026) y una sucursal concreta, calcular
el oráculo por camino independiente, y comparar al peso. Documentar
`reporte=$X / oráculo=$Y / delta / causa`. Reusar los oráculos de
`_test_reportes_readonly.py` y agregar los que falten (dejar la suite
actualizada como entregable).

**Fase 3 · Consistencia entre reportes.** Los totales que deberían coincidir
entre pantallas (ventas-global vs ventas-sucursal vs cuadratura vs
documentos-emitidos; existencias-sucursal vs resumen-existencias vs kardex)
— cuadrar o explicar la divergencia por diseño (ya hay una documentada:
cuadratura-vs-reporte).

**Fase 4 · Informe y plan.** Ver "Entregables".

---

## Severidad

- **P0**: número que miente con plata (KPI/total incorrecto medido), fuga de
  datos entre empresas, o reporte roto (500 / 403 para todos).
- **P1**: API/export sin permiso o sin scoping, filtros muertos, doble conteo
  acotado, export que difiere de la pantalla.
- **P2**: rendimiento (N+1, timeouts), fragilidad (except que tapa FieldError).
- **P3**: redundancia entre reportes, rótulos confusos, mejoras de indicadores.

## Entregables

1. `docs/AUDITORIA_REPORTES_2026-08.md` — informe: tabla resumen con veredicto
   por reporte (OK / MIENTE / ROTO / RIESGO / REDUNDANTE), fichas, y cada
   hallazgo con evidencia medida (números reales, no "podría").
2. Plan priorizado P0→P3 con esfuerzo estimado por fix.
3. `retailmind/_test_reportes_readonly.py` extendida con los oráculos nuevos.
4. Lista copy-paste de comandos que me tocan a mí (correr suite, probar en
   navegador, commit) — SIEMPRE al final de tu respuesta.

**NO apliques fixes en esta pasada.** Primero el informe; los fixes se
acuerdan y aplican después con mi OK, empezando por los P0.

## Qué NO hacer

- Nada de escrituras en la BD (regla de oro).
- No "arreglar de pasada" código mientras auditas.
- No inventar estimaciones: si un número no se pudo medir, decir "no medido" y
  por qué.
- No auditar dashboards (`dashboard_*`) ni módulos vecinos salvo para cruzar
  totales — ya tienen su plan propio (`docs/PLAN_DASHBOARDS_2026-07-30.md`).
- No tocar settings, requirements ni migraciones.
