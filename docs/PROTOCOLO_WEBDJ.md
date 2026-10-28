# Protocolo WebDJ de AzuraCast

Este documento describe el mecanismo real que usa este proyecto para inyectar
audio en vivo. Fue verificado leyendo el código fuente de AzuraCast, no la
documentación pública, porque el brief original asumía un endpoint que no
existe.

## Corrección importante

El brief indicaba:

```
wss://{host}/streamer/{station_shortcode}/websocket
```

Ese endpoint **no existe** en AzuraCast. La URL correcta es:

```
wss://{host}{base_path}/webdj/{station_short_name}{dj_mount_point}
```

Con una instalación en la raíz y montaje `/`, por ejemplo:

```
wss://radio.kumakuma.fm/webdj/kuma/
```

## Construcción de la URL en AzuraCast

`backend/src/Radio/Backend/Liquidsoap.php`, método `getWebStreamingUrl()`:

- Toma la URL base de la estación.
- Cambia el esquema a `wss` (o `ws` si la instalación es HTTP).
- Agrega la ruta `{basePath}/webdj/{short_name}{dj_mount_point}`.

`backend/src/Nginx/ConfigWriter.php` publica esa ruta a través del **puerto DJ**
de la estación (por defecto `8005`) y también bajo `/radio/{dj_port}`. No pasa
por la API administrativa ni por el puerto web normal de la estación.

## Handshake

1. Abrir un WebSocket con el subprotocolo `webcast`:

   ```js
   const socket = new WebSocket(url, "webcast");
   ```

2. Enviar un mensaje de texto `hello` **antes** que cualquier audio:

   ```json
   {
     "type": "hello",
     "data": {
       "mime": "audio/webm;codecs=opus",
       "user": "alert-bot",
       "password": "••••••"
     }
   }
   ```

   AzuraCast autentica esas credenciales contra el backend de la estación. La
   cuenta debe ser un **Streamer/DJ**, nunca una cuenta de administración.

3. Opcional: enviar metadata para que el reproductor muestre el título:

   ```json
   {
     "type": "metadata",
     "data": { "title": "緊急地震速報", "artist": "Kuma Radio" }
   }
   ```

4. Enviar el audio como **frames binarios** WebM/Opus. El WebDJ del navegador
   usa `MediaRecorder` con `audio/webm;codecs=opus` y cortes de 1000 ms; este
   proyecto envía el WebM generado por ffmpeg en trozos del mismo orden de
   tamaño, a ritmo real.

5. Al terminar, **cerrar el socket explícitamente**. Liquidsoap deja de
   preferir el `input.harbor` y vuelve al AutoDJ. No hace falta enviar ningún
   comando de "stop": el cierre es la señal.

## Por qué el audio se envía a ritmo real

Liquidsoap reproduce `input.harbor` como una fuente en vivo. Si se vuelca un
WebM completo de golpe, el buffer del harbor puede desbordarse y la alerta se
corta o se reproduce con saltos. Por eso:

- ffmpeg lee el WAV y escribe WebM/Opus con `-re` (ritmo real).
- Los fragmentos se envían a medida que se producen.
- `-cluster_time_limit 1000` y `-flush_packets 1` evitan que el muxer retenga
  el final del audio.

La conexión se mantiene abierta `DRAIN_SECONDS` después del último byte para
que el harbor reproduzca el final antes del cierre.

## Arranque (`LEAD_IN_SECONDS`)

El `input.harbor` de Liquidsoap no arranca a reproducir en el instante en que
llega la fuente: llena su buffer y recién entonces la transición conmuta
(`Switch to input_streamer`). Medido contra AzuraCast, ese cambio ocurre unos
**4–5 s** después de la conexión. Lo que ya estaba en el buffer puede
descartarse o llegar a sonar según el momento exacto de la conmutación: en una
prueba se perdió el comienzo de un tono y en otra los 5 s iniciales salieron
al aire tal cual.

Con silencio eso es **aire muerto**, que en radio es peor que un corte. Por eso
el tramo inicial va lleno de audio: **arranque de `LEAD_IN_SECONDS` (3 s) →
anuncio**. El contenido del arranque depende de la gravedad, elegida por el
backend en el campo `tone` de `/alert`:

| `tone` | Cuándo | Audio |
| --- | --- | --- |
| `soft` (defecto) | EEW hasta 6強, 地震情報, tsunami `Warning`/`Watch` | Cama grave: 440 Hz, nivel 0.12, un ping por segundo |
| `urgent` | EEW de 震度7 o más, 大津波警報 | 880 y 1175 Hz, niveles 0.4 y 0.25, un ping cada 0.3 s |

El `alert.wav` original no se modifica; lo que se transmite queda como
`alert-lead.wav`. La voz arranca ~3 s después de la conexión, dentro de la
ventana de conmutación medida: si la conmutación se come el comienzo, lo que se
pierde es el arranque y la voz entra casi completa. Con `CHIME_ENABLED=false` el
arranque queda en silencio (solo para pruebas); con `LEAD_IN_SECONDS=0` no hay
arranque.

Tiene que ser **audio real en el archivo**, no un filtro (`adelay`, `concat`
sobre `anullsrc`): con `-re`, ffmpeg adelanta lo que genera un filtro y lo
escribe apenas arranca en lugar de transmitirlo a ritmo real, así que el texto
vuelve a caer dentro de la ventana que se descarta. Comprobado: un tono de 3 s
con 2 s de `adelay` tarda lo mismo en transmitirse que sin filtro (2,5 s en vez
de 5 s), mientras que un WAV rellenado tarda 5 s.

Si en una instalación concreta todavía se corta la primera palabra, subir el
valor (5–7 s); si el arranque se siente largo antes de la voz, bajarlo.

El mismo WAV rellenado se usa para el fallback Icecast: comparte el mismo
harbor y la misma latencia de conmutación.

## Bucle de 大津波警報

Una alerta con `loop` (y normalmente `preempt`) no cierra el socket al terminar:
repite el anuncio hasta que un operador la corte o llegue otra alerta. La
secuencia es **arranque → anuncio → `LOOP_GAP_SECONDS` de pausa → anuncio → …**;
el repique no se repite, porque el bucle ya está en el aire.

Un solo `ffmpeg` genera el contenedor entero:

```
ffmpeg -re -f concat -safe 0 -i alert-loop.ffconcat … -c:a libopus -f webm pipe:1
```

`alert-loop.ffconcat` lista el `alert-lead.wav` una vez y `alert-loop.wav` (voz
más la pausa) muchas veces. **La lista es larga a propósito**: el demuxer
`concat` conserva los timestamps, así que un único `-re` mantiene el ritmo real
y el WebM queda continuo, sin cortes entre vueltas.

Lo que **no** funciona, medido en local: el filtro `concat` con `-stream_loop -1`
reinicia el reloj de `-re` en cada vuelta y transmite a **1.29×** realtime; el
harbor se desborda y la alerta se corta o se distorsiona. La medición se hace
contando paquetes Opus (`nb_read_packets` × 0.02 s) contra el reloj de pared: el
bitrate no sirve porque Opus es variable (un seno a `-b:a 32k` sale a ~54 kbps).

La lista cubre 24 h (`LOOP_MAX_SECONDS`). Es un tope de seguridad, no el
comportamiento esperado: si alguien olvida el comando de stop, el bucle se
detiene solo y el log `alert.loop_started` deja el comando a mano. Para
cortarlo antes:

```bash
docker compose exec alerts-tts python -m radio_alerts_tts.cli stop
```

El comando hace `POST /stop` en `127.0.0.1` con el token del contenedor, así que
la credencial no aparece en el shell. El corte mata el `ffmpeg` y cierra el
socket: Liquidsoap vuelve al AutoDJ y el resultado queda `stopped`.

Si llega otra alerta mientras el bucle suena, el bucle deja de enviar en el
siguiente fragmento, la nueva alerta se procesa y el resultado del bucle queda
`partial`. Si la nueva también pide `loop`, el bucle recomienza con su texto.

## Fallback Icecast

El mismo `input.harbor` acepta fuentes Icecast/SHOUTcast en el puerto DJ. Si el
WebSocket falla **antes de enviar audio**, el servicio reintenta por ese puerto
con `ffmpeg` como cliente de fuente (`icecast://user:pass@host:port/mount`),
usando Ogg/Opus o MP3 según `ICECAST_CODEC`.

Si el WebSocket ya envió audio y luego falla, **no** se reintenta: repetir la
alerta desde el principio sería peor que dejarla incompleta. El resultado queda
como `partial`.

## Fuentes verificadas

- `frontend/components/Public/WebDJ/useWebcaster.ts` — subprotocolo, `hello`,
  metadata, frames binarios y cierre.
- `frontend/components/Public/WebDJ/useWebDjNode.ts` — MIME
  `audio/webm;codecs=opus` y cortes de `mediaRecorder.start(1000)`.
- `backend/src/Radio/Backend/Liquidsoap.php` — `getWebStreamingUrl()`.
- `backend/src/Radio/Backend/Liquidsoap/ConfigWriter.php` — `input.harbor(...)`
  con fallback al AutoDJ.
- `backend/src/Nginx/ConfigWriter.php` — proxy de `/webdj/` y `/radio/{dj_port}`
  al puerto DJ.

Repositorio: <https://github.com/AzuraCast/AzuraCast>

## Cómo comprobarlo contra una instalación real

1. Abrir la página pública de WebDJ de la estación en un navegador.
2. Abrir las herramientas de desarrollo, pestaña Network → WS.
3. Conectar con la cuenta streamer y observar:
   - URL del socket (`/webdj/...`).
   - Primer mensaje de texto con `type: "hello"`.
   - Frames binarios mientras se habla.
   - Cierre del socket al detener la transmisión.

Esa traza es la referencia definitiva para esa versión de AzuraCast. Si una
actualización cambia el protocolo, este documento y `webdj.py` deben
actualizarse juntos.
