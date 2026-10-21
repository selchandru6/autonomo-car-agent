"""Buyer screen acceptance checks against the published statewide run."""

from __future__ import annotations

import math
import re
from pathlib import Path

from fastapi.testclient import TestClient

from carshop.config.settings import Settings
from carshop.storage.db import make_engine, read_conn
from carshop.web.app import create_app
from carshop.web.views import buyer_payload

FORBIDDEN = re.compile(r"REPORTED|NO_SOURCE|UNKNOWN|VALIDATED|run_id")


def _payload() -> dict:
    settings = Settings()
    engine = make_engine(settings.db_path)
    with read_conn(engine) as conn:
        return buyer_payload(conn, settings.registry_dir)


def _miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius = 3958.8

    def rad(value: float) -> float:
        return value * math.pi / 180

    d_lat = rad(b[0] - a[0])
    d_lon = rad(b[1] - a[1])
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(rad(a[0])) * math.cos(rad(b[0])) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(h))


def test_buyer_page_hides_pipeline_words() -> None:
    client = TestClient(create_app())
    html = client.get("/").text
    assert "Texas X5 Finder" in html
    assert "Cars for you" in html
    assert "Today&#39;s check" in html
    assert "Once a day" in html
    assert "6:00 AM Central" in html
    assert FORBIDDEN.search(html) is None
    assert "Price range today" in html
    assert "Use system setting" in html
    assert "x5finder.theme" in html
    assert "/static/finder.js" in html
    assert "x5finder.wish.v1" in client.get("/static/finder.js").text


def test_home_is_one_screen_and_the_old_overview_lands_on_todays_check() -> None:
    client = TestClient(create_app())
    home = client.get("/")
    assert "21 of 27 dealers" in home.text
    assert "112" in home.text
    for old in ("/operator", "/?view=check"):
        followed = client.get(old, follow_redirects=False)
        assert followed.status_code == 301
        assert followed.headers["location"] == "/check"
    check = client.get("/check").text
    assert "<h1>Today's check</h1>" in check
    assert "Nothing is waiting for a person to confirm" in check
    assert "Dealer results in this run" in check


def test_defaults_split_fit_and_might_on_the_statewide_run() -> None:
    payload = _payload()
    assert payload["asOf"] == "2026-10-20"
    assert payload["flow"]["checked"] == 21
    assert payload["flow"]["dealers"] == 27
    assert payload["flow"]["kept"] == 112
    assert payload["flow"]["priced"] == 101
    dap_yes = [car for car in payload["cars"] if car["feats"]["DAP"] == "yes"]
    assert len(dap_yes) == 24
    assert len(payload["cars"]) - len(dap_yes) == 88


def test_budget_hides_only_priced_cars_over_the_limit() -> None:
    cars = _payload()["cars"]
    over = [car for car in cars if car["price"] is not None and car["price"] > 80_000_00]
    kept = [car for car in cars if car["price"] is None or car["price"] <= 80_000_00]
    assert over
    assert len(over) + len(kept) == len(cars)
    assert all(car["price"] is None or car["noPriceWhy"] for car in cars if car["price"] is None)


def test_houston_within_50_miles_is_only_those_three_cities() -> None:
    payload = _payload()
    dealers = {dealer["id"]: dealer for dealer in payload["dealers"]}
    houston = [
        dealer
        for dealer in payload["dealers"]
        if dealer["city"] == "Houston" and dealer["lat"] is not None
    ]
    origin = (
        sum(dealer["lat"] for dealer in houston) / len(houston),
        sum(dealer["lon"] for dealer in houston) / len(houston),
    )
    near = {
        dealers[car["dealer"]]["city"]
        for car in payload["cars"]
        if dealers[car["dealer"]]["lat"] is not None
        and _miles(origin, (dealers[car["dealer"]]["lat"], dealers[car["dealer"]]["lon"])) <= 50
    }
    assert near <= {"Houston", "Katy", "The Woodlands"}
    assert "Houston" in near


def test_a_must_have_keeps_only_listed_cars_in_fit() -> None:
    cars = _payload()["cars"]
    fit = [car for car in cars if car["feats"]["MASSAGE"] == "yes"]
    assert fit
    assert all(car["feats"]["MASSAGE"] == "yes" for car in fit)
    assert any(car["feats"]["MASSAGE"] != "yes" for car in cars)


def test_trend_is_steady_across_cars_priced_on_two_days() -> None:
    payload = _payload()
    both = 0
    changed = 0
    for car in payload["cars"]:
        priced = [point for point in car["history"] if point[1] is not None]
        if len(priced) < 2:
            continue
        both += 1
        if abs(priced[-1][1] - priced[0][1]) >= 100:
            changed += 1
    assert both == 42
    assert changed == 0
    assert payload["days"][0] == "2026-10-19"


def test_one_same_vin_drop_is_going_down() -> None:
    history = [["2026-10-19", 80_000_00], ["2026-10-20", 79_000_00]]
    first, last = history[0][1], history[-1][1]
    assert abs(last - first) >= 100
    assert last < first


def test_dealer_list_covers_every_texas_dealer() -> None:
    dealers = _payload()["dealers"]
    assert len(dealers) == 27
    checked = [dealer for dealer in dealers if dealer["status"] == "checked"]
    unread = [dealer for dealer in dealers if dealer["status"] != "checked"]
    assert len(checked) == 21
    assert {dealer["reason"] for dealer in unread} <= {
        "Couldn't read the website",
        "Website not on file yet",
        "Partly checked",
    }


def test_lowest_and_highest_are_the_ends_of_the_cash_prices() -> None:
    cars = [car for car in _payload()["cars"] if car["price"] is not None]
    ordered = sorted(cars, key=lambda car: (car["price"], car["firstSeen"], car["vin"]))
    low, high = ordered[0], ordered[-1]
    assert low["price"] == min(car["price"] for car in cars)
    assert high["price"] == max(car["price"] for car in cars)
    assert low["price"] == 7205000
    assert high["price"] == 9377400
    assert all(
        feature in low["feats"]
        for feature in (
            "DAP",
            "M_SPORT",
            "MASSAGE",
            "PANORAMIC_ROOF",
            "SKY_LOUNGE",
            "EXECUTIVE",
            "CLIMATE_COMFORT",
            "AIR_SUSPENSION",
        )
    )


CSS = Path(__file__).parents[1] / "src/carshop/web/static/app.css"


def _themes() -> tuple[dict[str, str], dict[str, str]]:
    blocks = re.findall(r"(:root[^{]*)\{([^{}]*)\}", CSS.read_text())
    light = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", blocks[0][1]))
    dark = next(dict(re.findall(r"(--[\w-]+):\s*([^;]+);", b)) for s, b in blocks if "dark" in s)
    return light, dark


def _rgb(value: str, under: tuple[int, int, int]) -> tuple[int, int, int]:
    value = value.strip()
    if value.startswith("#"):
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    r, g, b, a = (float(p) for p in re.findall(r"[\d.]+", value))
    return tuple(round(c * a + u * (1 - a)) for c, u in zip((r, g, b), under, strict=True))  # type: ignore[return-value]


def test_theme_tokens_meet_aa_contrast_in_both_themes() -> None:
    pairs = [
        ("--ink", "--bg"), ("--ink", "--surface"), ("--muted", "--bg"), ("--muted", "--surface"),
        ("--accent", "--surface"), ("--ok-fg", "--ok-bg"), ("--warn-fg", "--warn-bg"),
        ("--bad-fg", "--bad-bg"), ("--none-fg", "--none-bg"), ("--model", "--model-soft"),
        ("--good", "--good-soft"),
    ]  # fmt: skip
    for theme in _themes():
        surface = _rgb(theme["--surface"], (0, 0, 0))
        for fg, bg in pairs:
            back = _rgb(theme[bg], surface)
            ratio = _contrast(_rgb(theme[fg], back), back)
            assert ratio >= 4.5, (fg, bg, round(ratio, 2))


def _contrast(ink: tuple[int, int, int], surface: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    def lum(rgb: tuple[int, int, int]) -> float:
        r, g, b = (channel(part) for part in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    lighter, darker = sorted((lum(ink), lum(surface)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
