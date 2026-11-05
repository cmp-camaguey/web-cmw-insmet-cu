# 025-update-web-deploy

## Por qué

Hoy actualizar el servidor exige una persona con acceso SSH. En el despliegue
real el servidor lo administra un tercero, y el escenario que hay que resolver es
concreto: **quien opera el portal no tiene SSH**, pero sí tiene el panel de
administración. Sin un botón, cada actualización es un correo pidiendo que alguien
entre a correr un script.

El panel ya sabe qué versión está sirviendo (`deploy.sh` deja constancia) y el
repositorio es público, así que el chequeo de "hay un commit nuevo en `main`" no
necesita credenciales. Lo único que falta es el puente entre el botón y el script
privilegiado que ya existe.

## La restricción que manda sobre el diseño

`webcmp` (el usuario que corre Gunicorn) tiene el shell en `/bin/false` y no tiene
sudo, a propósito. Darle `sudo` sobre `webcmp-deploy` convertiría **cualquier** RCE
en el proceso Django en root en producción: basta con encontrar una vista que
ejecute un comando, o con reemplazar el script que sudo invoca.

Por eso el panel **no ejecuta nada**. Escribe un archivo. Quien lee ese archivo es
`deploy`, que ya tiene la única regla de sudoers del servidor. El límite de
privilegios no cambia respecto de hoy: un compromiso del proceso web consigue, como
mucho, pedir un despliegue de lo que ya está en `main`.

## Qué cambia

1. **Chequeo** (`UpdateStatusView`): compara el commit desplegado
   (`/etc/webcmp/deployed`) con `refs/heads/main` resuelto por `git ls-remote`
   contra el repositorio público. Cachea el resultado 5 minutos; un fallo se cachea
   1 minuto para que GitHub caído no deje el panel mintiendo.
2. **Petición** (`UpdateRequestView`): crea `/var/lib/webcmp/deploy-request` con
   `O_EXCL`. Dos pestañas con el botón pulsado no encolan dos despliegues; la
   segunda recibe 409.
3. **Ejecutor** (`deploy/autodeploy.sh`): lo dispara un `.path` de systemd al
   aparecer el archivo. Corre como `deploy`, resuelve **él mismo** el SHA contra
   `refs/heads/main` (nunca confía en el contenido del archivo) e invoca
   `webcmp-deploy` con `sudo -n`. Escribe el estado en JSON atómico.
4. **Aviso** (`templates/includes/dashboard/update_navbar.html` +
   `static/dist/js/dashboard-update.js`): un icono en el navbar del dashboard,
   visible sólo para superusuarios, con punto rojo cuando hay algo pendiente y un
   desplegable con el rango `deployed → latest` y el botón. Al vivir en el layout,
   aparece en todas las páginas y no sólo en el index.
5. **Instalador** (`deploy/install.sh`): `setup_autodeploy()` crea
   `/var/lib/webcmp` (2775, `deploy:webcmp`), instala el ejecutor en
   `/usr/local/sbin`, renderiza ambos units y habilita el `.path`.

## Por qué polling y no WebSocket

El proyecto no tiene stack ASGI: Gunicorn sirve WSGI y no existe un solo
`new WebSocket` en el árbol. Montar Channels para esto agrega una dependencia, un
proceso y un protocolo para informar un dato que cambia una vez por release. Y la
latencia real no la domina el transporte: la domina el poll de GitHub, que se cachea
300 segundos igual. `setTimeout` con 60 s en reposo y 2 s mientras se despliega
resuelve el caso completo con `fetch`.

## Qué NO cambia

- El workflow de deploy por push sigue igual (y sigue apagado sin servidor).
- El sudoers sigue teniendo exactamente dos líneas.
- `webcmp` sigue sin sudo, sin shell y sin capacidad de ejecutar nada.
- El comando manual `webcmp-deploy <sha>` sigue funcionando.

## Rollback

Desactivar el botón es un comando, sin tocar código:

```bash
sudo systemctl disable --now webcmp-autodeploy.path
```

A partir de ahí el panel sigue mostrando el aviso, pero la petición queda en disco
y nadie la recoge. Para apagarlo del todo, además:

```bash
sudo rm -f /var/lib/webcmp/deploy-request
```

El resto del despliegue no depende de este cambio: `webcmp-deploy` se invoca igual
desde el workflow o a mano.

## Criterios de aceptación

- Un superusuario ve el aviso cuando el commit de `main` difiere del desplegado, y
  no lo ve cuando coinciden ni cuando no consta qué se desplegó.
- Un usuario que no es superusuario recibe 403 en ambas vistas, aunque sea staff.
- Dos peticiones simultáneas producen un solo archivo de petición y un 409 en la
  segunda.
- El ejecutor despliega `refs/heads/main` aunque el archivo de petición pida otro
  SHA.
- `bash -n` pasa en `deploy/autodeploy.sh` y en `deploy/install.sh`, y `install.sh`
  sigue siendo idempotente (segunda corrida no reescribe nada).
