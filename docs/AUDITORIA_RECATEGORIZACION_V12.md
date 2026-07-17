# Auditoría — Recategorización v1.2 del catálogo

**Fecha**: 2026-07-15 · **Alcance**: 100% solo lectura (BD producción vía `.env`, Excels del repo, planillas de Compras).
**Auditado por**: Claude Code (sesión de auditoría posterior al proyecto «re mapeo» de Cowork 14/15-07-2026).

---

## 1. Veredicto: estado de la BD

**LA BD ESTÁ INTACTA. Ninguna corrida (ni del pipeline viejo ni del v1.2) escribió nada.**

Evidencia (queries de solo lectura, 2026-07-15):

| Chequeo | Resultado |
|---|---|
| Categorías totales | **83**, todas planas (`padre__isnull=False` → **0 filas**) |
| Productos en categorías con padre | **0** de 137.916 |
| Árbol del pipeline viejo ("Zapatillas Urbanas", "Training y Fitness"…) | **No existe** ninguna |
| Árbol v1.2 ("Tacos", "Gateadores" hijo, "Equipamiento Fitness"…) | **No existe** como hijos; solo homónimos planos preexistentes (ver §1.1) |
| Atributo `Productos_Atributos` "Especialidad" | **No existe** (solo hay: 1 Marca, 2 Color, 3 Sexo, 4 Género) |
| Filas `ProductoAtributoValor` (todas) | **0** |
| Género DAMA (opción 711) | **Sigue vivo: 95 productos** (= 23 artículos distintos) |
| Género BEBÉ | **No existe** |
| Opciones del atributo 3 "Sexo" | 706=MUJER, 707=HOMBRE, 708=NIÑO, 709=UNISEX, 710=NIÑA, 711=DAMA, 712=JUVENIL — coincide exacto con lo documentado en `_data_recategorizacion_v12.py` |

Distribución de productos por categoría: sigue el patrón plano conocido — RAMA CASUAL 46.582, CHALAS 14.748, RAMA FOOTBALL 8.593, VESTIR 7.237, BOTINES 6.553… (todo cuadra con las 83 categorías del mapeo).

### 1.1 Detalles que conviene saber (no son escrituras del proyecto)

- Los **stubs padres ya existen y son los ids esperados**: Calzado=79, Ropa=80, Accesorios=81 (además Deportes=82 y Casual=83). Tienen algunos productos asignados directamente (Calzado 7, Accesorios 21, Casual 19, Deportes 0, Ropa 0 — 47 en total), probablemente desde el formulario de creación de productos. `aplicar` los moverá si sus artículos están en el Excel.
- **6 nombres de hijos v1.2 ya existen como categorías planas** (match `iexact`): BOTINES(26), GORROS(18), PANTUFLAS(58), PLATAFORMAS(29), GATEADORES(34), TROFEOS Y MEDALLAS(54). `sembrar` igual creará los hijos nuevos bajo su padre (filtra por `padre=`), así que **convivirán temporalmente dos categorías con el mismo nombre** hasta que `aplicar` mueva los productos. Reportes que agrupan por `categoria__nombre` verán el nombre duplicado durante la transición.
- Higiene pendiente del catálogo viejo: "TRAJE DE BAÃ‘O"(20, mojibake) vs "TRAJE DE BAÑO"(77); "SEGUIRAD"(72, typo) vs "SEGURIDAD"(68); "RAMA HANDBALL"(71) vs "RAMA DE HANDBALL"(69). Quedarán vacías tras aplicar; anotarlas para la fase de limpieza.
- 16 productos con `categoria=NULL` (aparecen en el Excel como "(sin categoría)" — cubiertos).

---

## 2. Calidad del preview y del Excel por artículo

### 2.1 Qué archivo es cuál (importante)

- `retailmind/recategorizacion_v12_preview_20260715_1734.xlsx` (el que corrió Javier hoy) **es una prueba de 500 filas sin Compras** (`--limite 500`, sin `--compras-dir`): no sirve para evaluar cobertura ni cruce.
- **`docs/recategorizacion_v12_por_articulo.xlsx` es el archivo que realmente consumirá `aplicar_recategorizacion_v12`** — 63.857 filas. Toda la evaluación de calidad va sobre él.
- Para auditar el cruce con Compras, esta auditoría generó un preview completo con `--compras-dir` (63.856 artículos, solo lectura, guardado fuera del repo).

### 2.2 Cobertura BD ↔ Excel: perfecta

- Artículos distintos en BD: **63.856**. Artículos en el Excel: **63.856 únicos** (63.857 filas: hay **1 fila duplicada**, artículo `5208320403814` "GUANTE PERFORMANCE", mismo destino en ambas — inofensiva pero conviene depurarla).
- En BD y no en Excel: **0**. En Excel y no en BD: **0**. No hay huérfanos en ninguna dirección.

### 2.3 Distribución del destino v1.2 (Excel por artículo)

| cat_v12 | artículos |
|---|---|
| calzado | 39.428 (61,7%) |
| accesorios | 17.039 (26,7%) |
| vestuario | 7.107 (11,1%) |
| **(sin destino)** | **283** (0,4%) |

Top subs: zapatillas 24.050 · **accdep 7.053** · sandalias 6.029 · zvestir 4.554 · poleras 3.546 · mochilas 3.219 · botines 2.704. Colas casi vacías: **tacos 0**, mallas 1, danza 55, plataformas 65 — el mapeo nunca asigna "Tacos" (no hay regla; el calzado de vestir mujer con taco cae entero en `zvestir`). "Tacos" y "Mallas" nacerán como categorías vacías.

Especialidades: urbano 30.442 · pasto 6.031 · vestir 2.513 · descanso 2.173 · training 1.927 · … · camping 8. **14.791 artículos con destino quedan sin especialidad** (23%, esperable en accesorios/ropa básica) y solo **134 artículos tienen >1 especialidad** — el multi-etiquetado casi no se usa en esta primera pasada.

Flags: confianza alta 46.457 / media 17.400; **revisar_final=Sí en 29.385 (46%)**. Notas dominantes: "accesorio genérico: confirmar sub" 6.911 · "Confort y Ortopédico: confirmar destino" 1.891 · "sin resolver — manual" 263.

Stock: 169.993 unidades totales en el Excel; 72.691 (43%) en filas `revisar_final=Sí`. El "stock sin destino" (37.242) es un espejismo: **32.577 son `VISA` ("DIFER VISA") y 4.435 `ENVIOS` ("COSTO ENVIO")** — pseudo-artículos no-producto de PAOLA repartidos en 7-8 sucursales. El stock real de productos sin destino es ~230 unidades. Recomendación: a esos ~20 no-producto (VISA, ENVIOS, LOTE1-4, SALDO5-7…) no recategorizarlos sino marcarles `Producto.excluir_de_analitica=True` (el campo ya existe).

Género: los **23 artículos** que concentran los 95 productos DAMA **tienen todos destino v1.2** en el Excel → `aplicar` migrará el 100% de DAMA→MUJER (verifica por producto, no por la columna `sexo` del Excel). BEBÉ solo se crea; nadie lo asigna todavía (123 notas "sugerir gen=Bebé" quedan como trabajo posterior).

### 2.4 Divergencia entre el Excel y el clasificador del repo (hallazgo importante)

Regenerando hoy el preview completo con el código del repo y comparando contra el Excel por artículo: **3.481 artículos (5,45%) obtienen destino distinto**, y de ellos **solo 872 (25%) están marcados `revisar_final=Sí`** — es decir, ~2.600 divergencias pasarían como "confiables". Patrones dominantes (generador actual → Excel):

| n | generador hoy | Excel por artículo | diagnóstico |
|---|---|---|---|
| 669 | accesorios/accdep | calzado/zapatillas | artículos Deportes sin tallas legibles caen a accesorios (ej.: botines `GW2355 SPEEDPORTAL`, `105013 01 CLASSICO C II FG` → el Excel acierta) |
| 313 | calzado/zapatillas | calzado/sandalias | ídem sensibilidad a `forma_por_tallas` |
| 303 | calzado/zvestir | accesorios/protecciones | bug "Confort y Ortopédico" (el Excel acierta: RODILLERA→protecciones) |
| 215 | accesorios/balones | calzado/zapatillas | **el Excel se equivoca**: `JD3826 BALON FUTBOL` (stock 123), `BALON TEAM`, `1002121099 BALON FUTSAL` figuran como *zapatillas* y **sin flag de revisión** |
| 214+ | zapatillas ↔ zvestir, etc. | | ruido de keywords |

Conclusión: el acuerdo global 94,5% es bueno, pero **los dos archivos tienen errores en direcciones opuestas** y el flag `revisar_final` no cubre la mayoría de las discrepancias. Los casos con stock real divergente son pocos (top 15 suman ~1.600 u.) y son revisables a mano.

### 2.5 Errores sistemáticos detectados (patrones, no casos aislados)

1. **SALUD Y ORTOPEDIA (id 7) → calzado/zvestir + descanso (1.891 artículos)**: el mapeo directo viejo etiqueta toda la categoría como "Confort y Ortopédico", pero el bucket es mayormente medicina deportiva/boxeo (GUANTILLA MMA, VENDA, MENISQUERA, RODILLERA VOLEY, TAPON OIDOS, e incluso botines `F50 PRO`). Mandarlos a "Zapatos de Vestir/descanso" es absurdo. *Mitigante*: los 1.891 llevan nota y `revisar_final=Sí`, así que `--solo-confiables` los salta. **No aplicar este bucket tal cual** (ver §4).
2. **TRABA (id 27) → accesorios/accvest (23 artículos, TODOS con `revisar_final=No`)**: "traba" aquí no es accesorio de pelo: son **zapatos de niña con traba/hebilla** (descripciones "CHAROL 20/25", "SANDALIA 18/25", "ZAPATO NIÑO", "TRABA 34/40", marcas Colloky/Bamboo/Paola, con rangos de talla de calzado). Se aplicarían **silenciosamente mal incluso con `--solo-confiables`**. Corregir el Excel antes de cualquier apply.
3. **Balones/sets clasificados como zapatillas sin flag** (§2.4, patrón de 215): revisar `BALON|PELOTA|SET|EQUIPO` dentro de las filas `revisar_final=No` del Excel.
4. **accdep como cajón de sastre nuevo**: 7.053 artículos (11%) caen en "Accesorios Deportivos" genérico, 6.911 con nota "confirmar sub". Es RAMA CASUAL 2.0 en miniatura; planificar una segunda pasada por keywords para repartirlo (balones/guantes/protecciones/equipamiento).
5. **Muestreo de coherencia (20 al azar del Excel + 20 del preview regenerado)**: fuera de los patrones anteriores, la coherencia es buena — zapatillas urbanas Nike/Adidas/Skechers de RAMA CASUAL → zapatillas+urbano; CHALAS→sandalias; POLERON→chaquetas; PANTUFLAS→pantuflas+descanso; CUECA→zvestir+cueca; trofeos, medias y gorros correctos. Los errores se concentran en los buckets ya señalados, no en el grueso del catálogo.

---

## 3. Veredicto del cruce con Compras

**Tal como está configurado hoy, el cruce NO sirve para reparar códigos a escala. Cobertura casi nula; el bucket `sin_sufijo` es útil pero diminuto; `truncado?` tiene demasiados falsos positivos.**

Números (preview completo regenerado hoy con `--compras-dir`, años default 2025-2027):

| match_compras | artículos | % | entre artículos con stock (10.005) |
|---|---|---|---|
| exacto | 777 | 1,2% | 592 (5,9%) |
| sin_sufijo | 88 | 0,1% | 8 |
| truncado? | 657 | 1,0% | 59 |
| **no_encontrado** | **62.334** | **97,6%** | 9.346 (93,4%) |

Causas de la cobertura nula:

1. **Solo se indexan 2025/2026/2027** (default `--anios`): 418 planillas de las 2.225 que hay en `C:\Users\cromo\Documents\Compras` (2013-2027). El catálogo es histórico desde 2013 — justamente los artículos truncados por la migración están en los años que no se indexan.
2. **Solo 181 de 418 planillas fueron legibles (43%)**: openpyxl no lee `.xls` antiguos, algunos libros fallan y el detector de cabecera (PRODUCTO/CODIGO/COD/ARTICULO en primeras 30 filas, solo primera hoja) no encuentra la columna en muchas. Se indexaron apenas 21.168 códigos únicos.
3. La carpeta **"2025 ACCESORIOS" queda fuera** (el glob va por nombre de año exacto; se puede incluir con `--anios "2025,2025 ACCESORIOS,2026,2027"`).

Validación manual de muestras (15 + 15 al azar, cotejadas contra descripción/color/tallas en BD):

- **`sin_sufijo` — plausible en ~13/15**: `332 REINA ELASTICO (PASSER)` → `332-9193` (candidato único, coherente); `803 GUANTE ARQUERO (DRIBLING)` → `803-1192|803-1400|803-653`; `402 ALPARGATA (PASSER)` → 3 candidatos. El patrón artículo+`-COLOR` es real. **Pero**: cuando hay varios candidatos, el artículo del ERP agrupa varios colores en un solo código (ej. 402 tiene 6 colores en 16 filas), así que "reparar" no es un rename 1:1 — habría que **dividir el artículo por color**, que es otra operación (tipo fusión-inversa). Los sufijos de Compras son códigos numéricos de proveedor y el ERP guarda nombres de color (NEGRO/ESTAMPADO/…), por lo que el match por color no es automatizable sin una tabla de equivalencias por proveedor.
- **`truncado?` — plausible en ~7/15, falsos positivos claros**: `210 COPA ALTEZZA (ALPESPORT, trofeo)` → `2106.1041.13488-16072…` (códigos estilo Vizzano de calzado brasileño: falso); `4985 CARA TF (NIKE)` → `4985138` (formato no-Nike: dudoso); en cambio `4895 PATIN HEAD (TRO)` → `48958700` y `8517 BOTIN (AGTA)` → `8517240…` son creíbles. El match por prefijo con artículos numéricos cortos (3-4 dígitos) matchea cualquier cosa.

**Ajustes recomendados antes de usarlo en serio**: (a) indexar todos los años + "2025 ACCESORIOS"; (b) soportar `.xls` (convertir o `xlrd`) y buscar la cabecera en todas las hojas; (c) para `truncado?`, exigir largo mínimo de prefijo (≥5) y cruzar la marca del producto con el proveedor de la planilla; (d) tratar `sin_sufijo` multi-candidato como caso de **división por color**, no de rename. Con eso, repetir el preview y re-medir. El cruce es buena idea, pero hoy es un prototipo.

---

## 4. Buckets problemáticos: destino v1.2 propuesto

Conteos BD = productos/artículos reales hoy; "Excel" = artículos cuya fila representativa cae en ese bucket (difiere porque el Excel toma una categoría representativa por artículo).

| Bucket (id) | BD | Qué contiene realmente (muestras) | Excel hoy propone | **Destino definitivo propuesto** |
|---|---|---|---|---|
| **LIN CORE (75)** | 1 prod / 1 art | `DT4827 "CORE"` ADIDAS (zapatilla) | calzado/zapatillas+urbano ✔ | **Calzado→Zapatillas + urbano**. Categoría muere. |
| **RAMA ZA (73)** | 2 prod / 1 art | `159-9057 "TONGA"` Bubble Gummers niño (chala) | **sin destino** ✘ | **Calzado→Sandalias y Chalas + urbano**, gen Niño. Corregir a mano en el Excel. |
| **SIN DEFINIR (63)** | 822 / 582 | Mezcla: zapatillas legacy (ADI HOOP, CONVERSE, DVS), pseudo-artículos (VISA, ENVIOS, LOTE*, SALDO*), sueltos (PAÑUELO, PINCHE) | 169 zapatillas ✔ / **158 sin destino** / resto variado | Aplicar los resueltos; los ~20 no-producto → **`excluir_de_analitica=True`** (no recategorizar); el resto (~140) revisión manual corta — la mayoría son zapatillas urbano por marca. |
| **ZAPATON (46)** | 2.093 / 868 | Zapato colegial niño (LIPI, BUBBLEONE, COLEGIAL RUGGED) + zapato casual hombre (SERCO, GUANTE, ALBANO) | 651 zapatillas+urbano / 80 sandalias | Dividir por keyword: `COLEGIAL` → **Zapatillas + esp blanco/negro/oficial** (escolar); `ZAPATO/ZAPATON` hombre adulto → **Zapatos de Vestir + vestir** (el "zapatón" clásico es vestir/casual de cuero, no zapatilla); resto → Zapatillas+urbano. El Excel hoy manda casi todo a zapatillas: aceptable como default, pero pierde el matiz escolar/vestir. |
| **TRABA (27)** | 48 / 36 | **Zapatos de niña con hebilla**: CHAROL 20/25, SANDALIA 18/25, ZAPATO NIÑO Colloky, TRABA 34/40 | **accesorios/accvest ✘ (los 23, sin flag)** | **Calzado→Ballerinas** (o Zapatos de Vestir niña para los CHAROL) + esp oficial/urbano según descripción, gen Niña. **Corregir el Excel antes de aplicar — hoy se aplicaría mal en silencio.** |
| **SET DE FOOTBAL (4)** | 1.169 / 562 | Balones (PUMA, OLYMPHUS), sets/equipos de camisetas (FOUR, BARLOSPORT), zapatillas fútbol, sets arquero | accdep 263 / poleras 95 / zapatillas 90 / balones 32 / guantes 24 | Dividir por keyword: `BALON` → **Balones + pasto/sala**; `SET/EQUIPO/CAMISETA` → **Poleras y Camisetas + pasto** (kits de equipo); `SET ARQ/GUANTE` → **Guantes + pasto**; `ZAPATILLA/BOTIN` → Zapatillas/Botines + pasto; `RED/BUCAL/accesorios` → Accesorios Deportivos. Los 263 accdep ya llevan flag: revisarlos con esta regla. |
| **SALUD Y ORTOPEDIA (7)** | 4.631 / 2.601 | Medicina deportiva/boxeo EVERLAST (GUANTILLA MMA, VENDA, RODILLERA, MENISQUERA, CHALECO 20LB) + algo de confort real | **1.891 → zvestir+descanso ✘** / 299 protecciones ✔ | Re-derivar por keyword ANTES del mapeo directo: `GUANTILLA/GUANTE` → **Guantes + boxeo**; `VENDA/RODILLERA/MENISQUERA/TOBILLERA/MUÑEQUERA` → **Protecciones**; `PESA/CHALECO nLB/MANCUERNA` → **Equipamiento Fitness + training**; plantillas/confort → **Zapatos de Vestir + descanso** (solo eso). Con flag hoy (no pasa `--solo-confiables`), pero hay que arreglarlo antes del apply total. |

---

## 5. Checklist GO / NO-GO

### `sembrar_taxonomia_v12 --apply` → **GO** ✅

Es seguro tal cual: idempotente (`get_or_create`), atómico, no toca productos ni borra nada. Verificado además:

- Los padres reutilizan los stubs existentes 79/80/81 por nombre (no crea duplicados de raíz).
- El lookup del atributo de género es correcto: solo el atributo 3 "Sexo" tiene HOMBRE/MUJER/UNISEX (el atributo 4 "Género" no interfiere) → BEBÉ se creará donde corresponde.
- `Producto.save()` no tiene señales `post_save` conectadas (no hay efectos colaterales al aplicar después).
- Único efecto cosmético: 6 hijos nuevos coexistirán con categorías planas homónimas (§1.1) hasta que se migre y limpie.

### `aplicar_recategorizacion_v12 --lote 200 --apply` → **NO-GO todavía** ⛔ (GO condicional)

El grueso del mapeo es bueno (94,5% de acuerdo, muestreos coherentes), pero hay 4 correcciones previas obligatorias y 2 decisiones de operación:

**Antes del primer `--apply` (bloqueantes):**
1. ☐ **Corregir TRABA en el Excel** (23 filas accvest → calzado, hoy sin flag — §2.5.2).
2. ☐ **Barrer `BALON|PELOTA|SET |EQUIPO` en filas `revisar_final=No`** y corregir las que caen en zapatillas (§2.4/§2.5.3).
3. ☐ **Marcar los ~20 no-producto** (VISA, ENVIOS, LOTE*, SALDO*…) para exclusión: vaciarles `cat_v12` si la tuvieran y tratarlos vía `excluir_de_analitica`, no como categoría.
4. ☐ **Eliminar la fila duplicada** del artículo `5208320403814`.

**Decisiones de operación:**
5. ☐ Primer lote real con **`--lote 200 --solo-confiables --apply`** (34.472 artículos elegibles; con TRABA corregido ya no hay error conocido sin flag). Validar con el CSV de log antes de ampliar.
6. ☐ El apply total corre en **una sola transacción** sobre la BD de producción (~64k artículos, 138k productos + PAV). Preferir tandas por `--filtro-cat` o lotes crecientes, en horario de baja carga. Recordar que `.env` apunta a producción y que el command se ejecuta desde `retailmind/` (la ruta default del Excel es relativa: `../docs/...`).

**Trabajo posterior al primer apply (no bloquea, pero agendar):**
7. ☐ Re-derivar SALUD Y ORTOPEDIA por keywords (1.891 artículos con flag, §4) y aplicar ese bucket aparte.
8. ☐ Segunda pasada sobre `accdep` (7.053) para repartir el nuevo cajón de sastre.
9. ☐ Resolver los 283 sin destino (158 SIN DEFINIR + REINA 74 + MAFALDA 36 + RAMA ZA 1…).
10. ☐ Reglas para "Tacos" (hoy 0 artículos) o decidir si se elimina de la taxonomía; asignación de gen BEBÉ (123 candidatos anotados).
11. ☐ Fase de limpieza de las 83 categorías viejas (renombres/mojibake/typos de §1.1) — hoy no existe command para esto.
12. ☐ Si se quiere usar el cruce Compras en serio: aplicar los 4 ajustes de §3 y regenerar el preview.

---

## Anexo: fuentes de esta auditoría

- BD producción (solo lectura): conteos de `Categoria`, `Producto`, `Productos_Atributos`, `AtributoOpcion`, `ProductoAtributoValor`, muestras por bucket, colores/tallas de artículos muestreados.
- `docs/recategorizacion_v12_por_articulo.xlsx` (63.857 filas) — análisis completo.
- `retailmind/recategorizacion_v12_preview_20260715_1734.xlsx` — identificado como prueba de 500 filas.
- Preview completo con Compras regenerado por la auditoría (63.856 artículos, 418 planillas 2025-27, guardado fuera del repo en el scratchpad de la sesión).
- Código: `sembrar_taxonomia_v12.py`, `aplicar_recategorizacion_v12.py`, `preview_recategorizacion_v12.py`, `_data_recategorizacion_v12.py`, `_data_recategorizacion.py`, `app/models/catalogo.py`, `app/signals.py`.
