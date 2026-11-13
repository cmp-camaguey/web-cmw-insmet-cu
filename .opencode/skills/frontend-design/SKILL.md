---
name: frontend-design
description: "Trigger: frontend, diseño visual, UI, identidad, Tabler, componentes. Diseño visual con identidad para vistas y componentes del proyecto."
license: Apache-2.0
metadata:
  author: "yoelvismr"
  version: "1.1"
---

# Skill: frontend-design

## Activation Contract

Cargar al crear o rediseñar vistas, componentes, layouts o cualquier UI del proyecto. Abordar el trabajo como el diseñador principal de un estudio que da a cada cliente una identidad visual inconfundible.

## Hard Rules

- UI exclusivamente con Tabler.io (Bootstrap 5). Prohibido otros frameworks CSS/UI.
- Templates Django con indentación de 2 espacios, formateados con djlint.
- Templates de correo en `*/emails/` NO se reindentan (whitespace-sensitive).
- Iconos meteorológicos: PNGs en `static/dist/img/weather_icon/`.
- Textos de UI en español, registro neutral/profesional.
- Idioma/región del sitio: `es-mx`, `America/Havana`.

## Design Principles

- **El hero es una tesis.** Abrir con lo más característico del sujeto (una cifra, una imagen, un momento interactivo). El número grande con etiqueta pequeña y acento degradado es la respuesta plantilla: usarla solo si es la mejor opción real.
- **La tipografía lleva la personalidad.** Combinar display y body deliberadamente; escala tipográfica clara con pesos, anchos y espaciados intencionales.
- **La estructura es información.** Numeradores, eyebrows, divisores y etiquetas deben codificar algo verdadero del contenido, no decorar. Un marcador 01/02/03 solo tiene sentido si el contenido es realmente una secuencia.
- **Movimiento deliberado.** Un momento orquestado suele rendir más que efectos dispersos; a veces menos es más (el exceso de animación delata diseño generado por IA).
- **Complejidad acorde a la visión.** Direcciones maximalistas exigen ejecución elaborada; las minimalistas exigen precisión en espaciado, tipo y detalle.
- **Un solo riesgo estético justificable**, no riesgos acumulados.

## Process: brainstorm, explore, critique, build, critique again

Trabajar en dos pasadas. Primero un plan corto con un sistema de tokens compacto: paleta (4–6 hex nombrados), tipografía (display con carácter usado con mesura + body complementario + utilidad si hace falta), layout (prosa de una frase + wireframes ASCII) y firma (el elemento único por el que se recordará la página).

Luego revisar el plan contra el brief: si alguna parte lee como el default genérico que producirías para cualquier página similar, revisarla, decir qué cambió y por qué. Solo tras confirmar la unicidad relativa del plan, escribir el código siguiéndolo exactamente.

Al escribir CSS, cuidar las especificidades de selectores (clases que se cancelan entre sí, común con paddings/margins entre secciones). Hacer la mayor parte del planeamiento e iteración en el razonamiento interno; mostrar ideas al usuario cuando haya alta confianza de que le van a gustar.

## Restraint and Self-Critique

Gastar la audacia en un solo lugar: la firma. Mantener lo demás callado y disciplinado; cortar cualquier decoración que no sirva al brief. No tomar riesgo también puede ser un riesgo. Construir hasta un piso de calidad sin anunciarlo: responsive hasta móvil, focus visible en teclado, reduced motion respetado. Criticar el trabajo propio mientras se construye (captura de pantalla si el entorno lo permite). Consejo de Chanel: antes de salir de casa, mirarse al espejo y quitarse un accesorio.

## Writing in Design

Las palabras aparecen por una razón: hacer más fácil de entender y usar. Son material de diseño, no decoración. Escribir del lado del usuario final: nombrar las cosas por lo que la gente controla y reconoce, nunca por cómo está construido el sistema. Voz activa por defecto: "Guardar cambios", no "Enviar". El botón que dice "Publicar" produce un toast que dice "Publicado". Tratar el error y el vacío como momentos de dirección, no de humor: explicar qué falló y cómo arreglarlo, sin disculpas y sin vaguedad. Registro conversacional y afinado: verbos simples, minúsculas, sin relleno, tono acorde a la marca.

## Decision Gates

| Necesidad | Recurso |
|---|---|
| Icono/componente Tabler | Buscar en la documentación Tabler antes de inventar |
| Gráfico meteorológico | Lo entrega la API externa de modelos; `SoundingView` y `SoundingExportView` solo reenvían |
| Página pública | Plantillas en `templates/` con `layouts/` e `includes/` |

## Execution Steps

1. Identificar el layout base y componentes Tabler ya usados en el proyecto.
2. Diseñar con identidad consistente (paleta, tipografía, espaciado del tema actual).
3. Escribir el template Django con 2 espacios.
4. Verificar: `djlint . --reformat --check` y `djlint . --lint` sobre los templates tocados.

## Output Contract

- Template(s) Django con indentación djlint-compatible.
- Cambios coherentes con la identidad visual existente.
- Resultado de la verificación djlint.

## References

- `../../AGENTS.md` — convenciones UI, estáticos y templates.
- `../../../templates/` — layouts e includes existentes.
