"""Candidate filtering and ranking on mock Soulseek responses."""
from rasa_core.audio import Tier
from rasa_core.ranking import candidates
from rasa_core.text import Query


def f(name, size=50_000_000, length=405, br=None, bd=16, sr=44100, vbr=False):
    return {"filename": name, "size": size, "length": length, "bitRate": br, "bitDepth": bd, "sampleRate": sr,
            "isVariableBitRate": vbr}


def peer(user, *files, free=True, speed=2_000_000, queue=0):
    return {"username": user, "hasFreeUploadSlot": free, "uploadSpeed": speed, "queueLength": queue, "files": list(files)}


def names(cs):
    return [c.name for c in cs]


MIHAI = [
    peer("fast_flac", f(r"@@x\Mihai Popoviciu\01 Mihai Popoviciu - Waitin' (Original Mix).flac"), speed=5_000_000),
    peer("queued_24", f(r"@@y\Mihai_Popoviciu-Waitin-WEB\01-mihai_popoviciu-waitin.flac", 90_000_000, bd=24),
         free=False, queue=40, speed=9_000_000),
    peer("mp3guy",
         f(r"@@z\Mihai Popoviciu - Waitin' (Original Mix).mp3", 16_200_000, br=320),
         f(r"@@z\Mihai Popoviciu - Waitin' (Someone Remix).mp3", 16_200_000, 400, br=320),
         f(r"@@z\Mihai Popoviciu - Waitin' (Radio Edit).mp3", 8_000_000, 200, br=320),
         f(r"@@z\Mihai Popoviciu - Waitin'.mp3", 6_000_000, br=128),
         f(r"@@z\Mihai Popoviciu - Waitin' preview.mp3", 1_000_000, 30, br=320),
         f(r"@@z\Mihai Popoviciu - Waiting For You.mp3", 16_000_000, 300, br=320)),
    peer("fakeflac", f(r"@@w\Mihai Popoviciu - Waitin.flac", 15_000_000)),   # 296 kbps "flac"
    peer("wrongartist", f(r"@@v\Other Guy - Waitin.flac")),
    peer("nas", f(r"@@n\@eaDir\Mihai Popoviciu - Waitin'.flac@SynoEAStream", 0, None)),
]


def test_filters_and_ranks():
    cs = candidates(Query.parse("Mihai Popoviciu - Waitin'"), MIHAI)
    good = [c for c in cs if c.tier > Tier.TOO_LOW]
    assert names(good) == ["01 Mihai Popoviciu - Waitin' (Original Mix).flac",   # free slot beats 24-bit in a queue
                           "01-mihai_popoviciu-waitin.flac",
                           "Mihai Popoviciu - Waitin' (Original Mix).mp3"]
    # 128k and the 296kbps "flac" are kept only as below-the-bar candidates
    assert {c.name for c in cs if c.tier == Tier.TOO_LOW} == {"Mihai Popoviciu - Waitin'.mp3", "Mihai Popoviciu - Waitin.flac"}


def test_asking_for_the_remix_gets_the_remix():
    cs = candidates(Query.parse("Mihai Popoviciu - Waitin' (Someone Remix)"), MIHAI)
    assert names(cs) == ["Mihai Popoviciu - Waitin' (Someone Remix).mp3"]


def test_flaky_peer_goes_last_within_its_tier_only():
    cs = candidates(Query.parse("Mihai Popoviciu - Waitin'"), MIHAI, flaky={"fast_flac"})
    good = [c for c in cs if c.tier > Tier.TOO_LOW]
    assert [c.username for c in good] == ["queued_24", "fast_flac", "mp3guy"]   # still before the mp3


def test_known_wrong_sizes_are_dropped():
    cs = candidates(Query.parse("Mihai Popoviciu - Waitin'"), MIHAI, known_wrong={50_000_000})
    assert "fast_flac" not in {c.username for c in cs}


def test_majority_length_beats_rarer_version():
    # BCN Dub: 136 copies at 12:04, 36 at 13:20. The rarer 13:20 is a different version.
    resp = [peer(f"u{i}", f(r"Deepchord Presents Echospace\05 - BCN Dub.mp3", 29_000_000, 724, br=320)) for i in range(5)]
    resp += [peer("rare", f(r"Echospace\04 - BCN Dub (Original Mix).flac", 84_000_000, 800), speed=20_000_000)]
    cs = candidates(Query.parse("Deepchord Presents Echospace - BCN Dub"), resp)
    assert cs[0].length == 724


def test_official_length_beats_majority():
    resp = [peer(f"u{i}", f(r"Aril Brikha\Setting Sun.flac", 80_000_000, 481)) for i in range(5)]
    resp += [peer("official", f(r"Aril Brikha\05 - Setting Sun.flac", 70_000_000, 437))]
    cs = candidates(Query.parse("Aril Brikha - Setting Sun"), resp, official_lengths=[434])
    assert cs[0].username == "official"


def test_pinned_length_filters_and_overrides_version_words():
    resp = [peer("short", f(r"x\Selan - Gravity (Roots Dub).flac", 33_000_000, 286)),
            peer("full", f(r"x\Louie Vega Remix Selan - Gravity (Roots Dub).mp3", 21_000_000, 536, br=320))]
    cs = candidates(Query.parse("Selan - Gravity (Roots Dub) | 8:54"), resp)
    assert [c.username for c in cs] == ["full"]   # "Remix" in the name is fine: you named this exact length


def test_catalog_prefers_files_from_that_release():
    resp = [peer("other", f(r"music\Roger Gerressen - Untitled 1.flac", 40_000_000, 365), speed=9_000_000),
            peer("release", f(r"[SUSH31] Roger Gerressen - Monoaware\01 - Untitled 1.flac", 40_000_000, 365))]
    cs = candidates(Query.parse("Roger Gerressen - Untitled 1 [SUSH31]"), resp)
    assert cs[0].username == "release"


def test_alac_m4a_counts_as_lossless():
    resp = [peer("a", f(r"DeepChord Presents Echospace\Liumin\1 05 BCN Dub.m4a", 85_500_000, 724))]
    assert candidates(Query.parse("Echospace - BCN Dub"), resp)[0].tier == Tier.LOSSLESS


def test_short_title_needs_whole_match():
    resp = [peer("u", f(r"x\Aril Brikha - On & On.mp3", br=320), f(r"x\Aril Brikha - Groove On.mp3", br=320),
                 f(r"x\Aril Brikha - On And On (Original Mix).mp3", br=320))]
    assert sorted(names(candidates(Query.parse("Aril Brikha - On & On"), resp))) == \
        ["Aril Brikha - On & On.mp3", "Aril Brikha - On And On (Original Mix).mp3"]


def test_mix_cd_edits_rank_last_and_dont_define_the_majority():
    # Robert Hood: the most-shared copy is the 3:41 blended edit from his Fabric 39 mix CD
    resp = [peer(f"fab{i}", f(rf"Fabric 39_ Robert Hood\31 - Robert Hood - And Then We Planned Our Escape.flac",
                              20_000_000, 221), speed=6_000_000) for i in range(6)]
    resp += [peer("orig", f(r"Robert Hood - Omega\01 And Then We Planned Our Escape.aiff", 80_000_000, 457))]
    cs = candidates(Query.parse("Robert Hood - And Then We Planned Our Escape"), resp, official_lengths=[221, 457])
    assert cs[0].username == "orig" and cs[1].mix_compilation
