from carshop.domain.codes import Feature, SourceKind, Sufficiency, Tri
from carshop.domain.features import evaluate_dap, evaluate_features, merge_feature_sets

KW = {"url": "https://x/1", "page_hash": "h"}


def test_dap_yes_requires_full_phrase_or_code() -> None:
    for text in [
        "Driving Assistance Professional Package",
        "ZDY Driving Assistance Pro",
        "5AU Driving Assistant Professional",
    ]:
        ev = evaluate_dap(
            text, source_kind=SourceKind.DETAIL, is_parsed_sticker_options=False, **KW
        )
        assert ev.status is Tri.YES and ev.sufficiency is Sufficiency.VERIFIED
        assert ev.evidence is not None and ev.evidence.quote in text


def test_driving_assistant_plus_is_not_dap() -> None:
    ev = evaluate_dap(
        "Active Driving Assistant Plus 5AS",
        source_kind=SourceKind.DETAIL,
        is_parsed_sticker_options=False,
        **KW,
    )
    assert ev.status is Tri.UNKNOWN


def test_dap_no_only_from_parsed_sticker() -> None:
    text = "Options: 5AS Active Driving Assistant, ZPP Premium Package"
    unk = evaluate_dap(text, source_kind=SourceKind.DETAIL, is_parsed_sticker_options=False, **KW)
    assert unk.status is Tri.UNKNOWN
    no = evaluate_dap(text, source_kind=SourceKind.STICKER, is_parsed_sticker_options=True, **KW)
    assert no.status is Tri.NO and no.sufficiency is Sufficiency.VERIFIED


def test_quote_is_exact_substring() -> None:
    text = "Equipped with the Driving Assistance Professional Package and more"
    ev = evaluate_dap(text, source_kind=SourceKind.DETAIL, is_parsed_sticker_options=False, **KW)
    assert ev.evidence is not None
    assert ev.evidence.quote in text


def test_m_sport_detection() -> None:
    feats = {
        f.feature: f
        for f in evaluate_features(
            "M Sport Package ZMP, Panoramic Moonroof", source_kind=SourceKind.LISTING, **KW
        )
    }
    assert feats[Feature.M_SPORT].status is Tri.YES
    assert feats[Feature.PANORAMIC_ROOF].status is Tri.YES
    assert feats[Feature.DAP].status is Tri.UNKNOWN


def test_merge_yes_beats_unknown_and_conflicts_flagged() -> None:
    a = evaluate_features("Driving Assistance Professional", source_kind=SourceKind.DETAIL, **KW)
    b = evaluate_features("nothing here", source_kind=SourceKind.LISTING, **KW)
    merged = {f.feature: f for f in merge_feature_sets(a, b)}
    assert merged[Feature.DAP].status is Tri.YES
    sticker_no = [
        evaluate_dap(
            "5AS only", source_kind=SourceKind.STICKER, is_parsed_sticker_options=True, **KW
        )
    ]
    conflict = {f.feature: f for f in merge_feature_sets(a, sticker_no)}
    assert conflict[Feature.DAP].sufficiency is Sufficiency.CONFLICT
