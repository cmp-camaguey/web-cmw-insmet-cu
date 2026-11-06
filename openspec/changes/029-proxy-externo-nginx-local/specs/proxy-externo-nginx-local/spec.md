# Spec delta — 029

## Requisito: el modo externo sirve archivos

`PROXY_MODE=external` DEBE instalar Nginx, habilitar un vhost HTTP en
`$HTTP_PORT` y dejar `/static/` y `/media/` servidos por `alias` desde
`$APP_DIR/staticfiles` y `$APP_DIR/media`.

## Requisito: el TLS externo se preserva

El vhost HTTP NO DEBE reescribir `X-Forwarded-Proto` con `$scheme`. DEBE
propagar el valor recibido y usar `$scheme` solo cuando el header no viene.

## Requisito: Gunicorn no se expone

En modo externo Gunicorn DEBE seguir escuchando únicamente en
`unix:$GUNICORN_SOCKET`. El instalador NO DEBE agregar un `--bind` TCP.

## Requisito: el health check atraviesa Nginx

En modo externo `deploy.env` DEBE definir `HEALTHCHECK_URL` y
`HEALTHCHECK_RESOLVE` apuntando a `127.0.0.1:$HTTP_PORT`, y NO DEBE definir
`HEALTHCHECK_UNIX_SOCKET`.

## Requisito: `nginx-local` no cambia

Con `PROXY_MODE=nginx-local` el vhost renderizado, el certificado, el health
check por HTTPS y los puertos DEBEN quedar igual que antes.
