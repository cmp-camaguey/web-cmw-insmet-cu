# 025-update-web-deploy — Diseño

## La decisión central: quién ejecuta

Hay tres formas de conectar un botón web con un script privilegiado, y solo una
sobrevive a un compromiso del proceso web:

| Opción | Qué pasa si Django queda comprometido |
|---|---|
| `webcmp` con sudo sobre `webcmp-deploy` | root. Cualquier RCE escala directo. |
| Endpoint privilegiado / API interna | depende del endpoint; si ejecuta comandos, es lo mismo con otro nombre. |
| `webcmp` escribe un archivo; otro usuario lo ejecuta | escribe un archivo. Nada más. |

Se eligió la tercera. `webcmp` no gana una sola capacidad: ya puede escribir en
`/var/lib/webcmp` y ese permiso no sirve para nada más. Quien tiene el poder es
`deploy`, que ya lo tenía antes de este cambio, con la misma regla de sudoers.

## Los archivos que forman el contrato

| Ruta | Escribe | Lee | Modo |
|---|---|---|---|
| `/etc/webcmp/deployed` | `deploy.sh` (root, vía sudo) | Django (`webcmp`) | 0644 |
| `/var/lib/webcmp/deploy-request` | Django (`webcmp`) | `autodeploy.sh` (`deploy`) | 0644, grupo `webcmp` |
| `/var/lib/webcmp/deploy-status.json` | `autodeploy.sh` (`deploy`) | Django (`webcmp`) | 0644, grupo `webcmp` |

`/var/lib/webcmp` es `deploy:webcmp` con modo **2775**. El `1` de más no es
decoración: el bit setgid hace que cualquier archivo creado ahí nazca con grupo
`webcmp`, así que el estado que escribe `deploy` lo puede leer Django sin tener que
ser superusuario ni pertenecer al grupo `deploy`.

## Por qué el SHA no viaja en la petición

El archivo de petición es una entrada **no confiable**: lo escribe otro proceso, y
en el escenario que importa ese proceso está comprometido. Si `autodeploy.sh` lo
usara como destino, un atacante podría pedir el despliegue de cualquier commit del
repositorio — incluida una rama suya con un cambio malicioso — y `webcmp-deploy` lo
aplicaría con root.

Por eso el archivo es solo un timbre. El ejecutor resuelve `refs/heads/main` con
`git ls-remote` y despliega eso. Lo máximo que consigue el atacante es pedir un
despliegue de lo que ya está en `main`, que es exactamente el botón que existe.

El SHA que el panel escribe (`expected_sha`) se conserva por trazabilidad en el
log, y el ejecutor lo ignora a propósito.

## Por qué `O_EXCL`

Un `open(path, 'w')` normal sobrescribe. Dos pestañas con el botón pulsado casi a la
vez producirían dos peticiones y, si la primera ya fue consumida, dos despliegues.
`O_CREAT | O_EXCL` falla con `FileExistsError` en la segunda, y la vista responde
409 sin tocar el archivo que ya está en disco.

El ejecutor **borra** la petición al inicio. Si no lo hiciera, `PathExists=` nunca
volvería a dispararse (solo reacciona a la transición ausente → presente) y, con
`O_EXCL` en el panel, una petición fallida bloquearía todas las siguientes para
siempre. Ese par de decisiones (borrar al consumir, crear con `O_EXCL`) es lo que
hace que un fallo no deje el botón muerto.

## Por qué el estado es JSON atómico

Django lee `deploy-status.json` en cada visita al dashboard. Un archivo a medio
escribir produciría un `JSONDecodeError` intermitente y el aviso parpadearía sin
motivo. `autodeploy.sh` escribe en `mktemp` dentro del mismo directorio y hace
`mv -f`, que es atómico en el mismo sistema de archivos.

Del JSON, el panel **solo** acepta tres campos y descarta el resto. El `sha` se
valida con una expresión regular de 40 hex antes de devolverlo. El archivo lo
escribe otro usuario: no se reenvía al navegador tal cual.

## Máquina de estados del panel

```
running          el ejecutor está desplegando      → punto azul, sin botón
available        deployed != main                  → punto rojo + botón
failed           el último intento falló            → punto rojo, sin botón
ok               el último intento terminó bien     → sin punto, "completado"
up_to_date       deployed == main                  → sin punto
unknown          fallo de red, o sin committed     → sin punto
```

El punto del navbar es la única señal que se ve sin abrir nada; el detalle y el
botón viven dentro del desplegable. Es deliberado: un punto rojo se entiende de un
vistazo y no ocupa espacio en una pantalla que se mira todo el día.

`unknown` cuando no consta qué se desplegó es deliberado: sin ese dato no se puede
afirmar que hay una actualización, y mandar al admin a un despliegue por un motivo
inventado es peor que no decir nada. `up_to_date` tampoco se anuncia: un panel que
dice "todo al día" en cada carga es ruido.

Un `failed` sí se muestra, y ahí no hay botón: reintentar es un clic de más en un
estado que probablemente necesita que alguien mire el log.

### El `running` huérfano

El ejecutor escribe `running` al empezar y sobrescribe el archivo al terminar. Si
systemd lo mata entre esas dos escrituras —OOM, `systemctl stop`, un corte de
energía— el archivo queda en `running` para siempre y el panel muestra
"Desplegando…" sin que nada esté corriendo. El botón no vuelve nunca y no hay error
que lo explique.

Por eso la petición lleva `started_at` y el panel descarta un `running` más viejo
que `RUN_STALE_SECONDS` (una hora). El límite es holgado a propósito:
`TimeoutStartSec=45min` en la unidad es el techo real del ejecutor, así que a la
hora solo puede ser un estado huérfano. El estado se sigue reportando como dato,
pero deja de bloquear la comparación de commits.

## Por qué la unidad es `.path` y no un cron

Un cron cada N minutos despierta aunque no haya nada que hacer y no corre la
petición cuando aparece. `PathExists=` despierta exactamente cuando el archivo
aparece — que es la definición del evento. `systemctl enable --now
webcmp-autodeploy.path` y ya.

El `.service` no se arranca nunca a mano: se dispararía un despliegue que nadie
pidió.

## Qué cambió: los mensajes de los commits

El desplegable decía `a1b2c3d → e4f5g6h`, que no alcanza para decidir. Ahora,
cuando el estado es `available`, el endpoint añade `commits`: hasta 10 entradas
con `sha` corto, `subject` y `author`, leídas de
`GET /repos/<org>/<repo>/compare/{deployed}...{latest}` — la API pública de
GitHub, sin token.

Tres decisiones que sostienen esto:

- **Solo en `available`.** En `up_to_date` no hay rango que comparar, y en
  `unknown` no hay con qué. Una consulta por carga del panel gastaría el límite
  anónimo (60/h) sin mostrar nada.
- **Cacheada aparte, con clave por rango.** `dashboard:update:compare:{A}:{B}`,
  300 s en éxito y 60 s en fallo. La clave lleva los dos SHA: después de
  desplegar, el rango viejo ya no describe nada.
- **Informativa, nunca parte del contrato de estado.** Si la API falla, responde
  403 o 404, `commits` es `null` y `state` sigue siendo `available`. Un fallo de
  la API no puede degradar la única información que decide si actualizar.

El render es con `textContent`, jamás `innerHTML`: el mensaje de un commit lo
escribe cualquiera que pueda pushear a `main`, y el panel es del administrador.
Un test lee el JS y falla si aparece una asignación de `innerHTML` o
`insertAdjacentHTML`.

`dashboard:update_request` llama `build_state(include_commits=False)`: el `POST`
decide con los SHA y no tiene por qué esperar una segunda salida de red.

## Riesgos aceptados

- **El panel puede pedir deploys.** Un superusuario comprometido puede forzar
  deploys repetidos. El piso es `main`, y `deploy.sh` tiene su propio `flock`, así
  que no se solapan.
- **El estado es informativo.** Si `autodeploy.sh` muere antes de escribir
  `failed`, el panel deja de mostrar "Desplegando…" pasada una hora
  (`RUN_STALE_SECONDS`) y vuelve a comparar commits, pero el estado crudo sigue
  ahí. `systemctl status` sigue siendo la fuente de verdad.
- **No hay verificación de salud posterior.** El panel muestra el estado del
  script, no si el sitio quedó arriba. El health check de `deploy.sh` sigue siendo
  el gate real, y un deploy que falla el health check sale con código distinto de
  cero → estado `failed`.
