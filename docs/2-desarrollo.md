# 2 · Desarrollo

El día a día: qué perfil de configuración corre, cómo se generan las claves, cómo se
corren los tests y cómo se comprueba que un correo salió de verdad.

## Perfiles de configuración

`config/settings/` es un paquete con perfiles. El dispatcher
(`config/settings/__init__.py`) elige uno antes de importar nada:

| Condición | Perfil | Para qué |
| --- | --- | --- |
| `PRODUCTION` en el entorno | `production` | Servidor |
| `DEBUG=True` | `dev` | Desarrollo con `.env` |
| sin `.env` | `testing` | Suite de tests |

`PRODUCTION` gana sobre `DEBUG`. Los tres perfiles importan `base.py` y solo declaran
sus diferencias.

## Claves y `.env`

El generador de claves es un script suelto: `manage.py` importa los settings antes
de despachar el comando y no puede arrancar sin `.env`.

```bash
python scripts/generate_env.py --development                 # PostgreSQL local, correo a consola
python scripts/generate_env.py --production                  # PostgreSQL, SMTP, fail-closed
python scripts/generate_env.py --production --rotate-keys    # invalida las sesiones
```

`base.load_secret_key()` levanta `ImproperlyConfigured` si no hay material
descifrable: no hay fallback a una clave aleatoria. El perfil `testing` inyecta un
par determinista, así que la suite no necesita `.env` para las claves. La base sí
la necesita: el CI le pasa `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASS` y
`DB_HOST` como variables de entorno, contra el servicio `postgres` del workflow.

`make env` genera el `.env` solo si falta; `make env-force` lo regenera conservando
el par de claves.

### La `ENCRYPTION_KEY` no va en el `.env`

El `.env` guarda la `SECRET_KEY` cifrada con `ENCRYPTION_KEY`. En producción la
clave de descifrado va aparte, en `/etc/webcmp/encryption.env` (`600`, root), y
systemd la carga con `EnvironmentFile=-`. Un `.env` filtrado no entrega nada por sí
solo.

## Tests

```bash
python manage.py test                    # suite completa
python manage.py test apps.meteo         # una app
python manage.py test apps.meteo.tests.test_forecasts
```

`make test` corre la suite completa más los tests de los scripts de deploy
(`deploy/tests/`). `make test-deploy` corre solo esos últimos.

Qué corre el CI en cada PR y qué cambios fuerzan la suite completa está en
[AGENTS.md](../AGENTS.md#ci).

Antes de abrir un PR:

```bash
python manage.py check && python manage.py test
```

Los hooks de pre-commit (`ruff`, `djlint`, `detect-secrets`) corren solos en cada
commit. Cómo se activan y por qué `pre-commit install` y `core.hooksPath` no se
combinan está en [AGENTS.md](../AGENTS.md#pre-commit).

## Worker y correos

El flujo de factura (PDF + correo) depende de tres cosas que fallan en silencio: el
binario `wkhtmltopdf`, el worker corriendo y el backend de correo. Si falta alguna,
Huey reintenta y la factura queda con `email_sent=True` sin haber salido.

```bash
./run_huey.sh     # el worker, en su propia terminal
```

`send_test_invoice` valida esas dependencias antes de tocar la base:

| Comando | Qué hace |
| --- | --- |
| `python manage.py send_test_invoice --demo --escribir-en-dev` | Crea cliente, factura e item de prueba y ejecuta la tarea en este proceso |
| `python manage.py send_test_invoice --demo --escribir-en-dev --encolar` | Deja la tarea en la cola para que la tome `./run_huey.sh` |
| `python manage.py send_test_invoice <uuid>` | Procesa una factura que ya existe |
| `python manage.py send_test_invoice --purge-demo` | Borra lo que dejaron las corridas con `--demo` |

`--demo` escribe en la base de verdad: sin `--escribir-en-dev` se niega a correr.
`--encolar` deja trabajo pendiente en la cola compartida, así que se usa solo para
probar el camino del worker.

`wkhtmltopdf` no se instala con `pip`: es una dependencia del sistema. `pdfkit` es
solo un wrapper; sin el binario, la tarea muere *antes* de enviar el correo.

### Ver el correo, no un "enviado" falso

El backend por defecto en desarrollo es `console`: imprime el mensaje y devuelve
éxito igual, así que `email_sent=True` no significa que el correo haya salido. Para
verlo de verdad, poné en tu `.env`:

```dotenv
EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend
```

Cada correo queda como archivo en `tmp/emails/` (ignorado por git, porque lleva
datos de clientes). Para cambiar la ruta: `EMAIL_FILE_PATH=...`.

Con SMTP real usá las variables de producción y el `EMAIL_BACKEND` de Django que
corresponda. `dev` acepta cualquiera; `production` rechaza `console`, `filebased` y
`locmem` (los tres aceptan el mensaje y lo descartan) y pide regenerar el `.env` con
`python scripts/generate_env.py --production`.
