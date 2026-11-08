# Despliegue automatizado

Como pasar de `sudo systemctl restart webcmp` a un deploy que se dispara solo
cuando el CI de `main` esta en verde, con gates y rollback.

La logica vive en [`deploy.sh`](deploy.sh), que corre **en el servidor**. Lo
dispara el boton **"Actualizar"** del panel: el servidor resuelve `refs/heads/main`
contra GitHub por HTTPS y aplica el commit localmente. No hace falta SSH entrante
ni ningun runner conectandose.
Una sola fuente de verdad: si el procedimiento esta en los dos lados, el dia que
uno cambie el deploy hace cosas distintas segun por donde entro.

## Que hace, y en que orden

El orden no es estetico, es la garantia:

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

### Por que `makemigrations --check` y no `makemigrations`

El proyecto **versiona las migraciones**: los `0001_initial` de cada app estan
en el repositorio y viajan en el clone. El esquema no se deriva de los modelos
en el servidor.

Con las migraciones en el arbol, `makemigrations --check --dry-run` pasa a ser
un gate util: si un cambio de modelo llega sin su migracion, el comando sale
distinto de 0 y el deploy aborta **antes** de tocar la base. El CI corre el
mismo gate antes de `migrate`.

Versionar las migraciones es lo que vuelve revisable un cambio de esquema: un
`ALTER` destructivo se ve en el diff del PR, no en el log del deploy. Sigue
siendo cierto que **el rollback del codigo no deshace el esquema**: Django no
genera migraciones inversas, y una migracion destructiva ya aplicada deja la
base con el esquema nuevo.

### Respaldo antes de migrar

Con el update por boton nadie mira el deploy: un superusuario aprieta, el commit
se aplica y `migrate` corre solo. Por eso `deploy.sh` toma un `pg_dump` **antes**
de migrar, y solo cuando hay algo que migrar:

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

Restaurar, con el sitio parado:

```bash
sudo systemctl stop webcmp webcmp-huey
sudo -u webcmp pg_restore -d <base> --clean --if-exists \
    /var/backups/webcmp/db/<archivo>.dump
sudo systemctl start webcmp webcmp-huey
```

`--clean` borra los objetos antes de recrearlos: sobre una base que ya tiene el
esquema nuevo, sin `--clean` el restore falla a mitad.

`webcmp-deploy --check` avisa si no hay `pg_dump` en el `PATH`. Lo instala
`install.sh` (`postgresql-client`); si se saca a mano, el proximo deploy con
migraciones se niega a correr.

### Gate de migraciones en el merge a `main`

El respaldo es la red; el gate es no necesitarla. El job
**`Migrations safety (dev -> main)`** corre en cada PR cuya base es `main` y
analiza el AST de las migraciones nuevas con
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

### Por que el `check --deploy` del servidor y no solo el del CI

El job `deploy-check` del CI corre el mismo check, pero con valores de mentira:
base de datos que no existe, sin SMTP, `SECRET_KEY` de un par Fernet descartable.
Verifica que **el codigo** no tenga problemas de seguridad.

El gate del servidor corre contra el `.env` de verdad. Es la primera vez que se
comprueba que la `SECRET_KEY` se descifra con la `ENCRYPTION_KEY` de
`/etc/webcmp/encryption.env`, que `DB_*` apunta al PostgreSQL real y que
`EMAIL_BACKEND` no es el de consola (que `production.py` rechaza). Tarda segundos
y evita un sitio caido.

### Por que `restart` y no `reload`

`systemctl reload` (SIGHUP) recarga la configuracion de Gunicorn y los workers se
re-forkean **desde el master**, que tiene el codigo viejo en memoria. Los workers
nuevos tambien tendrian el codigo viejo. El sitio serviria los templates viejos
con los estaticos nuevos: el peor estado posible, uno que no se ve como falla.

### Por que `systemd-run` y no `manage.py` pelado

Los comandos de Django corren en una unidad transitoria con el mismo
`EnvironmentFile` y `PRODUCTION=1` que `webcmp.service`. Un `manage.py` corrido a
mano en una shell sin `PRODUCTION` cae en el perfil de **desarrollo**
(`config/settings/__init__.py` elige perfil por la variable de entorno): migra
con las credenciales del entorno en vez de con las de produccion, y el deploy
"pasa" contra la base equivocada.

## Rollback

Automatico: si algo falla **despues** de `migrate`, el script vuelve al commit
anterior, reinstala dependencias, recoloca estaticos y reinicia los units. Sale
con codigo 1, asi que el panel y `deploy-status.json` lo marcan fallido aunque el
sitio haya quedado sirviendo la version anterior.

La trampa que lo dispara es `trap rollback EXIT`, no `ERR`. No es indistinto:
`trap ... ERR` no corre cuando el script hace `exit`, y `die()` -que es como
termina practicamente todo el deploy, incluido el health check- hace `exit 1`.
Con `ERR`, el rollback era inalcanzable justo en el caso para el que existe.

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

## El servidor en Proxmox (antes de instalar)

Esta seccion termina donde empieza `install.sh`. Todo lo de abajo se hace **una
vez**, en la consola de Proxmox, y no lo automatiza nada de este repo.

### Contenedor LXC

Se despliega en un contenedor LXC sin privilegios, no en una VM. Y conviene decir
por que eso es seguro **en este proyecto concreto**, porque la primera version de
este documento decia lo contrario:

`install.sh` no toca nada del host. No hace `mount`, ni `sysctl`, ni `modprobe`,
ni maneja `apparmor`, ni red con `iptables`. Lo que hace es `apt-get`, crear un
usuario, `python -m venv`, unidades systemd propias, `nginx` y PostgreSQL/Redis
como servicios. Todo eso corre dentro de un LXC con systemd, que es el PID 1 del
contenedor: `systemctl enable --now webcmp` funciona igual que en una VM.

La regla de "nada de systemd en contenedores" aplica a contenedores sin init
propio (imagenes de Docker hechas para correr un solo proceso). Proxmox no es
ese caso: entrega un sistema completo y systemd arranca como PID 1.

### Lo unico que si rompe: la version de Python

Este es el requisito que hay que respetar, y no es negociable:

```
MIN_PYTHON=(3 14)
```

`scripts/generate_env.py` usa PEP 758 (`except A, B:`, sin parentesis), que
existe **solo desde Python 3.14**. Con 3.12 o 3.13 el generador no compila y el
error no menciona la version: dice `multiple exception types must be
parenthesized`. Por eso `install.sh` chequea la version al arrancar y aborta con
un mensaje que si la nombra.

Consecuencia practica: **el template tiene que traer Python 3.14**. Ubuntu 24.04
trae 3.12. Comprueba antes de crear nada:

```bash
pveam update
pveam available --section system | grep -i 'ubuntu-26'
```

Si no aparece un template de Ubuntu 26.04 (o el que corresponda con 3.14), las
opciones son agregar el PPA de deadsnakes al contenedor o compilar Python a mano.
Las dos funcionan, las dos son trabajo extra que se evita eligiendo bien el
template.

Por la misma razon los workflows de CI fijan `runs-on: ubuntu-26.04`. Correr
`check --deploy` y los tests en otra imagen valida un sistema que no es el que
sirve el sitio, y el verde no dice nada sobre el servidor real.

### Crear el contenedor

Ajusta storage (`local-zfs`), red y el nombre del template a lo que liste
`pveam available` en tu nodo:

```bash
pct create 120 local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst \
  --hostname webcmp \
  --cores 4 --memory 8192 --swap 2048 \
  --rootfs local-zfs:60 \
  --net0 name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1 \
  --nameserver 1.1.1.1 \
  --unprivileged 1 \
  --onboot 1 \
  --timezone America/Havana

pct start 120
pct exec 120 -- bash -lc 'apt-get update && apt-get -y full-upgrade'
```

Notas sobre esas banderas:

- **`--unprivileged 1`**: no hace falta privilegiado. Nada del despliegue monta
  nada. Efecto lateral: los archivos de `/srv/webcmp` aparecen en el host con
  UID `100000 + uid`, que solo importa si alguien va a leerlos desde el host.
- **`--swap 2048`**: sin esto el contenedor no tiene swap propio y un pico de
  memoria mata un worker de Gunicorn en vez de paginar. El swap del LXC se
  cuenta contra el host, no es gratis.
- **Sin `nesting=1`**: eso es para Docker dentro del contenedor. Aca no hay
  Docker, y activarlo solo amplia la superficie.
- **`--rootfs` en storage con snapshots**: ZFS o LVM-thin. Sobre storage tipo
  `dir`, `pct snapshot` no existe y el paso siguiente se cae del plan.
- **`--memory` es un limite de cgroup**: si PostgreSQL y los workers lo superan,
  el kernel mata procesos *dentro* del contenedor. `free` dentro del contenedor
  muestra la memoria del host, asi que no sirve para diagnosticar — mira
  `pct status 120 --verbose` o el grafico del nodo.

### Dimensiones

Lo que carga el contenedor es PostgreSQL, Redis, Gunicorn (workers) y ~114 MB de
estaticos que `collectstatic` reescribe en cada deploy.

| Recurso | Recomendado | Minimo |
| --- | --- | --- |
| vCPU | 4 | 2 |
| RAM | 8 GB | 4 GB |
| Disco | 60 GB | 30 GB |

Con 2 vCPU el primer `collectstatic` (hashear y comprimir en gzip+brotli) tarda
varios minutos y el deploy se acerca al `TimeoutStartSec=45min` de
`webcmp-autodeploy.service`. No es solo lentitud: es margen que se pierde.

El disco crece con `media/`, los PDFs que sube la gente y los respaldos de
PostgreSQL de `/var/backups/webcmp/db` (10 como maximo, uno por deploy con
migraciones). 60 GB no es capricho: un dump completo de esta base entra sin
problema en ese margen, pero no entra en 30 GB junto con todo lo demas.

### Sistema y red

- **Ubuntu 26.04 LTS** (o el template que traiga Python 3.14), x86_64. El
  instalador asume `apt`.
- **Zona horaria `America/Havana`**, igual que `TIME_ZONE` del proyecto. Si no
  coinciden, las fechas de los avisos y los logs se leen en dos relojes
  distintos.
- **IP fija**, no DHCP. En Proxmox se fija en `--net0 ip=...` o se cambia despues
  con `pct set 120 --net0 ...`. Un servidor de produccion con IP dinamica es un
  incidente esperando su turno.
- **Nombre en DNS** que coincida **exactamente** con tres cosas: el
  `EXTERNAL_HOSTNAME` del `.env`, el `PublicHostname` de `deploy.env` y el
  `server_name` de Nginx. Si difieren, el health check entra con un nombre que
  el sitio no reconoce y responde `DisallowedHost`.

### Snapshot antes del primer `install.sh`

Toma un snapshot del contenedor recien creado y con la red configurada,
**antes** de correr el instalador:

```bash
pct snapshot 120 pre-install
```

Ese primer paso toca apt, crea usuarios, bases y vhosts: si sale mal a medias,
`pct rollback 120 pre-install` cuesta un minuto y volver a mano cuesta una
tarde. Requiere storage con soporte de snapshots (ver arriba); si el tuyo no lo
tiene, el equivalente es `vzdump 120 --mode snapshot --compress zstd` a un
storage de backup.

### Conectividad: salida HTTPS, no SSH entrante

La actualizacion **la tira el servidor**. El boton "Actualizar" del panel escribe
un archivo de peticion, un `.path` de systemd lo recoge y `webcmp-autodeploy`
resuelve `refs/heads/main` con `git ls-remote` contra GitHub. No hay ningun runner
de GitHub conectandose al contenedor, y por eso el proveedor puede prohibir SSH
sin que el deploy deje de funcionar.

Lo que si hace falta:

```bash
# como el usuario deploy, sin credenciales: el repo es publico
sudo -u deploy env GIT_TERMINAL_PROMPT=0 \
    git ls-remote https://github.com/cmp-camaguey/webcmp.git refs/heads/main
```

Si eso devuelve un SHA, el boton funciona. Si no:

| Sintoma | Causa |
| --- | --- |
| `Could not resolve host` | DNS. Sin resolucion no hay `ls-remote` |
| timeout / `Connection refused` | Sin salida al 443. Pedirla al proveedor |
| `Authentication failed` | El repo no es legible sin credenciales: el ejecutor no tiene token |

El servidor solo necesita **salida** a `github.com:443`. Nada de puertos
entrantes, ni el 22 ni ninguno.

Para una instalacion sin SSH hay un paso que el instalador **no** hace, y es a
proposito: apagar sshd. El instalador puede estar corriendo sobre esa misma
sesion, asi que cortarla desde adentro es perder la sesion y quedar sin poder
volver a entrar. Se hace despues, a mano, y solo cuando la consola del proveedor
(Proxmox, `pct enter`) ya esta probada:

```bash
sudo systemctl disable --now ssh
```

### Topologia con Nginx Proxy Manager externo

El caso del proveedor: NPM corre en Docker **en otra maquina** y termina TLS
ahi. `install.sh` lo soporta con `PROXY_MODE=external`, y esa opcion **si
instala Nginx en el LXC**:

```text
Navegador --https--> NPM (otra maquina) --http--> LXC:80 Nginx --> socket unix --> Gunicorn
```

El Nginx del contenedor no es un lujo: es el unico que puede servir `/media/` y
`/static/` desde ese filesystem. Django no sirve media con `DEBUG=False` y
WhiteNoise solo sirve `staticfiles`, asi que sin el Nginx, el logo, el favicon y
cada PDF subido dan 404 mientras el resto del sitio "funciona". Gunicorn sigue
escuchando **solo** en el socket unix: no se expone en la red en ninguna de las
dos topologias.

Lo que se configura en NPM:

| Campo | Valor |
| --- | --- |
| Scheme | `http` (NPM habla HTTP con el LXC; el TLS lo pone el) |
| Forward Hostname / IP | IP de LAN del LXC |
| Forward Port | puerto HTTP del instalador (80 por defecto) |
| Websockets Support | on (el grafico de sounding las usa) |
| SSL | Let's Encrypt en NPM; el dominio tiene que resolver a NPM |

No hacen falta Custom Locations ni headers extra: el `X-Forwarded-Proto` que
manda NPM lo **propaga** el vhost HTTP (`map`), no lo reescribe con `$scheme`.
Si lo reescribiera, Django no veria la peticion como segura y el login entraria
en un bucle de logout sin un solo error en el log.

El puerto HTTP del LXC tiene que quedar alcanzable **solo** desde la maquina del
NPM. El instalador no toca el firewall y lo imprime como advertencia:

```bash
sudo ufw allow from <IP_DEL_NPM> to any port 80 proto tcp
sudo ufw deny 80/tcp
```

El health check del deploy atraviesa ese Nginx por HTTP (`HEALTHCHECK_URL` +
`HEALTHCHECK_RESOLVE` a `127.0.0.1:$HTTP_PORT`), asi que un vhost roto o un
`/media/` sin servir no quedan en verde.

### Probar el sitio antes de que exista el NPM

Con `external`, este Nginx escucha HTTP y nada mas, y `production.py` deja
`SESSION_COOKIE_SECURE` y `CSRF_COOKIE_SECURE` en `True`. El navegador **no
guarda** una cookie `Secure` que llega por `http://`: el sitio abre, pero el
login devuelve el formulario vacio, sin un solo error en pantalla ni en el log.
Parece que el deploy esta roto y no lo esta.

Dos formas de validar el login sin el NPM:

1. Instalar con `nginx-local` + TLS autofirmado, probar el login completo, y
   despues reinstalar **de cero** con `external`. No re-correr `install.sh`
   sobre la misma instalacion: borra `.env` y `encryption.env`, rota
   `SECRET_KEY` y la clave de cifrado.
2. Dejar el login sin verificar hasta que el NPM apunte al contenedor. Todo lo
   demas se valida igual, y no necesita el NPM:

```bash
systemctl status webcmp webcmp-huey --no-pager

# El Host importa: ALLOWED_HOSTS lleva solo el dominio, asi que un pedido con
# Host: 127.0.0.1 lo rechaza Django con 400 y Nginx no registra nada. Es el
# mismo motivo por el que el health check usa --resolve.
H='Host: www.meteocamaguey.cu'

curl -sI -H "$H" http://127.0.0.1/ | head -1
curl -s -o /dev/null -w '%{http_code}\n' -H "$H" http://127.0.0.1/api/doc/
# Los estaticos los sirve Nginx, no Django: aca el Host da igual.
curl -s -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1/static/dist/css/tabler.min.css
```

`/api/doc/` tiene que dar `200` y `/` un `200` o `302`. Un `400` en los dos es
`ALLOWED_HOSTS`, no un deploy roto.

### Checklist antes de instalar

- [ ] Contenedor LXC creado (sin privilegios, sin `nesting`), x86_64
- [ ] Template con **Python 3.14** verificado (`install.sh` aborta si no)
- [ ] 2 vCPU / 4 GB / 30 GB como piso; 4/8/60 si el portal va a tener trafico real
- [ ] `--swap` asignado (sin swap, un pico mata un worker)
- [ ] Rootfs en storage con snapshots (ZFS / LVM-thin) o backup tomado
- [ ] IP fija
- [ ] Nombre en DNS resoluble desde donde se va a probar
- [ ] Zona horaria `America/Havana`
- [ ] Snapshot `pre-install` tomado
- [ ] Puerto 22 alcanzable desde donde se va a correr el deploy (o decision explicita de desplegar a mano)
- [ ] Topologia de proxy decidida: TLS en Nginx local (`nginx-local`) o en un NPM externo (`external`)
- [ ] Con NPM externo: IP o subred del NPM, para limitar el firewall del puerto HTTP del LXC

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

Por eso se descarga y se ejecuta en vez de encadenar `curl ... | sudo bash -s`:
en el pipe el `stdin` del script es la tuberia, y el guard de `-t 0` lo rechaza
antes de tocar nada. El pipe queda reservado para `--help` (sale antes del
guard) y para `--non-interactive`, donde no hay preguntas que responder.

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
| `--skip-deploy-scaffold` | No crea el usuario de despliegue, su keypair ni el sudoers: para servidores donde el deploy se corre a mano |

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
`webcmp-autodeploy` con su `.path` de systemd. **No se genera ninguna clave SSH**:
la actualizacion es pull, asi que no hay clave privada que sacar del servidor ni
secret que cargar en GitHub. Ver **El boton "Actualizar"**.

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
#    existe para correr Gunicorn, no para que alguien se loguee. Sin clave SSH:
#    nadie entra con este usuario, se usa para correr el ejecutor del boton.
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

El paso 6 es el ejecutor del boton (`webcmp-autodeploy` y su `.path`), que es
[`setup_autodeploy()`](../deploy/install.sh) y no vale la pena copiar a mano: son
dos unidades de systemd, el directorio de estado con setgid y el script que
resuelve el SHA. Si se dejo `--skip-deploy-scaffold`, el deploy sigue disponible
a mano con `webcmp-deploy`.

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

### Prerrequisitos de sistema que el README del proyecto no lista

Probado sobre Ubuntu 26.04 / Python 3.14. `install.sh` instala todo esto por apt
en el preflight, asi que en el camino automatico no hay que hacerlo a mano; la
tabla importa para el camino manual y para saber que se puede quitar sin
preguntar. Lo que hay en el README del proyecto
(`libcairo2-dev pkg-config python3-dev wkhtmltopdf`) no alcanza: `pip install`
falla y `manage.py` no llega a importar.

| Paquete | Por que |
| --- | --- |
| `build-essential` | En Python 3.14 no hay ruedas para varios pins (`pycairo` entre otros) y hay que compilar. Sin el, pip muere con `Running cc --version gave "No such file or directory: 'cc'"` |
| `libpango1.0-dev` (ojo: el nombre en Ubuntu 26.04 **no** es `libpango-1.0-dev`) | WeasyPrint lo necesita para renderizar PDF. Sin el, `manage.py check` muere con `ffi.dlopen('libpango-1.0.so.0')` |
| `redis-server` | El `.env` generado trae `USE_REDIS_CACHE=True` aunque el README diga que Redis es opcional. O se instala Redis o se pone `False` |
| `postgresql-client` | `pg_dump` para el respaldo previo a migrar. Sin el, un deploy con migraciones pendientes se niega a correr |
| `libcairo2-dev pkg-config python3-dev` | Los que si lista el README, para `pycairo` |

### Un paso del README del proyecto que no funciona como esta escrito

Esto ya no aplica al camino automatico: `install.sh` genera el `.env` como root
y devuelve el archivo a `webcmp`. Queda documentado para el camino manual. El
README del proyecto dice generar el entorno asi:

```
sudo -u webcmp .venv/bin/python scripts/generate_env.py --production
```

Eso no puede escribir `/etc/webcmp/encryption.env`, que es root con modo 600, y
el script falla sin escribir nada (falla cerrado, lo cual esta bien). Lo que
funciona es generarlo **como root** y despues devolverle el `.env` a `webcmp`,
porque Django corre como ese usuario y tiene que poder leerlo:

```bash
sudo install -d -m 0755 -o root -g root /etc/webcmp
sudo .venv/bin/python scripts/generate_env.py --production
sudo chown webcmp:webcmp /srv/webcmp/.env     # Django corre como webcmp
sudo chmod 600 /srv/webcmp/.env
```

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
> `sudo systemctl stop supervisor` y segui los pasos 3 a 6 del README. Nginx tiene
> que apuntar a `unix:/run/webcmp/gunicorn.sock`, no a `/tmp/gunicorn-webcmp.sock`.

## Secrets de GitHub

**No hay ninguno que cargar.** El deploy no se autentica contra GitHub para
desplegar: `webcmp-autodeploy` resuelve `refs/heads/main` con `git ls-remote`
sobre el repositorio publico, sin credenciales y sin tocar el checkout. Tampoco
existe una clave privada de despliegue en el servidor: el instalador ya no la
genera.

El workflow `.github/workflows/deploy-production.yml` sigue en el repo como
alternativa por SSH, pero esta apagado y **no** es el camino de esta instalacion:

- El job tiene `if: vars.DEPLOY_ENABLED == 'true'`, asi que sin esa variable
  termina en gris (*skipped*) en vez de fallar por secrets que no existen.
- Si algun dia se reactiva, necesita los cuatro secrets de antes (`DEPLOY_HOST`,
  `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`) y conectividad
  entrante al 22. Con un proveedor que prohibe SSH, no se activa: se deja sin la
  variable.

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

Vive aparte del estado de despliegue del panel a proposito. Ese JSON
(`dashboard:update_status`) lee archivos y compara commits con el remoto: es
caro por diseno y solo lo pide el panel de administracion. El contador es lo
unico que podra consultar la campana del portal publico cuando lleguen los
clientes (cambio 030), y un `COUNT` indexado no puede viajar pegado a esa carga.

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


## La alternativa por SSH (no se usa aca)

El workflow `.github/workflows/deploy-production.yml` hace `ssh` al puerto 22 y
solo sirve si el servidor acepta conexiones entrantes. Con un proveedor que
prohibe SSH no se activa: se deja `DEPLOY_ENABLED` sin definir y el job termina
en gris.

Si algun dia se necesita, conviene saber que un runner hospedado de GitHub no
llega a una red interna: atras de un firewall que solo deja entrar por VPN o por
un bastion, el deploy automatico no llega. Opciones, de peor a mejor:

1. Correr el deploy a mano (el script esta ahi para eso).
2. Self-hosted runner en la red interna.
3. Abrir el 22 restringido a las IP de GitHub.

Por eso este proyecto usa el boton del panel: no depende de nada de eso.

## Errores que solo aparecen en produccion

No los arregla este trabajo, pero conviene tenerlos a mano: son los que hacen que
un `collectstatic` o un `manage.py check` en verde no digan nada del sitio real.
Todos se reprodujeron en el servidor de prueba.

| Sintoma | Causa |
| --- | --- |
| `ValueError: Missing staticfiles manifest entry for ''`, 500 en **cada** pagina | Las plantillas hacian `{% static '' %}ruta`, concatenando `STATIC_URL` a mano. Con el storage de manifest hay que pasar la ruta al tag. Ver el commit que lo arregla |
| `DisallowedHost: Invalid HTTP_HOST header: 'webcmp'` en cada peticion | En Nginx, `proxy_set_header` se hereda del `server` **solo si el `location` no declara ninguno**. `location /` declara dos para el websocket, asi que el `Host` se perdia y volvia al default `$proxy_host`, que es el nombre del upstream |
| Un 400 que se convierte en 500 | Los handlers de error renderizan plantillas, y el context processor leia `request.user` sin `getattr`. Ese atributo lo pone `AuthenticationMiddleware`, que corre despues de `CommonMiddleware`: si la excepcion salta antes, no existe |
| 502 en todo, con `connect() failed (13: Permission denied)` en el log de Nginx | `www-data` no estaba en el grupo `webcmp`, y el socket sale `0770 webcmp:webcmp` |
| El sitio se cae cada 60 s y vuelve solo | `WatchdogSec=60` en el unit, pero Gunicorn solo manda `READY=1`, nunca `WATCHDOG=1`. systemd esperaba un ping que no llega y mandaba `SIGABRT` |
| `collectstatic` falla con `Post-processing 'x.js' failed!` | El `.min.js` termina en `//# sourceMappingURL=x.js.map` y el `.map` no esta en el repo. El storage de manifest tiene que resolver cada referencia |
| `[ERROR] Control server error: Permission denied: '/home/webcmp'` en cada arranque | El control socket de Gunicorn 26 cae a `$HOME/.gunicorn/`, y `webcmp` no tiene home con `ProtectHome=true` |
| `NodeNotFoundError` al correr `migrate` | Habia una migracion versionada (`commercial/0006`) que dependia de un padre nunca commiteado. Se elimino; desde el cambio 031 el gate `makemigrations --check --dry-run` del CI detecta esa cadena rota antes del merge |
| `the cache has been disabled` en cada deploy | `sudo` aplica `env_reset` y descarta `PIP_CACHE_DIR` del entorno del llamador. Hay que pasarla con `sudo -u user env PIP_CACHE_DIR=... cmd` |

Dos de estos se detectan antes de tocar produccion y conviene que sigan asi:

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
sudo -u webcmp systemd-run --quiet --pipe --wait \
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

Si el paso 3 falla, el problema es el `.env` o `encryption.env`, no el deploy.
