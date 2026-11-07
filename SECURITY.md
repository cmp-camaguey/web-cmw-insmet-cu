# Política de Seguridad

## Versiones soportadas

El proyecto se desarrolla sobre Django 5.2. Solo la versión más reciente de `main` recibe actualizaciones de seguridad activas. La política se aplica tanto al código del repositorio como a las dependencias declaradas en `requirements/`.

| Rama | Soporte |
| --- | --- |
| `main` (última) | ✅ Soporte activo |
| Tags/versiones anteriores | ❌ Sin soporte |

## Reportar una vulnerabilidad

Las vulnerabilidades se gestionan de forma **privada** hasta su resolución. No abras issues públicos con detalles de seguridad.

1. **Reporta a través de GitHub Security Advisories** si es posible:
   `https://github.com/cmp-camaguey/webcmp/security/advisories`
2. O contacta por correo institucional: `admin@cmw.insmet.cu`

### Qué incluir en el reporte

- Tipo de vulnerabilidad (XSS, SQLi, CSRF, exposición de datos, etc.).
- Componente afectado (app, vista, template, endpoint API, dependencia).
- Versión/commit donde se reproduce.
- Pasos de reproducción o prueba de concepto (sin datos reales de producción).
- Impacto esperado.

### Expectativas

- Confirmación de recepción: en un plazo razonable de **5 días hábiles**.
- Actualización de estado: a medida que se investiga y se prepara el fix.
- Divulgación pública coordinada: tras publicar la corrección en `main`.

## Alcance

Se consideran dentro del alcance: el código del repositorio (`apps/`, `config/`), los templates Django, los endpoints de la API REST (`/api/`) y las dependencias de `requirements/`.

**Fuera de alcance:** configuraciones del servidor (Nginx, Gunicorn, Supervisor), credenciales de producción, y vulnerabilidades en infraestructura de terceros (GitHub Actions, hosts).

## Rotación de credenciales FTP (FileObs)

Las credenciales FTP de observaciones (`FTP_OBS_HOST`, `FTP_OBS_USER`, `FTP_OBS_PASS`) estuvieron **versionadas en el historial de git** (en `apps/api/data/FileObs.py`). Ya no viven en el código, pero se consideran **comprometidas** por haber estado expuestas.

Pasos de rotación obligatoria:

1. Cambiar la contraseña en el servidor FTP de observaciones (host definido en `FTP_OBS_HOST`) cuando haya acceso al servidor.
2. Actualizar `FTP_OBS_PASS` (y demás `FTP_OBS_*`) en el `.env` de producción.
3. Reiniciar el worker/consumidor que usa `FileObs` para que tome la nueva configuración.

> No incluir la contraseña actual en documentación versionada ni en commits.

## Buenas prácticas del proyecto

- El CI ejecuta `pip-audit` (vulnerabilidades de dependencias) y `bandit` (SAST estático) en cada push/PR.
- Las dependencias se declaran en `requirements/`, un archivo por entorno: `base.txt` solo contiene dependencias de runtime; `prod.txt` añade el servidor WSGI (gunicorn); `dev.txt` y `test.txt` añaden el tooling de desarrollo. Ninguna dependencia de desarrollo vive en `base.txt` ni en `prod.txt`.
- Dependabot mantiene las dependencias actualizadas con auto-merge solo para updates menores/patch; los majors requieren revisión manual.
