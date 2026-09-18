from __future__ import annotations

import io
import uuid

import qrcode
import qrcode.image.svg
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.models import Cable, Label
from app.security import Principal, require_permission
from app.services.cable_lifecycle import lock_active_cable
from app.services.resource_scope import ResourceScope
from app.services.identifiers import get_active_profile


class LabelService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def create_cable_label(self, cable_id: uuid.UUID, public_base_url: str) -> tuple[Label, str]:
        scope = ResourceScope(self.session, self.principal)
        require_permission(scope.principal, "label:create")
        cable = scope.get(Cable, cable_id)
        scope.require(cable, "label:create")
        cable = lock_active_cable(self.session, self.principal, cable.id)
        scope = ResourceScope(self.session, self.principal)
        scope.require(cable, "label:create")
        profile = get_active_profile(self.session, self.principal.tenant_id)
        payload = f"{public_base_url.rstrip('/')}/app/?fieldCable={cable.id}"
        label = Label(
            tenant_id=self.principal.tenant_id,
            standard_profile_id=profile.id,
            object_type="cable",
            object_id=cable.id,
            identifier=cable.identifier,
            template_name="cable-default",
            qr_payload=payload,
            created_by=self.principal.actor_id,
        )
        self.session.add(label)
        self.session.flush()
        qr = qrcode.QRCode(border=2, box_size=6)
        qr.add_data(payload)
        qr.make(fit=True)
        image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buffer = io.BytesIO()
        image.save(buffer)
        record_audit(
            self.session,
            principal=self.principal,
            action="label.generated",
            object_type="label",
            object_id=label.id,
            after={"object_type": "cable", "object_id": str(cable.id)},
            project_id=cable.project_id,
        )
        return label, buffer.getvalue().decode("utf-8")
