# Plan: Gestión DTE — iconos de acciones + Reasignar sucursal destino de traspasos

Fecha: 2026-07-29. Estado: IMPLEMENTADO (Fases 1-4). Pendiente del usuario:
`makemigrations` (choice REASIGNACION_DESTINO, metadata-only) y correr la
suite `app.tests.test_reasignar_destino_dte`.

## Contexto técnico (verificado en código)

- El destino de un traspaso NO es un campo del `Dte`: se deriva de los
  `Movimientos_Producto` con `concepto='TRASPASO_SALIDA'` → `sucursal_destino`.
  Fuente única: `_sucursal_destino_traspaso()` (views.py:166). La lista de
  recepciones pendientes filtra por `dte_movimientos__sucursal_destino_id`
  (views.py:279) y `confirmar_recepcion_api` valida fail-closed que la sucursal
  ACTIVA de la sesión sea ese destino (views.py:661-704). El stock entra a la
  sucursal activa, replicando fichas/tallas por SKU si no existen.
- `Dte.receptor` es la **Empresa** (RUT del documento SII). Reasignar entre
  sucursales del mismo RUT receptor = logística; cambiar de empresa = otro
  receptor legal → requiere NC + re-emisión (fuera de alcance del botón).
- gestion-dte (`vistas/modulo_administracion/gestion_dte.html`) ya expone
  `dte.sucursal_destino` en su serializer (botón "Crear stock destino") y tiene
  9 botones de acción solo-ícono con `title` (poco entendibles).

## Fase 1 — Backend: endpoint "Reasignar destino de traspaso"

`POST /app/dte/reasignar_destino_traspaso/` con `{dte_id, nueva_sucursal_id, motivo}`.

Validaciones (todas fail-closed):
1. `tipo_transaccion='TRASPASO'` y estado en `EMITIDO`/`ACEPTADO` (aún no recepcionado).
2. Sin `Productos_Recepcionados`, sin NC/ajustes hijos del DTE.
3. Tiene movimiento(s) `TRASPASO_SALIDA` con destino (si no: usar primero
   "Diagnosticar/reparar trazabilidad", ya existente).
4. `nueva_sucursal` ≠ origen, ≠ destino actual, y **misma Empresa (RUT) que el
   receptor actual**. Si el RUT difiere → 400 con mensaje "requiere anular y
   re-emitir".
5. Permiso: emisor con `_puede_ajustar_dte_emisor` (mismo gate que los ajustes
   de emitidos) o admin. `select_for_update` del Dte (patrón confirmar_recepcion).

Efectos (transacción atómica):
- `UPDATE` de TODOS los movimientos `TRASPASO_SALIDA` del DTE →
  `sucursal_destino = nueva` (via `.update()`, sin señales).
- **Trazabilidad en movimientos** (pedido explícito): se crea 1 movimiento
  documental `cantidad=0`, `concepto='REASIGNACION_DESTINO'`,
  `sucursal_origen=destino_anterior`, `sucursal_destino=destino_nuevo`,
  `responsable=usuario`, `observaciones="Traspaso #N reasignado PAO2→PAO4 — motivo"`.
  `ProductoTalla` = primera línea activa del DTE (el FK es NOT NULL).
  NOTA: agregar el choice `REASIGNACION_DESTINO` a `CONCEPTO_MOVIMIENTO_CHOICES`
  genera una migración metadata-only → **avisar antes de migrar** (regla del
  proyecto; hay 0154/0155 pendientes).
- Append en `Dte.referencias`: `"[REASIGNADO] PAO2→PAO4 DD/MM/AAAA usuario: motivo"`.
- (Opcional) notificación al nuevo destino vía el circuito de notificaciones DTE.

## Fase 2 — UI gestion-dte

- **Botón "Reasignar destino"** (ícono `ri-arrow-left-right-line`), visible solo
  en traspasos EMITIDO/ACEPTADO. Modal: destino actual → select de sucursales de
  la misma empresa receptora + motivo obligatorio + resumen de confirmación.
  Deshabilitado con tooltip explicativo cuando ya está recepcionado
  ("usar Ajustar emitido / nuevo traspaso") o no hay mov de salida.
- **Badge de destino actual** en la fila de traspasos (dato ya disponible).
- **Iconos de acciones entendibles**: dejar visibles como botones las 3-4
  acciones frecuentes con ícono + etiqueta corta (Detalle, Pago, TXT, NC) y
  agrupar el resto en un dropdown "Más ▾" donde cada ítem lleva ícono + texto
  completo (Editar folio NC, Asignar receptor, Crear stock destino, Diagnóstico,
  Trazabilidad, Reasignar destino). Tooltips en todos, `aria-label`, colores
  según semántica NEXO (info/success/warning/danger). Sin librerías nuevas
  (Bootstrap 5 dropdown ya disponible).

## Fase 3 — recepcion-dte (efecto y visibilidad)

- Automático por diseño: al reasignar, PAO4 ve el pendiente (el filtro lee
  `sucursal_destino` del movimiento), PAO2 deja de verlo, y `confirmar_recepcion`
  ingresa el stock a la sucursal ACTIVA (= PAO4, validada contra el movimiento).
  **Cero cambios en la lógica de recepción.**
- Mejora de visibilidad: en el detalle del DTE en recepción, mostrar aviso
  "Destino reasignado desde PAO2 el DD/MM (usuario)" leyendo el movimiento
  `REASIGNACION_DESTINO`.

## Fase 4 — casos borde + tests

- Ya recepcionado en la sucursal equivocada → NO aplica reasignar (el stock ya
  entró): flujo existente `corregir_recepcion_emisor` / nuevo traspaso. El botón
  lo explica en tooltip.
- Legacy sin `TRASPASO_SALIDA` o sin destino → bloqueado (mismo fail-closed).
- Tests (`app/tests/test_reasignar_destino_dte.py`, factories existentes):
  emitir EDEL→PAO2, reasignar a PAO4 (OK + kardex con mov documental),
  recepcionar como PAO4 (stock entra a PAO4), como PAO2 (403),
  reasignar tras recepción parcial (400), cross-empresa (400), sin permiso (403).

## Archivos a tocar

| Archivo | Cambio |
|---|---|
| `app/views.py` (o `views_modulo_documentos.py`) | endpoint reasignar + nota en detalle recepción |
| `app/urls.py` | ruta plana (patrón existente) |
| `app/templates/vistas/modulo_administracion/gestion_dte.html` | modal + rediseño botonera |
| `app/models/inventario.py` | choice `REASIGNACION_DESTINO` (+ migración metadata-only, avisar) |
| `app/tests/test_reasignar_destino_dte.py` | suite nueva |

Orden sugerido: Fase 1 → 2 → 3 → 4. Estimación: 1 sesión de trabajo + tests.
