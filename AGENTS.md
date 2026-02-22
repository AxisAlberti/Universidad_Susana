# Guía del Repositorio para Agentes

Este repositorio contiene material educativo para módulos de formación profesional. La documentación está dirigida a **alumnado** y debe ser **didáctica, clara y pedagógica**.

## Normas de redacción de apuntes

- Los apuntes deben ser claros, didácticos y orientados al aprendizaje del alumnado.
- Cada vez que se edite un fichero `.md`, se añadirá al final una línea con la fecha de actualización, con el formato: `**Fecha de actualización:** 31/01/2026`.
- Las cuestiones generadas se guardarán en un fichero en la raíz del repositorio. El nombre del fichero debe incluir el nombre del módulo y el nivel de dificultad.
- Siempre se generarán 30 cuestiones de tipo cuestionario en formato GIFT.
- Al finalizar cada generación de cuestiones, se validará el fichero con las reglas incluidas en este AGENTS.md.
- En cuestiones tipo test y de desarrollo, no se incluirán preguntas que obliguen a memorizar datos numéricos concretos (por ejemplo, nº de núcleos, tasas, latencias u otros valores específicos de dispositivos o conceptos).
- En preguntas tipo test y de desarrollo, los enunciados deben empezar por ¿ y terminar en ? y respetar tildes y ortografía correcta.
- Cuando el contenido de origen proceda de una transcripción, los apuntes generados no deben mencionar ni hacer referencia a que provienen de una transcripción; deben redactarse y presentarse como un texto académico/universitario autónomo.

## Estructura de documentos teóricos (teoria/)

Los archivos de teoría (teoria/) deben seguir esta estructura:

```
---
title: "UD X - X.Y Título del tema"
description: Breve descripción
summary: Resumen corto
authors:
    - Eduardo Fdez
date: YYYY-MM-DD
icon: "material/file-document-outline"
permalink: /modulo/unidadX/X.Y
categories:
    - MODULO
tags:
    - Tag1
    - Tag2
---

## X.Y. Título del tema

[Introducción al tema que explique el contexto y objetivo]

### 1. Primer concepto

[Explicación clara del concepto]

#### 1.1. Subconcepto o ejemplo práctico

[Explicación detallada del subconcepto]

### 2. Segundo concepto

[Continuar con estructura similar]
```

Además, este texto representa un patrón a seguir y explica cómo generar la documentación siguiendo este patrón. Es **muy importante** respetar los saltos de línea y el número de espacios de indentación (4 espacios):

Aconsejamos una lista de cosas, deben seguirse para generar documentos claros y didácticos:

- Ser claro y concisos.
  
    - Como es otro bloque de identación, 4 espacios mas. y una linea en blanco antes y despues del bloque identado.
    - La identación será de 4 espacios.
    - Usar listas para organizar ideas, pero no abusar de ellas.
  
        - Como es otro bloque de identación, 4 espacios mas. y una linea en blanco antes y despues del bloque.
        - Asegurarse de que cada punto aporta valor.
        - Dividir el contenido en secciones lógicas.
        
    - Incluir definiciones cuando sea necesario.
    
- Incluir ejemplos visuales.
- Usar subtítulos para organizar la información.

También se pueden incluir listas de numeradas, en este formato y es **importante** respetar los saltos de línea y número de espacios de indentación (4 espacios):

A continuación un listado: 

1. Primer punto importante
2. Segundo punto relevante
   
    - Como es otro bloque de identación, 4 espacios mas. y una linea en blanco antes y despues del bloque identado.
    - Y anidar las viñetas si es necesario
    
3. Tercer punto clave

Se pueden incluir citas en bloque para resaltar definiciones o ideas clave:

> La programación es el proceso de crear un conjunto de instrucciones que le dicen a una computadora cómo

Se pueden incluir bloques de código para ilustrar ejemplos prácticos:

También es importante incluir imágenes o diagramas para ilustrar conceptos complejos.
[Ejemplos si procede]

```
<figure markdown>   
  ![](assets/nombre-imagen.png)   
  <figcaption>Descripción de la imagen</figcaption>   
</figure>
```

## Formato recomendado para apuntes

- Título principal con el nombre de la unidad y tema
- Objetivos de aprendizaje (3-5 puntos)
- Desarrollo del contenido con subsecciones claras
- Ejemplos prácticos o casos reales
- Resumen final en 3-5 ideas clave
- Referencias y enlaces (si aplica)
- Incluye todas las imagenes que consideres oportunas para complementar adecuadamente los apuntes.
- Estas autorizado a descargar todas las imagenes que consideres oportunas.

## 1. Estructura del repositorio

### 1.1. Carpeta `docs/`

Contiene la documentación principal en formato MkDocs Material. Está organizada por módulos:

- **section1**: Organización y Gestión del Aula
- **section2**: Organización Familiar y Acción Tutorial

#### Estructura general por módulo

```
docs/
├── index.md                     # Portada general del sitio
├── section1/                    # Módulo 1
│   ├── index.md                 # Portada del módulo
│   ├── recursos/                # Recursos del módulo
│   ├── u01..u08/                # Unidades didácticas
│   └── A1..A5/                  # Anexos (antiguas u09..u13)
├── section2/                    # Módulo 2
│   ├── index.md
│   ├── recursos/
│   └── u01..u02/
├── section1/slides/             # Slides del módulo (si aplica)
├── section2/slides/             # Slides del módulo (si aplica)
├── assets/                      # Recursos globales (logo, favicon, imágenes)
├── stylesheets/                 # CSS personalizado
├── includes/                    # Snippets y abreviaturas
├── blog.md
├── tags.md
└── about.md
```

#### Estructura típica de una unidad

```
sectionX/uXX/
├── index.md                     # Resumen y acceso a teoría
├── teoria/                      # Contenidos teóricos
│   ├── MODULO-UX.Y.-Tema.md
│   └── assets/                  # Imágenes y multimedia del tema
├── practica/                    # Prácticas (singular en section1)
└── gift/                        # Cuestionarios (GIFT)
└── slides/                      # Slides de la unidad (Markdown)
```

**Nota:** en algunos módulos la carpeta de prácticas se llama `practicas/` (plural).

### 1.2. Carpeta `site/`

Salida generada del sitio MkDocs. No editar manualmente.

## 2. Navegación y visibilidad

- La navegación se define en `mkdocs.yml`.
- **Prácticas y GIFT no aparecen en el menú lateral.**
- Los `.gift` están excluidos del build mediante:
  - `exclude_docs: "**/*.gift"`
- Es obligatorio mantener menús navegables a izquierda (barra lateral) y derecha (tabla de contenidos) en todo el repositorio.

## 3. Convenciones y formatos

### 3.0. Slides (Markdown y HTML)

- Cada módulo y cada unidad/anexo tiene carpeta `slides/`.
  - Módulo: `docs/sectionX/slides/`
  - Unidad: `docs/sectionX/uXX/slides/` o `docs/section1/A#//slides/`
- Las slides se pueden publicar como:
  - **Markdown** (borradores internos)
  - **HTML Reveal.js** para visualización en navegador
- En Markdown, usar separadores `---` por diapositiva y notas con `Note:`.
- En HTML Reveal.js, incluir `<aside class="notes">...</aside>` para notas.
- En HTML Reveal.js, incluir:
  - Logo arriba a la izquierda (usar `docs/assets/logo.png`)
  - Botón de retorno al módulo o repositorio
  - Incluir **imágenes** relevantes del tema.
  - Añadir **texto explicativo** para los puntos clave.
  - Resumir las **ideas clave** del tema en cada slide.
  - Diseño **responsive obligatorio**:
    - Usar imágenes con `max-width`, `max-height` y `object-fit: contain` para adaptarse a distintas resoluciones.
    - Ajustar tipografías con `clamp()` o tamaños escalables para móviles/tablets/escritorio.
    - Incluir `Reveal.initialize` con `width: "100%"`, `height: "100%"`, `margin` y escalado (`minScale`, `maxScale`).
    - Añadir `@media` para reorganizar columnas a una sola en pantallas estrechas.
    - Header adaptable: reducir el logo y apilar el botón de retorno en pantallas pequeñas para liberar espacio.
    - Ajuste dinámico en JS:
        - Calcular variables CSS (`--img-max-h`, `--img-wrap-h`, `--text-scale`) según `window.innerWidth/innerHeight`.
        - Recalcular en `resize`, `fullscreenchange`, `visibilitychange` y `orientationchange`.
        - Forzar `Reveal.configure({ width, height })` y `Reveal.layout()` tras cada recalculo.
        - Usar `requestAnimationFrame` para asegurar el cambio al salir de fullscreen o minimizar.
    - Imagen centrada cuando está sola:
        - Envolver en contenedor `.slide-image` con `display: flex` y `justify-content: center`.
        - Limitar altura con `vh` para evitar desbordes en resoluciones bajas.
    - Texto responsive:
        - Escalar tamaño con CSS variables y límites (ej. 0.78–1.0).
        - Añadir `padding-bottom` y margen inferior para evitar texto pegado al borde.
- Ejemplo básico (Markdown):

```md
---
marp: true
paginate: true
---

# Título

Note: Mensaje para el docente.
```

- Cuando se añadan slides nuevas, enlazarlas desde:
  - `docs/index.md` (sección Slides)
  - `docs/sectionX/index.md` (sección Slides del módulo)
  - `docs/sectionX/uXX/index.md` o `docs/section1/A#/index.md` si aplica
- Si una unidad no tiene presentación, enlazar a:
  - `docs/sectionX/slides/no-disponible.md` (según el módulo)

**Fecha de actualización:** 11/02/2026

### 3.1. Rutas de imágenes

- Usar rutas **relativas** en los `.md`.
- Para imágenes locales, el patrón correcto es:
  - `../assets/...` (desde un archivo de teoría dentro de `teoria/`)

### 3.2. Nomenclatura de anexos

Los anexos se nombran como **A1, A2, A3...**, con un fichero de teoría por anexo.  
Formato recomendado:

- `A#-1-Titulo-del-anexo.md`

### 3.3. Categorías

- Definir categorías según el curso o módulo.

## 4. Recursos y branding

- Favicon: `docs/assets/favicon.ico`
- Logo: `docs/assets/logo.png`
- Logo principal en portada: `docs/index.md` (imagen centrada)

## 5. Comandos útiles

```bash
mkdocs serve
mkdocs build
mkdocs gh-deploy --force
```

Ejemplo básico (Reveal.js HTML):

```html
<section>
  <h2>Título</h2>
  <aside class="notes">Notas para el docente.</aside>
</section>
```

## 6. Publicación

- GitHub Pages publica desde la rama `gh-pages`.
- El despliegue se realiza con `mkdocs gh-deploy --force`.

## 7. Preguntas tipo test con penalización (formato GIFT)

Las preguntas tipo test para cuestionarios se generarán en **formato GIFT** de Moodle, con **una única respuesta correcta y varias incorrectas con penalización**.

### 7.0. Fuentes para generar preguntas

- Las preguntas deben basarse en los apuntes indicados.
- Complementar siempre con información actualizada de Internet.

### 7.1. Estructura básica de la pregunta

Cada pregunta seguirá esta estructura:

```gift
::Texto corto identificador de la pregunta::
Texto de la pregunta, lo más descriptiva posible. Puede plantear una situación práctica
relacionada con el contenido del archivo de teoría al que acompaña. {

=Respuesta correcta #Feedback formativo: explica por qué es correcta.
~%-33.3333%Respuesta incorrecta 1 #Feedback formativo: explica por qué NO es correcta.
~%-33.3333%Respuesta incorrecta 2 #Feedback formativo: explica por qué NO es correcta.
~%-33.3333%Respuesta incorrecta 3 #Feedback formativo: explica por qué NO es correcta.
}
```

### 7.2. Categorías Moodle (GIFT)

- El fichero de categorías está en (si aplica):
  - `scripts/categorias.gift`
- Al generar cuestionarios, la **primera línea** del fichero debe ser la categoría correspondiente:

```gift
$CATEGORY: CURSO/Test/Nombre_de_la_unidad/Basico
```

El nombre de la unidad en la categoría debe estar **normalizado**:

- Sin tildes ni caracteres especiales
- Espacios reemplazados por `_`
- Solo letras, números, `_` y `-`
- En la cabecera de categoría debe incluirse también la expresión `UD` seguida del número de unidad (por ejemplo: `UD01`, `UD02`, etc.).

Ejemplo recomendado:

```gift
$CATEGORY: CURSO/Test/UD01_Fundamentos_didacticos_y_DUA/Basico
```

Para actualizar automáticamente el fichero de categorías tras añadir o renombrar unidades/anexos:

```bash
scripts/update_categories.py
```
### 7.3 Obligatorio cuestiones (GIFT)

- Siempre has de generar minimo 30 cuestiones.

- Evita enunciados del tipo ¿Qué describe mejor... y ¿Qué decisión es adecuada...

- Cualquier caracter usado en el formato GIFT que pueda generar conflicto, como los símbolos de porcentaje (%), tilde (~), igual (=), almohadilla (#), llaves ({, }), o dos puntos (::), deben ser escapados con una barra invertida \ para evitar errores de interpretación.

- En la retroalimentacion de las cuestiones si se usan  símbolos de porcentaje (%), tilde (~), igual (=), almohadilla (#), llaves ({, }), o dos puntos (::), deben ser escapados con una barra invertida \ para evitar errores de interpretación.

- Estas dos reglas de escape deben comprobarse siempre al generar un fichero de cuestiones. Es obligatorio validar que se cumplen.

- El fichero con las cuestiones se almacena en el raiz del repositorio.

- Estas autorizado a buscar informacion en fuentes externas como internet.

### 7.4 Notación de bases en preguntas

- En las cuestiones de numeración, usar subíndice para indicar la base (por ejemplo: 1010₂, 3A₁₆, 725₈, 37₁₀).

### 7.5 Checklist de validación obligatoria (GIFT)

- 30 cuestiones exactamente.
- Primera línea con `$CATEGORY` correcto.
- Una única respuesta correcta y varias incorrectas con penalización.
- Fichero en la raíz del repositorio y nombre con módulo + dificultad.
- Escape de caracteres especiales en enunciados.
- Escape de caracteres especiales en retroalimentación.
- Subíndice para bases en numeración.


## 8. Preguntas tipo ensayo con editor HTML (formato GIFT Moodle)

Las preguntas de **tipo ensayo** con editor HTML en Moodle se representan en GIFT siguiendo este patrón:

```gift
$CATEGORY: RUTA/CATEGORIA

// question: ID_INTERNO  name: TÍTULO_VISIBLE_EN_MOODLE
::TITULO_INTERNO::[html]ENUNCIADO_EN_HTML{}
```

### 8.1 Categoría en cuestiones de ensayo

- Esta regla se aplicará **a partir de ahora** a todas las nuevas cuestiones de **ensayo** (no aplica a tipo test).
- La cabecera `$CATEGORY` debe ser siempre: `CURSO/Desarrollo/Titulo_del_tema`.
- El `Titulo_del_tema` debe corresponder al título del tema sobre el que se hacen las cuestiones.
- Normalizar el título del tema:
  - Sin tildes ni caracteres especiales.
  - Espacios reemplazados por `_`.
  - Solo letras, números, `_` y `-`.

Ejemplo para U10 (Tarjetas Gráficas):

```gift
$CATEGORY: CURSO/Desarrollo/Tarjetas_Graficas
```

## 9. Indicaciones recientes para generación de apuntes (13/02/2026 y 14/02/2026)

Estas reglas se aplican a los nuevos apuntes y a revisiones de temas ya creados:

- En `section1/uXX/teoria/*.md`, los apuntes deben tener nivel **universitario** de forma obligatoria:
  - mayor profundidad conceptual y terminología académica precisa,
  - base normativa, pedagógica y de evidencia científica,
  - conexión entre teoría y aplicación práctica en el aula,
  - análisis crítico y justificación de decisiones didácticas.

- Cuando se solicite ampliar un tema, añadir:
  - nuevos apartados con fundamentación académica,
  - ejemplos de implementación real,
  - referencias actuales y verificables.
- Si el usuario facilita un PDF para generar apuntes, es obligatorio cubrir todos los puntos del PDF en el documento final.
- En apuntes basados en PDF, no se permite una versión resumida que omita bloques: cada apartado relevante del PDF debe aparecer desarrollado en profundidad.
- Si el PDF incluye secciones específicas (por ejemplo, calendario, horarios, ratios, bienestar o familias), deben tratarse explícitamente en los apuntes.

- Está autorizado y recomendado usar **fuentes externas de Internet** para enriquecer contenidos.
- Es obligatorio completar los apuntes con fuentes académicas e institucionales actuales cuando aporten valor formativo.

- Incluir imágenes adicionales cuando aporten valor didáctico.
- Buscar, descargar e incorporar desde Internet todas las imágenes que sean necesarias para complementar adecuadamente los apuntes.
- Priorizar fuentes abiertas y reutilizables (por ejemplo, Wikimedia Commons, organismos oficiales y recursos educativos institucionales).
- Todas las imágenes descargadas deben guardarse en `teoria/assets/` y referenciarse con rutas relativas:
  - `assets/...` (desde el archivo `.md` de `teoria/`).
- No limitarse a una imagen por sección: incluir todas las que hagan falta para mejorar comprensión, claridad y calidad didáctica.
- Si ayuda a clarificar el contenido, incluir tablas comparativas entre conceptos, modelos, metodologías o enfoques.
- Si ayuda al aprendizaje, incluir tablas de vocabulario con términos clave y sus definiciones didácticas.
- Las tablas de **Vocabulario clave** deben situarse al principio del documento de apuntes (tras la introducción y antes del desarrollo principal), para facilitar la comprensión desde el inicio.
- Buscar y descargar diagramas de Internet cuando aporten claridad (mapas conceptuales, esquemas de procesos, marcos visuales, infografías).
- Integrar esos diagramas en los apuntes con explicación pedagógica breve y relación directa con el apartado tratado.
- Si se incluye algún diagrama o imagen explicativa, debe estar en **español**. Si está en otro idioma, no se debe incluir.
- A partir de ahora, las búsquedas de fuentes, imágenes y diagramas deben realizarse **en español** de forma prioritaria.

- Para evitar problemas de renderizado de imágenes:
  - priorizar sintaxis Markdown directa `![texto](ruta)`,
  - evitar depender de bloques `<figure markdown>` si no está confirmada la extensión `md_in_html` en `mkdocs.yml`.

- Si se reporta que "no se ve ninguna imagen", revisar **todo el fichero**:
  - rutas de imágenes,
  - existencia real de archivos en `assets`,
  - sintaxis Markdown/HTML,
  - compilación final con `mkdocs build`.

- Tras editar cualquier `.md`, añadir o actualizar al final:
  - `**Fecha de actualización:** DD/MM/AAAA`

**Fecha de actualización:** 13/02/2026
**Fecha de actualización:** 14/02/2026

**Fecha de actualización:** 14/02/2026

**Fecha de actualización:** 14/02/2026

**Fecha de actualización:** 16/02/2026

**Fecha de actualización:** 16/02/2026

**Fecha de actualización:** 14/02/2026

**Fecha de actualización:** 14/02/2026

**Fecha de actualización:** 14/02/2026
