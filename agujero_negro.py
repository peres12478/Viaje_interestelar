#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AGUJERO NEGRO — laboratorio de gravedad controlado con la mano
==============================================================

Un agujero negro en el centro atrae polvo, hielo y fragmentos dorados. Tu mano
(vista por la cámara) es un segundo campo gravitatorio con el que puedes guiar
las partículas hacia el horizonte de sucesos para sumar puntos.

Gestos:
    MANO ABIERTA ... campo atractor: las partículas cercanas se sienten atraídas por tu palma
    PUÑO CERRADO ... pinza gravitatoria: atrapa partículas cercanas y las lleva contigo;
                     al abrir la mano las lanzas con la velocidad de tu mano
    GESTO "L" ...... (pulgar e índice en ángulo recto, mantenido 2 s) cierra este programa
                     y vuelve a abrir main.py (el Museo Espacial)

Puntuación:
    polvo 10 · hielo 25 · fragmento dorado 100, multiplicado por el combo (hasta x5)
    y por x2 si la partícula fue atrapada y lanzada por ti ("tiro certero").

Teclado (opcional):  Q / ESC salir · H esqueleto de la mano · L simular gesto L · C quitar simulación

Estructura esperada (la misma carpeta que main.py):
    museo_espacial/
    ├── main.py
    ├── agujero_negro.py
    └── assets/models/hand_landmarker.task   (se descarga solo si falta)
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
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
        self.stable: Optional[str] = None
        self.raw: Optional[str] = None
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
        else:
            self.landmarks, self.raw = None, None

        self.history.append(self.raw)
        for gesture, n in Counter(g for g in self.history if g).items():
            if n >= 5:                                   # voto por mayoría en los últimos 7 fotogramas
                self.stable = gesture
        if not any(self.history):
            self.stable = None

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
        self.typo.draw(frame, "GESTO L (2 s): VOLVER AL MUSEO", W - 28, H - 46, 12, AMBER, mono=True, align="right")
        self.typo.draw(frame, "Q salir · H mano · L/C teclado", W - 28, H - 24, 12, TEXT_DIM, mono=True, align="right")

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
# 8. APLICACIÓN
# ----------------------------------------------------------------------------
class BlackHoleGame:
    def __init__(self):
        self.typo = Typography()
        self.hud = HUD(self.typo)
        self.stars = Starfield()
        self.particles = ParticleSystem()
        self.glow = make_glow_sprite(256, (40, 140, 255), 0.55)
        self.navy_frame = np.full((H, W, 3), NAVY, np.uint8)
        self.cockpit_mask = self._make_cockpit_mask()

        # Estado de juego
        self.score = 0
        self.swallowed_total = 0
        self.mass = 1.0
        self.combo = 0
        self.last_swallow_t = -99.0
        self.pulse = 0.0
        self.popups = []            # [texto, x, y, tiempo_creación, color]
        self.shocks = []            # tiempos de creación de ondas de choque en el horizonte
        self.anim_t = 0.0
        self.fps = 30.0

        # Mano
        self.hand_pos: Optional[np.ndarray] = None
        self.hand_vel = np.zeros(2, np.float32)
        self.mode = "none"
        self.show_skeleton = True

        # Cambio de programa con el gesto "L"
        self.override: Optional[str] = None
        self.l_hold = 0.0
        self.l_armed = False
        self.next_script: Optional[str] = None

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

    # ------------------------------------------------------------------ lógica
    def _update_hand(self, dt: float, landmarks: Optional[np.ndarray], gesture: Optional[str]) -> None:
        """Posición suavizada de la palma, velocidad de la mano y modo de interacción."""
        if landmarks is None:
            self.hand_pos = None
            self.hand_vel *= 0.0
            self.mode = "none"
            return
        palm = landmarks[[0, 5, 9, 13, 17]].mean(axis=0).astype(np.float32)
        if self.hand_pos is None:
            self.hand_pos, self.hand_vel = palm, np.zeros(2, np.float32)
        else:
            new_pos = self.hand_pos + (palm - self.hand_pos) * 0.55          # suavizado anti-temblor
            vel = np.clip((new_pos - self.hand_pos) / max(dt, 1e-3), -1800, 1800)
            self.hand_vel = self.hand_vel * 0.6 + vel * 0.4
            self.hand_pos = new_pos
        if gesture == GESTURE_FIST:
            self.mode = "grab"
        elif gesture == GESTURE_L:
            self.mode = "none"
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

    def update(self, dt: float, landmarks: Optional[np.ndarray], gesture: Optional[str]) -> None:
        self.anim_t += dt
        self.pulse = max(0.0, self.pulse - dt * 1.6)
        self.shocks = [t for t in self.shocks if self.anim_t - t < 0.9]
        self.popups = [p for p in self.popups if self.anim_t - p[3] < 1.1]
        if self.anim_t - self.last_swallow_t > COMBO_WINDOW:
            self.combo = 0

        self._update_l_hold(dt, gesture)
        self._update_hand(dt, landmarks, gesture)

        hand_vel = self.hand_vel if self.hand_pos is not None else np.zeros(2, np.float32)
        for s in self.particles.update(dt, self.gm, self.r_horizon, self.hand_pos, hand_vel, self.mode):
            self._score_swallowed(s)

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
        self.popups = self.popups[-6:]                      # evita amontonar textos si caen muchas a la vez

    # ------------------------------------------------------------------ dibujo
    def _draw_hole_back(self, frame: np.ndarray) -> None:
        """Halo y mitad trasera del disco de acreción (detrás de las partículas)."""
        t, r = self.anim_t, self.r_horizon
        cx, cy = int(CENTER[0]), int(CENTER[1])
        size = int((r + 150) * 2 * (1 + 0.03 * math.sin(t * 2)))
        add_glow(frame, cv2.resize(self.glow, (size, size)), cx, cy, 0.55 + 0.35 * self.pulse)
        for i in range(11):
            rx = int(r + 16 + i * 10 + 2 * math.sin(t * 3 + i))
            col = hot_color(i / 10, 1.0 - 0.45 * i / 10 + 0.12 * math.sin(t * 3 + i))
            cv2.ellipse(frame, (cx, cy), (rx, int(rx * 0.26)), -10, 180, 360, col, 2, cv2.LINE_AA)

    def _draw_hole_front(self, frame: np.ndarray) -> None:
        """Núcleo negro, mitad delantera del disco, puntos calientes y ondas de choque."""
        t, r = self.anim_t, self.r_horizon
        cx, cy = int(CENTER[0]), int(CENTER[1])
        cv2.circle(frame, (cx, cy), int(r), (0, 0, 0), -1, cv2.LINE_AA)
        for i in range(11):
            rx = int(r + 16 + i * 10 + 2 * math.sin(t * 3 + i))
            col = hot_color(i / 10, 1.0 - 0.45 * i / 10 + 0.12 * math.sin(t * 3 + i))
            cv2.ellipse(frame, (cx, cy), (rx, int(rx * 0.26)), -10, 0, 180, col, 2, cv2.LINE_AA)

        # Puntos calientes que recorren el disco
        th = math.radians(-10)
        for k in range(6):
            a = math.radians((t * (55 + 20 * k) + k * 60) % 360)
            rx = r + 26 + k * 14
            x, y = rx * math.cos(a), rx * 0.26 * math.sin(a)
            px, py = cx + x * math.cos(th) - y * math.sin(th), cy + x * math.sin(th) + y * math.cos(th)
            if math.sin(a) > 0 or math.hypot(px - cx, py - cy) > r:      # no pintar si queda tras el núcleo
                cv2.circle(frame, (int(px), int(py)), 3, (230, 250, 255), -1, cv2.LINE_AA)

        cv2.circle(frame, (cx, cy), int(r) + 1, (205, 235, 255), 2, cv2.LINE_AA)     # anillo de fotones
        for t0 in self.shocks:                                                         # ondas al tragar
            age = (self.anim_t - t0) / 0.9
            cv2.circle(frame, (cx, cy), int(r + 8 + age * 90), hot_color(0.2 + age * 0.5, 1 - age), 2, cv2.LINE_AA)

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
            cv2.circle(frame, (cx, cy), int(HAND_RANGE), (90, 70, 30), 1, cv2.LINE_AA)       # alcance del campo
            for k in range(3):
                a = (t * 70 + k * 120) % 360
                cv2.ellipse(frame, (cx, cy), (70, 70), 0, a, a + 60, CYAN, 3, cv2.LINE_AA)
        elif self.mode == "grab":
            cv2.circle(frame, (cx, cy), int(GRAB_RADIUS), (60, 90, 160), 1, cv2.LINE_AA)
            cv2.circle(frame, (cx, cy), 34, AMBER, 3, cv2.LINE_AA)
            for k in range(4):                                                               # mira de la pinza
                a = math.radians(k * 90 + 45 + t * 40)
                cv2.line(frame, (int(cx + 46 * math.cos(a)), int(cy + 46 * math.sin(a))),
                         (int(cx + 62 * math.cos(a)), int(cy + 62 * math.sin(a))), AMBER, 3, cv2.LINE_AA)

    def _draw_skeleton(self, frame: np.ndarray, pts: np.ndarray) -> None:
        p = pts.astype(int)
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, tuple(p[a]), tuple(p[b]), CYAN, 2, cv2.LINE_AA)
        for q in p:
            cv2.circle(frame, tuple(q), 4, WHITE, -1, cv2.LINE_AA)

    def render(self, frame: np.ndarray, landmarks: Optional[np.ndarray], gesture: Optional[str]) -> np.ndarray:
        """Compone un fotograma a partir del frame de cámara (ya en W x H)."""
        # 1) Cámara oscurecida con tinte azul + estrellas
        frame[:] = cv2.addWeighted(frame, 0.22, self.navy_frame, 0.78, 0)
        self.stars.draw(frame, self.anim_t)

        # 2) Agujero negro y partículas (el orden hace que lo tragado desaparezca tras el núcleo)
        self._draw_hole_back(frame)
        self._draw_particles(frame)
        self._draw_hand_field(frame)
        self._draw_hole_front(frame)

        # 3) Mano
        if self.show_skeleton and landmarks is not None:
            self._draw_skeleton(frame, landmarks)

        # 4) Textos flotantes de puntos
        for text, x, y, t0, color in self.popups:
            age = (self.anim_t - t0) / 1.1
            self.typo.draw(frame, text, int(x), int(y - 40 * age), 18, color, bold=True, mono=True,
                           align="center", opacity=1.0 - age)

        # 5) HUD
        has_hand = landmarks is not None
        if self.override:
            g_label, g_color = "TECLADO: L", AMBER
        elif gesture == GESTURE_L:
            g_label, g_color = "GESTO L", AMBER
        elif gesture == GESTURE_OPEN:
            g_label, g_color = "MANO ABIERTA", GREEN
        elif gesture == GESTURE_FIST:
            g_label, g_color = "PUÑO CERRADO", AMBER
        else:
            g_label, g_color = ("DETECTANDO…", TEXT_DIM) if has_hand else ("SIN MANO", RED)

        status = {"attract": ("CAMPO ATRACTOR ACTIVO", CYAN), "grab": ("PINZA GRAVITATORIA", AMBER),
                  "none": ("SIN SEÑAL DE MANO" if not has_hand else "GESTO L EN CURSO",
                           RED if not has_hand else AMBER)}[self.mode]
        hints = {"attract": ("PUÑO: ATRAPAR · ABRE LA MANO PARA ATRAER PARTÍCULAS", CYAN),
                 "grab": ("LLÉVALAS AL AGUJERO Y ABRE LA MANO PARA LANZARLAS", AMBER),
                 "none": (("COLOCA LA MANO FRENTE A LA CÁMARA", AMBER) if not has_hand
                          else ("MANTÉN LA L PARA VOLVER AL MUSEO ESPACIAL", AMBER))}
        self.hud.screen_corners(frame, CYAN)
        self.hud.top_bar(frame, status[0], status[1], f"PUNTOS {self.score:,}".replace(",", "."))
        self.hud.stats(frame, self.score, self.combo_mult, self.particles.n_held, self.swallowed_total,
                       self.mass, g_label, g_color, self.fps)
        self.hud.hint(frame, hints[self.mode][0], hints[self.mode][1])
        self.hud.keys_help(frame)
        if self.l_hold > 0.05:
            self.hud.l_progress(frame, self.l_hold / L_HOLD_SECONDS, "MUSEO ESPACIAL")

        # 6) Viñeta y líneas de escaneo
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
                self.update(dt, detector.landmarks, gesture)
                cv2.imshow(WINDOW_NAME, self.render(frame, detector.landmarks, gesture))
                if self.next_script:                           # gesto L mantenido: salir para volver a main.py
                    break

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("h"):
                    self.show_skeleton = not self.show_skeleton
                elif key == ord("l"):
                    self.override = GESTURE_L
                elif key == ord("c"):
                    self.override = None
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
