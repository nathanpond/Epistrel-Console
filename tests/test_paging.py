"""#187 C12/C13: the shared Inspect paging and URL-id helpers."""

from django.http import QueryDict

from console.paging import CHARACTER_INVALID, CastId, cast_id, paging

ANA = "01HZY00000000000000000ANA0"
OUTSIDE = "01HZY0000000000000ZZZZZZZZ"


def test_paging_with_after_drops_and_sets_after_keeping_the_rest() -> None:
    params = QueryDict("character=X&after=OLD&kind=belief&kind=fact")
    links = paging(params, "OLD", "NEW", param="after")
    assert QueryDict(links["first_query"]) == QueryDict("character=X&kind=belief&kind=fact")
    following = QueryDict(links["next_query"])
    assert following["after"] == "NEW"
    assert following["character"] == "X"
    assert following.getlist("kind") == ["belief", "fact"]
    assert links["paged"] is True
    assert params["after"] == "OLD"  # copied, never mutated


def test_paging_with_the_default_param_and_no_next_page() -> None:
    links = paging(QueryDict("cursor=C&q=1"), None, None)
    assert links == {"first_query": "q=1", "next_query": "", "paged": False}


def test_paging_leaves_another_cursor_parameter_alone() -> None:
    links = paging(QueryDict("cursor=C&after_id=A"), "A", "B", param="after_id")
    assert QueryDict(links["first_query"]) == QueryDict("cursor=C")
    assert QueryDict(links["next_query"]) == QueryDict("cursor=C&after_id=B")


def test_cast_id_blank_is_no_filter() -> None:
    assert cast_id("  ", {ANA}) == CastId("", False, "")


def test_cast_id_trims_and_upper_cases_a_cast_id() -> None:
    assert cast_id(f"  {ANA.lower()} ", {ANA}) == CastId(ANA, False, "")


def test_cast_id_keeps_a_valid_id_outside_the_cast() -> None:
    assert cast_id(OUTSIDE.lower(), {ANA}) == CastId(OUTSIDE, True, "")
    assert cast_id(ANA, ()) == CastId(ANA, True, "")  # failed cast read: every valid id kept


def test_cast_id_refuses_garbage() -> None:
    assert cast_id(" not-an-id ", {ANA}) == CastId("NOT-AN-ID", False, CHARACTER_INVALID)
    assert CHARACTER_INVALID == (
        "Enter a 26-character character id (digits and capital letters, no I, L, O or U)"
    )
