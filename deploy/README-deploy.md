# Despliegue

Cómo instalar la aplicación en un servidor nuevo y cómo pasar de
`sudo systemctl restart webcmp` a un deploy que se dispara solo
cuando el CI de `main` esta en verde, con gates y rollback.

La logica vive en [`deploy.sh`](deploy.sh), que corre **en el servidor**. Lo
dispara el boton **"Actualizar"** del panel: el servidor resuelve `refs/heads/main`
contra GitHub por HTTPS y aplica el commit localmente. No hace falta ningun puerto
entrante ni ningun runner conectandose.

## Que hace, y en que orden

El orden importa:

| # | Paso | Por que en ese lugar |
| --- | --- | --- |
| 1 | `flock` | Dos pushes seguidos, o un deploy y el instalador, no pueden mutar el mismo checkout a la vez |
| 2 | Aborta si hay cambios sin commitear | `git checkout --force` los perderia |
| 3 | `git checkout` del SHA exacto | Desplegar un SHA, no una rama: la rama se movio entre el CI y el deploy |
| 4 | `pip install -r requirements/prod.txt` | Antes de los gates, que importan las deps |
| 5 | `makemigrations --check --dry-run` | Falla si un cambio de modelo no trae su migracion. Ver la nota de abajo |
| 6 | Gate: `check --deploy` con la config real | Ver la nota de abajo |
| 7 | `pg_dump` **si hay migraciones pendientes** | Es la unica vuelta atras de un esquema destructivo. Falla cerrado. Ver **Respaldo antes de migrar** |
| 8 | `migrate`, `migrate --check` y `create_role_groups` | Antes de que el codigo nuevo atienda un request. El `--check` confirma que no queda nada sin aplicar; los grupos de rol necesitan los permisos que `migrate` acaba de crear |
| 9 | `collectstatic` | Antes de que el codigo nuevo emita HTML que los referencia |
| 10 | `systemctl restart` de ambos units | `reload` NO alcanza, ver la nota de abajo |
| 11 | Health check contra Nginx local | Atraviesa Nginx, TLS y el socket: el camino completo |

### Gate `makemigrations --check`

El proyecto versiona las migraciones: los `0001_initial` de cada app están en el
repositorio y viajan en el clone. `makemigrations --check --dry-run` sale distinto
de 0 si un cambio de modelo llega sin su migración, y el deploy aborta **antes**
de tocar la base. El CI corre el mismo gate antes de `migrate`.

El rollback del código no deshace el esquema: Django no genera migraciones
inversas.

### Respaldo antes de migrar

`deploy.sh` toma un `pg_dump` **antes** de migrar, y solo cuando hay algo que
migrar:

```bash
# Se pregunta sin aplicar nada. Sale 1 si queda algo pendiente.
manage migrate --check
```

- Hay pendientes → `pg_dump --format=custom` en `DB_BACKUP_DIR`
  (`/var/backups/webcmp/db`), `root:root` 0600 dentro de un directorio 0700. Ni
  `webcmp` ni `deploy` pueden leer un respaldo completo de la base.
- Si `pg_dump` falla, **el deploy aborta sin migrar**. Un dump a medias se borra;
  no queda un archivo con fecha de hoy que no sirve.
- Se conservan `DB_BACKUP_KEEP` (10 por defecto); los mas viejos se borran en cada
  deploy que respalda.
- Sin migraciones pendientes no se respalda: un deploy que solo cambia HTML no
  llena el disco.

Restaurar, con el sitio parado y **como root** (el dump es `root:root 0600`
dentro de un directorio `0700`; otro usuario no puede leerlo):

```bash
sudo systemctl stop webcmp webcmp-huey
sudo pg_restore -h 127.0.0.1 -U <usuario-de-la-base> -W -d <base> \
    --clean --if-exists /var/backups/webcmp/db/<archivo>.dump
sudo systemctl start webcmp webcmp-huey
```

`--clean` borra los objetos antes de recrearlos: sobre una base que ya tiene el
esquema nuevo, sin `--clean` el restore falla a mitad.

`webcmp-deploy --check` avisa si no hay `pg_dump` en el `PATH`. Lo instala
`install.sh` (`postgresql-client`); si se saca a mano, el proximo deploy con
migraciones se niega a correr.

### Gate de migraciones en el merge a `main`

El job **`Migrations safety (dev -> main)`** corre en cada PR cuya base es `main`
y analiza el AST de las migraciones nuevas con
[`scripts/check_migrations.py`](../scripts/check_migrations.py):

| Caso | Resultado |
| --- | --- |
| `RemoveField`, `DeleteModel`, `RunSQL`, `RunPython`, `SeparateDatabaseAndState` | **Bloquea** |
| Operacion que el gate no conoce | **Bloquea** (falla cerrado) |
| `AddField`, `AlterField`, `RenameField`, `RenameModel`, indices y constraints | Aviso |
| Modificar, borrar o renombrar una migracion ya publicada | **Bloquea, sin escape** |
| Migracion nueva con sintaxis rota o sin `operations` | **Bloquea** |

Una operacion destructiva revisada a proposito se declara en el propio archivo:

```python
class Migration(migrations.Migration):
    dependencies = [('meteo', '0011_anterior')]
    operations = [
        # data-safety: reviewed: la columna se migro a 0012 y se verifico en produccion
        migrations.RemoveField(model_name='Municipio', name='poblacion'),
    ]
```

El motivo tiene que estar: un marcador vacio no cuenta. Editar historia ya
aplicada **no** tiene escape, porque el servidor que la corrio y el que no quedan
con esquemas distintos y el mismo `django_migrations`; eso se corrige hacia
adelante con otra migracion.

Local:

```bash
python scripts/check_migrations.py --base origin/main --head HEAD
```

Sale 0 sin bloqueos, 1 con alguno y 2 si no pudo analizar (clon shallow sin
merge-base). El 2 tambien es rojo: un gate que no puede comparar no puede
aprobar. En GitHub, el job se agrega a los *required status checks* de
`Protect main`; en un PR a `dev` aparece como *skipped*.

### Gate `check --deploy` en el servidor

El `deploy-check` del CI corre el mismo check con valores de mentira: base que no
existe, sin SMTP, `SECRET_KEY` descartable. El gate del servidor corre contra el
`.env` real: comprueba que la `SECRET_KEY` se descifra con la `ENCRYPTION_KEY` de
`/etc/webcmp/encryption.env`, que `DB_*` apunta al PostgreSQL real y que
`EMAIL_BACKEND` no es el de consola.

### `reload` no aplica código nuevo

`systemctl reload` (SIGHUP) recarga la configuración de Gunicorn, pero los workers
se re-forkean desde el master, que tiene el código viejo en memoria. El sitio
serviría templates viejos con estáticos nuevos. El deploy usa `restart`.

### `manage.py` a mano no corre en producción

Los comandos de Django corren en una unidad transitoria con el mismo
`EnvironmentFile` y `PRODUCTION=1` que `webcmp.service`. Sin `PRODUCTION` en el
entorno, `manage.py` cae en el perfil `testing`, que no es el del servicio.

## Rollback

Automatico: si algo falla **despues** de `migrate`, el script vuelve al commit
anterior, reinstala dependencias, recoloca estaticos y reinicia los units. Sale
con codigo 1, asi que el panel y `deploy-status.json` lo marcan fallido aunque el
sitio haya quedado sirviendo la version anterior.

El disparador es `trap rollback EXIT`, no `ERR`: `trap ... ERR` no corre cuando el
script hace `exit`, y `die()` —con el que termina casi todo el deploy, incluido el
health check— hace `exit 1`.

Lo que **no** hace, y hay que saber antes de confiar en el:

- **No revierte la base de datos.** Django no genera migraciones inversas y una
  operacion destructiva (`DROP COLUMN`, `AddField` con `default` + `NOT NULL`) no
  tiene hacia atras. El script avisa por pantalla cuando ya migro y nombra el
  respaldo que tomo antes de migrar; si el fallo fue post-migracion, la base quedo
  con el esquema nuevo y hay que revisarla a mano antes de volver a exponer el
  sitio.
- **No toca `media/`.** Los archivos que subio la gente no se deshacen.

Manual:

```bash
sudo /usr/local/sbin/webcmp-deploy <commit-anterior>
```

## Requisitos del servidor

Se instala en un Linux x86_64 con systemd: una VM o un contenedor con init propio,
es indistinto.

- **Ubuntu 26.04 LTS**, que trae Python 3.14. `scripts/generate_env.py` usa PEP 758
  y no compila en 3.12 ni 3.13, asi que `install.sh` verifica la version al
  arrancar y aborta si no sirve. Los workflows de CI usan `runs-on: ubuntu-26.04`
  por la misma razon.
- **IP fija** y nombre en DNS que coincida **exactamente** con el
  `EXTERNAL_HOSTNAME` del `.env`, el `PublicHostname` de `deploy.env` y el
  `server_name` de Nginx. Si difieren, el sitio responde `DisallowedHost`.
- **Zona horaria `America/Havana`**, igual que `TIME_ZONE`.
- **Salida HTTPS a `github.com:443`**. El servidor resuelve `refs/heads/main` con
  `git ls-remote`; no necesita ningun puerto entrante.
- **Dimensiones**: 4 vCPU, 8 GB de RAM y 60 GB de disco. Piso: 2, 4 y 30. El disco
  crece con `media/`, los PDFs y los respaldos de `/var/backups/webcmp/db`.

Comprobar la conectividad antes de instalar:

```bash
sudo -u deploy env GIT_TERMINAL_PROMPT=0 \
    git ls-remote https://github.com/cmp-camaguey/webcmp.git refs/heads/main
```

Si devuelve un SHA, el boton "Actualizar" va a funcionar. Si falla por DNS, por
timeout o por autenticacion, el deploy por boton no puede resolver el commit.

Toma un snapshot o un backup del servidor limpio **antes** del primer
`install.sh`: ese paso toca apt, crea usuarios, bases y vhosts, y rehacerlo a mano
cuesta una tarde.

## Instalación de la aplicación paso a paso

El camino manual, equivalente a lo que hace `install.sh` antes de tocar el
andamiaje del deploy. Se corre **en este orden**: la aplicación primero, y
después el andamiaje de la sección siguiente.

### 1. Prerrequisitos de sistema

```bash
sudo apt update
sudo apt install -y \
  git python3.14 python3.14-venv postgresql nginx redis-server \
  build-essential pkg-config libcairo2-dev libpango1.0-dev \
  python3-dev wkhtmltopdf
```

| Paquete | Para qué |
| --- | --- |
| `build-essential` | Compilar los pins de `prod.txt` que no tienen rueda en Python 3.14 |
| `libpango1.0-dev` | WeasyPrint lo carga por cffi. En Ubuntu 24.04+ el nombre **no** es `libpango-1.0-dev` |
| `redis-server` | El `.env` de producción trae `USE_REDIS_CACHE=True` |

Usuario dedicado del servicio:

```bash
# El servicio NUNCA corre como root, y este usuario no tiene shell a proposito:
# existe para correr Gunicorn y Huey, no para que alguien se loguee.
sudo useradd -r -s /bin/false webcmp
sudo mkdir -p /srv/webcmp && sudo chown webcmp:webcmp /srv/webcmp
```

### 2. Base de datos

El rol de PostgreSQL y el del servicio son el mismo (`webcmp`), y la base es
propiedad de ese rol.

```bash
sudo -u postgres psql <<'SQL'
CREATE USER webcmp WITH PASSWORD 'CAMBIAR_ESTA_CLAVE';  -- pragma: allowlist secret
CREATE DATABASE webcmp OWNER webcmp;
\c webcmp
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO webcmp;
SQL
```

El `GRANT` sobre el esquema `public` hace falta aunque el rol sea dueño de la
base: en PostgreSQL 15+ `PUBLIC` ya no tiene `CREATE` ahí y `migrate` falla al
crear la primera tabla.

Si vas a correr la suite de tests en el mismo servidor, el rol necesita poder
crear la base de prueba:

```bash
sudo -u postgres psql -c 'ALTER ROLE webcmp CREATEDB'
```

Sin eso, `python manage.py test` falla con `permission denied to create database`.

Cambiar la contraseña después es en **los dos lados**: el rol y el `DB_PASS` del
`.env`. Si la cambiás en uno solo, cada request muere con `password authentication
failed for user "webcmp"`, que parece un problema de red y no de clave.

```bash
sudo -u postgres psql -c "ALTER USER webcmp WITH PASSWORD 'NUEVA_CLAVE'"
sudo nano /srv/webcmp/.env      # DB_PASS
sudo chown webcmp:webcmp /srv/webcmp/.env && sudo chmod 600 /srv/webcmp/.env
sudo systemctl restart webcmp
```

### 3. Código, venv y `.env`

```bash
sudo -u webcmp git clone https://github.com/cmp-camaguey/webcmp.git /srv/webcmp
cd /srv/webcmp
sudo -u webcmp python3.14 -m venv .venv
sudo -u webcmp .venv/bin/pip install -r requirements/prod.txt

# generate_env.py escribe /etc/webcmp/encryption.env (root, 0600): corre como root.
sudo install -d -m 0755 -o root -g root /etc/webcmp
sudo .venv/bin/python scripts/generate_env.py --production

# Migraciones y estaticos con el perfil de produccion.
sudo systemd-run --quiet --wait --uid=webcmp --gid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py migrate

sudo systemd-run --quiet --wait --uid=webcmp --gid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py collectstatic --no-input
```

> Sin `PRODUCTION=1`, `manage.py` cae en el perfil `testing`: `collectstatic` no
> genera el manifest y el sitio responde 500 en cada página. `systemd-run` va con
> `sudo`, nunca con `sudo -u webcmp` (`Access denied`); el usuario lo pone
> `--uid=webcmp`.

El script escribe dos archivos:

| Archivo | Contiene | Permisos |
| --- | --- | --- |
| `/srv/webcmp/.env` | `SECRET_KEY` cifrada, `DEBUG=False`, `DB_*`, SMTP | `600`, **webcmp** |
| `/etc/webcmp/encryption.env` | `ENCRYPTION_KEY` (clave de descifrado) | `600`, root |

El `.env` es de `webcmp` y no de root porque Django corre como ese usuario y tiene
que poder leerlo. La `ENCRYPTION_KEY` queda en root y el `.env` en 600: entrar al
grupo `webcmp` (lo necesita Nginx, ver el paso 5) no da acceso a ninguna de las dos.

Después de generar el `.env` hay que **cambiar el hostname**: el generador escribe
`EXTERNAL_HOSTNAME=www.meteocamaguey.cu` por default y no tiene flag para pasarlo, así
que hay que editar a mano `EXTERNAL_HOSTNAME`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS` y `CORS_ALLOWED_ORIGINS`. Si no, el sitio responde
`DisallowedHost` a cada petición.

```bash
sudo nano /srv/webcmp/.env
sudo chown webcmp:webcmp /srv/webcmp/.env && sudo chmod 600 /srv/webcmp/.env
sudo chown root:root /etc/webcmp/encryption.env && sudo chmod 600 /etc/webcmp/encryption.env

# `staticfiles/` y `media/` los necesita escribibles por el usuario del servicio,
# y `.cache/` para MPLCONFIGDIR: con `ProtectSystem=full` el worker no puede
# crearse solo ese directorio, y el unit lo declara en `ReadWritePaths=`.
sudo mkdir -p /srv/webcmp/{media,logs,staticfiles,.cache/matplotlib}
sudo chown -R webcmp:webcmp /srv/webcmp/{media,logs,staticfiles,.cache}
```

El `.env` sale con dos cosas que hay que completar antes de que el sitio sirva de
verdad:

| Variable | Qué hacer |
| --- | --- |
| `EXTERNAL_HOSTNAME`, `ALLOWED_HOSTS` | El dominio real |
| `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | Idem |

Sin esas cuatro, cada petición es un `DisallowedHost`.

Y las de SMTP: `EMAIL_HOST_USER` y `EMAIL_HOST_PASSWORD` salen como
`CHANGE_ME_placeholder`, con lo que los correos no se van.

> `webcmp` no tiene home, así que pip no tiene dónde cachear. Dale uno:
> `sudo install -d -m 0700 -o webcmp -g webcmp /var/cache/webcmp-pip`. El deploy lo
> exporta solo (`PIP_CACHE_DIR` en `/etc/webcmp/deploy.env`).

> `collectstatic` tarda unos 3 minutos: ~917 archivos, ~114 MB, más el `.gz` y el
> `.br` de cada uno. El deploy completo anda por los 5.

### 4. systemd

```bash
sudo cp deploy/systemd/webcmp.service deploy/systemd/webcmp-huey.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now webcmp webcmp-huey
sudo journalctl -u webcmp -f
```

Los logs van a journald, no a archivos.

> **Las rutas de `/srv/webcmp` están escritas en el `.service`.** En un unit,
> `APP_DIR=/srv/webcmp` no es una clave válida de systemd y systemd tampoco expande
> variables en `WorkingDirectory=` o `ReadWritePaths=`. Si el checkout cambia de
> lugar, se editan las rutas y se verifica:
> `systemd-analyze verify /etc/systemd/system/webcmp.service`.

> `EnvironmentFile=-` lleva el guion a propósito: sin la clave de descifrado el
> servicio arranca y falla con el error de `load_secret_key()`.

### 5. Nginx

Hay **dos topologías** y cambia solo qué vhost se copia. `deploy/install.sh`
resuelve la elección con la pregunta `PROXY_MODE`; lo que sigue es el camino
manual, equivalente.

#### Con TLS en esta máquina

`PROXY_MODE=nginx-local`: Nginx hace TLS y hace de proxy.

```bash
sudo cp deploy/nginx/webcmp.conf.example /etc/nginx/sites-available/webcmp.conf
sudo nano /etc/nginx/sites-available/webcmp.conf   # certificados y rutas
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/webcmp.conf /etc/nginx/sites-enabled/

# www-data tiene que poder tocar el socket de Gunicorn, que sale
# 0770 webcmp:webcmp. Sin esto Nginx devuelve 502 en cada respuesta y el log dice
# "connect() failed (13: Permission denied)", que no parece un problema de grupo.
sudo usermod -aG webcmp www-data

sudo nginx -t && sudo systemctl restart nginx
```

#### Detrás de un proxy externo

`PROXY_MODE=external`: el TLS lo termina un Nginx Proxy Manager (NPM) que corre
**en otra máquina**. Acá Nginx escucha HTTP, sirve `/static/` y `/media/`, y hace
proxy a Gunicorn por el socket.

```bash
sudo cp deploy/nginx/webcmp-http.conf.example /etc/nginx/sites-available/webcmp.conf
sudo nano /etc/nginx/sites-available/webcmp.conf   # server_name y rutas
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/webcmp.conf /etc/nginx/sites-enabled/

sudo usermod -aG webcmp www-data
sudo nginx -t && sudo systemctl restart nginx
```

En NPM, el upstream es:

| Campo | Valor |
| --- | --- |
| Scheme | `http` (NPM habla HTTP con el servidor; el TLS lo pone él) |
| Forward Hostname / IP | IP de LAN de este servidor |
| Forward Port | puerto HTTP del vhost (80 por defecto) |
| Websockets Support | on (el gráfico de sounding las usa) |
| SSL | Let's Encrypt en NPM; el dominio tiene que resolver a NPM |

No hacen falta Custom Locations: el vhost local ya sirve `/static/` y `/media/`.
El puerto HTTP tiene que quedar alcanzable **solo** desde la máquina del NPM:

```bash
sudo ufw allow from <IP_DEL_NPM> to any port 80 proto tcp
sudo ufw deny 80/tcp
```

En esta topología el health check del deploy apunta a `http://` en
`127.0.0.1:$HTTP_PORT`, no al 443: ese puerto acá no lo escucha nadie. Sin
`HEALTHCHECK_URL` y `HEALTHCHECK_RESOLVE` en `/etc/webcmp/deploy.env`, el deploy
falla por construcción.

> **Sin NPM delante, el login no se puede probar.** `production.py` deja
> `SESSION_COOKIE_SECURE` y `CSRF_COOKIE_SECURE` en `True`, y el navegador
> descarta una cookie `Secure` que llega por `http://`: el sitio abre, pero el
> login devuelve el formulario vacío sin un solo error en pantalla ni en el log.
> Verifica el resto con `curl` (paso 7) y deja el login para cuando el NPM apunte
> al servidor.

> **Reinicio de Nginx, no `reload`.** Los grupos suplementarios se leen al crear
> los workers. Un `reload` alcanza para leer la config, pero si acabás de cambiar
> la pertenencia a un grupo, reiniciá el servicio.

> **`X-Forwarded-Proto` decide si el login funciona.** Django lo lee con
> `SECURE_PROXY_SSL_HEADER`; si falta o llega mal, el navegador no guarda las
> cookies `Secure` y el login entra en un bucle sin error en el log.
> `webcmp.conf.example` lo manda con `$scheme`; `webcmp-http.conf.example` usa un
> `map` que propaga el valor de NPM. No cambies una forma por la otra.

> **`Host` está repetido dentro de `location /` a propósito.** `proxy_set_header`
> se hereda del `server` solo si el `location` no declara ninguno, y `location /`
> declara dos para el websocket. Sin esas líneas Gunicorn recibe `Host: webcmp` y
> responde 400 en todo el sitio.

### 6. Datos iniciales y usuario administrador

Una base migrada está vacía: sin municipios ni estaciones, las páginas de pronóstico
no tienen contra qué renderizar.

> **`--pipe` es frágil en systemd 259.** Envía descriptores por D-Bus al arrancar la
> unidad transitoria y falla de forma intermitente con `Failed to start transient
> service unit: Connection reset by peer`. `deploy.sh` no lo usa: captura la salida
> con `-p StandardOutput=append:<archivo>`. Acá queda porque hay alguien mirando.

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py add_stations_data
```

Toma `--provincia` (default `Camagüey`) y `--tipo` (`municipios`, `estaciones` o
`todo`, que es el default).

Los grupos de rol los deja armados el propio `deploy.sh` en cada despliegue y el
instalador en el paso de migraciones: `create_role_groups` crea los grupos
`<Módulo> - <Ver|Editar|Gestionar>` de todos los modelos. Es idempotente y usa
`add` en vez de `set`, así que no pisa permisos cargados a mano. Para recrearlos
a mano:

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py create_role_groups
```

Sin esos grupos, un usuario no-superusuario entra al dashboard sin ver ninguna
sección: el menú y cada vista se resuelven por permiso.

El usuario administrador se crea con `createsuperuser`. Dos formas:

```bash
# Interactivo (la normal). -t le da terminal al comando.
sudo systemd-run --pipe --wait --uid=webcmp -t \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py createsuperuser

# No interactivo, para un servidor que se arma por script.
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 \
  -p Environment=DJANGO_SUPERUSER_PASSWORD='CONTRASENA_LARGA_Y_UNICA' \ # pragma: allowlist secret
  --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py createsuperuser \
    --noinput --username admin --email admin@insmet.cu
```

> La contraseña va en `-p Environment=DJANGO_SUPERUSER_PASSWORD=...`:
> `systemd-run` no hereda el entorno del shell y crearía el usuario con una
> contraseña inservible.
>
> `--uid=webcmp` no es opcional: como root no puede leer el `.env` (600 de
> `webcmp`).

Hasta que complete email, nombre y apellido, el usuario es redirigido a
`/accounts/profile/update/` en cada navegación (`CheckUserProfileMiddleware`).

Para crear más usuarios sin panel:

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py shell -c \
  "from django.contrib.auth.models import User; \
   User.objects.create_superuser('nombre', 'correo@insmet.cu', 'CONTRASENA')"
```

### 7. Verificar

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py check --deploy

curl -I https://www.meteocamaguey.cu
```

`check --deploy` tiene que salir con 0 issues. Lo único tolerado es `security.W008` (el
TLS lo termina Nginx, no Django) y está silenciado en `config/settings/base.py`.

Con proxy externo, ese `curl` pasa por NPM y prueba la cadena completa. Para
validar solo la capa local del servidor —vhost, `/static/` y `/media/`— sin salir
de él: `curl -sSI http://127.0.0.1/`.

## Instalacion en el servidor (una vez)

Hay dos caminos. El instalador es el que corresponde; el manual queda como
alternativa para servidores que ya estan armados o cuando se quiere ver cada
paso.

### Camino automatico: `install.sh`

`install.sh` es un solo archivo y no necesita el repo: hace el preflight, clona,
crea el usuario de servicio y el de despliegue, genera el `.env`, levanta venv,
migraciones, estaticos, las dos unidades systemd, el vhost de Nginx y el
`deploy.env` que usa el deploy.

Primero, ver que va a hacer, sin tocar nada:

```bash
curl -fsSL -o /tmp/install.sh https://raw.githubusercontent.com/cmp-camaguey/webcmp/main/deploy/install.sh \
    && sudo bash /tmp/install.sh --dry-run
```

Despues, la corrida real. **Necesita una terminal**: el script aborta si el
`stdin` no es un TTY, porque un instalador que adivina en un pipe escribe el
`.env` equivocado y nadie lo ve hasta que el sitio responde `DisallowedHost`.

El pipe (`curl ... | sudo bash -s`) no sirve: el `stdin` del script sería la
tubería y el guard `-t 0` lo rechaza. Se acepta solo para `--help` y para
`--non-interactive`.

```bash
curl -fsSL -o /tmp/install.sh https://raw.githubusercontent.com/cmp-camaguey/webcmp/main/deploy/install.sh \
    && sudo bash /tmp/install.sh
```

Opciones que importan:

| Opcion | Para que |
| --- | --- |
| `--dry-run`, `-n` | Imprime el plan y no ejecuta nada. Es el primer paso siempre |
| `--non-interactive` | Sin preguntas: cada valor sale de la variable de entorno del mismo nombre o del default. Sin terminal esto es obligatorio |
| `--allow-incomplete-env` | Deja los `CHANGE_ME` que sobren (por ejemplo, si el SMTP todavia no existe). NO migra, NO crea el superusuario y NO arranca los servicios |
| `--skip-deploy-scaffold` | No crea el usuario de despliegue, el sudoers ni los ejecutores de deploy: para servidores donde el deploy se corre a mano |

`sudo bash -s -- --help` imprime el uso y sale 0 sin exigir root ni terminal.

`install.sh` toma **el mismo lock que `deploy.sh`** (`/var/lock/webcmp-deploy.lock`,
no bloqueante). Si hay un deploy en curso, aborta sin tocar nada en vez de escribir
el mismo checkout por detras; el `--dry-run` no lo toma, porque no muta nada.

Si encuentra un `.env` de una instalacion anterior cuyo par
`SECRET_KEY`/`ENCRYPTION_KEY` no descifra, en modo interactivo **pregunta antes de
regenerarlo**. El par roto impide que Django arranque, y regenerar invalida todas
las sesiones; antes de borrar nada, los dos archivos quedan respaldados al lado
como `.env.bak.<fecha>` y `encryption.env.bak.<fecha>`, con el mismo modo 0600.
Responder que no aborta sin tocar nada: la otra salida es restaurar la mitad que
falta (por ejemplo, el `encryption.env` de la instalacion a la que pertenece ese
`.env`). Con `--non-interactive` no hay a quien preguntar y se regenera avisando.

### Pruebas de los scripts

`deploy/tests/` cubre la sintaxis de todos los scripts y la logica que se puede
aislar del sistema operativo: el validador del par `SECRET_KEY`/`ENCRYPTION_KEY`,
el lock, el respaldo previo a regenerar el `.env`, la escritura de
`/etc/webcmp/deployed`, la decision de si hay migraciones pendientes y la
rotacion de los respaldos de base. Los bloques se extraen del propio script entre
marcadores `# >>> TEST-BLOCK: <nombre>`, asi que se prueba el codigo que se
ejecuta y no una copia.

El gate de migraciones se prueba aparte, como proceso, en
`apps/core/tests/test_check_migrations_script.py` (repos git temporales, sin tocar
el del proyecto).

```bash
make test-deploy          # o: bash deploy/tests/run.sh
```

No corre el instalador: necesita root, systemd, apt, PostgreSQL y Nginx, y muta
la maquina. Ese camino se prueba en un servidor.

El instalador pregunta lo que hay que decidir: dominio y `www`, puertos, base de
datos, correo, superusuario y modo de proxy. Todo queda en el mismo `.env` y en el
mismo `deploy.env`.

Certificado **autofirmado** (sirve sin red y con DNS privado; el navegador lo
marca como no seguro y el health check no verifica el TLS) o **Let's Encrypt** con
`certbot --webroot`. El temporal de la primera emisión queda en
`/etc/webcmp/tls/` y se puede borrar.

El instalador imprime la clave privada del superusuario **una sola vez**, y solo
si la genero el. Las claves Django se pueden rotar despues con
`manage.py rotatekeys`; la contrasena de la base esta en el `.env` y se edita
alla.

Al final queda el andamiaje del boton "Actualizar": el usuario `deploy`, el
sudoers acotado a un comando, `/usr/local/sbin/webcmp-deploy` y el ejecutor
`webcmp-autodeploy` con su `.path` de systemd. **No se genera ninguna credencial
de despliegue**: la actualizacion es pull, asi que no hay clave privada que sacar
del servidor ni secret que cargar en GitHub. Ver **El boton "Actualizar"**.

En la misma pasada queda el ejecutor del boton **"Aplicar cambios"** del panel de
entorno (`webcmp-apply` y su `.path`), que corre como root porque reinicia los
servicios. Ver **El boton "Aplicar cambios" del panel de entorno**.

Re-correr el script es seguro: cada pregunta muestra el valor actual como
default, no regenera claves, no borra la base y no pisa ni `/etc/webcmp/deploy.env`
ni un `.env` afinado a mano sin confirmarlo.

### Camino manual (alternativa)

Sin el instalador hay que dejar el usuario, el script, la config y el sudoers a
mano. Despues de eso, el boton funciona igual. Es exactamente lo que hace
`install.sh`, paso por paso:

```bash
# 1. Usuario de despliegue. Distinto de webcmp, que no tiene shell a proposito:
#    existe para correr Gunicorn, no para que alguien se loguee. No tiene
#    credenciales: se usa unicamente para correr el ejecutor del boton.
sudo useradd -m -s /bin/bash deploy

# 2. Script FUERA del checkout.
sudo install -o root -g root -m 0755 deploy/deploy.sh /usr/local/sbin/webcmp-deploy

# 3. Config.
sudo install -o root -g root -m 0600 deploy/deploy.env.example /etc/webcmp/deploy.env
sudo nano /etc/webcmp/deploy.env   # rutas y PUBLIC_HOSTNAME

# 4. Sudoers acotado a UN comando. Validar el archivo ANTES de instalarlo: un
#    sudoers invalido deja al usuario de despliegue sin sudo.
sudo visudo -c -f deploy/sudoers/webcmp-deploy
sudo install -o root -g root -m 0440 deploy/sudoers/webcmp-deploy /etc/sudoers.d/webcmp-deploy
sudo visudo -c

# 5. www-data tiene que poder tocar el socket de Gunicorn (0770 webcmp:webcmp).
#    Sin esto Nginx devuelve 502 en cada respuesta.
sudo usermod -aG webcmp www-data
sudo systemctl restart nginx     # reinicio, no reload: los grupos se leen al
                                  # crear los workers
```

El paso 6 del andamiaje es el ejecutor del boton (`webcmp-autodeploy` y su
`.path`), que instala [`setup_autodeploy()`](../deploy/install.sh): dos unidades de
systemd, el directorio de estado con setgid y el script que resuelve el SHA. Si se
dejo `--skip-deploy-scaffold`, el deploy sigue disponible a mano con
`webcmp-deploy`.

El boton **"Aplicar cambios"** del panel de entorno sale del mismo paso, en
[`setup_apply()`](../deploy/install.sh): `webcmp-apply-env` y su `.path`. Son las
mismas piezas con un ejecutor distinto, y por eso `--skip-deploy-scaffold` tambien
lo omite; los archivos y permisos estan en **El boton "Aplicar cambios" del panel
de entorno**.

El `deploy.env` del camino manual sale de `deploy.env.example`, que **no define
ninguna `HEALTHCHECK_*`**. Sin ellas el health check va al 443 de `127.0.0.1`
con el SNI de `PUBLIC_HOSTNAME`, que es lo correcto con Nginx local y TLS en
esta maquina. Con proxy externo hay que agregarlas a mano: el 443 de este
servidor no lo escucha nadie y el health check fallaria siempre.

```bash
# En /etc/webcmp/deploy.env, solo con PROXY_MODE=external
HEALTHCHECK_URL=http://www.meteocamaguey.cu/
HEALTHCHECK_RESOLVE=www.meteocamaguey.cu:80:127.0.0.1
```

`webcmp-deploy --configure` reescribe ese archivo preguntando lo mismo, y
`webcmp-deploy --check` valida la configuracion sin desplegar nada.

El script va fuera del checkout (`/usr/local/sbin`): corre como root y el
checkout es escribible por `webcmp`, asi que dejarlo ahi seria una escalada de
privilegios.

## Secrets de GitHub

**No hay ninguno que cargar.** El deploy no se autentica contra GitHub:
`webcmp-autodeploy` resuelve `refs/heads/main` con `git ls-remote` sobre el
repositorio publico, sin credenciales y sin tocar el checkout.

## Correr un deploy a mano

Sin PR, sin CI. Para un fix caliente o un reintento:

```bash
sudo /usr/local/sbin/webcmp-deploy <sha-o-rama>
```

Desde el panel, el boton **Actualizar** hace exactamente lo mismo y deja registro
en `/var/lib/webcmp/deploy-status.json` y en el journal.

## El boton "Actualizar" del panel

Un superusuario ve en el **navbar del dashboard** un icono de refresco; cuando el
servidor no esta en el ultimo commit de `main`, le aparece un punto rojo. Al
abrirlo, el desplegable muestra el rango `deployed → latest` y el boton. Vive en el
layout, asi que aparece en todas las paginas del dashboard, no solo en el index. El
commit desplegado sale de `/etc/webcmp/deployed` (lo escriben `deploy.sh` en cada
deploy e `install.sh` al terminar la instalacion), y el
ultimo de `main` de un `git ls-remote` contra el repositorio publico: sin
credenciales, sin clave y sin tocar el checkout.

Cuando hay actualizacion pendiente, el desplegable muestra ademas **que cambio**:
la API publica de GitHub resuelve el rango `deployed...latest` y se listan hasta
10 mensajes, solo la primera linea de cada uno. Se consulta unicamente en ese
estado y se cachea 5 minutos, porque sin token el limite es de 60 peticiones por
hora. Si GitHub limita o no responde, el aviso sigue mostrando los SHA: los
mensajes son un extra, nunca un requisito. La URL de la API se puede cambiar con
`UPDATE_REPO_API` si el repositorio se mueve.

Lo que hace el boton **no** es desplegar. Escribe un archivo y se va:

```
Django (webcmp)  --escribe-->  /var/lib/webcmp/deploy-request
                                        |
                                systemd .path (PathExists=)
                                        |
                                        v
                          webcmp-autodeploy (usuario: deploy)
                                        |
                              sudo -n /usr/local/sbin/webcmp-deploy <sha>
                                        |
                                        v
                          escribe /var/lib/webcmp/deploy-status.json
```

`webcmp` corre con shell `/bin/false` y sin sudo: escribir un archivo no le da
root. El unico sudoers es el de `deploy`, acotado a `webcmp-deploy`.

El ejecutor **no confia en el SHA del archivo**: lo resuelve el mismo con `git
ls-remote` sobre `refs/heads/main`. Aunque el proceso web este comprometido y escriba
lo que quiera, lo maximo que consigue es desplegar lo que ya esta en `main`.

### Progreso del despliegue

Mientras el despliegue corre, el desplegable reemplaza el texto **"Desplegando…"** por
una barra `progress-sm` de Tabler y, debajo, la accion que se esta ejecutando. La
barra avanza con los pasos reales de `deploy.sh`, que publica la clave del paso en
`/var/lib/webcmp/deploy-progress.json`. El archivo lleva **solo la clave**: la
etiqueta y el porcentaje los calcula el panel desde una tabla cerrada, igual que los
mensajes de estado, para que un archivo escrito por root no sea un canal de texto
hacia el navegador del administrador.

| Paso publicado | Accion que muestra el panel | Barra al empezar |
|---|---|---|
| `code` | Actualizando el código | 0% |
| `deps` | Instalando dependencias | 15% |
| `checks` | Validando la configuración | 45% |
| `backup` | Respaldando la base de datos | 55% |
| `migrate` | Aplicando migraciones | 65% |
| `static` | Recolectando estáticos | 85% |
| `restart` | Reiniciando los servicios | 90% |
| `healthcheck` | Comprobando el sitio | 95% |

Antes del primer paso —el ejecutor todavia resolviendo el commit— y durante el
rollback la barra es **indeterminada**, no un 0% quieto: en la ventana inicial aun no
hay avance que medir, y revertir no avanza hacia el final. Con el despliegue ya
terminado (o fallido) el bloque de progreso desaparece y vuelven los mensajes de
siempre.

Escribir ese archivo es **best-effort**: si el directorio no existe o no se puede
escribir, `deploy.sh` sigue y el panel muestra "Preparando el despliegue…". Una barra
de progreso no puede abortar el despliegue que esta describiendo.

### Cuando el deploy falla

Un deploy que aborta revierte el codigo y **no** toca `/etc/webcmp/deployed`, asi
que el commit sigue pendiente. El panel lo distingue con un estado propio
(`failed`): el desplegable muestra **"El despliegue fallo"** y el boton para
reintentar, en vez de repetir "Actualizacion disponible" como si nunca se hubiera
intentado. Un fallo viejo no mancha un commit nuevo: si `main` avanzo despues del
intento, lo pendiente es otro commit y se anuncia como actualizacion normal.

El motivo no esta en el panel a proposito (el stderr de `git` puede traer rutas del
servidor). Esta en el journal:

```bash
cat /var/lib/webcmp/deploy-status.json          # status: failed + el sha intentado
journalctl -u webcmp-autodeploy -n 80 --no-pager
```

### Archivos y permisos

| Ruta | Dueno | Modo |
|---|---|---|
| `/etc/webcmp/deployed` | `root:root` | 0644 |
| `/var/lib/webcmp` | `deploy:webcmp` | 2775 |
| `/var/lib/webcmp/deploy-request` | `webcmp:webcmp` | 0644 |
| `/var/lib/webcmp/deploy-status.json` | `deploy:webcmp` | 0644 |
| `/var/lib/webcmp/deploy-progress.json` | `root:webcmp` | 0644 |
| `/usr/local/sbin/webcmp-autodeploy` | `root:root` | 0755 |

El 2775 del directorio lleva setgid a proposito: hace que el archivo de peticion y
el de estado nazcan con grupo `webcmp` los escriba quien los escriba, asi que cada
lado puede leer lo que escribio el otro.

`install.sh` crea todo esto: el andamiaje en `setup_autodeploy()` y
`/etc/webcmp/deployed` al terminar. Si se corre con
`--skip-deploy-scaffold`, el boton no se instala (necesita el usuario `deploy`).

### Apagarlo

```bash
sudo systemctl disable --now webcmp-autodeploy.path
sudo rm -f /var/lib/webcmp/deploy-request
```

A partir de ahi el dashboard sigue mostrando el aviso, pero la peticion queda en
disco y nadie la recoge. El deploy manual (`webcmp-deploy`) no se toca.

### Diagnosticar

```bash
systemctl status webcmp-autodeploy.path
journalctl -u webcmp-autodeploy -n 50
cat /var/lib/webcmp/deploy-status.json
cat /var/lib/webcmp/deploy-progress.json   # paso en curso de la barra
cat /etc/webcmp/deployed
```

Si el panel se queda en "Desplegando..." para siempre, el ejecutor murio antes de
escribir `failed`. Pasada una hora el panel deja de creer ese estado
(`RUN_STALE_SECONDS`) y vuelve a mostrar el aviso con el boton; el estado crudo
sigue en el archivo. La fuente de verdad es `journalctl`, no el panel.


## El boton "Aplicar cambios" del panel de entorno

Configuracion → Entorno del servidor guarda los parametros editables, pero Django
los leyo al arrancar: hasta que el servicio no se reinicie, el sitio sigue usando
los valores viejos. Por eso la pagina tiene, junto a **Guardar**, un boton
**Aplicar cambios** que hace las dos cosas en un solo paso.

Son dos botones y no uno porque **Guardar** sirve para dejar preparado un cambio
que se aplicara en la ventana de mantenimiento. **Aplicar cambios** es para
cuando el cambio se quiere en el sitio ahora. Un boton que solo reiniciara
descartaria en silencio lo que el operador acaba de escribir, asi que no existe.

**Aplicar cambios** solo se habilita con el sitio en **modo mantenimiento**
(Configuracion -> Configuracion del sitio). El reinicio corta las conexiones
abiertas, asi que el panel no lo dispara con el portal atendiendo publico: fuera
de esa ventana la pagina lo explica, el POST de aplicar se rechaza sin tocar el
`.env` y **Guardar** sigue disponible para dejar los valores listos hasta el
proximo reinicio. La comprobacion se hace en el servidor, no solo en el boton.

El mecanismo es el mismo del boton "Actualizar", con un privilegio distinto:

```
Django (webcmp)  --escribe-->  /var/lib/webcmp/apply-request
                                        |
                                systemd .path (PathExists=)
                                        |
                                        v
                          webcmp-apply-env (usuario: root)
                                        |
                          systemctl restart webcmp webcmp-huey
                                        |
                                        v
                          escribe /var/lib/webcmp/apply-status.json
```

Este es el **unico** lugar del diseno donde el ejecutor corre como root, porque
reiniciar un servicio del sistema lo exige. Ese privilegio es seguro solo
mientras el script no acepte ordenes, y no las acepta:

- No lee destinos ni comandos del archivo de peticion. Del contenido solo toma
  el nombre de quien lo pidio, y lo sanea antes de escribirlo.
- Las unidades que reinicia estan escritas en el propio unit
  (`Environment="UNITS=..."`), que `install.sh` renderiza desde `GUNICORN_UNIT` y
  `HUEY_UNIT`.
- No ejecuta nada del checkout. El script vive en `/usr/local/sbin`, `root:root`,
  fuera del directorio que escribe `webcmp`.

Un proceso web comprometido consigue, como mucho, reiniciar esos dos servicios
—algo que ya puede lograr tumbando el proceso— y no gana un interprete ni un
comando arbitrario.

Se reinician **los dos** servicios siempre: Huey manda correos y genera PDF con la
configuracion que leyo al arrancar, asi que reiniciar solo Gunicorn deja al worker
con los valores viejos.

### Lo que el panel muestra despues

El ejecutor escribe `apply-status.json` y la pagina lo lee en cada visita:

| Estado | Cuando | Que dice la pagina |
|---|---|---|
| `running` | el ejecutor arranco y todavia no termino | "Reinicio en curso" |
| `ok` | `systemctl restart` salio 0 | "Ultimo reinicio completado", con fecha y autor |
| `failed` | `systemctl restart` fallo | "El ultimo reinicio fallo", con el motivo |
| `stale` | `running` con mas de 180 s (`RUN_STALE_SECONDS`) | "quedo sin confirmar; revise los servicios" |

`stale` existe porque un `running` eterno es peor que un aviso: systemd puede
matar la unidad por timeout y el panel se quedaria "en curso" para siempre. Un
estado que no se reconoce, o un JSON a medias, no se muestra como si fuera un
resultado: la pagina no inventa.

Si el directorio no es escribible por el proceso web, el boton queda
deshabilitado y la pagina lo explica. En un servidor sin esta unidad instalada,
**Guardar** sigue funcionando y el reinicio se hace a mano.

### Archivos y permisos

| Ruta | Dueno | Modo |
|---|---|---|
| `/var/lib/webcmp/apply-request` | `webcmp:webcmp` | 0644 |
| `/var/lib/webcmp/apply-status.json` | `root:webcmp` | 0644 |
| `/usr/local/sbin/webcmp-apply-env` | `root:root` | 0755 |
| `/etc/systemd/system/webcmp-apply.path` | `root:root` | 0644 |
| `/etc/systemd/system/webcmp-apply.service` | `root:root` | 0644 |

`install.sh` lo instala en `setup_apply()`, junto al andamiaje del boton
"Actualizar". Con `--skip-deploy-scaffold` no se instala.

### Apagarlo

```bash
sudo systemctl disable --now webcmp-apply.path
sudo rm -f /var/lib/webcmp/apply-request
```

A partir de ahi la peticion queda en disco y nadie la recoge: el boton sigue
visible, porque lo que lo habilita es el permiso del directorio, no la unidad.
Como el archivo no se consume, el segundo intento avisa **"Ya hay un reinicio en
curso"** aunque no haya ninguno. Es el precio de no dejar que el proceso web
pregunte por systemd; si se apaga la unidad, conviene quitar el boton de la vista
o volver a habilitarla. **Guardar** no se ve afectado y el reinicio se hace a
mano. Para volver a habilitarlo, `sudo systemctl enable --now webcmp-apply.path`.

### Diagnosticar

```bash
systemctl status webcmp-apply.path
journalctl -u webcmp-apply -n 50
cat /var/lib/webcmp/apply-status.json
```


## Las notificaciones del panel

El mismo navbar trae un icono de campana con el numero de notificaciones sin
leer. Las notificaciones las escribe el propio sistema en `Notification`
(`apps/core/models.py`), nunca una persona, y salen de cuatro hechos:

| Origen | Que lo dispara |
|---|---|
| `huey` | una tarea de Huey termino en `ERROR` **y no le quedan reintentos** (solo `send_email_task`) |
| `invoice` | fallo el PDF o el correo de una factura (la clave incluye cual de los dos) |
| `warning` | un `Warning` vence dentro del horizonte configurado |
| `subscription` | un cliente solicita un servicio desde el portal |
| `invoice_pending` | una factura lleva `INVOICE_PENDING_REMINDER_DAYS` dias sin pagarse |

Los destinatarios son **todos los superusuarios activos**, y el queryset se
filtra ademas por `recipient`: un administrador no ve la notificacion de otro.

El recordatorio de facturas corre en la tarea periodica
`apps.commercial.tasks.recordar_facturas_pendientes` (`crontab(minute=0,
hour=13)`, las **09:00 en `America/Havana`**). Mientras la factura siga sin
cobrarse vuelve cada `INVOICE_PENDING_REMINDER_DAYS` dias, no en cada corrida
diaria: leer el aviso no lo hace reaparecer al dia siguiente.

El desplegable lista **solo las no leidas**; abrir una la marca leida y navega
al objeto. Las leidas quedan en "Ver todas", donde tambien se pueden eliminar
una a una o todas de golpe.

### Configuracion

Todo por variables de entorno, con default en `config/settings/base.py`:

| Variable | Default | Que controla |
|---|---|---|
| `NOTIFICATIONS_ENABLED` | `True` | Si se crean notificaciones y se muestra el icono |
| `NOTIFICATIONS_RETENTION_READ` | `30` | Dias que se conserva una notificacion **leida** |
| `NOTIFICATIONS_RETENTION_UNREAD` | `90` | Dias que se conserva una notificacion **sin leer** |
| `NOTIFICATIONS_MAX_VISIBLE` | `10` | Notificaciones sin leer que caben en el desplegable |
| `NOTIFICATIONS_WARNING_HORIZON_HOURS` | `48` | Antelacion con que se recuerda un aviso por vencer |
| `INVOICE_PENDING_REMINDER_DAYS` | `7` | Dias sin cobrar que disparan el recordatorio, y cadencia con que se repite |

### Apagarlos

```bash
# en el .env del servidor
NOTIFICATIONS_ENABLED=False
```

Apagado: no se crean filas y el icono no se renderiza. El badge se refresca
contra `GET /notificaciones/sin-leer/`, que responde `{"unread": null}` cuando el
sistema esta apagado; el JS lo interpreta como "no hay numero que mostrar" y deja
de preguntar. **La poda sigue
corriendo**: limpiar datos viejos no es una funcion visible que haya que apagar,
y dejarla detenida solo hace crecer la tabla.

### Poda

Dos entradas a la misma funcion (`apps.core.notifications.prune_notifications`):

- **Huey**, tarea `apps.core.tasks.podar_notificaciones`, `crontab(minute=0,
  hour=4)`. Huey corre con `utc=True`, asi que son las **00:00 en
  `America/Havana`**. Se registra porque `CoreConfig.ready()` importa
  `apps/core/tasks.py`; `apps/core/tests/test_huey_registry.py` afirma el
  conjunto exacto de tareas del consumer y falla si esa cadena se rompe.
- **A mano**, cuando haga falta:

```bash
.venv/bin/python manage.py notificar_podar
```

Borra, en este orden, los vencidos (`expires_at`), los leidos mas viejos que
`NOTIFICATIONS_RETENTION_READ` y los no leidos mas viejos que
`NOTIFICATIONS_RETENTION_UNREAD`. Es idempotente: correrlo dos veces seguido
borra cero la segunda y lo informa.

### Diagnosticar

```bash
.venv/bin/python manage.py notificar_podar            # que borraria ahora
journalctl -u webcmp-huey -n 50 | grep -i notificac  # poda y recordatorios
```

El badge cuenta las **no leidas** y el historial muestra todas: que no coincidan
no es un defecto. Dentro de cada uno la fuente es una sola
(`apps.core.notifications.visible_for`): la usan el navbar, el historial y el
contador. Una notificacion vencida no se muestra aunque la poda todavia no la
haya borrado: `expires_at` es la fecha del hecho, la poda es mantenimiento.

### El endpoint del contador

```
GET /notificaciones/sin-leer/     ->  {"unread": 2}
                                  ->  {"unread": null}      # sistema apagado
                                  ->  304                    # con If-None-Match
```

Solo superusuario (403 en cualquier otro caso), `Cache-Control: private,
no-cache` y `ETag` derivado del cuerpo, para que la mayoria de los sondeos
devuelvan 0 bytes. El JS (`static/dist/js/notifications-badge.js`) pregunta cada
20 s y **no pregunta mientras la pestaña esta oculta**.

Vive aparte del estado de despliegue del panel: ese JSON
(`dashboard:update_status`) lee archivos y compara commits con el remoto, es
caro por diseño y solo lo pide el panel de administracion. El contador es un
`COUNT` indexado, pensado para que lo consulte la campana del portal publico.

Con `USE_REDIS_CACHE=True` el conteo se guarda 20 s y se invalida al marcar,
borrar o crear un aviso. Con `USE_REDIS_CACHE=False` **no se cachea**: LocMem
vive en cada worker de Gunicorn, y ahi invalidar solo alcanzaria al worker que
atendio el POST, dejando a los otros dos sirviendo un numero viejo.


## La franja de aviso meteorologico

El portal publico pinta una franja arriba de todo cuando hay un `Warning` vigente
(`valid_until >= ahora`). **No es una notificacion**: se sirve en el HTML, sin
sesion y sin JavaScript, porque tiene que alcanzar a un visitante anonimo. Sigue
el tema del visitante (claro u oscuro); no impone fondo oscuro.

Sale de `apps.core.context_processors.menu_notifications`, la misma consulta que
alimenta los badges del menu.

- **Manda la gravedad, no la fecha.** Ciclon > tormenta > alerta temprana. Entre
  dos del mismo tipo gana el mas reciente.
- **No se puede cerrar.**
- **El color no es la unica senal**: el tipo va escrito al lado del punto.
- **En el landing solo entran ciclon y tormenta.** La portada omite el resto
  (`severe_only=True` en `templates/layouts/landing.html`).

### Apagarla

```bash
# en el .env del servidor
EMERGENCY_BANNER_ENABLED=False
```

Apagarla no toca el contenido publicado: las paginas de avisos y los badges del
menu siguen igual.

## Operacion del servicio

```bash
sudo systemctl restart webcmp   # aplica codigo nuevo
sudo systemctl reload webcmp    # solo recarga configuracion
sudo journalctl -u webcmp --since "1 hour ago" -p err
```

Rotar claves (invalida sesiones y cookies firmadas):

```bash
sudo -u webcmp .venv/bin/python scripts/generate_env.py --production --rotate-keys
sudo systemctl restart webcmp
```

Limpiar archivos huérfanos de `media/` (tiene `--dry-run`):

```bash
sudo -u webcmp .venv/bin/python manage.py cleanup_orphan_media --dry-run
```

## Errores que solo aparecen en produccion

Sintomas que un `collectstatic` o un `manage.py check` en verde no detectan:

| Sintoma | Causa |
| --- | --- |
| `ValueError: Missing staticfiles manifest entry for ''`, 500 en **cada** pagina | Algun template concatena `STATIC_URL` a mano. Con el storage de manifest hay que pasar la ruta a `{% static 'ruta' %}` |
| `DisallowedHost: Invalid HTTP_HOST header: 'webcmp'` en cada peticion | `location /` declara `proxy_set_header` para el websocket, y eso descarta los del `server`: hay que repetir `Host $host` ahi |
| Un 400 que se convierte en 500 | Los handlers de error renderizan plantillas: el context processor necesita `getattr(request, 'user', None)`, porque `AuthenticationMiddleware` corre despues de `CommonMiddleware` |
| 502 en todo, con `connect() failed (13: Permission denied)` en el log de Nginx | `www-data` no esta en el grupo `webcmp`, y el socket sale `0770 webcmp:webcmp` |
| El sitio se cae cada 60 s y vuelve solo | `WatchdogSec` en el unit: Gunicorn manda `READY=1`, nunca `WATCHDOG=1`, y systemd manda `SIGABRT` |
| `collectstatic` falla con `Post-processing 'x.js' failed!` | Un `.min.js` vendoreado referencia un `.map` que no esta en el repo, y el storage de manifest resuelve cada referencia |
| `[ERROR] Control server error: Permission denied: '/home/webcmp'` en cada arranque | El control socket de Gunicorn cae a `$HOME/.gunicorn/`, y `webcmp` no tiene home |
| `the cache has been disabled` en cada deploy | `sudo` aplica `env_reset` y descarta `PIP_CACHE_DIR`: pasala con `sudo -u user env PIP_CACHE_DIR=... cmd` |

Dos se detectan antes de tocar produccion:

- `manage.py check --deploy` con la configuracion real ya esta en el deploy.
- Las referencias del tag `static` se pueden revisar con un chequeo de dos
  segundos: todas las rutas de `{% static '...' %}` tienen que existir en disco,
  porque el manifest revienta en el **primer request** de esa pagina, no en el
  deploy. Ignorar lo que esta dentro de `{% comment %}`: Django no lo evalua.

## Primer deploy: checklist

Proba el rollback una vez, a mano, antes de confiar en el deploy automatico.

```bash
# 1. El servidor tiene /etc/webcmp/deploy.env y /etc/webcmp/encryption.env.
sudo install -o root -g root -m 0600 deploy/deploy.env.example /etc/webcmp/deploy.env

# 2. El script anda con los permisos correctos.
sudo -u webcmp /srv/webcmp/.venv/bin/python -c 'import django'   # el venv esta sano

# 3. El gate real pasa.
sudo systemd-run --quiet --wait \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --uid=webcmp --gid=webcmp \
  --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py check --deploy --fail-level WARNING

# 4. Deploy de verdad, a mano, con un SHA que ya sepas que es bueno.
sudo /usr/local/sbin/webcmp-deploy main

# 5. Recien ahi, probalo mal a proposito: desplegar un commit anterior y
#    confirmar que el sitio vuelve atras sin intervention.
sudo /usr/local/sbin/webcmp-deploy <commit-anterior>
```

El paso 3 no imprime nada en la terminal: decide el codigo de salida, y el
reporte queda en el journal (`journalctl --since -5min | grep -A5 'check --deploy'`).
**No le agregues `--pipe`**: en systemd 259 falla de forma intermitente con
`Failed to start transient service unit: Connection reset by peer`.

Si el paso 3 falla, el problema es el `.env` o `encryption.env`, no el deploy.
