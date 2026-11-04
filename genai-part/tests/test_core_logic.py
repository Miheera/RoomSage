import itertools

import httpx
import httpx2
import pytest

from app.budget.optimizer import PRIORITY_WEIGHT, calculate_selection, optimize_budget
from app.config import Settings
from app.core.models import Category, RoomType, Theme
from app.dataset.catalog import Catalog
from app.llm import designer, rules
from app.llm.provider import AnthropicClient, LLMUnavailable, OpenAICompatibleClient
from app.llm.schemas import (
    ChangeAction, DesignRequest, LLMDesignDraft, Priority, ProductRequirement, RefinementPatch,
)
from app.recommendations.engine import RankedProduct, SlotRecommendation, recommend, recommend_slot
from app.scene.normalizer import build_scene, category_for_label
from app.services.design_models import Design
from app.services.planner import build_plan
from app.services.refinement import apply_patch
from tests.conftest import make_product


def slot(name, category, priority, *products):
    return SlotRecommendation(
        slot=name, category=category, priority=priority,
        options=[RankedProduct(product=p, score=50 - i, reasons=[]) for i, p in enumerate(products)],
    )


# Scene

def test_scene_tolerates_partial_and_malformed_input():
    scene = build_scene({"room_type": "Living Room", "dimensions": {"length_m": -3},
                         "objects": ["old sofa", {"nolabel": 1}, {"label": "lamp", "color": "gold"}]})
    assert scene.room_type is RoomType.LIVING_ROOM
    assert scene.dimensions is None
    assert [o.category for o in scene.objects] == [Category.SOFA_SEATING, Category.LIGHTING]
    assert "dimensions" in scene.missing_fields


def test_empty_scene_reports_everything_missing():
    scene = build_scene(None)
    assert set(scene.missing_fields) == {"room_type", "dimensions", "objects", "colors", "materials"}


# Spec

def test_spec_records_unknowns_as_assumptions(real_catalog):
    request = DesignRequest(theme=Theme.JAPANDI, budget_inr=50000)
    spec = rules.build_spec(request, real_catalog.theme(Theme.JAPANDI))
    assert any("Room size is unknown" in a for a in spec.assumptions)
    assert spec.generated_by == "rules"


def test_retained_objects_get_no_slot_and_replace_is_marked(real_catalog):
    scene = build_scene({"room_type": "bedroom", "objects": ["bed", "old wardrobe"]})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000, scene=scene, retain=["bed"])
    spec = rules.build_spec(request, real_catalog.theme(Theme.MODERN))
    assert Category.BED not in {r.category for r in spec.product_requirements}
    actions = {c.label: c.action for c in spec.furniture_changes}
    assert actions["bed"] is ChangeAction.KEEP
    assert actions["old wardrobe"] is ChangeAction.REPLACE


def test_width_limit_comes_from_the_real_room(real_catalog):
    scene = build_scene({"room_type": "living_room", "dimensions": {"length_m": 4, "width_m": 3}})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000, scene=scene, small_room=True)
    spec = rules.build_spec(request, real_catalog.theme(Theme.MODERN))
    sofa = next(r for r in spec.product_requirements if r.category is Category.SOFA_SEATING)
    assert sofa.max_width_cm == 180


def test_llm_draft_only_changes_creative_fields(real_catalog):
    class FakeClient:
        def generate(self, schema, system, user):
            return LLMDesignDraft(color_palette=["sand"], accent_colors=["ink"], materials=["oak"],
                                  layout_constraints=["Float the sofa."], redesign_prompt="p",
                                  edit_prompt_3d="e", rationale="r")

    request = DesignRequest(theme=Theme.JAPANDI, budget_inr=50000)
    spec, warning = designer.create_spec(request, real_catalog.theme(Theme.JAPANDI), FakeClient())
    assert spec.generated_by == "llm" and warning is None
    assert spec.redesign_prompt == "p" and spec.color_palette == ["sand"]
    base = rules.build_spec(request, real_catalog.theme(Theme.JAPANDI))
    assert [r.slot for r in spec.product_requirements] == [r.slot for r in base.product_requirements]


def test_off_palette_colours_are_negated_but_theme_colours_are_not():
    negative = rules.off_palette_negative("blurry", ["beige"], ["pastel blue", "green"])
    assert "red" in negative and "pink" in negative
    assert "blue" not in negative and "green" not in negative


def test_chosen_items_lead_the_preview_prompt():
    prompt = rules.compose_redesign_prompt("japandi interior", RoomType.LIVING_ROOM, ["beige"], ["beige sofa"])
    assert prompt.index("beige sofa") < prompt.index("japandi interior")


def test_llm_failure_falls_back_to_rules(real_catalog):
    class Broken:
        def generate(self, *args):
            raise LLMUnavailable("down")

    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000)
    spec, warning = designer.create_spec(request, real_catalog.theme(Theme.MODERN), Broken())
    assert spec.generated_by == "rules" and "unavailable" in warning


def test_llm_draft_schema_cannot_carry_prices():
    with pytest.raises(ValueError):
        LLMDesignDraft.model_validate({
            "color_palette": [], "accent_colors": [], "materials": [], "layout_constraints": [],
            "redesign_prompt": "p", "edit_prompt_3d": "e", "rationale": "r", "price_inr": 100,
        })


DRAFT_JSON = ('{"color_palette":["sand"],"accent_colors":["ink"],"materials":["oak"],'
              '"layout_constraints":[],"redesign_prompt":"p","edit_prompt_3d":"e","rationale":"r"}')


def test_anthropic_client_sends_structured_request():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = request.read().decode()
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": DRAFT_JSON}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    settings = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="k",
                        llm_base_url="https://llm.test")
    client = AnthropicClient(settings, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    draft = client.generate(LLMDesignDraft, "sys", "user")
    assert draft.redesign_prompt == "p"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert '"fallbacks":"default"' in seen["body"].replace(" ", "")


def test_refinement_patch_schema_is_accepted_by_the_sdk():
    def handler(request):
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": '{"theme":"japandi","budget_limit_inr":20000,"small_room":null,'
                         '"keep_categories":[],"remove_categories":[],"reduce_decor":false,'
                         '"color_requests":[{"category":"sofa_seating","colors":["navy"]}],"understood":true}'}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    settings = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="k",
                        llm_base_url="https://llm.test")
    client = AnthropicClient(settings, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    patch = client.generate(RefinementPatch, "sys", "user")
    assert patch.theme is Theme.JAPANDI and patch.color_requests[0].colors == ["navy"]


def test_anthropic_refusal_is_unavailable():
    def handler(request):
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": "refusal", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        })

    settings = Settings(_env_file=None, llm_provider="anthropic", llm_api_key="k",
                        llm_base_url="https://llm.test")
    client = AnthropicClient(settings, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(LLMUnavailable):
        client.generate(LLMDesignDraft, "sys", "user")


def test_openai_compatible_client_retries_invalid_output():
    replies = iter(['{"bad": true}', DRAFT_JSON])

    def handler(request):
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": next(replies)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    settings = Settings(_env_file=None, llm_provider="openai_compatible", llm_model="m",
                        llm_base_url="https://llm.test/v1")
    client = OpenAICompatibleClient(settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.generate(LLMDesignDraft, "sys", "user").edit_prompt_3d == "e"


# Recommendations

def test_recommendations_are_deterministic_and_explained(real_catalog):
    request = DesignRequest(theme=Theme.MODERN, budget_inr=80000)
    spec = rules.build_spec(request, real_catalog.theme(Theme.MODERN))
    first = recommend(spec, real_catalog)
    second = recommend(spec, real_catalog)
    assert [o.product.product_id for r in first for o in r.options] == \
           [o.product.product_id for r in second for o in r.options]
    sofa = next(r for r in first if r.category is Category.SOFA_SEATING)
    assert sofa.options and sofa.options[0].reasons
    assert all(Theme.MODERN in o.product.compatible_themes for o in sofa.options)


def test_priced_products_lead_unpriced_ones():
    products = [make_product("a", Category.BED, None, color="white"), make_product("b", Category.BED, 9000)]
    catalog = Catalog(products, {})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000, room_type=RoomType.BEDROOM)
    spec = rules.build_spec(request, {"palette": ["white"], "accent_colors": [], "materials": [],
                                      "prompt": "p", "label": "Modern", "description": "d"})
    requirement = next(r for r in spec.product_requirements if r.category is Category.BED)
    result = recommend_slot(requirement, spec, catalog)
    assert [o.product.product_id for o in result.options] == ["b", "a"]


def test_theme_filter_relaxes_visibly():
    catalog = Catalog([make_product("a", Category.RUG, 500, compatible_themes=[Theme.BOHEMIAN])], {})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000)
    spec = rules.build_spec(request, {"palette": [], "accent_colors": [], "materials": [],
                                      "prompt": "p", "label": "Modern", "description": "d"})
    requirement = ProductRequirement(slot="rug", category=Category.RUG, priority=Priority.RECOMMENDED)
    result = recommend_slot(requirement, spec, catalog)
    assert result.relaxed_filters == ["theme"]
    assert result.options[0].warnings


def test_oversized_product_is_filtered_but_unknown_size_is_flagged():
    wide = make_product("wide", Category.SOFA_SEATING, 900, dimensions={"width_cm": 250})
    unknown = make_product("unknown", Category.SOFA_SEATING, 900)
    catalog = Catalog([wide, unknown], {})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000)
    spec = rules.build_spec(request, {"palette": [], "accent_colors": [], "materials": [],
                                      "prompt": "p", "label": "Modern", "description": "d"})
    requirement = ProductRequirement(slot="s", category=Category.SOFA_SEATING,
                                     priority=Priority.ESSENTIAL, max_width_cm=180)
    result = recommend_slot(requirement, spec, catalog)
    assert [o.product.product_id for o in result.options] == ["unknown"]
    assert any("Width unknown" in w for w in result.options[0].warnings)


# Budget

def test_budget_prefers_essentials_and_stays_under_limit():
    sofa = make_product("sofa", Category.SOFA_SEATING, 8000)
    rug = make_product("rug", Category.RUG, 3000)
    decor = make_product("decor", Category.ACCESSORY, 2500)
    slots = [slot("decor", Category.ACCESSORY, Priority.OPTIONAL, decor),
             slot("rug", Category.RUG, Priority.RECOMMENDED, rug),
             slot("sofa", Category.SOFA_SEATING, Priority.ESSENTIAL, sofa)]
    result = optimize_budget(slots, 11000)
    assert {i.slot for i in result.items} == {"sofa", "rug"}
    assert result.total_inr == 11000 and result.within_budget
    assert result.is_partial and result.skipped_slots[0].slot == "decor"


def test_budget_picks_cheaper_option_to_fit_more_slots():
    expensive = make_product("big", Category.SOFA_SEATING, 9000)
    cheap = make_product("small", Category.SOFA_SEATING, 5000)
    lamp = make_product("lamp", Category.LIGHTING, 4000)
    slots = [slot("sofa", Category.SOFA_SEATING, Priority.ESSENTIAL, expensive, cheap),
             slot("lamp", Category.LIGHTING, Priority.ESSENTIAL, lamp)]
    result = optimize_budget(slots, 9500)
    assert {i.product_id for i in result.items} == {"small", "lamp"}


def test_budget_never_counts_unknown_price_as_zero():
    unpriced = make_product("mystery", Category.BED, None)
    sofa = make_product("sofa", Category.SOFA_SEATING, 8000)
    slots = [slot("bed", Category.BED, Priority.ESSENTIAL, unpriced),
             slot("sofa", Category.SOFA_SEATING, Priority.ESSENTIAL, sofa)]
    result = optimize_budget(slots, 20000)
    assert result.total_inr == 8000
    assert result.missing_essentials == ["bed"]
    assert result.unknown_price_items[0].product_id == "mystery"


def test_impossible_budget_returns_partial_result():
    sofa = make_product("sofa", Category.SOFA_SEATING, 8000)
    result = optimize_budget([slot("sofa", Category.SOFA_SEATING, Priority.ESSENTIAL, sofa)], 100)
    assert result.items == [] and result.is_partial
    assert result.skipped_slots[0].cheapest_known_price_inr == 8000


def test_lower_cost_option_is_offered():
    a = make_product("a", Category.RUG, 3000)
    b = make_product("b", Category.RUG, 1000)
    result = optimize_budget([slot("rug", Category.RUG, Priority.RECOMMENDED, a, b)], 5000)
    assert result.items[0].product_id == "a"
    assert result.lower_cost_options[0].saving_inr == 2000


def test_recommendations_include_a_cheap_way_out():
    products = [make_product(f"p{i}", Category.RUG, 4000 + i, color="white") for i in range(5)]
    products.append(make_product("cheap", Category.RUG, 300))
    catalog = Catalog(products, {})
    request = DesignRequest(theme=Theme.MODERN, budget_inr=50000)
    spec = rules.build_spec(request, {"palette": ["white"], "accent_colors": [], "materials": [],
                                      "prompt": "p", "label": "Modern", "description": "d"})
    requirement = ProductRequirement(slot="rug", category=Category.RUG, priority=Priority.RECOMMENDED,
                                     preferred_colors=["white"])
    options = recommend_slot(requirement, spec, catalog).options
    assert options[0].product.color == "white"
    cheap = next(o for o in options if o.product.product_id == "cheap")
    assert "Lower-cost alternative." in cheap.reasons


@pytest.mark.parametrize("budget", [60000, 20000, 8000, 2500])
def test_solver_matches_exhaustive_search(real_catalog, budget):
    request = DesignRequest(theme=Theme.JAPANDI, budget_inr=60000, room_type=RoomType.LIVING_ROOM)
    spec = rules.build_spec(request, real_catalog.theme(Theme.JAPANDI))
    slots = recommend(spec, real_catalog)

    def value(slot, option):
        return PRIORITY_WEIGHT[slot.priority] * (1 + option.score / 100)

    choices = [[None, *(o for o in s.options if o.product.price_inr)] for s in slots]
    best = max(
        (sum(value(s, o) for s, o in zip(slots, combo) if o)
         for combo in itertools.product(*choices)
         if sum(o.product.price_inr for o in combo if o) <= budget),
    )
    result = optimize_budget(slots, budget)
    options = {o.product.product_id: (s, o) for s in slots for o in s.options}
    assert sum(value(*options[i.product_id]) for i in result.items) == pytest.approx(best)


def test_unbinding_budget_does_not_change_the_selection(real_catalog):
    request = DesignRequest(theme=Theme.MODERN, budget_inr=500000, room_type=RoomType.LIVING_ROOM)
    spec = rules.build_spec(request, real_catalog.theme(Theme.MODERN))
    slots = recommend(spec, real_catalog)
    first = optimize_budget(slots, 500000)
    second = optimize_budget(slots, first.total_inr + 1)
    assert {i.product_id for i in first.items} == {i.product_id for i in second.items}


def test_stale_prices_are_disclosed():
    a = make_product("a", Category.RUG, 3000)
    result = optimize_budget([slot("rug", Category.RUG, Priority.RECOMMENDED, a)], 5000)
    assert "not current" in result.price_notes[0]


def test_calculate_selection_flags_unknown_prices():
    catalog = Catalog([make_product("a", Category.RUG, 3000), make_product("b", Category.BED, None)], {})
    result = calculate_selection(["a", "b"], 2000, catalog)
    assert result.total_inr == 3000 and not result.within_budget
    assert [u.product_id for u in result.unknown_price_items] == ["b"]


# Plan

def _design(catalog, theme=Theme.MODERN, budget=60000, **kwargs):
    request = DesignRequest(theme=theme, budget_inr=budget, **kwargs)
    spec = rules.build_spec(request, catalog.theme(theme))
    design = Design(request=request, spec=spec)
    design.recommendations = recommend(spec, catalog)
    design.budget = optimize_budget(design.recommendations, budget)
    design.plan = build_plan(spec, design.budget)
    return design


def test_plan_matches_budget_exactly(real_catalog):
    design = _design(real_catalog, retain=["curtain"])
    stepped = [i.product_id for s in design.plan.steps for i in s.items]
    assert sorted(stepped) == sorted(i.product_id for i in design.budget.items)
    costs = sum(s.estimated_cost_inr or 0 for s in design.plan.steps)
    assert costs == pytest.approx(design.budget.total_inr)
    assert "curtain" in design.plan.keep


def test_plan_does_not_price_paint(real_catalog):
    step = next(s for s in _design(real_catalog).plan.steps if "walls" in s.title)
    assert step.estimated_cost_inr is None and "not included" in step.cost_note


# Refinement

@pytest.mark.parametrize(
    "message,check",
    [
        ("Make the room more minimalist.", lambda p: p.theme is Theme.MINIMALIST and p.reduce_decor),
        ("Use a darker sofa.", lambda p: p.color_requests[0].category is Category.SOFA_SEATING and "charcoal" in p.color_requests[0].colors),
        ("Keep the existing bed.", lambda p: p.keep_categories == [Category.BED]),
        ("Make it suitable for a small room.", lambda p: p.small_room is True),
        ("Keep everything under ₹20,000.", lambda p: p.budget_limit_inr == 20000 and not p.keep_categories),
        ("budget of 50k please", lambda p: p.budget_limit_inr == 50000),
        ("remove the rug", lambda p: p.remove_categories == [Category.RUG]),
        ("sing me a song", lambda p: not p.understood),
    ],
)
def test_refinement_parsing(message, check):
    assert check(rules.parse_refinement(message))


@pytest.mark.parametrize(
    "message,slot",
    [
        ("Make the sofa lighter", Category.SOFA_SEATING),
        ("Make the sofa light", Category.SOFA_SEATING),
        ("Use a light rug", Category.RUG),
        ("Use a lighter curtain", Category.CURTAIN),
    ],
)
def test_light_as_a_colour_leaves_lighting_alone(message, slot):
    patch = rules.parse_refinement(message)
    assert [r.category for r in patch.color_requests] == [slot]
    assert "white" in patch.color_requests[0].colors


@pytest.mark.parametrize("message", ["Remove the light", "no lights", "drop the lamps"])
def test_light_as_an_object_is_still_lighting(message):
    assert rules.parse_refinement(message).remove_categories == [Category.LIGHTING]


def test_keeping_the_light_and_rug_keeps_both():
    patch = rules.parse_refinement("keep the light and the rug")
    assert patch.keep_categories == [Category.LIGHTING, Category.RUG]


def test_black_light_recolours_lighting():
    patch = rules.parse_refinement("use a black light")
    assert patch.color_requests[0].category is Category.LIGHTING


@pytest.mark.parametrize("message", ["Make the bedroom darker", "Keep the bedroom calm", "a tablet stand"])
def test_room_and_lookalike_words_are_not_objects(message):
    patch = rules.parse_refinement(message)
    assert not patch.understood and not patch.color_requests and not patch.keep_categories


def test_compound_noun_is_one_object():
    assert rules.parse_refinement("remove the table lamp").remove_categories == [Category.LIGHTING]


def test_named_colour_beats_a_vague_one():
    patch = rules.parse_refinement("use a dark blue sofa")
    assert patch.color_requests[0].colors == ["blue"]


@pytest.mark.parametrize(
    "label,category",
    [("floor lamp", Category.LIGHTING), ("old wardrobe", Category.STORAGE), ("bookshelf", Category.STORAGE),
     ("Couches", Category.SOFA_SEATING), ("bedroom", None), ("lighter", None)],
)
def test_scene_labels_match_whole_words(label, category):
    assert category_for_label(label) is category


def test_budget_only_refinement_skips_recommendations(real_catalog):
    design = _design(real_catalog, budget=80000)
    before = design.recommendations
    outcome = apply_patch(design, rules.parse_refinement("Keep everything under ₹20,000."), real_catalog)
    assert outcome.stages_rerun == ["budget", "plan"]
    assert "recommendations" in outcome.stages_skipped
    assert design.recommendations is before
    assert design.budget.total_inr <= 20000


def test_color_refinement_recomputes_only_that_slot(real_catalog):
    design = _design(real_catalog)
    rug_before = next(r for r in design.recommendations if r.slot == "rug")
    outcome = apply_patch(design, rules.parse_refinement("Use a darker sofa."), real_catalog)
    assert outcome.slots_recomputed == ["sofa_seating"]
    assert next(r for r in design.recommendations if r.slot == "rug") is rug_before
    sofa = next(r for r in design.recommendations if r.slot == "sofa_seating")
    assert sofa.options[0].product.color in ("charcoal", "dark grey", "black", "brown", "navy")


def test_keep_refinement_removes_slot_without_rerunning_others(real_catalog):
    design = _design(real_catalog)
    outcome = apply_patch(design, rules.parse_refinement("keep the rug"), real_catalog)
    assert "recommendations" not in outcome.stages_rerun
    assert "rug" not in {i.slot for i in design.budget.items}
    assert "rug" in design.plan.keep


def test_theme_refinement_reruns_everything_downstream(real_catalog):
    design = _design(real_catalog)
    outcome = apply_patch(design, rules.parse_refinement("make it bohemian"), real_catalog)
    assert outcome.stages_rerun == ["spec", "recommendations", "budget", "plan"]
    assert design.spec.theme is Theme.BOHEMIAN
    assert "bohemian" in design.spec.redesign_prompt
    assert outcome.preview_stale


def test_unclear_refinement_changes_nothing(real_catalog):
    design = _design(real_catalog)
    snapshot = design.model_dump_json()
    outcome = apply_patch(design, rules.parse_refinement("sing me a song"), real_catalog)
    assert not outcome.understood and outcome.stages_rerun == []
    assert design.model_dump_json() == snapshot


def test_repeat_budget_refinement_is_a_no_op(real_catalog):
    design = _design(real_catalog, budget=30000)
    outcome = apply_patch(design, RefinementPatch(budget_limit_inr=30000), real_catalog)
    assert outcome.stages_rerun == []
