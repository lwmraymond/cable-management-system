from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.exceptions import AuthorizationError, ConflictError, ValidationError
from app.fiber_models import (
    ChannelMember,
    ConnectivityChannel,
    CopperPair,
    FiberBreakout,
    FiberBreakoutLeg,
    FiberEndpointClaim,
    FiberPortTermination,
    FiberSplice,
    OtdrEvent,
    OtdrRecord,
    PhysicalPortClaim,
)
from app.models import CableTermination
from app.services.connectivity import ConnectivityService
from app.services.fiber_advanced import FiberAdvancedService


def advanced(env) -> FiberAdvancedService:
    return FiberAdvancedService(env.db, env.principal)


def bundle(env, cable_index: int):
    service = env.service()
    return env.write(
        service.provision_bundle,
        env.cables[cable_index].id,
        env.cables[cable_index].identifier,
    )


def strand(result, number: int = 1) -> uuid.UUID:
    return uuid.UUID(result["strands"][number - 1]["id"])


def count(env, model) -> int:
    return env.db.scalar(select(func.count()).select_from(model))


def test_fiber_termination_claims_trace_release_and_reuse(env):
    result = bundle(env, 0)
    strand_id = strand(result)
    service = advanced(env)
    created = env.write(
        service.terminate_strand,
        strand_id=strand_id,
        side="A",
        port_id=env.fiber_ports[0].id,
        connection_type="connector",
        loss_db=0.15,
    )
    assert created["version"] == 1
    assert count(env, FiberPortTermination) == 1
    assert count(env, FiberEndpointClaim) == 1
    assert count(env, PhysicalPortClaim) == 1
    traced = env.service().trace(strand_id, "B")
    assert traced["termination"] == "port"
    assert traced["steps"][-1]["port_id"] == str(env.fiber_ports[0].id)
    assert traced["total_splice_loss_db"] == 0.15

    with pytest.raises(ConflictError, match="changed"):
        env.write(service.release_termination, uuid.UUID(created["id"]), 2)
    assert count(env, FiberEndpointClaim) == 1

    released = env.write(service.release_termination, uuid.UUID(created["id"]), 1)
    assert released == {"id": created["id"], "version": 2, "released": True}
    assert count(env, FiberPortTermination) == 0
    assert count(env, FiberEndpointClaim) == 0
    assert count(env, PhysicalPortClaim) == 0
    history = env.db.execute(select(FiberPortTermination.__table__).where(
        FiberPortTermination.__table__.c.id == uuid.UUID(created["id"])
    )).mappings().one()
    assert history["version"] == 2 and history["deleted_at"] is not None

    reused = env.write(
        service.terminate_strand,
        strand_id=strand_id,
        side="A",
        port_id=env.fiber_ports[0].id,
    )
    assert reused["id"] != created["id"]


def test_shared_physical_port_claim_blocks_cable_and_fiber_double_use(env):
    first = bundle(env, 0)
    second = bundle(env, 1)
    service = advanced(env)
    legacy = ConnectivityService(env.db, env.principal)

    cable = env.write(
        legacy.create_cable,
        identifier="F-PATCH-1",
        media_type="fiber_os2",
        construction="patch",
        port_a_id=env.fiber_ports[0].id,
        port_b_id=env.fiber_ports[1].id,
        project_id=env.projects[0].id,
    )
    assert cable.id
    assert count(env, PhysicalPortClaim) == 2
    with pytest.raises(ConflictError, match="port"):
        env.write(
            service.terminate_strand,
            strand_id=strand(first),
            side="A",
            port_id=env.fiber_ports[0].id,
        )

    env.write(
        service.terminate_strand,
        strand_id=strand(second),
        side="A",
        port_id=env.fiber_ports[2].id,
    )
    with pytest.raises(ConflictError, match="terminated"):
        env.write(
            legacy.create_cable,
            identifier="F-PATCH-2",
            media_type="fiber_os2",
            construction="patch",
            port_a_id=env.fiber_ports[2].id,
            port_b_id=env.fiber_ports[3].id,
            project_id=env.projects[0].id,
        )
    assert env.db.scalar(select(func.count()).select_from(CableTermination)) == 2


def test_termination_rejects_wrong_media_splice_claim_and_foreign_scope(env):
    first = bundle(env, 0)
    second = bundle(env, 1)
    service = advanced(env)
    with pytest.raises(ValidationError, match="fiber-compatible"):
        env.write(
            service.terminate_strand,
            strand_id=strand(first),
            side="A",
            port_id=env.copper_ports[0].id,
        )

    cassette = env.write(
        env.service().create_cassette,
        env.devices[0].id,
        env.projects[0].id,
        "Termination collision",
        2,
    )
    slot = uuid.UUID(cassette["slots"][0]["id"])
    env.write(
        env.service().splice,
        slot,
        strand(first),
        "A",
        strand(second),
        "B",
        1,
    )
    with pytest.raises(ConflictError, match="endpoint"):
        env.write(
            service.terminate_strand,
            strand_id=strand(first),
            side="A",
            port_id=env.fiber_ports[0].id,
        )

    contractor = FiberAdvancedService(env.db, env.service(env.contractor).principal)
    with pytest.raises(AuthorizationError):
        env.write(
            contractor.terminate_strand,
            strand_id=strand(first),
            side="B",
            port_id=env.fiber_ports[4].id,
        )


def test_pair_provisioning_channel_exclusivity_release_and_reuse(env):
    service = advanced(env)
    provisioned = env.write(service.provision_pairs, env.copper_cables[0].id)
    assert provisioned["pair_count"] == 4
    pair_ids = [uuid.UUID(row["id"]) for row in provisioned["pairs"]]
    with pytest.raises(ConflictError, match="already"):
        env.write(service.provision_pairs, env.copper_cables[0].id)

    channel = env.write(
        service.create_channel,
        project_id=env.projects[0].id,
        identifier="CH-CU-01",
        name="Copper channel",
        medium="copper",
        topology="ethernet",
        status="active",
        members=[
            {"kind": "copper_pair", "resource_id": str(pair_ids[0]), "role": "pair-1"},
            {"kind": "copper_pair", "resource_id": str(pair_ids[1]), "role": "pair-2"},
        ],
    )
    assert channel["version"] == 1 and len(channel["members"]) == 2
    assert count(env, ConnectivityChannel) == 1
    assert count(env, ChannelMember) == 2

    with pytest.raises(IntegrityError):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="CH-CU-02",
            name="Collision",
            medium="copper",
            topology="duplex",
            members=[
                {"kind": "copper_pair", "resource_id": str(pair_ids[0])},
                {"kind": "copper_pair", "resource_id": str(pair_ids[2])},
            ],
        )
    assert count(env, ConnectivityChannel) == 1

    with pytest.raises(ConflictError, match="changed"):
        env.write(service.release_channel, uuid.UUID(channel["id"]), 2)
    released = env.write(service.release_channel, uuid.UUID(channel["id"]), 1)
    assert released["version"] == 2
    history = env.db.execute(select(ConnectivityChannel.__table__).where(
        ConnectivityChannel.__table__.c.id == uuid.UUID(channel["id"])
    )).mappings().one()
    assert history["version"] == 2 and history["deleted_at"] is not None

    replacement = env.write(
        service.create_channel,
        project_id=env.projects[0].id,
        identifier="CH-CU-03",
        name="Replacement",
        medium="copper",
        topology="simplex",
        members=[{"kind": "copper_pair", "resource_id": str(pair_ids[0])}],
    )
    assert replacement["members"][0]["resource_id"] == str(pair_ids[0])


def test_fiber_channel_validates_medium_project_duplicates_and_role(env):
    service = advanced(env)
    first = bundle(env, 0)
    other_project = bundle(env, 4)
    first_id = strand(first)
    with pytest.raises(AuthorizationError):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="BAD-MEDIUM",
            name="Bad medium",
            medium="copper",
            topology="simplex",
            members=[{"kind": "fiber_strand", "resource_id": str(first_id)}],
        )
    with pytest.raises(AuthorizationError):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="BAD-PROJECT",
            name="Bad project",
            medium="fiber",
            topology="simplex",
            members=[{"kind": "fiber_strand", "resource_id": str(strand(other_project))}],
        )
    with pytest.raises(ValidationError, match="Duplicate"):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="DUP",
            name="Duplicate",
            medium="fiber",
            topology="duplex",
            members=[
                {"kind": "fiber_strand", "resource_id": str(first_id)},
                {"kind": "fiber_strand", "resource_id": str(first_id)},
            ],
        )
    with pytest.raises(ValidationError, match="role"):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="ROLE",
            name="Role",
            medium="fiber",
            topology="simplex",
            members=[{
                "kind": "fiber_strand",
                "resource_id": str(first_id),
                "role": "x" * 81,
            }],
        )


def test_breakout_chain_trace_release_and_same_request_cycle_rejection(env):
    service = advanced(env)
    bundles = [bundle(env, index) for index in (0, 1, 2)]
    strands = [strand(row) for row in bundles]
    breakout = env.write(
        service.create_breakout,
        project_id=env.projects[0].id,
        device_id=env.devices[0].id,
        identifier="BO-01",
        name="Fanout",
        mode="fanout",
        legs=[
            {
                "parent_strand_id": str(strands[0]),
                "parent_side": "B",
                "child_strand_id": str(strands[1]),
                "child_side": "A",
                "loss_db": 0.05,
            },
            {
                "parent_strand_id": str(strands[1]),
                "parent_side": "B",
                "child_strand_id": str(strands[2]),
                "child_side": "A",
                "loss_db": 0.06,
            },
        ],
    )
    assert len(breakout["legs"]) == 2
    traced = env.service().trace(strands[0], "A")
    assert [step["kind"] for step in traced["steps"]] == [
        "strand", "breakout", "strand", "breakout", "strand"
    ]
    assert traced["total_splice_loss_db"] == 0.11
    assert count(env, FiberEndpointClaim) == 4

    with pytest.raises(ConflictError, match="changed"):
        env.write(service.release_breakout, uuid.UUID(breakout["id"]), 2)
    released = env.write(service.release_breakout, uuid.UUID(breakout["id"]), 1)
    assert released["version"] == 2 and count(env, FiberEndpointClaim) == 0
    history = env.db.execute(select(FiberBreakout.__table__).where(
        FiberBreakout.__table__.c.id == uuid.UUID(breakout["id"])
    )).mappings().one()
    assert history["version"] == 2 and history["deleted_at"] is not None

    with pytest.raises(ConflictError, match="cycle"):
        env.write(
            service.create_breakout,
            project_id=env.projects[0].id,
            device_id=env.devices[0].id,
            identifier="BO-CYCLE",
            name="Cycle",
            mode="passive",
            legs=[
                {
                    "parent_strand_id": str(strands[0]),
                    "parent_side": "B",
                    "child_strand_id": str(strands[1]),
                    "child_side": "A",
                },
                {
                    "parent_strand_id": str(strands[1]),
                    "parent_side": "B",
                    "child_strand_id": str(strands[0]),
                    "child_side": "A",
                },
            ],
        )
    assert count(env, FiberBreakout) == 0
    assert count(env, FiberBreakoutLeg) == 0


def test_breakout_claim_blocks_termination_and_splice(env):
    service = advanced(env)
    rows = [bundle(env, index) for index in (0, 1, 2)]
    strands = [strand(row) for row in rows]
    env.write(
        service.create_breakout,
        project_id=env.projects[0].id,
        device_id=env.devices[0].id,
        identifier="BO-LOCK",
        name="Endpoint lock",
        mode="passive",
        legs=[{
            "parent_strand_id": str(strands[0]),
            "parent_side": "B",
            "child_strand_id": str(strands[1]),
            "child_side": "A",
        }],
    )
    with pytest.raises(ConflictError, match="endpoint"):
        env.write(
            service.terminate_strand,
            strand_id=strands[0],
            side="B",
            port_id=env.fiber_ports[0].id,
        )
    cassette = env.write(
        env.service().create_cassette,
        env.devices[0].id,
        env.projects[0].id,
        "Claim collision",
        1,
    )
    with pytest.raises(IntegrityError):
        env.write(
            env.service().splice,
            uuid.UUID(cassette["slots"][0]["id"]),
            strands[0],
            "B",
            strands[2],
            "A",
            1,
        )


def test_otdr_record_order_link_version_and_project_guards(env):
    service = advanced(env)
    first = bundle(env, 0)
    second = bundle(env, 1)
    strand_id = strand(first)
    cassette = env.write(
        env.service().create_cassette,
        env.devices[0].id,
        env.projects[0].id,
        "OTDR cassette",
        1,
    )
    splice = env.write(
        env.service().splice,
        uuid.UUID(cassette["slots"][0]["id"]),
        strand_id,
        "B",
        strand(second),
        "A",
        1,
        0.12,
    )
    record = env.write(
        service.create_otdr_record,
        project_id=env.projects[0].id,
        cable_id=env.cables[0].id,
        strand_id=strand_id,
        direction="A",
        wavelength_nm=1550,
        acquired_at=datetime.now(UTC),
        source_name="trace.sor",
        source_object_key="tenant/trace.sor",
        total_length_m=100.0,
        end_to_end_loss_db=1.2,
        metadata={"instrument": "test"},
        events=[
            {"event_type": "launch", "distance_m": 0.0, "confidence": 1.0},
            {"event_type": "splice", "distance_m": 42.0, "loss_db": 0.12,
             "confidence": 0.9},
            {"event_type": "end", "distance_m": 100.0, "confidence": 1.0},
        ],
    )
    assert len(record["events"]) == 3
    event_id = uuid.UUID(record["events"][1]["id"])
    linked = env.write(
        service.link_otdr_event,
        event_id=event_id,
        linked_kind="splice",
        linked_id=uuid.UUID(splice["id"]),
        expected_version=1,
        link_offset_m=0.25,
    )
    assert linked["version"] == 2 and linked["linked_kind"] == "splice"
    with pytest.raises(ConflictError, match="changed"):
        env.write(
            service.link_otdr_event,
            event_id=event_id,
            linked_kind="splice",
            linked_id=uuid.UUID(splice["id"]),
            expected_version=1,
        )
    stored = env.db.execute(select(OtdrEvent.__table__).where(
        OtdrEvent.__table__.c.id == event_id
    )).mappings().one()
    assert stored["version"] == 2 and stored["linked_id"] == uuid.UUID(splice["id"])

    with pytest.raises(ValidationError, match="ordered"):
        env.write(
            service.create_otdr_record,
            project_id=env.projects[0].id,
            cable_id=env.cables[0].id,
            strand_id=strand_id,
            direction="A",
            wavelength_nm=1310,
            acquired_at=datetime.now(UTC),
            source_name="bad-order.sor",
            total_length_m=100,
            events=[
                {"event_type": "connector", "distance_m": 20},
                {"event_type": "splice", "distance_m": 10},
            ],
        )
    with pytest.raises(ValidationError, match="exceeds"):
        env.write(
            service.create_otdr_record,
            project_id=env.projects[0].id,
            cable_id=env.cables[0].id,
            strand_id=strand_id,
            direction="A",
            wavelength_nm=1310,
            acquired_at=datetime.now(UTC),
            source_name="too-long.sor",
            total_length_m=10,
            events=[{"event_type": "end", "distance_m": 11}],
        )
    assert count(env, OtdrRecord) == 1


def test_raw_composite_foreign_keys_protect_new_claim_and_channel_tables(env):
    first = bundle(env, 0)
    strand_id = strand(first)
    with pytest.raises(IntegrityError):
        env.db.execute(insert(FiberEndpointClaim.__table__).values(
            tenant_id=env.tenants[1].id,
            strand_id=strand_id,
            side="A",
            owner_type="splice",
            owner_id=uuid.uuid4(),
        ))
    env.db.rollback()

    pairs = env.write(advanced(env).provision_pairs, env.copper_cables[0].id)
    pair_id = uuid.UUID(pairs["pairs"][0]["id"])
    channel = ConnectivityChannel(
        tenant_id=env.tenants[0].id,
        project_id=env.projects[0].id,
        identifier="RAW",
        name="Raw",
        medium="copper",
        topology="simplex",
        status="planned",
    )
    env.db.add(channel)
    env.db.flush()
    with pytest.raises(IntegrityError):
        env.db.execute(insert(ChannelMember.__table__).values(
            tenant_id=env.tenants[1].id,
            channel_id=channel.id,
            sequence=1,
            role="bad",
            copper_pair_id=pair_id,
        ))
    env.db.rollback()


def test_channel_fixed_cardinality_and_otdr_direct_service_boundaries(env):
    service = advanced(env)
    first = bundle(env, 0)
    strand_id = strand(first)
    with pytest.raises(ValidationError, match="duplex channel requires exactly 2"):
        env.write(
            service.create_channel,
            project_id=env.projects[0].id,
            identifier="BAD-DUPLEX",
            name="Bad duplex",
            medium="fiber",
            topology="duplex",
            members=[{"kind": "fiber_strand", "resource_id": strand_id, "role": "tx"}],
        )
    with pytest.raises(ValidationError, match="include a timezone"):
        env.write(
            service.create_otdr_record,
            project_id=env.projects[0].id,
            cable_id=env.cables[0].id,
            strand_id=strand_id,
            direction="A",
            wavelength_nm=1550,
            acquired_at=datetime(2026, 9, 4, 10, 0, 0),
            source_name="naive.sor",
        )
    with pytest.raises(ValidationError, match="source name"):
        env.write(
            service.create_otdr_record,
            project_id=env.projects[0].id,
            cable_id=env.cables[0].id,
            strand_id=strand_id,
            direction="A",
            wavelength_nm=1550,
            acquired_at=datetime.now(UTC),
            source_name="x" * 501,
        )
