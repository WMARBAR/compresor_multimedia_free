# Conversor Multimedia

Aplicación de escritorio **100 % local** para reducir la resolución de imágenes y videos con Python y OpenCV.
No usa navegador, servidor web ni internet: todo se procesa en tu equipo.

## Qué hace

1. Cargas un video o una imagen.
2. Eliges cuánto reducir sus dimensiones y el formato de salida.
3. Pulsas **CONVERTIR**; el resultado se guarda en `outs/` (en la raíz del proyecto).

Los archivos originales **nunca** se modifican, renombran, mueven ni borran.

## Instalación (Windows 10 / 11)

Requiere Python 3.10 o superior (con Tkinter, incluido en el instalador oficial de python.org).

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
| Video | `.mp4` `.avi` `.mov` `.mkv` `.m4v` `.webm` `.mpg` `.mpeg` `.wmv` | MP4, AVI, MOV |

- También puedes elegir «Todos los archivos (*.*)»: las imágenes se detectan por su **contenido**, no por la extensión.
  Si OpenCV no puede abrir el archivo, verás un mensaje de error.
- La lista de formatos de salida solo muestra los que tu instalación de OpenCV puede codificar de verdad
  (al cargar el archivo se hace una prueba de escritura/lectura). Códecs usados: MP4 → `mp4v`, AVI → `XVID` (o `MJPG`), MOV → `mp4v`.
- Que un formato se pueda **leer** no implica que se pueda **escribir**: `.mkv`, `.webm`, `.wmv`, etc. se aceptan como entrada,
  pero el resultado siempre es MP4, AVI o MOV.

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
- **Video:** muchos códecs exigen dimensiones pares, así que cada lado se ajusta al par inmediato inferior
  (por ejemplo 961 → 960). La interfaz avisa cuando ocurre este ajuste.
- La resolución de salida se muestra y se actualiza en la ventana antes de convertir.

## Carpeta de salida y nombres

Los resultados van a `outs/` (se crea sola si no existe; se ubica a partir de la carpeta del proyecto, no del directorio desde el que ejecutes el comando).

- `video_endoscopia.mp4` → `outs/video_endoscopia_convertido.mp4`
- `herida_paciente.png` → formato JPG → `outs/herida_paciente_convertido.jpg`
- Si el nombre ya existe nunca se sobrescribe: se crea `_convertido_1`, `_convertido_2`, etc.

El botón **ABRIR CARPETA DE SALIDA** (aparece tras una conversión correcta) abre `outs/` en el Explorador de archivos.

## Imágenes: detalles

- Se reescalan con `cv2.INTER_AREA` (la mejor opción para reducir).
- Se respeta la orientación EXIF (fotos de celular).
- **Transparencia:** PNG, WEBP y TIFF conservan el canal alfa. Al convertir a **JPG** o **BMP**, que no lo admiten,
  la transparencia se compone sobre **fondo blanco** (la app lo avisa).
- Escala de grises y 16 bits son compatibles (se pasa a 8 bits si el formato de salida no admite 16).
- JPG se guarda con calidad 95 y WEBP con calidad 95 (con pérdida). Para no perder calidad usa PNG o TIFF.

## Videos: detalles y audio (FFmpeg)

OpenCV lee y escribe los frames, pero su `VideoWriter` **no conserva el audio**. Por eso:

- **Con FFmpeg instalado:** el video reducido se une con el audio del original (el audio se recodifica a AAC en MP4/MOV y MP3 en AVI).
- **Sin FFmpeg:** la conversión funciona igual, pero el resultado queda **sin audio** y la app muestra:
  «FFmpeg no está disponible. El video convertido se generará sin audio.»

**FFmpeg no se incluye** con esta aplicación ni se instala con `pip`. La app lo busca automáticamente:
primero en la variable de entorno `FFMPEG_PATH` (ruta al `ffmpeg.exe` o a su carpeta) y luego en el `PATH` del sistema.

### Instalar FFmpeg en Windows

Opción A (winget):

```powershell
winget install Gyan.FFmpeg
```

Opción B (manual): descarga una compilación para Windows desde <https://ffmpeg.org/download.html>,
descomprímela y agrega la carpeta `bin` al `PATH` (o define `FFMPEG_PATH` con la ruta a `ffmpeg.exe`).

Cierra y vuelve a abrir la terminal, comprueba con `ffmpeg -version` y reinicia la aplicación.

Otros detalles de video:

- Se conservan los FPS originales. Si el archivo informa un FPS inválido, la app lo rechaza con un mensaje en lugar de adivinar.
- Algunos contenedores no informan el número de frames; en ese caso se muestra «desconocido» y el progreso indica los frames procesados en vez de un porcentaje.
- La conversión corre en un hilo aparte (la ventana no se congela) y se puede **cancelar**; al cancelar o fallar no queda ningún archivo parcial.
- Los archivos temporales se crean en la carpeta temporal del sistema y se eliminan siempre al terminar.

## Estructura

```
app.py                      Punto de entrada
requirements.txt
services/
    common.py               Rutas, validación del %, cálculo de tamaño, nombres de salida
    image_processor.py      Lectura/reducción/escritura de imágenes (OpenCV)
    video_processor.py      Lectura/reducción/escritura de video (OpenCV)
    ffmpeg_tools.py         Detección de FFmpeg y mezcla del audio original
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

Las pruebas generan imágenes y videos sintéticos en carpetas temporales y no tocan `outs/`.
La prueba de mezcla de audio usa un FFmpeg **simulado**; con FFmpeg real instalado conviene verificar manualmente con un video que tenga audio.
