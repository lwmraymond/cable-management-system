from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Cable, CableTermination, Device, Label, Location, Port, Rack
from app.security import Principal
from app.services.identifiers import get_active_profile
from app.services.account_scope_endpoints import AccountScopeEndpoints


class ComplianceService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def _select(self, model):
        return select(model).where(
            model.tenant_id == self.principal.tenant_id, model.deleted_at.is_(None),
        )

    def report(self) -> dict[str, Any]:
        # Complete compliance reports and scores are tenant-wide, not partial grants.
        AccountScopeEndpoints(self.session, self.principal).require_member("compliance:read")
        profile = get_active_profile(self.session, self.principal.tenant_id)
        compiled = re.compile(
            profile.rules_json.get("identifier_regex", r"^[A-Z0-9][A-Z0-9-]{2,179}$")
        )
        findings: list[dict[str, Any]] = []
        compliant = checked = 0
        resources = [
            ("location", self.session.scalars(self._select(Location)).all(), "identifier"),
            ("rack", self.session.scalars(self._select(Rack)).all(), "rack_identifier"),
            ("device", self.session.scalars(self._select(Device)).all(), "identifier"),
            ("cable", self.session.scalars(self._select(Cable)).all(), "identifier"),
        ]
        for object_type, rows, attribute in resources:
            counts = Counter(getattr(row, attribute) for row in rows)
            for row in rows:
                checked += 1
                identifier = getattr(row, attribute)
                if counts[identifier] > 1:
                    findings.append(
                        {
                            "severity": "error",
                            "type": "duplicate_identifier",
                            "object_type": object_type,
                            "object_id": str(row.id),
                            "identifier": identifier,
                            "recommendation": "Assign a unique tenant-scoped identifier.",
                        }
                    )
                elif not identifier or not compiled.fullmatch(identifier):
                    findings.append(
                        {
                            "severity": "warning",
                            "type": "invalid_label",
                            "object_type": object_type,
                            "object_id": str(row.id),
                            "identifier": identifier,
                            "recommendation": "Regenerate the identifier using the active profile.",
                        }
                    )
                else:
                    compliant += 1
        labels = {
            (row.object_type, row.object_id)
            for row in self.session.scalars(self._select(Label)).all()
        }
        for cable in self.session.scalars(self._select(Cable)).all():
            terms = self.session.scalars(
                self._select(CableTermination).where(CableTermination.cable_id == cable.id)
            ).all()
            if len(terms) != 2:
                findings.append(
                    {
                        "severity": "error",
                        "type": "inconsistent_endpoints",
                        "object_type": "cable",
                        "object_id": str(cable.id),
                        "identifier": cable.identifier,
                        "recommendation": "Document exactly two physical terminations.",
                    }
                )
            if ("cable", cable.id) not in labels:
                findings.append(
                    {
                        "severity": "warning",
                        "type": "missing_label",
                        "object_type": "cable",
                        "object_id": str(cable.id),
                        "identifier": cable.identifier,
                        "recommendation": "Generate a label from the active profile.",
                    }
                )
            if (
                cable.installation_status.value in {"installed", "terminated", "tested", "in_service"}
                and not cable.test_status
            ):
                findings.append(
                    {
                        "severity": "warning",
                        "type": "unverified_installation",
                        "object_type": "cable",
                        "object_id": str(cable.id),
                        "identifier": cable.identifier,
                        "recommendation": "Upload and approve the applicable certification result.",
                    }
                )
        terminated_ports = {
            row.port_id for row in self.session.scalars(self._select(CableTermination)).all()
        }
        mapped_ports = {
            port_id
            for row in self.session.scalars(self._select(Port)).all()
            for port_id in ([row.id] if row.id in terminated_ports else [])
        }
        _ = mapped_ports  # explicit placeholder for future disconnected mapping rules
        score = round(compliant / checked * 100, 2) if checked else 100.0
        return {
            "profile": {
                "id": str(profile.id),
                "name": profile.name,
                "family": profile.standard_family,
                "edition": profile.edition,
                "mode": "assisted",
            },
            "summary": {
                "checked": checked,
                "compliant_objects": compliant,
                "finding_count": len(findings),
                "score_percent": score,
            },
            "findings": findings,
            "legal_notice": (
                "This report applies configured policy rules and is not a legal certification "
                "of proprietary TIA standard clauses."
            ),
        }
