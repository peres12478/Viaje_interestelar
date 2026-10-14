# Agentes y skills de OpenCode para un proyecto Python

Este paquete toma como referencia de estilo el agente `commit-writer.md` del repositorio de clase: archivos individuales, descripción precisa, reglas explícitas, procedimiento y formato de salida. No copia su contenido ni sus permisos literalmente; cada archivo se adapta a su función.

## Contenido

| Ruta | Función | Permisos / alcance |
|---|---|---|
| `.opencode/agents/arquitecto.md` | Analiza y planifica cambios. | Solo lectura; comandos de inspección limitados. |
| `.opencode/agents/desarrollador-python.md` | Implementa cambios autorizados. | Trabaja en el código del proyecto; evita acciones destructivas o externas. |
| `.opencode/agents/revisor.md` | Busca errores y regresiones. | Solo lectura; inspecciona diffs e historial. |
| `.opencode/skills/python-project/SKILL.md` | Convenciones generales de Python. | Guía reutilizable. |
| `.opencode/skills/opencv-mediapipe/SKILL.md` | Cámara, imágenes y gestos. | Guía especializada. |
| `.opencode/skills/pygame-audio/SKILL.md` | Interfaz y audio. | Guía especializada. |
| `.opencode/skills/testing-debugging/SKILL.md` | Diagnóstico y validación. | Guía especializada. |

## Instalación en Windows

1. Extrae el ZIP.
2. Copia la carpeta `.opencode` dentro de la raíz del proyecto, por ejemplo `C:\\Aulas de ciudad`.
3. Si ya existe una carpeta `.opencode`, combina las carpetas; no borres configuraciones existentes.
4. Comprueba que las rutas finales queden así:

```text
C:\Aulas de ciudad\
└── .opencode\
    ├── agents\
    │   ├── arquitecto.md
    │   ├── desarrollador-python.md
    │   └── revisor.md
    └── skills\
        ├── python-project\SKILL.md
        ├── opencv-mediapipe\SKILL.md
        ├── pygame-audio\SKILL.md
        └── testing-debugging\SKILL.md
```

## Verificación

Desde PowerShell, en la carpeta del proyecto:

```powershell
Get-ChildItem .opencode -Recurse -File | Select-Object FullName
```

Después abre o reinicia OpenCode desde la raíz del proyecto y comprueba si reconoce los agentes y skills. La detección exacta depende de la versión y de la configuración de OpenCode; no se da por confirmada hasta probarla.

## Notas importantes

- Los agentes tienen funciones distintas para evitar que todos hagan lo mismo.
- El arquitecto y el revisor están configurados como solo lectura.
- El desarrollador implementa cambios, pero no debe borrar archivos, hacer commits, publicar código ni cambiar configuraciones globales sin autorización.
- Las skills describen procedimientos; no garantizan por sí solas que una biblioteca esté instalada ni que el modelo local pueda ejecutar todas las herramientas.
- Si OpenCode rechaza alguna clave de configuración o permiso, revisa la sintaxis admitida por la versión instalada y ajusta ese archivo antes de usarlo.
