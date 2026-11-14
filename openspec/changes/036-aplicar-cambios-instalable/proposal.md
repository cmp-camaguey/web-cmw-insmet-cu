# 036-aplicar-cambios-instalable — propuesta

> **Estado: aprobada el 2026-11-13.** El usuario reportó que el botón **Aplicar
> cambios** del panel de entorno nunca reinicia el servidor en el contenedor de
> pruebas, y pidió el arreglo en el proyecto, no en el servidor.

## 1. Motivación

El botón **Aplicar cambios** (`033-entorno-servidor`, fase 3d) escribe
`/var/lib/webcmp/apply-request` y se desentiende. Quien lo recoge es
`webcmp-apply.path`, una unidad `systemd` instalada por `install.sh`. La cadena se
rompe en un punto que nadie comprueba:

- `install.sh` es un instalador completo —pips, migraciones, `.env`, preguntas— y
  no se vuelve a correr en un servidor que ya está sirviendo.
- `deploy.sh` actualiza el checkout y reinicia los servicios, y **no** escribe en
  `/etc/systemd/system` a propósito: corre como root y el checkout lo escribe el
  usuario del sitio, así que un unit escrito desde ahí sería un archivo
  root-ejecutable bajo su control.

Resultado: un servidor se actualiza a un commit que depende de una unidad que
nunca se instaló. El deploy termina "ok", el panel dice "Cambios guardados y
reinicio solicitado", y no pasa nada más. La petición queda en disco, el archivo
de estado nunca se escribe y la página no muestra nada, que es indistinguible de
"nunca se pidió". Es exactamente lo que pasó el 2026-11-13 en el contenedor de
pruebas: `webcmp-apply.path` no existía, `apply-status.json` tampoco y había un
`apply-request` huérfano de 77 bytes esperando desde las 19:37.

## 2. Solución

Tres piezas, ninguna de las cuales vuelve a correr `install.sh`:

- **`deploy/install-apply-units.sh`** (nuevo, root, idempotente): instala el
  ejecutor y las dos unidades de "Aplicar cambios", recarga `systemd` y habilita
  el `.path`. No toca el `.env`, la base, el venv ni el código.
- **`deploy.sh` se niega a desplegar** cuando falta una unidad que el commit
  trae en `deploy/systemd/`. Comprueba después del `checkout` (una unidad nueva
  recién aparece en el árbol nuevo) y antes de `pip`/`migrate`/`restart` (el
  rollback devuelve el código y no deshace lo aplicado después). No la instala:
  dice cuál falta y con qué comando se arregla.
- **`deploy.sh` habilita el `.path`** cuando la unidad ya está instalada. Habilitar
  es `systemctl` con un nombre que sale de la configuración, no ejecutar código
  del checkout, así que el deploy sí puede hacerlo. Sin esto el arreglo no llega
  por el botón **Actualizar**, que es la única vía que tiene el operador.
- **El panel avisa** que una petición quedó sin recoger pasados 30 s. Es el único
  estado en el que, sin aviso, la página no decía nada en absoluto.

## 3. Alcance

Dentro:

- `deploy/install-apply-units.sh` y sus pruebas en `deploy/tests/`.
- `missing_units()` en `deploy/deploy.sh`, su guard posterior al `checkout` y su
  comprobación en `--check`.
- `ensure_apply_path()` en `deploy/deploy.sh`: habilita y arranca el `.path`
  instalado, y da por cumplida una petición pendiente.
- `pending_request()` y `PENDING_GRACE_SECONDS` en `apps/core/env_apply.py`, el
  contexto `apply_pending` y el aviso de la plantilla.
- `deploy/deploy.env.example`, `deploy/README-deploy.md` y este cambio.

Fuera:

- Cambiar `install.sh`: sigue siendo el instalador completo y sigue siendo la
  única vía en un host limpio. El script nuevo reutiliza su lógica de render, no
  su flujo.
- Instalar unidades desde `deploy.sh`: la regla de privilegios no cambia.
- Un botón en el panel para instalar las unidades: eso sería darle root al
  proceso web por otra puerta.
