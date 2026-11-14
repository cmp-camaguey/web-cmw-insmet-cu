# 036-aplicar-cambios-instalable — tareas

- [x] 1. `deploy/install-apply-units.sh`: ejecutor, `.service` y `.path`, con
      `--help`, `DRY_RUN=1` e idempotencia. (S1)
- [x] 2. Rechazo a habilitar el `.path` con una petición sin recoger. (S2)
- [x] 3. `missing_units()` en `deploy.sh`, en `--check` y en el deploy real
      después del checkout. (S3)
- [x] 3b. `ensure_apply_path()` en `deploy.sh`: habilita y arranca el `.path`
      instalado, descarta una petición pendiente y falla si no queda activo. (S7)
- [x] 4. `AUTODEPLOY_UNIT` y `APPLY_UNIT` en `deploy.sh` y en
      `deploy/deploy.env.example`. (S3, S6)
- [x] 5. `pending_request()` y `PENDING_GRACE_SECONDS` en `env_apply.py`. (S4)
- [x] 6. `apply_pending` en la vista y el aviso en la plantilla. (S4)
- [x] 7. Pruebas de render, negativa, `missing_units` y panel. (S5)
- [x] 8. `deploy/README-deploy.md`: instalar o reparar sin `install.sh`, y la
      fila de estado "sin recoger". (S6)
- [x] 9. Documentación OpenSpec del cambio. (todas)
