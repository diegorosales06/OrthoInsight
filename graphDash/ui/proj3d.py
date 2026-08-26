"""Minimal software 3D projection for the arch view.

No OpenGL and no Qt here -- just enough vector math to orbit a camera around a
target and project world points into normalized image coordinates. The arch
view draws ~16 teeth and a handful of arrows per frame, which is far cheaper in
pure Python than adding a PyOpenGL dependency the Pi image doesn't carry.

World frame (also the frame the arch is built in):

    +x  viewer's right in the default occlusal view (the patient's left)
    +y  posterior -- toward the back of the mouth
    +z  occlusal  -- up, out of the occlusal plane

The arch itself lies in the z = 0 plane, so tooth crowns extrude toward +z.

`Camera.projector()` snapshots the camera basis once per frame; `project()`
returns `(u, v, depth)` where u/v are perspective-divided image coordinates
(dimensionless) and depth is the distance along the view axis. Callers pick
their own pixels-per-unit scale, which lets the view fit-to-pane after
projecting without re-deriving the basis.
"""

import math


# ---- vector helpers (plain 3-tuples) ----

def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vnorm(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def vunit(a):
    n = vnorm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def vmad(a, b, s):
    """a + b * s -- the inner loop of every local-to-world transform here."""
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)


class Projector:
    """Frozen camera basis; projects world points to image coordinates."""

    __slots__ = ("eye", "right", "up", "fwd")

    def __init__(self, eye, right, up, fwd):
        self.eye = eye
        self.right = right
        self.up = up
        self.fwd = fwd

    def project(self, pt):
        """World point -> (u, v, depth). depth is clamped positive so a point
        level with or behind the eye degrades instead of dividing by zero."""
        vx = pt[0] - self.eye[0]
        vy = pt[1] - self.eye[1]
        vz = pt[2] - self.eye[2]
        f, r, u = self.fwd, self.right, self.up
        depth = vx * f[0] + vy * f[1] + vz * f[2]
        if depth < 1e-4:
            depth = 1e-4
        return (
            (vx * r[0] + vy * r[1] + vz * r[2]) / depth,
            (vx * u[0] + vy * u[1] + vz * u[2]) / depth,
            depth,
        )


class Camera:
    """Orbiting perspective camera.

    `yaw` spins around the target's vertical (+z) axis; `pitch` is the elevation
    above the occlusal plane, so pitch = 90 deg looks straight down (the classic
    occlusal view) and pitch = 0 sits in the plane. `distance` is in world units
    and only affects perspective strength -- the view fits the pane by scaling
    the projected coordinates, not by dollying the camera.
    """

    PITCH_MIN = math.radians(-5.0)
    PITCH_MAX = math.radians(89.5)

    def __init__(self, target=(0.0, 0.0, 0.0), distance=4.0,
                 yaw=0.0, pitch=math.radians(38.0)):
        self.target = target
        self.distance = distance
        self.yaw = yaw
        self.pitch = self._clamp_pitch(pitch)

    @staticmethod
    def _clamp_pitch(pitch):
        return max(Camera.PITCH_MIN, min(Camera.PITCH_MAX, pitch))

    def set_orientation(self, yaw, pitch):
        self.yaw = yaw
        self.pitch = self._clamp_pitch(pitch)

    def orbit(self, dyaw, dpitch):
        self.yaw = (self.yaw + dyaw) % (2 * math.pi)
        self.pitch = self._clamp_pitch(self.pitch + dpitch)

    def eye(self):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        d = self.distance
        return vadd(self.target, (d * cp * sy, -d * cp * cy, d * sp))

    def projector(self):
        """Snapshot the basis for one frame's worth of projections.

        `right` is derived analytically from yaw alone, so the basis stays
        well-conditioned at pitch = 90 deg where a cross product against world
        +z would collapse.
        """
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        fwd = (-cp * sy, cp * cy, -sp)
        right = (cy, sy, 0.0)
        up = vcross(right, fwd)      # (-sy*sp, cy*sp, cp): world +z at pitch 0
        return Projector(self.eye(), right, up, fwd)
