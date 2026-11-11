"""Essential furniture roles and sensible quantity ranges by room type."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRequirement:
    role: str
    minimum_quantity: int
    maximum_quantity: int


ROOM_ROLE_REQUIREMENTS = {
    "living_room": (
        RoleRequirement("sofa", 1, 1),
        RoleRequirement("chair", 1, 2),
        RoleRequirement("coffee_table", 1, 1),
        RoleRequirement("lighting", 1, 2),
        RoleRequirement("rug", 1, 1),
    ),
    "bedroom": (
        RoleRequirement("bed", 1, 1),
        RoleRequirement("storage", 1, 2),
        RoleRequirement("lighting", 1, 2),
        RoleRequirement("rug", 1, 1),
    ),
    "dining_room": (
        RoleRequirement("dining_table", 1, 1),
        RoleRequirement("chair", 2, 6),
        RoleRequirement("lighting", 1, 2),
        RoleRequirement("rug", 1, 1),
    ),
}
