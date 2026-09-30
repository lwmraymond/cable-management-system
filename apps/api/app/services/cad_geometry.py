"""Exchange geometry is room-local metres, Z-up; never reconstructed from meshes."""

from __future__ import annotations

import hashlib
import json
import math

from app.exceptions import ValidationError

PROFILE = "CMS-CAD-1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_OBJECTS = 20000
MAX_POINTS = 512
UNIT_SCALE = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}


def normalized(value):
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > 1e9:
            raise ValidationError("CAD contains invalid or excessive coordinates")
        rounded = round(value, 7) + 0.0
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalized(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(
            normalized(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def point(value):
    if len(value) != 3 or any(
        isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
        for v in value
    ):
        raise ValidationError("CAD point must contain three finite coordinates")
    return normalized([float(v) for v in value])


def points(values):
    if not 2 <= len(values) <= MAX_POINTS:
        raise ValidationError("CAD polyline must have 2–512 vertices")
    result = [point(v) for v in values]
    if sum(math.dist(a, b) for a, b in zip(result, result[1:])) <= 1e-7:
        raise ValidationError("CAD polyline has zero length")
    return result


def pose(matrix, scale=1.0):
    """Column-vector affine matrix -> application clockwise rack rotation."""
    axes = [[float(matrix[r][c]) for r in range(3)] for c in range(3)]
    if any(abs(math.dist([0, 0, 0], a) - 1) > 1e-6 for a in axes):
        raise ValidationError("Scaled CAD placements are outside the supported profile")
    if any(abs(v) > 1e-6 for v in [axes[0][2], axes[1][2], axes[2][0], axes[2][1], axes[2][2] - 1]):
        raise ValidationError("CAD placements must preserve the Z-up vertical axis")
    angle = -math.degrees(math.atan2(axes[0][1], axes[0][0]))
    if abs(axes[1][0] + axes[0][1]) > 1e-6 or abs(axes[1][1] - axes[0][0]) > 1e-6:
        raise ValidationError("Mirrored or sheared CAD placements are unsupported")
    return {
        "position": point([float(matrix[i][3]) * scale for i in range(3)]),
        "rotation": normalized((angle + 180) % 360 - 180),
    }


def rotation(value):
    return normalized((float(value) + 180) % 360 - 180)


def parsed_document(snapshot_id, objects, references=0):
    if len(objects) + references > MAX_OBJECTS:
        raise ValidationError("CAD object limit exceeded; export a smaller room")
    ids = [obj["id"] for obj in objects]
    if len(ids) != len(set(ids)):
        raise ValidationError("Duplicate CAD object IDs; copied objects must remain references")
    return normalized({"snapshot_id": snapshot_id, "objects": objects, "references": references})
