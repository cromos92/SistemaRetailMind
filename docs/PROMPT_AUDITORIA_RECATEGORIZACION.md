# PROMPT para Claude Code (pegar completo en el repo SistemaRetailMind)

Audita el estado real de la recategorización v1.2 del catálogo. Contexto completo abajo. NO escribas nada en la base de datos: todo tu trabajo es de SOLO LECTURA (queries de análisis, lectura de archivos). No corras migrate, makemigrations, ni ningún comando clean_*/limpiar_*. Al final entrégame un informe con veredictos claros.

## Contexto — qué se hizo en la sesión de Cowork (14/15-07-2026)

1. **Taxonomía v1.2 diseñada** (proyecto «re mapeo»): árbol físico de 2 niveles + especialidades transversales múltiples.
   - Categoria (padre→hijo vía FK `padre` existente): Calzado → Zapatillas, Botines, Botas, Sandalias y Chalas, Mocasines, Zapatos de Vestir, Alpargatas, Pantuflas, Tacos, Ballerinas, Plataformas, Gateadores, Danza · Ropa → Poleras y Camisetas, Shorts, Buzos y Pantalones, Chaquetas y Polerones, Trajes de Baño, Mallas · Accesorios → Bolsos y Mochilas, Gorros, Medias y Calcetines, Accesorios de Vestuario, Trofeos y Medallas, Protecciones, Balones, Guantes, Equipamiento Fitness, Accesorios Deportivos.
   - Especialidades (36 slugs, multi-etiqueta por producto, guardadas como AtributoOpcion del atributo "Especialidad" + ProductoAtributoValor): pasto/baby/sala (fútbol), running, atletismo, basket, training, tenis, voley, handball, rugby, boxeo, artesm, natacion, ciclismo, pingpong, badminton, beisbol, hockey, pool, skate, recre, golf, trekking, camping, pesca, oficial/blanco/negro (escolar), urbano (fusiona los antiguos casual+moda), cueca, vestir, fiesta, descanso, gim, seguridad.
   - Género: DAMA (AtributoOpcion 711) debe migrar a MUJER (706); falta crear BEBÉ.
2. **Archivos creados en este repo** (por la sesión de Cowork):
   - `app/management/commands/_data_recategorizacion_v12.py` — datos de la taxonomía v1.2.
   - `app/management/commands/sembrar_taxonomia_v12.py` — siembra árbol+especialidades (dry-run por defecto, idempotente).
   - `app/management/commands/aplicar_recategorizacion_v12.py` — aplica por artículo desde `docs/recategorizacion_v12_por_articulo.xlsx` (dry-run por defecto; --apply escribe).
   - `app/management/commands/preview_recategorizacion_v12.py` — genera Excel preview de solo lectura (BD + mapeo + cruce Compras). **Javier ya lo ejecutó** y generó `recategorizacion_v12_preview_*.xlsx` en `retailmind/`.
   - `docs/recategorizacion_erp_v1.2.xlsx` — Excel maestro (7 hojas: taxonomía, especialidades, género con IDs 706-712, mapeo de las 83 categorías viejas con sus IDs de BD, 62 reglas de menú, inventario de 1.354 planillas de Compras).
   - `docs/recategorizacion_v12_por_articulo.xlsx` — 63.857 artículos con clasificación v1.2 derivada del preview del pipeline anterior.
3. **Pipeline anterior ya existente en el repo** (sesión previa de Claude Code): `_data_recategorizacion.py` (MAPEO_DIRECTO por id, REGLAS_KEYWORD, REGLAS_MARCA, taxonomía Departamento→Tipo), `recategorizar_catalogo.py`, `_clasificar_modelos.py`, `modelos_clasificados.json`, y previews `recategorizacion_preview_2026*.xlsx`. **Decisión tomada con Javier: se aplica el árbol v1.2 (físico + especialidades), NO el árbol Departamento→Tipo del pipeline anterior.**
4. **Nada debería estar aplicado aún en producción** — pero Javier no está seguro de si alguna corrida anterior (de la sesión previa o de ahora) escribió algo. Eso es lo primero que debes verificar.

## Tus tareas (en orden, todo read-only)

### A. Estado real de la BD — ¿se recategorizó algo o no?
Con `python manage.py shell` (la BD de producción está en `.env`):
1. `Categoria`: cuántas hay en total; listar las que tienen `padre__isnull=False` (¿existe ya algún árbol? ¿del pipeline viejo —"Zapatillas Urbanas", "Training y Fitness"— o del v1.2 —"Tacos", "Gateadores", "Equipamiento Fitness"?); cuántos productos apuntan a cada categoría (`Producto.objects.values('categoria__nombre').annotate(n=Count('id'))` ordenado desc).
2. ¿Existe `Productos_Atributos` con nombre "Especialidad"? ¿Cuántas `AtributoOpcion` tiene y cuántas filas de `ProductoAtributoValor` la usan?
3. Género: conteo de productos por `atributo3__valor` — ¿sigue habiendo DAMA? ¿existe BEBÉ?
4. Concluye: **"la BD está intacta (todo sigue en las 83 categorías planas)"** o **"hubo escritura: esto y esto ya cambió"** (con números).

### B. Efectividad del preview y del cruce con Compras
5. Abre el `recategorizacion_v12_preview_*.xlsx` más reciente en `retailmind/` (openpyxl, read_only). Reporta: total de artículos; distribución de `cat_v12/sub_v12`; top especialidades; % confianza alta/media/baja; % con flag revisar; cuántos quedaron sin destino.
6. Columna `match_compras`: distribución exacto / sin_sufijo / truncado? / no_encontrado. Toma 15 casos al azar de `sin_sufijo` y `truncado?` y valida a mano contra la descripción/color del producto si el `codigo_sugerido` es plausible. Veredicto: ¿el cruce con Compras sirve para reparar artículos o necesita ajustes (qué ajustes)?
7. Coherencia: muestrea 20 artículos al azar y revisa que cat/sub/esp tengan sentido versus descripción y categoría original. Anota patrones de error (no casos aislados).

### C. Gaps entre el preview y la BD
8. ¿Cuántos artículos de la BD NO aparecen en el preview y viceversa (creados después, artículos vacíos, etc.)?
9. Revisa los buckets problemáticos: cuántos productos hay hoy en LIN CORE (id 75), RAMA ZA (73), SIN DEFINIR (63), ZAPATON (46), TRABA (27), SET DE FOOTBAL (4), SALUD Y ORTOPEDIA (7) — y para cada uno mira 5 descripciones reales y propone su destino v1.2 definitivo.

### D. Informe final
Entrega `docs/AUDITORIA_RECATEGORIZACION_V12.md` con: (1) veredicto sobre el estado de la BD (¿algo aplicado? qué exactamente), (2) calidad del preview (números + patrones de error), (3) veredicto del cruce Compras con ejemplos, (4) destino propuesto para los 7 buckets dudosos, (5) checklist GO/NO-GO para correr `sembrar_taxonomia_v12 --apply` y luego `aplicar_recategorizacion_v12 --lote 200 --apply`. No apliques nada: solo el informe.

## Reglas duras (de CLAUDE.md, respétalas)
- Solo lectura en BD. Cero `save()`, `update()`, `delete()`, `migrate`, `collectstatic`.
- No toques `settings.py` ni los archivos generados/previews existentes.
- No corras comandos `clean_*` ni scripts `_fix_*`/`_limpiar_*`.
- Los commands v1.2 son dry-run por defecto: NO les pases `--apply`.
