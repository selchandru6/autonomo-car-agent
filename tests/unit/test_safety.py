import pytest

from carshop.domain.codes import ReasonCode
from carshop.domain.models import Dealer
from carshop.safety import robots, ssrf
from carshop.safety.allowlist import check_url


def test_allowlist_happy_path(dealer: Dealer) -> None:
    assert check_url("https://www.bmwoftest.com/new-vehicles/x5/?_p=2", dealer).allowed
    assert check_url("https://www.bmwoftest.com/inventory/new-2026-bmw-x5/", dealer).allowed
    assert check_url("https://www.bmwoftest.com/robots.txt", dealer).allowed
    # A prefix "/new-vehicles/" admits the bare page "/new-vehicles" but not "/new-vehiclesX".
    assert check_url("https://www.bmwoftest.com/new-vehicles", dealer).allowed
    assert not check_url("https://www.bmwoftest.com/new-vehiclesX", dealer).allowed


def test_allowlist_fails_closed(dealer: Dealer) -> None:
    for url in [
        "http://www.bmwoftest.com/new-vehicles/x5/",  # not https
        "https://user:pw@www.bmwoftest.com/new-vehicles/x5/",  # userinfo
        "https://www.bmwoftest.com.evil.com/new-vehicles/",  # host suffix trick
        "https://evil.com/new-vehicles/",
        "https://www.bmwoftest.com/used-vehicles/",  # outside prefixes
        "https://www.bmwoftest.com@evil.com/new-vehicles/",
    ]:
        d = check_url(url, dealer)
        assert not d.allowed and d.reason is ReasonCode.OFF_DOMAIN, url


def test_sticker_host_only_when_allowed(dealer: Dealer) -> None:
    assert not check_url("https://sticker.example.com/x.pdf", dealer).allowed
    assert check_url("https://sticker.example.com/x.pdf", dealer, allow_sticker=True).allowed


def test_punycode_host_match(dealer: Dealer) -> None:
    d2 = dealer.model_copy(update={"hostname": "www.bmwoftëst.com"})
    assert check_url("https://www.xn--bmwoftst-y1a.com/new-vehicles/", d2).allowed
    assert not check_url(
        "https://www.xn--bmwoftst-65a.com/new-vehicles/", d2
    ).allowed  # a different IDN host


def test_robots_disallow_fails_closed() -> None:
    body = "User-agent: *\nDisallow: /inventory/\nAllow: /new-vehicles/\n"
    assert robots.evaluate(body, 200, "https://h/new-vehicles/x5/", "carshop/0.1").allowed
    assert not robots.evaluate(body, 200, "https://h/inventory/abc", "carshop/0.1").allowed
    assert robots.evaluate(body, 200, "https://h/new-vehicles/x5/", "carshop/0.1").sha256


def test_robots_unavailable_is_allow_all() -> None:
    assert robots.evaluate(None, 404, "https://h/anything", "carshop/0.1").allowed
    assert robots.evaluate(None, 403, "https://h/anything", "carshop/0.1").allowed


def test_H2_robots_5xx_is_disallow() -> None:  # noqa: N802
    denied = robots.evaluate("garbage", 503, "https://h/anything", "carshop/0.1")
    assert not denied.allowed
    assert "unreachable" in denied.detail
    assert not robots.evaluate(None, None, "https://h/anything", "carshop/0.1").allowed


def test_ssrf_blocks_literal_private_ips() -> None:
    for host in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "[::1]", "0.0.0.0"]:
        with pytest.raises(ssrf.SsrfBlocked):
            ssrf.resolve_and_check(f"https://{host}/x")
