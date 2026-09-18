"""Compatibility names for the canonical revision-006 floor-plan tables."""

from sqlalchemy.orm import synonym

from app.floorplan_models import FLOOR_PLAN_TABLES, FloorPlan, FloorPlanRevision

# The earlier standalone editor used different Python names for the same data.
FloorPlan.unit = synonym("units")
FloorPlan.width_mm = synonym("canvas_width")
FloorPlan.height_mm = synonym("canvas_height")
FloorPlan.head_revision = synonym("current_revision_number")
FloorPlan.published_revision = synonym("published_revision_number")
FloorPlan.background_object_key = synonym("background_reference")
FloorPlanRevision.revision = synonym("revision_number")
FloorPlanRevision.note = synonym("change_summary")

__all__ = ["FLOOR_PLAN_TABLES", "FloorPlan", "FloorPlanRevision"]
