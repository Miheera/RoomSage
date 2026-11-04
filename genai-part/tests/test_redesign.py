import numpy as np
from PIL import Image

from app.core.models import Category
from app.redesign import segmentation as seg
from app.redesign.base import EditScope, RenderRequest
from app.redesign.diffusion import DiffusionRenderer, _load_rgb, MIN_MASK_FRACTION
from app.redesign.mock import MockRenderer
from app.config import Settings

ID2LABEL = {0: "wall", 1: "floor, flooring", 2: "ceiling", 3: "windowpane, window", 4: "sofa, couch, lounge",
            5: "bed", 6: "rug, carpet, carpeting", 7: "sky"}


def strip_scene() -> np.ndarray:
    """A 2 x 8 'image' with one pixel of each class."""
    return np.array([[0, 1, 2, 3, 4, 5, 6, 7], [0, 1, 2, 3, 4, 5, 6, 7]])


def masked_labels(mask: np.ndarray) -> set[str]:
    return {seg.primary_label(ID2LABEL[int(i)]) for i in np.unique(strip_scene()[mask])}


def test_furniture_scope_leaves_the_shell_alone():
    mask = seg.build_mask(strip_scene(), ID2LABEL, EditScope.FURNITURE)
    assert masked_labels(mask) == {"sofa", "bed", "rug"}


def test_full_scope_repaints_walls_but_not_openings():
    mask = seg.build_mask(strip_scene(), ID2LABEL, EditScope.FULL)
    labels = masked_labels(mask)
    assert {"wall", "floor", "sofa"} <= labels
    assert not labels & {"windowpane", "ceiling", "sky"}


def test_targeted_scope_touches_only_the_target():
    mask = seg.build_mask(strip_scene(), ID2LABEL, EditScope.TARGETED, [Category.RUG])
    assert masked_labels(mask) == {"rug"}


def test_kept_categories_are_never_masked():
    mask = seg.build_mask(strip_scene(), ID2LABEL, EditScope.FURNITURE, keep_categories=[Category.BED])
    assert "bed" not in masked_labels(mask)


def test_dilation_never_grows_into_protected_or_kept_pixels():
    scene = np.full((40, 40), 4)  # sofa everywhere
    scene[:, :10] = 3  # window
    scene[:, 30:] = 5  # bed, kept
    mask = scene == 4
    grown = seg.dilate_mask(mask, scene, ID2LABEL, radius=6, keep_categories=[Category.BED])
    assert not grown[:, :10].any() and not grown[:, 30:].any()


def test_composite_restores_original_outside_the_mask():
    original = Image.new("RGB", (64, 64), (10, 20, 30))
    generated = Image.new("RGB", (64, 64), (200, 200, 200))
    mask = np.zeros((64, 64), dtype=bool)
    mask[24:40, 24:40] = True
    result = np.asarray(seg.soft_composite(original, generated, mask, feather=1))
    assert tuple(result[2, 2]) == (10, 20, 30)
    assert tuple(result[32, 32]) == (200, 200, 200)


def test_image_is_resized_to_vae_multiples(tmp_path):
    path = tmp_path / "wide.png"
    Image.new("RGB", (1280, 854)).save(path)
    image = _load_rgb(path, 512)
    assert image.width <= 512 and image.width % 8 == 0 and image.height % 8 == 0


def test_empty_room_falls_back_to_full_scope():
    renderer = DiffusionRenderer(Settings(_env_file=None))
    scene = np.zeros((100, 100), dtype=int)
    scene[50:] = 1
    request = RenderRequest(image_path="a.png", output_path="b.png", prompt="p", negative_prompt="n")
    mask, scope, notes = renderer._resolve_mask(request, scene, ID2LABEL)
    assert scope is EditScope.FULL and notes and mask.mean() > MIN_MASK_FRACTION


def test_mock_output_is_labelled(tmp_path):
    source = tmp_path / "room.jpg"
    Image.new("RGB", (900, 600), (120, 130, 140)).save(source)
    result = MockRenderer().render(RenderRequest(
        image_path=source, output_path=tmp_path / "out" / "preview.png",
        prompt="p", negative_prompt="n", palette=["beige"]))
    assert result.is_mock and "Mock" in result.limitations[0]
    assert result.image_path.exists()
