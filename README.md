# Conversor Multimedia

Aplicación de escritorio **100 % local** para reducir imágenes y comprimir videos con Python, OpenCV y FFmpeg.
No usa navegador, servidor web ni internet: todo se procesa en tu equipo.

## Qué hace

1. Cargas un video, una imagen o varias imágenes a la vez.
2. Eliges cuánto reducir las imágenes (o el perfil de calidad del video) y el formato de salida.
3. Pulsas **CONVERTIR**; el resultado se guarda en `outs/` (en la raíz del proyecto).

Los archivos originales **nunca** se modifican, renombran, mueven ni borran.

## Instalación (Windows 10 / 11)

Requiere Python 3.10 o superior (con Tkinter, incluido en el instalador oficial de python.org). FFmpeg **no** hay que instalarlo aparte: lo trae `imageio-ffmpeg`.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecución

```powershell
python app.py
```

## Formatos

| Tipo | Entrada | Salida |
|------|---------|--------|
| Imagen | `.jpg` `.jpeg` `.png` `.bmp` `.tif` `.tiff` `.webp` | JPG, PNG, WEBP, BMP, TIFF |
| Video | `.mp4` `.avi` `.mov` `.mkv` `.m4v` `.webm` `.mpg` `.mpeg` `.wmv` | MP4 (recomendado), MOV |

- También puedes elegir «Todos los archivos (*.*)»: las imágenes se detectan por su **contenido**, no por la extensión.
  Si OpenCV no puede abrir el archivo, verás un mensaje de error.
- Los formatos de salida de imagen que se muestran son solo los que tu OpenCV puede codificar. Los de video dependen de FFmpeg
  (ver más abajo): siempre H.264 + AAC.
- Que un formato se pueda **leer** no implica que se pueda **escribir**: `.mkv`, `.webm`, `.avi`, `.wmv`, etc. se aceptan como entrada,
  pero el resultado de un video siempre es MP4 o MOV.

## Cómo funciona el porcentaje de reducción

Rango permitido: **1 – 99 %** (se aceptan decimales; no se aceptan 0, negativos, 100 o más, ni letras).

«Reducir un X %» significa que cada lado conserva `(100 − X) %` de su tamaño original:

| Original | Reducción | Salida |
|----------|-----------|--------|
| 1920 × 1080 | 50 % | 960 × 540 |
| 1920 × 1080 | 25 % | 1440 × 810 |
| 1920 × 1080 | 75 % | 480 × 270 |

- Ancho y alto se escalan con el mismo factor: **no hay estirado ni recorte**. La imagen/video completo se conserva.
- Las dimensiones se redondean al entero más cercano.
- Este porcentaje es el de las **imágenes**. Los videos usan perfiles de calidad (ver «Videos»).
- La resolución de salida se muestra y se actualiza en la ventana antes de convertir.

## Carpeta de salida y nombres

Los resultados van a `outs/` (se crea sola si no existe; se ubica a partir de la carpeta del proyecto, no del directorio desde el que ejecutes el comando).

- `video_endoscopia.mp4` → `outs/video_endoscopia_convertido.mp4`
- `herida_paciente.png` → formato JPG → `outs/herida_paciente_convertido.jpg`
- Si el nombre ya existe nunca se sobrescribe: se crea `_convertido_1`, `_convertido_2`, etc.

El botón **ABRIR CARPETA DE SALIDA** (aparece tras una conversión correcta) abre `outs/` en el Explorador de archivos.

## Conversión de varias imágenes (lote)

**CARGAR IMAGEN** permite seleccionar una o varias imágenes a la vez (Ctrl/Shift + clic en el selector de archivos).

| Selección | Resultado |
|-----------|-----------|
| 1 imagen | `outs/foto_convertido.jpg` (sin carpeta con fecha, igual que siempre) |
| 2 o más imágenes | `outs/2026-10-28_08-49-32/foto_convertido.jpg`, una carpeta nueva por lote |

- **Ajustes una sola vez:** el porcentaje y el formato de salida se piden una sola vez y se aplican a todo el lote.
- **Cada imagen por separado:** la reducción se calcula sobre el tamaño de cada imagen, así que un lote puede mezclar
  resoluciones (1920 × 1080 → 960 × 540 y 4000 × 3000 → 2000 × 1500 con un 50 %). Cada una conserva su relación de aspecto.
- **Lista y vista previa:** la ventana muestra «N imágenes seleccionadas», una lista desplazable con los nombres y la vista
  previa de la primera imagen legible (marcada como vista previa del lote).
- **Progreso:** «Procesando imagen 7 de 24 (25%) — Procesando: frame_007.jpg», con barra de progreso (imágenes terminadas / total).
- **Errores:** una imagen dañada o no compatible **no detiene el lote**: se registra como fallida y se continúa con el resto.
  Al final se muestra «Correctas / Fallidas / Carpeta», y el botón **VER ARCHIVOS FALLIDOS** lista cada archivo con su motivo.
- **Cancelar:** el botón **CANCELAR** detiene el lote antes de la siguiente imagen. Las imágenes ya convertidas se conservan.
- **Abrir carpeta:** **ABRIR CARPETA DE SALIDA** abre la carpeta del lote (o `outs/` si fue una sola imagen).
- **Nombres repetidos:** si dos imágenes de carpetas distintas se llaman igual, nada se sobrescribe:
  `imagen_convertido.jpg`, `imagen_convertido_1.jpg`, `imagen_convertido_2.jpg`.

### Carpeta con fecha y hora

- Se crea **solo** cuando se seleccionan 2 o más imágenes; los videos y las imágenes sueltas nunca la usan.
- Formato `YYYY-MM-DD_HH-MM-SS` con la hora local (sin `:`, válido en Windows).
- Si la carpeta ya existe (por ejemplo, dos lotes en el mismo segundo) se crea una variante única: `2026-10-28_08-49-32_1`,
  `_2`, etc. Nunca se reutiliza ni se mezcla con una carpeta existente.
- Si el lote termina sin ninguna imagen convertida (todas fallaron o se canceló antes de la primera), la carpeta vacía se elimina.
- Los originales solo se leen: nunca se modifican, mueven, renombran ni borran.

## Imágenes: detalles

- Se reescalan con `cv2.INTER_AREA` (la mejor opción para reducir).
- Se respeta la orientación EXIF (fotos de celular).
- **Transparencia:** PNG, WEBP y TIFF conservan el canal alfa. Al convertir a **JPG** o **BMP**, que no lo admiten,
  la transparencia se compone sobre **fondo blanco** (la app lo avisa).
- Escala de grises y 16 bits son compatibles (se pasa a 8 bits si el formato de salida no admite 16).
- JPG se guarda con calidad 95 y WEBP con calidad 95 (con pérdida). Para no perder calidad usa PNG o TIFF.

## Videos: perfiles de calidad, H.264 + AAC y FFmpeg

En un video hay **dos decisiones distintas** que la ventana muestra por separado:

- **Resolución** (ancho × alto): cuántos píxeles tiene cada frame.
- **Calidad de compresión** (CRF de H.264): cuánta información se descarta al comprimir. Cuanto **más bajo** el CRF, más calidad y más peso.

Un video puede pesar bastante menos **sin reducir su resolución** (solo con una compresión H.264 más eficiente), y reducir la
resolución a la mitad suele verse pixelado. Por eso el perfil por defecto **conserva la resolución original**.

### Perfiles

| Perfil | Resolución final | CRF | Preset | Audio AAC | Para qué |
|--------|------------------|-----|--------|-----------|----------|
| **Alta calidad** (por defecto) | La original | 20 | `medium` | 160 kbps | Mucha calidad con tamaño razonable |
| Máxima calidad | La original | 18 | `slow` | 192 kbps | Máxima nitidez; archivo más grande y más lento |
| Web optimizado | Máx. 1280×720 (1920×1080 → 1280×720); **nunca se amplía** | 20 | `medium` | 128 kbps | Videos para sitios web |
| Compresión alta | 50 % por lado (1920×1080 → 960×540) | 23 | `medium` | 128 kbps | Mínimo peso. *Puede reducir notablemente la nitidez del video.* |
| Personalizado | Reducción 0–99 % (0 = conservar) | 17–28 | `medium` | 128 kbps | Tú eliges ambos valores |

- Todos usan H.264 (`libx264`), `yuv420p`, AAC y `faststart`.
- **Web optimizado** conserva la relación de aspecto y no recorta: el lado largo máximo es 1280 y el corto 720
  (1280×720 y 854×480 se quedan como están; 2560×1080 → 1280×540; un video vertical 1080×1920 → 720×1280).
- **CRF guía:** `18` muy alta calidad · `20` alta calidad · `21` buena calidad · `23` mayor compresión. Se acepta de 17 a 28;
  por encima de 23 la app avisa que pueden aumentar los artefactos visibles.
- Los lados siempre quedan **pares** (requisito de H.264 con `yuv420p`); si el original tiene un lado impar se baja 1 píxel.
- El porcentaje de reducción de las **imágenes** funciona como se explicó arriba; en video solo se usa en «Personalizado».

### Resultado

Al terminar se muestra, con datos reales del archivo generado:

```
Resolución: 1920×1080 → 1920×1080
Codec: H.264
Calidad: Alta (CRF 20)
Audio: AAC — preservado
Tamaño: 42.6 MB → 14.8 MB
Reducción: 65.3%
```

Si el original ya estaba muy comprimido, el archivo puede pesar **igual o algo más** (la app lo indica como «Aumento»):
una compresión eficiente no puede reducir un video que ya fue comprimido a bitrates bajos sin bajar calidad.
Si lo que buscas es menos peso, usa «Web optimizado» o sube el CRF en «Personalizado».

### Contenedores y códecs

**El video final siempre lo genera FFmpeg**, y es compatible con la web (HTML5 / React / Vite):

| Contenedor | Video | Píxeles | Audio | Otros |
|------------|-------|---------|-------|-------|
| **MP4** (recomendado para web y compatibilidad) | H.264 (`libx264`) | `yuv420p` | AAC | `faststart` |
| MOV | H.264 (igual que MP4) | `yuv420p` | AAC | `faststart` |

- **No** se ofrece AVI: no es un contenedor adecuado para H.264 + AAC ni para web. La app lo indica en pantalla.
- **No** se usa MPEG-4 Parte 2 (`mp4v`) en ningún punto: esos archivos no se reproducen bien en Chrome.
- Con audio en el original: se conserva (se vuelve a codificar a AAC) y sigue sincronizado. Sin audio: el resultado tampoco lo tiene
  y la app lo informa («El archivo original no contiene audio»); no es un error.

### FFmpeg se administra solo (no hace falta instalarlo ni tocar el PATH)

FFmpeg viene con el paquete de Python [`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/), que se instala con
`pip install -r requirements.txt`. Al arrancar la app lo busca así:

1. **FFmpeg administrado** de `imageio-ffmpeg` (lo normal).
2. Si no está, el `ffmpeg` del sistema (`PATH`), siempre que incluya los codificadores `libx264` y `aac`.
3. Si ninguno funciona, la app muestra
   «FFmpeg no está disponible. La conversión de video requiere FFmpeg para generar un archivo compatible y preservar el audio.»
   y **bloquea la conversión de video** (el botón CONVERTIR se desactiva). **Nunca** genera un video sin audio por omisión.
   Las imágenes siguen funcionando. Para repararlo: `pip install -r requirements.txt` dentro del entorno virtual
   (o instala FFmpeg y agrégalo al `PATH`).

Cuando todo está bien verás en verde «FFmpeg disponible · Audio preservado · H.264».

### Cómo se procesa un video

```
video original (imagen + audio) ──► FFmpeg ──► [reescalado Lanczos, solo si hay reducción]
                                               H.264 (CRF) + AAC · yuv420p · faststart
                                                      │
                                    validación del archivo final ──► outs/<nombre>_convertido.mp4
```

Todo ocurre en **una sola pasada de FFmpeg** sobre el archivo original: no hay un video intermedio ni una segunda compresión
(que degradaría la calidad). OpenCV solo se usa para leer los metadatos y la vista previa, y para comprobar el resultado;
no procesa los frames. Si hay que reducir la resolución se usa el filtro `scale=...:flags=lanczos` de FFmpeg (sin estirar ni recortar);
si no, no se toca ningún píxel de la imagen. Solo el archivo final válido llega a `outs/`.

Antes de entregarlo se **valida**: que exista y se abra con OpenCV, la resolución, los FPS, el número de frames, la duración, y
que el video sea H.264, `yuv420p`, y AAC cuando el original tenía audio. Si algo no cumple, se descarta y se muestra un error.

Etapas que muestra la barra de progreso: «Preparando video...» → «Codificando H.264 y audio... 47%» →
«Validando archivo final...» → «Conversión completada correctamente.»

Otros detalles:

- Se conservan los FPS originales. Si el archivo informa un FPS inválido, la app lo rechaza con un mensaje en lugar de adivinar.
- Algunos contenedores no informan el número de frames; en ese caso el progreso indica los frames procesados en vez de un porcentaje.
- La conversión corre en un hilo aparte (la ventana no se congela) y se puede **cancelar**: se termina el proceso de FFmpeg,
  se liberan los recursos y no queda ningún archivo parcial. El original nunca se toca.
- El detalle técnico de FFmpeg solo se guarda en `logs/app.log`; la interfaz muestra mensajes simples.
- Los archivos temporales (el video en construcción y el registro de FFmpeg) se crean en la carpeta temporal del sistema y se eliminan siempre.

## Estructura

```
app.py                      Punto de entrada
requirements.txt
services/
    common.py               Rutas, validación del %, cálculo de tamaño, nombres y carpeta de lote
    image_processor.py      Lectura/reducción/escritura de imágenes y lotes de imágenes (OpenCV)
    video_processor.py      Conversión de video con FFmpeg + validación del resultado (OpenCV solo lee y comprueba)
    video_profiles.py       Perfiles de calidad: resolución final y CRF (Máxima, Alta, Web, Compresión, Personalizado)
    ffmpeg_service.py       FFmpeg: detección, comando H.264/AAC (Lanczos), progreso, sondeo, cancelación
ui/
    main_window.py          Interfaz CustomTkinter (orquesta los servicios)
tests/test_services.py      Pruebas automáticas (unittest)
outs/                       Resultados
logs/app.log                Detalles técnicos de errores (se crea al ejecutar)
```

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

Las pruebas (incluidas las de lotes) generan imágenes y videos sintéticos en carpetas temporales y no tocan `outs/`.
Las pruebas de video usan el FFmpeg real administrado por `imageio-ffmpeg` (generan videos con y sin audio, comprueban códec H.264, `yuv420p`, AAC, `faststart`, resolución, FPS y duración, y los caminos de fallo y cancelación).
