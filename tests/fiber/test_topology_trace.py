from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.exceptions import AuthorizationError, ValidationError
from app.fiber_models import PhysicalPortClaim
from app.models import PortMapping
from app.services.connectivity import ConnectivityService
from app.services.fiber_advanced import FiberAdvancedService
from app.services.topology_trace import TopologyTraceService


def provision(env, cable_index: int):
    return env.write(
        env.service().provision_bundle,
        env.cables[cable_index].id,
        env.cables[cable_index].identifier,
    )


def strand_id(bundle: dict, number: int = 1) -> uuid.UUID:
    return uuid.UUID(bundle["strands"][number - 1]["id"])


def build_fiber_chain(env):
    advanced = FiberAdvancedService(env.db, env.principal)
    first = provision(env, 0)
    second = provision(env, 1)
    left = strand_id(first)
    right = strand_id(second)
    env.write(
        advanced.terminate_strand,
        strand_id=left,
        side="A",
        port_id=env.fiber_ports[0].id,
        loss_db=0.1,
    )
    env.write(
        advanced.terminate_strand,
        strand_id=right,
        side="B",
        port_id=env.fiber_ports[3].id,
        loss_db=0.2,
    )
    breakout = env.write(
        advanced.create_breakout,
        project_id=env.projects[0].id,
        device_id=env.devices[0].id,
        identifier="TRACE-BO",
        name="Trace breakout",
        mode="passive",
        legs=[{
            "parent_strand_id": str(left),
            "parent_side": "B",
            "child_strand_id": str(right),
            "child_side": "A",
            "loss_db": 0.05,
        }],
    )
    mapping = PortMapping(
        tenant_id=env.tenants[0].id,
        source_port_id=env.fiber_ports[0].id,
        target_port_id=env.fiber_ports[1].id,
        mapping_type="front_rear",
        lane=1,
    )
    env.db.add(mapping)
    env.db.commit()
    legacy = ConnectivityService(env.db, env.principal)
    patch = env.write(
        legacy.create_cable,
        identifier="TRACE-PATCH",
        media_type="fiber_os2",
        construction="patch",
        port_a_id=env.fiber_ports[1].id,
        port_b_id=env.fiber_ports[2].id,
        project_id=env.projects[0].id,
    )
    otdr = env.write(
        advanced.create_otdr_record,
        project_id=env.projects[0].id,
        cable_id=env.cables[0].id,
        strand_id=left,
        direction="A",
        wavelength_nm=1550,
        acquired_at=datetime.now(UTC),
        source_name="trace.sor",
        total_length_m=100,
        end_to_end_loss_db=0.8,
        events=[
            {"event_type": "connector", "distance_m": 0, "loss_db": 0.1},
            {"event_type": "bend", "distance_m": 50, "loss_db": 0.2},
            {"event_type": "end", "distance_m": 100},
        ],
    )
    return {
        "left": left,
        "right": right,
        "breakout": breakout,
        "mapping": mapping,
        "patch": patch,
        "otdr": otdr,
    }


def test_generic_fiber_trace_crosses_ports_mapping_cable_breakout_and_otdr(env):
    graph = build_fiber_chain(env)
    result = TopologyTraceService(env.db, env.principal).trace_cable(
        env.cables[0].id,
        strand_number=1,
    )
    kinds = [item["kind"] for item in result["items"]]
    for expected in (
        "port",
        "cable",
        "internal_mapping",
        "fiber_termination",
        "fiber_strand",
        "breakout",
    ):
        assert expected in kinds
    assert result["trace_model"] == "generic-fiber"
    assert result["selected_strand"]["id"] == str(graph["left"])
    assert result["complete"] and not result["cycle"] and not result["truncated"]
    assert result["hop_count"] >= 7
    assert result["otdr"][0]["id"] == graph["otdr"]["id"]
    assert len(result["otdr"][0]["events"]) == 3
    selected = [item for item in result["items"] if item.get("selected")]
    assert len(selected) == 1 and selected[0]["kind"] == "fiber_strand"


def test_generic_trace_safety_cap_never_returns_more_than_requested_nodes(env):
    build_fiber_chain(env)
    result = TopologyTraceService(env.db, env.principal).trace_cable(
        env.cables[0].id,
        strand_number=1,
        max_nodes=2,
    )
    assert result["truncated"]
    assert result["node_count"] <= 2
    assert result["edge_count"] <= 1


def test_copper_pair_trace_preserves_legacy_path_and_adds_channel(env):
    legacy = ConnectivityService(env.db, env.principal)
    cable = legacy.create_cable(
        identifier="CU-TRACE",
        media_type="copper_cat6a",
        construction="horizontal",
        port_a_id=env.copper_ports[0].id,
        port_b_id=env.copper_ports[1].id,
        project_id=env.projects[0].id,
    )
    cable.pair_count = 4
    env.db.commit()
    advanced = FiberAdvancedService(env.db, env.principal)
    pairs = env.write(advanced.provision_pairs, cable.id)
    pair = pairs["pairs"][0]
    channel = env.write(
        advanced.create_channel,
        project_id=env.projects[0].id,
        identifier="CU-CHANNEL",
        name="Copper trace channel",
        medium="copper",
        topology="ethernet",
        status="active",
        members=[{
            "kind": "copper_pair",
            "resource_id": pair["id"],
            "role": "pair-1",
        }],
    )
    result = TopologyTraceService(env.db, env.principal).trace_cable(
        cable.id,
        pair_number=1,
    )
    assert result["trace_model"] == "generic-copper"
    assert result["selected_pair"]["id"] == pair["id"]
    assert result["selected_pair"]["channels"][0]["id"] == channel["id"]
    assert [item["kind"] for item in result["items"]] == ["port", "cable", "port"]
    assert result["items"][1]["selected"]

    channel_trace = TopologyTraceService(env.db, env.principal).trace_channel(
        uuid.UUID(channel["id"])
    )
    assert channel_trace["members"][0]["trace"]["selected_pair"]["number"] == 1


def test_fiber_channel_trace_returns_each_selected_member(env):
    first = provision(env, 0)
    second = provision(env, 1)
    advanced = FiberAdvancedService(env.db, env.principal)
    channel = env.write(
        advanced.create_channel,
        project_id=env.projects[0].id,
        identifier="F-DUPLEX",
        name="Fiber duplex",
        medium="fiber",
        topology="duplex",
        status="active",
        members=[
            {"kind": "fiber_strand", "resource_id": first["strands"][0]["id"], "role": "tx"},
            {"kind": "fiber_strand", "resource_id": second["strands"][1]["id"], "role": "rx"},
        ],
    )
    traced = TopologyTraceService(env.db, env.principal).trace_channel(uuid.UUID(channel["id"]))
    assert [row["role"] for row in traced["members"]] == ["tx", "rx"]
    assert [row["trace"]["selected_strand"]["number"] for row in traced["members"]] == [1, 2]


def test_trace_validates_selector_and_requires_fresh_cable_trace_permission(env):
    first = provision(env, 0)
    service = TopologyTraceService(env.db, env.principal)
    with pytest.raises(ValidationError, match="either"):
        service.trace_cable(env.cables[0].id, strand_number=1, pair_number=1)
    with pytest.raises(ValidationError, match="between"):
        service.trace_cable(env.cables[0].id, strand_number=5)
    with pytest.raises(ValidationError, match="max_nodes"):
        service.trace_cable(env.cables[0].id, strand_number=1, max_nodes=1)

    forged = env.service(env.reader).principal
    with pytest.raises(AuthorizationError):
        TopologyTraceService(env.db, forged).trace_cable(
            env.cables[0].id,
            strand_number=1,
        )
    assert first["strand_count"] == 4


def test_new_connectivity_writes_always_create_two_normalized_port_claims(env):
    cable = env.write(
        ConnectivityService(env.db, env.principal).create_cable,
        identifier="CLAIM-CABLE",
        media_type="copper_cat6a",
        construction="patch",
        port_a_id=env.copper_ports[0].id,
        port_b_id=env.copper_ports[1].id,
        project_id=env.projects[0].id,
    )
    claims = env.db.scalars(select(PhysicalPortClaim).where(
        PhysicalPortClaim.owner_type == "cable_termination"
    )).all()
    assert len(claims) == 2
    assert {claim.port_id for claim in claims} == {
        env.copper_ports[0].id,
        env.copper_ports[1].id,
    }
    assert cable.id
