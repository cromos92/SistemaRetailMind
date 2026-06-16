# App Flutter — Fidelización de Clientes ("Paola" / puntos)

> Especificación de implementación para la **app móvil de clientes finales** (Android + iOS)
> que consume la API REST `/api/v1/cliente/` del backend SistemaRetailMind.
>
> El **backend ya está construido y verificado** (auth RUT+OTP, puntos, gift cards, perfil,
> carnet). Este documento describe **solo el lado Flutter**: qué pantallas, qué llamadas,
> cómo manejar tokens y qué entregar.

---

## 0. TL;DR — qué hace la app

1. El cliente abre la app → ingresa su **RUT** → recibe un **código de 6 dígitos por email**.
2. Ingresa el código → queda logueado (guarda tokens JWT).
3. Ve su **saldo de puntos**, valor en pesos y puntos por vencer.
4. Ve su **historial de movimientos** (acumulaciones, canjes, expiraciones).
5. Ve sus **gift cards** (saldo y estado).
6. Muestra un **carnet con QR** de su RUT para que el cajero lo escanee en caja.
7. Edita su **perfil** (email / celular).

No hay contraseñas. No hay registro self-service: el cliente **debe existir** en el CRM
(creado en caja). Si su RUT no está, la app solo dice "acércate a la tienda".

---

## 1. Stack y decisiones técnicas

| Tema | Decisión |
|------|----------|
| Framework | **Flutter** (un código → Android + iOS) |
| Lenguaje | Dart (null-safety) |
| State management | **Riverpod** (recomendado) o Provider. Evita pasar `setState` entre pantallas para auth |
| HTTP | **dio** (interceptores para token + refresh automático) |
| Almacenamiento seguro de tokens | **flutter_secure_storage** (Keychain en iOS / Keystore en Android). NUNCA SharedPreferences para el refresh token |
| QR | **qr_flutter** (dibuja el QR del RUT) |
| Routing | **go_router** |
| Modelos/JSON | `json_serializable` + `freezed` (opcional pero recomendado) |
| Min SDK | Android API 23+ / iOS 13+ |

### Estructura de carpetas sugerida

```
lib/
├── main.dart
├── app.dart                      # MaterialApp + router + theme
├── core/
│   ├── api_client.dart           # dio + interceptores (auth, refresh, errores)
│   ├── token_storage.dart        # flutter_secure_storage wrapper
│   ├── config.dart               # baseUrl por entorno
│   └── result.dart               # tipo Result<T> / manejo de errores
├── features/
│   ├── auth/
│   │   ├── auth_repository.dart   # solicitar/verificar OTP, refresh, logout
│   │   ├── auth_controller.dart   # estado de sesión (Riverpod)
│   │   ├── login_rut_screen.dart  # paso 1: ingresar RUT
│   │   └── login_otp_screen.dart  # paso 2: ingresar código
│   ├── puntos/
│   │   ├── puntos_repository.dart
│   │   ├── home_screen.dart       # saldo + accesos
│   │   └── movimientos_screen.dart
│   ├── giftcards/
│   │   ├── giftcards_repository.dart
│   │   └── giftcards_screen.dart
│   ├── carnet/
│   │   └── carnet_screen.dart     # QR del RUT
│   └── perfil/
│       ├── perfil_repository.dart
│       └── perfil_screen.dart
└── models/
    ├── saldo.dart
    ├── movimiento.dart
    ├── giftcard.dart
    └── perfil.dart
```

---

## 2. Configuración del backend (lo que ya existe)

- **Base URL (producción):** `https://retail.webappsolutions.cl/api/v1/cliente/`
- **Base URL (dev local):** `http://10.0.2.2:8000/api/v1/cliente/` (emulador Android apunta al host con `10.0.2.2`; iOS simulator usa `http://localhost:8000`)
- **Auth header:** `Authorization: Bearer <access_token>`
- **Content-Type:** `application/json`
- **CORS / headers:** la app móvil no necesita `X-Device-ID` (eso es del POS desktop). Solo manda `Authorization`.

> ⚠️ El access token dura **12 horas**; el refresh dura **7 días** y **rota en cada uso**
> (si reusas un refresh ya usado, el backend invalida toda la sesión por seguridad). El
> interceptor de dio debe manejar el refresh **una sola vez** por respuesta 401.

---

## 3. Contrato de la API (endpoints exactos)

Todos los endpoints cuelgan de `/api/v1/cliente/`. Las respuestas llevan `"success": true/false`.

### 3.1 `POST auth/solicitar-otp/` — pedir el código  *(sin token)*

Request:
```json
{ "rut": "12.345.678-9", "canal": "EMAIL" }
```
- `canal` es opcional, default `"EMAIL"`. (SMS aún no implementado en backend.)

Response `200` (SIEMPRE genérica, exista o no el RUT — anti-enumeración):
```json
{ "success": true, "mensaje": "Si el RUT está registrado, te enviamos un código de acceso." }
```
Response `400` (RUT con formato inválido):
```json
{ "rut": ["El RUT no es válido."] }
```
Response `429` (throttle): superó **5 solicitudes/hora**.

> **UX clave:** como la respuesta es genérica, la app SIEMPRE avanza a la pantalla del
> código tras un 200. No puedes saber si el RUT existe — y eso es a propósito.

### 3.2 `POST auth/verificar-otp/` — canjear el código por tokens  *(sin token)*

Request:
```json
{ "rut": "12.345.678-9", "codigo": "482913" }
```
Response `200`:
```json
{
  "success": true,
  "access": "<JWT access>",
  "refresh": "<uuid refresh>",
  "expires_at": "2026-06-16T23:00:00+00:00",
  "cliente": { "id": 42, "nombre_completo": "Juan Pérez", "rut": "12.345.678-9", "email": "j@x.cl" }
}
```
Response `400`:
```json
{ "success": false, "error": "Código inválido o expirado." }
```
- El backend NO distingue "RUT no existe" de "código malo" (mismo mensaje).
- Throttle: **10 verificaciones/hora**.
- Tras **5 intentos fallidos** la cuenta se bloquea 15 min (recibirás `400` con mensaje de bloqueo).

**Acción de la app al 200:** guardar `access` + `refresh` en `flutter_secure_storage` y navegar al Home.

### 3.3 `POST auth/refresh/` — renovar el access  *(sin token, manda el refresh)*

Request:
```json
{ "refresh": "<uuid refresh actual>" }
```
Response `200`:
```json
{ "success": true, "access": "<nuevo JWT>", "refresh": "<nuevo uuid>", "expires_at": "..." }
```
Response `401`: token inválido/expirado/reusado → **forzar logout** (borrar tokens, volver a login).

> **El refresh ROTA:** la respuesta trae un refresh NUEVO. Debes **reemplazar** el guardado.
> Si guardas mal y reusas el viejo, el backend mata la sesión entera.

### 3.4 `POST auth/logout/`  *(requiere token)*

Request:
```json
{ "refresh": "<uuid refresh actual>" }
```
Response `200`: `{ "success": true }`. Borra los tokens locales igual aunque falle.

### 3.5 `GET puntos/saldo/`  *(requiere token)*
```json
{ "success": true, "cliente": "Juan Pérez", "saldo_puntos": 1240, "valor_pesos": 12400, "puntos_por_vencer": 80 }
```
- `puntos_por_vencer` = puntos que expiran en los próximos 30 días (mostrar como aviso).

### 3.6 `GET puntos/movimientos/?page=1&page_size=20`  *(requiere token)*
```json
{
  "success": true,
  "count": 53,
  "next": "http://.../movimientos/?page=2",
  "previous": null,
  "results": [
    {
      "tipo": "ACUMULACION",
      "tipo_display": "Acumulación por compra",
      "puntos": 50,
      "saldo_resultante": 1240,
      "fecha": "2026-06-10T15:30:00Z",
      "fecha_expiracion": "2027-06-10",
      "observaciones": "Compra ticket 12345 ($50.000)"
    }
  ]
}
```
- `page_size` máx 100. Paginación estándar DRF (`count` / `next` / `previous` / `results`).
- `puntos` positivo = suma; negativo = canje/expiración. Colorea verde/rojo por signo.
- Tipos posibles: `ACUMULACION`, `CANJE`, `EXPIRACION`, `AJUSTE`, `REVERSA`, `BIENVENIDA`.

### 3.7 `GET giftcards/`  *(requiere token)*
```json
{
  "success": true,
  "results": [
    {
      "codigo": "GC-ABCD-1234-WXYZ",
      "saldo_actual": 15000,
      "estado": "ACTIVA",
      "estado_display": "Activa",
      "fecha_vencimiento": "2026-12-31",
      "esta_vencida": false
    }
  ]
}
```
- Estados: `ACTIVA`, `AGOTADA`, `ANULADA`, `VENCIDA`, `BLOQUEADA`.
- **El backend NO expone el PIN** (a propósito). No lo pidas.

### 3.8 `GET perfil/`  ·  `PATCH perfil/`  *(requiere token)*

GET:
```json
{ "success": true, "perfil": { "id": 42, "nombre": "Juan", "apellido": "Pérez",
  "nombre_completo": "Juan Pérez", "rut": "12.345.678-9", "email": "j@x.cl",
  "celular": "912345678", "tipo_cliente": "INDIVIDUAL" } }
```
PATCH (solo `email` y/o `celular` son editables):
```json
{ "email": "nuevo@x.cl", "celular": "+56 9 8765 4321" }
```
Response `200`: `{ "success": true, "perfil": { ... } }`
Response `400`: errores de validación por campo (`email`/`celular` inválidos).
- Al cambiar email/celular el backend resetea la verificación de ese canal (transparente para la app).

### 3.9 `GET carnet/`  *(requiere token)*
```json
{ "success": true, "rut": "123456789", "rut_formateado": "12.345.678-9",
  "nombre_completo": "Juan Pérez", "qr_payload": "123456789" }
```
- **Dibuja el QR con `qr_payload`** (RUT sin puntos ni guion). El cajero lo escanea en el POS,
  que identifica al cliente por RUT y le acumula puntos.
- Muestra `rut_formateado` y `nombre_completo` como texto debajo del QR.

---

## 4. Tabla resumen de endpoints

| Pantalla | Método | Ruta | Token |
|----------|--------|------|-------|
| Login paso 1 | POST | `auth/solicitar-otp/` | No |
| Login paso 2 | POST | `auth/verificar-otp/` | No |
| (interceptor) | POST | `auth/refresh/` | No (manda refresh) |
| Cerrar sesión | POST | `auth/logout/` | Sí |
| Home | GET | `puntos/saldo/` | Sí |
| Movimientos | GET | `puntos/movimientos/` | Sí |
| Gift cards | GET | `giftcards/` | Sí |
| Perfil (ver/editar) | GET/PATCH | `perfil/` | Sí |
| Carnet QR | GET | `carnet/` | Sí |

---

## 5. Flujo de pantallas

```
┌─────────────┐   RUT válido    ┌──────────────┐   código OK   ┌──────────┐
│ Login RUT   │ ───POST otp───▶ │ Login OTP    │ ──POST verif─▶│  HOME    │
│ (paso 1)    │                 │ (paso 2)     │   guarda      │ (saldo)  │
└─────────────┘                 └──────────────┘   tokens      └────┬─────┘
                                                                     │
        ┌────────────────────────────┬───────────────┬─────────────┤
        ▼                            ▼               ▼             ▼
  ┌───────────┐              ┌─────────────┐  ┌───────────┐  ┌──────────┐
  │Movimientos│              │ Gift Cards  │  │  Carnet   │  │  Perfil  │
  └───────────┘              └─────────────┘  │   (QR)    │  │(ver/edit)│
                                              └───────────┘  └──────────┘
```

**Arranque de la app (splash):**
1. ¿Hay refresh token guardado? No → Login RUT.
2. Sí → intentar `GET puntos/saldo/`. Si 401 → intentar refresh. Si refresh falla → Login RUT.
3. Si saldo OK → Home.

---

## 6. Detalle por pantalla

### 6.1 Login — Paso 1 (RUT)
- Input de RUT con **formateo en vivo** y validación de DV chileno en el cliente (módulo 11)
  antes de enviar (ahorra un round-trip; el backend igual valida).
- Botón "Enviar código" → `POST auth/solicitar-otp/`.
- Al 200 → navegar a Paso 2 pasando el RUT. Mostrar "Te enviamos un código a tu email".
- Manejar `400` (RUT inválido) y `429` (límite: "Intenta de nuevo en una hora").

### 6.2 Login — Paso 2 (OTP)
- 6 cajas de un dígito (o un solo input de 6 dígitos) + auto-focus.
- Botón "Verificar" → `POST auth/verificar-otp/` con `{rut, codigo}`.
- Al 200 → guardar tokens (secure storage) → navegar a Home (limpiar el stack).
- Al 400 → "Código inválido o expirado" + botón "Reenviar código" (vuelve a llamar solicitar-otp).
- Timer de reenvío (p. ej. 60s) para no spamear el endpoint (throttle 5/h).

### 6.3 Home (saldo)
- `GET puntos/saldo/`. Card grande con `saldo_puntos`, subtítulo `valor_pesos` ($).
- Si `puntos_por_vencer > 0` → banner "Tienes X puntos por vencer pronto".
- Pull-to-refresh. Accesos a Movimientos, Gift Cards, Carnet, Perfil.

### 6.4 Movimientos
- `GET puntos/movimientos/` con **scroll infinito** (usa `next`).
- Cada ítem: ícono por `tipo`, `tipo_display`, fecha formateada (zona `America/Santiago`),
  `puntos` con signo y color, saldo resultante.

### 6.5 Gift Cards
- `GET giftcards/`. Lista de tarjetas con `saldo_actual` ($), `estado_display`, vencimiento.
- Atenuar (gris) las que `esta_vencida` o estado ≠ `ACTIVA`.
- Estado vacío: "No tienes gift cards".

### 6.6 Carnet (QR)
- `GET carnet/`. `QrImageView(data: qr_payload)` grande y centrado.
- Texto: `nombre_completo` + `rut_formateado`.
- Subir brillo de pantalla al máximo mientras está abierta (paquete `screen_brightness`)
  para que el escáner del POS lo lea bien.

### 6.7 Perfil
- `GET perfil/` para precargar. Campos editables: `email`, `celular` (resto solo lectura).
- Botón "Guardar" → `PATCH perfil/`. Mostrar errores por campo del 400.
- Botón "Cerrar sesión" → `POST auth/logout/` + borrar tokens + ir a Login.

---

## 7. Manejo de tokens (lo más importante)

### token_storage.dart
```dart
class TokenStorage {
  final _storage = const FlutterSecureStorage();
  Future<void> save({required String access, required String refresh}) async {
    await _storage.write(key: 'access', value: access);
    await _storage.write(key: 'refresh', value: refresh);
  }
  Future<String?> get access async => _storage.read(key: 'access');
  Future<String?> get refresh async => _storage.read(key: 'refresh');
  Future<void> clear() async => _storage.deleteAll();
}
```

### Interceptor de dio (auth + refresh automático)
```dart
// Pseudocódigo del onError del interceptor:
// 1. Si la respuesta es 401 y NO es la propia llamada a auth/refresh/:
// 2.   leer refresh guardado; si no hay -> forzar logout.
// 3.   POST auth/refresh/ { refresh }.
// 4.   si 200 -> GUARDAR el access Y el refresh NUEVOS (rota!), reintentar la request original 1 sola vez.
// 5.   si falla -> clear() + redirigir a login.
// Usa un lock/Completer para que requests concurrentes no disparen N refresh a la vez.
```

**Reglas de oro:**
- El **refresh rota**: cada `auth/refresh/` devuelve un refresh nuevo → reemplázalo SIEMPRE.
- **Un solo refresh concurrente:** si 3 requests fallan con 401 a la vez, dispara UN refresh y
  encola los reintentos (si no, reusas el refresh viejo y el backend mata la sesión).
- Nunca reintentar `auth/refresh/` ni `auth/verificar-otp/` en el interceptor (evita loops).
- El refresh va en el **body**, no en el header.

---

## 8. Manejo de errores / códigos HTTP

| Código | Significado | Acción en la app |
|--------|-------------|------------------|
| `200` | OK | continuar |
| `400` | validación / OTP inválido | mostrar `error` o errores por campo |
| `401` | token inválido/expirado | intentar refresh; si falla → login |
| `429` | throttle (límite por hora) | "Demasiados intentos, espera una hora" |
| `5xx` / timeout | servidor/red | "Sin conexión, reintenta" + botón retry |

---

## 9. Modelos Dart (ejemplos mínimos)

```dart
class Saldo {
  final int saldoPuntos;
  final int valorPesos;
  final int puntosPorVencer;
  final String? cliente;
  Saldo.fromJson(Map<String, dynamic> j)
      : saldoPuntos = j['saldo_puntos'] ?? 0,
        valorPesos = j['valor_pesos'] ?? 0,
        puntosPorVencer = j['puntos_por_vencer'] ?? 0,
        cliente = j['cliente'];
}

class Movimiento {
  final String tipo, tipoDisplay;
  final int puntos, saldoResultante;
  final DateTime fecha;
  final DateTime? fechaExpiracion;
  final String? observaciones;
  Movimiento.fromJson(Map<String, dynamic> j)
      : tipo = j['tipo'],
        tipoDisplay = j['tipo_display'],
        puntos = j['puntos'],
        saldoResultante = j['saldo_resultante'],
        fecha = DateTime.parse(j['fecha']),
        fechaExpiracion = j['fecha_expiracion'] != null ? DateTime.parse(j['fecha_expiracion']) : null,
        observaciones = j['observaciones'];
}

class GiftCard {
  final String codigo, estado, estadoDisplay;
  final int saldoActual;
  final bool estaVencida;
  final DateTime? fechaVencimiento;
  GiftCard.fromJson(Map<String, dynamic> j)
      : codigo = j['codigo'],
        estado = j['estado'],
        estadoDisplay = j['estado_display'],
        saldoActual = j['saldo_actual'],
        estaVencida = j['esta_vencida'] ?? false,
        fechaVencimiento = j['fecha_vencimiento'] != null ? DateTime.parse(j['fecha_vencimiento']) : null;
}
```

---

## 10. Diseño / branding (alinear con NEXO)

El backend usa el design system **NEXO** (web). Para coherencia de marca:
- Paleta: primary `#0066FF`, dark `#1A1A2E`, accent `#00D4AA`.
- Targets táctiles ≥ 48px, tipografía 16-18px (la base ya piensa en táctil, ver `pos-kiosk.css`).
- Define un `ThemeData` central en `app.dart` con esos colores y reúsalo.
- Formatea pesos chilenos con separador de miles de punto (`$12.400`) e idioma `es_CL`
  (paquete `intl`, `NumberFormat.currency(locale: 'es_CL', symbol: '\$', decimalDigits: 0)`).

---

## 11. Plan de entrega (orden sugerido)

1. **Scaffold + theme + config** (baseUrl por entorno, dio, secure storage).
2. **Auth completo** (login RUT → OTP → tokens → interceptor refresh). Es el cimiento; sin esto nada funciona.
3. **Home (saldo)** — primer dato real, valida el flujo end-to-end.
4. **Movimientos** (scroll infinito).
5. **Carnet QR** (alto valor, bajo esfuerzo).
6. **Gift cards**.
7. **Perfil** (ver + editar + logout).
8. **Pulido:** estados de carga/vacío/error, pull-to-refresh, splash con auto-login.

---

## 12. Checklist de publicación en tiendas

- **iOS (App Store):** cuenta Apple Developer ($99/año). Apple suele exigir "Sign in with Apple"
  solo si ofreces login social de terceros — como aquí es OTP propio, normalmente no aplica, pero
  revisa la guideline vigente. Configura `Info.plist` (permiso de red, no usa cámara salvo que
  agregues escaneo).
- **Android (Play Store):** cuenta Google Play ($25 único). `applicationId` único, firma de release.
- **Ambos:** política de privacidad (manejas RUT y email → obligatoria), íconos/splash, build de release.
- **Backend antes de publicar:** la API debe estar en HTTPS (ya lo está en Railway/DigitalOcean) y
  el `DEFAULT_FROM_EMAIL` con un remitente real (el OTP llega por email).

---

## 13. Dependencias Flutter sugeridas (`pubspec.yaml`)

```yaml
dependencies:
  flutter:
    sdk: flutter
  dio: ^5.x
  flutter_secure_storage: ^9.x
  flutter_riverpod: ^2.x
  go_router: ^14.x
  qr_flutter: ^4.x
  intl: ^0.19.x
  screen_brightness: ^1.x       # subir brillo en pantalla del carnet
```

---

## 14. Notas de coherencia con el backend (no romper)

- El login es **RUT + OTP por email**. No inventes pantalla de contraseña.
- **No hay registro self-service**: si `solicitar-otp` no encuentra el RUT, el cliente igual ve
  "te enviamos un código" (anti-enumeración) pero nunca llega el email. La app no puede ni debe
  distinguir ese caso. En la pantalla del OTP, ofrece "¿No te llegó? Acércate a la tienda".
- El **carnet QR lleva solo el RUT** (`qr_payload`). El POS hace el resto.
- Respeta los **throttles**: 5 solicitudes/h, 10 verificaciones/h. Pon timers de reenvío.
- Zona horaria de las fechas: el backend está en `America/Santiago`. Formatea en local.
```
