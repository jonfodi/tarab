"""Matching heuristics. Each case is a real miss from building the prototype."""
import pytest

from tarab_core.text import Query, compact_in, other_version, parse_list, tokens


def test_parse_basic():
    q = Query.parse("Mihai Popoviciu - Waitin'")
    assert (q.artist, q.title, q.length, q.catalog) == ("Mihai Popoviciu", "Waitin'", None, None)


def test_parse_title_first_header_and_trailing_dash():
    qs = parse_list("TITLE - ARTIST\n\nElvism - burger/ink\nBCN Dub - Deepchord presents Echospace - \n# note\n")
    assert [(q.artist, q.title) for q in qs] == [("burger/ink", "Elvism"), ("Deepchord presents Echospace", "BCN Dub")]


def test_parse_catalog_and_pinned_length():
    q = Query.parse("Roger Gerressen - Untitled 1 [SUSH31] | 6:05")
    assert (q.title, q.catalog, q.length) == ("Untitled 1", "SUSH31", 365)


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        Query.parse("just a title")


@pytest.mark.parametrize("line, expected", [
    # Soulseek indexes "D'Arcangelo" as "d" + "arcangelo"; squashing it to "darcangelo" found 1 peer instead of 30
    ("D'Arcangelo - Phlexio 14", "arcangelo phlexio 14"),
    # stopwords matter in titles: "On & On" must not shrink to "on"
    ("Aril Brikha - On & On", "aril brikha on and"),
    # "presents" isn't part of the artist; "(Original Mix)" is noise
    ("Deepchord presents Echospace - BCN Dub (Original Mix)", "deepchord echospace bcn dub"),
    ("Mihai Popoviciu - Waitin'", "mihai popoviciu waitin"),
])
def test_search_text(line, expected):
    assert Query.parse(line).search_text == expected


def test_roman_numerals_match_digits():
    assert tokens("Sensitive Part II") == tokens("sensitive part 2")


def test_compact_in():
    assert compact_in("Page-R", "a1 repair - pager")           # "Page-R" filed as "Pager"
    assert compact_in("Untitled 1", "01 - Untitled 1.flac")
    assert not compact_in("Untitled 1", "B1 Untitled 10")      # not the same track
    assert not compact_in("Waitin'", "Mihai Popoviciu - Waiting For You")
    assert compact_in("Waitin'", "Mihai Popoviciu - Waitin' (Original Mix)")


def test_other_version():
    asked = Query.parse("Havantepe - Rain On Window").all_tokens
    assert other_version("02 - Havantepe - Rain On Window (Stojche's Overdub)", asked)
    assert not other_version("Rain On Window (Original Mix)", asked)
    assert not other_version("Rain On Window [Vertex Recordings]", asked)
    assert not other_version("08 Love Is the Drug [Paris Texas]", asked)
    roots = Query.parse("Selan - Gravity (Roots Dub)").all_tokens
    assert not other_version("Selan - Gravity (Roots Dub)", roots)
    assert other_version("Selan - Gravity (Club Mix)", roots)


def test_mix_query_accepts_remix_files():
    assert "remix" in Query.parse("Jovonn - Pianos of Gold (Ian Pooley Mix)").all_tokens
    assert "remix" not in Query.parse("Selan - Stocha (Original Mix)").all_tokens


def test_same_title_is_version_aware():
    original = Query.parse("Havantepe - Passing by")
    assert original.same_title("03 Passing By")
    assert not original.same_title("B2 Havantepe - Passing by (Roger Gerressen Remix)")
    remix = Query.parse("Havantepe - Passing By (Roger Gerressen Remix)")
    assert remix.same_title("B2 Havantepe - Passing by (Roger Gerressen Remix ) [Berg Audio]")


def test_key_ignores_generic_version_and_case():
    assert Query.parse("Selan - Stocha (Original Mix)").key == Query.parse("selan - stocha").key
    assert Query.parse("Selan - Gravity (Roots Dub)").key != Query.parse("Selan - Gravity").key
