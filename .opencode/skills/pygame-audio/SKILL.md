---
name: pygame-audio
description: Ayuda a mantener la interfaz, el bucle principal, las imágenes y el audio de aplicaciones hechas con Pygame.
---

# Skill: Pygame, interfaz y audio

## Bucle principal

- Procesa los eventos de Pygame en cada iteración para que la ventana responda.
- Gestiona el evento de cierre y termina limpiamente.
- Separa, cuando sea práctico, la lectura de entradas, actualización del estado y dibujo de la pantalla.
- Evita tareas lentas dentro del bucle; el procesamiento pesado de visión artificial puede hacer que la interfaz se congele.
- Mantén el control del ritmo de actualización de forma coherente con el código existente.

## Ventana y dibujo

| Tema | Regla práctica |
|---|---|
| Inicialización | Inicializa los módulos que se necesitan y comprueba errores de carga. |
| Pantalla | Usa el tamaño real de la superficie y evita coordenadas mágicas dispersas. |
| Imágenes | Comprueba rutas y formato; convierte superficies cuando sea apropiado. |
| Texto | Define fuentes y tamaños de manera coherente con la interfaz existente. |
| Eventos | No elimines el procesamiento de eventos mientras agregas nuevas funciones. |
| Cierre | Detén el bucle y libera recursos al salir. |

## Audio

- Inicializa el sistema de audio antes de reproducir sonidos y maneja el caso en que no esté disponible.
- Comprueba que las rutas de los recursos sean válidas.
- Evita reinicializar el mezclador continuamente.
- No reproduzcas el mismo efecto en cada fotograma por accidente; usa una condición o estado que evite repeticiones no deseadas.
- Distingue entre música de fondo y efectos cortos cuando el diseño lo requiera.

## Integración con OpenCV / MediaPipe

- No permitas que una espera larga de la cámara bloquee la interfaz.
- Evita mantener dos bucles principales independientes que compitan por el control.
- Define claramente quién actualiza el estado compartido entre detección e interfaz.
- Haz cambios pequeños y prueba tanto la interacción visual como el cierre de la aplicación.

## Verificación

Prueba, según corresponda:
1. apertura y cierre de ventana;
2. respuesta a eventos;
3. carga de recursos;
4. reproducción de audio;
5. comportamiento cuando falta un recurso;
6. interacción con cámara o detección de gestos.

Registra qué se probó realmente. No afirmes que el audio o la interfaz funcionan si no fue posible ejecutarlos.
