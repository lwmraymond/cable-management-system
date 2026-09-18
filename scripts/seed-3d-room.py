#!/usr/bin/env python3
"""Create an additive, repeatable server-room example through a local demo API.

Run after the scene editing API is available:
    .venv/bin/python scripts/seed-3d-room.py
This script never opens the database, changes existing records, or deletes data.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
ROOM_IDENTIFIER = "DEMO-SR-01"
RACK_PREFIX = "DEMO-SR-R"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("The local demo API must not redirect requests")


class DemoApi:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("--base-url must be a loopback HTTP(S) origin without credentials")
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 80, type=socket.SOCK_STREAM)
        if not addresses or not all(ipaddress.ip_address(row[4][0]).is_loopback for row in addresses):
            raise ValueError("Localhost must resolve only to loopback addresses")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}), NoRedirects())
        self.headers = {"Accept": "application/json"}
        self.context = self.request("/demo/context")
        for key in ("tenant_id", "owner_id", "project_id", "location_id"):
            uuid.UUID(self.context[key])
        self.headers.update({
            "X-Tenant-ID": self.context["tenant_id"],
            "X-Actor-ID": self.context["owner_id"],
            "X-Project-ID": self.context["project_id"],
        })

    def request(self, path: str, body=None, *, method=None):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if body is not None else None
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
                if len(payload) > 16 * 1024 * 1024:
                    raise RuntimeError("Demo API response exceeded the bounded scene size")
                return json.loads(payload)
        except HTTPError as exc:
            detail = exc.read(1500).decode(errors="replace")
            raise RuntimeError(f"{request.get_method()} {path}: HTTP {exc.code}: {detail}") from None

    def scope(self, location_id: str):
        self.headers["X-Location-ID"] = location_id

    def scene(self):
        scene = self.request("/scene")
        if scene.get("truncated"):
            raise RuntimeError("Scene was truncated; refusing incomplete idempotency checks")
        return scene


def indexed(rows, key="identifier"):
    result = {}
    for row in rows:
        if row[key] in result:
            raise RuntimeError(f"Duplicate {key}: {row[key]}")
        result[row[key]] = row
    return result


def require_fields(row, expected, description):
    for key, value in expected.items():
        actual = row.get(key)
        equal = math.isclose(actual, value, abs_tol=1e-7) if isinstance(value, (float, int)) and isinstance(actual, (float, int)) else actual == value
        if not equal:
            raise RuntimeError(f"Existing {description} differs at {key}; nothing was overwritten")


def template_ports(template):
    return sum(int(part.get("count", 0)) for part in template.get("port_blueprint", [])
               if part.get("face", "front") == "front"
               and part.get("media_type", "copper").lower() == "copper"
               and part.get("connector_type", "RJ45").upper() == "RJ45")


def choose_templates(api):
    templates = api.request("/device-templates")
    result = {}
    for kind, units, minimum_ports in (("patch_panel", 1, 1), ("switch", 1, 8), ("server", 2, 4)):
        candidates = [row for row in templates if row["device_type"] == kind
                      and row["rack_units"] == units and template_ports(row) >= minimum_ports
                      and row["width_mm"] <= 600 and row["depth_mm"] <= 1000]
        if candidates:
            result[kind] = sorted(candidates, key=lambda row: (row["manufacturer"] != "SIM Reference", row["model"], row["id"]))[0]
        elif kind == "server":
            result[kind] = api.request("/device-templates", {
                "manufacturer": "SIM Demo", "model": "DEMO-SR-2U-4RJ45", "device_type": "server",
                "rack_units": 2, "width_mm": 482, "depth_mm": 700,
                "port_blueprint": [{"count": 4, "prefix": "ETH", "face": "front", "connector_type": "RJ45", "media_type": "copper"}],
            })
        else:
            raise RuntimeError(f"No suitable {kind} template; run the baseline demo seed first")
    return result


def ensure_room(api):
    locations = api.request("/locations")
    by_id = {row["id"]: row for row in locations}
    original = by_id.get(api.context["location_id"])
    floor = by_id.get(original.get("parent_id")) if original else None
    if not floor or floor["location_type"] != "floor":
        raise RuntimeError("The original demo telecom room has no visible parent floor")
    api.scope(floor["id"])
    room = indexed(locations).get(ROOM_IDENTIFIER)
    expected = {"parent_id": floor["id"], "location_type": "data_hall"}
    if room:
        require_fields(room, expected, ROOM_IDENTIFIER)
        require_fields(room["dimensions"], {"width_m": 8.0, "depth_m": 7.0, "height_m": 3.6}, "room dimensions")
    else:
        room = api.request("/scene/rooms", {
            "parent_id": floor["id"], "identifier": ROOM_IDENTIFIER,
            "name": "Server Room · 3D 示例", "kind": "server_room", "width_m": 8, "depth_m": 7, "height_m": 3.6,
        })
    api.scope(room["id"])
    return room, floor


def ensure_racks(api, room):
    expected_ids = [f"{RACK_PREFIX}-{index:02d}" for index in range(1, 5)]
    existing = indexed(api.scene()["racks"], "rack_identifier")
    present = [identifier for identifier in expected_ids if identifier in existing]
    if present and len(present) != 4:
        raise RuntimeError("The example rack batch is incomplete; refusing to overwrite or renumber existing racks")
    if not present:
        rows = api.request("/scene/racks", {
            "location_id": room["id"], "identifier_prefix": RACK_PREFIX, "name_prefix": "SR 机柜",
            "count": 4, "columns": 2, "position_x": 2, "position_y": 2, "gap_m": 1.8,
            "rotation": 0, "width_mm": 600, "depth_mm": 1000, "height_u": 42,
        })["racks"]
        existing.update(indexed(rows, "rack_identifier"))
    racks = [existing[identifier] for identifier in expected_ids]
    for index, rack in enumerate(racks):
        require_fields(rack, {"location_id": room["id"], "width_mm": 600, "depth_mm": 1000, "height_u": 42,
                             "position_x": 2 + index % 2 * 2.4, "position_y": 2 + index // 2 * 2.8,
                             "position_z": 0, "rotation": 0}, rack["rack_identifier"])
    return racks


def ensure_devices(api, racks, templates):
    existing = indexed(api.scene()["devices"])
    devices = {}
    for index, rack in enumerate(racks):
        for suffix, kind, start, name in (("PP", "patch_panel", 40, "配线架"), ("SW", "switch", 38, "接入交换机"), ("SRV", "server", 10 if index % 2 == 0 else 14, "2U 服务器")):
            identifier = rack["rack_identifier"] + "-" + suffix
            expected = {"rack_id": rack["id"], "device_type": kind, "start_u": start, "face": "front", "rack_units": templates[kind]["rack_units"]}
            if identifier in existing:
                row = existing[identifier]
                require_fields(row, expected, identifier)
            else:
                row = api.request("/scene/devices", {"rack_id": rack["id"], "template_id": templates[kind]["id"],
                                  "identifier": identifier, "name": f"SR {index + 1:02d} · {name}", "start_u": start, "face": "front"})
            devices[index, suffix] = row
    return devices


def ensure_pathways(api, room):
    def point(x, y):
        return {"x": x, "y": y, "z": 3.0}
    definitions = [
        ("TRAY-A", "tray", [[point(2, 2.8), point(1.3, 2.8)], [point(2, 2.8), point(4.4, 2.8)]]),
        ("TRAY-B", "tray", [[point(1.3, 5.6), point(2, 5.6)], [point(2, 5.6), point(4.4, 5.6)]]),
        ("TRAY-LINK", "ladder", [[point(1.3, 2.8), point(1.3, 5.6)]]),
    ]
    existing = indexed(api.scene()["pathways"])
    for suffix, kind, coordinates in definitions:
        identifier = "DEMO-SR-" + suffix
        if identifier not in existing:
            api.request("/scene/pathways", {"location_id": room["id"], "identifier": identifier,
                "name": suffix, "pathway_type": kind, "capacity_area_mm2": 12000,
                "segments": [{"sequence": index, "name": f"{suffix}-{index:02d}", "coordinates": points} for index, points in enumerate(coordinates, 1)]})
    existing = indexed(api.scene()["pathways"])
    result = {}
    for suffix, kind, coordinates in definitions:
        row = existing["DEMO-SR-" + suffix]
        require_fields(row, {"location_id": room["id"], "type": kind}, row["identifier"])
        segments = sorted(row["segments"], key=lambda segment: segment["sequence"])
        if [segment["coordinates"] for segment in segments] != coordinates:
            raise RuntimeError(f"Existing {row['identifier']} geometry differs; nothing was overwritten")
        result[suffix] = segments
    return result


def ensure_cables(api, devices, pathways):
    scene = api.scene()
    existing = indexed(scene["cables"])
    ports = scene["ports"]
    occupied = {port["id"] for port in ports if port["occupied"]}
    plans = []
    for index in range(4):
        plans.extend([((index, "SRV"), (index, "SW"), []), ((index, "PP"), (index, "SW"), [])])
    a, b, link = pathways["TRAY-A"], pathways["TRAY-B"], pathways["TRAY-LINK"]
    plans.extend([((0, "SW"), (1, "SW"), [a[1]["id"]]), ((2, "SW"), (3, "SW"), [b[1]["id"]]),
                  ((0, "SW"), (2, "SW"), [a[0]["id"], link[0]["id"], b[0]["id"]]),
                  ((0, "SW"), (3, "SW"), [a[0]["id"], link[0]["id"], b[0]["id"], b[1]["id"]])])
    expected = {}
    for index, (source, target, route) in enumerate(plans, 1):
        identifier = f"DEMO-SR-C{index:02d}"
        device_ids = [devices[source]["id"], devices[target]["id"]]
        expected[identifier] = (device_ids, route)
        if identifier in existing:
            continue
        selected = []
        for device_id in device_ids:
            candidates = sorted([port for port in ports if port["device_id"] == device_id and port["id"] not in occupied
                                 and port["front_or_rear"] == "front" and port["media_type"].lower() == "copper"
                                 and port["connector_type"].upper() == "RJ45"], key=lambda port: (port["position_index"], port["identifier"]))
            if not candidates:
                raise RuntimeError(f"No free RJ45 copper port on demo device {device_id}")
            selected.append(candidates[0]["id"])
            occupied.add(candidates[0]["id"])
        api.request("/scene/cables", {"identifier": identifier, "media_type": "copper", "construction": "Cat6A",
            "port_a_id": selected[0], "port_b_id": selected[1], "project_id": api.context["project_id"],
            "color": "blue", "route_segment_ids": route})
    return expected


def verify(api, room, floor, racks, devices, cable_plans):
    scene = api.scene()
    if scene["scope"]["location_id"] != room["id"]:
        raise RuntimeError("Reloaded scene changed location scope")
    cables = indexed(scene["cables"])
    used_ports = []
    route_count = 0
    for identifier, (device_ids, route) in cable_plans.items():
        cable = cables[identifier]
        terms = sorted(cable["terminations"], key=lambda term: term["side"])
        if len(terms) != 2 or [term["device_id"] for term in terms] != device_ids or cable["route_segment_ids"] != route:
            raise RuntimeError(f"Reloaded {identifier} endpoints/routes differ; existing data was not changed")
        used_ports.extend(term["port_id"] for term in terms)
        route_count += len(route)
    if len(used_ports) != len(set(used_ports)):
        raise RuntimeError("Example cables reused a physical port")
    by_port = {port["id"]: port for port in scene["ports"]}
    if not all(by_port[port_id]["occupied"] for port_id in used_ports):
        raise RuntimeError("Reloaded port occupancy is inconsistent")
    if not set(rack["id"] for rack in racks) <= {row["id"] for row in scene["racks"]} or not set(row["id"] for row in devices.values()) <= {row["id"] for row in scene["devices"]}:
        raise RuntimeError("Reloaded scene is missing example infrastructure")
    return {"room_id": room["id"], "floor_id": floor["id"], "room_identifier": ROOM_IDENTIFIER,
            "counts": {"rooms": 1, "racks": len(racks), "devices": len(devices), "pathways": 3, "pathway_segments": 5,
                       "cables": len(cable_plans), "terminations": len(used_ports), "route_references": route_count},
            "url": api.base_url + "/app-next/3d?room=" + room["id"],
            "context": {"tenantId": api.context["tenant_id"], "actorId": api.context["owner_id"],
                        "projectId": api.context["project_id"], "locationId": floor["id"]}}


def save_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".3d-example-", delete=False) as output:
        temporary = Path(output.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(manifest, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/3d-example.json")
    args = parser.parse_args()
    api = DemoApi(args.base_url)
    room, floor = ensure_room(api)
    templates = choose_templates(api)
    racks = ensure_racks(api, room)
    devices = ensure_devices(api, racks, templates)
    pathways = ensure_pathways(api, room)
    cable_plans = ensure_cables(api, devices, pathways)
    manifest = verify(api, room, floor, racks, devices, cable_plans)
    save_manifest(args.output, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        print(f"Example creation stopped: {exc}", file=sys.stderr)
        sys.exit(1)
