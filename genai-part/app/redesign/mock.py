"""Weight-free stand-in that keeps the API flow working. The output is the input photo,
tinted and stamped MOCK. It is not a redesign and is never presented as one."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from app.redesign.base import DetectedObject, EditScope, RenderRequest, RenderResult

_TINTS = {
    "white": (235, 232, 226), "off white": (232, 226, 214), "beige": (214, 197, 170),
    "grey": (160, 160, 165), "light grey": (205, 205, 208), "charcoal": (70, 72, 78),
    "black": (50, 50, 55), "brown": (130, 95, 70), "terracotta": (190, 110, 80),
    "sand": (214, 195, 160), "maroon": (120, 45, 55), "cream": (240, 228, 205), "natural": (205, 180, 145),
}


class MockRenderer:
    def render(self, request: RenderRequest) -> RenderResult:
        tint = next((_TINTS[c] for c in request.palette if c in _TINTS), (200, 195, 185))
        with Image.open(request.image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((768, 768))
        blended = Image.blend(image, Image.new("RGB", image.size, tint), 0.35)
        draw = ImageDraw.Draw(blended)
        draw.rectangle((0, 0, blended.width, 24), fill=(0, 0, 0))
        draw.text((8, 6), "MOCK PREVIEW - no model was run", fill=(255, 255, 255))
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        blended.save(request.output_path)
        return RenderResult(
            image_path=request.output_path,
            is_mock=True,
            scope_used=EditScope(request.scope),
            limitations=["Mock mode: a tinted copy of the photo, not a generated redesign."],
        )

    def analyze(self, image_path: Path) -> list[DetectedObject]:
        return []
