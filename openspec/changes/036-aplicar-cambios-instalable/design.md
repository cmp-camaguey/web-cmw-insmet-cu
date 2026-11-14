# 036-aplicar-cambios-instalable — diseño

## 1. Por qué un script nuevo y no `install.sh`

`install.sh` instala un host desde cero: crea usuarios, `pip install`, `migrate`,
genera el `.env` y hace preguntas. Correrlo otra vez sobre un servidor que ya
sirve es una operación de riesgo, y el operador no lo va a hacer para arreglar un
botón.

El script nuevo hace solo la parte que falta y es idempotente: tres archivos,
`daemon-reload`, `enable --now` del `.path`. Reutiliza la lógica de render de
`install.sh` —copia literal de la idea, no `source`— por la misma razón que
`install.sh` no usa regex: `webcmp` como patrón también matchearía
`webcmp-huey`, y un unit con el nombre del otro servicio no falla al instalarse,
falla cuando alguien pulsa el botón.

## 2. Qué puede comprobar `deploy.sh` y qué no

| Pieza | La instala `deploy.sh` | `deploy.sh` la comprueba |
|---|---|---|
| Código, dependencias, migraciones, estáticos | sí | sí |
| `webcmp.service`, `webcmp-huey.service`, `webcmp-autodeploy.*`, `webcmp-apply.*` | **no** | sí, desde `deploy/systemd/` |

La asimetría es deliberada: ejecutar código del checkout como root es la
escalada más directa que existe, y el checkout lo escribe `DEPLOY_USER`. Lo que
el deploy sí puede hacer es negarse a seguir.

`missing_units()` recorre `deploy/systemd/*.service` y `*.path` y traduce los
cuatro nombres que `install.sh` puede renderizar distinto
(`GUNICORN_UNIT`, `HUEY_UNIT`, `AUTODEPLOY_UNIT`, `APPLY_UNIT`). Compararla
contra el nombre del archivo reportaría faltantes falsos en un servidor bien
instalado, y el operador aprendería a ignorar el error.

El guard corre en dos lugares:

1. `--check`, para que el problema se vea antes de desplegar.
2. En el deploy real, después de `git checkout` y antes de `pip install`. Antes
   del `checkout` el árbol todavía es el viejo y no tiene la unidad que agrega el
   commit nuevo; después de `pip`/`migrate` el rollback devuelve el código pero no
   deshace lo aplicado.

## 2b. Habilitar no es instalar

`deploy.sh` no puede copiar `deploy/systemd/*` a `/etc`, pero sí puede habilitar
una unidad que ya está ahí: `systemctl enable` es un comando con un nombre que
sale de `deploy.env`, exactamente igual que el `systemctl restart` que el deploy
ya hace, y no ejecuta nada del checkout.

`ensure_apply_path()` corre después de reiniciar los dos servicios y antes del
health check:

- Si la unidad no está instalada, no hace nada y lo dice. El guard de
  `missing_units` ya abortó antes, así que este camino queda para `--skip` y para
  instalaciones a mano.
- Si hay una petición en disco, la descarta informándolo: el reinicio de este
  mismo deploy es lo que la petición pedía, y dejarla solo agrega un segundo
  reinicio en medio del health check.
- Si `start` o `is-active` fallan, el deploy aborta y revierte. Un deploy que
  dice "ok" con el botón muerto es el bug que este cambio cierra.

Sin este paso el arreglo no llega por el botón **Actualizar**: el operador
despliega el código, la página empieza a avisar de la petición huérfana, pero el
botón sigue sin reiniciar nada.

## 3. Una petición que nadie recogió
`deploy/apply-env.sh` borra `/var/lib/webcmp/apply-request` **antes** de hacer
nada. Por lo tanto un archivo que sigue en disco no describe un reinicio en
curso: describe uno que nadie procesó.

`pending_request()` mira solo el `mtime` del archivo, no su contenido, porque el
contenido es lo que menos importa: el aviso tiene que aparecer aunque el archivo
esté vacío o con JSON roto. Del contenido solo se toma `requested_by`, si se
puede leer.

`PENDING_GRACE_SECONDS = 30` existe porque la página se vuelve a pintar en el
mismo instante en que el POST escribe el archivo. Sin margen, el primer render
diría "nadie la recogió" de una petición que el ejecutor estaba recogiendo.
Treinta segundos es más que cualquier arranque de systemd y menos de lo que tarda
un operador en mirar la pantalla y desconfiar.

El aviso se muestra aparte del estado del último reinicio y no lo reemplaza: son
dos hechos distintos —"el último intento terminó así" y "hay un intento que no
terminó nunca"— y pueden coexistir.

## 4. Negarse a habilitar el `.path` con una petición en disco

`PathExists=` reacciona a "el archivo está": habilitar la unidad con una petición
vieja reinicia el sitio en ese instante, fuera de la ventana de mantenimiento.
El script aborta, dice dónde está el archivo y cómo destrabarlo. Es una decisión
del operador, no un efecto secundario de instalar.

## 5. Pruebas

- `deploy/tests/test_install_apply_units.sh`: render literal (que `webcmp-huey`
  sobreviva), negativa con petición en disco, y —solo como root— instalación
  real con `systemctl` de mentira, modos, dueño e idempotencia.
- `deploy/tests/test_deploy_sh_units.sh`: `missing_units` con árboles temporales,
  nombres renderizados, la posición del guard respecto del `checkout` y de
  `pip install`, y `ensure_apply_path` con un `systemctl` de mentira (sin unidad,
  habilitar, petición pendiente, `start` que falla, `is-active` que falla).
- `apps/core/tests/test_env_config.py`: `pending_request` y las dos vistas (avisa
  tarde, no avisa recién pedido).
