"""Advanced Fiber mixin: FiberAdvancedChannelMixin."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update

from app.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.fiber_models import (
    ChannelMember,
    ConnectivityChannel,
    CopperPair,
    FiberBreakout,
    FiberBreakoutLeg,
    FiberEndpointClaim,
    FiberPortTermination,
    FiberSplice,
    FiberSpliceEnd,
    OtdrEvent,
    OtdrRecord,
    PhysicalPortClaim,
)
from app.models import Cable, CableTermination, Device, Port, utcnow

class FiberAdvancedChannelMixin:
    def create_channel(
        self,
        *,
        project_id: uuid.UUID,
        identifier: str,
        name: str,
        medium: str,
        topology: str,
        members: list[dict[str, Any]],
        status: str = "planned",
    ) -> dict[str, Any]:
        identifier = self._identifier(identifier)
        name = self._name(name)
        if medium not in {"fiber", "copper"}:
            raise ValidationError("Channel medium must be fiber or copper")
        if topology not in {"simplex", "duplex", "quad", "bundle", "ethernet"}:
            raise ValidationError("Unsupported channel topology")
        if status not in {"planned", "active", "reserved", "retired"}:
            raise ValidationError("Unsupported channel status")
        if not isinstance(members, list) or not 1 <= len(members) <= self.MAX_MEMBERS:
            raise ValidationError("Channel requires 1 to 600 members")
        fixed_cardinality = {"simplex": 1, "duplex": 2, "quad": 4}.get(topology)
        if fixed_cardinality is not None and len(members) != fixed_cardinality:
            raise ValidationError(
                f"{topology} channel requires exactly {fixed_cardinality} member(s)"
            )
        self._authorize("write", project_id)
        self._project_lock(project_id)
        normalized: list[tuple[str, Any, str]] = []
        seen: set[tuple[str, uuid.UUID]] = set()
        for raw in members:
            if not isinstance(raw, dict):
                raise ValidationError("Each channel member must be an object")
            kind = raw.get("kind")
            try:
                resource_id = uuid.UUID(str(raw.get("resource_id")))
            except (TypeError, ValueError):
                raise ValidationError("Channel member resource_id must be a UUID") from None
            raw_role = raw.get("role", "member")
            if not isinstance(raw_role, str) or not 1 <= len(raw_role.strip()) <= 80:
                raise ValidationError("Channel member role must contain 1 to 80 characters")
            role = raw_role.strip()
            key = (kind, resource_id)
            if key in seen:
                raise ValidationError("Duplicate channel member")
            seen.add(key)
            if kind == "fiber_strand":
                strand, cable = self._strand(resource_id)
                if medium != "fiber" or cable.project_id != project_id:
                    raise AuthorizationError("Fiber channel member is outside the channel project")
                normalized.append((kind, strand, role))
            elif kind == "copper_pair":
                pair = self._get(CopperPair, resource_id)
                cable = self._copper_cable(pair.cable_id)
                if medium != "copper" or cable.project_id != project_id:
                    raise AuthorizationError("Copper channel member is outside the channel project")
                normalized.append((kind, pair, role))
            else:
                raise ValidationError("Channel member kind must be fiber_strand or copper_pair")
        channel = ConnectivityChannel(
            tenant_id=self.tenant_id,
            project_id=project_id,
            identifier=identifier,
            name=name,
            medium=medium,
            topology=topology,
            status=status,
        )
        self.db.add(channel)
        self.db.flush()
        rows = []
        for sequence, (kind, resource, role) in enumerate(normalized, start=1):
            row = ChannelMember(
                tenant_id=self.tenant_id,
                channel_id=channel.id,
                sequence=sequence,
                role=role,
                fiber_strand_id=resource.id if kind == "fiber_strand" else None,
                copper_pair_id=resource.id if kind == "copper_pair" else None,
            )
            rows.append(row)
        self.db.add_all(rows)
        self.db.flush()
        self._audit("channel.created", channel, project_id, after={
            "identifier": identifier, "medium": medium, "member_count": len(rows),
        })
        return self.get_channel(channel.id, action="write")

    def get_channel(self, channel_id: uuid.UUID, *, action: str = "read") -> dict[str, Any]:
        channel = self._get(ConnectivityChannel, channel_id)
        self._authorize(action, channel.project_id)
        members = self.db.scalars(select(ChannelMember).where(
            ChannelMember.tenant_id == self.tenant_id,
            ChannelMember.channel_id == channel.id,
            ChannelMember.deleted_at.is_(None),
        ).order_by(ChannelMember.sequence).limit(self.MAX_MEMBERS)).all()
        return {
            "id": str(channel.id),
            "version": channel.version,
            "project_id": str(channel.project_id),
            "identifier": channel.identifier,
            "name": channel.name,
            "medium": channel.medium,
            "topology": channel.topology,
            "status": channel.status,
            "members": [{
                "id": str(member.id),
                "sequence": member.sequence,
                "role": member.role,
                "kind": "fiber_strand" if member.fiber_strand_id else "copper_pair",
                "resource_id": str(member.fiber_strand_id or member.copper_pair_id),
            } for member in members],
        }

    def release_channel(self, channel_id: uuid.UUID, expected_version: int) -> dict[str, Any]:
        channel = self._get(ConnectivityChannel, channel_id)
        self._authorize("write", channel.project_id)
        self._project_lock(channel.project_id)
        before = self.get_channel(channel.id, action="write")
        now = utcnow()
        self._atomic_update(
            ConnectivityChannel,
            channel.id,
            expected_version,
            "Channel changed; reload it before retrying",
            deleted_at=now,
            updated_at=now,
        )
        self.db.execute(update(ChannelMember).where(
            ChannelMember.tenant_id == self.tenant_id,
            ChannelMember.channel_id == channel.id,
            ChannelMember.deleted_at.is_(None),
        ).values(
            deleted_at=now,
            updated_at=now,
            version=ChannelMember.version + 1,
        ))
        self._audit("channel.released", channel, channel.project_id, before=before)
        return {
            "id": str(channel.id),
            "version": self._version(expected_version) + 1,
            "released": True,
        }
