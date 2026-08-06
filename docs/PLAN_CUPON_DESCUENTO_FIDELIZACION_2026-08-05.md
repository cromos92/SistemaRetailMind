# Plan — Cupón de descuento nominativo (5%) para cliente identificado en el POS

**Fecha:** 2026-08-05
**Objetivo:** emitir desde **Fidelización** un código de descuento **único por cliente** que
el cliente presenta en caja para obtener un 5%. Sólo aplica si la venta se identifica con el
RUT del **mismo cliente** dueño del cupón.

---

## 0. Parámetros definidos (decisión del usuario, 2026-08-05)

| # | Parámetro | Decisión |
|---|---|---|
| **P1** | Naturaleza del código | **Único por cliente.** Nominativo, un solo uso. No hay códigos públicos reusables. |
| **P2** | Dónde se crea | **Menú Fidelización**, sobre un cliente concreto (ficha) o por lote sobre un segmento. |
| **P3** | Alcance | **Por empresa.** El cupón nace atado a la empresa que lo emite y sólo se canjea en sucursales de esa empresa. |
| **P4** | Requisito del cliente | **Requiere ficha de cliente en el CRM.** Sin ficha no hay cupón — se cumple por construcción (se emite *desde* la ficha) y se revalida en caja por RUT. |
| **P5** | Stacking con descuento manual | **Excluyente.** Si el cajero aplicó descuento manual (línea o global), el cupón se rechaza; y con el cupón validado, los botones de descuento se bloquean. |
| **P6** | Stacking con vale de puntos | **Excluyente.** O cupón, o vale de puntos. Nunca los dos en la misma venta. |

**Regla única que resume P5 + P6 — "el cupón no se combina con nada":**

> En una venta hay **un solo beneficio**: o el descuento manual del cajero, o el vale de
> puntos, o el cupón. El cupón es excluyente con los otros dos.

Se implementa como **regla dura del servicio, no como flag configurable**: un checkbox que un
día alguien destilda sería la forma más barata de regalar 5% + 5% + descuento manual sin que
nadie se entere. La combinación descuento manual + vale de puntos, que hoy sí se permite,
**no se toca** (está fuera del alcance de este cambio).

Consecuencia de P1 + P4: el cupón nominativo **hace innecesaria** buena parte del andamiaje de
un cupón público (contadores de usos, usos por cliente, control de fraude por RUT repetido).
El modelo se simplifica y queda calcado de `CanjeVale`, que ya existe y está probado.

---

## 1. Análisis del flujo de cobro actual (`/app/pos-dashboard/`)

### 1.1 Anatomía

| Pieza | Ubicación |
|---|---|
| Vista | `pos_dashboard` — [views_modulo_ventas.py:1708](../retailmind/app/views_modulo_ventas.py#L1708) |
| Template | [generacionVentas.html](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html) (12.807 líneas, JS en closure) |
| Endpoint de cobro | `registrar_pagos_ticket` — [views_modulo_ventas.py:3529](../retailmind/app/views_modulo_ventas.py#L3529) |
| Regla de elegibilidad | `venta_fideliza()` — [fidelizacion_service.py:88](../retailmind/app/services/fidelizacion_service.py#L88) |
| DTE / TXT Acepta | [views_modulo_documentos.py:3307-3334](../retailmind/app/views_modulo_documentos.py#L3307) |

El POS es un **wizard de 4 pasos** dentro de un solo template:

1. **Paso 1** — ticket / correlativo.
2. **Paso 2** — cliente + tipo de documento. `buscarClientePorRut()`
   ([línea 6405](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L6405))
   consulta `/app/buscar-cliente-rut/`, puebla `clienteActual` y `fidelizacionActual`.
   El botón **Cliente Genérico** ([línea 6500](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L6500))
   carga RUT `66666666-6` con datos dummy (`generico@test.cl`, celular `999999999`,
   nacimiento `2000-01-01`).
3. **Paso 3** — carrito, descuentos, fidelización y pagos (zona `pos-checkout-scroll`,
   [línea 744](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L744)):
   tarjeta de fidelización → **Vale de Puntos** → métodos de pago.
4. **Paso 4** — confirmación → `POST` a `registrar_pagos_ticket`.

### 1.2 Los tres descuentos que ya existen

| Descuento | Dónde se calcula | Dónde se guarda | Límite |
|---|---|---|---|
| **Por línea** (`mostrarModalDescuento`) | Frontend, sobre `descuento_unitario` de cada producto | `Ticket_Producto.descuento_unitario` | `limite_descuento_rol` ([views_modulo_ventas.py:1730](../retailmind/app/views_modulo_ventas.py#L1730)) + password |
| **Global distribuido** (`mostrarModalDescuentoGlobal`) | Frontend, repartido proporcional entre líneas | idem (líneas) | idem |
| **Vale de puntos** (`codigo_vale_canje`) | **Backend**, sobre `ticket.total` | `Ticket.descuento_fidelizacion` (cabecera) | Saldo de puntos |

El backend es **server-authoritative** con las líneas
([views_modulo_ventas.py:4219-4235](../retailmind/app/views_modulo_ventas.py#L4219)):
`ticket.descuento` y `ticket.total` se recalculan siempre desde `Ticket_Producto`.

> **P5 se apoya en un dato ya disponible:** "hay descuento manual" se detecta con
> `any(tp.descuento_unitario > 0)` sobre las líneas del ticket — el mismo criterio que ya usa
> el frontend en [línea 4091](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L4091).

### 1.3 El patrón "Vale de Puntos" — es el molde exacto

Ya existe end-to-end el flujo "código que el cliente presenta en caja y rebaja el total".
El cupón debe **clonar estos 6 puntos de enganche**:

```
UI (paso 3)          card #valeCanjeCard  ........... generacionVentas.html:776-797
JS validar           validarVale() → GET /app/api/fidelizacion/vale/<codigo>/ ... :4931
JS estado            valeCanjeActual + fila "Desc. Puntos" en resumen ......... :5004
JS payload           codigo_vale_canje ...................................... :8701
Backend validar      venta_fideliza() + validar_vale() → ticket.total -= X ... views_modulo_ventas.py:4237-4285
Backend guard        VALIDAR_COBERTURA_PAGOS usa el total YA rebajado ....... :4298
Backend consumir     canjear_vale() DESPUÉS de ticket_se_pago ............... :4468
DTE                  línea DR "Descuento Puntos Fidelizacion" .............. views_modulo_documentos.py:3321
Cuadratura           total_descuento_puntos ............................... views_modulo_ventas.py:8048
```

### 1.4 La regla "no genérico / no empresa" YA está resuelta

`venta_fideliza(ticket, tipo_documento, cotizacion)` →
[fidelizacion_service.py:88](../retailmind/app/services/fidelizacion_service.py#L88)
es la **fuente única de verdad** y rechaza: cotización, `FACTURA_ELECTRONICA`, ticket sin RUT,
RUT genérico `66666666-6` (comparación **normalizada**), RUT inválido, RUT de empresa (≥50M).
**Se reutiliza tal cual.**

Con P1 (nominativo) se le suma un gate más fuerte: el RUT del ticket debe ser **el del dueño
del cupón**. Eso hace imposible usarlo con el genérico, aunque `venta_fideliza` cambiara.

---

## 2. Qué NO sirve reutilizar

- **`CanjeVale`** ([fidelizacion.py:690](../retailmind/app/models/fidelizacion.py#L690)) — mismo
  *patrón*, distinta *naturaleza*: está respaldado por puntos del cliente. El cupón es margen
  que regala la empresa. **Se copia la estructura, no el modelo.**
- **`CampanaLiquidacion`** ([precios.py:381](../retailmind/app/models/precios.py#L381)) —
  descuento por producto, sin código ni cliente.
- **`GiftCard`** — medio de **pago**, entra por `TicketDetallePago`.

---

## 3. Diseño

### 3.1 Dos modelos nuevos → `app/models/fidelizacion.py` (re-exportar en `models/__init__.py`)

**Por qué dos y no uno:** emitir 500 cupones al 5% con los parámetros repetidos a mano es
inviable, y si mañana quieres cambiar la vigencia tendrías que editar 500 filas. La campaña es
la plantilla; el cupón es la instancia nominativa. Es la misma relación que
`ProgramaFidelizacion` ↔ `CuentaPuntos` que el módulo ya usa.

```python
TIPO_VALOR_CUPON_CHOICES = [('PORCENTAJE', '% sobre el total'), ('MONTO', 'Monto fijo ($)')]

class CampanaCupon(models.Model):
    """Plantilla de emisión. Define QUÉ descuento y bajo qué condiciones."""
    nombre        = CharField(max_length=80)
    descripcion   = TextField(blank=True)

    # P3 — alcance por empresa. NO nullable: un cupón siempre tiene dueño de cadena.
    empresa       = FK(Empresa, on_delete=PROTECT, related_name='campanas_cupon')

    tipo_valor    = CharField(choices=TIPO_VALOR_CUPON_CHOICES, default='PORCENTAJE')
    valor         = DecimalField(max_digits=7, decimal_places=2)   # 5.00 = 5%
    tope_descuento = IntegerField(null=True, blank=True)           # techo en $ para %
    monto_minimo  = IntegerField(default=0)                        # compra mínima

    vigencia_dias = IntegerField(default=30)      # define expira_en de cada cupón emitido
    fecha_inicio  = DateField(db_index=True)      # ventana de EMISIÓN de la campaña
    fecha_fin     = DateField(db_index=True)      # inclusive
    activo        = BooleanField(default=True, db_index=True)

    # P1 — un solo cupón vivo por cliente y campaña.
    uno_vivo_por_cliente = BooleanField(default=True)

    # OJO: P5 y P6 (excluyente con descuento manual y con vale de puntos) NO son
    # campos. Son regla dura de `cupon_service.validar_cupon` — ver §0. Un flag
    # editable sería la forma más barata de regalar 5% + 5% + descuento manual.

    created_by / created_at / updated_at
```

```python
ESTADO_CUPON_CHOICES = [
    ('PENDIENTE', 'Pendiente de uso'), ('CANJEADO', 'Canjeado'),
    ('EXPIRADO', 'Expirado'), ('ANULADO', 'Anulado'),
]

class CuponCliente(models.Model):
    """Cupón NOMINATIVO de un solo uso. Calcado de CanjeVale."""
    campana     = FK(CampanaCupon, on_delete=PROTECT, related_name='cupones')
    cliente     = FK(Cliente, on_delete=CASCADE, related_name='cupones_descuento')

    # Denormalizado y NORMALIZADO (sin puntos ni guion): en caja se compara contra
    # ticket.cliente_rut sin join y sin depender del formato que tecleó el cajero.
    rut_cliente = CharField(max_length=20, db_index=True)

    codigo      = CharField(max_length=24, unique=True, db_index=True)   # DC-XXXXXXXX (secrets)
    empresa     = FK(Empresa, on_delete=PROTECT)   # copia de campana.empresa (validar sin join)

    # SNAPSHOT al emitir: si la campaña cambia, el cupón ya entregado NO muta.
    # Mismo principio que CanjeVale.valor_pesos ("snapshot al generarse").
    tipo_valor  = CharField(choices=TIPO_VALOR_CUPON_CHOICES)
    valor       = DecimalField(max_digits=7, decimal_places=2)
    tope_descuento = IntegerField(null=True, blank=True)
    monto_minimo   = IntegerField(default=0)

    estado      = CharField(choices=ESTADO_CUPON_CHOICES, default='PENDIENTE', db_index=True)
    expira_en   = DateTimeField(db_index=True)

    # === Datos del canje efectivo ===
    ticket          = FK('app.Ticket', null=True, blank=True, on_delete=SET_NULL,
                         related_name='cupones_descuento')
    monto_descuento = IntegerField(default=0)      # lo REALMENTE descontado
    sucursal_canje  = FK(Sucursal, null=True, blank=True, on_delete=SET_NULL)
    usuario_canje   = FK(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL)
    canjeado_en     = DateTimeField(null=True, blank=True)

    idempotency_key = CharField(max_length=100, unique=True, null=True, blank=True)
    created_by / created_at / updated_at

    class Meta:
        indexes = [Index(fields=['estado', 'expira_en']),
                   Index(fields=['cliente', 'estado']),
                   Index(fields=['rut_cliente', 'estado'])]
        constraints = [
            # P1 — un solo cupón VIVO por cliente y campaña (los canjeados no cuentan).
            UniqueConstraint(fields=['campana', 'cliente'],
                             condition=Q(estado='PENDIENTE'),
                             name='uniq_cupon_vivo_por_cliente'),
            # Un ticket no puede consumir dos cupones.
            UniqueConstraint(fields=['ticket'], condition=Q(estado='CANJEADO'),
                             name='uniq_cupon_por_ticket'),
        ]
```

> **No hace falta un modelo-ledger de usos.** Con P1 el propio `CuponCliente` **es** el registro
> del uso (`ticket`, `monto_descuento`, `canjeado_en`, `usuario_canje`). El ledger separado sólo
> se justificaba con cupones públicos multi-uso.

### 3.2 Campos nuevos en `Ticket` (`app/models/ventas.py`)

```python
descuento_cupon = IntegerField(null=True, blank=True,
    help_text='Descuento por cupón nominativo (pesos brutos, IVA incluido)')
```

Simetría exacta con `descuento_fidelizacion`
([ventas.py:182](../retailmind/app/models/ventas.py#L182)) — así el DTE, la cuadratura y el
ticket impreso lo tratan igual. La FK inversa al cupón ya existe vía `CuponCliente.ticket`,
así que **no** se agrega FK en `Ticket`.

### 3.3 Servicio → `app/services/cupon_service.py` (nuevo)

```python
class CuponError(Exception): ...

def emitir_cupon(campana, cliente, *, usuario=None, idempotency_key=None) -> CuponCliente
    # Valida ficha (ver 3.4), campaña vigente y "uno vivo por cliente".
    # Congela el snapshot de valores y genera el código con `secrets`.

def emitir_lote(campana, clientes, *, usuario=None) -> dict
    # Emisión masiva. Devuelve {emitidos, omitidos: [(cliente, motivo)]}.
    # NUNCA falla en bloque: un cliente con ficha incompleta se omite con motivo.

def validar_cupon(codigo, *, ticket=None, monto=None, rut_cliente=None,
                  sucursal=None, tiene_dcto_manual=False, tiene_vale_puntos=False) -> dict
    # → {'valido', 'motivo', 'codigo', 'descuento_pesos', 'cliente_nombre', ...}
    # SIN escribir nada. Lo usa el POS al validar y el cobro al confirmar.
    # P5 + P6: rechaza si tiene_dcto_manual o tiene_vale_puntos. Regla dura.

def canjear_cupon(codigo, *, ticket, sucursal, usuario, monto_descuento) -> CuponCliente
    # transaction.atomic + select_for_update. Idempotente por (cupon, ticket).

def liberar_cupon(ticket, *, motivo='') -> int
    # Anulación/devolución del ticket: CANJEADO → PENDIENTE si aún no expiró.

def expirar_vencidos() -> int       # comando programable
def ficha_completa(cliente) -> tuple[bool, list[str]]
```

### 3.4 P4 — qué se considera "ficha de cliente"

`Cliente` ([crm.py:57](../retailmind/app/models/crm.py#L57)) tiene todo nullable salvo
`nombre` y `apellido`. Se valida en **dos momentos distintos**:

**Al emitir** (guardrail de calidad — sin esto emites cupones que no puedes ni enviar):

| Campo | Regla |
|---|---|
| `rut` | presente, válido (`validar_rut_chileno`), no genérico, no empresa (≥50M) |
| `nombre` + `apellido` | no vacíos |
| `email` **o** `celular` | al menos un canal de contacto válido, y **no** los dummy del genérico (`generico@test.cl`, `999999999`) |
| `activo` | `True` |

**Al canjear en caja** (lo que realmente protege la plata):

```
normalizar_rut(ticket.cliente_rut) == cupon.rut_cliente
```

Un cupón sólo lo usa su dueño. Esto **por sí solo** excluye el genérico, las empresas y las
ventas sin RUT, sin depender de ningún otro chequeo.

> **Ojo con el histórico:** el comentario de `_cliente_en_alcance`
> ([views_modulo_fidelizacion.py:71](../retailmind/app/views_modulo_fidelizacion.py#L71))
> advierte que *la mayoría de los clientes migrados no tiene `empresa` asignada*. Por eso la
> empresa del cupón **la define quien lo emite** (`_empresa_actual(request)`), no el cliente.

### 3.5 Orden de aplicación

Con P5 + P6 no hay cascada: sobre `ticket.total` se aplica **exactamente un** beneficio.

```
Σ subtotales de línea (server-authoritative)
  └─► ticket.total
       │
       ├── camino A: descuento manual  → ya viene incorporado en las líneas
       ├── camino B: VALE DE PUNTOS    → ticket.descuento_fidelizacion   (flujo actual)
       └── camino C: CUPÓN 5%          → ticket.descuento_cupon          (nuevo)
                                          ⛔ exige A y B ausentes
              = total final a cobrar
```

El bloque del cupón se inserta **antes** del bloque del vale y **antes** del guard de
cobertura de pagos, para que `ticket.total` ya sea el definitivo cuando se validan los pagos.
Si el payload trae `codigo_cupon_descuento` **y** `codigo_vale_canje`, el backend responde
400 sin cobrar: es un estado que la UI no debería permitir jamás, así que no se resuelve
silenciosamente eligiendo uno.

### 3.6 Motivos de rechazo en caja (texto de mostrador)

| Motivo | Mensaje al cajero |
|---|---|
| `NO_EXISTE` | "Ese código de descuento no existe." |
| `CANJEADO` | "Este cupón ya fue utilizado." |
| `VENCIDO` / `EXPIRADO` | "Este cupón está vencido." |
| `ANULADO` | "Este cupón fue anulado." |
| `OTRO_CLIENTE` | "Este cupón pertenece a otro cliente. Verifique el RUT de la venta." |
| `SIN_CLIENTE` | "Identifique al cliente con su RUT para usar este cupón." |
| `OTRA_EMPRESA` | "Este cupón es de otra cadena y no aplica en esta sucursal." |
| `NO_FIDELIZA` | (el que devuelva `venta_fideliza`: factura / cotización / RUT de empresa) |
| `DCTO_MANUAL` | **"No se puede combinar con un descuento manual. Quite el descuento o el cupón."** |
| `VALE_ACTIVO` | **"Esta venta ya tiene un vale de puntos. Es uno u otro: quite el vale para usar el cupón."** |
| `MONTO_MINIMO` | "Este cupón requiere una compra mínima de $X." |

Y el simétrico, al validar un vale con un cupón ya aplicado:
*"Esta venta ya tiene un cupón de descuento. Es uno u otro."*

---

## 4. Fases de implementación

> **ESTADO 2026-08-06 — Fases 1 a 5 escritas, NADA probado ni migrado.**
> `manage.py check` pasa sin issues. Los tests **no se han corrido nunca** y las
> migraciones **0205** (modelos) y **0206** (permiso de menú) **no están aplicadas**.
>
> **Dónde se crea el código:** menú **Fidelización → Códigos de Descuento**
> (`/app/fidelizacion/cupones/`). Primero se crea una **campaña** (el 5%, la
> vigencia, la empresa, el límite por cliente) y después se **emite el cupón** a
> cada cliente, buscándolo por RUT o nombre.
>
> Cambio sobre el diseño original: `uno_vivo_por_cliente` (bool) pasó a ser
> `limite_por_cliente` con tres opciones — **UNICO** (default: uno por cliente
> para siempre, cuenta también los ya usados y los vencidos), **VIVO** (sólo
> impide acumular sin usar) y **SIN_LIMITE**.


### Fase 1 — Modelos + servicio + tests (sin UI) — ✅ CÓDIGO ESCRITO (2026-08-05)

Falta sólo correr la suite (comandos en §7). `manage.py check` pasa sin issues.

1. ✅ `CampanaCupon` + `CuponCliente` en `app/models/fidelizacion.py`, re-exportados en
   `app/models/__init__.py`.
2. ✅ `descuento_cupon` en `Ticket` (`app/models/ventas.py`).
3. ✅ Migración **`0205_cupon_descuento_nominativo`** — 100% aditiva: 2 tablas nuevas +
   1 columna nullable en `ticket`. **Sin aplicar todavía.**
   (Quedó 0205 y no 0204 porque en el intertanto se creó
   `0204_pedido_ecommerce_estado_logistica_canal`.)
4. ✅ `app/services/cupon_service.py` completo (§3.3).
5. ✅ Tests `app/tests/test_cupones.py` — 38 tests:
   - emisión: ficha incompleta rechazada, RUT genérico/empresa rechazados, "uno vivo por
     cliente", snapshot congelado al cambiar la campaña, idempotencia;
   - canje: **RUT distinto → rechazo** (el test central de P1), empresa distinta, vencido,
     ya canjeado, monto mínimo, tope, **descuento manual presente → rechazo (P5)**,
     **vale de puntos presente → rechazo (P6)**, **payload con cupón + vale → 400 sin cobrar**;
   - liberación al anular el ticket; concurrencia con `select_for_update`.

### Fase 2 — Backend del cobro

6. En `registrar_pagos_ticket`, **inmediatamente antes** del bloque del vale
   ([views_modulo_ventas.py:4237](../retailmind/app/views_modulo_ventas.py#L4237)): leer
   `codigo_cupon_descuento` y, si viene junto con `codigo_vale_canje`, cortar con 400
   (`error_tipo: 'BENEFICIOS_EXCLUYENTES'`) **antes de tocar nada**. Si viene solo, llamar
   `venta_fideliza()` + `validar_cupon()` (con `tiene_dcto_manual` calculado sobre las líneas
   ya recalculadas), setear `ticket.descuento_cupon` y rebajar `ticket.total`.
7. Después de `ticket_se_pago`, junto al `canjear_vale` de la
   [línea 4468](../retailmind/app/views_modulo_ventas.py#L4468): `canjear_cupon()`.
   Misma resiliencia: si falla se loguea y **no se tumba la venta ya cobrada**.
8. Endpoint `GET /app/api/fidelizacion/cupon/<codigo>/?monto=&rut=` en
   `views_modulo_fidelizacion.py`, con
   `@requiere_alguno_de_los_permisos(('ticket_venta','puede_crear'), ('fidelizacion_cupones','puede_ver'))`
   — mismo criterio que `api_validar_vale_canje`
   ([:1531](../retailmind/app/views_modulo_fidelizacion.py#L1531)).
   **El `rut` es obligatorio**: sin él el endpoint no puede verificar que el portador sea el dueño.
9. `liberar_cupon()` en la anulación del ticket, donde hoy corre
   `fidelizacion_service.reversar_venta` ([:2211](../retailmind/app/views_modulo_ventas.py#L2211)).

### Fase 3 — UI del POS

10. Card **"Código de Descuento"** en el paso 3, debajo de `#valeCanjeCard`
    ([:797](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L797)),
    misma estructura (input + Validar + limpiar + `<div>` de mensaje).
11. JS `validarCupon()` / `limpiarCupon()` + `cuponActual`, clonando `validarVale()`.
    **`window.validarCupon = validarCupon`** — obligatorio: los `onclick` inline se resuelven
    contra `window` y el JS vive en un closure (bug ya pagado en el vale,
    ver comentario en [:4997](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L4997)).
    La llamada envía el RUT del formulario (`clienteRut`), no sólo el código.
12. Fila "Desc. Cupón" en el resumen (`actualizarResumenConCupon`) y `codigo_cupon_descuento`
    en el payload ([:8701](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L8701)).
13. **P5 + P6 en la UI — exclusión bidireccional en los tres sentidos:**
    - cupón validado → deshabilitar los botones de descuento por línea y global (reusando el
      patrón que ya existe para tickets de cambio,
      [:5781-5794](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L5781))
      **y** deshabilitar la tarjeta del vale de puntos;
    - vale de puntos validado → deshabilitar la tarjeta del cupón (tocar `validarVale()`,
      [:4931](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L4931));
    - descuento manual presente → `validarCupon()` avisa y no aplica.

    La forma más barata de que esto no se desincronice: una sola función
    `refrescarExclusionBeneficios()` que lea `cuponActual`, `valeCanjeActual` y
    `estado.productos.some(p => p.descuento_unitario > 0)`, y decida el estado de los tres
    controles. Se llama desde los tres puntos, en vez de que cada uno apague al otro a mano.
14. Extender `actualizarVisibilidadFidelizacion()`
    ([:5032](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L5032))
    para ocultar y limpiar el cupón en factura/cotización, **y además** cuando el RUT del
    formulario sea el genérico → *"Este descuento requiere identificar al cliente con su RUT"*.
15. Reflejar el descuento en el ticket térmico
    ([:4820 y :5148](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L4820)).

### Fase 4 — Menú Fidelización: campañas y emisión

16. `app/views_modulo_cupones.py` (FBV, patrón del proyecto):
    - **Campañas**: listado, crear/editar, activar/desactivar.
    - **Emisión individual**: botón "Emitir cupón" en la ficha del cliente
      (`ficha_cliente_puntos_vista`, [:290](../retailmind/app/views_modulo_fidelizacion.py#L290)),
      que ya valida alcance multi-empresa con `_cliente_en_alcance`.
    - **Emisión masiva**: desde el listado de clientes con filtros (nivel, gasto 12m,
      sin compras en N días) → `emitir_lote`, con reporte de omitidos por ficha incompleta.
    - **Seguimiento**: cupones emitidos / canjeados / vencidos, tasa de redención, $ regalados.
17. Templates en `app/templates/vistas/modulo_fidelizacion/`: `cupones_campanas.html`,
    `cupon_campana_form.html`, `cupones_emitidos.html` — patrón de **includes**
    (`layout/header.html` → `layout/menu.html` → contenido → `layout/footer.html`),
    reusando `module-header`, `kpi-card`, `pagination-controls`, `quick-filter-btn`.

    > **Gotcha del proyecto:** el footer trae jQuery, así que va **antes** del JS del módulo;
    > y el `main-content` duplicado sólo aparece si el footer va abajo
    > (ver `project_dashboard_requerimientos_fix`).
18. URLs planas en `app/urls.py` (sin namespace, import explícito):
    `/app/fidelizacion/cupones/`, `.../campanas/nueva/`, `.../campanas/<id>/editar/`,
    `.../emitidos/`, más las APIs de emisión.
19. Permiso `fidelizacion_cupones` — data migration espejando
    [0165_permisos_modulo_fidelizacion.py](../retailmind/app/migrations/0165_permisos_modulo_fidelizacion.py)
    (el `ModuloSistema` 'fidelizacion' ya existe; sólo `OpcionMenu` + `PermisoRol`).
    Reparto sugerido: admin/administracion total; jefe_local ver + emitir + exportar;
    cajero/vendedor **sin** opción de menú (validan el código desde el POS con `ticket_venta`).
20. `URL_PERMISO_MAP` en [middleware_permisos.py:90](../retailmind/app/middleware_permisos.py#L90):
    agregar `/app/fidelizacion/cupones/` → `fidelizacion_cupones` **antes** de la entrada
    genérica `/app/fidelizacion/` (el matching es por orden del diccionario).
21. Entrada en el submenú Fidelización de
    [menu.html:2805](../retailmind/app/templates/layout/menu.html#L2805).

### Fase 5 — Documento tributario, arqueo y expiración

22. `views_modulo_documentos.py` ([:3321](../retailmind/app/views_modulo_documentos.py#L3321)):
    3ª línea DR `"Descuento Cupon <CODIGO>"`. Bruto en boleta, `/1.19` en factura (aunque en
    factura el cupón nunca debería llegar).
23. Cuadratura ([views_modulo_ventas.py:8048](../retailmind/app/views_modulo_ventas.py#L8048)):
    acumulador `total_descuento_cupones`, o el arqueo no cuadra contra el reporte de ventas.
24. Comando `python manage.py expirar_cupones` (PENDIENTE → EXPIRADO), en la línea de
    `evaluar_alertas_pendientes`. Sin esto la tasa de redención miente.

---

## 5. Riesgos y gotchas

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | **Cupón validado después de registrar los pagos** → el total baja y los pagos sobran. Ya pasa con el vale ([:7238](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html#L7238)). | Al validar, si ya hay pagos, avisar y forzar recálculo. El guard `VALIDAR_COBERTURA_PAGOS` protege el servidor. |
| R2 | **P5/P6 se burlan por orden de operaciones**: validar el cupón primero y *después* aplicar el descuento manual o el vale. | Los chequeos `tiene_dcto_manual` y `tiene_vale_puntos` se repiten **en el backend** al cobrar, sobre las líneas ya recalculadas y sobre el payload completo. La UI es conveniencia; el backend manda. |
| R3 | **El mismo código usado en dos cajas a la vez.** | `select_for_update` + `UniqueConstraint(ticket, estado=CANJEADO)` + estado `PENDIENTE` como candado. |
| R4 | **Cliente sin `empresa`** (mayoría del histórico migrado). | La empresa del cupón la fija el emisor (`_empresa_actual`), y en caja se compara contra `ticket.sucursal.empresa_id`. Nunca contra `cliente.empresa`. |
| R5 | **Costo combinado invisible.** El cupón no se suma a otro descuento (P5/P6), pero **sí convive con la acumulación de puntos**: la venta con cupón igual genera cashback de nivel (hasta 5% en PLATINO). 5% + 5% = 10% de costo, y el guardrail `TASA_TOPE=10.0` de `api_guardar_programa` ([:1259](../retailmind/app/views_modulo_fidelizacion.py#L1259)) **no ve el cupón**. | Mostrar el costo combinado en la pantalla de campaña y en el reporte. Decisión a tomar al implementar: si una venta con cupón debe o no acumular puntos. |
| R6 | **Acumulación de puntos sobre el total ya rebajado** (`acumular_puntos_por_venta`, [:4923](../retailmind/app/views_modulo_ventas.py#L4923)). | Comportamiento correcto; documentarlo para que no se reporte como bug. |
| R7 | **Anulación / devolución** deja el cupón quemado. | `liberar_cupon()` en el mismo punto que `reversar_venta`. Si ya expiró, se deja EXPIRADO y se avisa. |
| R8 | **Límite de descuento por rol** (`limite_descuento_rol`). | El cupón NO pasa por ahí — es descuento del sistema, no del vendedor. Trazabilidad vía `usuario_canje` + `sucursal_canje`. |
| R9 | **Cliente desktop Tauri (NEXO POS)** consume `app/api/desktop/` y no vería el cupón. | Fuera de alcance de fase 1; replicar después en `api/desktop/fidelizacion_views.py`. |
| R10 | **Cliente cambia de RUT / ficha con RUT mal tecleado** → cupón inutilizable. | El RUT se congela normalizado al emitir. Admin puede anular y reemitir. |

---

## 6. Decisión abierta

Ninguna de diseño: P1–P6 están cerradas (§0) y el plan es ejecutable tal como está.

Queda **una** pregunta que sólo aparece al implementar, y no bloquea el arranque (Fases 1–4
son idénticas en ambos casos): **¿una venta pagada con cupón debe acumular puntos?**

- **Acumula** (lo que hace hoy el código sin tocar nada): el costo llega a 5% + hasta 5% de
  cashback. Es coherente con "el cliente compró, gana puntos".
- **No acumula**: el cupón *es* el beneficio de esa venta, coherente con la regla "un solo
  beneficio por venta" de P5/P6. Se implementa saltando `acumular_puntos_por_venta`
  ([:4923](../retailmind/app/views_modulo_ventas.py#L4923)) cuando hay `descuento_cupon`.

Se decide al llegar a la Fase 2.

---

## 7. Comandos pendientes (ejecutar en PC, tras aprobar el plan)

```powershell
cd c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind

# 1. Tests del módulo nuevo.
#    --noinput evita el prompt "test_retail already exists": Django borra y recrea
#    la base de TEST (test_retail), nunca la real.
python manage.py test app.tests.test_cupones --noinput

# 2. Regresión: el cobro y la fidelización son lo que más se tocó
python manage.py test app.tests.test_fidelizacion app.tests.test_ventas --noinput

# 3. Ver el SQL de las migraciones ANTES de aplicarlas
python manage.py sqlmigrate app 0205
python manage.py sqlmigrate app 0206

# 4. Recién con los tests en verde, aplicar (ver aviso abajo)
python manage.py migrate app 0206
```

### ⚠️ Sobre `migrate`

El `.env` local apunta a la **base de producción** (DigitalOcean), así que
`python manage.py migrate` aplica **en producción**.

- **0205** es aditiva y de bajo riesgo: 2 tablas nuevas + 1 columna nullable en `ticket`
  (en PostgreSQL agregar una columna nullable no reescribe la tabla).
- **0206** sólo inserta una `OpcionMenu` y sus `PermisoRol`; es idempotente.

Mientras no se apliquen, la pantalla de cupones responde error 500 (las tablas no existen)
y el menú no muestra la opción. El resto del sistema no se ve afectado: el bloque del cupón
en el cobro sólo corre si el payload trae `codigo_cupon_descuento`.
