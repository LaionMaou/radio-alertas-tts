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
