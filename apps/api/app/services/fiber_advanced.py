"""Composed advanced Fiber topology service."""
from app.services.fiber import FiberService
from app.services.fiber_advanced_base import FiberAdvancedBaseMixin
from app.services.fiber_advanced_breakout import FiberAdvancedBreakoutMixin
from app.services.fiber_advanced_channel import FiberAdvancedChannelMixin
from app.services.fiber_advanced_otdr import FiberAdvancedOtdrMixin


class FiberAdvancedService(
    FiberAdvancedOtdrMixin,
    FiberAdvancedBreakoutMixin,
    FiberAdvancedChannelMixin,
    FiberAdvancedBaseMixin,
    FiberService,
):
    MAX_MEMBERS = 600
