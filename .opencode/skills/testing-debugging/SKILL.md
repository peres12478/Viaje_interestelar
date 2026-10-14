---
name: testing-debugging
description: Diagnostica errores de forma reproducible y valida cambios sin asumir resultados.
---

# Skill: pruebas y depuración

## Método de diagnóstico

1. Reproduce el error o identifica con precisión el síntoma informado.
2. Lee el mensaje completo, el traceback y el contexto donde ocurre.
3. Formula una hipótesis comprobable, no una lista de suposiciones.
4. Cambia una cosa cada vez cuando sea posible.
5. Repite la misma comprobación y compara el resultado.
6. Revisa el diff para detectar efectos colaterales.

## Tipos de comprobación

| Tipo | Objetivo | Ejemplo de evidencia |
|---|---|---|
| Sintaxis | Detectar errores de escritura en Python. | Resultado del compilador o del comando de comprobación. |
| Importación | Detectar dependencias ausentes o incompatibles. | Import ejecutado en el entorno correcto. |
| Funcional | Comprobar el comportamiento solicitado. | Entrada, acción y resultado esperado. |
| Regresión | Verificar que no se dañó lo que ya funcionaba. | Prueba anterior ejecutada de nuevo. |
| Recursos | Detectar problemas de cámara, audio o archivos. | Prueba con recurso disponible y ausente. |

## Reglas

- No confundas “no apareció un error” con “se comprobó toda la funcionalidad”.
- No ocultes errores eliminando excepciones sin entender la causa.
- No cambies varias dependencias o componentes a la vez para intentar arreglar un problema.
- No ejecutes comandos destructivos ni elimines archivos para depurar sin autorización.
- No inventes pruebas, resultados, logs o salidas de consola.
- Si el problema no se reproduce, indica el entorno, los pasos intentados y lo que sigue siendo incierto.

## Informe final

Incluye:
- síntoma inicial;
- causa confirmada o hipótesis aún no confirmada;
- archivos modificados;
- comprobaciones ejecutadas y resultados;
- pasos pendientes para validar manualmente.
