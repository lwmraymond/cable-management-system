"""Retirement integration checks use isolated databases, never the local demo store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.api.cable_lifecycle import build_cable_lifecycle_router
from app.exceptions import (
    AuthorizationError,
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.fiber_models import CopperPair, FiberBundle, PhysicalPortClaim
from app.models import (
    AccessGrant,
    AccessGrantStatus,
    AuditEvent,
    Cable,
    CableRouteSegment,
    CableStatus,
    CableTermination,
    Device,
    Pathway,
    PathwaySegment,
    TenantMembership,
    TestRecord as CableTestRecord,
    WorkOrder,
    WorkOrderStatus,
)
from app.security import resolve_principal
from app.services.cable_lifecycle import CableLifecycleService
from app.services.connectivity import ConnectivityService
from app.services.scene import SceneService


def principal(session, world, actor=None):
    return resolve_principal(
        session,
        actor_id=actor or world.admin,
        tenant_id=world.tenant_a,
        project_id=world.project,
        location_id=world.tr,
    )


def work_order(session, world, status=WorkOrderStatus.READY):
    row = WorkOrder(
        tenant_id=world.tenant_a,
        cable_id=world.patch_cord,
        project_id=world.project,
        work_order_number="WO-LIFECYCLE",
        title="Hidden dependency title",
        assigned_organization_id=world.contractor_org,
        created_by=world.admin,
        status=status,
    )
    session.add(row)
    session.flush()
    return row


def test_preview_is_read_only_and_describes_endpoint_impact(world):
    with world.scoped_session() as session:
        who = principal(session, world)
        writes = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
                writes.append(statement)

        event.listen(session.get_bind(), "before_cursor_execute", capture)
        try:
            result = CableLifecycleService(session, who).preview(world.patch_cord)
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture)
        assert result.allowed and result.action == "delete" and result.version == 1
        assert [(p.side, p.device_name) for p in result.endpoints] == [
            ("A", "Switch"),
            ("B", "Patch Panel"),
        ]
        assert result.route_segment_count == 0 and result.blockers == []
        assert writes == [] and not session.dirty and not session.new


def test_delete_releases_exact_claims_routes_and_allows_port_reuse_without_replay(world):
    with world.scoped_session() as session:
        who = principal(session, world)
        pathway = Pathway(
            tenant_id=world.tenant_a,
            location_id=world.tr,
            identifier="LIFECYCLE-TRAY",
            name="Tray",
            pathway_type="tray",
        )
        session.add(pathway)
        session.flush()
        segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=pathway.id,
            name="LIFECYCLE-SEG",
            sequence=1,
            length_m=5,
        )
        session.add(segment)
        session.flush()
        route = CableRouteSegment(
            tenant_id=world.tenant_a,
            cable_id=world.patch_cord,
            pathway_segment_id=segment.id,
            sequence=1,
        )
        session.add(route)
        session.commit()
        old_terms = list(
            session.scalars(
                select(CableTermination)
                .where(CableTermination.cable_id == world.patch_cord)
                .order_by(CableTermination.side)
            )
        )
        old_ports = [row.port_id for row in old_terms]
        other_claims = list(
            session.scalars(
                select(PhysicalPortClaim).where(
                    PhysicalPortClaim.owner_id.not_in([row.id for row in old_terms])
                )
            )
        )
        service = CableLifecycleService(session, who)
        assert service.preview(world.patch_cord).route_segment_count == 1
        result = service.retire(world.patch_cord, action="delete", expected_version=1)
        assert result.model_dump() == {"id": world.patch_cord, "action": "delete", "version": 2}
        session.commit()
        assert all(row.deleted_at is not None for row in old_terms)
        assert route.deleted_at is not None
        assert all(row.deleted_at is None for row in other_claims)
        assert (
            session.scalar(
                select(func.count()).select_from(Cable).where(Cable.id == world.patch_cord)
            )
            == 0
        )
        scene = SceneService(session, who).read()
        assert str(world.patch_cord) not in {row["id"] for row in scene["cables"]}
        assert all(
            not row["occupied"]
            for row in scene["ports"]
            if row["id"] in {str(p) for p in old_ports}
        )
        assert all(str(segment.id) not in row["route_segment_ids"] for row in scene["cables"])
        trace = ConnectivityService(session, who).trace_cable(world.horizontal_cable)
        assert "A-MC-ENG-TR01-PC-00001" not in {row.get("identifier") for row in trace["items"]}
        with pytest.raises(NotFoundError):
            ConnectivityService(session, who).trace_cable(world.patch_cord)
        replacement = ConnectivityService(session, who).create_cable(
            identifier="LIFECYCLE-REPLACEMENT",
            media_type="Cat6A copper",
            construction="patch_cord",
            port_a_id=old_ports[0],
            port_b_id=old_ports[1],
            project_id=world.project,
        )
        session.commit()
        replacement_id = replacement.id
        with pytest.raises(NotFoundError):
            service.retire(world.patch_cord, action="delete", expected_version=1)
        session.rollback()
        assert (
            session.scalar(
                select(func.count())
                .select_from(PhysicalPortClaim)
                .where(PhysicalPortClaim.port_id.in_(old_ports))
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(CableTermination)
                .where(CableTermination.cable_id == replacement_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == world.patch_cord, AuditEvent.action == "cable.deleted"
                )
            )
            == 1
        )
        # Historical identifiers remain reserved, even though physical ports can be reused.
        archived = session.scalar(
            select(Cable)
            .where(Cable.id == world.patch_cord)
            .execution_options(skip_tenant_criteria=True)
        )
        assert archived.deleted_at is not None
        assert archived.installation_status == CableStatus.PLANNED


def test_installed_removal_requires_reason_and_preserves_history(world):
    with world.scoped_session() as session:
        who = principal(session, world)
        cable = session.get(Cable, world.patch_cord)
        cable.installation_status = CableStatus.IN_SERVICE
        test = CableTestRecord(
            tenant_id=world.tenant_a,
            cable_id=cable.id,
            tester_id=world.admin,
            result="PASS",
            measurements={"length_m": 4},
            tested_at=datetime.now(UTC),
        )
        session.add(test)
        order = work_order(session, world, WorkOrderStatus.COMPLETED)
        session.commit()
        version = cable.version
        service = CableLifecycleService(session, who)
        assert service.preview(cable.id).action == "remove"
        with pytest.raises(ValidationError, match="reason"):
            service.retire(cable.id, action="remove", expected_version=version, reason=" ")
        with pytest.raises(ConflictError, match="different action"):
            service.retire(cable.id, action="delete", expected_version=version)
        result = service.retire(
            cable.id, action="remove", expected_version=version, reason="  Replaced damaged cable  "
        )
        session.commit()
        assert result.version == version + 1
        assert cable.deleted_at is not None and cable.installation_status == CableStatus.REMOVED
        assert session.get(CableTestRecord, test.id).deleted_at is None
        assert session.get(WorkOrder, order.id).deleted_at is None
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == cable.id, AuditEvent.action == "cable.removed"
            )
        )
        assert audit.before["status"] == "in_service"
        assert audit.after["reason"] == "Replaced damaged cable"
        assert audit.after["released_endpoints"] == 2


def test_reader_gets_denied_preview_and_cannot_delete(world):
    with world.scoped_session() as session:
        reader = principal(session, world, world.supervisor)
        service = CableLifecycleService(session, reader)
        preview = service.preview(world.patch_cord)
        assert not preview.allowed
        assert [row.code for row in preview.blockers] == ["permission_denied"]
        with pytest.raises(AuthorizationError):
            service.retire(world.patch_cord, action="delete", expected_version=1)
        assert session.get(Cable, world.patch_cord).deleted_at is None


def test_cached_membership_and_grant_do_not_authorize_after_revocation(world):
    with world.scoped_session() as session:
        who = principal(session, world)
        membership = session.scalar(
            select(TenantMembership).where(TenantMembership.user_id == world.admin)
        )
        membership.permissions = ["cable:read"]
        session.commit()
        service = CableLifecycleService(session, who)
        assert not service.preview(world.patch_cord).allowed
        with pytest.raises(AuthorizationError):
            service.retire(world.patch_cord, action="delete", expected_version=1)
        contractor = principal(session, world, world.contractor)
        grant = session.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.status = AccessGrantStatus.REVOKED
        session.commit()
        with pytest.raises(AuthorizationError):
            CableLifecycleService(session, contractor).preview(world.patch_cord)


def test_permissions_are_rechecked_after_waiting_for_lifecycle_lock(world, monkeypatch):
    from app.services import cable_lifecycle

    original = cable_lifecycle.lock_active_cable

    def revoke_after_lock(session, who, cable_id):
        cable = original(session, who, cable_id)
        membership = session.scalar(
            select(TenantMembership).where(TenantMembership.user_id == who.actor_id)
        )
        membership.permissions = ["cable:read"]
        session.flush()
        return cable

    with world.scoped_session() as session:
        who = principal(session, world)
        monkeypatch.setattr(cable_lifecycle, "lock_active_cable", revoke_after_lock)
        with pytest.raises(AuthorizationError):
            CableLifecycleService(session, who).retire(
                world.patch_cord, action="delete", expected_version=1
            )
        assert session.get(Cable, world.patch_cord).deleted_at is None


def test_stale_version_has_no_side_effects(world):
    with world.scoped_session() as session:
        service = CableLifecycleService(session, principal(session, world))
        old = service.preview(world.patch_cord)
        cable = session.get(Cable, world.patch_cord)
        cable.color = "red"
        session.commit()
        with pytest.raises(ConflictError, match="changed"):
            service.retire(cable.id, action="delete", expected_version=old.version)
        assert cable.deleted_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(CableTermination)
                .where(CableTermination.cable_id == cable.id)
            )
            == 2
        )


@pytest.mark.parametrize(
    "dependency,code",
    [
        ("work_order", "active_work_order"),
        ("fiber", "fiber_topology"),
        ("pairs", "copper_topology"),
    ],
)
def test_new_dependencies_after_preview_block_write_without_disclosing_names(
    world, dependency, code
):
    with world.scoped_session() as session:
        service = CableLifecycleService(session, principal(session, world))
        assert service.preview(world.patch_cord).allowed
        if dependency == "work_order":
            work_order(session, world)
        elif dependency == "fiber":
            session.add(
                FiberBundle(
                    tenant_id=world.tenant_a,
                    cable_id=world.patch_cord,
                    name="Secret fiber dependency",
                    strand_count=2,
                )
            )
        else:
            session.add(CopperPair(tenant_id=world.tenant_a, cable_id=world.patch_cord, number=1))
        session.commit()
        preview = service.preview(world.patch_cord)
        assert not preview.allowed and [row.code for row in preview.blockers] == [code]
        assert (
            "Secret" not in preview.model_dump_json() and "Hidden" not in preview.model_dump_json()
        )
        with pytest.raises(ConflictError):
            service.retire(world.patch_cord, action="delete", expected_version=preview.version)
        assert session.get(Cable, world.patch_cord).deleted_at is None


def test_both_endpoints_and_every_route_must_be_in_grant_scope(world):
    with world.scoped_session() as session:
        grant = session.scalar(
            select(AccessGrant).where(AccessGrant.subject_user_id == world.contractor)
        )
        grant.permissions = [*grant.permissions, "cable:delete"]
        session.commit()
        who = principal(session, world, world.contractor)
        service = CableLifecycleService(session, who)
        assert service.preview(world.patch_cord).allowed
        device = session.get(Device, world.switch)
        device.location_id = world.other_building
        session.commit()
        with pytest.raises(AuthorizationError):
            service.preview(world.patch_cord)
        with pytest.raises(AuthorizationError):
            service.retire(world.patch_cord, action="delete", expected_version=1)
        device.location_id = world.tr
        pathway = Pathway(
            tenant_id=world.tenant_a,
            location_id=world.other_building,
            identifier="HIDDEN-TRAY",
            name="Hidden route",
            pathway_type="tray",
        )
        session.add(pathway)
        session.flush()
        segment = PathwaySegment(
            tenant_id=world.tenant_a,
            pathway_id=pathway.id,
            name="HIDDEN-SEG",
            sequence=1,
            length_m=5,
        )
        session.add(segment)
        session.flush()
        session.add(
            CableRouteSegment(
                tenant_id=world.tenant_a,
                cable_id=world.patch_cord,
                pathway_segment_id=segment.id,
                sequence=1,
            )
        )
        session.commit()
        with pytest.raises(AuthorizationError):
            service.retire(world.patch_cord, action="delete", expected_version=1)


def test_cross_tenant_ids_are_not_found_even_if_cached(world):
    with world.scoped_session(world.tenant_b) as session:
        foreign = Cable(
            tenant_id=world.tenant_b,
            identifier="PRIVATE-CABLE",
            media_type="copper",
            construction="patch_cord",
        )
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    with world.scoped_session() as session:
        session.scalar(
            select(Cable).where(Cable.id == foreign_id).execution_options(skip_tenant_criteria=True)
        )
        service = CableLifecycleService(session, principal(session, world))
        with pytest.raises(NotFoundError):
            service.preview(foreign_id)
        with pytest.raises(NotFoundError):
            service.retire(foreign_id, action="delete", expected_version=1)


@pytest.fixture
def client(world):
    app = FastAPI()

    def get_db():
        with world.scoped_session() as session:
            yield session

    def get_principal(request: Request, session=Depends(get_db)):
        return principal(
            session, world, world.supervisor if request.headers.get("X-Reader") else None
        )

    app.include_router(build_cable_lifecycle_router(get_db, get_principal), prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    with TestClient(app) as test_client:
        yield test_client


def test_http_preview_permissions_versions_delete_and_replay(client, world):
    url = f"/api/v1/cables/{world.patch_cord}"
    assert client.get(f"{url}/deletion-preview").json()["allowed"] is True
    assert (
        client.get(f"{url}/deletion-preview", headers={"X-Reader": "1"}).json()["allowed"] is False
    )
    assert (
        client.delete(url, params={"expected_version": 1}, headers={"X-Reader": "1"}).status_code
        == 403
    )
    assert client.delete(url).status_code == 422
    assert client.delete(url, params={"expected_version": 0}).status_code == 422
    assert client.delete(url, params={"expected_version": 9}).status_code == 409
    response = client.delete(url, params={"expected_version": 1})
    assert response.status_code == 200 and response.json() == {
        "id": str(world.patch_cord),
        "action": "delete",
        "version": 2,
    }
    assert client.delete(url, params={"expected_version": 1}).status_code == 404
    assert client.get(f"{url}/deletion-preview").status_code == 404


def test_http_removal_input_status_and_success(client, world):
    url = f"/api/v1/cables/{world.patch_cord}/remove"
    for body in (
        {"expected_version": True, "reason": "valid"},
        {"expected_version": 1, "reason": " "},
        {"expected_version": 1, "reason": "x" * 1001},
        {"expected_version": 1, "reason": "valid", "tenant_id": str(uuid.uuid4())},
    ):
        assert client.post(url, json=body).status_code == 422
    assert client.post(url, json={"expected_version": 1, "reason": "valid"}).status_code == 409
    with world.scoped_session() as session:
        cable = session.get(Cable, world.patch_cord)
        cable.installation_status = CableStatus.INSTALLED
        session.commit()
        version = cable.version
    result = client.post(url, json={"expected_version": version, "reason": "Replacement"})
    assert result.status_code == 200 and result.json()["action"] == "remove"


@pytest.mark.parametrize("writer", ["bundle", "pairs", "work_order", "otdr", "label"])
def test_concurrent_new_references_wait_for_retirement_and_fail_closed(world, tmp_path, writer):
    """Two independent SQLite connections exercise the actual database writer mutex."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.fiber_models import OtdrRecord
    from app.models import Label
    from app.schemas import WorkOrderCreate
    from app.services.account_scope_endpoints import AccountScopeEndpoints
    from app.services.fiber import FiberService
    from app.services.fiber_advanced import FiberAdvancedService
    from app.services.labels import LabelService

    with world.scoped_session() as session:
        cable = session.get(Cable, world.patch_cord)
        if writer in {"bundle", "otdr"}:
            cable.media_type = "OS2"
            cable.strand_count = 2
        elif writer == "pairs":
            cable.pair_count = 4
        session.commit()
        expected_version = cable.version
    file_path = tmp_path / "concurrent-cable-lifecycle.db"
    source = world.session_factory.kw["bind"].raw_connection()
    try:
        with sqlite3.connect(file_path) as target:
            source.driver_connection.backup(target)
    finally:
        source.close()
    engine = create_engine(
        f"sqlite:///{file_path}", connect_args={"check_same_thread": False, "timeout": 5}
    )
    factory = sessionmaker(engine, class_=Session, autoflush=False, expire_on_commit=False)
    attempted_lock = Event()

    def capture(conn, _cursor, statement, _params, _context, _many):
        if conn.info.get("waiting_writer") and statement.lstrip().upper().startswith(
            "UPDATE PROJECTS"
        ):
            attempted_lock.set()

    event.listen(engine, "before_cursor_execute", capture)

    def attempt_write():
        with factory() as session:
            session.info["tenant_id"] = world.tenant_a
            session.connection().info["waiting_writer"] = True
            who = principal(session, world)
            try:
                if writer == "bundle":
                    FiberService(session, who).provision_bundle(
                        world.patch_cord, "Concurrent bundle"
                    )
                elif writer == "pairs":
                    FiberAdvancedService(session, who).provision_pairs(world.patch_cord)
                elif writer == "work_order":
                    order = AccountScopeEndpoints(session, who).work_order(
                        WorkOrderCreate(
                            project_id=world.project,
                            cable_id=world.patch_cord,
                            work_order_number="WO-CONCURRENT",
                            title="Concurrent order",
                            assigned_organization_id=world.contractor_org,
                        )
                    )
                    session.add(order)
                elif writer == "otdr":
                    FiberAdvancedService(session, who).create_otdr_record(
                        project_id=world.project,
                        cable_id=world.patch_cord,
                        strand_id=None,
                        direction="A",
                        wavelength_nm=1310,
                        acquired_at=datetime.now(UTC),
                        source_name="Concurrent trace",
                    )
                else:
                    LabelService(session, who).create_cable_label(
                        world.patch_cord, "https://app.example"
                    )
                session.commit()
                return "unexpected success"
            except (NotFoundError, ConflictError) as error:
                session.rollback()
                return type(error).__name__

    try:
        with factory() as deleting, ThreadPoolExecutor(max_workers=1) as pool:
            deleting.info["tenant_id"] = world.tenant_a
            service = CableLifecycleService(deleting, principal(deleting, world))
            service.retire(world.patch_cord, action="delete", expected_version=expected_version)
            pending = pool.submit(attempt_write)
            reached_lock = attempted_lock.wait(timeout=5)
            deleting.commit()
            assert reached_lock, "Reference writer did not reach the lifecycle mutex"
            assert pending.result(timeout=8) == "NotFoundError"
        with factory() as session:
            session.info["tenant_id"] = world.tenant_a
            for model in (FiberBundle, CopperPair, WorkOrder, OtdrRecord, Label):
                assert session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        engine.dispose()
