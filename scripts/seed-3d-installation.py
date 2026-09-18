#!/usr/bin/env python3
"""Extend the local demo with saved entrances and fiber installation examples.

Only modifies the three already-created demonstration rooms, filling missing
entrances without replacing existing ones. Hardware/links are additive by ID.
Requires scripts/seed-3d-room.py and migration 009 first.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import uuid

spec = importlib.util.spec_from_file_location("room_seed", Path(__file__).with_name("seed-3d-room.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    api = module.DemoApi("http://127.0.0.1:18000")
    room, floor = module.ensure_room(api)
    api.scope(floor["id"])
    mutations = []
    for location in api.scene()["locations"]:
        if location["identifier"] not in {"DEMO-SR-01", "DEMO-PREP-01", "MC-ENG-F02-TR02"}:
            continue
        dims = location["dimensions"]
        if not dims.get("entrances"):
            width = min(1.2, dims["width_m"])
            entries = [{"id": str(uuid.uuid4()), "name": "主入口", "wall": "south", "offset_m": (dims["width_m"]-width)/2, "width_m": width, "height_m": min(2.1, dims["height_m"])}]
            if location["identifier"] == "DEMO-SR-01":
                entries.append({"id": str(uuid.uuid4()), "name": "侧面出口", "wall": "east", "offset_m": 1, "width_m": 1, "height_m": 2.1})
            api.request(f"/scene/rooms/{location['id']}/entrances", {"expected_version": location["version"], "entrances": entries}, method="PATCH")
            mutations.append("entrances:" + location["identifier"])
    api.scope(room["id"])
    alternative = next((p for p in api.scene()["pathways"] if p["identifier"] == "DEMO-SR-TRAY-ALT"), None)
    if not alternative:
        api.request("/scene/pathways", {"location_id": room["id"], "identifier": "DEMO-SR-TRAY-ALT", "name": "光纤备用桥架", "pathway_type": "basket_tray", "segments": [{"name": "光纤备用桥架 · 01", "sequence": 1, "coordinates": [{"x": 4.4, "y": 2.8, "z": 3}, {"x": 4.4, "y": 5.6, "z": 3}]}]})
        mutations.append("DEMO-SR-TRAY-ALT")
        created = next(p for p in api.scene()["pathways"] if p["identifier"] == "DEMO-SR-TRAY-ALT")
        api.request(f"/scene/pathways/{created['id']}/cable-policy", {"expected_version": created["version"], "allows_cables": True, "allowed_media": ["fiber"]}, method="PATCH")
    templates = api.request("/device-templates")
    template = next((t for t in templates if t["manufacturer"] == "Generic" and t["model"] == "CMS Fiber Panel 24 LC"), None)
    if not template:
        template = api.request("/device-templates", {"manufacturer": "Generic", "model": "CMS Fiber Panel 24 LC", "device_type": "fiber_patch_panel", "rack_units": 1, "width_mm": 482, "depth_mm": 300, "port_blueprint": [{"count": 24, "prefix": "LC" if face == "front" else "LCR", "face": face, "connector_type": "LC", "media_type": "fiber", "mapping_key": "panel"} for face in ["front", "rear"]]})
        mutations.append("fiber-template")
    scene = api.scene()
    created_panels = set()
    for rack in scene["racks"]:
        if not rack["rack_identifier"].startswith("DEMO-SR-R-"):
            continue
        identifier = rack["rack_identifier"] + "-ODF"
        existing = next((device for device in scene["devices"] if device["identifier"] == identifier), None)
        if not existing:
            api.request("/scene/devices", {"rack_id": rack["id"], "template_id": template["id"], "identifier": identifier, "name": rack["name"] + " · 光纤配线架", "start_u": 36, "face": "front"})
            mutations.append(identifier)
            created_panels.add(identifier)
    scene = api.scene()
    panels = sorted([d for d in scene["devices"] if d["identifier"].startswith("DEMO-SR-R-") and d["identifier"].endswith("-ODF")], key=lambda d:d["identifier"])
    if len(panels) != 4:
        raise RuntimeError("Expected four demo fiber panels")
    for panel in panels:
        policy = {"allows_cables": True, "allowed_media": ["fiber"]}
        if panel["identifier"] in created_panels and panel.get("cable_policy") != policy:
            api.request(f"/scene/devices/{panel['id']}/cable-policy", {"expected_version": panel["version"], **policy}, method="PATCH")
            mutations.append("policy:"+panel["identifier"])
    def endpoint(device):
        return next(p["id"] for p in scene["ports"] if p["device_id"] == device["id"] and p["front_or_rear"] == "front" and p["position_index"] == 1)
    for number, (a, b) in enumerate([(panels[0], panels[1]), (panels[2], panels[3])], 1):
        identifier = f"DEMO-SR-F{number:02d}"
        if any(c["identifier"] == identifier for c in scene["cables"]):
            continue
        body = {"port_a_id": endpoint(a), "port_b_id": endpoint(b), "media_type": "OS2 fiber"}
        preview = api.request("/scene/routes/preview", body)
        route = next((c for c in preview["candidates"] if c["segment_ids"]), None)
        if not route:
            raise RuntimeError("Demo fiber endpoints have no continuous tray route")
        api.request("/scene/cables", {**body, "identifier": identifier, "construction": "fiber_trunk", "project_id": api.context["project_id"], "color": "yellow", "route_segment_ids": route["segment_ids"]})
        mutations.append(identifier)
    final = api.scene()
    print(json.dumps({"mutations": mutations, "room": room["id"], "fiber_panels": len(panels), "fiber_cables": [c["identifier"] for c in final["cables"] if c["identifier"].startswith("DEMO-SR-F")]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
