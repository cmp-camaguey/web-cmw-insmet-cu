#!/usr/bin/env python3
"""Bloquea en el PR a `main` las migraciones que puedan perder datos.

Por qué un script y no un `grep`
--------------------------------
`migrate` corre solo en produccion: lo dispara el boton "Actualizar" del panel, sin
nadie mirando, despues de un `git checkout`. Y el rollback del deploy revierte el
codigo, NO el esquema: Django no genera migraciones inversas, asi que un
`DROP COLUMN` aplicado no tiene vuelta atras. El unico punto donde todavia hay
una persona en el camino es la promocion `dev -> main`, y ahi no basta con mirar
el diff a ojo: la operacion destructiva esta una linea entre cincuenta.

Por eso el gate es de contenido y no de nombres: se lee el AST de cada migracion
nueva y se clasifican sus operaciones. Un archivo llamado `0007_borrar_campo.py`
con un `AddField` adentro no se bloquea, y uno llamado `0007_ajuste.py` con un
`RemoveField` si.

Historia inmutable, migraciones nuevas revisables
-------------------------------------------------
- Una migracion que YA esta en `main` no se edita, no se borra y no se renombra.
  Editar historia aplicada hace que el servidor que ya la corrio y el que no,
  queden con esquemas distintos y el mismo `django_migrations`. Se corrige hacia
  adelante con otra migracion, no reescribiendo la anterior.
- Una migracion NUEVA puede traer una operacion destructiva, pero tiene que
  declararlo. El escape es un comentario en el propio archivo:

      # data-safety: reviewed: <motivo, en una linea>

  El motivo vacio no cuenta: un marcador sin justificacion es una tecla que se
  aprieta sola. Sin escape, la operacion destructiva bloquea el merge.

- El escape declara una perdida que una persona reviso. Por eso **no** cubre lo
  que el gate no puede verificar: una migracion que no se parsea, una lista
  `operations` que no se encuentra y una operacion desconocida bloquean siempre.
  Ahi no hay una decision que revisar, hay una incognita.

Uso
---
    python scripts/check_migrations.py --base origin/main --head HEAD

Sale 0 si no hay bloqueos, 1 si hay alguno y 2 si no pudo analizar (por ejemplo
un clon shallow sin merge-base). El 2 tambien es rojo a proposito: un gate que no
puede comparar no puede aprobar.

Solo stdlib: no importa Django ni necesita el venv. Se ejecuta antes de instalar
dependencias y su resultado no puede depender de que `settings` importe bien.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Operaciones que pierden datos o ejecutan codigo que no se puede leer.
#
# RunSQL y RunPython van aca aunque muchas veces sean inocentes (un backfill, un
# CREATE INDEX). Es una linea de escape, no una pared: si el SQL se reviso, se
# declara. Lo que no puede pasar es que un `DELETE FROM` viaje sin que nadie lo
# haya leido, y el AST no distingue el backfill del DELETE.
BLOCKING_OPERATIONS = {
    'DeleteModel',
    'RemoveField',
    'RunPython',
    'RunSQL',
    'SeparateDatabaseAndState',
}

# Operaciones que cambian el esquema pero se pueden revertir con `migrate <app>
# <anterior>`, y que por lo tanto se informan sin bloquear. La lista es explicita
# y no un "todo lo que no sea destructivo": una operacion DESCONOCIDA bloquea.
#
# RenameField esta aca a proposito. `makemigrations` no detecta renombres: un
# campo renombrado aparece como RemoveField + AddField, o sea perdida de datos
# silenciosa. Escribir el RenameField a mano ES la correccion, y bloquearla
# empujaria a la gente de vuelta al par destructivo.
REVIEW_OPERATIONS = {
    'AddConstraint',
    'AddField',
    'AddIndex',
    'AddIndexConcurrently',
    'AlterField',
    'AlterIndexTogether',
    'AlterModelManagers',
    'AlterModelOptions',
    'AlterModelTable',
    'AlterUniqueTogether',
    'CreateExtension',
    'CreateModel',
    'RemoveConstraint',
    'RemoveIndex',
    'RemoveIndexConcurrently',
    'RenameField',
    'RenameModel',
}

ESCAPE_MARKER = '# data-safety: reviewed:'

BLOCK = 'bloquea'
WARN = 'aviso'


@dataclass(frozen=True)
class Finding:
    """Un hallazgo del analisis: severidad, operacion y detalle legible."""

    severity: str
    operation: str
    detail: str


def git(repo_root: Path, *args: str) -> str:
    """Ejecuta git en `repo_root` y devuelve stdout. Falla con exit 2."""
    try:
        proc = subprocess.run(
            ['git', '-C', str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - git ausente es un entorno roto
        raise SystemExit(f'No se pudo ejecutar git: {exc}') from exc
    if proc.returncode != 0:
        detalle = proc.stderr.strip() or proc.stdout.strip()
        raise SystemExit(
            f'git {" ".join(args)} salio con {proc.returncode}: {detalle}\n'
            'Si el error es "no merge base" o "bad revision", el clon es shallow: '
            'hace falta `git fetch --unshallow` (o fetch-depth: 0 en CI).'
        )
    return proc.stdout


def is_migration_path(path: str) -> bool:
    """True si `path` es un archivo de migracion versionado."""
    partes = Path(path)
    if partes.parent.name != 'migrations':
        return False
    return partes.suffix == '.py' and not partes.name.startswith('__')


def changed_entries(repo_root: Path, base: str, head: str) -> list[tuple[str, str, str | None]]:
    """Cambios entre base y head como (estado, ruta_nueva, ruta_vieja|None).

    `...` y no `..`: se compara contra el merge-base, asi que un PR que viene
    atrasado en `main` no arrastra los cambios que ya entraron a `main` mientras
    tanto. Con `..` esos cambios aparecerian como "borrados por el PR".
    """
    bruto = git(repo_root, 'diff', '--name-status', '-z', '--find-renames', f'{base}...{head}')

    campos = bruto.split('\0')
    entradas: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(campos):
        estado = campos[i]
        if not estado:
            i += 1
            continue
        if estado[0] in {'R', 'C'}:
            entradas.append((estado, campos[i + 2], campos[i + 1]))
            i += 3
        else:
            entradas.append((estado, campos[i + 1], None))
            i += 2
    return entradas


def migration_source(repo_root: Path, rev: str, path: str) -> str:
    """Contenido del archivo en esa revision, no del working tree."""
    return git(repo_root, 'show', f'{rev}:{path}')


def escape_reason(source: str) -> str | None:
    """Motivo declarado con el marcador de escape, o None si no hay."""
    for linea in source.splitlines():
        if ESCAPE_MARKER in linea:
            return linea.split(ESCAPE_MARKER, 1)[1].strip()
    return None


def callee_name(call: ast.Call) -> str | None:
    """Nombre de la operacion: `migrations.RemoveField(...)` -> `RemoveField`."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _const(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _texto_de_args(call: ast.Call) -> str:
    """`Modelo.campo` para que el reporte diga QUE se toca.

    `makemigrations` escribe los dos como keyword (`model_name=`, `name=`), no
    posicionales, asi que mirar solo `call.args` deja el mensaje vacio justo en
    el caso mas comun.
    """
    partes = [_const(arg) for arg in call.args[:2]]
    if not any(partes):
        por_nombre = {kw.arg: _const(kw.value) for kw in call.keywords}
        partes = [por_nombre.get('model_name'), por_nombre.get('name')]
    return '.'.join(parte for parte in partes if parte)


def _describir(operation: str, detail: str) -> str:
    return f'{operation}: {detail}' if detail else operation


def _field_no_admite_nulos(call: ast.Call) -> bool:
    """True si el `AddField` agrega una columna NOT NULL sin default.

    Esa columna no se puede agregar a una tabla con filas: PostgreSQL la rechaza y
    el deploy aborta en el `migrate`, ya con el respaldo tomado pero con el sitio
    a medio actualizar. Se avisa antes, en el PR.
    """
    if any(kw.arg in {'default', 'preserve_default'} for kw in call.keywords):
        return False
    campo = next((kw.value for kw in call.keywords if kw.arg == 'field'), None)
    if not isinstance(campo, ast.Call):
        return False
    for kw in campo.keywords:
        if kw.arg == 'null' and isinstance(kw.value, ast.Constant):
            return kw.value.value is False
    # Sin `null=True` explicito el campo es NOT NULL.
    return True


def classify(call: ast.Call) -> Finding:
    """Clasifica una operacion de la lista `operations`."""
    nombre = callee_name(call) or '<anonima>'
    detalle = _texto_de_args(call)

    if nombre in BLOCKING_OPERATIONS:
        motivos = {
            'DeleteModel': 'borra la tabla y todas sus filas',
            'RemoveField': 'borra la columna y sus datos',
            'RunPython': 'ejecuta codigo arbitrario sobre la base',
            'RunSQL': 'ejecuta SQL arbitrario sobre la base',
            'SeparateDatabaseAndState': 'desacopla el estado de Django del SQL real',
        }
        detalle_final = f'{detalle}: {motivos[nombre]}' if detalle else motivos[nombre]
        return Finding(BLOCK, nombre, detalle_final)

    if nombre == 'AddField' and _field_no_admite_nulos(call):
        return Finding(
            WARN,
            nombre,
            f'{detalle}: columna NOT NULL sin default, falla en una tabla con filas'
            if detalle
            else 'columna NOT NULL sin default, falla en una tabla con filas',
        )

    if nombre in REVIEW_OPERATIONS:
        return Finding(WARN, nombre, detalle)

    return Finding(
        BLOCK,
        nombre,
        f'{detalle}: operacion no reconocida por el gate'
        if detalle
        else 'operacion no reconocida por el gate',
    )


def analyze(source: str) -> list[Finding]:
    """Operaciones de la migracion, ya clasificadas.

    Una migracion que no se puede parsear bloquea: no saber que hace no es lo
    mismo que saber que no hace dano.
    """
    try:
        arbol = ast.parse(source)
    except SyntaxError as exc:
        return [Finding(BLOCK, '<sintaxis>', f'no se pudo parsear: {exc.msg} (linea {exc.lineno})')]

    operaciones = _operations_list(arbol)
    if operaciones is None:
        return [Finding(BLOCK, '<estructura>', 'no se encontro `operations = [...]` en Migration')]

    hallazgos: list[Finding] = []
    for elemento in operaciones:
        if not isinstance(elemento, ast.Call):
            hallazgos.append(
                Finding(BLOCK, '<estructura>', 'la lista operations no es de llamadas')
            )
            continue
        hallazgos.append(classify(elemento))
    return hallazgos


def _operations_list(arbol: ast.Module) -> list[ast.expr] | None:
    """La lista literal asignada a `operations` dentro de la clase Migration."""
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.ClassDef) or nodo.name != 'Migration':
            continue
        for sentencia in nodo.body:
            if not isinstance(sentencia, ast.Assign):
                continue
            destinos = [t.id for t in sentencia.targets if isinstance(t, ast.Name)]
            if 'operations' in destinos and isinstance(sentencia.value, ast.List):
                return sentencia.value.elts
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Bloquea migraciones destructivas o historia reescrita en un PR.',
    )
    parser.add_argument(
        '--base',
        default='origin/main',
        help='Revision contra la que se compara (default: origin/main)',
    )
    parser.add_argument('--head', default='HEAD', help='Revision del PR (default: HEAD)')
    parser.add_argument(
        '--repo-root',
        default=str(REPO_ROOT),
        help='Raiz del repo git (default: la del script)',
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        entradas = changed_entries(repo_root, args.base, args.head)
    except SystemExit as exc:
        print(f'check_migrations: {exc}', file=sys.stderr)
        return 2

    print(f'check_migrations: {args.base}...{args.head}')

    bloqueos = 0
    bloqueos_escapables = 0
    avisos = 0
    revisadas = 0

    for estado, path, viejo in entradas:
        accion = estado[0]

        # Historia ya publicada: se corrige hacia adelante, no se reescribe.
        if viejo is not None and is_migration_path(viejo):
            print(f'\n{path}  (renombrada desde {viejo})')
            print('  BLOQUEA  una migracion ya publicada no se renombra')
            bloqueos += 1
            continue

        if not is_migration_path(path):
            continue

        if accion in {'M', 'D'}:
            print(f'\n{path}  ({"modificada" if accion == "M" else "borrada"})')
            print('  BLOQUEA  una migracion ya publicada no se edita ni se borra')
            print('           se corrige con una migracion nueva')
            bloqueos += 1
            continue

        if accion != 'A':
            continue

        revisadas += 1
        try:
            fuente = migration_source(repo_root, args.head, path)
        except SystemExit as exc:
            print(f'\n{path}')
            print(f'  BLOQUEA  no se pudo leer el archivo: {exc}')
            bloqueos += 1
            continue

        marca = escape_reason(fuente)
        hallazgos = analyze(fuente)
        print(f'\n{path}  (nueva)')

        if marca:
            print(f'  ESCAPE   revisado: {marca}')
        elif marca == '':
            print(f'  {ESCAPE_MARKER} esta vacio: no cuenta como revision')

        for hallazgo in hallazgos:
            texto = _describir(hallazgo.operation, hallazgo.detail)
            if hallazgo.severity == BLOCK:
                # El escape declara una perdida de datos que una persona reviso.
                # Solo aplica a operaciones conocidas: una migracion que no se
                # parsea o una operacion desconocida no son una decision, son
                # una incognita. Taparlas con el marcador convertiria el gate en
                # un tramite.
                if marca and hallazgo.operation in BLOCKING_OPERATIONS:
                    print(f'  AVISO    {texto} (declarado)')
                    avisos += 1
                else:
                    print(f'  BLOQUEA  {texto}')
                    if marca:
                        print('           el escape no cubre lo que el gate no puede verificar')
                    bloqueos += 1
                    if hallazgo.operation in BLOCKING_OPERATIONS:
                        bloqueos_escapables += 1
            else:
                print(f'  AVISO    {texto}')
                avisos += 1

    print()
    if not revisadas and not bloqueos:
        print('check_migrations: el PR no toca migraciones.')
    print(
        f'check_migrations: {bloqueos} bloqueo(s), {avisos} aviso(s), '
        f'{revisadas} migracion(es) nueva(s) revisada(s).'
    )

    if bloqueos:
        print()
        if bloqueos_escapables:
            print('Si la operacion destructiva ya se reviso y la perdida es intencional,')
            print(f'declararlo en la propia migracion:  {ESCAPE_MARKER} <motivo>')
        print('Una migracion ya publicada no se edita ni se borra: se corrige hacia adelante.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
