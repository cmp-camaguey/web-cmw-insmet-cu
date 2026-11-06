# Tareas — 029

- [x] T1. `deploy/nginx/webcmp-http.conf.example`: vhost HTTP-only con `map`
      para `X-Forwarded-Proto`, `/media/`, `/static/` y proxy al socket unix.
- [x] T2. `install.sh`: instalar Nginx en los dos modos (paquete y grupo
      `www-data` sobre el socket).
- [x] T3. `install.sh`: `setup_nginx_local` renderiza el vhost HTTP cuando
      `TLS_MODE=none` y no toca certbot.
- [x] T4. `install.sh`: el bloque de proxy externo configura Nginx y luego
      imprime los datos reales para NPM.
- [x] T5. `install.sh`: health check de `deploy.env` en modo externo por HTTP a
      través de Nginx.
- [x] T6. `deploy/README-deploy.md`: documentar la topología de dos capas.
- [ ] T7. Verificación: `bash -n`, `ruff`/`djlint` sin cambios, `--dry-run` del
      instalador (lo corre el usuario, exige root y TTY).
