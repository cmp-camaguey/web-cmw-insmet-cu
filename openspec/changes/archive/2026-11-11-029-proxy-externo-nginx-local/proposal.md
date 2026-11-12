# 029 — Proxy externo con Nginx local

## Motivación

El proveedor termina TLS con Nginx Proxy Manager (NPM) corriendo en Docker **en
otra máquina**. `install.sh` ofrece `PROXY_MODE=external` para esa topología y,
en esa rama, no instala Nginx.

Eso deja el sitio sin `media/`:

- En producción `/media/` no resuelve a ninguna ruta de Django; WhiteNoise solo
  sirve `staticfiles`.
- Lo único que sirve `media/` es `location /media/ { alias ... }` del vhost de
  Nginx, y en `external` ese vhost no existe.
- En `media/` viven `brand_logo` y `favicon` (la marca de todas las páginas),
  las imágenes de servicios y los PDFs de avisos, contratos, certificados y
  publicaciones.

Además, la salida impresa para NPM describe campos que NPM no tiene
(`Forward Hostname = $host`, `Forward Proto = $scheme`), pone `Scheme https`
cuando Gunicorn habla HTTP, y sugiere montar el socket unix dentro del
contenedor, que la UI de NPM no puede usar.

## Solución

`PROXY_MODE=external` pasa a significar **"el TLS termina afuera"**, no "no hay
Nginx acá":

1. Nginx se instala siempre. En modo externo escucha HTTP en `$HTTP_PORT`,
   sirve `/media/` y `/static/`, y hace de proxy local a Gunicorn por el socket
   unix.
2. NPM apunta a `IP_LAN_DEL_LXC:$HTTP_PORT` con `Scheme http` y sigue emitiendo
   el certificado.
3. Gunicorn nunca se expone a la red: sigue escuchando solo en el socket unix.
4. Un vhost nuevo, `deploy/nginx/webcmp-http.conf.example`, sin TLS ni
   redirección, con un `map` que **pasa** el `X-Forwarded-Proto` que manda NPM
   en lugar de reescribirlo con `$scheme`.
5. El health check del deploy pasa a atravesar Nginx por HTTP (`--resolve` a
   127.0.0.1), en vez de ir directo al socket: así detecta un vhost roto que
   antes quedaba en verde.

## Criterios de aceptación

- `PROXY_MODE=external` instala Nginx, habilita el vhost HTTP y arranca el
  servicio.
- `/static/` y `/media/` responden 200 a través de Nginx en modo externo.
- `POST`/login a través de NPM con `X-Forwarded-Proto: https` produce
  `request.is_secure() == True` (sin bucle de logout).
- El health check de `deploy.sh` en modo externo golpea
  `http://$EXTERNAL_HOSTNAME/` resuelto a 127.0.0.1 en `$HTTP_PORT`.
- `PROXY_MODE=nginx-local` no cambia de comportamiento.
- `bash -n` y `--dry-run` limpios.
