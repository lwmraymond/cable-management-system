"""Metre offsets on immutable-at-preview pathway polylines; no invented junctions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from app.exceptions import ConflictError, ValidationError

Point = tuple[float, float, float]
JOIN_TOLERANCE_M = 0.001


@dataclass(frozen=True)
class RouteGeometry:
    points: tuple[Point, ...]
    offsets: tuple[float, ...]
    recorded_length: float
    fingerprint: str

    @classmethod
    def from_segment(cls, segment):
        if not 2 <= len(segment.coordinates) <= 512:
            raise ValidationError("Selected route segment has no valid spatial geometry")
        points = []
        for value in segment.coordinates:
            if not isinstance(value, dict):
                raise ValidationError("Selected route segment has invalid spatial geometry")
            point = tuple(value.get(k, 0 if k == "z" else None) for k in ("x", "y", "z"))
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in point
            ):
                raise ValidationError("Selected route segment has non-finite spatial geometry")
            points.append(tuple(float(v) for v in point))
        offsets = [0.0]
        for a, b in zip(points, points[1:]):
            offsets.append(offsets[-1] + math.dist(a, b))
        if not math.isfinite(offsets[-1]) or offsets[-1] <= 0:
            raise ValidationError("Selected route segment has zero spatial length")
        recorded = segment.length_m
        length = (
            recorded
            if isinstance(recorded, (int, float))
            and not isinstance(recorded, bool)
            and math.isfinite(recorded)
            and recorded > 0
            else offsets[-1]
        )
        length = float(length)
        fingerprint = hashlib.sha256(
            json.dumps([points, length], separators=(",", ":")).encode()
        ).hexdigest()
        return cls(tuple(points), tuple(offsets), float(length), fingerprint)

    @property
    def length(self):
        return self.offsets[-1]

    def at(self, offset):
        if not math.isfinite(offset) or not 0 <= offset <= self.length + 1e-7:
            raise ValidationError("Route portion offset is outside segment geometry")
        offset = min(offset, self.length)
        for i in range(1, len(self.points)):
            size = self.offsets[i] - self.offsets[i - 1]
            if size > 0 and offset <= self.offsets[i]:
                ratio = (offset - self.offsets[i - 1]) / size
                return tuple(
                    a + ratio * (b - a) for a, b in zip(self.points[i - 1], self.points[i])
                )
        return self.points[-1]

    def clip(self, start, end):
        low, high = sorted((min(start, self.length), min(end, self.length)))
        points = [
            self.at(low),
            *[p for p, offset in zip(self.points, self.offsets) if low < offset < high],
            self.at(high),
        ]
        if start > end:
            points.reverse()
        return [dict(zip(("x", "y", "z"), point)) for point in points]

    def project(self, point):
        choices = []
        for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
            delta = tuple(y - x for x, y in zip(a, b))
            size = sum(v * v for v in delta)
            if size == 0:
                continue
            ratio = max(0, min(1, sum((p - x) * d for p, x, d in zip(point, a, delta)) / size))
            projected = tuple(x + ratio * d for x, d in zip(a, delta))
            offset = self.offsets[i] + ratio * math.sqrt(size)
            choices.append((math.dist(point, projected), offset))
        distance, offset = min(choices)
        return offset, distance

    def adopted_length(self, start, end):
        return abs(end - start) / self.length * self.recorded_length


def choose_portions(geometries, point_a, point_b):
    """Nearest entry/exit projections, then cheapest valid orientation of the given order."""
    start, access_a = geometries[0].project(point_a)
    end, access_b = geometries[-1].project(point_b)
    if len(geometries) == 1:
        return [(start, end)], access_a + access_b
    first = geometries[0]
    states = [
        (first.adopted_length(start, exit), first.at(exit), [(start, exit)])
        for exit in (0.0, first.length)
    ]
    for index, geometry in enumerate(geometries[1:], start=1):
        next_states = []
        for entry in (0.0, geometry.length):
            exit = end if index == len(geometries) - 1 else geometry.length - entry
            candidates = [
                (
                    cost + geometry.adopted_length(entry, exit),
                    geometry.at(exit),
                    [*parts, (entry, exit)],
                )
                for cost, previous_end, parts in states
                if math.dist(previous_end, geometry.at(entry)) <= JOIN_TOLERANCE_M
            ]
            if candidates:
                next_states.append(min(candidates, key=lambda item: (item[0], item[2])))
        states = next_states
        if not states:
            raise ValidationError(
                "Route segments are disconnected or not in continuous traversal order"
            )
    return min(states, key=lambda item: (item[0], item[2]))[2], access_a + access_b


def validate_portions(segments, portions):
    """Revalidate every client supplied range; only route ends may join within a segment."""
    if len(segments) != len(portions) or len(segments) > 100:
        raise ValidationError("Route portions must match all ordered route segments")
    result, previous_end = [], None
    for index, (segment, portion) in enumerate(zip(segments, portions)):
        if str(portion["segment_id"]) != str(segment.id):
            raise ValidationError("Route portions must match the ordered route segment IDs")
        geometry = RouteGeometry.from_segment(segment)
        if portion["geometry_hash"] != geometry.fingerprint:
            raise ConflictError("Tray geometry changed; preview the route again before saving")
        start, end = portion["start_offset_m"], portion["end_offset_m"]
        a, b = geometry.at(start), geometry.at(end)
        # Middle junctions remain explicit registered segment endpoints, never intersections.
        if index and min(start, abs(geometry.length - start)) > 1e-7:
            raise ValidationError("An intermediate route joint must use a segment endpoint")
        if index < len(segments) - 1 and min(end, abs(geometry.length - end)) > 1e-7:
            raise ValidationError("An intermediate route joint must use a segment endpoint")
        if previous_end is not None and math.dist(previous_end, a) > JOIN_TOLERANCE_M:
            raise ValidationError("Route portions are disconnected or reversed")
        previous_end = b
        result.append(
            {
                "segment_id": segment.id,
                "start_offset_m": min(start, geometry.length),
                "end_offset_m": min(end, geometry.length),
                "geometry_hash": geometry.fingerprint,
            }
        )
    return result


def saved_portion_geometry(segment, route):
    """Do not present a saved range as current geometry after its source changes."""
    try:
        geometry = RouteGeometry.from_segment(segment)
        geometry.at(route.start_offset_m)
        geometry.at(route.end_offset_m)
        return geometry if geometry.fingerprint == route.geometry_hash else None
    except (ValidationError, TypeError):
        return None
