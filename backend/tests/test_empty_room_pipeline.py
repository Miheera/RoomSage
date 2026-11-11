from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from services.empty_room_pipeline import (
    EMPTY_ROOM_GUARD_MESSAGE,
    RoomMasks,
    apply_surface_edits,
    assert_same_size,
    enforce_empty_room,
    resize_longest_side,
)
from config import settings


def _mask(size: tuple[int, int], points: list[tuple[int, int]]) -> Image.Image:
    array = np.zeros((size[1], size[0]), dtype=np.uint8)
    for x, y in points:
        array[y, x] = 255
    return Image.fromarray(array, mode="L")


def _texture_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 24), (160, 115, 70)).save(output, format="PNG")
    return output.getvalue()


def test_resize_preserves_aspect_ratio_and_caps_longest_side() -> None:
    result = resize_longest_side(Image.new("RGB", (1600, 900)), 768)

    assert result.size == (768, 432)


def test_geometry_guard_reports_mismatched_stage_size() -> None:
    with pytest.raises(ValueError, match="Pipeline stage 'bad' changed image size"):
        assert_same_size("bad", Image.new("RGB", (20, 10)), Image.new("RGB", (10, 10)))


def test_furniture_guard_rejects_only_more_than_fifteen_percent() -> None:
    enforce_empty_room(15.0)

    with pytest.raises(ValueError, match=f"^{EMPTY_ROOM_GUARD_MESSAGE}$"):
        enforce_empty_room(15.01)


def test_surface_edits_leave_every_pixel_outside_wall_and_floor_masks_unchanged() -> None:
    size = (64, 48)
    source_array = np.arange(size[0] * size[1] * 3, dtype=np.uint8).reshape(
        size[1], size[0], 3
    )
    source = Image.fromarray(source_array, mode="RGB")
    wall = np.zeros((size[1], size[0]), dtype=np.uint8)
    wall[4:22, 4:30] = 255
    floor = np.zeros_like(wall)
    floor[30:46, 8:57] = 255
    ceiling = np.zeros_like(wall)
    window = np.zeros_like(wall)
    door = np.zeros_like(wall)
    curtain = np.zeros_like(wall)
    window[8:14, 12:20] = 255
    door[14:20, 22:27] = 255
    curtain[4:10, 4:8] = 255
    masks = RoomMasks(
        wall=Image.fromarray(wall),
        floor=Image.fromarray(floor),
        ceiling=Image.fromarray(ceiling),
        window=Image.fromarray(window),
        door=Image.fromarray(door),
        curtain=Image.fromarray(curtain),
        furniture=Image.new("L", size, 0),
        furniture_percent=0.0,
    )

    edited, metadata = apply_surface_edits(
        source,
        masks,
        paint={"primary_color": "warm beige"},
        flooring={"floor_texture_bytes": _texture_bytes()},
    )

    editable = (wall > 0) & (ceiling == 0) & (window == 0) & (door == 0) & (curtain == 0)
    editable |= floor > 0
    assert np.array_equal(source_array[~editable], np.asarray(edited)[~editable])
    assert metadata["floor"]["fallback"] is False
    assert metadata["wall"]["applied"] is True
    assert_same_size("test result", source, edited)


def test_baseboard_strip_is_never_recolored_and_selected_paint_resolves_from_catalog() -> None:
    size = (40, 32)
    source_array = np.full((size[1], size[0], 3), (180, 170, 160), dtype=np.uint8)
    source = Image.fromarray(source_array, mode="RGB")
    wall = np.zeros((size[1], size[0]), dtype=np.uint8)
    wall[2:24, 2:38] = 255
    baseboard = np.zeros_like(wall)
    baseboard[21:24, 2:38] = 255
    empty = Image.new("L", size, 0)
    masks = RoomMasks(
        wall=Image.fromarray(wall), floor=empty, ceiling=empty, window=empty,
        door=empty, curtain=empty, furniture=empty, furniture_percent=0,
        baseboard=Image.fromarray(baseboard),
    )

    edited, metadata = apply_surface_edits(
        source, masks,
        paint={
            "catalog_item_id": "PAINT-JAP-087",
            "primary_color": "Sugared Nut",
            "prompt_phrase": "warm soft beige wall paint",
        },
        palette="Warm Earth",
    )

    assert metadata["wall"]["color_hex"] == "#CDC2AA"
    assert metadata["wall"]["source_color"] == "Sugared Nut"
    assert metadata["wall"]["palette"] == "Warm Earth"
    assert np.array_equal(source_array[baseboard > 0], np.asarray(edited)[baseboard > 0])


def test_floor_texture_tile_scale_is_image_width_configurable() -> None:
    size = (80, 48)
    floor = np.zeros((size[1], size[0]), dtype=np.uint8)
    floor[12:46, 4:76] = 255
    empty = Image.new("L", size, 0)
    masks = RoomMasks(
        wall=empty, floor=Image.fromarray(floor), ceiling=empty, window=empty,
        door=empty, curtain=empty, furniture=empty, furniture_percent=0,
    )
    _, metadata = apply_surface_edits(
        Image.new("RGB", size, (110, 110, 110)), masks,
        flooring={"floor_texture_bytes": _texture_bytes()},
    )
    assert metadata["floor"]["tile_width_px"] == max(
        8, round(size[0] * settings.hf_floor_texture_tile_width_fraction)
    )
    assert metadata["floor"]["texture_contrast"] == settings.hf_floor_texture_contrast


def test_floor_perspective_failure_records_luminance_tint_fallback() -> None:
    size = (32, 24)
    floor = np.zeros((size[1], size[0]), dtype=np.uint8)
    floor[12, 12:16] = 255
    empty = Image.new("L", size, 0)
    masks = RoomMasks(
        wall=empty,
        floor=Image.fromarray(floor),
        ceiling=empty,
        window=empty,
        door=empty,
        curtain=empty,
        furniture=empty,
        furniture_percent=0.0,
    )

    _, metadata = apply_surface_edits(
        Image.new("RGB", size, (120, 130, 140)),
        masks,
        flooring={"floor_texture_bytes": _texture_bytes()},
    )

    assert metadata["floor"]["fallback"] is True
    assert metadata["floor"]["reason"]
    assert metadata["floor"]["method"] == "luminance_preserving_texture_tint"


def test_perspective_floor_texture_keeps_original_floor_luminance() -> None:
    size = (64, 48)
    floor = np.zeros((size[1], size[0]), dtype=np.uint8)
    floor[12:46, 4:60] = 255
    empty = Image.new("L", size, 0)
    masks = RoomMasks(
        wall=empty,
        floor=Image.fromarray(floor),
        ceiling=empty,
        window=empty,
        door=empty,
        curtain=empty,
        furniture=empty,
        furniture_percent=0.0,
    )
    source = Image.new("RGB", size, (110, 110, 110))
    bright_tile = BytesIO()
    Image.new("RGB", (64, 64), (245, 239, 220)).save(bright_tile, format="PNG")

    edited, metadata = apply_surface_edits(
        source,
        masks,
        flooring={"floor_texture_bytes": bright_tile.getvalue()},
    )

    output_luma = np.asarray(edited.convert("L"))[floor > 0]
    assert abs(float(np.median(output_luma)) - 110) <= 3
    assert metadata["floor"]["fallback"] is False
