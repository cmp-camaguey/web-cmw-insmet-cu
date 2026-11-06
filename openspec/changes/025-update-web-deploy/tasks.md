# Tasks

## 1. Contrato de archivos y configuración

- [x] 1.1 Settings `UPDATE_*` en `config/settings/base.py` (repo, ref, TTL,
      timeout, rutas de los tres archivos)
- [x] 1.2 `deploy.sh` escribe `/etc/webcmp/deployed` al final, de forma atómica
- [x] 1.3 Tests del contrato: SHA manipulado, estado ilegible, claves
      desconocidas del JSON no se reenvían

## 2. Vistas y UI

- [x] 2.1 `UpdateStatusView` (GET, JSON, sólo superusuario, `raise_exception`)
- [x] 2.2 `UpdateRequestView` (POST, `O_EXCL`, 409 en carrera, 503 sin permiso)
- [x] 2.3 Cache del `ls-remote`: 300 s en éxito, 60 s en fallo
- [x] 2.4 `templates/includes/dashboard/update_navbar.html` en el navbar del
      layout (visible en todas las páginas del dashboard)
- [x] 2.5 `static/dist/js/dashboard-update.js` (polling 60 s / 2 s, JS vanilla)
- [x] 2.6 Tests de render: el aviso aparece para superusuario en cualquier
      página del dashboard y no para staff
- [x] 2.7 Mensajes de los commits pendientes en el desplegable: API pública de
      GitHub (`compare`), cache 300 s / 60 s, degradación a los SHA si falla, y
      render con `textContent`. 19 tests nuevos (49 en
      `apps.dashboard.tests.test_update`; 1471 en la suite completa)

## 3. Ejecutor privilegiado

- [x] 3.1 `deploy/autodeploy.sh`: resuelve `refs/heads/main` por su cuenta, nunca
      confía en el SHA del archivo
- [x] 3.2 Estado JSON atómico (`mktemp` + `mv`) y `flock` propio
- [x] 3.2b `started_at` en el estado y `RUN_STALE_SECONDS` en el panel, para que
      un ejecutor muerto a mitad no deje el botón pegado para siempre
- [x] 3.3 `deploy/systemd/webcmp-autodeploy.service` (`User=deploy`,
      `TimeoutStartSec=45min`, `Restart=no`)
- [x] 3.4 `deploy/systemd/webcmp-autodeploy.path`
- [x] 3.5 `setup_autodeploy()` en `install.sh`: directorio 2775, ejecutor en
      `/usr/local/sbin`, render de ambos units, `enable --now` del `.path`

## 4. Verificación

- [x] 4.1 `python manage.py test apps.dashboard.tests.test_update` (25 tests, OK)
- [x] 4.2 `python manage.py test` (suite completa)
- [x] 4.3 `bash -n deploy/autodeploy.sh` y `bash -n deploy/install.sh`
- [x] 4.4 `djlint . --reformat --check` sobre el template nuevo
- [ ] 4.5 Corrida de `install.sh --dry-run` en el contenedor (pendiente de
      infraestructura)

## 5. Documentación

- [x] 5.1 `deploy/README-deploy.md`: sección del botón, con rollback
- [x] 5.2 `AGENTS.md`: no aplica (es un cambio de deploy, no una convención nueva)

## Nota

El punto 4.5 es el único que no se puede cerrar en desarrollo: `install.sh`
necesita systemd real y un usuario `deploy` que no existe en la estación de
trabajo. Todo lo demás se verifica sin servidor.
