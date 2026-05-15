# Fix: polling agresivo de `/api/skus/` que saturaba producción

## Problema

Producción de RetailMind reportaba sistema lento desde el deploy. En logs se veía:

```
12:56:05 → [external/skus] rut=76104936-4
12:56:18 → [external/skus] rut=76104936-4 (13s después)
12:57:49 → 19091 productos (86338 filas raw)         ← terminó 1ra
12:57:52 → [external/skus] rut=76104936-4 (3s después)
12:58:01 → 19091 productos                            ← terminó 2da
...
```

`/api/skus/` se llamaba cada 7-15s, cada llamada tardaba 60-90s, devolvía 19,091 productos y procesaba 86,338 filas raw. Los 2 workers de gunicorn quedaron permanentemente ocupados → el resto del sistema (POS, dashboards, sincronizaciones) en cola.

## Causa raíz identificada

`AllConected` tiene un health check del canal RetailMind ([diagnostics.py:243](../VicentAllEcommercesConected/system/core/diagnostics.py#L243)) llamado `check_retailmind_skus` que **descargaba el catálogo completo de 19,091 productos solo para validar que la conexión funciona**.

```python
# ❌ Diseño incorrecto: usa el endpoint más caro como ping
def check_retailmind_skus(canal) -> Dict:
    client = RetailMindClient(canal=canal)
    resp = client.obtener_skus_por_empresa(rut)  # ← 19K productos solo para probar
    if resp.get("success"):
        return _result("ok", f"{total} articulo(s) disponibles", ...)
```

Esto se invocaba desde `DiagnosticEngine.run(canal)` ([diagnostics.py:628](../VicentAllEcommercesConected/system/core/diagnostics.py#L628)), expuesto en `POST /api/canal/<id>/diagnosticar/` ([core/views.py:139](../VicentAllEcommercesConected/system/core/views.py#L139)).

**El bucle**: alguna pantalla / proceso pollea ese endpoint repetidamente → cada poll dispara 60-90s de descarga de 19K productos.

### Por qué empeoró ahora

Ayer arreglamos un `FieldError` en `realsport_imagenes_service.py` que hacía que `/api/skus/` devolviera 500 rápido cuando faltaba foto para el empresa_id. Después del fix, el endpoint devuelve 200 (correcto) pero tarda más porque hace 19K lookups reales de fotos. Antes: 500 rápido → AllConected fallaba rápido. Ahora: 200 lento → AllConected procesa lento pero "exitoso" → dispara el siguiente poll.

## Fix aplicado (3 capas, defensa en profundidad)

### Capa 1 — Cache en RetailMind (alivio inmediato)

[`retailmind/app/api/external/views.py`](retailmind/app/api/external/views.py#L94) — `SkusPorEmpresaView.get`:

- Cache de 15 minutos por (`rut_empresa`).
- Primera llamada: 60s (calcula y cachea).
- Llamadas siguientes en los próximos 15 min: <50ms (`CACHE HIT`).

```python
cache_key = f'external_skus_v1:{rut}'
cached = cache.get(cache_key)
if cached is not None:
    return Response(cached)
# ... cálculo ...
cache.set(cache_key, response_data, timeout=900)
```

**Efecto**: aunque AllConected siga pollando agresivamente, solo la primera llamada cada 15 min toca DB. Los workers de RetailMind quedan libres para el resto del sistema.

### Capa 2 — Health check liviano en AllConected (arregla el diseño)

`system/marketplaces/retailmind/client.py:148` — nuevo método:

```python
def verificar_salud(self) -> Dict[str, Any]:
    """Health check ligero contra /api/health/ (~200 bytes, <100ms).
    NO usar obtener_skus_por_empresa() como health check.
    """
    return self._get("/api/health/")
```

`system/core/diagnostics.py:243` — `check_retailmind_skus` refactorizado:

```python
# ✅ Ahora usa health endpoint
def check_retailmind_skus(canal) -> Dict:
    client = RetailMindClient(canal=canal)
    resp = client.verificar_salud()  # ← /api/health/ ~200 bytes
    if resp.get("success"):
        return _result("ok", "Endpoint responde correctamente", ...)
```

Nombre de función mantenido para backward-compat con `DiagnosticEngine.run()`. Etiqueta UI actualizada a "Conexión RetailMind" (más preciso semánticamente).

**Efecto**: cada diagnóstico ahora baja de ~60s a ~100ms. Si la pantalla que pollea diagnósticos sigue activa, ya no causa daño.

### Capa 3 — Investigación pendiente

Aún hay que encontrar **qué/quién está pollando `POST /api/canal/<id>/diagnosticar/`**. Sospechosos:

- Tab del navegador con `gestion_holdingtebes.html` abierta (hay `setInterval(..., 2000)` ahí).
- Algún script externo / monitoring tool.
- Otra pantalla con polling no documentado.

Para detectarlo en producción de AllConected:

```bash
grep "POST.*diagnosticar" /var/log/nginx/access.log | awk '{print $1, $4}' | sort | uniq -c | sort -rn | head -20
```

Eso muestra IPs únicas y timestamps de polling. Si hay 1 IP haciendo >100 requests/hora → es alguien con tab abierta.

## Comandos para aplicar

### En RetailMind

```bash
cd C:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind
git add retailmind/app/api/external/views.py FIX_SKUS_POLLING_PRODUCCION.md
git commit -m "perf(api/skus): cache 15min para absorber polling agresivo desde AllConected"
git push
```

Después del deploy, verifica en logs que aparezca `→ CACHE HIT`:

```bash
tail -f /var/log/retailmind/access.log | grep "external/skus"
```

### En AllConected

```bash
cd C:\Users\cromo\Documents\DjangoProyects\VicentAllEcommercesConected
git add system/marketplaces/retailmind/client.py system/core/diagnostics.py
git commit -m "perf(diagnostics): usar /api/health/ en check_retailmind_skus en lugar de catálogo completo"
git push
```

## Rollback

Ambos cambios son backward-compatible. Si algo se rompe:

**RetailMind**: revertir es seguro, el cache es opcional:
```bash
git revert <hash>
```

**AllConected**: si `/api/health/` no existe en algún canal viejo, el check fallará pero sin generar carga. Si quieres volver al comportamiento anterior:
```bash
git revert <hash>
```

## Validación post-deploy

### En RetailMind

```bash
# Logs deberían mostrar mayoritariamente CACHE HIT
tail -f logs/info.log | grep "external/skus"
# Antes: 19091 productos cada llamada
# Después: CACHE HIT cada llamada (excepto 1 cada 15 min)
```

```sql
-- Workers liberados → otros endpoints rinden
-- Ver tiempos de respuesta de POS, dashboards, etc.
```

### En AllConected

Abrir el modal "Diagnosticar canal" en `gestion-canales/` para un canal RetailMind. Debería tardar **menos de 2 segundos** (antes: 60-90s). El check ahora dice "Conexión RetailMind: Endpoint responde correctamente" en lugar de "X articulos disponibles".

## Archivos modificados

### En SistemaRetailMind
- [retailmind/app/api/external/views.py](retailmind/app/api/external/views.py#L94) — Cache en `SkusPorEmpresaView`

### En VicentAllEcommercesConected
- `system/marketplaces/retailmind/client.py:148` — Nuevo método `verificar_salud()`
- `system/core/diagnostics.py:243` — `check_retailmind_skus` ahora usa health endpoint

## Métricas esperadas

| Métrica | Antes | Después |
|---|---|---|
| Tiempo de `/api/skus/` (cold) | 60-90s | 60-90s (1ra vez cada 15min) |
| Tiempo de `/api/skus/` (warm) | 60-90s | <50ms |
| Tiempo de diagnosticar canal | 60-90s | <2s |
| Workers gunicorn ocupados | 2 de 2 (saturado) | 0-1 (normal) |
| Polling impacto en DB | 19K queries/poll | 0 queries (cache hit) |

## Lecciones aprendidas

1. **Un health check NUNCA debería descargar el catálogo completo**. Usar siempre endpoints `/health/` o `/ping/` con respuesta mínima.
2. **Si un endpoint es caro, debe tener cache** — sobre todo si lo consume polling externo.
3. **Endpoints externos deben tener observabilidad**: las primeras 4 horas de degradación pasaron desapercibidas porque no había alertas en P99 de `/api/skus/`.
