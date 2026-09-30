"""Composed bounded physical topology trace service."""
from app.services.fiber_advanced import FiberAdvancedService
from app.services.topology_trace_describe import TopologyTraceDescribeMixin
from app.services.topology_trace_entry import TopologyTraceEntryMixin
from app.services.topology_trace_graph import TopologyTraceGraphMixin
from app.services.topology_trace_path import TopologyTracePathMixin
from app.services.topology_trace_types import Node, TopologyEdge, fiber_node, port_node


class TopologyTraceService(
    TopologyTraceEntryMixin,
    TopologyTraceDescribeMixin,
    TopologyTracePathMixin,
    TopologyTraceGraphMixin,
    FiberAdvancedService,
):
    MAX_TRACE_NODES = 1_000
    MAX_OTDR_RECORDS = 100


__all__ = [
    "Node", "TopologyEdge", "TopologyTraceService", "fiber_node", "port_node"
]
