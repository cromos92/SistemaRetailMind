# Despliegue en Digital Ocean App Platform — Scheduler + Seguridad

Esta guía cubre lo nuevo de la **compra/canje desde la app móvil**: cómo correr
las tareas periódicas (expiración de reservas de puntos, vales de canje y lotes
de puntos) en **Digital Ocean App Platform**, y el endurecimiento de seguridad
asociado. App Platform **no tiene cron nativo** para componentes, así que hay dos
caminos soportados; elige uno.

---

## ¿Por qué hace falta un scheduler?

| Tarea | Frecuencia | Qué pasa si no corre |
|---|---|---|
| `expirar_reservas_puntos` | cada ~5 min | Reservas de compra abandonadas no liberan el saldo (mitigado por expiración *lazy*, pero el cupón del ecommerce queda vivo hasta que corra). |
| Expirar **vales de canje** (`canje con código`) | cada ~5 min | Vales no usados siguen comprometiendo puntos (mitigado *lazy* al cotizar/generar). |
| `expirar_puntos` | diaria | **Los puntos nunca caducan.** No hay mitigación *lazy* para esto: es la tarea crítica del scheduler. |

> Las expiraciones *lazy* (al cotizar/reservar/generar) hacen que la app no se
> "rompa" sin scheduler, pero **no** sustituyen la expiración diaria de lotes de
> puntos ni la limpieza de cupones del ecommerce. El scheduler es obligatorio en
> producción.

---

## Opción A (recomendada) — Componente `worker`

Un proceso de larga vida que reusa la misma imagen Docker que la web.

1. **Código:** ya incluido — `python manage.py run_scheduler` (bucle interno).
2. **Spec:** agrega a tu app spec el bloque `workers:` de [`.do/app.yaml`](.do/app.yaml):

   ```yaml
   workers:
     - name: scheduler
       dockerfile_path: Dockerfile
       source_dir: /
       instance_count: 1            # NO escalar: una sola instancia
       instance_size_slug: basic-xxs
       run_command: python manage.py run_scheduler
       envs:
         - key: TZ
           value: America/Santiago
         - key: SCHEDULER_INTERVALO_SEG
           value: "300"
         - key: SCHEDULER_HORA_PUNTOS
           value: "4"
         # + DATABASE_URL / REDIS_URL / SECRET_KEY (las mismas que la web)
   ```

3. **Aplica el spec** (no sobrescribas tu app entera — mergea el bloque):

   ```bash
   doctl apps list
   doctl apps spec get <APP_ID> > app.actual.yaml
   # pega el bloque workers: y las envs nuevas en app.actual.yaml
   doctl apps update <APP_ID> --spec app.actual.yaml
   ```

   O por el panel: **Create Component → Worker**, misma repo/Dockerfile,
   run command `python manage.py run_scheduler`, 1 instancia `basic-xxs`.

**Pros:** autónomo, sin servicios externos. **Contra:** una instancia siempre
encendida (~costo de un `basic-xxs`).

---

## Opción B (más barata) — Disparador HTTP agendado

En vez del worker, un programador externo hace `POST` a un endpoint protegido.

1. **Setea la clave** en el servicio `web`:

   ```
   CRON_TRIGGER_KEY = <una clave larga y aleatoria>
   ```
   Sin esta env var el endpoint responde **404** (queda deshabilitado).

2. **El endpoint** (ya implementado):

   ```
   POST https://retail-ap-mh3y2.ondigitalocean.app/app/api/cron/tareas/
        Header: X-Cron-Key: <CRON_TRIGGER_KEY>
   ```
   - Cada pocos minutos: llámalo sin query → expira reservas y vales.
   - Una vez al día: llámalo con `?incluir_puntos=1` → además expira lotes de puntos.

3. **Quién lo dispara** (elige uno):
   - **DigitalOcean Functions** con *scheduled trigger* (cron `*/5 * * * *`) que hace el POST.
   - Un monitor de uptime (p.ej. cron-job.org, UptimeRobot con método POST + header).
   - **GitHub Actions** con `on: schedule`:
     ```yaml
     on:
       schedule:
         - cron: "*/5 * * * *"      # reservas + vales
         - cron: "30 7 * * *"       # 04:30 Chile (UTC-3/-4) → incluir_puntos=1
     jobs:
       cron:
         runs-on: ubuntu-latest
         steps:
           - run: |
               curl -fsS -X POST \
                 -H "X-Cron-Key: ${{ secrets.CRON_TRIGGER_KEY }}" \
                 "https://retail-ap-mh3y2.ondigitalocean.app/app/api/cron/tareas/${{ github.event.schedule == '30 7 * * *' && '?incluir_puntos=1' || '' }}"
     ```

**Pros:** sin componente extra. **Contra:** dependes de un disparador externo
(si se cae, no corre). La autenticación es por `X-Cron-Key` (comparación en
tiempo constante; 404 si no coincide → no revela el endpoint a escáneres).

---

## Migraciones

El `release` del Procfile ya corre `python manage.py migrate` en cada deploy.
Antes de desplegar, verifica localmente que las migraciones nuevas estén sanas:

```bash
python manage.py makemigrations --check --dry-run   # no deben faltar
python manage.py migrate
```

Migraciones nuevas de este trabajo: `0169_reservapuntos`, `0170_pedidoecommerce_*`,
`0171_canjevale`.

---

## Checklist de seguridad (producción)

Variables de entorno a confirmar en el servicio `web` (y el `worker`):

- [ ] `DEBUG=False`
- [ ] `SECRET_KEY` = valor largo y aleatorio (NO el `django-insecure-...` por defecto)
- [ ] `ALLOWED_HOSTS` = tu dominio (evita `*` si puedes)
- [ ] `DATABASE_URL` inyectada por la plataforma (no credenciales en el repo)
- [ ] `CRON_TRIGGER_KEY` (si usas la Opción B)
- [ ] `APP_COUPON_MAX_AMOUNT` (Constance, en el ecommerce) — tope del cupón de puntos
- [ ] `SECURE_SSL_REDIRECT=True` **solo** si confirmaste que el health check no se rompe

Endurecimiento ya aplicado en `settings.py` (activo con `DEBUG=False`):

- `SECURE_PROXY_SSL_HEADER` → Django detecta HTTPS detrás del LB de DO.
- HSTS 1 año + `includeSubDomains` + `preload`.
- Cookies `Secure` + `HttpOnly` (sesión) + `SameSite=Lax`.
- `X-Frame-Options: DENY`, `nosniff`, XSS filter.

A nivel de API móvil (ya implementado): throttling por scope
(`app_catalogo`/`app_consulta`/`app_checkout`), OTP anti-enumeración, rotación de
refresh tokens con blacklist, idempotencia en operaciones de puntos, y la API key
del ecommerce vive **solo** en el servidor (proxy), nunca en el móvil.
