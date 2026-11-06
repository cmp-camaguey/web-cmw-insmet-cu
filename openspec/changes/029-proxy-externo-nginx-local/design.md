# Diseño — 029

## Topología

```text
Navegador --https--> NPM (otra máquina, Docker) --http--> LXC:80 Nginx --> unix socket --> Gunicorn
                                                        |
                                                        +--> /media/  (alias, sin Django)
                                                        +--> /static/ (alias, sin WhiteNoise)
```

Dos capas de proxy. La de adentro no hace TLS y no se expone fuera de la red
donde vive el NPM.

## Por qué Nginx local y no Gunicorn por TCP

Servir `media/` requiere leer el directorio de archivos. El NPM está en otra
máquina, así que no puede montarlo ni servirlo desde su filesystem. Las salidas
eran:

| Opción | Coste |
| --- | --- |
| Nginx local como servidor de archivos | Un vhost nuevo; cero código Django. |
| Gunicorn por TCP + `media/` desde Django | Middleware nuevo, tests, y el servidor de aplicación expuesto en la LAN. |

La segunda además pone a Python a servir binarios que hoy sirve Nginx.

## `X-Forwarded-Proto`: el punto que rompe todo si se copia mal

El vhost de `nginx-local` hace `proxy_set_header X-Forwarded-Proto $scheme`.
Copiado tal cual al vhost HTTP, `$scheme` es `http` y **sobrescribe** el `https`
que manda NPM. Django dejaría de ver la petición como segura y las cookies
`SECURE` no viajarían: bucle de logout sin ningún error en el log.

Por eso el vhost HTTP declara:

```nginx
map $http_x_forwarded_proto $webcmp_forwarded_proto {
    default $http_x_forwarded_proto;
    ""      $scheme;
}
```

Pasa el header tal cual y cae a `$scheme` solo cuando no viene (acceso directo
por la LAN, que no es el camino de producción).

## Health check

Antes: en modo externo iba directo al socket de Gunicorn, así que un vhost de
Nginx roto seguía dando verde. Ahora va a `http://$EXTERNAL_HOSTNAME/` con
`--resolve $EXTERNAL_HOSTNAME:$HTTP_PORT:127.0.0.1`: atraviesa Nginx, el proxy y
Gunicorn. `SECURE_SSL_REDIRECT` es `False`, así que una petición HTTP responde
200 sin redirección.

## Riesgos

- **Firewall**: el `:80` del LXC queda alcanzable desde la red. Tiene que
  limitarse a la IP del NPM; el instalador no gestiona firewall y lo imprime
  como advertencia con el comando exacto.
- **`nginx-local` intacto**: el vhost con TLS no se toca; solo cambia qué
  archivo se renderiza cuando `TLS_MODE=none`.
