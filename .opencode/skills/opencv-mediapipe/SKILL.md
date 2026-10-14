---
name: opencv-mediapipe
description: Guía el trabajo con captura de cámara, procesamiento de imágenes y detección de manos usando OpenCV y MediaPipe.
---

# Skill: OpenCV y MediaPipe

## Flujo recomendado

1. Inicializa la cámara una sola vez y comprueba que se abrió correctamente.
2. Lee cada fotograma y comprueba que la lectura fue exitosa.
3. Convierte los formatos de color que requiera la API utilizada.
4. Ejecuta el procesamiento/detección sobre el fotograma apropiado.
5. Convierte o dibuja los resultados para mostrarlos.
6. Libera la cámara y cierra las ventanas al terminar, incluso si ocurre un error.

## Puntos técnicos

| Tema | Qué comprobar |
|---|---|
| Captura | Que `VideoCapture` se abrió y que `read()` no devolvió un fotograma inválido. |
| Color | Distinguir BGR de OpenCV y RGB cuando se intercambian imágenes con otras bibliotecas. |
| Tamaño | Verificar alto, ancho y canales antes de indexar o redimensionar. |
| Coordenadas | No confundir coordenadas normalizadas de MediaPipe con píxeles de la imagen. |
| Rendimiento | Evitar convertir, copiar o procesar la misma imagen varias veces sin necesidad. |
| Recursos | Liberar la cámara al cerrar la aplicación o al ocurrir una excepción. |
| Robustez | Manejar cámara no disponible, permisos denegados y fotogramas vacíos. |

## Detección de manos y gestos

- Confirma qué API y versión de MediaPipe utiliza realmente el proyecto; no mezcles ejemplos de APIs diferentes.
- Trata las detecciones como datos que pueden faltar en un fotograma.
- Comprueba que existan manos y puntos de referencia antes de acceder a ellos.
- Para reconocer un gesto, documenta la condición usada y considera variaciones pequeñas entre fotogramas.
- Evita cambiar umbrales o lógica de gestos sin explicar cómo afecta al comportamiento.

## Diagnóstico

Cuando haya un fallo, separa estas posibilidades:
- problema de cámara o permisos;
- incompatibilidad de versiones;
- formato o dimensiones incorrectos;
- error en la lógica de detección;
- rendimiento insuficiente.

No inventes la causa: pide o recoge el error exacto y el contexto necesario. No prometas una tasa de FPS sin medirla.
