# Media Compressor — Conversor Multimedia

Utilidad de escritorio **100 % local** para optimizar imágenes y videos: reduce la resolución, comprime y convierte formatos
con Python, OpenCV y FFmpeg, y te dice con datos reales cuánto espacio ahorraste.

- **100 % local y privado:** todo se procesa en tu equipo. La aplicación no abre conexiones de red, no sube archivos a ningún
  servicio y no tiene telemetría (internet solo se usa al instalar las dependencias con `pip`).
- **Sin marcas de agua** ni límites artificiales de tamaño, cantidad o duración.
- **Gratis y de código abierto**, sin cuentas, anuncios ni suscripciones.
- Los archivos originales **nunca** se modifican, renombran, mueven ni borran.

> **Capturas de pantalla:** _(pendiente: agrega aquí tus capturas, p. ej. `docs/screenshot-dashboard.png`)_

## Contenido

- [Instalación y ejecución](#instalación-y-ejecución)
- [La interfaz](#la-interfaz)
- [Formatos](#formatos)
- [Imágenes](#imágenes)
- [Lotes de imágenes](#lotes-de-imágenes)
- [Videos](#videos)
- [Resultados y compresión](#resultados-y-compresión)
- [Salida y nombres de archivo](#salida-y-nombres-de-archivo)
- [Límites conocidos](#límites-conocidos)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Pruebas](#pruebas)

## Instalación y ejecución

Requiere Python 3.10 o superior con Tkinter (incluido en el instalador oficial de python.org). Probado en Windows 11.
FFmpeg **no** hay que instalarlo aparte: lo trae `imageio-ffmpeg`.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Dependencias (`requirements.txt`): `opencv-python`, `numpy`, `Pillow`, `customtkinter` e `imageio-ffmpeg`.
Arrastrar y soltar no necesita ninguna librería adicional.

## La interfaz

Tema oscuro negro + verde Matrix contenido (fuente monoespaciada para datos técnicos). Todo el contenido está en un panel
desplazable, así que la ventana es usable desde 1366×768 hasta 1920×1080 (y más).

| Zona | Qué muestra |
|------|-------------|
| **Cabecera** | Nombre, indicador `● FFMPEG ONLINE` / `✗ FFMPEG OFFLINE` (con versión y origen al pasar el cursor) |
| **Carga** | Botones `[ VIDEO ]` / `[ IMAGEN ]`, zona para soltar archivos y formatos aceptados |
| **SOURCE MEDIA** | Vista previa y metadatos **detectados** del archivo (solo se muestra lo que realmente se pudo leer) |
| **OUTPUT CONFIGURATION** | Perfil o reducción, formato, `ORIGINAL → SALIDA`, calidad, códec, audio y avisos |
| **INICIAR CONVERSIÓN** | Se activa solo cuando los ajustes son válidos |
| **PROCESSING** | Barra de progreso, porcentaje, etapa, tiempo transcurrido y botón CANCELAR |
| **RESULT** | Ficha técnica del video, comparación de imágenes o resumen del lote, con acciones |
| **RECENT CONVERSIONS** | Historial de la sesión actual (solo en memoria; no hay base de datos) |
| **SYSTEM LOG** | Registro breve: verde = normal, amarillo = aviso, rojo = error (siempre con prefijo `>`, `!`, `✗`) |

### Cargar archivos

- **Clic:** `[ VIDEO ]` (un archivo), `[ IMAGEN ]` (uno o varios) o cualquier parte del recuadro de carga (acepta ambos).
- **Arrastrar y soltar** desde el Explorador de Windows sobre la ventana. Se implementa con la API nativa de Windows
  (`ctypes`), sin dependencias. Reglas: varias imágenes → lote; **un solo video a la vez** (si sueltas varios se carga el primero
  y se avisa); no se mezclan imágenes y videos; los archivos no compatibles se ignoran con un aviso.
- Atajos: `Ctrl+O` (elegir archivos), `Ctrl+Enter` (iniciar), `Esc` (cancelar); los botones también responden a `Tab` + `Espacio`/`Enter`.
- La inspección se hace en un hilo aparte: la ventana no se congela con archivos grandes.

### Metadatos detectados

| Video | Imagen |
|-------|--------|
| Archivo, resolución, tamaño, contenedor, códec de video, FPS, duración, frames (solo si el conteo es fiable), audio (códec o `NINGUNO`) | Archivo, resolución, tamaño, formato real (por contenido, no por extensión), canales, profundidad de bits, transparencia |

Los nombres largos se acortan con «…» (conservando la extensión) y muestran el nombre completo al pasar el cursor.

## Formatos

| Tipo | Entrada | Salida |
|------|---------|--------|
| Imagen | `.jpg` `.jpeg` `.png` `.bmp` `.tif` `.tiff` `.webp` | JPG, PNG, WEBP, BMP, TIFF |
| Video | `.mp4` `.avi` `.mov` `.mkv` `.m4v` `.webm` `.mpg` `.mpeg` `.wmv` | MP4 (recomendado), MOV |

- Solo se ofrecen los formatos de imagen que **tu** OpenCV puede codificar.
- Que un formato se pueda **leer** no implica que se pueda **escribir**: el resultado de un video siempre es MP4 o MOV con H.264 + AAC.
- No se ofrece AVI como salida (no es adecuado para H.264 + AAC ni para web) y **nunca** se genera MPEG-4 Parte 2 (`mp4v`).

## Imágenes

**Reducción (1 – 99 %).** «Reducir un X %» significa que cada lado conserva `(100 − X) %` de su tamaño:
1920 × 1080 con 50 % → 960 × 540; con 25 % → 1440 × 810; con 75 % → 480 × 270. Ancho y alto usan el mismo factor
(sin estirar ni recortar) y nunca se amplía. Los porcentajes se escriben o se eligen con los atajos 25 / 50 / 75.

- Se reescalan con `cv2.INTER_AREA` (la mejor opción para reducir) y se respeta la orientación EXIF.
- **Transparencia:** PNG, WEBP y TIFF conservan el canal alfa. JPG y BMP no lo admiten: se compone sobre fondo blanco y la
  interfaz lo avisa **antes** de convertir.
- Escala de grises y 16 bits son compatibles (se pasa a 8 bits si el formato no admite 16).
- **Calidad:** JPG y WEBP se guardan con calidad 95 por defecto. En **MOSTRAR OPCIONES AVANZADAS** puedes elegir 1–100
  (PNG, BMP y TIFF son sin pérdida: la calidad no aplica).
- Avisos: reducción del 50 % o más («La resolución se reducirá significativamente.») y calidad de imagen baja.

### Antes / después

Tras convertir una imagen aparece una comparación **ORIGINAL | OPTIMIZADA | LADO A LADO** con la resolución y el peso de cada
una. Usa miniaturas generadas en un hilo (nunca se carga la imagen completa en la interfaz) y la conversión real es independiente.
En lotes, un selector permite comparar cualquiera de las imágenes convertidas.

## Lotes de imágenes

| Selección | Resultado |
|-----------|-----------|
| 1 imagen | `outs/foto_convertido.jpg` |
| 2 o más imágenes | `outs/2026-10-28_08-49-32/foto_convertido.jpg` (una carpeta nueva por lote) |

- Los ajustes se piden una sola vez y la reducción se calcula **por imagen** (un lote puede mezclar resoluciones).
- Al cargar: `N IMÁGENES`, peso total y lista de nombres. Durante el proceso: `Procesando 7 / 24 — nombre.jpg`.
- Una imagen dañada **no detiene** el lote: se registra como fallida y se continúa. **VER ARCHIVOS FALLIDOS** lista cada una con su motivo.
- Al terminar: procesadas, fallidas, peso original, peso optimizado, ahorro, reducción %, razón de compresión y **tiempo total**.
  Las cifras se calculan solo con las imágenes que sí se convirtieron.
- **CANCELAR** detiene el lote antes de la siguiente imagen y conserva lo ya convertido.
- Si dos imágenes se llaman igual: `foto_convertido.png`, `foto_convertido_1.png`, … (nada se sobrescribe).
- La carpeta con fecha usa la hora local (`YYYY-MM-DD_HH-MM-SS`); si ya existe se crea `_1`, `_2`…; si el lote queda vacío, se elimina.

## Videos

En un video hay **dos decisiones distintas** que la interfaz muestra por separado:

- **Resolución** (ancho × alto) → `ORIGINAL → SALIDA`, por ejemplo `1920 × 1080 → 1280 × 720`, con la escala lineal
  («66.7% resolución lineal · 44.4% de los píxeles») o «100% resolución (se conserva)».
- **Calidad de compresión** (CRF de H.264): cuanto **más bajo**, más calidad y más peso.

Un video puede pesar bastante menos **sin reducir su resolución**. Por eso el perfil por defecto la conserva.

### Perfiles de calidad

| Perfil | Resolución final | CRF | Preset | Audio AAC | Para qué |
|--------|------------------|-----|--------|-----------|----------|
| **Alta calidad** (por defecto) | La original | 20 | `medium` | 160 kbps | Mucha calidad con tamaño razonable |
| Máxima calidad | La original | 18 | `slow` | 192 kbps | Máxima nitidez; archivo más grande y más lento |
| Web optimizado | Máx. 1280×720; **nunca se amplía** | 20 | `medium` | 128 kbps | Videos para sitios web |
| Compresión alta | 50 % por lado | 23 | `medium` | 128 kbps | Mínimo peso. *Puede reducir notablemente la nitidez del video.* |
| Personalizado | Reducción 0–99 % (0 = conservar) | 17–28 | `medium` | 128 kbps | Tú eliges ambos valores |

- Todos usan H.264 (`libx264`), `yuv420p`, AAC y `faststart`. Resolución: reescalado **Lanczos** de FFmpeg, sin estirar ni recortar.
- **Web optimizado** respeta la relación de aspecto (1280×720 y 854×480 se quedan como están; un video vertical 1080×1920 → 720×1280).
- Los lados siempre quedan **pares** (requisito de H.264 con `yuv420p`).
- CRF guía: `18` muy alta calidad · `20` alta calidad · `21` buena calidad · `23` mayor compresión.

### Opciones avanzadas (perfil Personalizado)

El perfil **Personalizado** pide la reducción de resolución y el CRF. **MOSTRAR OPCIONES AVANZADAS** añade el preset de x264
(`veryfast` … `slower`) y un **ancho / alto máximo** opcional (se reduce todo por igual, sin estirar ni ampliar). Los valores
avanzados solo se aplican mientras ese panel está abierto. Los usuarios normales no necesitan tocar nada de esto.

### Avisos de configuración

| Situación | Aviso |
|-----------|-------|
| CRF por encima de 23 | Pueden aumentar los artefactos visibles |
| CRF 27–28 (el máximo es 28) | «Esta configuración puede producir una pérdida considerable de calidad.» |
| Reducción de resolución del 50 % o más | «La resolución se reducirá significativamente.» |
| Perfil Compresión alta | «Puede reducir notablemente la nitidez del video.» |
| Ancho/alto máx. mayores que el original | Se respeta la resolución original (nunca se amplía) |
| Valor inválido | Mensaje en rojo y el botón de conversión se desactiva |

### Audio

- Con audio en el original: se **preserva** (se recodifica a AAC) y se confirma verificando el archivo final.
- Sin audio: no es un error; se muestra `AUDIO: NINGUNO` y el resultado tampoco lo tiene.
- Solo se conservan la primera pista de video y la primera de audio.

### FFmpeg se administra solo

FFmpeg viene con [`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/). Al arrancar la app lo busca así:

1. **FFmpeg administrado** de `imageio-ffmpeg` (lo normal).
2. Si no está, el `ffmpeg` del sistema (`PATH`), siempre que incluya `libx264` y `aac`.
3. Si ninguno funciona: la cabecera muestra `✗ FFMPEG OFFLINE`, el botón de conversión de video se desactiva y se explica cómo
   repararlo (`pip install -r requirements.txt`). **Nunca** se genera un video sin audio por omisión. Las imágenes siguen funcionando.

### Cómo se procesa un video

```
video original (imagen + audio) ──► FFmpeg ──► [reescalado Lanczos, solo si hay reducción]
                                               H.264 (CRF) + AAC · yuv420p · faststart
                                                      │
                                    validación del archivo final ──► outs/<nombre>_convertido.mp4
```

Todo ocurre en **una sola pasada de FFmpeg** sobre el archivo original: no hay un video intermedio ni una segunda compresión
(que degradaría la calidad). OpenCV solo lee metadatos y vista previa, y comprueba el resultado; no procesa los frames.

Antes de dar la conversión por buena se **valida** el archivo final: que exista y se abra, la resolución, los FPS, el número de
frames, la duración, y que sea H.264 `yuv420p` y, si el original tenía audio, AAC. Si algo no cumple, se descarta y se informa.
Nunca aparece «Conversión completa» antes de pasar esta validación.

- La conversión corre en un hilo; **CANCELAR** termina el proceso de FFmpeg, elimina los temporales y no deja archivos parciales.
- Los temporales se crean en la carpeta temporal del sistema y se eliminan siempre. El original nunca se toca.

## Resultados y compresión

Todas las cifras se calculan con los **tamaños reales** de los archivos.

| Indicador | Ejemplo | Significado |
|-----------|---------|-------------|
| ANTES / DESPUÉS | 42.6 MB / 14.8 MB | Peso del original y del resultado |
| AHORRADO | 27.8 MB | Espacio liberado |
| REDUCCIÓN | 65.3% | Espacio ahorrado respecto al original |
| RATIO | 2.88 : 1 | Razón de compresión (original ÷ final). **No es lo mismo** que el porcentaje de reducción |
| TIEMPO | 18.4 s (`00:00:18`) | Duración real del proceso (en lotes: tiempo total) |

La ficha de un **video** muestra además `INPUT` / `OUTPUT` (resolución, códec, audio, tamaño) y resúmenes como
`1920×1080 → 1280×720 · H.264 → H.264 · AAC → AAC`.

**Si el resultado pesa más que el original** la aplicación no lo presenta como éxito: el título cambia a
«EL ARCHIVO ES MÁS GRANDE», aparece «El archivo resultante es 8.4% más grande que el original.», la reducción sale negativa y el
historial lo marca con `⚠`. El archivo **se conserva** (nunca se borra solo); puedes quitarlo con **DESCARTAR RESULTADO**.
Esto ocurre, por ejemplo, con videos que ya estaban muy comprimidos. Para pesar menos, usa «Web optimizado» o sube el CRF.

### Acciones

`ABRIR ARCHIVO`, `ABRIR CARPETA`, `COPIAR RUTA` y `DESCARTAR RESULTADO` (con confirmación; solo borra el archivo generado, jamás
el original). En lotes: `ABRIR CARPETA`, `COPIAR RUTA` y `VER ARCHIVOS FALLIDOS`. Las filas de **RECENT CONVERSIONS** abren la ubicación al hacer clic.

### Errores

Los errores se muestran en una tarjeta («ERROR DE CONVERSIÓN» o «ERROR AL CARGAR ARCHIVO») y en el registro, con mensajes
sencillos, sin trazas de Python ni ventanas emergentes. El detalle técnico (incluida la salida de FFmpeg) va solo a `logs/app.log`.

## Salida y nombres de archivo

Los resultados van a `outs/` (se crea sola; se ubica desde la carpeta del proyecto, no desde donde ejecutes el comando).

- `video_endoscopia.mp4` → `outs/video_endoscopia_convertido.mp4`; `herida.png` → `outs/herida_convertido.jpg`.
- Si el nombre ya existe nunca se sobrescribe: `_convertido_1`, `_convertido_2`, …
- La reserva del nombre es atómica y el video se escribe primero en una carpeta temporal: solo un archivo válido llega a `outs/`.

## Límites conocidos

- **Arrastrar y soltar** solo está disponible en Windows (implementación nativa sin dependencias). En otros sistemas la
  aplicación funciona igual con los botones de selección; no se ha probado fuera de Windows.
- La **estimación del tamaño final** antes de convertir no se ofrece: con H.264 depende del contenido y no puede ser fiable.
- No hay vista previa «antes/después» de video; el video muestra una ficha técnica.
- Salida de video solo MP4/MOV con H.264 + AAC (no HEVC, AV1 ni WebM). Se conservan la primera pista de video y la primera de audio
  (no se copian subtítulos ni capítulos).
- CRF permitido: 17–28. El conteo de frames se omite cuando el contenedor no lo informa de forma fiable.
- Teclado: los botones se alcanzan con `Tab` (se resaltan al recibir el foco) y se activan con `Espacio` o `Enter`, y hay atajos globales
  (`Ctrl+O`, `Ctrl+Enter`, `Esc`). Los menús desplegables y el selector de formato se manejan con el ratón (limitación de CustomTkinter).
- No se admiten formatos como HEIC/RAW.

## Estructura del proyecto

```
app.py                         Punto de entrada y configuración del registro
requirements.txt
services/                      Lógica de procesamiento (sin interfaz)
    common.py                  Rutas, validación del %, tamaños, nombres, carpeta de lote, resultados y formatos de texto
    image_processor.py         Lectura/reducción/escritura de imágenes, calidad, miniaturas y lotes (OpenCV)
    video_processor.py         Conversión de video con FFmpeg + validación del resultado
    video_profiles.py          Perfiles de calidad, CRF, presets y límites de resolución
    ffmpeg_service.py          FFmpeg: detección, comando H.264/AAC, progreso, sondeo, cancelación
    metadata_service.py        Metadatos detectados al cargar y reglas de carga (imágenes o un video)
    compression_stats.py       Antes/después, ahorro, reducción %, razón de compresión y avisos
    history.py                 Historial de la sesión (memoria)
ui/                            Interfaz CustomTkinter
    theme.py                   Colores, fuentes y estilos (único lugar con valores de diseño)
    widgets.py                 Tarjetas, filas de datos, KPI, tooltips y registro tipo terminal
    main_window.py             Ventana principal: orquesta paneles, hilos y eventos
    dropzone.py, source_panel.py, config_panel.py, result_panel.py, history_panel.py
    dnd.py                     Arrastrar y soltar nativo de Windows (ctypes)
tests/
    test_services.py           Pruebas de servicios (imágenes, lotes, FFmpeg, perfiles, estadísticas, metadatos)
    test_ui.py                 Pruebas de interfaz (se omiten si no hay pantalla)
outs/                          Resultados
logs/app.log                   Detalles técnicos (se crea al ejecutar)
```

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Generan imágenes y videos sintéticos en carpetas temporales y **no tocan `outs/`**. Las pruebas de video usan el FFmpeg real
(videos con y sin audio, H.264 `yuv420p` AAC, `faststart`, resolución, FPS, duración, fallos y cancelación sin procesos huérfanos).
Las de interfaz abren la ventana real: perfiles, advertencias, carga, lotes con imágenes dañadas, descartar, cancelar,
FFmpeg ausente y arrastrar y soltar (simulando el mensaje `WM_DROPFILES` de Windows).
