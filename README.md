# Centro Meteorológico Provincial Camagüey

Sistema web del **Centro Meteorológico Provincial Camagüey** (CMP): portal público
meteorológico, panel administrativo y API REST para terceros.

**Producción:** [https://www.meteocamaguey.cu](https://www.meteocamaguey.cu)

Django 5.2 + Django REST Framework sobre PostgreSQL, interfaz con
[Tabler](https://tabler.io/), Gunicorn detrás de Nginx y un worker de Huey para
correos y PDFs. Interfaz y configuración en español (`es-MX`, `America/Havana`).

## Arranque rápido

Requiere Python 3.14 y PostgreSQL.

```bash
git clone https://github.com/cmp-camaguey/webcmp.git
cd webcmp
make setup                          # venv, dependencias, .env y hooks
# crear la base y el rol: docs/1-instalacion.md#2-base-de-datos
python manage.py migrate
python manage.py add_stations_data
python manage.py createsuperuser
python manage.py runserver
```

En `http://127.0.0.1:8000/`. La base local se crea una sola vez; el paso a paso
está en [Instalación](docs/1-instalacion.md).

## Documentación

| Documento | Para qué |
| --- | --- |
| [Instalación](docs/1-instalacion.md) | De un clon vacío a `runserver`: PostgreSQL, `.env`, dependencias |
| [Desarrollo](docs/2-desarrollo.md) | Perfiles de configuración, tests, worker de Huey y correos |
| [Despliegue](deploy/README-deploy.md) | Servidor, Nginx, deploy automático, respaldo y rollback |
| [AGENTS.md](AGENTS.md) | Arquitectura, convenciones y reglas duras del proyecto |
| [SECURITY.md](SECURITY.md) | Cómo reportar una vulnerabilidad |
| [openspec/specs](openspec/specs) | Comportamiento vigente, por capacidad |

## Contribuir

Rama corta desde `dev` y PR a `dev` con squash merge. Los cambios con impacto se
describen antes en `openspec/changes/<NNN-nombre>/`. El ciclo completo, los
required checks y el criterio de aprobación están en
[AGENTS.md](AGENTS.md#flujo-de-ramas-dev--main).

## Licencia

MIT. Ver [LICENSE](LICENSE).

Centro Meteorológico Provincial Camagüey — Instituto de Meteorología de Cuba.
