from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from app.config import Settings
from app.core.store import DesignStore
from app.dataset.catalog import Catalog
from app.integration.person_a_client import PersonAClient, build_person_a_client
from app.llm.provider import build_client
from app.redesign.base import Renderer
from app.redesign.diffusion import build_renderer
from app.services.design_service import DesignService


@dataclass
class Container:
    settings: Settings
    catalog: Catalog
    designs: DesignService
    renderer: Renderer
    person_a: PersonAClient


def build_container(settings: Settings) -> Container:
    catalog = Catalog.load(settings)
    outputs = Path(settings.outputs_dir)
    person_a = build_person_a_client(settings)
    designs = DesignService(catalog, DesignStore(outputs / "designs"), build_client(settings), person_a)
    renderer = build_renderer(settings)
    Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
    return Container(settings, catalog, designs, renderer, person_a)


def get_container(request: Request) -> Container:
    return request.app.state.container
