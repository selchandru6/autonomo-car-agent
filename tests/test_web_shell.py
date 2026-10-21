"""App shell acceptance checks (requirements-web-ui.md section 11, checks 1-4, 6, 8, 9)."""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from carshop.web import shell
from carshop.web.app import create_app

WEB = Path(__file__).parents[1] / "src/carshop/web"
TEMPLATES = WEB / "templates"

SHELL_ROUTES = [
    "/", "/shortlist", "/inventory", "/catalog", "/dealers", "/trends", "/pipeline",
    "/check", "/health", "/runs", "/review", "/architecture",
]  # fmt: skip


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_every_template_extends_base() -> None:
    standalone = {
        p.name for p in TEMPLATES.glob("*.html") if '{% extends "base.html" %}' not in p.read_text()
    }
    assert standalone == {"base.html", "_macros.html"}
    assert not (TEMPLATES / "overview.html").exists()


def test_templates_have_no_literal_colors() -> None:
    literal = re.compile(
        r"#[0-9a-fA-F]{3,6}\b|rgb\(|(text|bg|border)-(slate|sky|emerald|amber|rose|indigo)-"
    )
    hits = [
        f"{p.name}:{n}"
        for p in TEMPLATES.glob("*.html")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if literal.search(line)
    ]
    assert hits == []
    css = (WEB / "static/app.css").read_text()
    assert "!important" not in css
    assert 'html[data-theme="light"]' not in css


def test_menu_is_four_groups_twelve_items_in_order() -> None:
    assert [i.group for i in shell.MENU].count("Shop") == 2
    assert shell.GROUPS == ("Shop", "Market", "Pipeline", "System")
    assert [i.href for i in shell.MENU] == SHELL_ROUTES


@pytest.mark.parametrize("path", SHELL_ROUTES)
def test_each_menu_page_renders_in_the_shell(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert '<nav class="menu" aria-label="Main">' in html
    assert '<header class="topbar">' in html
    assert "Unknown is not No." in html
    current = re.findall(r'<a href="([^"?]+)[^"]*" aria-current="page">', html)
    assert current == [path]
    assert html.count('aria-current="page"') == 1
    labels = [unescape(x) for x in re.findall(r'<span class="lbl">([^<]+)</span>', html)]
    assert labels[:12] == [i.label for i in shell.MENU]


def test_badges_match_the_shown_run(client: TestClient) -> None:
    status = client.get("/api/status").json()
    html = client.get("/health").text
    if status["not_reported"]:
        assert f'aria-label="{status["not_reported"]} dealers not read in this run"' in html
    assert status["pending"] == 0
    assert "pending review" not in html


def test_api_status_shape(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert set(status) == {"run", "running", "coverage", "model", "pending", "not_reported"}
    assert status["running"] is False
    assert status["coverage"] == {"reported": 21, "active": 27, "no_source": 0}
    assert status["run"]["status"] == "PUBLISHED"


def test_picked_run_carries_to_run_scoped_menu_links(client: TestClient) -> None:
    run_id = client.get("/api/status").json()["run"]["id"]
    html = client.get(f"/inventory?run_id={run_id}").text
    assert f'href="/trends?run_id={run_id}"' in html
    assert 'href="/dealers"' in html
    assert 'href="/"' in html


def test_unknown_pages_render_404_inside_the_shell(client: TestClient) -> None:
    for path in ("/vin/NOTAVIN", "/dealers/nope", "/no-such-page"):
        r = client.get(path)
        assert r.status_code == 404
        assert '<nav class="menu" aria-label="Main">' in r.text
    assert client.get("/api/nope").headers["content-type"].startswith("application/json")


def test_theme_and_sidebar_are_applied_before_paint(client: TestClient) -> None:
    html = client.get("/runs").text
    head = html.split("</head>")[0]
    assert head.index("x5finder.theme") < head.index("/static/app.css")
    assert "x5finder.sidebar" in head
    assert 'id="themeBtn"' in html and 'id="themeSystem"' in html
    assert "x5finder-theme" in head
    assert "cdn.tailwindcss.com/3.4.16" in head


def _labels(result: dict, group: str) -> list[str]:
    return [item["label"] for item in result[group]]


def test_api_search_groups_and_limits(client: TestClient) -> None:
    result = client.get("/api/search", params={"q": "bmw"}).json()
    assert list(result) == ["pages", "cars", "dealers", "runs"]
    assert 0 < len(result["dealers"]) <= 6
    for items in result.values():
        for item in items:
            assert {"label", "sublabel", "href"} <= set(item)
    assert len(client.get("/api/search", params={"q": "bmw", "limit": 2}).json()["dealers"]) == 2
    assert not any(client.get("/api/search", params={"q": "h"}).json().values())


def test_search_check_7(client: TestClient) -> None:
    vin = re.search(r'href="/vin/(\w{17})"', client.get("/inventory").text)
    assert vin
    tail = client.get("/api/search", params={"q": vin.group(1)[-6:].lower()}).json()
    assert f"/vin/{vin.group(1)}" in [c["href"] for c in tail["cars"]]
    assert client.get("/api/search", params={"q": vin.group(1)}).json()["cars"][0][
        "label"
    ] == vin.group(1)

    houston = client.get("/api/search", params={"q": "houston"}).json()["dealers"]
    assert houston and all(d["href"].startswith("/dealers/") for d in houston)
    assert {"BMW of Houston North", "BMW of West Houston"} <= {d["label"] for d in houston}

    runs = client.get("/api/search", params={"q": "2026-10-20"}).json()["runs"]
    assert runs and runs[0]["href"].startswith("/pipeline?run_id=")
    covered = [
        int(m.group(1)) if (m := re.search(r"(\d+) dealers", r["sublabel"])) else 1 for r in runs
    ]
    assert covered == sorted(covered, reverse=True)
    assert runs[0]["alt"]["href"] == "/runs"
    assert client.get("/api/search", params={"q": "oct 20"}).json()["runs"] == runs

    for word, page in (("coverage", "/health"), ("digest", "/runs"), ("agents", "/architecture")):
        hits = client.get("/api/search", params={"q": word}).json()["pages"]
        assert page in [p["href"] for p in hits], word

    page = client.get("/search", params={"q": "houston"})
    assert page.status_code == 200
    assert 'href="/dealers/' in page.text and "BMW of Houston North" in page.text


def test_palette_is_in_the_shell(client: TestClient) -> None:
    html = client.get("/runs").text
    assert 'role="dialog" aria-modal="true"' in html
    assert '<form class="searchbox" id="searchForm" action="/search"' in html
    js = (WEB / "static/shell.js").read_text()
    for needle in (
        'e.key === "/"',
        "e.metaKey || e.ctrlKey",
        "x5finder.recent",
        "setTimeout(query, 150)",
        "abort()",
    ):
        assert needle in js, needle
    assert 'action="/search"' in client.get("/no-such-page").text


def test_dealers_list_every_registry_dealer_in_buyer_words(client: TestClient) -> None:
    from carshop.config.settings import Settings
    from carshop.storage import repos
    from carshop.storage.db import make_engine, read_conn

    with read_conn(make_engine(Settings().db_path)) as conn:
        registry = repos.list_dealers(conn)
    active = [d for d in registry if d["active"]]
    html = client.get("/dealers").text
    main = html.split('<details class="panel p-5">')[0]
    assert len(
        set(
            re.findall(
                r'<td data-sort="[^"]*"><a class="link font-semibold" href="/dealers/([\w-]+)"',
                main,
            )
        )
    ) == len(active)
    results = set(re.findall(r'data-result="([^"]+)"', html))
    assert results and results <= {
        "Checked",
        "Partly checked",
        "Couldn&#39;t read the website",
        "Website not on file yet",
        "Not checked yet",
    }
    for word in ("REPORTED", "NO_SOURCE", "PARTIAL"):
        assert f">{word}<" not in main
    if len(active) < len(registry):
        assert "No longer on the BMW locator" in html


def test_dealer_detail_page(client: TestClient) -> None:
    html = client.get("/dealers").text
    with_cars = re.search(
        r'href="/dealers/([\w-]+)">[^<]+</a></td>\s*<td data-sort="[^"]*">[^<]*</td>\s*<td class="text-right tabular-nums" data-sort="[1-9]',
        html,
    )
    assert with_cars
    page = client.get(f"/dealers/{with_cars.group(1)}").text
    assert 'href="/vin/' in page
    assert "Last 30 days" in page and "cell cell-" in page
    assert (
        '<details class="panel p-5">\n  <summary class="cursor-pointer font-semibold">Technical details</summary>'
        in page
    )
    assert 'rel="noopener"' in page
    assert "/pipeline?run_id=" in page and f"dealer={with_cars.group(1)}" in page
    assert client.get("/dealers/not-a-dealer").status_code == 404


def test_vin_breadcrumb_uses_the_page_the_user_came_from(client: TestClient) -> None:
    vin = re.search(r'href="/vin/(\w{17})"', client.get("/inventory").text)
    assert vin
    plain = client.get(f"/vin/{vin.group(1)}").text
    assert '<small><a href="/inventory">Eligible cars</a> /' in plain
    came = client.get(f"/vin/{vin.group(1)}", headers={"referer": "http://x/shortlist"}).text
    assert '<small><a href="/shortlist">My shortlist</a> /' in came
