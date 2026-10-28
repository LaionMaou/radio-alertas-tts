# Radio Alerts TTS

Cadena independiente de alertas por voz para la radio en vivo de AzuraCast.
Recibe un texto, lo sintetiza en japonés con **Kokoro** (offline), lo inyecta
en la transmisión por el **WebDJ** y avisa a **Discord**. Si el WebSocket falla
antes de enviar audio, reintenta por el puerto **Streamer/DJ** con Icecast.

Este proyecto vive **fuera** de la app Kuma: es un servicio propio, con su
propio repositorio, su propio `.env` y su propio contenedor. No modifica la
instancia de AzuraCast ni su configuración.

## Restricciones respetadas

| Restricción | Cómo se cumple |
| --- | --- |
| Kokoro, no ElevenLabs | `kokoro-ja-voice` con modelo ONNX int8 local |
| Sin automatización de navegador | Cliente WebSocket directo (`websockets`) |
| Sin cuenta de administración | Solo una cuenta Streamer/DJ de la estación |
| Sin cola de reproducción | El WebDJ interrumpe el AutoDJ; al cerrar el socket, el AutoDJ retoma |
| Audio corto | `MAX_TEXT_CHARS` (300 por defecto, tope duro 600) |
| Sin afectar producción | Contenedor aislado, límites de CPU/RAM, sin tocar la API admin |

## Arquitectura

```
backend de alertas (P2P地震情報 / JMA)
        │  POST /alert  (Bearer token)
        ▼
┌─────────────────────────────────────────────┐
│ contenedor alerts-tts                       │
│                                             │
│  API FastAPI ──► cola de 1 lugar ──► worker │
│                                   │         │
│                          Kokoro TTS (WAV)   │
│                                   │         │
│                     ffmpeg WebM/Opus (-re)  │
│                                   │         │
│                    WebDJ WebSocket ──► AzuraCast
│                       │ (si falla antes de audio)
│                       └──► Icecast :8005    │
│                                             │
│  Discord webhook ◄── resultado y métricas   │
│  data/alerts/<id>/ {wav, webm, meta.json}   │
└─────────────────────────────────────────────┘
```

La cola de un solo lugar no es una cola de reproducción: si llega una alerta
nueva mientras otra espera, la vieja se descarta (`LATEST_WINS`). El audio se
envía a ritmo real para que Liquidsoap lo reproduzca como fuente en vivo.

## Endpoint correcto del WebDJ

El brief original indicaba `/streamer/{shortcode}/websocket`. Ese endpoint no
existe. La URL real es:

```
wss://{host}{base_path}/webdj/{station_short_name}{dj_mount_point}
```

El detalle del protocolo verificado (subprotocolo `webcast`, `hello`,
`metadata`, frames binarios y cierre) está en
[`docs/PROTOCOLO_WEBDJ.md`](docs/PROTOCOLO_WEBDJ.md).

## Requisitos

- Docker con Compose v2.
- Una estación de AzuraCast con cuentas de Streamer/DJ habilitadas.
- Salida a internet solo para el webhook de Discord (Kokoro corre offline).
- ~2 GB de RAM y hasta 3 vCPU disponibles para el contenedor (ver límites en
  `docker-compose.yml`).

## Inicio rápido

```bash
cp .env.example .env
```

Editar `.env` como mínimo:

- `AZURACAST_BASE_URL` y `AZURACAST_STATION_SHORT_NAME`
- `AZURACAST_STREAMER_USER` y `AZURACAST_STREAMER_PASSWORD`
- `AZURACAST_DJ_HOST` y `AZURACAST_DJ_PORT`
- `API_TOKEN` (por ejemplo, `openssl rand -hex 32`)
- `DISCORD_WEBHOOK_URL` (opcional)

Dejar `DRY_RUN=true` para la primera prueba.

```bash
docker compose up -d --build
docker compose logs -f alerts-tts
```

Comprobar salud:

```bash
curl -s http://127.0.0.1:8090/health
```

Debe responder `"status":"ok"` y `"model_ready":true`.

## Fase 1 — TTS y medición

El modelo se ensambla durante el build, así que el primer aviso no espera la
carga inicial del ONNX. Para medir tiempo de generación, RTF y memoria en el
VPS:

```bash
docker compose run --rm alerts-tts python /app/scripts/benchmark_tts.py --runs 5 --out /data/benchmark.json
```

Salida de ejemplo para completar la tabla de la fase:

| Equipo | Voz | Texto (car.) | Audio (s) | Generación (s) | RTF | RAM pico (MB) |
| --- | --- | --- | --- | --- | --- | --- |
| VPS 4 vCPU / 8 GB | jf_alpha | 54 | (medir) | (medir) | (medir) | (medir) |

`RTF = generación / duración del audio`. Menor que 1 significa que sintetiza
más rápido de lo que se reproduce. En una máquina de desarrollo se midió
RTF ≈ 1.4 para 50 caracteres (el audio de 9.5 s tardó ~13 s en generarse); el
valor del VPS puede ser mejor o peor. Si el RTF medido es mayor que 1, la
alerta tarda en empezar más de lo que dura: conviene acortar el texto o
asignar más CPU al contenedor.

También se puede generar un WAV sin transmitir:

```bash
docker compose exec alerts-tts python -m radio_alerts_tts.cli --dry-run "これはテストです。"
```

El resultado queda en `data/alerts/<id>/`. La primera síntesis de un proceso
incluye la carga del modelo (varios segundos); el servicio en vivo no la paga
porque lo precarga al arrancar.

## Fase 2 — Cuenta Streamer/DJ

En AzuraCast, con una cuenta de administración **solo para crear la cuenta**:

1. Estación → **Streamers** → crear `alert-bot` con contraseña larga.
2. Verificar que la cuenta esté activa y asociada a la estación correcta.
3. Estación → **Broadcasting** → comprobar que las cuentas de DJ están
   habilitadas y anotar el **DJ/Streamer Port** (por defecto `8005`).
4. No asignar permisos de administración. `alert-bot` solo debe poder
   conectarse como fuente.

Anotar también el *short name* de la estación (el que aparece en la URL
pública), que no siempre coincide con el nombre visible.

Probar la conexión con un tono de 2 segundos:

```bash
docker compose run --rm alerts-tts python /app/scripts/check_streamer.py --seconds 2
```

> El tono **se escucha en la radio**. Hacer la prueba en un momento de bajo
> tráfico y avisar al equipo. El script solo usa la cuenta streamer; no toca
> la API de administración.

Si el contenedor no llega al puerto DJ porque AzuraCast está en el host, el
`docker-compose.yml` ya define `host.docker.internal`. Si AzuraCast está en
otro servidor, usar su IP/hostname y asegurarse de que el puerto DJ esté
accesible (o ejecutar el contenedor en el mismo VPS y usar `127.0.0.1`).

## Fase 3 — Alertas en vivo

1. Poner `DRY_RUN=false` en `.env`.
2. Reiniciar: `docker compose up -d`.
3. Disparar una alerta de prueba por la API:

```bash
curl -s -X POST http://127.0.0.1:8090/alert \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "緊急地震速報。宮城県沖で強い地震が発生しました。震度5強を観測しました。",
    "kind": "eew",
    "magnitude": 7.2,
    "title": "宮城県沖",
    "dedupe_key": "eew-2026-10-25-01"
  }'
```

4. Confirmar en el stream que se escucha la alerta y que el AutoDJ retoma
   unos segundos después.
5. Confirmar el mensaje en Discord.

### Contrato de la API

`POST /alert` requiere `Authorization: Bearer <API_TOKEN>`.

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `text` | string | sí | Japonés; máximo `MAX_TEXT_CHARS` |
| `kind` | `eew` \| `tsunami` \| `typhoon` \| `other` | no | Define título y color |
| `magnitude` | number 0–12 | no | Se agrega al título |
| `title` | string | no | Lugar o detalle, máx. 200 |
| `dedupe_key` | string | no | Evita repetir la misma alerta |

Respuestas:

| Código | `status` | Significado |
| --- | --- | --- |
| 202 | `queued` | Aceptada y en cola |
| 202 | `replaced` | Aceptada; reemplazó a otra que esperaba |
| 200 | `duplicate` | Ya se procesó con esa `dedupe_key` |
| 429 | `busy` | Hay una alerta en curso y `LATEST_WINS=false` |
| 401 | — | Token ausente o incorrecto |
| 422 | — | Texto vacío, demasiado largo o `kind` inválido |

`GET /alert/{alert_id}` requiere el mismo token y devuelve el resultado completo
de una alerta ya procesada (el mismo objeto que aparece en `last_alert`). Si el id
no está en la caché en memoria (últimas 200 alertas) responde `404`. La caché se
pierde al reiniciar el contenedor; los artefactos y `meta.json` siguen en
`data/alerts/<id>/`.

`GET /health` no requiere token y devuelve:

```json
{
  "status": "ok",
  "model_ready": true,
  "model_error": null,
  "busy": false,
  "queue": 0,
  "uptime_seconds": 123.4,
  "last_alert": null,
  "version": "0.1.0"
}
```

Mientras el modelo se está cargando responde `503` con `"status":"starting"`.
Si no pudo cargar, responde `503` con `"status":"degraded"` y el error en
`model_error`.

## Operación

### Logs

Todo va a stdout como líneas JSON (un objeto por línea). Eventos principales:

| Evento | Cuándo |
| --- | --- |
| `tts.model_ready` | Terminó la carga del modelo |
| `alert.received` | Llegó una alerta (incluye el texto) |
| `tts.completed` | Audio generado, con duración y tiempo |
| `webdj.connected` / `webdj.closed` | Estado de la conexión WebDJ |
| `transport.failed` | Falló un transporte |
| `alert.completed` | Resultado final con todas las métricas |
| `alert.duplicate` | Alerta descartada por `dedupe_key` |
| `discord.sent` / `discord.*_error` | Estado de la notificación |

```bash
docker compose logs -f alerts-tts
```

Los logs no incluyen contraseñas, token ni URL del webhook.

### Artefactos

Cada alerta deja `data/alerts/<id>/` con:

- `alert.wav` — salida cruda de Kokoro (24 kHz mono).
- `alert-lead.wav` — lo que se transmite: cama de timbre (`LEAD_IN_SECONDS`),
  4 pings fuertes (`CHIME_ENABLED`) y el anuncio.
- `alert.webm` — WebM/Opus enviado (o generado en dry-run).
- `meta.json` — alerta, resultado, configuración sin secretos, host y versión.

Se conservan `ARTIFACT_RETENTION_DAYS` días (14 por defecto; `0` desactiva la
limpieza).

### Estados del resultado

- `ok`: se entregó por el transporte configurado.
- `partial`: se envió audio y la conexión falló después. **No** se reintenta
  para no repetir la alerta desde el principio.
- `failed`: no se envió audio. Se intentó el transporte alternativo si
  `ENABLE_ICECAST_FALLBACK=true`.
- `dry-run`: se generó audio pero no se transmitió.

### Fallback

Con `TRANSPORT=webdj` (recomendado) y `ENABLE_ICECAST_FALLBACK=true`, si el
WebSocket no llega a enviar audio se reintenta con `ffmpeg` como fuente
Icecast contra `AZURACAST_DJ_HOST:AZURACAST_DJ_PORT`. `ICECAST_CODEC` elige
`opus` (Ogg) o `mp3`.

## Solución de problemas

| Síntoma | Causa probable | Qué revisar |
| --- | --- | --- |
| El WebSocket cierra al enviar `hello` | Credenciales o estación incorrectas | Usuario/clave del streamer y `AZURACAST_STATION_SHORT_NAME` |
| `Connection refused` al puerto DJ | El puerto no es accesible desde el contenedor | `AZURACAST_DJ_HOST`, firewall, o ejecutar en el mismo VPS |
| La alerta se corta al empezar | Liquidsoap todavía no conmutó al streamer | Subir `LEAD_IN_SECONDS` (6–7 s) |
| La alerta se corta al final | Cierre demasiado pronto | Subir `DRAIN_SECONDS` (2–3 s) |
| El AutoDJ tarda en volver | Liquidsoap espera el cierre del harbor | Verificar que el proceso termine; revisar logs de AzuraCast |
| Primer aviso lento | Modelo cargándose | Esperar `tts.model_ready` en `/health` o reiniciar antes de operar |
| `ffmpeg falló` | WAV inválido o falta ffmpeg | Reconstruir la imagen; revisar el WAV en `data/alerts/<id>/` |
| Discord no recibe nada | Webhook inválido o `DRY_RUN=true` | `DISCORD_WEBHOOK_URL`, `NOTIFY_IN_DRY_RUN` |
| `503` en `/health` | El modelo no cargó | Ver `model_error` y los logs del arranque |

## Seguridad

- `API_TOKEN` es obligatorio y se compara en tiempo constante.
- El puerto de la API se publica solo en `127.0.0.1`; el backend de alertas
  debe correr en el mismo host o detrás de un proxy con autenticación.
- La cuenta `alert-bot` es solo Streamer/DJ: no puede administrar la estación.
- El contenedor corre como usuario sin privilegios, con
  `no-new-privileges`, CPU y memoria limitadas.
- `.env` está en `.gitignore`; los logs y `meta.json` no contienen secretos.

## Estructura

```
radio-alerts-tts/
├── src/radio_alerts_tts/
│   ├── api.py          # FastAPI + worker
│   ├── service.py      # cola, orquestación, artefactos
│   ├── tts.py          # Kokoro
│   ├── audio.py        # ffmpeg WAV -> WebM/Opus
│   ├── webdj.py        # WebSocket del WebDJ
│   ├── icecast.py      # fallback al puerto DJ
│   ├── notifier.py     # Discord
│   ├── config.py       # variables de entorno
│   ├── models.py       # AlertRequest / AlertResult
│   ├── logging_setup.py
│   ├── cli.py
│   └── errors.py
├── scripts/
│   ├── benchmark_tts.py
│   ├── check_streamer.py
│   └── healthcheck.py
├── tests/
├── docs/PROTOCOLO_WEBDJ.md
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Desarrollo local

Requiere Python 3.11+ y ffmpeg.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src python -m radio_alerts_tts.cli --dry-run "テストです。"
pytest
```

En Windows, activar con `.venv\Scripts\activate`.

## Qué no hace

- No programa ni encola música.
- No sincroniza la alerta con el tiempo de la canción (no se copió la lógica
  de Synthetic Heart).
- No usa la API de administración de AzuraCast en tiempo de ejecución.
- No usa ElevenLabs ni ningún servicio TTS comercial.
- No abre un navegador.
