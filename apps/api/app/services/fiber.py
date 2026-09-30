"""Composed Fiber service. Implementation lives in focused mixins."""
from app.services.fiber_core import FiberCoreMixin
from app.services.fiber_splice import FiberSpliceMixin
from app.services.fiber_walk import FiberWalkMixin


class FiberService(FiberSpliceMixin, FiberWalkMixin, FiberCoreMixin):
    MAX_TRACE_HOPS = 256
