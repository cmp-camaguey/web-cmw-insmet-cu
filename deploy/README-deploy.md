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
| 8 | `migrate` y `migrate --check` | Antes de que el codigo nuevo atienda un request. El `--check` confirma que no queda nada sin aplicar |
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

### `manage.py` a mano cae en el perfil de desarrollo

Los comandos de Django corren en una unidad transitoria con el mismo
`EnvironmentFile` y `PRODUCTION=1` que `webcmp.service`. Un `manage.py` corrido en
una shell sin `PRODUCTION` usa el perfil de desarrollo y opera contra la base
equivocada.

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

Faltan tres paquetes en la lista corta, y sin ellos ni `pip install` ni `manage.py`
llegan a empezar:

| Paquete | Por qué |
| --- | --- |
| `build-essential` | Python 3.14 no tiene ruedas para varios pins de `prod.txt` y hay que compilar |
| `libpango1.0-dev` | WeasyPrint (PDF) lo carga por cffi |
| `redis-server` | El `.env` de producción trae `USE_REDIS_CACHE=True` |

Los dos primeros fallan con errores que no dicen qué falta:

```bash
# Sin build-essential
#   Running cc --version gave [Errno 2] No such file or directory: 'cc'

# Sin libpango1.0-dev
#   ffi.dlopen('libpango-1.0.so.0')
```

> Ojo con el nombre: en Ubuntu 24.04+ es `libpango1.0-dev`, **no**
> `libpango-1.0-dev`.

Usuario dedicado del servicio:

```bash
# El servicio NUNCA corre como root, y este usuario no tiene shell a proposito:
# existe para correr Gunicorn y Huey, no para que alguien se loguee.
sudo useradd -r -s /bin/false webcmp
sudo mkdir -p /srv/webcmp && sudo chown webcmp:webcmp /srv/webcmp
```

### 2. Base de datos

El rol de PostgreSQL y el del servicio son el mismo (`webcmp`), y la base es
propiedad de ese rol. `psycopg[binary]` va en `requirements/base.txt`, así que lo
traen los tres entornos: es el driver del motor, no una herramienta de
desarrollo.

```bash
sudo -u postgres psql <<'SQL'
CREATE USER webcmp WITH PASSWORD 'CAMBIAR_ESTA_CLAVE';  -- pragma: allowlist secret
CREATE DATABASE webcmp OWNER webcmp;
\c webcmp
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO webcmp;
SQL
```

El `GRANT` explícito sobre el esquema `public` no es redundante con el `CREATE
DATABASE OWNER`: en PostgreSQL 15+ `PUBLIC` ya no tiene `CREATE` en `public`, pero
sin ese `GRANT` el rol tampoco puede escribirlo, y `migrate` falla al crear la
primera tabla.

Si vas a correr la suite de tests en el mismo servidor, el rol necesita poder
crear la base de prueba:

```bash
sudo -u postgres psql -c 'ALTER ROLE webcmp CREATEDB'
```

Sin eso, `python manage.py test` muere con `permission denied to create database`
después de encontrar los 1184 tests.

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

# `generate_env.py` tiene que correr como root: escribe /etc/webcmp/encryption.env,
# que es root con modo 600 y webcmp no puede crear. Correlo como webcmp falla y no
# escribe nada (falla cerrado, lo cual esta bien).
sudo install -d -m 0755 -o root -g root /etc/webcmp
sudo .venv/bin/python scripts/generate_env.py --production

# Las migraciones vienen versionadas en el clone, asi que `migrate` es suficiente.
sudo -u webcmp .venv/bin/python manage.py migrate
sudo -u webcmp .venv/bin/python manage.py collectstatic --no-input
```

> **Los `manage.py` de este bloque son un caso especial.** Corren a secas, sin el
> `systemd-run` que sí usan los pasos 6 y 7, y funcionan solo porque los dos
> comandos de acá (`migrate`, `collectstatic`) no necesitan la
> `ENCRYPTION_KEY`: leen el `.env` como webcmp y el perfil de desarrollo les sirve.
>
> El día que quieras correr **otro** `manage.py` a mano, ese no es el camino. Sin el
> `PRODUCTION=1` del unit caés al perfil de desarrollo y `migrate` se ejecuta
> contra **SQLite**: dice que todo bien y el sitio sigue en PostgreSQL. Para
> cualquier otro comando, el contexto correcto es el del ejecutor del botón
> (el `systemd-run` con `PRODUCTION=1`), que es lo que hace `deploy/deploy.sh`.
>
> Y `systemd-run` va con `sudo`, nunca con `sudo -u webcmp`: necesita hablar con el
> systemd del sistema, y el drop de privilegios lo hace `--uid=webcmp`. Con `sudo
> -u webcmp systemd-run` el comando muere con `Failed to start transient service
> unit: Access denied`.

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

> `webcmp` es una cuenta de sistema **sin home**, así que pip no encuentra dónde
> escribir su caché y cada `pip install` baja todo desde PyPI. Dedicale uno:
> `sudo install -d -m 0700 -o webcmp -g webcmp /var/cache/webcmp-pip`. El deploy lo
> exporta solo (`PIP_CACHE_DIR` en `/etc/webcmp/deploy.env`).

> `collectstatic` **no es instantáneo**: son ~917 archivos y ~114 MB, y con
> `CompressedManifestStaticFilesStorage` genera además el `.gz` y el `.br` de cada
> uno. En esta máquina son unos 3 minutos. El deploy completo anda por los 5.

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

> **`EnvironmentFile=-` lleva el guion a propósito.** Sin la clave de descifrado, el
> servicio arranca y falla con el error accionable de `load_secret_key()`. Sin el
> guion, fallaría systemd con un error que no dice qué hacer.

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

> **`X-Forwarded-Proto` es el header que rompe el login, en las dos direcciones.**
> Django lo lee con `SECURE_PROXY_SSL_HEADER` para resolver `request.is_secure()`.
> Si falta, las cookies de sesión seguras no se envían y el usuario entra en un
> bucle de logout sin un solo error en el log.
>
> - Con TLS acá (`webcmp.conf.example`) la línea correcta es
>   `proxy_set_header X-Forwarded-Proto $scheme;`. **No la quites.**
> - Detrás de un proxy externo (`webcmp-http.conf.example`) esa misma línea es el
>   bug: `$scheme` vale `http` y **sobrescribe** el `https` que manda NPM. Ese
>   vhost usa un `map` que **propaga** el valor recibido y cae a `$scheme` solo
>   cuando no viene header. No lo reemplaces por el `$scheme` del otro vhost.

> **`Host` está repetido dentro de `location /`, y no es una redundancia.** En
> Nginx, `proxy_set_header` se hereda del `server` solo si el `location` no declara
> ninguno. `location /` declara dos (para el websocket), así que los del `server`
> se descartan ahí y el `Host` vuelve al default, que es `$proxy_host`: el nombre
> del upstream. Gunicorn recibía `Host: webcmp` y contestaba `DisallowedHost` en
> todas las peticiones. Si sacás esas líneas, el sitio entero responde 400. Aplica
> a los dos vhosts.

### 6. Datos iniciales y usuario administrador

Una base migrada está vacía: sin municipios ni estaciones, las páginas de pronóstico
no tienen contra qué renderizar.

> **`--pipe` es frágil en systemd 259.** Envía descriptores por D-Bus al arrancar la
> unidad transitoria y falla de forma intermitente con `Failed to start transient
> service unit: Connection reset by peer`. Por eso `deploy.sh` ya no lo usa:
> captura la salida con `-p StandardOutput=append:<archivo>` y devuelve el código
> de salida del comando. Los `systemd-run` de esta sección conservan `--pipe`
> porque hay un humano mirando que ve el error y reintenta —no porque sea seguro—;
> para un comando no interactivo, o usá `webcmp-deploy`, o reemplazá `--pipe` por
> `-p StandardOutput=append:/tmp/out -p StandardError=append:/tmp/out` y después
> `cat /tmp/out`.

```bash
sudo systemd-run --pipe --wait --uid=webcmp \
  -p EnvironmentFile=/etc/webcmp/encryption.env \
  -p Environment=PRODUCTION=1 --working-directory=/srv/webcmp \
  /srv/webcmp/.venv/bin/python manage.py add_stations_data
```

Toma `--provincia` (default `Camagüey`) y `--tipo` (`municipios`, `estaciones` o
`todo`, que es el default).

El usuario administrador se crea con `createsuperuser`. Hay dos formas, y la
diferencia importa:

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

> **La contraseña va adentro del `systemd-run`, no en tu shell.**
> `systemd-run` no hereda el entorno de quien lo invoca, así que
> `DJANGO_SUPERUSER_PASSWORD=x systemd-run ... createsuperuser` **crea el usuario
> con una contraseña inservible y no avisa**. El síntoma es un login que siempre
> rechaza la clave. Por eso va como `-p Environment=DJANGO_SUPERUSER_PASSWORD=...`.
>
> `--uid=webcmp` tampoco es opcional: sin él el comando corre como root, no puede
> leer el `.env` (600 de `webcmp`) y falla con un error de descifrado que parece un
> problema de claves.

Después del primer login, el usuario va a ser redirigido a
`/accounts/profile/update/` en cada navegación hasta que complete email, nombre y
apellido. No es un bug: es `CheckUserProfileMiddleware` (`apps/core/middleware.py`),
que manda a cualquier usuario con el perfil incompleto a esa página. Completalo y
seguí.

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

Lo que decide el operador, y que antes habia que editar a mano en tres
lugares distintos (`generate_env.py`, `server_name` de Nginx y `PublicHostname`
del deploy): el dominio y su `www`, los puertos, la base de datos (rol y base
creados de verdad con el usuario prompted), el correo, el superusuario y el modo
de proxy. Todo eso sale en el mismo `.env` y en el mismo `deploy.env`, asi que no
pueden quedar desincronizados.

Certificado, segun el caso: **autofirmado** (sin red, funciona con DNS privado o
con puertos cerrados; el navegador lo marca como no seguro y el health check
deja de verificar el TLS) o **Let's Encrypt** con `certbot --webroot`. Con
Let's Encrypt, la primera emision levanta el vhost con un autofirmado temporal
para que el puerto 80 responda el challenge de ACME, y recien despues cambia el
vhost al certificado real. Ese temporal queda en `/etc/webcmp/tls/` y ya no se
usa: se puede borrar a mano.

El instalador imprime la clave privada del superusuario **una sola vez**, y solo
si la genero el. Las claves Django se pueden rotar despues con
`manage.py rotatekeys`; la contrasena de la base esta en el `.env` y se edita
alla.

Al final queda el andamiaje del boton "Actualizar": el usuario `deploy`, el
sudoers acotado a un comando, `/usr/local/sbin/webcmp-deploy` y el ejecutor
`webcmp-autodeploy` con su `.path` de systemd. **No se genera ninguna credencial
de despliegue**: la actualizacion es pull, asi que no hay clave privada que sacar
del servidor ni secret que cargar en GitHub. Ver **El boton "Actualizar"**.

Re-correr el script es seguro: cada pregunta muestra el valor actual como
default, no regenera claves, no borra la base y no pisa ni `/etc/webcmp/deploy.env`
ni un `.env` afinado a mano sin confirmarlo.

> Si el servidor ya venia de supervisor + `gunicorn.sh`, primero
> `sudo systemctl stop supervisor`. Nginx tiene que apuntar a
> `unix:/run/webcmp/gunicorn.sock`, no a `/tmp/gunicorn-webcmp.sock`.

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

### Prerrequisitos que el paso 1 no lista

Probado sobre Ubuntu 26.04 / Python 3.14. `install.sh` instala todo esto por apt
en el preflight, asi que en el camino automatico no hay que hacerlo a mano; la
tabla importa para el camino manual y para saber que se puede quitar sin
preguntar. Lo que lista el paso 1
(`libcairo2-dev pkg-config python3-dev wkhtmltopdf`) no alcanza: `pip install`
falla y `manage.py` no llega a importar.

| Paquete | Por que |
| --- | --- |
| `build-essential` | En Python 3.14 no hay ruedas para varios pins (`pycairo` entre otros) y hay que compilar. Sin el, pip muere con `Running cc --version gave "No such file or directory: 'cc'"` |
| `libpango1.0-dev` (ojo: el nombre en Ubuntu 26.04 **no** es `libpango-1.0-dev`) | WeasyPrint lo necesita para renderizar PDF. Sin el, `manage.py check` muere con `ffi.dlopen('libpango-1.0.so.0')` |
| `redis-server` | El `.env` generado trae `USE_REDIS_CACHE=True` aunque la introduccion diga que Redis es opcional. O se instala Redis o se pone `False` |
| `postgresql-client` | `pg_dump` para el respaldo previo a migrar. Sin el, un deploy con migraciones pendientes se niega a correr |
| `libcairo2-dev pkg-config python3-dev` | Los que si lista el paso 1, para `pycairo` |

### Generar el `.env` como root

`install.sh` lo hace solo. En el camino manual, `generate_env.py` corre **como
root** y después devuelve el `.env` a `webcmp`, porque Django corre como ese
usuario y tiene que poder leerlo:

```bash
sudo install -d -m 0755 -o root -g root /etc/webcmp
sudo .venv/bin/python scripts/generate_env.py --production
sudo chown webcmp:webcmp /srv/webcmp/.env     # Django corre como webcmp
sudo chmod 600 /srv/webcmp/.env
```

Corrido como `webcmp` falla sin escribir nada: no puede crear
`/etc/webcmp/encryption.env`, que es root con modo 600.

Ahi va el hostname real: el generador escribe `EXTERNAL_HOSTNAME=www.meteocamaguey.cu` por
default y no tiene flag para cambiarlo, asi que `EXTERNAL_HOSTNAME`,
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y `CORS_ALLOWED_ORIGINS` hay que
editarlos a mano. Si no, el sitio responde `DisallowedHost` a cada peticion.

Con eso, `PublicHostname` del deploy, el `server_name` de Nginx y el
`EXTERNAL_HOSTNAME` del `.env` tienen que ser exactamente el mismo: el health
check entra a `127.0.0.1:443` con ese nombre como SNI y como `Host`.

Por que el script va en `/usr/local/sbin` y no en `/srv/webcmp/deploy/deploy.sh`:
el script corre como root y el checkout es escribible por `webcmp`. Con el sudoers
de arriba, tener el script dentro del checkout seria una escalada de privilegios
trivial: alcanza con editar el archivo para ejecutar root.

> Si el servidor ya venia de supervisor + `gunicorn.sh`, primero
> `sudo systemctl stop supervisor` y segui los pasos 3 a 6 de la instalación de la
> aplicación. Nginx tiene
> que apuntar a `unix:/run/webcmp/gunicorn.sock`, no a `/tmp/gunicorn-webcmp.sock`.

## Secrets de GitHub

**No hay ninguno que cargar.** El deploy no se autentica contra GitHub para
desplegar: `webcmp-autodeploy` resuelve `refs/heads/main` con `git ls-remote`
sobre el repositorio publico, sin credenciales y sin tocar el checkout. Tampoco
existe una clave privada de despliegue en el servidor: el instalador ya no la
genera.

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

Ese rodeo es la pieza de seguridad del cambio. `webcmp` tiene el shell en
`/bin/false` y ningun sudo, a proposito. Darle sudo sobre `webcmp-deploy` convertiria
cualquier RCE de Django en root; escribir un archivo no. `deploy` ya tenia la unica
regla de sudoers que existe, y este cambio no le agrega ninguna.

El ejecutor **no confia en el SHA del archivo**: lo resuelve el mismo con `git
ls-remote` sobre `refs/heads/main`. Aunque el proceso web este comprometido y escriba
lo que quiera, lo maximo que consigue es desplegar lo que ya esta en `main`.

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
cat /etc/webcmp/deployed
```

Si el panel se queda en "Desplegando..." para siempre, el ejecutor murio antes de
escribir `failed`. Pasada una hora el panel deja de creer ese estado
(`RUN_STALE_SECONDS`) y vuelve a mostrar el aviso con el boton; el estado crudo
sigue en el archivo. La fuente de verdad es `journalctl`, no el panel.


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
haya borrado, y eso es a proposito: `expires_at` es la fecha del hecho, la poda
es solo mantenimiento.

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

Sale de `apps.core.context_processors.menu_notifications`, que ya recorria
`Warning` para los badges del menu. Esa consulta alimenta las tres cuentas y el
aviso que encabeza la franja: agregarla no costo una consulta extra y de paso
elimino dos, porque antes eran tres `COUNT(*)` separados. Son dos consultas
constantes menos en **cada** pagina, anonima o no.

Decisiones que no se cambian sin pensar:

- **Manda la gravedad, no la fecha.** Ciclon > tormenta > alerta temprana. Entre
  dos del mismo tipo gana el mas reciente.
- **No se puede cerrar.** Cerrar una alerta de ciclon es la unica accion que no
  queremos facilitar.
- **El color no es la unica senal**: el tipo va escrito al lado del punto, asi que
  sirve igual para quien no distingue rojo de naranja.
- **En el landing solo entran los avisos graves.** La portada de marketing omite
  el contenido vivo a proposito (`apps/home/tests/test_landing.py`), con una
  excepcion: ciclon tropical y tormenta. Es la raiz del dominio, y esos dos no se
  esconden detras de un clic a "Tiempo". Un `early` no se pinta ahi: es rutina y
  acostumbraria al visitante a ignorar la franja. La excepcion vive en
  `templates/layouts/landing.html` (`severe_only=True`), no en Python.

### Apagarla

```bash
# en el .env del servidor
EMERGENCY_BANNER_ENABLED=False
```

Apagarla no toca el contenido publicado: las paginas de avisos y los badges del
menu siguen igual.

## Operacion del servicio

```bash
sudo systemctl restart webcmp   # reinicio sin cortar conexiones
sudo systemctl reload webcmp    # recarga de workers (SIGHUP)
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
| `ValueError: Missing staticfiles manifest entry for ''`, 500 en **cada** pagina | Las plantillas hacian `{% static '' %}ruta`, concatenando `STATIC_URL` a mano. Con el storage de manifest hay que pasar la ruta al tag |
| `DisallowedHost: Invalid HTTP_HOST header: 'webcmp'` en cada peticion | En Nginx, `proxy_set_header` se hereda del `server` **solo si el `location` no declara ninguno**. `location /` declara dos para el websocket, asi que el `Host` se perdia y volvia al default `$proxy_host`, que es el nombre del upstream |
| Un 400 que se convierte en 500 | Los handlers de error renderizan plantillas, y el context processor leia `request.user` sin `getattr`. Ese atributo lo pone `AuthenticationMiddleware`, que corre despues de `CommonMiddleware`: si la excepcion salta antes, no existe |
| 502 en todo, con `connect() failed (13: Permission denied)` en el log de Nginx | `www-data` no estaba en el grupo `webcmp`, y el socket sale `0770 webcmp:webcmp` |
| El sitio se cae cada 60 s y vuelve solo | `WatchdogSec=60` en el unit, pero Gunicorn solo manda `READY=1`, nunca `WATCHDOG=1`. systemd esperaba un ping que no llega y mandaba `SIGABRT` |
| `collectstatic` falla con `Post-processing 'x.js' failed!` | El `.min.js` termina en `//# sourceMappingURL=x.js.map` y el `.map` no esta en el repo. El storage de manifest tiene que resolver cada referencia |
| `[ERROR] Control server error: Permission denied: '/home/webcmp'` en cada arranque | El control socket de Gunicorn 26 cae a `$HOME/.gunicorn/`, y `webcmp` no tiene home con `ProtectHome=true` |
| `NodeNotFoundError` al correr `migrate` | Una migracion versionada dependia de un padre nunca commiteado. El gate `makemigrations --check --dry-run` del CI detecta esa cadena rota antes del merge |
| `the cache has been disabled` en cada deploy | `sudo` aplica `env_reset` y descarta `PIP_CACHE_DIR` del entorno del llamador. Hay que pasarla con `sudo -u user env PIP_CACHE_DIR=... cmd` |

Dos se detectan antes de tocar produccion:

- `manage.py check --deploy` con la configuracion real ya esta en el deploy.
- Las referencias del tag `static` se pueden revisar con un chequeo de dos
  segundos: todas las rutas de `{% static '...' %}` tienen que existir en disco,
  porque el manifest revienta en el **primer request** de esa pagina, no en el
  deploy. Ignorar lo que esta dentro de `{% comment %}`: Django no lo evalua.

## Primer deploy: checklist

Antes de fiar el primer deploy automatico, probalo a mano contra un SHA
equivocado a proposito. El rollback automatico no se conoce hasta que se lo vio
funcionar una vez.

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

El paso 3 no imprime nada en la terminal: lo que decide es el codigo de salida, y
la salida del reporte queda en el journal (`journalctl --since -5min | grep -A5
'check --deploy'`). **No le agregues `--pipe`**: en systemd 259 ese flag pasa
descriptores por D-Bus al arrancar la unidad transitoria y falla de forma
intermitente con `Failed to start transient service unit: Connection reset by
peer`. Fue lo que tumbo el deploy del 2026-11-11, en el paso de `migrate`, y
`deploy.sh` ya no lo usa.

Si el paso 3 falla, el problema es el `.env` o `encryption.env`, no el deploy.
