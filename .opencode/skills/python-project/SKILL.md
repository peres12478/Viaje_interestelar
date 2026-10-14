---
name: python-project
description: Aplica convenciones prácticas para entender, modificar y mantener el proyecto Python existente.
---

# Skill: Python para este proyecto

## Antes de cambiar código

1. Lee el archivo implicado y sus usos antes de editarlo.
2. Comprueba la estructura real del proyecto y localiza `requirements.txt` si existe.
3. Conserva la organización actual; evita crear capas, clases o módulos solo por estética.
4. Determina cómo se ejecuta la aplicación antes de cambiar el punto de entrada.

## Convenciones

| Tema | Regla |
|---|---|
| Claridad | Usa nombres descriptivos y funciones con una responsabilidad reconocible. |
| Cambios | Haz modificaciones pequeñas y relacionadas con la solicitud. |
| Errores | Maneja errores previsibles con mensajes útiles; no ocultes excepciones con `except` vacío. |
| Rutas | Evita depender de que el programa se ejecute desde una carpeta concreta si el proyecto ya tiene una solución de rutas. |
| Recursos | Cierra cámara, archivos, ventanas y dispositivos de audio cuando corresponda. |
| Dependencias | Actualiza `requirements.txt` solo si realmente se introduce o cambia una dependencia. |
| Compatibilidad | Usa la versión de Python y las versiones de paquetes que el proyecto soporte. |

## Dependencias

- No instales paquetes por iniciativa propia si la tarea no lo requiere.
- Antes de sugerir una versión, comprueba las restricciones existentes y evita afirmar compatibilidad sin evidencia.
- Distingue entre un error del código, un paquete ausente y un problema del entorno.
- No borres ni recrees entornos virtuales sin autorización.

## Validación mínima

- Comprueba sintaxis e imports cuando sea posible.
- Ejecuta las pruebas existentes relacionadas con el cambio.
- Si no existen pruebas, describe una comprobación manual reproducible.
- Informa los comandos exactos ejecutados y sus resultados reales.

## Entrega

Resume qué cambió, por qué era necesario, cómo se comprobó y qué limitaciones permanecen. No afirmes que el proyecto completo funciona si solo se verificó una parte.
