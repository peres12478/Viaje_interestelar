#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MUSEO ESPACIAL — viaje interestelar controlado con gestos de la mano
=====================================================================

Gestos (cámara + MediaPipe):
    MANO ABIERTA ... la nave viaja a la velocidad de la luz (efecto hiperespacio)
    PUÑO CERRADO ... la nave frena, llega al planeta y se muestra su ficha de museo
    MANO ABIERTA ... (de nuevo) despega hacia el siguiente destino
    GESTO "L" ...... (pulgar e índice en ángulo recto, mantenido 2 s) cierra este
                     programa y abre automáticamente agujero_negro.py

Teclado:
    Q / ESC .... salir            H ... mostrar/ocultar esqueleto de la mano
    M .......... silenciar        O / F ... simular mano abierta / puño (sin cámara)
    C .......... volver a usar los gestos reales de la cámara

Estructura del proyecto:
    museo_espacial/
    ├── main.py
    ├── requirements.txt
    └── assets/
        ├── planets/   <- PNG con canal alfa (marte.png, jupiter.png, ...)
        ├── sounds/    <- hyperspace.wav, brake.wav, arrival.wav
        └── models/    <- hand_landmarker.task (se descarga solo la primera vez)

Si faltan imágenes o sonidos, el programa genera versiones de marcador
(procedurales) y las guarda en esas carpetas. Basta con reemplazarlos por tus
propios archivos con el mismo nombre.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
import urllib.request
import wave
from collections import Counter, deque
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Optional

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Falta una dependencia ({exc.name}). Ejecuta:  pip install -r requirements.txt")

# ----------------------------------------------------------------------------
# 1. CONFIGURACIÓN
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
PLANETS_DIR = ASSETS_DIR / "planets"
SOUNDS_DIR = ASSETS_DIR / "sounds"
MODELS_DIR = ASSETS_DIR / "models"

HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/1/hand_landmarker.task")

W, H = 1280, 720                 # resolución interna de la ventana (16:9)


def _camera_from_args(default: int = 0) -> int:
    """Permite heredar la cámara del otro script: python main.py --camera 1"""
    if "--camera" in sys.argv:
        try:
            return int(sys.argv[sys.argv.index("--camera") + 1])
        except (IndexError, ValueError):
            pass
    return default


CAMERA_INDEX = _camera_from_args(0)
WINDOW_NAME = "Museo Espacial"

# Tiempos de la máquina de estados (segundos)
T_BRAKE = 1.9                    # duración del frenado/aproximación
T_DEPART = 1.4                   # duración del despegue
MIN_TRAVEL_BEFORE_BRAKE = 0.8    # evita frenar por un falso positivo al despegar
MIN_ARRIVED_BEFORE_DEPART = 1.5  # tiempo mínimo en órbita antes de poder despegar

SPEED_MAX = 1.7                  # velocidad máxima de las estrellas (unidades z/s)
LIGHT_SPEED_KMS = 299_792        # para mostrar en la telemetría

PLANET_X, PLANET_Y = int(W * 0.30), H // 2 + 8
PLANET_BASE = 540                # tamaño base del sprite en pantalla (px)
CARD_W = 540
CARD_TOP, CARD_MAX_H = 84, 556

GESTURE_OPEN, GESTURE_FIST, GESTURE_L = "open", "fist", "l"

# Salto entre aplicaciones mediante el gesto "L"
NEXT_SCRIPT = "agujero_negro.py"
L_HOLD_SECONDS = 2.0             # tiempo que hay que mantener la "L" para cambiar de programa
L_ANGLE_RANGE = (60.0, 120.0)    # ángulo (grados) permitido entre pulgar e índice

# Colores en BGR (OpenCV)
CYAN = (255, 225, 100)
AMBER = (60, 190, 255)
RED = (90, 90, 255)
GREEN = (140, 255, 150)
WHITE = (245, 245, 245)
TEXT = (235, 230, 225)
TEXT_DIM = (175, 160, 140)
PANEL_BG = (30, 18, 10)
NAVY = (46, 20, 8)


# ----------------------------------------------------------------------------
# 2. DATOS: DESTINOS DEL MUSEO
# ----------------------------------------------------------------------------
@dataclass
class Destino:
    id: str                              # también es el nombre del PNG: assets/planets/<id>.png
    nombre: str
    tipo: str
    subtitulo: str
    datos: list                          # lista de (etiqueta, valor)
    composicion: str
    curiosidades: list
    estilo: str                          # tipo de sprite procedural de respaldo
    color: tuple                         # color de acento (BGR)
    escala: float = 1.0                  # tamaño relativo en pantalla
    seed: int = 0

    @property
    def archivo(self) -> Path:
        return PLANETS_DIR / f"{self.id}.png"


DESTINOS = [
    Destino(
        id="marte", nombre="Marte", tipo="Planeta rocoso", subtitulo="El planeta rojo",
        datos=[("Distancia al Sol", "227,9 millones de km"),
               ("Diámetro", "6.779 km"),
               ("Gravedad", "3,71 m/s² (38 % de la Tierra)"),
               ("Duración del día", "24 h 37 min"),
               ("Duración del año", "687 días terrestres"),
               ("Temperatura media", "-63 °C"),
               ("Lunas", "2 (Fobos y Deimos)")],
        composicion="Corteza de basalto cubierta de polvo rico en óxido de hierro, que le da su color "
                    "rojizo. Atmósfera muy tenue, compuesta en un 95 % por dióxido de carbono.",
        curiosidades=["El Monte Olimpo mide cerca de 22 km: es el volcán más alto del Sistema Solar.",
                      "Valles Marineris se extiende unos 4.000 km, como cruzar Estados Unidos.",
                      "Sus atardeceres son azulados, al revés que en la Tierra."],
        estilo="rocky_red", color=(70, 110, 220), escala=0.85, seed=3),
    Destino(
        id="jupiter", nombre="Júpiter", tipo="Gigante gaseoso", subtitulo="El rey del Sistema Solar",
        datos=[("Distancia al Sol", "778,5 millones de km"),
               ("Diámetro", "142.984 km (ecuatorial)"),
               ("Gravedad", "24,79 m/s² (2,5 g)"),
               ("Duración del día", "9 h 56 min"),
               ("Duración del año", "11,9 años terrestres"),
               ("Temperatura", "-108 °C (nubes altas)"),
               ("Lunas", "más de 95 conocidas")],
        composicion="Gigante gaseoso formado sobre todo por hidrógeno (~90 % de sus átomos) y helio "
                    "(~10 %), con un posible núcleo denso en el centro.",
        curiosidades=["Su masa es 2,5 veces la de todos los demás planetas juntos.",
                      "La Gran Mancha Roja es una tormenta más grande que la Tierra, observada "
                      "desde hace más de 150 años.",
                      "Sus bandas de colores son corrientes de viento de cientos de km/h."],
        estilo="gas_giant", color=(110, 170, 230), escala=1.0, seed=5),
    Destino(
        id="europa", nombre="Europa", tipo="Luna helada de Júpiter", subtitulo="Un océano bajo el hielo",
        datos=[("Distancia a Júpiter", "671.000 km"),
               ("Diámetro", "3.122 km"),
               ("Gravedad", "1,31 m/s² (13 % de la Tierra)"),
               ("Rotación", "3,55 días (sincrónica)"),
               ("Temperatura", "-160 °C (ecuador)"),
               ("Atmósfera", "Exosfera de oxígeno muy tenue")],
        composicion="Corteza de hielo de agua sobre un océano global de agua salada, un manto de roca "
                    "y un núcleo metálico.",
        curiosidades=["Podría albergar más del doble del agua de todos los océanos terrestres.",
                      "Las grietas rojizas de su superficie son fracturas en el hielo.",
                      "La sonda Europa Clipper de la NASA la estudiará: llegará en 2030."],
        estilo="icy_moon", color=(255, 210, 150), escala=0.75, seed=8),
    Destino(
        id="saturno", nombre="Saturno", tipo="Gigante gaseoso", subtitulo="El señor de los anillos",
        datos=[("Distancia al Sol", "1.434 millones de km"),
               ("Diámetro", "120.536 km (ecuatorial)"),
               ("Gravedad", "10,44 m/s²"),
               ("Duración del día", "≈ 10 h 33 min"),
               ("Duración del año", "29,4 años terrestres"),
               ("Lunas", "más de 270 conocidas")],
        composicion="Hidrógeno (~96 %) y helio (~3 %). Es tan poco denso que flotaría en agua. "
                    "Sus anillos son sobre todo hielo de agua, en fragmentos del tamaño de un grano de polvo a una casa.",
        curiosidades=["Los anillos miden unos 280.000 km de ancho, pero en muchos puntos solo "
                      "decenas de metros de grosor.",
                      "Su luna Titán tiene una atmósfera densa y lagos de metano líquido.",
                      "La sonda Cassini lo exploró entre 2004 y 2017."],
        estilo="ringed_gas", color=(140, 205, 235), escala=1.15, seed=12),
    Destino(
        id="kepler186f", nombre="Kepler-186f", tipo="Exoplaneta (candidato habitable)",
        subtitulo="Un primo lejano de la Tierra",
        datos=[("Distancia a la Tierra", "≈ 500–580 años luz"),
               ("Radio", "≈ 1,17 veces el terrestre"),
               ("Período orbital", "130 días"),
               ("Estrella", "Kepler-186 (enana roja)"),
               ("Luz estelar recibida", "≈ 32 % de la solar"),
               ("Descubrimiento", "2014, telescopio Kepler")],
        composicion="Aún desconocida. Por su tamaño podría ser un mundo rocoso, quizá con agua líquida; "
                    "su masa y atmósfera no se han medido.",
        curiosidades=["Fue el primer planeta de tamaño terrestre hallado en la zona habitable de otra estrella.",
                      "Desde su superficie, el mediodía sería parecido al atardecer en la Tierra.",
                      "Está en la constelación del Cisne, dentro del campo de visión que observó Kepler."],
        estilo="exo_ocean", color=(200, 230, 120), escala=0.9, seed=21),
    Destino(
        id="ton618", nombre="Ton 618", tipo="Agujero negro ultramasivo", subtitulo="Un monstruo cósmico",
        datos=[("Masa", "≈ 66.000 millones de soles"),
               ("Distancia", "≈ 10.400 millones de años luz"),
               ("Constelación", "Canes Venatici"),
               ("Radio del horizonte", "≈ 1.300 UA"),
               ("Luminosidad (cuásar)", "≈ 140 billones de soles"),
               ("Tipo", "Cuásar hiperluminoso")],
        composicion="Una singularidad rodeada de un disco de acreción de gas ultracaliente que se "
                    "precipita hacia ella y emite luz y radiación a escala galáctica.",
        curiosidades=["Su horizonte de sucesos es unas 40 veces más ancho que la órbita de Neptuno.",
                      "'Ton' viene del catálogo del Observatorio de Tonantzintla (México).",
                      "Mantén la distancia: la nave no sobreviviría ni al borde de su disco."],
        estilo="black_hole", color=(40, 140, 255), escala=1.1, seed=30),
]


# ----------------------------------------------------------------------------
# 3. UTILIDADES DE IMAGEN (incluye el blending RGBA -> BGR)
# ----------------------------------------------------------------------------
def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(x: float) -> float:
    return 1.0 - (1.0 - x) ** 3


def ease_in_cubic(x: float) -> float:
    return x ** 3


def overlay_rgba(background: np.ndarray, overlay: np.ndarray, x: int, y: int,
                 opacity: float = 1.0) -> np.ndarray:
    """
    Superpone una imagen BGRA (con transparencia) sobre un frame BGR de OpenCV.

    background : frame BGR uint8 (se modifica "in place")
    overlay    : imagen BGRA uint8
    (x, y)     : esquina superior izquierda del overlay sobre el frame; puede
                 quedar parcialmente (o totalmente) fuera de la pantalla
    opacity    : opacidad global extra, de 0.0 a 1.0

    Fórmula por píxel:  salida = fg * alfa + bg * (1 - alfa)
    (calculada con operaciones uint8 de OpenCV para que sea rápida en tiempo real)
    """
    bh, bw = background.shape[:2]
    oh, ow = overlay.shape[:2]
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + ow, bw), min(y + oh, bh)
    if x0 >= x1 or y0 >= y1 or opacity <= 0.0:
        return background

    ov = overlay[y0 - y:y1 - y, x0 - x:x1 - x]
    roi = background[y0:y1, x0:x1]
    alpha = ov[..., 3]
    if opacity < 1.0:
        alpha = cv2.convertScaleAbs(alpha, alpha=float(opacity))
    a3 = cv2.merge([alpha, alpha, alpha])
    fg = cv2.multiply(np.ascontiguousarray(ov[..., :3]), a3, scale=1 / 255.0)      # fg * alfa
    bg = cv2.multiply(roi, cv2.bitwise_not(a3), scale=1 / 255.0)                   # bg * (1 - alfa)
    roi[:] = cv2.add(fg, bg)
    return background


def overlay_centered(background: np.ndarray, overlay: np.ndarray, cx: float, cy: float,
                     opacity: float = 1.0) -> np.ndarray:
    """Igual que overlay_rgba pero indicando el centro del sprite."""
    x = int(round(cx - overlay.shape[1] / 2))
    y = int(round(cy - overlay.shape[0] / 2))
    return overlay_rgba(background, overlay, x, y, opacity)


def load_sprite_bgra(path: Path, max_side: int = 1024) -> Optional[np.ndarray]:
    """Carga un PNG y lo devuelve siempre como BGRA uint8 (o None si no existe)."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.dtype != np.uint8:
        img = (img.astype(np.float32) / (65535.0 if img.dtype == np.uint16 else 1.0) * 255).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)   # sin alfa: queda opaco
    h, w = img.shape[:2]
    if max(h, w) > max_side:                          # evita redimensionar imágenes enormes en cada frame
        k = max_side / max(h, w)
        img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


def make_glow_sprite(size: int, color_bgr: tuple, strength: float = 0.9) -> np.ndarray:
    """Halo radial BGR (color * intensidad) para sumarlo de forma aditiva alrededor del planeta."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    r = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / (size / 2.0)
    k = np.clip(1.0 - r, 0.0, 1.0) ** 2.4 * strength
    return (np.array(color_bgr, np.float32)[None, None, :] * k[..., None]).astype(np.uint8)


def add_glow(frame: np.ndarray, glow: np.ndarray, cx: float, cy: float, gain: float = 1.0) -> None:
    """Suma (con saturación) un halo BGR centrado en (cx, cy); admite recortes fuera de pantalla."""
    gh, gw = glow.shape[:2]
    x, y = int(round(cx - gw / 2)), int(round(cy - gh / 2))
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + gw, W), min(y + gh, H)
    if x0 >= x1 or y0 >= y1 or gain <= 0:
        return
    roi = frame[y0:y1, x0:x1]
    roi[:] = cv2.addWeighted(roi, 1.0, np.ascontiguousarray(glow[y0 - y:y1 - y, x0 - x:x1 - x]), gain, 0)


def fit_frame(frame: np.ndarray) -> np.ndarray:
    """Recorta el frame de la cámara a 16:9 y lo escala a la resolución interna (W x H)."""
    h, w = frame.shape[:2]
    target = W / H
    if w / h > target:
        new_w = int(h * target)
        x0 = (w - new_w) // 2
        frame = frame[:, x0:x0 + new_w]
    else:
        new_h = int(w / target)
        y0 = (h - new_h) // 2
        frame = frame[y0:y0 + new_h]
    return cv2.resize(frame, (W, H), interpolation=cv2.INTER_LINEAR)


# ----------------------------------------------------------------------------
# 4. RECURSOS DE RESPALDO: planetas procedurales (PNG RGBA) y sonidos sintéticos
# ----------------------------------------------------------------------------
def _fbm(size: int, octaves: int, seed: int, persistence: float = 0.5) -> np.ndarray:
    """Ruido fractal (suma de ruido de valor a varias escalas), normalizado a 0..1."""
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 4 * 2 ** o
        grid = rng.random((cells, cells)).astype(np.float32)
        out += cv2.resize(grid, (size, size), interpolation=cv2.INTER_CUBIC) * amp
        total += amp
        amp *= persistence
    out /= total
    return (out - out.min()) / (out.max() - out.min() + 1e-6)


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _col(rgb) -> np.ndarray:
    return np.array(rgb, np.float32)


def _lerp(a, b, t: np.ndarray) -> np.ndarray:
    return _col(a)[None, None, :] * (1 - t[..., None]) + _col(b)[None, None, :] * t[..., None]


def _mix(base: np.ndarray, rgb, w: np.ndarray) -> np.ndarray:
    return base * (1 - w[..., None]) + _col(rgb)[None, None, :] * w[..., None]


def _over(base_rgb, base_a, top_rgb, top_a):
    """Composición 'over' de dos capas RGB + alfa (valores 0..1)."""
    out_a = top_a + base_a * (1 - top_a)
    num = top_rgb * top_a[..., None] + base_rgb * (base_a * (1 - top_a))[..., None]
    return num / np.maximum(out_a, 1e-6)[..., None], out_a


def _hot(t: np.ndarray) -> np.ndarray:
    """Gradiente de temperatura del disco de acreción: blanco -> naranja -> rojo oscuro."""
    a = _lerp((1.0, 0.96, 0.82), (1.0, 0.52, 0.14), np.clip(t * 2, 0, 1))
    b = _lerp((1.0, 0.52, 0.14), (0.50, 0.10, 0.05), np.clip(t * 2 - 1, 0, 1))
    return np.where((t < 0.5)[..., None], a, b)


def _to_bgra(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
    out = np.dstack([rgb[..., ::-1], a])
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def generate_planet_sprite(estilo: str, size: int = 640, seed: int = 0) -> np.ndarray:
    """Genera un sprite BGRA de un planeta (marcador de posición hasta tener tus PNG reales)."""
    S = size
    rng = np.random.default_rng(seed)
    ys, xs = np.mgrid[0:S, 0:S].astype(np.float32)
    c = (S - 1) / 2.0
    ringed = estilo in ("ringed_gas", "black_hole")
    Rpx = S / 2 * (0.42 if ringed else 0.78)
    nx, ny = (xs - c) / Rpx, (ys - c) / Rpx
    r = np.sqrt(nx * nx + ny * ny)
    nz = np.sqrt(np.clip(1 - r * r, 0, 1))
    disc_a = np.clip((1 - r) * Rpx, 0, 1)                 # borde antialias de 1 px
    n1, n2 = _fbm(S, 6, seed + 11), _fbm(S, 4, seed + 23)
    lat = np.arcsin(np.clip(ny, -1, 1))
    atm, atm_k = (1.0, 1.0, 1.0), 0.0
    albedo = np.zeros((S, S, 3), np.float32)

    if estilo == "rocky_red":                              # Marte
        albedo = _lerp((0.45, 0.22, 0.15), (0.86, 0.52, 0.33), _smooth(0.35, 0.65, n1))
        albedo = _mix(albedo, (0.95, 0.93, 0.92), _smooth(1.15, 1.3, np.abs(lat)) * 0.8)
        atm, atm_k = (0.9, 0.55, 0.4), 0.35
    elif estilo == "gas_giant":                            # Júpiter
        bands = 0.6 * (0.5 + 0.5 * np.sin(lat * 13 + n1 * 5.0)) + 0.4 * (0.5 + 0.5 * np.sin(lat * 31 + n2 * 8))
        albedo = _lerp((0.93, 0.85, 0.72), (0.62, 0.38, 0.24), bands)
        d = ((nx - 0.35) / 0.24) ** 2 + ((ny - 0.30) / 0.11) ** 2
        albedo = _mix(albedo, (0.72, 0.28, 0.18), np.clip(1 - d, 0, 1) ** 0.6 * 0.9)   # Gran Mancha Roja
        atm, atm_k = (0.95, 0.75, 0.5), 0.3
    elif estilo == "icy_moon":                             # Europa
        albedo = _lerp((0.80, 0.86, 0.92), (0.95, 0.97, 1.0), n1)
        albedo = _mix(albedo, (0.66, 0.50, 0.40), _smooth(0.62, 0.8, n2) * 0.5)
        crack = np.zeros((S, S), np.uint8)
        for _ in range(34):                                # grietas: polilíneas aleatorias
            ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0, 0.85) * Rpx
            p = np.array([c + rad * np.cos(ang), c + rad * np.sin(ang)])
            direction = rng.uniform(0, 2 * np.pi)
            for _ in range(int(rng.integers(3, 7))):
                direction += rng.normal(0, 0.35)
                q = p + rng.uniform(25, 70) * np.array([np.cos(direction), np.sin(direction)])
                cv2.line(crack, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), 255,
                         int(rng.integers(1, 3)), cv2.LINE_AA)
                p = q
        crack = cv2.GaussianBlur(crack, (0, 0), 1.1).astype(np.float32) / 255.0
        albedo = _mix(albedo, (0.55, 0.33, 0.22), np.clip(crack * 1.4, 0, 1) * 0.75)
        atm, atm_k = (0.7, 0.85, 1.0), 0.15
    elif estilo == "ringed_gas":                           # Saturno
        bands = 0.5 * (0.5 + 0.5 * np.sin(lat * 18 + n1 * 3)) + 0.25 * n2
        albedo = _lerp((0.88, 0.78, 0.58), (0.76, 0.62, 0.42), bands)
    elif estilo == "exo_ocean":                            # Kepler-186f
        land = _smooth(0.48, 0.56, n1)
        ocean = _col((0.08, 0.28, 0.38))[None, None, :] * (1 - land[..., None])
        albedo = ocean + _lerp((0.45, 0.20, 0.14), (0.65, 0.38, 0.22), n2) * land[..., None]
        albedo = _mix(albedo, (0.95, 0.90, 0.85), _smooth(0.55, 0.75, _fbm(S, 5, seed + 3)) * 0.6)
        atm, atm_k = (1.0, 0.62, 0.35), 0.8

    # --- Iluminación: Lambert + oscurecimiento de borde + brillo atmosférico ---
    if estilo == "black_hole":
        shaded = np.zeros((S, S, 3), np.float32)
    else:
        L = np.array([-0.55, -0.45, 0.70], np.float32)
        L /= np.linalg.norm(L)
        lam = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
        shaded = albedo * ((0.16 + 0.84 * lam) * np.power(nz, 0.30))[..., None]
        rim = np.power(1 - nz, 2.5) * (0.25 + lam) * 0.5 * atm_k
        shaded = np.clip(shaded + _col(atm)[None, None, :] * rim[..., None], 0, 1)

    zeros3, zeros = np.zeros((S, S, 3), np.float32), np.zeros((S, S), np.float32)

    if estilo == "ringed_gas":
        rx = S / 2 * 0.97
        ry = rx * 0.27
        ex, ey = (xs - c) / rx, (ys - c) / ry
        e = np.sqrt(ex * ex + ey * ey)
        edge = _smooth(0.60, 0.615, e) * (1 - _smooth(0.985, 1.0, e))
        b = np.clip(0.5 + 0.25 * np.sin(e * 70) + 0.2 * np.sin(e * 23 + 1.3) + 0.15 * (n1 - 0.5), 0, 1)
        gap = 1 - 0.9 * np.exp(-(((e - 0.80) / 0.012) ** 2))      # división de Cassini
        ring_a = edge * gap * (0.45 + 0.5 * b)
        ring_rgb = _lerp((0.55, 0.45, 0.32), (0.92, 0.84, 0.68), b)
        rgb, a = _over(zeros3, zeros, ring_rgb, ring_a * (ey < 0))   # mitad trasera del anillo
        rgb, a = _over(rgb, a, shaded, disc_a)                        # planeta
        rgb, a = _over(rgb, a, ring_rgb, ring_a * (ey >= 0))          # mitad delantera
        sprite = _to_bgra(rgb, a)
        M = cv2.getRotationMatrix2D((c, c), 18, 1.0)
        return cv2.warpAffine(sprite, M, (S, S), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))

    if estilo == "black_hole":
        rx = S / 2 * 0.97
        ry = rx * 0.22
        ex, ey = (xs - c) / rx, (ys - c) / ry
        e = np.sqrt(ex * ex + ey * ey)
        t = np.clip((e - 0.45) / 0.55, 0, 1)
        edge = _smooth(0.45, 0.47, e) * (1 - _smooth(0.97, 1.0, e))
        doppler = np.clip(0.80 - 0.40 * np.clip(ex, -1, 1), 0.3, 1.15)    # un lado más brillante
        disk_a = np.clip(edge * (0.25 + 0.75 * (1 - t)) * doppler * (0.65 + 0.7 * n1), 0, 1)
        disk_rgb = _hot(t)
        arc_a = (_smooth(1.0, 1.02, r) * (1 - _smooth(1.12, 1.30, r))
                 * np.power(np.clip(-ny, 0, 1), 0.5) * 0.85)               # luz curvada sobre el horizonte
        arc_rgb = _hot(np.clip((r - 1.0) / 0.3, 0, 1) * 0.8)
        photon_a = np.clip(np.exp(-(((r - 1.035) / 0.022) ** 2)), 0, 1)
        rr = np.sqrt(((xs - c) / (S / 2)) ** 2 + ((ys - c) / (S / 2)) ** 2)
        halo_a = np.clip(1 - rr, 0, 1) ** 3 * 0.30
        rgb, a = _over(zeros3, zeros, _hot(np.full((S, S), 0.35, np.float32)), halo_a)
        rgb, a = _over(rgb, a, disk_rgb, disk_a * (ey < 0))
        rgb, a = _over(rgb, a, arc_rgb, arc_a)
        rgb, a = _over(rgb, a, shaded, disc_a)                              # horizonte negro
        rgb, a = _over(rgb, a, disk_rgb, disk_a * (ey >= 0))
        rgb, a = _over(rgb, a, np.broadcast_to(_col((1.0, 0.93, 0.8)), (S, S, 3)).copy(), photon_a)
        sprite = _to_bgra(rgb, a)
        M = cv2.getRotationMatrix2D((c, c), -10, 1.0)
        return cv2.warpAffine(sprite, M, (S, S), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))

    # Planetas esféricos: halo atmosférico exterior + disco
    glow_a = np.clip(1 - (r - 1) / 0.22, 0, 1) ** 2 * (r >= 1) * 0.55 * atm_k
    glow_rgb = np.broadcast_to(_col(atm), (S, S, 3)).copy()
    rgb, a = _over(glow_rgb, glow_a, shaded, disc_a)
    return _to_bgra(rgb, a)


SR = 22050  # frecuencia de muestreo de los sonidos sintéticos


def _write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


def synth_hyperspace(dur: float = 2.0) -> np.ndarray:
    """Zumbido de motor + ruido filtrado, con bucle sin cortes (para reproducir en loop)."""
    n, fade = int(SR * dur), int(SR * 0.25)
    rng = np.random.default_rng(7)
    noise = np.convolve(rng.standard_normal(n + fade).astype(np.float32),
                        np.ones(24, np.float32) / 24, mode="same")
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    noise[:fade] = noise[:fade] * ramp + noise[n:n + fade] * (1 - ramp)   # empalme del bucle
    noise = noise[:n] / (np.max(np.abs(noise[:n])) + 1e-6)
    t = np.arange(n) / SR
    drone = (np.sin(2 * np.pi * 55 * t) + 0.6 * np.sin(2 * np.pi * 82.5 * t)
             + 0.3 * np.sin(2 * np.pi * 220 * t)) / 1.9
    out = (0.55 * noise + 0.55 * drone) * (1 + 0.25 * np.sin(2 * np.pi * 4 * t))
    return 0.7 * out / np.max(np.abs(out))


def synth_brake(dur: float = 1.6) -> np.ndarray:
    """Barrido descendente + ráfaga de ruido: sonido de frenado."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * (40 * t + 120 * (1 - np.exp(-3 * t)))) * np.exp(-1.6 * t)
    rng = np.random.default_rng(3)
    noise = np.convolve(rng.standard_normal(n), np.ones(12) / 12, mode="same") * np.exp(-5 * t)
    out = 0.8 * tone + 0.6 * noise
    return 0.8 * out / np.max(np.abs(out))


def synth_arrival(dur: float = 2.2) -> np.ndarray:
    """Acorde suave tipo campanilla: confirmación de llegada."""
    n = int(SR * dur)
    out = np.zeros(n)
    for i, f in enumerate((523.25, 659.25, 783.99, 1046.5)):
        start = int(SR * 0.12 * i)
        tt = np.arange(n - start) / SR
        env = np.exp(-2.2 * tt) * (1 - np.exp(-60 * tt))
        out[start:] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt)) * env
    return 0.6 * out / np.max(np.abs(out))


def ensure_assets() -> None:
    """Crea las carpetas y genera los PNG/WAV que falten (marcadores de posición)."""
    for folder in (PLANETS_DIR, SOUNDS_DIR, MODELS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    for d in DESTINOS:
        if not d.archivo.exists():
            print(f"[assets] Generando imagen de marcador: {d.archivo.relative_to(BASE_DIR)}")
            cv2.imwrite(str(d.archivo), generate_planet_sprite(d.estilo, 640, d.seed))
    for name, fn in (("hyperspace.wav", synth_hyperspace), ("brake.wav", synth_brake),
                     ("arrival.wav", synth_arrival)):
        path = SOUNDS_DIR / name
        if not path.exists():
            print(f"[assets] Generando sonido de marcador: {path.relative_to(BASE_DIR)}")
            _write_wav(path, fn())


# ----------------------------------------------------------------------------
# 5. AUDIO (pygame.mixer)
# ----------------------------------------------------------------------------
class AudioManager:
    """Reproduce el motor/hiperespacio (en bucle) y los sonidos de frenado y llegada."""

    def __init__(self, folder: Path):
        self.enabled = False
        self.muted = False
        self.sounds = {}
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            pygame.mixer.init()
            self._pg = pygame
            for key, filename, volume in (("hyper", "hyperspace.wav", 0.55),
                                          ("brake", "brake.wav", 0.8),
                                          ("arrive", "arrival.wav", 0.7)):
                path = folder / filename
                if path.exists():
                    snd = pygame.mixer.Sound(str(path))
                    snd.set_volume(volume)
                    self.sounds[key] = snd
            self.enabled = bool(self.sounds)
        except Exception as exc:  # sin pygame o sin dispositivo de audio
            print(f"[audio] Sonido desactivado: {exc}")

    def _play(self, key: str, **kwargs) -> None:
        if self.enabled and not self.muted and key in self.sounds:
            self.sounds[key].play(**kwargs)

    def start_hyperspace(self) -> None:
        self.sounds.get("hyper") and self.sounds["hyper"].stop()
        self._play("hyper", loops=-1, fade_ms=600)

    def stop_hyperspace(self, fade_ms: int = 500) -> None:
        if self.enabled and "hyper" in self.sounds:
            self.sounds["hyper"].fadeout(fade_ms)

    def play_brake(self) -> None:
        self._play("brake")

    def play_arrival(self) -> None:
        self._play("arrive")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted and self.enabled:
            self._pg.mixer.stop()

    def close(self) -> None:
        if self.enabled:
            self._pg.mixer.quit()


# ----------------------------------------------------------------------------
# 6. TIPOGRAFÍA (Pillow, para soportar acentos y ñ; cv2.putText no los dibuja)
# ----------------------------------------------------------------------------
class Typography:
    SANS = ["segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc"]
    SANS_BOLD = ["segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
                 "LiberationSans-Bold.ttf", "Helvetica.ttc"]
    MONO = ["consola.ttf", "Menlo.ttc", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "cour.ttf"]
    MONO_BOLD = ["consolab.ttf", "Menlo.ttc", "DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf", "courbd.ttf"]

    def __init__(self):
        self._cache = {}        # fuentes ya cargadas
        self._text_cache = {}   # textos ya renderizados (sprites BGRA): evita repetir trabajo cada frame

    def font(self, size: int, bold: bool = False, mono: bool = False):
        key = (size, bold, mono)
        if key not in self._cache:
            names = (self.MONO_BOLD if bold else self.MONO) if mono else (self.SANS_BOLD if bold else self.SANS)
            font = None
            for name in names:
                try:
                    font = ImageFont.truetype(name, size)
                    break
                except OSError:
                    continue
            if font is None:
                try:
                    font = ImageFont.load_default(size)
                except TypeError:
                    font = ImageFont.load_default()
            self._cache[key] = font
        return self._cache[key]

    def width(self, text: str, size: int, bold: bool = False, mono: bool = False) -> int:
        return int(self.font(size, bold, mono).getlength(text))

    def wrap(self, text: str, size: int, max_w: int, bold: bool = False) -> list:
        """Divide el texto en líneas que no superen max_w píxeles."""
        font = self.font(size, bold)
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines

    def _render_text(self, text: str, size: int, color, bold: bool, mono: bool) -> np.ndarray:
        """Renderiza el texto con Pillow en un sprite BGRA con fondo transparente."""
        font = self.font(size, bold, mono)
        pad = 4
        tw, th = int(font.getlength(text)) + 2 * pad, int(size * 1.6) + 2 * pad
        img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((pad, pad), text, font=font, fill=(color[2], color[1], color[0], 255))
        return np.ascontiguousarray(np.asarray(img)[..., [2, 1, 0, 3]])

    def draw(self, frame: np.ndarray, text: str, x: int, y: int, size: int = 16, color=TEXT,
             bold: bool = False, mono: bool = False, align: str = "left") -> None:
        """Dibuja texto (con acentos) sobre un frame BGR usando un caché de sprites de texto."""
        key = (text, size, color, bold, mono)
        sprite = self._text_cache.get(key)
        if sprite is None:
            if len(self._text_cache) > 600:        # evita que el caché crezca sin límite
                self._text_cache.clear()
            sprite = self._text_cache[key] = self._render_text(text, size, color, bold, mono)
        pad, tw = 4, sprite.shape[1] - 8
        if align == "right":
            x -= tw
        elif align == "center":
            x -= tw // 2
        overlay_rgba(frame, sprite, x - pad, y - pad)


# ----------------------------------------------------------------------------
# 7. DETECCIÓN DE GESTOS (MediaPipe Tasks: HandLandmarker)
# ----------------------------------------------------------------------------
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
                    (18, 19), (19, 20), (0, 17)]


def ensure_hand_model() -> Path:
    """Devuelve la ruta del modelo hand_landmarker.task, descargándolo la primera vez."""
    path = MODELS_DIR / "hand_landmarker.task"
    if path.exists():
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("[mediapipe] Descargando modelo de manos (≈ 8 MB), solo la primera vez...")
    try:
        urllib.request.urlretrieve(HAND_MODEL_URL, path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        sys.exit(f"No se pudo descargar el modelo ({exc}).\n"
                 f"Descárgalo manualmente desde:\n  {HAND_MODEL_URL}\n"
                 f"y guárdalo como:\n  {path}")
    return path


class GestureDetector:
    """
    Clasifica la mano en ABIERTA / PUÑO a partir de los 21 puntos de MediaPipe.

    Un dedo se considera extendido si su punta está más lejos de la muñeca que su
    articulación PIP (y suficientemente lejos respecto al tamaño de la palma). Así
    funciona sin importar la orientación de la mano. Además se aplica un voto por
    mayoría sobre los últimos fotogramas para evitar parpadeos.
    """

    FINGERS = ((8, 6), (12, 10), (16, 14), (20, 18))   # (punta, PIP) de índice..meñique

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(ensure_hand_model())),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5)
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ts = 0
        self.history = deque(maxlen=7)
        self.stable: Optional[str] = None      # gesto estable actual
        self.raw: Optional[str] = None         # gesto instantáneo
        self.landmarks: Optional[np.ndarray] = None   # (21, 2) en píxeles de la pantalla (W x H)

    def process(self, frame_bgr: np.ndarray) -> None:
        small = cv2.resize(frame_bgr, (640, 360), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        ts = max(int(time.monotonic() * 1000), self._last_ts + 1)   # la marca de tiempo debe crecer
        self._last_ts = ts
        result = self._landmarker.detect_for_video(
            self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts)

        if result.hand_landmarks:
            pts = np.array([(p.x * W, p.y * H) for p in result.hand_landmarks[0]], np.float32)
            self.landmarks, self.raw = pts, self.classify(pts)
        else:
            self.landmarks, self.raw = None, None

        self.history.append(self.raw)
        votes = Counter(g for g in self.history if g)
        for gesture, n in votes.items():
            if n >= 5:
                self.stable = gesture
        if not any(self.history):
            self.stable = None

    @classmethod
    def classify(cls, pts: np.ndarray) -> Optional[str]:
        def dist(a, b):
            return float(np.linalg.norm(pts[a] - pts[b]))

        palm = dist(0, 9) + 1e-6                        # muñeca -> base del dedo corazón
        ext = [dist(0, tip) > dist(0, pip) * 1.18 and dist(0, tip) > palm * 1.3
               for tip, pip in cls.FINGERS]             # índice, corazón, anular, meñique
        if sum(ext) >= 4:
            return GESTURE_OPEN

        # "L": índice extendido, los otros tres dedos cerrados, pulgar abierto y ~90° entre ambos
        if ext[0] and not any(ext[1:]):
            thumb_out = dist(4, 9) > palm * 0.85 and dist(4, 5) > palm * 0.55
            if thumb_out:
                v_thumb, v_index = pts[4] - pts[2], pts[8] - pts[5]
                cos = float(np.dot(v_thumb, v_index) /
                            (np.linalg.norm(v_thumb) * np.linalg.norm(v_index) + 1e-6))
                angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                if L_ANGLE_RANGE[0] <= angle <= L_ANGLE_RANGE[1]:
                    return GESTURE_L

        if sum(ext) <= 1:
            return GESTURE_FIST
        return None                                      # gesto ambiguo (p. ej. señal de paz)

    def close(self) -> None:
        self._landmarker.close()


# ----------------------------------------------------------------------------
# 8. CAMPO DE ESTRELLAS (efecto hiperespacio, vectorizado con NumPy)
# ----------------------------------------------------------------------------
class Starfield:
    """Estrellas en 3D proyectadas a 2D; al acelerar se alargan en estelas hacia los bordes."""

    def __init__(self, n: int = 700, seed: int = 1):
        self.rng = np.random.default_rng(seed)
        self.n = n
        self.x = self.rng.uniform(-1, 1, n).astype(np.float32)
        self.y = self.rng.uniform(-1, 1, n).astype(np.float32)
        self.z = self.rng.uniform(0.05, 1.0, n).astype(np.float32)
        self.phase = self.rng.uniform(0, 6.28, n).astype(np.float32)

    def update_and_draw(self, frame: np.ndarray, speed: float, dt: float, t: float) -> None:
        dz = speed * dt
        z_new = self.z - dz
        dead = z_new < 0.03                              # la estrella pasó la nave: reaparece lejos
        k = int(dead.sum())
        if k:
            self.x[dead] = self.rng.uniform(-1, 1, k)
            self.y[dead] = self.rng.uniform(-1, 1, k)
            z_new[dead] = self.rng.uniform(0.85, 1.0, k)
        z_prev = np.minimum(z_new + dz * 4.0, 1.5)       # origen de la estela
        self.z = z_new

        cx, cy = W / 2, H / 2
        px1, py1 = cx + self.x / z_new * (W / 2), cy + self.y / z_new * (H / 2)
        px0, py0 = cx + self.x / z_prev * (W / 2), cy + self.y / z_prev * (H / 2)
        bright = np.clip(1.15 - z_new, 0.15, 1.0) * (0.75 + 0.25 * np.sin(t * 3 + self.phase))

        visible = (px1 > -5) & (px1 < W + 5) & (py1 > -5) & (py1 < H + 5)
        for i in np.nonzero(visible)[0]:
            b = float(bright[i])
            color = (int(255 * b), int(238 * b), int(215 * b))
            thick = 2 if z_new[i] < 0.2 else 1
            cv2.line(frame, (int(px0[i]), int(py0[i])), (int(px1[i]), int(py1[i])), color, thick)


# ----------------------------------------------------------------------------
# 9. HUD (panel de mando de la nave)
# ----------------------------------------------------------------------------
@dataclass
class InfoCard:
    layer: np.ndarray        # BGRA con todo el texto de la ficha (pre-renderizado)
    x: int
    y: int
    w: int
    h: int
    accent: tuple


def build_info_card(d: Destino, typo: Typography) -> InfoCard:
    """Pre-renderiza la ficha técnica de un destino como imagen BGRA transparente."""
    accent = (d.color[2], d.color[1], d.color[0])        # RGB para Pillow
    dim, bright = (150, 165, 185), (235, 240, 248)
    pad, label_w = 26, 200
    inner_w = CARD_W - 2 * pad

    def layout(scale: float):
        s = lambda v: max(10, int(v * scale))
        cmds, y = [], pad
        cmds.append(("text", pad, y, d.nombre.upper(), typo.font(s(36), True), bright))
        y += int(s(36) * 1.3)
        for ln in typo.wrap(f"{d.tipo} · {d.subtitulo}", s(17), inner_w):
            cmds.append(("text", pad, y, ln, typo.font(s(17)), accent))
            y += int(s(17) * 1.4)
        y += int(s(17) * 0.3)
        cmds.append(("line", pad, y, CARD_W - pad, y))
        y += 12
        cmds.append(("text", pad, y, "DATOS CLAVE", typo.font(s(14), True, True), dim))
        y += int(s(14) * 1.8)
        lh = int(s(16) * 1.45)
        for label, value in d.datos:
            cmds.append(("text", pad, y, label, typo.font(s(16)), dim))
            lines = typo.wrap(value, s(16), inner_w - label_w, bold=True)
            for ln in lines:
                cmds.append(("text", pad + label_w, y, ln, typo.font(s(16), True), bright))
                y += lh
        y += 8
        cmds.append(("text", pad, y, "COMPOSICIÓN", typo.font(s(14), True, True), dim))
        y += int(s(14) * 1.8)
        for ln in typo.wrap(d.composicion, s(16), inner_w):
            cmds.append(("text", pad, y, ln, typo.font(s(16)), bright))
            y += lh
        y += 8
        cmds.append(("text", pad, y, "CURIOSIDADES", typo.font(s(14), True, True), dim))
        y += int(s(14) * 1.8)
        for item in d.curiosidades:
            for j, ln in enumerate(typo.wrap(item, s(16), inner_w - 16)):
                if j == 0:
                    cmds.append(("text", pad, y, "•", typo.font(s(16), True), accent))
                cmds.append(("text", pad + 16, y, ln, typo.font(s(16)), bright))
                y += lh
            y += 4
        return cmds, y + pad

    for scale in (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7):   # reduce la letra si no cabe en pantalla
        cmds, height = layout(scale)
        if height <= CARD_MAX_H:
            break

    img = Image.new("RGBA", (CARD_W, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for cmd in cmds:
        if cmd[0] == "text":
            _, x, y, text, font, color = cmd
            draw.text((x, y), text, font=font, fill=tuple(color) + (255,))
        else:
            _, x0, y0, x1, y1 = cmd
            draw.line((x0, y0, x1, y1), fill=accent + (150,), width=2)
    arr = np.array(img)[..., [2, 1, 0, 3]].copy()          # RGBA -> BGRA
    y = CARD_TOP + (CARD_MAX_H - height) // 2
    return InfoCard(arr, W - CARD_W - 36, y, CARD_W, height, d.color)


class HUD:
    """Paneles translúcidos (cv2.addWeighted), bordes tipo cabina y telemetría."""

    def __init__(self, typo: Typography):
        self.typo = typo

    def panel(self, frame, x, y, w, h, accent=CYAN, alpha=0.58, fade=1.0, corner=16):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        roi = frame[y0:y1, x0:x1]
        dark = np.empty_like(roi)
        dark[:] = PANEL_BG
        a = alpha * fade
        roi[:] = cv2.addWeighted(dark, a, roi, 1 - a, 0)          # fondo translúcido
        col = tuple(int(c * fade) for c in accent)
        cv2.rectangle(frame, (x, y), (x + w, y + h), tuple(int(c * 0.35) for c in col), 1)
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * corner, cy), col, 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy * corner), col, 2)

    def screen_corners(self, frame, color=CYAN):
        col = tuple(int(c * 0.55) for c in color)
        m, L = 8, 46
        for cx, cy, dx, dy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * L, cy), col, 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy * L), col, 2)

    def top_bar(self, frame, status, status_color, dest_text, accent):
        self.panel(frame, 20, 16, W - 40, 54, accent)
        T = self.typo
        T.draw(frame, "MUSEO ESPACIAL // NAVE EXPLORADORA-01", 42, 33, 17, TEXT, bold=True, mono=True)
        T.draw(frame, status, W // 2, 31, 20, status_color, bold=True, mono=True, align="center")
        T.draw(frame, dest_text, W - 42, 33, 17, accent, bold=True, mono=True, align="right")

    def telemetry(self, frame, ratio, gesture_label, gesture_color, idx, total, name, fps, accent):
        x, y, w, h = 20, H - 178, 340, 158
        self.panel(frame, x, y, w, h, accent)
        T = self.typo
        T.draw(frame, "TELEMETRÍA", x + 16, y + 12, 14, accent, bold=True, mono=True)
        cv2.line(frame, (x + 16, y + 34), (x + w - 16, y + 34), tuple(int(c * 0.4) for c in accent), 1)
        T.draw(frame, "VELOCIDAD", x + 16, y + 42, 15, TEXT_DIM, mono=True)
        kms = f"{int(ratio * LIGHT_SPEED_KMS):,}".replace(",", ".")
        T.draw(frame, f"{kms} km/s", x + w - 16, y + 42, 15, TEXT, bold=True, mono=True, align="right")
        bx, by, bw, bh = x + 16, y + 68, w - 32, 10                  # barra de velocidad
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), tuple(int(c * 0.5) for c in accent), 1)
        fill = int((bw - 2) * clamp(ratio))
        if fill > 0:
            cv2.rectangle(frame, (bx + 1, by + 1), (bx + 1 + fill, by + bh - 1), accent, -1)
        for k in range(1, 10):
            tx = bx + int(bw * k / 10)
            cv2.line(frame, (tx, by + bh), (tx, by + bh + 3), tuple(int(c * 0.5) for c in accent), 1)
        T.draw(frame, "GESTO", x + 16, y + 94, 15, TEXT_DIM, mono=True)
        T.draw(frame, gesture_label, x + w - 16, y + 94, 15, gesture_color, bold=True, mono=True, align="right")
        T.draw(frame, "DESTINO", x + 16, y + 116, 15, TEXT_DIM, mono=True)
        T.draw(frame, f"{idx + 1:02d}/{total:02d}  {name.upper()}", x + w - 16, y + 116, 15, TEXT, bold=True,
               mono=True, align="right")
        T.draw(frame, "FPS", x + 16, y + 136, 13, TEXT_DIM, mono=True)
        T.draw(frame, f"{fps:4.0f}", x + w - 16, y + 136, 13, TEXT_DIM, mono=True, align="right")

    def hint(self, frame, text, color, warning=None):
        w, h = 640, 44
        x, y = 380, H - 76
        self.panel(frame, x, y, w, h, color, alpha=0.62)
        self.typo.draw(frame, text, x + w // 2, y + 11, 17, color, bold=True, mono=True, align="center")
        if warning:
            self.typo.draw(frame, warning, x + w // 2, y - 28, 15, AMBER, bold=True, mono=True, align="center")

    def keys_help(self, frame):
        self.typo.draw(frame, "GESTO L (2 s): AGUJERO NEGRO", W - 28, H - 46, 12, AMBER, mono=True, align="right")
        self.typo.draw(frame, "Q salir · H mano · M sonido · O/F/L/C teclado", W - 28, H - 24, 12, TEXT_DIM,
                       mono=True, align="right")

    def l_progress(self, frame, progress: float, destination: str):
        """Barra de carga mientras se mantiene el gesto L."""
        w, h = 540, 56
        x, y = (W - w) // 2, 84
        self.panel(frame, x, y, w, h, AMBER, alpha=0.72)
        self.typo.draw(frame, f"GESTO L DETECTADO · SALTANDO A {destination}", x + w // 2, y + 7, 15, AMBER,
                       bold=True, mono=True, align="center")
        bx, by, bw, bh = x + 20, y + 34, w - 40, 10
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (140, 160, 175), 1)
        fill = int((bw - 2) * clamp(progress))
        if fill > 0:
            cv2.rectangle(frame, (bx + 1, by + 1), (bx + 1 + fill, by + bh - 1), AMBER, -1)

    def reticle(self, frame, center, radius, color, t, thick=2):
        """Arcos giratorios estilo 'bloqueo de objetivo'."""
        cx, cy = int(center[0]), int(center[1])
        for k in range(4):
            ang = (t * 40 + k * 90) % 360
            cv2.ellipse(frame, (cx, cy), (radius, radius), 0, ang, ang + 50, color, thick, cv2.LINE_AA)
        r2 = int(radius * 1.08)
        for k in range(4):
            ang = (-t * 25 + k * 90 + 45) % 360
            cv2.ellipse(frame, (cx, cy), (r2, r2), 0, ang, ang + 18, tuple(int(c * 0.6) for c in color), 1,
                        cv2.LINE_AA)

    def info_card(self, frame, card: InfoCard, fade: float):
        if fade <= 0.01:
            return
        self.panel(frame, card.x, card.y, card.w, card.h, card.accent, alpha=0.66, fade=fade)
        overlay_rgba(frame, card.layer, card.x, card.y, opacity=fade)


# ----------------------------------------------------------------------------
# 10. APLICACIÓN: máquina de estados del viaje
# ----------------------------------------------------------------------------
class State(Enum):
    STANDBY = auto()      # nave detenida, esperando mano abierta
    HYPERSPACE = auto()   # viajando a la velocidad de la luz
    BRAKING = auto()      # frenando y aproximándose al planeta
    ARRIVED = auto()      # en órbita: se muestra la ficha técnica
    DEPARTING = auto()    # despegando hacia el siguiente destino


STATUS = {State.STANDBY: ("SISTEMAS EN ESPERA", AMBER),
          State.HYPERSPACE: ("SALTO HIPERESPACIAL ACTIVO", CYAN),
          State.BRAKING: ("FRENADO DE EMERGENCIA", RED),
          State.ARRIVED: ("EN ÓRBITA · ESCANEO COMPLETO", GREEN),
          State.DEPARTING: ("INICIANDO SALTO", CYAN)}


class SpaceMuseum:
    def __init__(self):
        ensure_assets()
        self.destinos = DESTINOS
        self.typo = Typography()
        self.hud = HUD(self.typo)
        self.audio = AudioManager(SOUNDS_DIR)
        self.stars = Starfield()

        # Sprites RGBA, halos y fichas pre-renderizadas
        self.sprites, self.glows, self.cards = {}, {}, {}
        for d in self.destinos:
            sprite = load_sprite_bgra(d.archivo)
            if sprite is None:
                sprite = generate_planet_sprite(d.estilo, 640, d.seed)
            self.sprites[d.id] = sprite
            self.glows[d.id] = make_glow_sprite(256, d.color, 0.8)
            self.cards[d.id] = build_info_card(d, self.typo)

        # Capas precalculadas para el render
        self.navy_frame = np.full((H, W, 3), NAVY, np.uint8)
        self.white_frame = np.full((H, W, 3), 255, np.uint8)
        self.warp_glow = self._make_warp_glow()
        self.cockpit_mask = self._make_cockpit_mask()

        # Estado
        self.state = State.STANDBY
        self.state_t = 0.0
        self.anim_t = 0.0
        self.target_idx = 0
        self.leaving_idx = 0
        self.speed = 0.0
        self.dim = 0.55
        self.flash = 0.0
        self.fps = 30.0
        self.override: Optional[str] = None     # gesto simulado con el teclado
        self.l_hold = 0.0                       # segundos acumulados con la "L" estable
        self.l_armed = False                    # la "L" solo cuenta tras haber soltado el gesto una vez
        self.next_script: Optional[str] = None  # si se rellena, run() termina y se lanza ese script
        self.show_skeleton = True

    # ------------------------------------------------------------------ capas
    @staticmethod
    def _make_warp_glow() -> np.ndarray:
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xs - W / 2) / (W / 2)) ** 2 + ((ys - H / 2) / (H / 2)) ** 2)
        g = np.exp(-(r * 2.2) ** 2)
        glow = np.zeros((H, W, 3), np.float32)
        glow[..., 0], glow[..., 1], glow[..., 2] = 255 * g, 210 * g, 130 * g
        return glow.astype(np.uint8)

    @staticmethod
    def _make_cockpit_mask() -> np.ndarray:
        """Viñeta + líneas de escaneo sutiles, aplicadas una sola vez por frame."""
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        nx, ny = (xs - W / 2) / (W / 2), (ys - H / 2) / (H / 2)
        vig = np.clip(1 - 0.55 * np.clip(nx ** 2 + ny ** 2 - 0.35, 0, 1), 0.35, 1)
        scan = np.where(ys % 3 == 0, 0.91, 1.0)
        m = (vig * scan * 255).astype(np.uint8)
        return cv2.merge([m, m, m])

    # ----------------------------------------------------------- transiciones
    def _set_state(self, state: State) -> None:
        self.state, self.state_t = state, 0.0

    def _begin_hyperspace(self) -> None:
        self.audio.start_hyperspace()
        self._set_state(State.HYPERSPACE)

    def _begin_braking(self) -> None:
        self.audio.stop_hyperspace(500)
        self.audio.play_brake()
        self.flash = 0.45
        self._set_state(State.BRAKING)

    def _begin_departure(self) -> None:
        self.leaving_idx = self.target_idx
        self.target_idx = (self.target_idx + 1) % len(self.destinos)
        self.audio.start_hyperspace()
        self.flash = 0.25
        self._set_state(State.DEPARTING)

    def update(self, dt: float, gesture: Optional[str]) -> None:
        self.anim_t += dt
        self.state_t += dt
        self._update_l_hold(dt, gesture)
        self.flash = max(0.0, self.flash - dt * 1.4)
        k = min(1.0, dt * 3.0)
        s = self.state

        if s == State.STANDBY:
            self.speed += (0.0 - self.speed) * k
            if gesture == GESTURE_OPEN:
                self._begin_hyperspace()
        elif s == State.HYPERSPACE:
            self.speed += (SPEED_MAX - self.speed) * k
            if gesture == GESTURE_FIST and self.state_t >= MIN_TRAVEL_BEFORE_BRAKE:
                self._begin_braking()
        elif s == State.BRAKING:
            self.speed *= math.exp(-4.0 * dt)
            if self.state_t >= T_BRAKE:
                self.audio.play_arrival()
                self.flash = 0.2
                self._set_state(State.ARRIVED)
        elif s == State.ARRIVED:
            self.speed *= math.exp(-6.0 * dt)
            if gesture == GESTURE_OPEN and self.state_t >= MIN_ARRIVED_BEFORE_DEPART:
                self._begin_departure()
        elif s == State.DEPARTING:
            self.speed += (SPEED_MAX - self.speed) * min(1.0, dt * 2.0)
            if self.state_t >= T_DEPART:
                self._set_state(State.HYPERSPACE)

        target_dim = {State.STANDBY: 0.55, State.HYPERSPACE: 0.82, State.BRAKING: 0.55,
                      State.ARRIVED: 0.55, State.DEPARTING: 0.70}[self.state]
        self.dim += (target_dim - self.dim) * min(1.0, dt * 4.0)

    def _update_l_hold(self, dt: float, gesture: Optional[str]) -> None:
        """
        Cuenta cuánto tiempo se mantiene la "L". Si el programa acaba de abrirse
        con la "L" todavía hecha (porque venimos del otro script), se ignora hasta
        que el usuario la suelte: así no se produce un rebote entre programas.
        """
        if not self.l_armed:
            self.l_hold = 0.0
            if gesture != GESTURE_L and self.anim_t > 1.0:
                self.l_armed = True
            return
        if gesture == GESTURE_L:
            self.l_hold += dt
        else:
            self.l_hold = max(0.0, self.l_hold - dt * 2.0)    # tolera parpadeos breves del detector
        if self.l_hold >= L_HOLD_SECONDS:
            if (BASE_DIR / NEXT_SCRIPT).exists():
                self.next_script = NEXT_SCRIPT
            else:
                print(f"[aviso] No se encontró {NEXT_SCRIPT} en {BASE_DIR}")
                self.l_hold, self.l_armed = 0.0, False

    # --------------------------------------------------------------- animación
    def _planet_pose(self):
        """(índice, escala, centro, opacidad) del planeta visible, o None."""
        s, t = self.state, self.state_t
        if s == State.BRAKING:
            p = clamp(t / T_BRAKE)
            e = ease_out_cubic(p)
            return (self.target_idx, 0.05 + 0.95 * e,
                    (lerp(W / 2, PLANET_X, e), lerp(H / 2, PLANET_Y, e)), min(1.0, p * 4))
        if s == State.ARRIVED:
            return (self.target_idx, 1.0 + 0.008 * math.sin(t * 1.3),
                    (PLANET_X, PLANET_Y + 6 * math.sin(t * 0.9)), 1.0)
        if s == State.DEPARTING:
            p = clamp(t / T_DEPART)
            e = ease_in_cubic(p)
            return (self.leaving_idx, 1.0 - 0.95 * e,
                    (lerp(PLANET_X, W / 2, e), lerp(PLANET_Y, H / 2, e)), 1.0 - clamp((p - 0.6) / 0.4))
        return None

    def _card_fade(self):
        """(índice, opacidad) de la ficha técnica, o None."""
        s, t = self.state, self.state_t
        if s == State.BRAKING:
            return self.target_idx, clamp((clamp(t / T_BRAKE) - 0.65) / 0.35)
        if s == State.ARRIVED:
            return self.target_idx, 1.0
        if s == State.DEPARTING:
            return self.leaving_idx, 1.0 - clamp(clamp(t / T_DEPART) / 0.35)
        return None

    def _draw_planet(self, frame, idx, scale, center, opacity) -> None:
        d = self.destinos[idx]
        size = int(PLANET_BASE * d.escala * scale)
        if size < 4 or opacity <= 0.01:
            return
        halo = cv2.resize(self.glows[d.id], (int(size * 1.7), int(size * 1.7)), interpolation=cv2.INTER_LINEAR)
        add_glow(frame, halo, center[0], center[1], opacity)

        sprite = self.sprites[d.id]
        h0, w0 = sprite.shape[:2]
        new_w, new_h = size, max(2, int(size * h0 / w0))
        interp = cv2.INTER_AREA if new_w < w0 else cv2.INTER_LINEAR
        overlay_centered(frame, cv2.resize(sprite, (new_w, new_h), interpolation=interp),
                         center[0], center[1], opacity)

        if self.state in (State.BRAKING, State.ARRIVED) and opacity > 0.6:
            radius = int(size * 0.5 * 0.97)
            self.hud.reticle(frame, center, radius, d.color, self.anim_t)

    def _draw_skeleton(self, frame, pts: np.ndarray, color) -> None:
        p = pts.astype(int)
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, tuple(p[a]), tuple(p[b]), color, 2, cv2.LINE_AA)
        for q in p:
            cv2.circle(frame, tuple(q), 4, WHITE, -1, cv2.LINE_AA)

    # ------------------------------------------------------------------ render
    def render(self, frame: np.ndarray, dt: float, detector: Optional[GestureDetector] = None) -> np.ndarray:
        """Compone un fotograma completo a partir del frame de cámara (ya en W x H)."""
        d_target = self.destinos[self.target_idx]
        accent = d_target.color if self.state in (State.BRAKING, State.ARRIVED) else CYAN

        # 1) Cámara oscurecida con tinte azul: deja ver las estrellas y al jugador
        frame[:] = cv2.addWeighted(frame, 1 - self.dim, self.navy_frame, self.dim, 0)

        # 2) Hiperespacio
        self.stars.update_and_draw(frame, self.speed, dt, self.anim_t)
        ratio = clamp(self.speed / SPEED_MAX)
        if ratio > 0.05:
            frame[:] = cv2.addWeighted(frame, 1.0, self.warp_glow, 0.55 * ratio, 0)
        if ratio > 0.2 and self.state in (State.HYPERSPACE, State.DEPARTING):
            self.hud.reticle(frame, (W / 2, H / 2), 70, CYAN, self.anim_t * 2, 1)

        # 3) Planeta (PNG RGBA) y ficha técnica
        pose = self._planet_pose()
        if pose:
            self._draw_planet(frame, *pose)

        # 4) Esqueleto de la mano
        pts = detector.landmarks if detector else None
        if self.show_skeleton and pts is not None:
            self._draw_skeleton(frame, pts, CYAN)

        # 5) Destello en frenado/llegada
        if self.flash > 0.01:
            frame[:] = cv2.addWeighted(frame, 1 - self.flash, self.white_frame, self.flash, 0)

        # 6) HUD
        card = self._card_fade()
        if card:
            self.hud.info_card(frame, self.cards[self.destinos[card[0]].id], card[1])

        status, status_color = STATUS[self.state]
        self.hud.screen_corners(frame, accent)
        self.hud.top_bar(frame, status, status_color,
                         f"DESTINO {self.target_idx + 1:02d}/{len(self.destinos):02d} · {d_target.nombre.upper()}",
                         accent)

        gesture = self.override or (detector.stable if detector else None)
        if self.override:
            g_label, g_color = "TECLADO: " + {GESTURE_OPEN: "ABIERTA", GESTURE_FIST: "PUÑO",
                                               GESTURE_L: "L"}.get(gesture, "?"), AMBER
        elif gesture == GESTURE_L:
            g_label, g_color = "GESTO L", AMBER
        elif gesture == GESTURE_OPEN:
            g_label, g_color = "MANO ABIERTA", GREEN
        elif gesture == GESTURE_FIST:
            g_label, g_color = "PUÑO CERRADO", AMBER
        else:
            g_label, g_color = ("DETECTANDO…" if pts is not None else "SIN MANO"), (TEXT_DIM if pts is not None else RED)
        self.hud.telemetry(frame, ratio, g_label, g_color, self.target_idx, len(self.destinos),
                           d_target.nombre, self.fps, accent)

        nxt = self.destinos[(self.target_idx + 1) % len(self.destinos)].nombre.upper()
        hints = {State.STANDBY: ("ABRE LA MANO PARA DESPEGAR", AMBER),
                 State.HYPERSPACE: (f"DESTINO FIJADO: {d_target.nombre.upper()} · CIERRA EL PUÑO PARA FRENAR", CYAN),
                 State.BRAKING: (f"APROXIMACIÓN A {d_target.nombre.upper()}…", RED),
                 State.ARRIVED: (f"ABRE LA MANO PARA VIAJAR A {nxt}", GREEN),
                 State.DEPARTING: ("ACELERANDO…", CYAN)}
        hint_text, hint_color = hints[self.state]
        warning = "SIN SEÑAL DE MANO · COLÓCALA FRENTE A LA CÁMARA" if (pts is None and not self.override) else None
        self.hud.hint(frame, hint_text, hint_color, warning)
        self.hud.keys_help(frame)
        if self.l_hold > 0.05:
            self.hud.l_progress(frame, self.l_hold / L_HOLD_SECONDS, "AGUJERO NEGRO")

        # 7) Viñeta y líneas de escaneo
        return cv2.multiply(frame, self.cockpit_mask, scale=1 / 255.0)

    # --------------------------------------------------------------- principal
    def run(self) -> Optional[str]:
        """Bucle principal. Devuelve el nombre del script a lanzar (o None si se cierra normalmente)."""
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            sys.exit(f"No se pudo abrir la cámara (índice {CAMERA_INDEX}). Cambia CAMERA_INDEX al inicio del archivo.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        try:
            detector = GestureDetector()
        except Exception as exc:
            cap.release()
            sys.exit(f"No se pudo iniciar MediaPipe: {exc}\nRevisa que 'pip install mediapipe' haya funcionado.")

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, W, H)
        last = time.perf_counter()
        failures = 0
        try:
            while True:
                ok, raw = cap.read()
                if not ok:
                    failures += 1
                    if failures > 30:
                        print("La cámara dejó de responder.")
                        break
                    continue
                failures = 0

                now = time.perf_counter()
                dt = clamp(now - last, 1e-3, 0.1)
                last = now
                self.fps = 0.92 * self.fps + 0.08 * (1.0 / dt)

                frame = fit_frame(cv2.flip(raw, 1))              # espejo, para que se sienta natural
                detector.process(frame)
                gesture = self.override or detector.stable
                self.update(dt, gesture)
                cv2.imshow(WINDOW_NAME, self.render(frame, dt, detector))
                if self.next_script:                             # gesto L mantenido: salir para cambiar de programa
                    break

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("h"):
                    self.show_skeleton = not self.show_skeleton
                elif key == ord("m"):
                    self.audio.set_muted(not self.audio.muted)
                    if not self.audio.muted and self.state in (State.HYPERSPACE, State.DEPARTING):
                        self.audio.start_hyperspace()
                elif key == ord("o"):
                    self.override = GESTURE_OPEN
                elif key == ord("f"):
                    self.override = GESTURE_FIST
                elif key == ord("l"):
                    self.override = GESTURE_L
                elif key == ord("c"):
                    self.override = None
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            cap.release()                 # libera la cámara para que el otro script pueda usarla
            detector.close()
            self.audio.close()            # cierra el mezclador de audio
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        return self.next_script


def launch_script(script: str, camera_index: int) -> None:
    """Lanza otro script de Python (mismo intérprete, multiplataforma) como proceso independiente."""
    time.sleep(0.4)   # margen para que el sistema libere del todo la cámara
    subprocess.Popen([sys.executable, str(BASE_DIR / script), "--camera", str(camera_index)],
                     cwd=str(BASE_DIR))


def main() -> None:
    next_script = SpaceMuseum().run()    # al volver, cámara, audio y ventanas ya están cerrados
    if next_script:
        launch_script(next_script, CAMERA_INDEX)
    sys.exit()


if __name__ == "__main__":
    main()
