"""The model guide link (#46): a release version links to its tag, anything else to main."""

import httpx
import pytest
from django.test import Client

from console.model_config import guide_link
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.models.helpers import LIST, answer_list

pytestmark = pytest.mark.django_db

REPO = "https://github.com/nathanpond/Epistrel/blob"


@pytest.mark.parametrize(
    ("version", "url", "label"),
    [
        ("1.2.3", f"{REPO}/v1.2.3/docs/models.md", "Model guide for Engine 1.2.3"),
        ("0.10.0", f"{REPO}/v0.10.0/docs/models.md", "Model guide for Engine 0.10.0"),
        ("1.2.3-rc.1", f"{REPO}/main/docs/models.md", "Model guide for Engine 1.2.3-rc.1 (main)"),
        ("1.2.3+local", f"{REPO}/main/docs/models.md", "Model guide for Engine 1.2.3+local (main)"),
        ("1.2.3\n", f"{REPO}/main/docs/models.md", "Model guide for Engine 1.2.3\n (main)"),
        ("dev", f"{REPO}/main/docs/models.md", "Model guide for Engine dev (main)"),
        ("m3", f"{REPO}/main/docs/models.md", "Model guide for Engine m3 (main)"),
        (None, f"{REPO}/main/docs/models.md", "Model guide (Engine version unknown)"),
    ],
)
def test_guide_link(version: str | None, url: str, label: str) -> None:
    link = guide_link(version)
    assert (link.url, link.label) == (url, label)


def test_the_list_links_a_release_to_its_tag(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine, version="1.2.3")
    html = operator_client.get("/models/").content.decode()
    assert f'<a href="{REPO}/v1.2.3/docs/models.md">Model guide for Engine 1.2.3</a>' in html


def test_the_list_links_a_dev_version_to_main(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine, version="m3")
    html = operator_client.get("/models/").content.decode()
    assert f'<a href="{REPO}/main/docs/models.md">Model guide for Engine m3 (main)</a>' in html


def test_no_version_in_the_document_links_to_main(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine, version=None)
    html = operator_client.get("/models/").content.decode()
    assert "Model guide (Engine version unknown)" in html


def test_a_failed_openapi_read_still_renders_the_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=engine_fixture("list_model_config"))
    engine.fail("GET", "/openapi.json", httpx.ConnectError("refused"))
    response = operator_client.get("/models/")
    html = response.content.decode()
    assert response.status_code == 200
    assert f'<a href="{REPO}/main/docs/models.md">Model guide (Engine version unknown)</a>' in html
    assert 'id="purpose-generation"' in html
