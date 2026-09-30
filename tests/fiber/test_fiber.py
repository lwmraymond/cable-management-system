from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    FiberBundle, FiberCassetteSlot, FiberSplice, FiberSpliceEnd, FiberStrand,
)
from app.models import AuditEvent


def topology(env):
    service = env.service()
    bundles = [env.write(service.provision_bundle, cable.id, cable.identifier)
               for cable in env.cables[:3]]
    cassette = env.write(service.create_cassette, env.devices[0].id,
                          env.projects[0].id, "Cassette 1", 4)
    strands = [uuid.UUID(b["strands"][0]["id"]) for b in bundles]
    slots = [uuid.UUID(s["id"]) for s in cassette["slots"]]
    return service, strands, slots, cassette


def count(env, model):
    return env.db.scalar(select(func.count()).select_from(model))


def test_bundle_and_cassette_persist_and_audit(env):
    service, strands, slots, cassette = topology(env)
    assert len(slots) == 4
    assert count(env, FiberBundle) == 3
    assert count(env, FiberStrand) == 12
    assert count(env, AuditEvent) == 4
    assert service.get_cassette(uuid.UUID(cassette["id"]))["slots"][0]["version"] == 1
    assert [x["number"] for x in cassette["slots"]] == [1, 2, 3, 4]


def test_splice_trace_reverse_release_and_reuse(env):
    service, strands, slots, _ = topology(env)
    splice = env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1, 0.125)
    assert splice["slot_version"] == 2
    assert count(env, FiberSpliceEnd) == 2
    forward = service.trace(strands[0])
    reverse = service.trace(strands[1], "B")
    assert [s["kind"] for s in forward["steps"]] == ["strand", "splice", "strand"]
    assert forward["steps"][2]["strand_id"] == str(strands[1])
    assert reverse["steps"][2]["strand_id"] == str(strands[0])
    assert forward["termination"] == "open" and not forward["cycle"]
    assert forward["total_splice_loss_db"] == 0.125
    env.write(service.release, slots[0], 2)
    assert count(env, FiberSpliceEnd) == 0
    assert count(env, FiberSplice) == 0  # Default reads exclude released history.
    history = env.db.execute(select(FiberSplice.__table__).where(
        FiberSplice.__table__.c.id == uuid.UUID(splice["id"]))).mappings().one()
    assert history["deleted_at"] is not None
    released_audit = env.db.scalar(select(AuditEvent).where(
        AuditEvent.action == "fiber.splice.released"))
    assert len(released_audit.before["ends"]) == 2
    env.write(service.splice, slots[0], strands[0], "B", strands[2], "A", 3, 0)
    assert count(env, FiberSpliceEnd) == 2
    assert service.trace(strands[0])["steps"][-1]["strand_id"] == str(strands[2])


def test_stale_version_rejected_without_releasing(env):
    service, strands, slots, _ = topology(env)
    env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    with pytest.raises(ConflictError, match="Slot changed"):
        env.write(service.release, slots[0], 1)
    assert count(env, FiberSpliceEnd) == 2


def test_active_slot_and_endpoint_orientation_constraints(env):
    service, strands, slots, _ = topology(env)
    env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    # Endpoint reused in the opposite position of the new splice payload.
    with pytest.raises(IntegrityError):
        env.write(service.splice, slots[1], strands[2], "B", strands[0], "B", 1)
    assert count(env, FiberSpliceEnd) == 2
    assert env.db.get(FiberCassetteSlot, slots[1]).version == 1
    with pytest.raises(IntegrityError):
        env.db.execute(insert(FiberSplice).values(tenant_id=env.tenants[0].id,
                       slot_id=slots[0], loss_db=0))
    env.db.rollback()
    assert count(env, FiberSplice) == 1


def test_cycle_rejection_and_trace_bound(env):
    service, strands, slots, _ = topology(env)
    env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    with pytest.raises(ConflictError, match="cycle"):
        env.write(service.splice, slots[1], strands[0], "A", strands[1], "B", 1)
    limited = service.trace(strands[0], max_hops=1)
    assert limited["truncated"] and limited["termination"] == "limit"
    assert count(env, FiberSplice) == 1


def test_cycle_created_outside_service_is_detected(env):
    service, strands, slots, _ = topology(env)
    env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    bad = FiberSplice(tenant_id=env.tenants[0].id, slot_id=slots[1], loss_db=0)
    env.db.add(bad); env.db.flush()
    env.db.add_all([FiberSpliceEnd(tenant_id=env.tenants[0].id, splice_id=bad.id,
                                 strand_id=strands[0], side="A", end_number=1),
                   FiberSpliceEnd(tenant_id=env.tenants[0].id, splice_id=bad.id,
                                 strand_id=strands[1], side="B", end_number=2)])
    env.db.commit()
    result = service.trace(strands[0])
    assert result["cycle"] and result["termination"] == "cycle"
    assert len(result["steps"]) == 4


@pytest.mark.parametrize("loss", [float('nan'), float('inf'), -0.1, 10.1, True, "0.2"])
def test_invalid_loss_is_rejected(env, loss):
    service, strands, slots, _ = topology(env)
    with pytest.raises(ValidationError):
        env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1, loss)
    assert count(env, FiberSplice) == 0


@pytest.mark.parametrize("side,limit", [("C", 2), ("A", 0), ("B", 257), ("A", True)])
def test_invalid_trace_limit_is_rejected(env, side, limit):
    service, strands, _, _ = topology(env)
    with pytest.raises(ValidationError):
        service.trace(strands[0], side, limit)


def test_cross_tenant_parent_rejected_even_when_cached(env):
    service = env.service()
    assert env.cables[8].tenant_id != env.tenants[0].id
    with pytest.raises(NotFoundError):
        env.write(service.provision_bundle, env.cables[8].id, "foreign")
    with pytest.raises(NotFoundError):
        env.write(service.create_cassette, env.devices[2].id,
                  env.projects[0].id, "foreign", 4)
    assert count(env, FiberBundle) == 0


def test_composite_foreign_key_blocks_cross_tenant_raw_insert(env):
    with pytest.raises(IntegrityError):
        env.db.execute(insert(FiberBundle.__table__).values(
            tenant_id=env.tenants[0].id, cable_id=env.cables[8].id,
            name="foreign-parent", strand_count=4))
    env.db.rollback()
    assert count(env, FiberBundle) == 0


def test_composite_fk_blocks_cross_tenant_strand_to_bundle(env):
    bundle = env.write(env.service().provision_bundle, env.cables[0].id, "first")
    with pytest.raises(IntegrityError):
        env.db.execute(insert(FiberStrand.__table__).values(
            tenant_id=env.tenants[1].id, bundle_id=uuid.UUID(bundle["id"]), number=5))
    env.db.rollback()


def test_duplicate_bundle_rolls_back(env):
    service = env.service()
    env.write(service.provision_bundle, env.cables[0].id, "first")
    with pytest.raises(IntegrityError):
        env.write(service.provision_bundle, env.cables[0].id, "second")
    assert count(env, FiberBundle) == 1 and count(env, FiberStrand) == 4


def test_reader_cannot_write_despite_forged_cached_permissions(env):
    service, _, _, cassette = topology(env)
    reader = env.service(env.reader)
    assert reader.get_cassette(uuid.UUID(cassette["id"]))["slot_count"] == 4
    with pytest.raises(AuthorizationError):
        env.write(reader.create_cassette, env.devices[0].id, env.projects[0].id, "denied", 4)


def test_contractor_actual_location_overrides_headers(env):
    # Same project, different actual device location. Cached principal claims room 0.
    cassette = env.write(env.service().create_cassette, env.devices[1].id,
                         env.projects[0].id, "Outside", 4)
    with pytest.raises(AuthorizationError):
        env.service(env.contractor).get_cassette(uuid.UUID(cassette["id"]))


def test_contractor_can_splice_locally_but_not_trace_entire_cable(env):
    _, strands, slots, cassette = topology(env)
    contractor = env.service(env.contractor)
    assert contractor.get_cassette(uuid.UUID(cassette["id"]))["slot_count"] == 4
    env.write(contractor.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    with pytest.raises(AuthorizationError):
        contractor.trace(strands[0])
    env.write(contractor.release, slots[0], 2)


def test_expired_grant_is_rechecked(env):
    _, _, _, cassette = topology(env)
    contractor = env.service(env.contractor)
    env.grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    env.db.commit()
    with pytest.raises(AuthorizationError):
        contractor.get_cassette(uuid.UUID(cassette["id"]))


def test_actual_project_prevents_header_spoof(env):
    cassette = env.write(env.service().create_cassette, env.devices[0].id,
                         env.projects[1].id, "Project 2", 4)
    with pytest.raises(AuthorizationError):
        env.service(env.contractor).get_cassette(uuid.UUID(cassette["id"]))


def test_splice_cannot_cross_project(env):
    service, strands, slots, _ = topology(env)
    other = env.write(service.provision_bundle, env.cables[4].id, "Other project")
    with pytest.raises(AuthorizationError):
        env.write(service.splice, slots[0], strands[0], "B",
                  uuid.UUID(other["strands"][0]["id"]), "A", 1)
    assert count(env, FiberSpliceEnd) == 0


def test_inactive_tenant_is_denied(env):
    service, _, _, cassette = topology(env)
    env.tenants[0].active = False
    env.db.commit()
    with pytest.raises(AuthorizationError):
        service.get_cassette(uuid.UUID(cassette["id"]))


def test_project_mutex_does_not_change_project_revision(env):
    service, strands, slots, _ = topology(env)
    before = env.projects[0].version
    env.write(service.splice, slots[0], strands[0], "B", strands[1], "A", 1)
    env.db.refresh(env.projects[0])
    assert env.projects[0].version == before


def test_platform_bypass_and_wrong_tenant_session_denied(env):
    env.db.info["bypass_tenant"] = True
    with pytest.raises(AuthorizationError):
        env.service()
    env.db.info["bypass_tenant"] = False
    env.db.info["tenant_id"] = env.tenants[1].id
    with pytest.raises(AuthorizationError):
        env.service()


def test_self_splice_and_bad_side_denied(env):
    service, strands, slots, _ = topology(env)
    with pytest.raises(ValidationError):
        env.write(service.splice, slots[0], strands[0], "A", strands[0], "B", 1)
    with pytest.raises(ValidationError):
        env.write(service.splice, slots[0], strands[0], "C", strands[1], "B", 1)


@pytest.mark.parametrize("count_value", [0, 577, None])
def test_invalid_cable_strand_count(env, count_value):
    env.cables[0].strand_count = count_value
    env.db.commit()
    with pytest.raises(ValidationError):
        env.write(env.service().provision_bundle, env.cables[0].id, "bad")


def test_concurrent_opposite_splices_do_not_form_cycle(env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.db import set_postgres_tenant_context
    from app.services.fiber import FiberService

    _, strands, slots, _ = topology(env)
    principal, tenant_id = env.principal, env.tenants[0].id
    gate = Barrier(2)

    def writer(number):
        with env.factory() as session:
            set_postgres_tenant_context(session, tenant_id)
            service = FiberService(session, principal)
            gate.wait(timeout=5)
            try:
                service.splice(slots[number], strands[0], "AB"[number],
                               strands[1], "BA"[number], 1)
                session.commit()
                return "committed"
            except ConflictError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, (0, 1)))
    assert sorted(outcomes) == ["committed", "conflict"]
    env.db.expire_all()
    assert count(env, FiberSplice) == 1
