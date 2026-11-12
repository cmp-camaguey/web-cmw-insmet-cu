# 031-migraciones-versionadas — especificación

## Requirement 1: migraciones versionadas

El repositorio SHALL contener las migraciones canónicas de todas las apps con
modelos, y el `.gitignore` MUST NOT excluirlas.

### Scenario: clone limpio

- **Given** un clone nuevo del repositorio
- **When** se ejecuta `git ls-files '*/migrations/*.py'`
- **Then** aparecen las migraciones de `commercial`, `core`, `meteo`,
  `publications` y `user_auth`, además de los `__init__.py`

### Scenario: esquema reproducible

- **Given** una base de datos vacía
- **When** se ejecuta `python manage.py migrate`
- **Then** el esquema queda completo y `python manage.py migrate --check` sale
  con código 0

## Requirement 2: gate de migraciones faltantes

CI y los dos puntos de deploy SHALL ejecutar
`makemigrations --check --dry-run` antes de `migrate`, y MUST fallar si un
cambio de modelo no trae su migración.

### Scenario: modelo sin migración

- **Given** un cambio en un modelo sin migración generada
- **When** corre el job de CI o el deploy
- **Then** el comando sale distinto de 0 y el proceso se detiene antes de tocar
  la base

### Scenario: árbol consistente

- **Given** el árbol versionado con todas las migraciones
- **When** corre `makemigrations --check --dry-run`
- **Then** imprime «No changes detected» y sale con código 0

## Requirement 3: documentación coherente

Ningún documento del repositorio SHALL afirmar que las migraciones no se
versionan.

### Scenario: búsqueda de la convención vieja

- **Given** el árbol después del cambio
- **When** se busca `no versiona` junto a `migraciones`
- **Then** solo aparecen referencias históricas marcadas como tales, nunca la
  convención vigente
