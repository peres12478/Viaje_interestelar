#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGUJERO NEGRO — del desafío de gestos al tesseract de Interstellar
==================================================================

FASE 1 · PROTOCOLO DE ACCESO
    Un agujero negro atrae polvo, hielo y fragmentos dorados. El sistema te pide
    mostrar con los dedos un número (1 a 5) y mantenerlo ~1 s. Si completas 5
    gestos correctos, el agujero abre un portal. Si se agota el tiempo (12 s) pierdes
    un punto de sincronización. Mientras tanto la mano sigue siendo un campo
    gravitatorio: palma abierta atrae partículas y puño las atrapa y lanza.

FASE 2 · PORTAL
    Cruzas el horizonte de sucesos (efecto de caída y destello).

FASE 3 · TESSERACT
    Una simulación de la "biblioteca del tiempo": un hipercubo 4D girando dentro de
    un túnel de líneas temporales. Mueve la mano de izquierda a derecha para viajar
    por el tiempo; cuando un recuerdo (esfera luminosa) se alinee contigo, cierra el
    puño para sellarlo. Con 5 recuerdos el mensaje llega a su destino.

Gestos de números (con el pulgar recogido salvo en el 5):
    1 = índice · 2 = índice+corazón · 3 = índice+corazón+anular · 4 = cuatro dedos · 5 = mano abierta
GESTO "L" (pulgar e índice en ángulo recto, mantenido 2 s): cierra este programa y
vuelve a abrir main.py (el Museo Espacial) en cualquier fase.

Teclado (opcional, para probar sin cámara):
    1-5 simular número · F puño · O mano abierta · L gesto L · C quitar simulación
    R reiniciar la secuencia · H esqueleto de la mano · Q / ESC salir

Estructura esperada (la misma carpeta que main.py):
    museo_espacial/
    ├── main.py
    ├── agujero_negro.py
    └── assets/models/hand_landmarker.task   (se descarga solo si falta)
"""

from __future__ import annotations

import itertools
import math
import subprocess
import sys
import time
import urllib.request
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
MODELS_DIR = BASE_DIR / "assets" / "models"
HAND_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                  "hand_landmarker/float16/1/hand_landmarker.task")

W, H = 1280, 720
CENTER = np.array([W / 2, H / 2], np.float32)
WINDOW_NAME = "Agujero Negro"


def _camera_from_args(default: int = 0) -> int:
    """Hereda la cámara de main.py:  python agujero_negro.py --camera 1"""
    if "--camera" in sys.argv:
        try:
            return int(sys.argv[sys.argv.index("--camera") + 1])
        except (IndexError, ValueError):
            pass
    return default


CAMERA_INDEX = _camera_from_args(0)

GESTURE_OPEN, GESTURE_FIST, GESTURE_L = "open", "fist", "l"

# Salto entre aplicaciones mediante el gesto "L"
NEXT_SCRIPT = "main.py"
L_HOLD_SECONDS = 2.0
L_ANGLE_RANGE = (60.0, 120.0)

# --- Física (unidades: píxeles y segundos) ---
BH_GM = 5.5e6              # "G·M" del agujero negro: a 300 px la aceleración es ~60 px/s²
BH_SOFT2 = 30.0 ** 2       # suavizado para evitar aceleraciones infinitas
BH_RADIUS0 = 46.0          # radio inicial del horizonte (crece al tragar partículas)
DRAG = 0.015               # rozamiento tenue del polvo: las órbitas decaen lentamente
N_PARTICLES = 130
SUBSTEPS = 3

HAND_GM = 2.6e6            # intensidad del campo atractor de la mano
HAND_SOFT2 = 50.0 ** 2
HAND_RANGE = 330.0         # alcance del campo de la mano (px)
HAND_DAMP = 1.3            # amortiguación cerca de la mano (permite "orbitarla" sin salir disparado)
GRAB_RADIUS = 120.0        # radio de captura de la pinza (puño)
MAX_HELD = 24              # partículas máximas sujetas a la vez
THROW_GAIN = 1.1           # factor de velocidad al lanzar

# --- Desafío de gestos y tesseract ---
CHALLENGE_TOTAL = 5        # gestos correctos necesarios para abrir el portal
CHALLENGE_TIME = 12.0      # segundos disponibles por gesto
CHALLENGE_HOLD = 0.9       # segundos que hay que mantener el número correcto
T_PORTAL = 4.2             # duración de la caída por el horizonte de sucesos
MEMORIES = 5               # recuerdos que hay que sellar en el tesseract
TESS_SCALE = 120.0         # tamaño en pantalla del hipercubo

COMBO_WINDOW = 2.0         # segundos para encadenar combos
KIND_VALUE = {0: 10, 1: 25, 2: 100}

# Colores BGR
CYAN = (255, 225, 100)
AMBER = (60, 190, 255)
RED = (90, 90, 255)
GREEN = (140, 255, 150)
WHITE = (245, 245, 245)
TEXT = (235, 230, 225)
TEXT_DIM = (175, 160, 140)
PANEL_BG = (30, 18, 10)
NAVY = (46, 20, 8)
KIND_COLOR = {0: (255, 235, 215), 1: (255, 225, 110), 2: (50, 210, 255)}


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------
# 2. UTILIDADES DE IMAGEN
# ----------------------------------------------------------------------------
def overlay_rgba(background: np.ndarray, overlay: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
    """Superpone una imagen BGRA sobre un frame BGR (in place): salida = fg·alfa + bg·(1 − alfa)."""
    bh, bw = background.shape[:2]
    oh, ow = overlay.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + ow, bw), min(y + oh, bh)
    if x0 >= x1 or y0 >= y1 or opacity <= 0.0:
        return
    ov = overlay[y0 - y:y1 - y, x0 - x:x1 - x]
    roi = background[y0:y1, x0:x1]
    alpha = ov[..., 3]
    if opacity < 1.0:
        alpha = cv2.convertScaleAbs(alpha, alpha=float(opacity))
    a3 = cv2.merge([alpha, alpha, alpha])
    fg = cv2.multiply(np.ascontiguousarray(ov[..., :3]), a3, scale=1 / 255.0)
    bg = cv2.multiply(roi, cv2.bitwise_not(a3), scale=1 / 255.0)
    roi[:] = cv2.add(fg, bg)


def make_glow_sprite(size: int, color_bgr: tuple, strength: float = 0.9) -> np.ndarray:
    """Halo radial BGR (color * intensidad) para sumarlo de forma aditiva."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    r = np.sqrt((xs - c) ** 2 + (ys - c) ** 2) / (size / 2.0)
    k = np.clip(1.0 - r, 0.0, 1.0) ** 2.4 * strength
    return (np.array(color_bgr, np.float32)[None, None, :] * k[..., None]).astype(np.uint8)


def add_glow(frame: np.ndarray, glow: np.ndarray, cx: float, cy: float, gain: float = 1.0) -> None:
    gh, gw = glow.shape[:2]
    x, y = int(round(cx - gw / 2)), int(round(cy - gh / 2))
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + gw, W), min(y + gh, H)
    if x0 >= x1 or y0 >= y1 or gain <= 0:
        return
    roi = frame[y0:y1, x0:x1]
    roi[:] = cv2.addWeighted(roi, 1.0, np.ascontiguousarray(glow[y0 - y:y1 - y, x0 - x:x1 - x]), gain, 0)


def fit_frame(frame: np.ndarray) -> np.ndarray:
    """Recorta a 16:9 y escala a la resolución interna (W x H)."""
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


def hot_color(t: float, brightness: float = 1.0) -> tuple:
    """Gradiente del disco de acreción (BGR): blanco-amarillo -> naranja -> rojo oscuro."""
    inner, mid, outer = (210, 245, 255), (36, 133, 255), (13, 26, 128)
    t = clamp(t)
    a, b, k = (inner, mid, t * 2) if t < 0.5 else (mid, outer, t * 2 - 1)
    return tuple(int(clamp(lerp(a[i], b[i], k) * brightness, 0, 255)) for i in range(3))


# ----------------------------------------------------------------------------
# 3. TIPOGRAFÍA (Pillow: soporta acentos y ñ) con caché de textos
# ----------------------------------------------------------------------------
class Typography:
    SANS = ["segoeui.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc"]
    SANS_BOLD = ["segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
                 "LiberationSans-Bold.ttf", "Helvetica.ttc"]
    MONO = ["consola.ttf", "Menlo.ttc", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "cour.ttf"]
    MONO_BOLD = ["consolab.ttf", "Menlo.ttc", "DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf", "courbd.ttf"]

    def __init__(self):
        self._fonts, self._texts = {}, {}

    def font(self, size: int, bold: bool = False, mono: bool = False):
        key = (size, bold, mono)
        if key not in self._fonts:
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
            self._fonts[key] = font
        return self._fonts[key]

    def _render(self, text, size, color, bold, mono) -> np.ndarray:
        font, pad = self.font(size, bold, mono), 4
        tw, th = int(font.getlength(text)) + 2 * pad, int(size * 1.6) + 2 * pad
        img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((pad, pad), text, font=font, fill=(color[2], color[1], color[0], 255))
        return np.ascontiguousarray(np.asarray(img)[..., [2, 1, 0, 3]])

    def draw(self, frame, text, x, y, size=16, color=TEXT, bold=False, mono=False, align="left",
             opacity: float = 1.0) -> None:
        key = (text, size, color, bold, mono)
        sprite = self._texts.get(key)
        if sprite is None:
            if len(self._texts) > 600:
                self._texts.clear()
            sprite = self._texts[key] = self._render(text, size, color, bold, mono)
        pad, tw = 4, sprite.shape[1] - 8
        if align == "right":
            x -= tw
        elif align == "center":
            x -= tw // 2
        overlay_rgba(frame, sprite, x - pad, y - pad, opacity)


# ----------------------------------------------------------------------------
# 4. DETECCIÓN DE GESTOS (MediaPipe Tasks: HandLandmarker) — incluye la "L"
# ----------------------------------------------------------------------------
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                    (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18),
                    (18, 19), (19, 20), (0, 17)]


def ensure_hand_model() -> Path:
    path = MODELS_DIR / "hand_landmarker.task"
    if path.exists():
        return path
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print("[mediapipe] Descargando modelo de manos (≈ 8 MB), solo la primera vez...")
    try:
        urllib.request.urlretrieve(HAND_MODEL_URL, path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        sys.exit(f"No se pudo descargar el modelo ({exc}).\nDescárgalo desde:\n  {HAND_MODEL_URL}\n"
                 f"y guárdalo como:\n  {path}")
    return path


class GestureDetector:
    """
    Clasifica la mano en ABIERTA / PUÑO / "L" con los 21 puntos de MediaPipe.

    Un dedo está extendido si su punta está más lejos de la muñeca que su
    articulación PIP (y lejos respecto al tamaño de la palma), sin importar la
    orientación de la mano. La "L" exige: índice extendido, corazón/anular/meñique
    cerrados, pulgar abierto y un ángulo de ~90° entre pulgar e índice.
    """

    FINGERS = ((8, 6), (12, 10), (16, 14), (20, 18))

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(ensure_hand_model())),
            running_mode=vision.RunningMode.VIDEO, num_hands=1,
            min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5)
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ts = 0
        self.history = deque(maxlen=7)
        self.num_history = deque(maxlen=7)
        self.stable: Optional[str] = None
        self.raw: Optional[str] = None
        self.stable_number: Optional[int] = None         # 1..5 estable (dedos mostrados)
        self.number_raw: Optional[int] = None
        self.landmarks: Optional[np.ndarray] = None      # (21, 2) en píxeles de pantalla (W x H)

    def process(self, frame_bgr: np.ndarray) -> None:
        small = cv2.resize(frame_bgr, (640, 360), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        ts = max(int(time.monotonic() * 1000), self._last_ts + 1)
        self._last_ts = ts
        result = self._landmarker.detect_for_video(
            self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ts)
        if result.hand_landmarks:
            pts = np.array([(p.x * W, p.y * H) for p in result.hand_landmarks[0]], np.float32)
            self.landmarks, self.raw = pts, self.classify(pts)
            self.number_raw = self.classify_number(pts)
        else:
            self.landmarks, self.raw, self.number_raw = None, None, None

        self.history.append(self.raw)
        for gesture, n in Counter(g for g in self.history if g).items():
            if n >= 5:                                   # voto por mayoría en los últimos 7 fotogramas
                self.stable = gesture
        if not any(self.history):
            self.stable = None

        self.num_history.append(self.number_raw)
        votes = Counter(n for n in self.num_history if n)
        if votes:
            best, n_best = votes.most_common(1)[0]
            if n_best >= 5:
                self.stable_number = best
        if self.stable_number is not None and votes.get(self.stable_number, 0) < 3:
            self.stable_number = None                    # el número dejó de verse

    @classmethod
    def classify(cls, pts: np.ndarray) -> Optional[str]:
        def dist(a, b):
            return float(np.linalg.norm(pts[a] - pts[b]))

        palm = dist(0, 9) + 1e-6
        ext = [dist(0, tip) > dist(0, pip) * 1.18 and dist(0, tip) > palm * 1.3 for tip, pip in cls.FINGERS]
        if sum(ext) >= 4:
            return GESTURE_OPEN

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
        return None

    @classmethod
    def classify_number(cls, pts: np.ndarray) -> Optional[int]:
        """
        Cuenta los dedos mostrados (1 a 5) como se hace al contar con la mano:
        1 índice · 2 índice+corazón · 3 +anular · 4 cuatro dedos con el pulgar recogido ·
        5 mano abierta con el pulgar fuera. Con el pulgar fuera y solo el índice sería
        una "L", así que eso no cuenta como número.
        """
        def dist(a, b):
            return float(np.linalg.norm(pts[a] - pts[b]))

        palm = dist(0, 9) + 1e-6
        ext = [dist(0, tip) > dist(0, pip) * 1.18 and dist(0, tip) > palm * 1.3 for tip, pip in cls.FINGERS]
        thumb_out = dist(4, 9) > palm * 0.85 and dist(4, 5) > palm * 0.55
        if all(ext):
            return 5 if thumb_out else 4
        if thumb_out:
            return None
        if ext == [True, False, False, False]:
            return 1
        if ext == [True, True, False, False]:
            return 2
        if ext == [True, True, True, False]:
            return 3
        return None

    def close(self) -> None:
        self._landmarker.close()


# ----------------------------------------------------------------------------
# 5. SISTEMA DE PARTÍCULAS (física vectorizada con NumPy)
# ----------------------------------------------------------------------------
@dataclass
class Swallowed:
    """Datos de una partícula tragada por el agujero negro (para puntuar y animar)."""
    kind: int
    pos: np.ndarray
    thrown: bool


class ParticleSystem:
    """
    N partículas con gravedad hacia el agujero negro y, opcionalmente, hacia la mano.
    Toda la dinámica se calcula con arrays: sin bucles for en la física.
    """

    def __init__(self, n: int = N_PARTICLES, seed: int = 4):
        self.n = n
        self.rng = np.random.default_rng(seed)
        self.pos = np.zeros((n, 2), np.float32)
        self.vel = np.zeros((n, 2), np.float32)
        self.radius = np.zeros(n, np.float32)
        self.kind = np.zeros(n, np.int32)
        self.held = np.zeros(n, bool)
        self.thrown = np.zeros(n, bool)           # fue atrapada por la pinza (da bonus al tragarse)
        self.offset = np.zeros((n, 2), np.float32)
        self._set_kinds(np.arange(n))
        self._place_orbiting(np.arange(n))

    def _set_kinds(self, idx: np.ndarray) -> None:
        k = len(idx)
        self.kind[idx] = self.rng.choice(3, size=k, p=[0.72, 0.22, 0.06])
        base = np.array([2.0, 3.0, 4.0], np.float32)[self.kind[idx]]
        self.radius[idx] = base + self.rng.uniform(0, 2, k)
        self.held[idx] = False
        self.thrown[idx] = False

    def _place_orbiting(self, idx: np.ndarray) -> None:
        """Posición inicial en un anillo alrededor del agujero, con velocidad casi orbital."""
        k = len(idx)
        r = self.rng.uniform(180, 520, k)
        th = self.rng.uniform(0, 2 * np.pi, k)
        self.pos[idx] = CENTER + np.stack((r * np.cos(th), r * np.sin(th)), axis=1)
        v = np.sqrt(BH_GM / r) * self.rng.uniform(0.55, 0.95, k)
        self.vel[idx] = np.stack((-v * np.sin(th), v * np.cos(th)), axis=1)

    def _respawn_edge(self, idx: np.ndarray) -> None:
        """Reaparece en un borde de la pantalla, moviéndose hacia la zona central."""
        k = len(idx)
        rng = self.rng
        edge = rng.integers(0, 4, k)
        x = np.where(edge == 0, rng.uniform(0, W, k), np.where(edge == 1, W + 30, np.where(edge == 2, rng.uniform(0, W, k), -30)))
        y = np.where(edge == 0, -30, np.where(edge == 1, rng.uniform(0, H, k), np.where(edge == 2, H + 30, rng.uniform(0, H, k))))
        self.pos[idx] = np.stack((x, y), axis=1)
        target = CENTER + rng.uniform(-1, 1, (k, 2)) * np.array([420, 260], np.float32)
        d = target - self.pos[idx]
        d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-6
        self.vel[idx] = d * rng.uniform(50, 130, (k, 1))
        self._set_kinds(idx)

    @property
    def n_held(self) -> int:
        return int(self.held.sum())

    def update(self, dt: float, gm: float, r_horizon: float, hand: Optional[np.ndarray],
               hand_vel: np.ndarray, mode: str) -> list:
        """
        Avanza la simulación.
            hand      : posición de la mano (px) o None
            mode      : "attract" (mano abierta), "grab" (puño) o "none"
        Devuelve la lista de partículas tragadas en este paso.
        """
        h = dt / SUBSTEPS
        free = (~self.held)[:, None]
        for _ in range(SUBSTEPS):
            # Gravedad del agujero negro: a = GM · d / (|d|² + ε²)^(3/2)
            d = CENTER - self.pos
            r2 = np.einsum("ij,ij->i", d, d) + BH_SOFT2
            acc = d * (gm / (r2 * np.sqrt(r2)))[:, None]

            if hand is not None and mode == "attract":
                dh = hand - self.pos
                rh2 = np.einsum("ij,ij->i", dh, dh)
                near = rh2 < HAND_RANGE ** 2
                acc += dh * (HAND_GM / (rh2 + HAND_SOFT2) ** 1.5 * near)[:, None]
                damp = near & ~self.held
                self.vel[damp] *= math.exp(-HAND_DAMP * h)

            self.vel += acc * h * free
            self.vel *= math.exp(-DRAG * h)
            self.pos += self.vel * h * free

        # Pinza: atrapar partículas cercanas y arrastrarlas con la mano
        if hand is not None and mode == "grab":
            dh = hand - self.pos
            dist = np.linalg.norm(dh, axis=1)
            cand = np.nonzero(~self.held & (dist < GRAB_RADIUS))[0]
            room = MAX_HELD - self.n_held
            if len(cand) and room > 0:
                chosen = cand[np.argsort(dist[cand])[:room]]
                self.held[chosen] = True
                self.thrown[chosen] = True
                self.offset[chosen] = (self.pos[chosen] - hand) * 0.45
            if self.held.any():
                target = hand + self.offset[self.held]
                self.pos[self.held] += (target - self.pos[self.held]) * min(1.0, 16.0 * dt)
                self.vel[self.held] = hand_vel
        elif self.held.any():                              # soltar: conservan la velocidad de la mano
            self.vel[self.held] = hand_vel * THROW_GAIN
            self.held[:] = False

        # Horizonte de sucesos: tragar partículas
        d = CENTER - self.pos
        r2 = np.einsum("ij,ij->i", d, d)
        swallowed_idx = np.nonzero(r2 < (r_horizon + self.radius * 0.4) ** 2)[0]
        swallowed = [Swallowed(int(self.kind[i]), self.pos[i].copy(), bool(self.thrown[i])) for i in swallowed_idx]

        # Reaparecer las tragadas y las que se alejaron demasiado
        lost = np.nonzero(r2 > 1400 ** 2)[0]
        respawn = np.unique(np.concatenate((swallowed_idx, lost)))
        if len(respawn):
            self._respawn_edge(respawn)
        return swallowed


# ----------------------------------------------------------------------------
# 6. HUD
# ----------------------------------------------------------------------------
class HUD:
    def __init__(self, typo: Typography):
        self.typo = typo

    def panel(self, frame, x, y, w, h, accent=CYAN, alpha=0.58, corner=16):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        roi = frame[y0:y1, x0:x1]
        dark = np.empty_like(roi)
        dark[:] = PANEL_BG
        roi[:] = cv2.addWeighted(dark, alpha, roi, 1 - alpha, 0)          # fondo translúcido
        cv2.rectangle(frame, (x, y), (x + w, y + h), tuple(int(c * 0.35) for c in accent), 1)
        for cx, cy, dx, dy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * corner, cy), accent, 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy * corner), accent, 2)

    def screen_corners(self, frame, color=CYAN):
        col = tuple(int(c * 0.55) for c in color)
        m, L = 8, 46
        for cx, cy, dx, dy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + dx * L, cy), col, 2)
            cv2.line(frame, (cx, cy), (cx, cy + dy * L), col, 2)

    def top_bar(self, frame, status, status_color, score_text):
        self.panel(frame, 20, 16, W - 40, 54, CYAN)
        T = self.typo
        T.draw(frame, "AGUJERO NEGRO // LABORATORIO DE GRAVEDAD", 42, 33, 17, TEXT, bold=True, mono=True)
        T.draw(frame, status, W // 2 + 40, 31, 20, status_color, bold=True, mono=True, align="center")
        T.draw(frame, score_text, W - 42, 31, 20, AMBER, bold=True, mono=True, align="right")

    def stats(self, frame, score, combo_mult, held, swallowed, mass, gesture_label, gesture_color, fps):
        x, y, w, h = 20, H - 216, 340, 196
        self.panel(frame, x, y, w, h, CYAN)
        T = self.typo
        T.draw(frame, "PANEL DE CONTROL", x + 16, y + 12, 14, CYAN, bold=True, mono=True)
        cv2.line(frame, (x + 16, y + 34), (x + w - 16, y + 34), tuple(int(c * 0.4) for c in CYAN), 1)
        rows = [("PUNTUACIÓN", f"{score:,}".replace(",", "."), TEXT),
                ("COMBO", f"x{combo_mult}", AMBER if combo_mult > 1 else TEXT),
                ("ATRAPADAS", str(held), TEXT),
                ("TRAGADAS", str(swallowed), TEXT),
                ("MASA", f"{mass:.1f} u", TEXT),
                ("GESTO", gesture_label, gesture_color)]
        for i, (label, value, color) in enumerate(rows):
            yy = y + 42 + i * 21
            T.draw(frame, label, x + 16, yy, 14, TEXT_DIM, mono=True)
            T.draw(frame, value, x + w - 16, yy, 14, color, bold=True, mono=True, align="right")
        T.draw(frame, f"{fps:4.0f} FPS", x + w - 16, y + h - 24, 12, TEXT_DIM, mono=True, align="right")

    def hint(self, frame, text, color, warning=None):
        w, h = 640, 44
        x, y = 380, H - 76
        self.panel(frame, x, y, w, h, color, alpha=0.62)
        self.typo.draw(frame, text, x + w // 2, y + 11, 16, color, bold=True, mono=True, align="center")
        if warning:
            self.typo.draw(frame, warning, x + w // 2, y - 28, 15, AMBER, bold=True, mono=True, align="center")

    def keys_help(self, frame):
        self.typo.draw(frame, "GESTO L (2 s): MUSEO ESPACIAL", W - 28, H - 46, 12, AMBER, mono=True, align="right")
        self.typo.draw(frame, "Q salir · H mano · R reiniciar · 1-5/F/L/C teclado", W - 28, H - 24, 12, TEXT_DIM,
                       mono=True, align="right")

    def l_progress(self, frame, progress: float, destination: str):
        w, h = 540, 56
        x, y = (W - w) // 2, 84
        self.panel(frame, x, y, w, h, AMBER, alpha=0.72)
        self.typo.draw(frame, f"GESTO L DETECTADO · VOLVIENDO A {destination}", x + w // 2, y + 7, 15, AMBER,
                       bold=True, mono=True, align="center")
        bx, by, bw, bh = x + 20, y + 34, w - 40, 10
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (140, 160, 175), 1)
        fill = int((bw - 2) * clamp(progress))
        if fill > 0:
            cv2.rectangle(frame, (bx + 1, by + 1), (bx + 1 + fill, by + bh - 1), AMBER, -1)

    # ---- Fase 1: protocolo de acceso ----
    def challenge_panel(self, frame, ch, accent=CYAN):
        x, y, w, h = W - 390, 150, 360, 372
        self.panel(frame, x, y, w, h, accent, alpha=0.68)
        T = self.typo
        T.draw(frame, "PROTOCOLO DE ACCESO", x + 18, y + 14, 15, accent, bold=True, mono=True)
        cv2.line(frame, (x + 18, y + 38), (x + w - 18, y + 38), tuple(int(c * 0.4) for c in accent), 1)

        seg_w = (w - 36 - 4 * 8) // CHALLENGE_TOTAL
        for i in range(CHALLENGE_TOTAL):
            sx = x + 18 + i * (seg_w + 8)
            if i < ch.progress:
                cv2.rectangle(frame, (sx, y + 50), (sx + seg_w, y + 64), GREEN, -1)
            else:
                cv2.rectangle(frame, (sx, y + 50), (sx + seg_w, y + 64), (120, 110, 100), 1)
        T.draw(frame, f"SINCRONIZACIÓN {ch.progress}/{CHALLENGE_TOTAL}", x + w // 2, y + 70, 13, TEXT_DIM,
               mono=True, align="center")

        ncx, ncy = x + w // 2, y + 172                              # número grande + anillo de retención
        cv2.circle(frame, (ncx, ncy), 78, (100, 90, 70), 2, cv2.LINE_AA)
        hold = clamp(ch.hold / CHALLENGE_HOLD)
        if hold > 0.01:
            cv2.ellipse(frame, (ncx, ncy), (78, 78), 0, -90, -90 + 360 * hold, GREEN, 6, cv2.LINE_AA)
        T.draw(frame, str(ch.target), ncx, ncy - 66, 110, WHITE, bold=True, align="center")

        T.draw(frame, f"MUESTRA {ch.target} DEDO" + ("" if ch.target == 1 else "S"), ncx, y + 266, 18, accent,
               bold=True, mono=True, align="center")
        for i in range(5):                                           # fila de dedos
            cx = ncx - 48 + i * 24
            if i < ch.target:
                cv2.circle(frame, (cx, y + 304), 7, accent, -1, cv2.LINE_AA)
            else:
                cv2.circle(frame, (cx, y + 304), 7, (120, 110, 100), 1, cv2.LINE_AA)

        bx, by, bw, bh = x + 18, y + 326, w - 36, 8                  # tiempo restante
        frac = clamp(ch.time_left / CHALLENGE_TIME)
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (120, 110, 100), 1)
        if frac > 0:
            cv2.rectangle(frame, (bx + 1, by + 1), (bx + 1 + int((bw - 2) * frac), by + bh - 1),
                          AMBER if frac > 0.25 else RED, -1)
        if ch.message_t > 0:
            ok = ch.message == "SINCRONIZADO"
            T.draw(frame, ch.message, ncx, y + 342, 15, GREEN if ok else RED, bold=True, mono=True,
                   align="center", opacity=clamp(ch.message_t / 0.5))

    # ---- Fase 3: tesseract ----
    def timeline(self, frame, tau, mem_tau, collected, aligned_idx, accent=AMBER):
        x, y, w, h = 360, H - 152, 700, 60
        self.panel(frame, x, y, w, h, accent, alpha=0.62)
        T = self.typo
        T.draw(frame, "LÍNEA TEMPORAL", x + 18, y + 6, 12, TEXT_DIM, mono=True)
        x0, x1, yy = x + 30, x + w - 30, y + 38
        cv2.line(frame, (x0, yy), (x1, yy), (130, 120, 110), 1)
        for k in range(11):
            tx = x0 + int((x1 - x0) * k / 10)
            cv2.line(frame, (tx, yy - 3), (tx, yy + 3), (130, 120, 110), 1)
        for i, tm in enumerate(mem_tau):                             # recuerdos: rombos
            mx = x0 + int((x1 - x0) * float(tm))
            pts = np.array([[mx, yy - 9], [mx + 8, yy], [mx, yy + 9], [mx - 8, yy]], np.int32)
            if collected[i]:
                cv2.fillPoly(frame, [pts], GREEN)
            elif i == aligned_idx:
                cv2.fillPoly(frame, [pts], WHITE)
            else:
                cv2.polylines(frame, [pts], True, accent, 2, cv2.LINE_AA)
        cx = x0 + int((x1 - x0) * tau)                               # cursor
        cv2.fillPoly(frame, [np.array([[cx - 7, yy - 24], [cx + 7, yy - 24], [cx, yy - 10]], np.int32)], CYAN)
        T.draw(frame, f"{tau * 100:5.1f} %", x + w - 18, y + 6, 12, CYAN, bold=True, mono=True, align="right")

    def tess_stats(self, frame, tau, n_done, rot_deg, gesture_label, gesture_color, fps):
        x, y, w, h = 20, H - 196, 340, 176
        self.panel(frame, x, y, w, h, AMBER)
        T = self.typo
        T.draw(frame, "SIMULACIÓN · TESSERACT", x + 16, y + 12, 14, AMBER, bold=True, mono=True)
        cv2.line(frame, (x + 16, y + 34), (x + w - 16, y + 34), tuple(int(c * 0.4) for c in AMBER), 1)
        rows = [("INSTANTE", f"{tau * 100:5.1f} %", TEXT), ("RECUERDOS", f"{n_done}/{MEMORIES}", GREEN if n_done else TEXT),
                ("ROTACIÓN 4D", f"{rot_deg:5.0f}°", TEXT), ("GESTO", gesture_label, gesture_color)]
        for i, (label, value, color) in enumerate(rows):
            yy = y + 44 + i * 24
            T.draw(frame, label, x + 16, yy, 14, TEXT_DIM, mono=True)
            T.draw(frame, value, x + w - 16, yy, 14, color, bold=True, mono=True, align="right")
        T.draw(frame, f"{fps:4.0f} FPS", x + w - 16, y + h - 24, 12, TEXT_DIM, mono=True, align="right")

    def banner(self, frame, title, subtitle, color):
        w, h = 760, 120
        x, y = (W - w) // 2, 200
        self.panel(frame, x, y, w, h, color, alpha=0.74)
        self.typo.draw(frame, title, W // 2, y + 18, 38, color, bold=True, mono=True, align="center")
        self.typo.draw(frame, subtitle, W // 2, y + 76, 16, TEXT, mono=True, align="center")


# ----------------------------------------------------------------------------
# 7. FONDO ESTELAR
# ----------------------------------------------------------------------------
class Starfield:
    """Estrellas lejanas con parpadeo y una deriva lentísima (sensación de profundidad)."""

    def __init__(self, n: int = 220, seed: int = 2):
        rng = np.random.default_rng(seed)
        self.x = rng.uniform(0, W, n)
        self.y = rng.uniform(0, H, n)
        self.base = rng.uniform(0.25, 1.0, n)
        self.phase = rng.uniform(0, 6.28, n)
        self.speed = rng.uniform(2, 9, n)

    def draw(self, frame: np.ndarray, t: float) -> None:
        xs = (self.x + t * self.speed) % W
        br = self.base * (0.7 + 0.3 * np.sin(t * 2 + self.phase))
        for x, y, b in zip(xs.astype(int).tolist(), self.y.astype(int).tolist(), br.tolist()):
            c = (int(255 * b), int(240 * b), int(220 * b))
            frame[y:y + 2, x:x + 2] = c


# ----------------------------------------------------------------------------
# 8. FASES DEL JUEGO, DESAFÍO DE GESTOS Y TESSERACT
# ----------------------------------------------------------------------------
class Phase(Enum):
    CHALLENGE = auto()    # protocolo de acceso: mostrar números con los dedos
    PORTAL = auto()       # caída por el horizonte de sucesos
    TESSERACT = auto()    # simulación del tesseract


class WarpField:
    """Estrellas en 3D que se estiran en estelas (efecto de caída por el portal)."""

    def __init__(self, n: int = 650, seed: int = 1):
        self.rng = np.random.default_rng(seed)
        self.x = self.rng.uniform(-1, 1, n).astype(np.float32)
        self.y = self.rng.uniform(-1, 1, n).astype(np.float32)
        self.z = self.rng.uniform(0.05, 1.0, n).astype(np.float32)

    def update_and_draw(self, frame: np.ndarray, speed: float, dt: float) -> None:
        dz = speed * dt
        z_new = self.z - dz
        dead = z_new < 0.03
        k = int(dead.sum())
        if k:
            self.x[dead] = self.rng.uniform(-1, 1, k)
            self.y[dead] = self.rng.uniform(-1, 1, k)
            z_new[dead] = self.rng.uniform(0.85, 1.0, k)
        z_prev = np.minimum(z_new + dz * 4.0, 1.5)
        self.z = z_new
        cx, cy = W / 2, H / 2
        px1, py1 = cx + self.x / z_new * (W / 2), cy + self.y / z_new * (H / 2)
        px0, py0 = cx + self.x / z_prev * (W / 2), cy + self.y / z_prev * (H / 2)
        bright = np.clip(1.15 - z_new, 0.15, 1.0)
        for i in np.nonzero((px1 > -5) & (px1 < W + 5) & (py1 > -5) & (py1 < H + 5))[0]:
            b = float(bright[i])
            cv2.line(frame, (int(px0[i]), int(py0[i])), (int(px1[i]), int(py1[i])),
                     (int(255 * b), int(235 * b), int(200 * b)), 2 if z_new[i] < 0.2 else 1)


class ChallengeSystem:
    """
    Secuencia de gestos numéricos. Hay que mantener el número pedido durante
    CHALLENGE_HOLD segundos. Con CHALLENGE_TOTAL aciertos se completa el protocolo.
    Si se agota el tiempo se pierde un punto de sincronización (nunca baja de 0).
    """

    def __init__(self, seed: int = 11):
        self.rng = np.random.default_rng(seed)
        self.progress = 0
        self.target = 0
        self.hold = 0.0
        self.time_left = CHALLENGE_TIME
        self.message = ""
        self.message_t = 0.0
        self.complete = False
        self.fails = 0
        self._new_target()

    def _new_target(self) -> None:
        choices = [n for n in range(1, 6) if n != self.target]      # nunca repite el número anterior
        self.target = int(self.rng.choice(choices))
        self.hold, self.time_left = 0.0, CHALLENGE_TIME

    def update(self, dt: float, number: Optional[int]) -> Optional[str]:
        """Devuelve "ok" o "fail" si ocurrió algo en este fotograma."""
        if self.complete:
            return None
        self.message_t = max(0.0, self.message_t - dt)
        if number == self.target:
            self.hold += dt
        else:
            self.hold = max(0.0, self.hold - dt * 2.0)               # tolera parpadeos del detector
        self.time_left -= dt

        if self.hold >= CHALLENGE_HOLD:
            self.progress += 1
            self.message, self.message_t = "SINCRONIZADO", 1.2
            if self.progress >= CHALLENGE_TOTAL:
                self.complete = True
            else:
                self._new_target()
            return "ok"
        if self.time_left <= 0:
            self.progress = max(0, self.progress - 1)
            self.fails += 1
            self.message, self.message_t = "SEÑAL PERDIDA", 1.2
            self._new_target()
            return "fail"
        return None


def _rot4(i: int, j: int, a: float) -> np.ndarray:
    """Matriz de rotación 4D en el plano (i, j)."""
    m = np.eye(4, dtype=np.float32)
    c, s = math.cos(a), math.sin(a)
    m[i, i], m[j, j], m[i, j], m[j, i] = c, c, -s, s
    return m


class TesseractScene:
    """
    Simulación del tesseract: un hipercubo 4D girando dentro de un túnel de líneas
    temporales. La mano recorre el tiempo (eje horizontal) y gira el hipercubo (vertical).
    Cuando un recuerdo se alinea con el instante actual, el puño lo sella.
    """

    Z_RANGE = 11.0     # distancia en profundidad que recorre un recuerdo al moverse en el tiempo
    Z_ALIGNED = 3.0    # profundidad a la que se ve un recuerdo cuando está alineado con el instante actual
    ALIGN = 0.035      # tolerancia (en unidades de tiempo normalizado) para sellar un recuerdo
    FOCAL = 520.0

    def __init__(self, seed: int = 5):
        rng = np.random.default_rng(seed)
        self.verts = np.array(list(itertools.product((-1.0, 1.0), repeat=4)), np.float32)   # 16 vértices
        self.edges = [(i, j) for i in range(16) for j in range(i + 1, 16)
                      if int((self.verts[i] != self.verts[j]).sum()) == 1]                  # 32 aristas

        taus: list = []
        while len(taus) < MEMORIES:                                   # instantes separados entre sí
            c = float(rng.uniform(0.08, 0.92))
            if all(abs(c - t) > 0.12 for t in taus):
                taus.append(c)
        self.mem_tau = np.array(sorted(taus), np.float32)
        xs, ys = np.array([-2.6, -1.7, 1.7, 2.6]), np.array([-1.6, -0.9, 0.9, 1.6])
        self.mem_xy = np.stack((rng.choice(xs, MEMORIES), rng.choice(ys, MEMORIES)), axis=1)
        self.collected = np.zeros(MEMORIES, bool)

        self.t = 0.0
        self.tau = 0.5
        self.tau_target = 0.5
        self.hy = 0.5
        self.a_xw = self.a_yz = self.a_zw = self.a_xy = 0.0
        self.rings: list = []          # (t0, px, py) ondas al sellar un recuerdo
        self.done_t: Optional[float] = None

    @property
    def n_done(self) -> int:
        return int(self.collected.sum())

    @property
    def done(self) -> bool:
        return bool(self.collected.all())

    def aligned_index(self) -> Optional[int]:
        """Índice del recuerdo alineado con el instante actual (o None)."""
        hit = np.nonzero(~self.collected & (np.abs(self.mem_tau - self.tau) < self.ALIGN))[0]
        return int(hit[0]) if len(hit) else None

    def update(self, dt: float, hand: Optional[np.ndarray], gesture: Optional[str]) -> None:
        self.t += dt
        if hand is not None:
            self.tau_target = clamp((float(hand[0]) / W - 0.12) / 0.76)
            self.hy = clamp(float(hand[1]) / H)
        self.tau += (self.tau_target - self.tau) * min(1.0, dt * 7.0)

        self.a_xw += dt * 0.35
        self.a_yz += dt * 0.22
        self.a_zw += ((self.hy - 0.5) * math.pi * 1.2 - self.a_zw) * min(1.0, dt * 4.0) if hand is not None else dt * 0.3
        self.a_xy = self.tau * 2 * math.pi

        if gesture == GESTURE_FIST and not self.done:
            idx = self.aligned_index()
            if idx is not None:
                self.collected[idx] = True
                z = self.Z_ALIGNED + float(self.mem_tau[idx] - self.tau) * self.Z_RANGE
                px = W / 2 + self.mem_xy[idx, 0] / z * self.FOCAL
                py = H / 2 + self.mem_xy[idx, 1] / z * self.FOCAL
                self.rings.append((self.t, float(np.clip(px, 60, W - 60)), float(np.clip(py, 60, H - 60))))
                if self.done:
                    self.done_t = self.t
        self.rings = [r for r in self.rings if self.t - r[0] < 1.3]

    # ------------------------------------------------------------- dibujo
    def _project(self, scale: float):
        R = _rot4(0, 3, self.a_xw) @ _rot4(1, 2, self.a_yz) @ _rot4(2, 3, self.a_zw) @ _rot4(0, 1, self.a_xy)
        v = self.verts @ R.T
        w = v[:, 3]
        p3 = v[:, :3] * (2.4 / (3.2 - w))[:, None]                    # perspectiva 4D -> 3D
        f3 = 5.0 / (5.0 - p3[:, 2])                                   # perspectiva 3D -> 2D
        return p3[:, :2] * f3[:, None] * scale + np.array([W / 2, H / 2], np.float32), w

    def _draw_lattice(self, layer: np.ndarray) -> None:
        """Túnel de líneas temporales: rieles que convergen y marcos que avanzan con el tiempo."""
        cx, cy, F = W / 2, H / 2, self.FOCAL
        z_near, z_far = 0.55, 10.0
        xs, ys = np.arange(-4, 5, dtype=np.float32), np.arange(-3, 4, dtype=np.float32)
        rail = (14, 42, 90)
        for x in xs:
            for y in ys:
                cv2.line(layer, (int(cx + x / z_near * F), int(cy + y / z_near * F)),
                         (int(cx + x / z_far * F), int(cy + y / z_far * F)), rail, 1)
        nf = 16
        for k in range(nf):
            u = (k / nf - self.tau * 2.0) % 1.0                       # al avanzar el tiempo, el túnel se desplaza
            z = z_near * (z_far / z_near) ** u
            b = smoothstep(z_near, 1.4, z) * (1.0 - 0.6 * z / z_far)
            if b < 0.08:
                continue
            col = (int(38 * b), int(105 * b), int(185 * b))
            s = F / z
            top, bot = int(cy - 3.5 * s), int(cy + 3.5 * s)
            left, right = int(cx - 4.5 * s), int(cx + 4.5 * s)
            for x in xs:
                px = int(cx + x * s)
                cv2.line(layer, (px, top), (px, bot), col, 1)
            for y in ys:
                py = int(cy + y * s)
                cv2.line(layer, (left, py), (right, py), col, 1)

    def _draw_memories(self, layer: np.ndarray) -> None:
        cx, cy, F = W / 2, H / 2, self.FOCAL
        aligned = self.aligned_index()
        for i in range(MEMORIES):
            if self.collected[i]:
                continue
            z = self.Z_ALIGNED + float(self.mem_tau[i] - self.tau) * self.Z_RANGE
            if not 0.7 < z < 9.5:
                continue
            px, py = int(cx + self.mem_xy[i, 0] / z * F), int(cy + self.mem_xy[i, 1] / z * F)
            r = int(clamp(60.0 / z, 4, 70))
            if i == aligned:
                pulse = 1.0 + 0.25 * math.sin(self.t * 10)
                cv2.circle(layer, (px, py), int(r * 1.5 * pulse), (255, 255, 255), -1, cv2.LINE_AA)
                cv2.circle(layer, (px, py), int(r * 2.4 * pulse), (255, 235, 140), 2, cv2.LINE_AA)
            else:
                cv2.circle(layer, (px, py), r, (255, 225, 120), -1, cv2.LINE_AA)
                cv2.circle(layer, (px, py), int(r * 1.8), (200, 160, 60), 1, cv2.LINE_AA)

    def _draw_hypercube(self, layer: np.ndarray) -> None:
        boost = 0.0
        if self.done_t is not None:
            boost = smoothstep(0.0, 1.5, self.t - self.done_t)
        pts_f, w = self._project(TESS_SCALE * (1.0 + 0.04 * math.sin(self.t * 2) + 0.3 * boost))
        pts = pts_f.astype(int)
        for i, j in self.edges:
            b = clamp(0.62 + 0.22 * (float(w[i]) + float(w[j])) / 2 + 0.3 * boost, 0.3, 1.0)
            cv2.line(layer, tuple(pts[i]), tuple(pts[j]), (int(175 * b), int(220 * b), int(255 * b)), 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(layer, tuple(p), 4, (255, 255, 255), -1, cv2.LINE_AA)

    def render(self, frame: np.ndarray) -> None:
        layer = np.zeros((H, W, 3), np.uint8)
        self._draw_lattice(layer)
        self._draw_memories(layer)
        self._draw_hypercube(layer)
        for t0, px, py in self.rings:                                 # ondas al sellar un recuerdo
            age = (self.t - t0) / 1.3
            cv2.circle(layer, (int(px), int(py)), int(age * 520), (int(255 * (1 - age)),) * 3, 3, cv2.LINE_AA)
        if self.done_t is not None and int((self.t - self.done_t) * 1.6) != int((self.t - self.done_t - 0.03) * 1.6):
            self.rings.append((self.t, W / 2, H / 2))                 # latido continuo al completar

        small = cv2.GaussianBlur(cv2.resize(layer, (W // 4, H // 4), interpolation=cv2.INTER_AREA), (0, 0), 2.2)
        glow = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
        frame[:] = cv2.convertScaleAbs(frame, alpha=0.14)             # la cámara queda casi apagada
        frame[:] = cv2.add(frame, layer)
        frame[:] = cv2.addWeighted(frame, 1.0, glow, 1.05, 0)


# ----------------------------------------------------------------------------
# 9. APLICACIÓN
# ----------------------------------------------------------------------------
class BlackHoleGame:
    def __init__(self):
        self.typo = Typography()
        self.hud = HUD(self.typo)
        self.stars = Starfield()
        self.warp = WarpField()
        self.glow = make_glow_sprite(256, (40, 140, 255), 0.55)
        self.navy_frame = np.full((H, W, 3), NAVY, np.uint8)
        self.white_frame = np.full((H, W, 3), 255, np.uint8)
        self.red_frame = np.full((H, W, 3), (0, 0, 170), np.uint8)
        self.cockpit_mask = self._make_cockpit_mask()

        self.anim_t = 0.0
        self.fps = 30.0
        self.show_skeleton = True
        self.override: Optional[str] = None            # gesto simulado con el teclado (L / puño / abierta)
        self.override_number: Optional[int] = None     # número simulado con el teclado
        self.l_hold = 0.0
        self.l_armed = False
        self.next_script: Optional[str] = None
        self.hand_pos: Optional[np.ndarray] = None
        self.hand_vel = np.zeros(2, np.float32)
        self.mode = "none"
        self._dt = 1 / 30
        self.reset_run()

    def reset_run(self) -> None:
        """Reinicia la secuencia completa (desafío -> portal -> tesseract)."""
        self.particles = ParticleSystem()
        self.challenge = ChallengeSystem()
        self.tess = TesseractScene()
        self.score = 0
        self.swallowed_total = 0
        self.mass = 1.0
        self.combo = 0
        self.last_swallow_t = -99.0
        self.pulse = 0.0
        self.fail_flash = 0.0
        self.popups: list = []
        self.shocks: list = []
        self.gate_rings: list = []
        self.phase = Phase.CHALLENGE
        self.phase_t = 0.0

    @staticmethod
    def _make_cockpit_mask() -> np.ndarray:
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        nx, ny = (xs - W / 2) / (W / 2), (ys - H / 2) / (H / 2)
        vig = np.clip(1 - 0.55 * np.clip(nx ** 2 + ny ** 2 - 0.35, 0, 1), 0.35, 1)
        m = (vig * np.where(ys % 3 == 0, 0.91, 1.0) * 255).astype(np.uint8)
        return cv2.merge([m, m, m])

    # --------------------------------------------------------------- derivados
    @property
    def r_horizon(self) -> float:
        return BH_RADIUS0 + min(20.0, 0.9 * math.sqrt(self.swallowed_total))

    @property
    def gm(self) -> float:
        return BH_GM * (1.0 + min(0.5, 0.004 * self.swallowed_total))

    @property
    def combo_mult(self) -> int:
        return 1 + min(self.combo, 12) // 3

    def _set_phase(self, phase: Phase) -> None:
        self.phase, self.phase_t = phase, 0.0

    # ------------------------------------------------------------------ lógica
    def _update_hand(self, dt: float, landmarks: Optional[np.ndarray], gesture: Optional[str],
                     number: Optional[int]) -> None:
        """Posición suavizada de la palma, velocidad de la mano y modo de interacción."""
        if landmarks is None:
            self.hand_pos, self.hand_vel, self.mode = None, np.zeros(2, np.float32), "none"
            return
        palm = landmarks[[0, 5, 9, 13, 17]].mean(axis=0).astype(np.float32)
        if self.hand_pos is None:
            self.hand_pos, self.hand_vel = palm, np.zeros(2, np.float32)
        else:
            new_pos = self.hand_pos + (palm - self.hand_pos) * 0.55          # suavizado anti-temblor
            vel = np.clip((new_pos - self.hand_pos) / max(dt, 1e-3), -1800, 1800)
            self.hand_vel = self.hand_vel * 0.6 + vel * 0.4
            self.hand_pos = new_pos
        if number in (1, 2, 3, 4) or gesture == GESTURE_L:
            self.mode = "none"                    # contando con los dedos o haciendo la L: sin campo
        elif gesture == GESTURE_FIST:
            self.mode = "grab"
        else:
            self.mode = "attract"

    def _update_l_hold(self, dt: float, gesture: Optional[str]) -> None:
        """La "L" debe mantenerse 2 s. Se ignora hasta soltarla una vez (evita rebotes entre programas)."""
        if not self.l_armed:
            self.l_hold = 0.0
            if gesture != GESTURE_L and self.anim_t > 1.0:
                self.l_armed = True
            return
        if gesture == GESTURE_L:
            self.l_hold += dt
        else:
            self.l_hold = max(0.0, self.l_hold - dt * 2.0)
        if self.l_hold >= L_HOLD_SECONDS:
            if (BASE_DIR / NEXT_SCRIPT).exists():
                self.next_script = NEXT_SCRIPT
            else:
                print(f"[aviso] No se encontró {NEXT_SCRIPT} en {BASE_DIR}")
                self.l_hold, self.l_armed = 0.0, False

    def _step_particles(self, dt: float, gm_mult: float, use_hand: bool = True) -> None:
        hand = self.hand_pos if use_hand else None
        mode = self.mode if use_hand else "none"
        vel = self.hand_vel if hand is not None else np.zeros(2, np.float32)
        for s in self.particles.update(dt, self.gm * gm_mult, self.r_horizon, hand, vel, mode):
            self._score_swallowed(s)

    def update(self, dt: float, landmarks: Optional[np.ndarray], gesture: Optional[str],
               number: Optional[int] = None) -> None:
        self._dt = dt
        self.anim_t += dt
        self.phase_t += dt
        self.pulse = max(0.0, self.pulse - dt * 1.6)
        self.fail_flash = max(0.0, self.fail_flash - dt * 1.8)
        self.shocks = [t for t in self.shocks if self.anim_t - t < 0.9]
        self.popups = [p for p in self.popups if self.anim_t - p[3] < 1.1]
        self.gate_rings = [t for t in self.gate_rings if self.anim_t - t < 1.2]
        if self.anim_t - self.last_swallow_t > COMBO_WINDOW:
            self.combo = 0

        self._update_l_hold(dt, gesture)
        self._update_hand(dt, landmarks, gesture, number)

        if self.phase == Phase.CHALLENGE:
            event = self.challenge.update(dt, None if gesture == GESTURE_L else number)
            if event == "ok":
                self.gate_rings.append(self.anim_t)
                self.pulse = 1.0
            elif event == "fail":
                self.fail_flash = 1.0
            self._step_particles(dt, 1.0)
            if self.challenge.complete:
                self._set_phase(Phase.PORTAL)
        elif self.phase == Phase.PORTAL:
            p = clamp(self.phase_t / T_PORTAL)
            self._step_particles(dt, 1.0 + 10.0 * p * p, use_hand=False)      # el agujero arrastra todo
            if self.phase_t >= T_PORTAL:
                self._set_phase(Phase.TESSERACT)
        else:
            self.tess.update(dt, self.hand_pos, gesture)

    def _score_swallowed(self, s: Swallowed) -> None:
        self.combo += 1
        self.last_swallow_t = self.anim_t
        mult = self.combo_mult * (2 if s.thrown else 1)
        points = KIND_VALUE[s.kind] * mult
        self.score += points
        self.swallowed_total += 1
        self.mass += 0.02 * (s.kind + 1)
        self.pulse = min(1.0, self.pulse + 0.5)
        self.shocks.append(self.anim_t)
        label = f"+{points}" + (f" x{mult}" if mult > 1 else "") + (" TIRO CERTERO" if s.thrown else "")
        self.popups.append([label, float(s.pos[0]), float(s.pos[1]), self.anim_t, KIND_COLOR[s.kind]])
        self.popups = self.popups[-6:]

    # ------------------------------------------------------------------ dibujo
    def _draw_hole_back(self, frame: np.ndarray, k: float = 1.0) -> None:
        """Halo y mitad trasera del disco de acreción (k > 1 acerca la cámara al agujero)."""
        t, r = self.anim_t, self.r_horizon * k
        cx, cy = int(CENTER[0]), int(CENTER[1])
        size = int(min(2400, (r + 150 * k) * 2 * (1 + 0.03 * math.sin(t * 2))))
        add_glow(frame, cv2.resize(self.glow, (size, size)), cx, cy, 0.55 + 0.35 * self.pulse)
        for i in range(11):
            rx = int(r + (16 + i * 10 + 2 * math.sin(t * 3 + i)) * k)
            col = hot_color(i / 10, 1.0 - 0.45 * i / 10 + 0.12 * math.sin(t * 3 + i))
            cv2.ellipse(frame, (cx, cy), (rx, max(1, int(rx * 0.26))), -10, 180, 360, col, 2, cv2.LINE_AA)

    def _draw_hole_front(self, frame: np.ndarray, k: float = 1.0) -> None:
        """Núcleo negro, mitad delantera del disco, puntos calientes y ondas de choque."""
        t, r = self.anim_t, self.r_horizon * k
        cx, cy = int(CENTER[0]), int(CENTER[1])
        cv2.circle(frame, (cx, cy), int(r), (0, 0, 0), -1, cv2.LINE_AA)
        for i in range(11):
            rx = int(r + (16 + i * 10 + 2 * math.sin(t * 3 + i)) * k)
            col = hot_color(i / 10, 1.0 - 0.45 * i / 10 + 0.12 * math.sin(t * 3 + i))
            cv2.ellipse(frame, (cx, cy), (rx, max(1, int(rx * 0.26))), -10, 0, 180, col, 2, cv2.LINE_AA)

        th = math.radians(-10)
        for j in range(6):                                                    # puntos calientes del disco
            a = math.radians((t * (55 + 20 * j) + j * 60) % 360)
            rx = r + (26 + j * 14) * k
            x, y = rx * math.cos(a), rx * 0.26 * math.sin(a)
            px, py = cx + x * math.cos(th) - y * math.sin(th), cy + x * math.sin(th) + y * math.cos(th)
            if math.sin(a) > 0 or math.hypot(px - cx, py - cy) > r:
                cv2.circle(frame, (int(px), int(py)), 3, (230, 250, 255), -1, cv2.LINE_AA)

        cv2.circle(frame, (cx, cy), int(r) + 1, (205, 235, 255), 2, cv2.LINE_AA)   # anillo de fotones
        for t0 in self.shocks:                                                     # ondas al tragar
            age = (self.anim_t - t0) / 0.9
            cv2.circle(frame, (cx, cy), int(r + (8 + age * 90) * k), hot_color(0.2 + age * 0.5, 1 - age), 2, cv2.LINE_AA)

    def _draw_gate_rings(self, frame: np.ndarray) -> None:
        """Un anillo por cada gesto completado, más una onda de luz en cada acierto."""
        cx, cy = int(CENTER[0]), int(CENTER[1])
        base = int(self.r_horizon + 150)
        for i in range(self.challenge.progress):
            a0 = (self.anim_t * (40 + 12 * i) + i * 72) % 360
            r = base + i * 16
            cv2.ellipse(frame, (cx, cy), (r, r), 0, a0, a0 + 250, CYAN if i % 2 == 0 else WHITE, 2, cv2.LINE_AA)
        for t0 in self.gate_rings:
            age = (self.anim_t - t0) / 1.2
            cv2.circle(frame, (cx, cy), int(base * 0.5 + age * 460), tuple(int(c * (1 - age)) for c in (255, 255, 200)),
                       3, cv2.LINE_AA)

    def _draw_particles(self, frame: np.ndarray) -> None:
        ps = self.particles
        pos = ps.pos.astype(int).tolist()
        tail = (ps.pos - ps.vel * 0.035).astype(int).tolist()
        for p, q, rad, kind, held in zip(pos, tail, ps.radius.tolist(), ps.kind.tolist(), ps.held.tolist()):
            if not (-20 < p[0] < W + 20 and -20 < p[1] < H + 20):
                continue
            col = KIND_COLOR[kind]
            cv2.line(frame, (q[0], q[1]), (p[0], p[1]), tuple(int(c * 0.55) for c in col), 1)
            cv2.circle(frame, (p[0], p[1]), int(rad), col, -1, cv2.LINE_AA)
            if held:
                cv2.circle(frame, (p[0], p[1]), int(rad) + 3, WHITE, 1, cv2.LINE_AA)

    def _draw_hand_field(self, frame: np.ndarray) -> None:
        if self.hand_pos is None:
            return
        cx, cy = int(self.hand_pos[0]), int(self.hand_pos[1])
        t = self.anim_t
        if self.mode == "attract":
            cv2.circle(frame, (cx, cy), int(HAND_RANGE), (90, 70, 30), 1, cv2.LINE_AA)
            for k in range(3):
                a = (t * 70 + k * 120) % 360
                cv2.ellipse(frame, (cx, cy), (70, 70), 0, a, a + 60, CYAN, 3, cv2.LINE_AA)
        elif self.mode == "grab":
            cv2.circle(frame, (cx, cy), int(GRAB_RADIUS), (60, 90, 160), 1, cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), 34, AMBER, 3, cv2.LINE_AA)
            for k in range(4):
                a = math.radians(k * 90 + 45 + t * 40)
                cv2.line(frame, (int(cx + 46 * math.cos(a)), int(cy + 46 * math.sin(a))),
                         (int(cx + 62 * math.cos(a)), int(cy + 62 * math.sin(a))), AMBER, 3, cv2.LINE_AA)

    def _draw_skeleton(self, frame: np.ndarray, pts: np.ndarray) -> None:
        p = pts.astype(int)
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, tuple(p[a]), tuple(p[b]), CYAN, 2, cv2.LINE_AA)
        for q in p:
            cv2.circle(frame, tuple(q), 4, WHITE, -1, cv2.LINE_AA)

    def _gesture_label(self, has_hand: bool, gesture: Optional[str], number: Optional[int]):
        if self.override or self.override_number:
            return "TECLADO", AMBER
        if number:
            return f"NÚMERO {number}", GREEN
        if gesture == GESTURE_L:
            return "GESTO L", AMBER
        if gesture == GESTURE_OPEN:
            return "MANO ABIERTA", GREEN
        if gesture == GESTURE_FIST:
            return "PUÑO CERRADO", AMBER
        return ("DETECTANDO…", TEXT_DIM) if has_hand else ("SIN MANO", RED)

    # --- fase 1 ---
    def _render_challenge(self, frame, has_hand, gesture, number) -> None:
        frame[:] = cv2.addWeighted(frame, 0.22, self.navy_frame, 0.78, 0)
        self.stars.draw(frame, self.anim_t)
        self._draw_hole_back(frame)
        self._draw_particles(frame)
        self._draw_hand_field(frame)
        self._draw_hole_front(frame)
        self._draw_gate_rings(frame)
        if self.fail_flash > 0.01:
            frame[:] = cv2.addWeighted(frame, 1.0, self.red_frame, 0.28 * self.fail_flash, 0)

    # --- fase 2 ---
    def _render_portal(self, frame) -> None:
        p = clamp(self.phase_t / T_PORTAL)
        frame[:] = cv2.addWeighted(frame, 0.12, self.navy_frame, 0.88, 0)
        self.warp.update_and_draw(frame, lerp(0.25, 3.4, p * p), self._dt)
        k = 1.0 + 9.0 * p ** 3
        self._draw_hole_back(frame, k)
        self._draw_hole_front(frame, k)
        if p > 0.78:                                                    # destello blanco final
            f = (p - 0.78) / 0.22
            frame[:] = cv2.addWeighted(frame, 1 - f, self.white_frame, f, 0)

    # --- fase 3 ---
    def _render_tesseract(self, frame) -> None:
        self.tess.render(frame)
        if self.phase_t < 1.2:                                          # destello al entrar
            f = 1.0 - self.phase_t / 1.2
            frame[:] = cv2.addWeighted(frame, 1 - f, self.white_frame, f, 0)

    def render(self, frame: np.ndarray, landmarks: Optional[np.ndarray], gesture: Optional[str],
               number: Optional[int] = None) -> np.ndarray:
        """Compone un fotograma a partir del frame de cámara (ya en W x H)."""
        has_hand = landmarks is not None
        g_label, g_color = self._gesture_label(has_hand, gesture, number)
        hud = self.hud

        if self.phase == Phase.CHALLENGE:
            self._render_challenge(frame, has_hand, gesture, number)
        elif self.phase == Phase.PORTAL:
            self._render_portal(frame)
        else:
            self._render_tesseract(frame)

        if self.show_skeleton and has_hand and not (self.phase == Phase.PORTAL and self.phase_t > T_PORTAL * 0.78):
            self._draw_skeleton(frame, landmarks)

        # ---- HUD según la fase ----
        hud.screen_corners(frame, CYAN)
        warning = None if has_hand or self.override or self.override_number else "SIN SEÑAL DE MANO · COLÓCALA FRENTE A LA CÁMARA"

        if self.phase == Phase.CHALLENGE:
            for text, x, y, t0, color in self.popups:
                age = (self.anim_t - t0) / 1.1
                self.typo.draw(frame, text, int(x), int(y - 40 * age), 18, color, bold=True, mono=True,
                               align="center", opacity=1.0 - age)
            status = {"attract": ("CAMPO ATRACTOR ACTIVO", CYAN), "grab": ("PINZA GRAVITATORIA", AMBER),
                      "none": ("PROTOCOLO DE ACCESO ACTIVO", CYAN)}[self.mode]
            hud.top_bar(frame, status[0], status[1], f"PUNTOS {self.score:,}".replace(",", "."))
            hud.stats(frame, self.score, self.combo_mult, self.particles.n_held, self.swallowed_total,
                      self.mass, g_label, g_color, self.fps)
            hud.challenge_panel(frame, self.challenge)
            hud.hint(frame, f"MUESTRA {self.challenge.target} CON LOS DEDOS Y MANTÉN EL GESTO", CYAN, warning)
        elif self.phase == Phase.PORTAL:
            hud.top_bar(frame, "ACCESO CONCEDIDO", GREEN, "CRUZANDO EL HORIZONTE")
            hud.banner(frame, "ACCESO CONCEDIDO", "CRUZANDO EL HORIZONTE DE SUCESOS…", GREEN)
        else:
            tess = self.tess
            aligned = tess.aligned_index()
            hud.top_bar(frame, "TESSERACT · BIBLIOTECA DEL TIEMPO", AMBER, f"RECUERDOS {tess.n_done}/{MEMORIES}")
            hud.tess_stats(frame, tess.tau, tess.n_done, math.degrees(tess.a_zw) % 360, g_label, g_color, self.fps)
            hud.timeline(frame, tess.tau, tess.mem_tau, tess.collected, aligned)
            if tess.done:
                hud.banner(frame, "MENSAJE ENTREGADO", "TODOS LOS RECUERDOS SELLADOS · SIMULACIÓN COMPLETADA", GREEN)
                hud.hint(frame, "HAZ LA L (2 s) PARA VOLVER AL MUSEO ESPACIAL", GREEN, warning)
            elif aligned is not None:
                hud.hint(frame, "INSTANTE ALINEADO · CIERRA EL PUÑO AHORA", GREEN, warning)
            else:
                hud.hint(frame, "MUEVE LA MANO (IZQ/DER) · PUÑO PARA SELLAR EL RECUERDO", AMBER, warning)

        hud.keys_help(frame)
        if self.l_hold > 0.05:
            hud.l_progress(frame, self.l_hold / L_HOLD_SECONDS, "MUSEO ESPACIAL")
        return cv2.multiply(frame, self.cockpit_mask, scale=1 / 255.0)

    # --------------------------------------------------------------- principal
    def run(self) -> Optional[str]:
        """Bucle principal. Devuelve el script a lanzar (o None si se cierra normalmente)."""
        cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            sys.exit(f"No se pudo abrir la cámara (índice {CAMERA_INDEX}). Prueba:  python agujero_negro.py --camera 1")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        try:
            detector = GestureDetector()
        except Exception as exc:
            cap.release()
            sys.exit(f"No se pudo iniciar MediaPipe: {exc}")

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_NAME, W, H)
        last, failures = time.perf_counter(), 0
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

                frame = fit_frame(cv2.flip(raw, 1))
                detector.process(frame)
                gesture = self.override or detector.stable
                number = self.override_number if self.override_number is not None else detector.stable_number
                self.update(dt, detector.landmarks, gesture, number)
                cv2.imshow(WINDOW_NAME, self.render(frame, detector.landmarks, gesture, number))
                if self.next_script:                           # gesto L mantenido: salir para volver a main.py
                    break

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("h"):
                    self.show_skeleton = not self.show_skeleton
                elif key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                    self.override_number = key - ord("0")
                elif key == ord("l"):
                    self.override = GESTURE_L
                elif key == ord("f"):
                    self.override = GESTURE_FIST
                elif key == ord("o"):
                    self.override = GESTURE_OPEN
                elif key == ord("c"):
                    self.override, self.override_number = None, None
                elif key == ord("r"):
                    self.reset_run()
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            cap.release()                 # libera la cámara para el siguiente programa
            detector.close()
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        return self.next_script


def launch_script(script: str, camera_index: int) -> None:
    """Lanza otro script de Python (mismo intérprete, multiplataforma) como proceso independiente."""
    time.sleep(0.4)   # margen para que el sistema libere del todo la cámara
    subprocess.Popen([sys.executable, str(BASE_DIR / script), "--camera", str(camera_index)],
                     cwd=str(BASE_DIR))


def main() -> None:
    next_script = BlackHoleGame().run()      # al volver, cámara y ventanas ya están cerradas
    if next_script:
        launch_script(next_script, CAMERA_INDEX)
    sys.exit()


if __name__ == "__main__":
    main()
