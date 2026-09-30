from __future__ import annotations

import uuid
from dataclasses import dataclass

Node = tuple[str, uuid.UUID, str]


def port_node(port_id: uuid.UUID) -> Node:
    return ("port", port_id, "")


def fiber_node(strand_id: uuid.UUID, side: str) -> Node:
    return ("fiber", strand_id, side)


@dataclass(frozen=True)
class TopologyEdge:
    neighbor: Node
    kind: str
    resource_id: uuid.UUID
