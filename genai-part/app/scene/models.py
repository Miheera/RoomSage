from pydantic import BaseModel, ConfigDict, Field

from app.core.models import Category, RoomType


class RoomDimensions(BaseModel):
    length_m: float | None = Field(default=None, gt=0, le=50)
    width_m: float | None = Field(default=None, gt=0, le=50)
    height_m: float | None = Field(default=None, gt=0, le=10)

    @property
    def floor_area_m2(self) -> float | None:
        if self.length_m is None or self.width_m is None:
            return None
        return round(self.length_m * self.width_m, 2)


class SceneObject(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    category: Category | None = None
    color: str | None = None
    material: str | None = None
    # Person A's Gaussian label. Only meaningful after a person has reviewed it.
    mask_id: int | None = Field(default=None, gt=0)


class SceneInfo(BaseModel):
    """What is known about the room. Every field but the room type may be absent."""

    model_config = ConfigDict(extra="ignore")

    room_type: RoomType = RoomType.LIVING_ROOM
    dimensions: RoomDimensions | None = None
    objects: list[SceneObject] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    person_a_project_id: str | None = None
    depth_map_available: bool = False
    # Which parts of the scene were not supplied, for the UI and the spec's assumptions.
    missing_fields: list[str] = Field(default_factory=list)
