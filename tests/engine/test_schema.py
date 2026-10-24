import copy
from typing import Any

from console.engine.schema import diff, normalise

BASE: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Epistrel Engine", "version": "m3"},
    "paths": {
        "/health": {"get": {"responses": {"200": {"description": "ok"}}}},
        "/stories": {
            "get": {"responses": {"200": {"description": "list"}}},
            "post": {"responses": {"201": {"description": "created"}}},
        },
    },
    "components": {
        "schemas": {
            "HealthResponse": {"type": "object", "properties": {"status": {"type": "string"}}},
            "Error": {"type": "object"},
        },
        "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}},
    },
    "tags": [{"name": "ops"}],
}


def variant() -> dict[str, Any]:
    return copy.deepcopy(BASE)


def test_identical_documents_have_no_diff() -> None:
    result = diff(BASE, variant())
    assert not result
    assert result.render() == ""
    assert result.summary() == ""


def test_info_version_is_not_drift() -> None:
    live = variant()
    live["info"]["version"] = "m4"
    live["servers"] = [{"url": "http://x"}]
    live["externalDocs"] = {"url": "http://docs"}
    assert not diff(BASE, live)


def test_key_order_is_irrelevant() -> None:
    live = {key: BASE[key] for key in reversed(list(BASE))}
    assert not diff(BASE, live)


def test_normalise_drops_top_level_only() -> None:
    doc = {"info": {}, "servers": [], "externalDocs": {}, "paths": {"/x": {"info": 1}}}
    assert normalise(doc) == {"paths": {"/x": {"info": 1}}}


def test_added_path() -> None:
    live = variant()
    live["paths"]["/stories/{story_id}"] = {"delete": {"responses": {}}}
    result = diff(BASE, live)
    assert result.operations_added == ("DELETE /stories/{story_id}",)
    assert "Operations added:\n  DELETE /stories/{story_id}\n" in result.render()
    assert result.summary() == "1 operation added"


def test_removed_path() -> None:
    live = variant()
    del live["paths"]["/stories"]
    result = diff(BASE, live)
    assert result.operations_removed == ("GET /stories", "POST /stories")
    assert result.render().startswith("Operations removed:\n  GET /stories\n  POST /stories\n")
    assert result.summary() == "2 operations removed"


def test_changed_response() -> None:
    live = variant()
    live["paths"]["/health"]["get"]["responses"]["503"] = {"description": "down"}
    result = diff(BASE, live)
    assert result.operations_changed == ("GET /health",)
    assert "Operations changed:\n  GET /health" in result.render()


def test_path_level_key_changes_every_operation_under_it() -> None:
    live = variant()
    live["paths"]["/stories"]["parameters"] = [{"name": "x", "in": "query"}]
    assert diff(BASE, live).operations_changed == ("GET /stories", "POST /stories")


def test_changed_added_removed_components() -> None:
    live = variant()
    live["components"]["schemas"]["HealthResponse"]["required"] = ["status"]
    del live["components"]["schemas"]["Error"]
    live["components"]["schemas"]["Story"] = {"type": "object"}
    result = diff(BASE, live)
    assert result.components_changed == ("schemas/HealthResponse",)
    assert result.components_removed == ("schemas/Error",)
    assert result.components_added == ("schemas/Story",)
    rendered = result.render()
    assert rendered.index("Components added:") < rendered.index("Components removed:")
    assert rendered.index("Components removed:") < rendered.index("Components changed:")
    assert result.summary() == "1 component added, 1 component removed, 1 component changed"


def test_other_top_level_changes() -> None:
    live = variant()
    live["tags"] = [{"name": "ops"}, {"name": "stories"}]
    live["webhooks"] = {}
    result = diff(BASE, live)
    assert result.other_changed == ("tags", "webhooks")
    assert "Other changes:\n  tags\n  webhooks\n" in result.render()
    assert result.summary() == "2 other changes"


def test_summary_order_and_changes_lines() -> None:
    live = variant()
    live["paths"]["/x"] = {"post": {}}
    live["components"]["schemas"]["Error"] = {"type": "string"}
    live["tags"] = []
    result = diff(BASE, live)
    assert result.summary() == "1 operation added, 1 component changed, 1 other change"
    assert result.changes() == ("added: POST /x", "changed: schemas/Error", "changed: tags")
