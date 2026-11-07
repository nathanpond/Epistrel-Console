"""#240: the deployment's providers in the Engine panel, against the schema-validating fake."""

import copy
import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

import httpx
from django.test import Client, override_settings
from django.utils.html import escape

from console.effects import provider_url
from console.engine.models import Provider, ProviderList
from tests.engine_panel.helpers import error
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.golden import normalise_html

LIST = "/admin/external-providers"
ONE = "/admin/external-providers/{name}"
URL = "/engine/providers/"
ROOT = Path(__file__).resolve().parents[2]


def providers_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m6/providers"))
    body.update(changes)
    return body


def answer_list(engine: FakeEngine, **changes: Any) -> dict[str, Any]:
    body = providers_body(**changes)
    engine.respond("GET", LIST, 200, json=body)
    return body


def answer_raw(engine: FakeEngine, body: dict[str, Any]) -> None:
    """A list the schema refuses (a malformed entry), served unvalidated."""
    engine.side_effect("GET", LIST, lambda request: httpx.Response(200, json=body))


def answer_one(engine: FakeEngine, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m6/provider"))
    body.update(changes)
    engine.respond("GET", ONE, 200, json=body)
    return body


def row(page: str, name: str) -> str:
    found = re.search(rf'<tr id="provider-{re.escape(name)}">(.*?)</tr>', page, re.S)
    assert found is not None, name
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return re.sub(r"\s+", " ", found.group(1)).strip()


def text(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


# --- the list -------------------------------------------------------------------------------------


def test_list_rows_in_engine_order(operator_client: Client, engine: FakeEngine) -> None:
    body = answer_list(engine)
    names = [p["name"] for p in body["providers"]]

    page = operator_client.get(URL).content.decode()

    assert re.findall(r'<tr id="provider-([^"]+)">', page) == names
    n = len(names)
    count = "1 provider" if n == 1 else f"{n} providers"
    assert f"Config revision {body['config_revision']} · {count}" in page
    bank, shop = row(page, "bank"), row(page, "shop")
    assert cell(bank, "provider-trust") == '<span title="authoritative">Authoritative</span>'
    assert cell(bank, "provider-transport") == '<span title="http">Http</span>'
    assert text(cell(bank, "provider-writes")) == "Write-capable · Synchronous"
    assert '<span title="synchronous">Synchronous</span>' in cell(bank, "provider-writes")
    assert text(cell(bank, "provider-unavailable")) == "Last known · fresh for 10 min"
    assert '<span title="last_known">Last known</span>' in cell(bank, "provider-unavailable")
    assert cell(bank, "provider-values") == "balance"
    assert '<time datetime="2026-10-30T09:30:00Z"' in cell(bank, "provider-updated")
    assert cell(shop, "provider-transport") == '<span title="in_process">In process</span>'
    assert text(cell(shop, "provider-trust")) == "Advisory"
    assert text(cell(shop, "provider-writes")) == "Read-only"
    assert text(cell(shop, "provider-unavailable")) == "Unknown"  # not last_known: no window
    assert cell(shop, "provider-values") == "stock"
    [call] = engine.requests
    assert (call.method, call.url.path) == ("GET", LIST)


def test_list_freshness_default_and_bare_write_capable(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = providers_body()
    bank = next(p for p in body["providers"] if p["name"] == "bank")
    bank.update(freshness_minutes=None, confirmation=None)
    bank["manifest"] = {"values": {}}
    engine.respond("GET", LIST, 200, json=body)

    page = operator_client.get(URL).content.decode()

    bank_row = row(page, "bank")
    assert text(cell(bank_row, "provider-unavailable")) == "Last known · fresh for 60 min (default)"
    assert text(cell(bank_row, "provider-writes")) == "Write-capable"
    assert cell(bank_row, "provider-values") == "—"


def test_unparsed_provider_shows_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = engine_fixture("m6/providers_bad_item")
    answer_raw(engine, body)
    rejected = [e for e in body["providers"] if Provider.from_json(e) is None]
    parsed = len(body["providers"]) - len(rejected)

    page = operator_client.get(URL).content.decode()

    rows = re.findall(r'<tr (id="provider-[^"]+"|class="unread")>', page)
    assert rows == ['id="provider-bank"', 'class="unread"', 'id="provider-shop"']
    assert page.count(escape("This provider couldn't be read")) == len(rejected) == 1
    unread = re.search(r'<tr class="unread">(.*?)</tr>', page, re.S)
    assert unread is not None
    more = re.search(r"<summary>More</summary><pre>(.*?)</pre>", unread.group(1), re.S)
    assert more is not None
    assert json.loads(html_lib.unescape(more.group(1))) == rejected[0]
    assert f"{parsed} providers" in page  # parsed entries only


def test_providers_not_a_list_is_one_unread_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#240's AC (#281): a ``providers`` value that isn't a list is one "couldn't be read" row
    holding the raw value, not #43's panel."""
    raw: dict[str, Any] = {"bank": {}}
    answer_raw(engine, {"config_revision": 4, "providers": raw})

    page = operator_client.get(URL).content.decode()

    assert 'class="engine-error' not in page
    assert '<table class="providers">' in page
    assert re.findall(r'<tr (id="provider-[^"]+"|class="unread")>', page) == ['class="unread"']
    assert page.count(escape("This provider couldn't be read")) == 1
    unread = re.search(r'<tr class="unread">(.*?)</tr>', page, re.S)
    assert unread is not None
    more = re.search(r"<summary>More</summary><pre>(.*?)</pre>", unread.group(1), re.S)
    assert more is not None
    assert json.loads(html_lib.unescape(more.group(1))) == raw
    assert "Config revision 4" in page
    assert "No providers are registered." not in page


def test_provider_list_model_keeps_a_non_list_raw() -> None:
    """#281: the model reads a non-list ``providers`` as none parsed, the raw kept in
    ``unparsed``; a list still parses entry by entry, and a missing ``providers`` is refused."""
    listed = ProviderList.from_json({"config_revision": 4, "providers": {"bank": {}}})
    assert listed is not None
    assert listed.providers == []
    assert listed.config_revision == 4
    assert listed.unparsed == {"providers": {"bank": {}}}
    assert ProviderList.from_json({"config_revision": 4}) is None
    assert ProviderList.from_json({"providers": "x"}) is None  # config_revision still required
    fine = ProviderList.from_json(providers_body())
    assert fine is not None
    assert fine.unparsed == {}


def test_empty_registry_line(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine, providers=[], config_revision=7)

    page = operator_client.get(URL).content.decode()

    assert "Config revision 7</p>" in page
    assert "No providers are registered." in page
    assert "0 providers" not in page
    assert '<table class="providers">' not in page


def test_list_read_failure_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 503, json=error("database_unavailable"))

    response = operator_client.get(URL)
    page = response.content.decode()

    assert 'class="engine-error' in page
    assert "database_unavailable" in page
    assert '<table class="providers">' not in page
    assert "Config revision" not in page


def test_list_erasure_pending_is_the_held_fragment(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 503, json=error("erasure_pending"))

    response = operator_client.get(URL)
    page = response.content.decode()

    assert response.status_code == 503
    assert "Held until the erasure ledger is re-applied" in page
    assert ">Retry</a>" in page
    assert "Delete</a>" not in page  # no story links on a storyless page
    assert "/stories/" not in re.sub(r"<nav.*?</nav>", "", page, flags=re.S)


def test_provider_names_link_once_registered(operator_client: Client, engine: FakeEngine) -> None:
    assert provider_url("bank") == "/engine/providers/bank/"
    answer_list(engine)

    page = operator_client.get(URL).content.decode()

    assert cell(row(page, "bank"), "provider-name") == (
        '<a href="/engine/providers/bank/"><code>bank</code></a>'
    )


# --- one provider ---------------------------------------------------------------------------------


def _registration(page: str) -> list[str]:
    table = re.search(r'<table class="registration">(.*?)</table>', page, re.S)
    assert table is not None
    return [text(r) for r in re.findall(r"<tr>(.*?)</tr>", table.group(1), re.S)]


def test_detail_shows_every_registration_field(operator_client: Client, engine: FakeEngine) -> None:
    answer_one(engine)

    response = operator_client.get("/engine/providers/bank/")
    page = response.content.decode()

    assert response.status_code == 200
    assert "<title>Provider bank — Epistrel Console</title>" in page
    assert _registration(page) == [
        "Name bank",
        "Trust tier Authoritative",
        "Transport Http",
        "Write-capable yes",
        "Confirmation Synchronous",
        "Dedupes dispatch yes",
        "Returns resulting values yes",
        "When unavailable Last known",
        "Freshness window 10 min",
        "Confirm wait (max) 10 s",
        "Created 2026-10-30 08:00:00 UTC",
        "Updated 2026-10-30 09:30:00 UTC",
        "Last changed at config revision 3",
        "Read at config revision 4",
    ]
    assert '<span title="authoritative">Authoritative</span>' in page
    assert '<span title="last_known">Last known</span>' in page
    values = re.search(r'<table class="manifest-values">(.*?)</table>', page, re.S)
    assert values is not None
    assert "<td><code>balance</code></td><td>number</td><td>read, write</td>" in values.group(1)
    [call] = engine.requests
    assert call.url.path == "/admin/external-providers/bank"
    assert 'href="/engine/providers/">All providers</a>' in page
    assert 'href="/engine/config-changes/">Config changes</a>' in page


def test_detail_nulls_and_defaults(operator_client: Client, engine: FakeEngine) -> None:
    shop = next(p for p in engine_fixture("m6/providers")["providers"] if p["name"] == "shop")
    engine.respond("GET", ONE, 200, json={**shop, "config_revision": 4})

    page = operator_client.get("/engine/providers/shop/").content.decode()

    rows = _registration(page)
    assert "Write-capable no" in rows
    assert "Confirmation —" in rows
    assert "Dedupes dispatch —" in rows
    assert "Returns resulting values —" in rows
    assert "Freshness window 60 min (default)" in rows
    assert "<td><code>stock</code></td><td>number</td><td>read-only</td>" in page
    assert "None — a story binds no ids to this provider" in page
    secrets = re.search(r'<section class="secrets">(.*?)</section>', page, re.S)
    assert secrets is not None
    assert "<p>None</p>" in secrets.group(1)
    assert "secret values never leave" not in secrets.group(1)


def test_detail_summarises_bindings_and_secrets(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_one(engine)

    page = operator_client.get("/engine/providers/bank/").content.decode()

    assert '<p class="binding-keys"><code>account_id</code></p>' in page
    assert "Each story using this provider binds these on its Providers page" in page
    assert (
        "<li><code>{secret.BANK_TOKEN}</code> → environment variable "
        "<code>EPISTREL_PROVIDER_SECRET_BANK_TOKEN</code> on the Engine</li>"
    ) in page
    assert page.count("<li><code>{secret.") == 1  # used twice, listed once
    assert (
        "The Engine stores only these references; secret values never leave its environment."
    ) in page


def test_detail_renders_manifest_verbatim(operator_client: Client, engine: FakeEngine) -> None:
    body = answer_one(engine)

    page = operator_client.get("/engine/providers/bank/").content.decode()

    block = re.search(r'<pre class="manifest">(.*?)</pre>', page, re.S)
    assert block is not None
    expected = json.dumps(body["manifest"], indent=2, ensure_ascii=False)
    assert block.group(1) == escape(expected)
    # **Complement:** the references are there, and nothing but the fixture's own text is.
    assert "{secret.BANK_TOKEN}" in block.group(1)
    assert json.loads(html_lib.unescape(block.group(1))) == body["manifest"]


def test_invalid_name_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get("/engine/providers/Bank/")
    page = response.content.decode()

    assert response.status_code == 404
    assert "No provider named “Bank” is registered" in page
    assert engine.requests == []
    assert '<table class="registration">' not in page


def test_detail_404_line(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE, 404, json=error("not_found", provider="vault"))

    response = operator_client.get("/engine/providers/vault/")
    page = response.content.decode()

    assert response.status_code == 404
    assert "No provider named “vault” is registered" in page
    assert '<table class="registration">' not in page
    assert 'class="engine-error' not in page
    assert len(engine.requests) == 1


def test_detail_other_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE, 503, json=error("database_unavailable"))

    page = operator_client.get("/engine/providers/bank/").content.decode()

    assert 'class="engine-error' in page
    assert "database_unavailable" in page
    assert '<table class="registration">' not in page
    assert "No provider named" not in page


def test_detail_bad_response_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.side_effect("GET", ONE, lambda request: httpx.Response(200, json={"name": "bank"}))

    page = operator_client.get("/engine/providers/bank/").content.decode()

    assert 'class="engine-error' in page
    assert '<table class="registration">' not in page


# --- navigation and secrets -----------------------------------------------------------------------


def test_engine_home_links_providers(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))

    page = operator_client.get("/engine/").content.decode()

    assert re.search(r'>Config changes</a> · <a href="/engine/providers/">Providers</a> · ', page)


# Main's Engine home link line (E24: captured before #296, compared normalised).
ENGINE_HOME_LINK_LINE = (
    '<p><a href="/engine/settings/">Deployment settings</a> · '
    '<a href="/engine/config-changes/">Config changes</a> · '
    '<a href="/engine/providers/">Providers</a> · '
    '<a href="/models/embedding/reembed/">Re-embed run</a></p>'
)
ENGINE_NAV = (
    ' · <span class="engine-pages" role="navigation" aria-label="Engine pages">'
    '<a href="/engine/import/">Import a bundle</a> · '
    '<a href="/engine/certifications/">Certifications</a> · '
    '<a href="/engine/evals/">Eval runs</a></span>'
)
# The registered M7 Engine rows (#313: Certifications, #316: Eval runs); #320 adds its own.
REGISTERED_ENGINE_NAV = (
    ' · <span class="engine-pages" role="navigation" aria-label="Engine pages">'
    '<a href="/engine/certifications/">Certifications</a> · '
    '<a href="/engine/evals/">Eval runs</a></span>'
)


def home_link_line(html: str) -> str:
    start = html.index('<p><a href="/engine/settings/">')
    return html[start : html.index("</p>", start) + 4]


def test_engine_home_links(operator_client: Client, engine: FakeEngine) -> None:
    """E11 (#296): the link line is main's plus the registered M7 Engine rows after Providers
    (#313 regenerated it: Certifications)."""
    engine.respond("GET", "/stories", 200, json=engine_fixture("list_stories"))
    html = operator_client.get("/engine/").content.decode()
    providers = '<a href="/engine/providers/">Providers</a>'
    registered = ENGINE_HOME_LINK_LINE.replace(providers, providers + REGISTERED_ENGINE_NAV)
    assert normalise_html(home_link_line(html)) == normalise_html(registered)
    # Complement: under the stub urlconf the Engine nav follows Providers.
    with override_settings(ROOT_URLCONF="tests.urls_with_m7_pages"):
        stubbed = operator_client.get("/engine/").content.decode()
    providers = '<a href="/engine/providers/">Providers</a>'
    expected = ENGINE_HOME_LINK_LINE.replace(providers, providers + ENGINE_NAV)
    assert normalise_html(home_link_line(stubbed)) == normalise_html(expected)


def test_engine_nav_active_on_provider_pages(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine)
    answer_one(engine)

    for url in (URL, "/engine/providers/bank/", "/engine/providers/Bank/"):
        page = operator_client.get(url).content.decode()
        assert '<a href="/engine/" class="active">Engine</a>' in page, url


def test_console_reads_no_provider_secret() -> None:
    """**Complement:** the only ``EPISTREL_PROVIDER_SECRET_`` in the Console's source is
    ``secret_env_var``'s string, and nothing reads the environment with it."""
    hits: list[str] = []
    for path in [*(ROOT / "console").rglob("*.py"), *(ROOT / "templates").rglob("*.html")]:
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            if "EPISTREL_PROVIDER_SECRET_" in line:
                hits.append(f"{path.relative_to(ROOT)}: {line.strip()}")
            if "PROVIDER_SECRET" in line and re.search(r"os\.environ|getenv", line):
                hits.append(f"ENV {path.relative_to(ROOT)}: {line.strip()}")
    assert hits == ['console/providers.py: return "EPISTREL_PROVIDER_SECRET_" + name']
