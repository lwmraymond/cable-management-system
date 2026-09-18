"""Bounded same-room tray routing with explicit, estimated endpoint access leads."""

from __future__ import annotations

import hashlib
import heapq
import itertools
import math
from typing import TYPE_CHECKING


from app.exceptions import ConflictError, ValidationError
from app.fiber_models import PhysicalPortClaim
from app.models import CableTermination, Device, Pathway, PathwaySegment, Port, Rack
from app.schemas_scene_editor import RoutePreview
from app.services.cable_policy import cable_policy, media_family, require_cable_admission
from app.services.route_geometry import RouteGeometry, choose_portions, validate_portions

if TYPE_CHECKING:
    from app.services.scene_editor import SceneEditorService


class SceneRouteService:
    JOIN_TOLERANCE_M = 0.001
    MAX_SEGMENTS = 1000
    MAX_PORTS_PER_DEVICE = 4096
    MAX_EXPANSIONS = 5000

    def __init__(self, editor: SceneEditorService):
        self.editor, self.db, self.scene = editor, editor.db, editor.scene

    def _endpoints(self, port_a_id, port_b_id, media_type, project_id=None):
        if port_a_id == port_b_id:
            raise ValidationError("A cable needs two different ports")
        endpoints = []
        for port_id in (port_a_id, port_b_id):
            port = self.editor._get(Port, port_id)
            device = self.editor._get(Device, port.device_id)
            self.db.refresh(device)
            self.editor._scope(device.location_id, "cable:create", project_id)
            require_cable_admission(
                device.instance_overrides.get("cable_policy"), media_type, "Device"
            )
            if media_family(port.media_type) != media_family(media_type):
                raise ValidationError("Cable media is incompatible with an endpoint port")
            if port.status != "available":
                raise ConflictError("Endpoint port is not available")
            rack = self.editor._get(Rack, device.rack_id) if device.rack_id else None
            if rack and rack.location_id != device.location_id:
                raise ConflictError("Device rack and location are inconsistent")
            endpoints.append((port, device, rack))
        for model in (PhysicalPortClaim, CableTermination):
            if self.db.scalar(
                self.scene._query(model).where(model.port_id.in_([port_a_id, port_b_id])).limit(1)
            ):
                raise ConflictError("One or more ports are already physically terminated")
        return endpoints

    def _port_point(self, endpoint):
        port, device, rack = endpoint
        if rack is None:
            point = device.instance_overrides.get("position")
            if isinstance(point, dict) and all(
                isinstance(point.get(k), (int, float)) and math.isfinite(point[k])
                for k in ("x", "y", "z")
            ):
                return tuple(float(point[k]) for k in ("x", "y", "z"))
            return None
        if not all(
            math.isfinite(v)
            for v in (rack.position_x, rack.position_y, rack.position_z, rack.rotation)
        ):
            return None
        ports = self.db.scalars(
            self.scene._query(Port)
            .where(
                Port.device_id == device.id,
                Port.front_or_rear == port.front_or_rear,
            )
            .order_by(Port.position_index, Port.id)
            .limit(self.MAX_PORTS_PER_DEVICE + 1)
        ).all()
        if len(ports) > self.MAX_PORTS_PER_DEVICE:
            return None
        index = next((i for i, candidate in enumerate(ports) if candidate.id == port.id), 0)
        width, depth = max(0.5, rack.width_mm / 1000), max(0.45, rack.depth_mm / 1000)
        local_x = ((index + 0.5) / max(1, len(ports)) - 0.5) * width * 0.7
        local_y = (-1 if port.front_or_rear == "rear" else 1) * (depth / 2 - 0.047)
        angle = math.radians(rack.rotation)
        return (
            rack.position_x + local_x * math.cos(angle) + local_y * math.sin(angle),
            rack.position_y - local_x * math.sin(angle) + local_y * math.cos(angle),
            rack.position_z
            + 0.115
            + (device.start_u - 1) * 0.04445
            + (device.rack_units * 0.04445 - 0.003) / 2,
        )

    @staticmethod
    def _geometry(segment):
        geometry = RouteGeometry.from_segment(segment)
        return list(geometry.points), geometry.recorded_length

    def _selected(self, ids, location_id, media_type, project_id=None, excluded=()):
        if len(ids) > 100 or len(set(ids)) != len(ids):
            raise ValidationError("Route needs at most 100 distinct ordered segments")
        rows = []
        for segment_id in ids:
            segment = self.editor._get(PathwaySegment, segment_id)
            pathway = self.editor._get(Pathway, segment.pathway_id)
            self.db.refresh(pathway)
            self.editor._scope(pathway.location_id, "cable:create", project_id)
            if pathway.location_id != location_id:
                raise ValidationError(
                    "Tray routing requires endpoints and all route segments in one room"
                )
            if pathway.id in excluded:
                raise ValidationError("Custom route includes an excluded pathway")
            require_cable_admission(pathway.cable_policy, media_type, "Pathway")
            points, length = self._geometry(segment)
            rows.append((segment, pathway, points, length))
        return rows

    def _orientations(self, rows):
        if not rows:
            return []
        first = rows[0][2]
        orientations = [(first[0], first[-1]), (first[-1], first[0])]
        for _segment, _pathway, points, _length in rows[1:]:
            choices = []
            for beginning, ending in orientations:
                for a, b in ((points[0], points[-1]), (points[-1], points[0])):
                    if math.dist(ending, a) <= self.JOIN_TOLERANCE_M:
                        choices.append((beginning, b))
            orientations = list(dict.fromkeys(choices))
            if not orientations:
                raise ValidationError(
                    "Route segments are disconnected or not in continuous traversal order"
                )
        return orientations

    def validate_for_save(self, body):
        endpoints = self._endpoints(
            body.port_a_id, body.port_b_id, body.media_type, body.project_id
        )
        if body.route_segment_ids:
            if endpoints[0][1].location_id != endpoints[1][1].location_id:
                raise ValidationError(
                    "Cross-room tray routing needs a shared coordinate frame before saving"
                )
            rows = self._selected(
                body.route_segment_ids,
                endpoints[0][1].location_id,
                body.media_type,
                body.project_id,
            )
            self._orientations(rows)
            if body.route_portions is not None:
                validate_portions(
                    [row[0] for row in rows],
                    [portion.model_dump() for portion in body.route_portions],
                )
        elif body.route_portions:
            raise ValidationError("Route portions require ordered route segments")

    def _candidate(self, rows, point_a, point_b, label):
        if not rows:
            return {
                "id": "direct",
                "label": "端口直连 · 未记录线槽路径",
                "segment_ids": [],
                "segments": [],
                "length_m": round(max(0.001, math.dist(point_a, point_b)), 3),
                "warnings": ["直连长度为端口间直线估算，不代表实测敷设线路。"],
            }
        geometries = [RouteGeometry.from_segment(row[0]) for row in rows]
        portions, access = choose_portions(geometries, point_a, point_b)
        ids = [str(row[0].id) for row in rows]
        lengths = [
            geometry.adopted_length(start, end)
            for geometry, (start, end) in zip(geometries, portions)
        ]
        ranges = [
            {
                "segment_id": id,
                "start_offset_m": start,
                "end_offset_m": end,
                "geometry_hash": geometry.fingerprint,
            }
            for id, geometry, (start, end) in zip(ids, geometries, portions)
        ]
        return {
            "id": "route-" + hashlib.sha256(repr(ranges).encode()).hexdigest()[:16],
            "label": label,
            "segment_ids": ids,
            "route_portions": ranges,
            "segments": [
                {
                    "id": str(segment.id),
                    "pathway_id": str(pathway.id),
                    "pathway_identifier": pathway.identifier,
                    "name": segment.name,
                    "length_m": round(length, 3),
                    "full_length_m": round(geometry.recorded_length, 3),
                    "start_offset_m": start,
                    "end_offset_m": end,
                }
                for (segment, pathway, _points, _full), geometry, length, (start, end) in zip(
                    rows, geometries, lengths, portions
                )
            ],
            "length_m": round(sum(lengths) + access, 3),
            "warnings": [
                f"端口投影到线槽内接入，接入直线估算 {access:.2f} m；局部线槽长度按几何占比折算登记值，尚非现场测量。",
                "线槽之间仍仅连接已登记段端点（容差 1 mm），不会把相交或断开的线槽自动接通。",
            ],
        }

    @staticmethod
    def _distance_to_segment(point, points):
        distances = []
        for a, b in zip(points, points[1:]):
            delta = [y - x for x, y in zip(a, b)]
            size = sum(x * x for x in delta)
            ratio = (
                0
                if size == 0
                else max(0, min(1, sum((p - x) * d for p, x, d in zip(point, a, delta)) / size))
            )
            distances.append(math.dist(point, tuple(x + ratio * d for x, d in zip(a, delta))))
        return min(distances)

    def preview(self, body: RoutePreview):
        endpoints = self._endpoints(body.port_a_id, body.port_b_id, body.media_type)
        location_id = endpoints[0][1].location_id
        for pathway_id in body.excluded_pathway_ids:
            excluded_pathway = self.editor._get(Pathway, pathway_id)
            self.editor._scope(excluded_pathway.location_id, "cable:create")
        if location_id != endpoints[1][1].location_id:
            if body.route_segment_ids:
                raise ValidationError("Cross-room tray routing needs a shared coordinate frame")
            return {
                "candidates": [],
                "warnings": ["两端位于不同房间，尚无共同坐标系；未生成跨房间猜测路由。"],
            }
        points = [self._port_point(endpoint) for endpoint in endpoints]
        if any(point is None for point in points):
            return {"candidates": [], "warnings": ["端口缺少可靠空间位置，无法估算接入路径。"]}
        point_a, point_b = points
        if body.route_segment_ids is not None:
            rows = self._selected(
                body.route_segment_ids,
                location_id,
                body.media_type,
                excluded=set(body.excluded_pathway_ids),
            )
            return {
                "candidates": [self._candidate(rows, point_a, point_b, "自定义连续线槽路线")],
                "warnings": [],
            }
        pathways = self.db.scalars(
            self.scene._query(Pathway)
            .where(Pathway.location_id == location_id, Pathway.id.not_in(body.excluded_pathway_ids))
            .order_by(Pathway.id)
            .limit(self.MAX_SEGMENTS + 1)
        ).all()
        if len(pathways) > self.MAX_SEGMENTS:
            return {
                "candidates": [],
                "warnings": ["当前房间线槽数量超过搜索上限，请排除部分线槽后重试。"],
            }
        allowed = {
            row.id: row
            for row in pathways
            if row.id not in body.excluded_pathway_ids
            and cable_policy(row.cable_policy)["allows_cables"]
            and media_family(body.media_type) in cable_policy(row.cable_policy)["allowed_media"]
        }
        segments = self.db.scalars(
            self.scene._query(PathwaySegment)
            .where(PathwaySegment.pathway_id.in_(allowed))
            .order_by(PathwaySegment.id)
            .limit(self.MAX_SEGMENTS + 1)
        ).all()
        if len(segments) > self.MAX_SEGMENTS:
            return {
                "candidates": [],
                "warnings": ["当前房间线槽段超过 1000，请排除部分线槽后重试。"],
            }
        rows, warnings = [], []
        for segment in segments:
            try:
                geometry, length = self._geometry(segment)
                rows.append((segment, allowed[segment.pathway_id], geometry, length))
            except ValidationError:
                if not warnings:
                    warnings.append("部分线槽缺少有效几何，未用于推荐。")
        if not rows:
            return {
                "candidates": [self._candidate([], point_a, point_b, "直连")],
                "warnings": [*warnings, "没有允许当前介质且具有有效几何的线槽。"],
            }
        distances_a = [self._distance_to_segment(point_a, row[2]) for row in rows]
        distances_b = [self._distance_to_segment(point_b, row[2]) for row in rows]
        starts = {i for i, value in enumerate(distances_a) if value <= min(distances_a) + 0.05}
        goals = {i for i, value in enumerate(distances_b) if value <= min(distances_b) + 0.05}
        nodes, adjacency, ends = [], {}, []

        def node(point):
            for index, known in enumerate(nodes):
                if math.dist(point, known) <= self.JOIN_TOLERANCE_M:
                    return index
            nodes.append(point)
            return len(nodes) - 1

        for index, row in enumerate(rows):
            a, b = node(row[2][0]), node(row[2][-1])
            ends.append((a, b))
            adjacency.setdefault(a, []).append((index, b))
            adjacency.setdefault(b, []).append((index, a))
        heap, counter = [], itertools.count()
        for index in sorted(starts):
            a, b = ends[index]
            geometry = RouteGeometry.from_segment(rows[index][0])
            entry, access = geometry.project(point_a)
            for exit_node, offset in ((a, 0.0), (b, geometry.length)):
                heapq.heappush(
                    heap,
                    (
                        access + geometry.adopted_length(entry, offset),
                        next(counter),
                        exit_node,
                        (index,),
                        frozenset((a, b)),
                    ),
                )
        candidates, seen, expansions = [], set(), 0
        while heap and expansions < self.MAX_EXPANSIONS and len(candidates) < 12:
            cost, _serial, end, path, visited = heapq.heappop(heap)
            expansions += 1
            if path[-1] in goals and path not in seen:
                seen.add(path)
                candidates.append(
                    self._candidate([rows[i] for i in path], point_a, point_b, "连续线槽候选")
                )
            if len(path) >= 100:
                continue
            for index, next_node in adjacency.get(end, []):
                if index in path or next_node in visited or len(heap) >= 10000:
                    continue
                try:
                    self._orientations([rows[i] for i in (*path, index)])
                except ValidationError:
                    continue
                heapq.heappush(
                    heap,
                    (
                        cost + rows[index][3],
                        next(counter),
                        next_node,
                        (*path, index),
                        visited | {next_node},
                    ),
                )
        if expansions >= self.MAX_EXPANSIONS:
            warnings.append("已达到候选搜索上限；可排除无关线槽缩小范围。")
        candidates.sort(key=lambda item: (item["length_m"], item["id"]))
        for index, candidate in enumerate(candidates[:3], start=1):
            candidate["label"] = f"线槽路线 {index} · 估算 {candidate['length_m']:.2f} m"
        if not candidates:
            warnings.append("两端最近的合规线槽之间没有连续连接；未跨越断点拼接路线。")
        return {"candidates": candidates[:3], "warnings": warnings}
