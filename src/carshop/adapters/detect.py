"""Platform detection is a function, not an agent (requirements 9)."""

from __future__ import annotations

from carshop.adapters.base import Adapter
from carshop.adapters.dealer_com import DealerComAdapter
from carshop.adapters.dealer_inspire import DealerInspireAdapter
from carshop.adapters.dealerfire import DealerFireAdapter
from carshop.adapters.dealeron import DealerOnAdapter
from carshop.adapters.generic_jsonld import GenericJsonLdAdapter
from carshop.domain.codes import Platform

_ADAPTERS: dict[Platform, Adapter] = {
    Platform.DEALER_INSPIRE: DealerInspireAdapter(),
    Platform.DEALER_COM: DealerComAdapter(),
    Platform.DEALERON: DealerOnAdapter(),
    Platform.DEALERFIRE: DealerFireAdapter(),
}
# Platforms without a dedicated adapter are attempted with the generic order (7.1, 7.4):
# JSON-LD in the page, then one browser render. Failure is NOT_REPORTED / LAYOUT_UNKNOWN.
_GENERIC: Adapter = GenericJsonLdAdapter()


def adapter_for(platform: Platform) -> Adapter | None:
    return _ADAPTERS.get(platform, _GENERIC)


def has_dedicated_adapter(platform: Platform) -> bool:
    return platform in _ADAPTERS


_OTHER_MARKERS = (
    "dealereprocess",  # Dealer eProcess
    "foxdealer",
    "sokal",
    "tekion",
    "roadster",
    "/viewdetails/new/",  # Sewell group platform
    "dealer.com/",  # a Dealer.com asset host without the DDC markup (treated as other)
)


def detect_platform(html: str) -> Platform:
    """Platform from markup markers. Order matters: a page's own inventory markup beats a
    vendor asset that a theme happens to load (a DealerOn store may load Dealer Inspire fonts)."""
    h = html[:2_000_000].lower()  # SRPs ship 0.6-1.3 MB of inline CSS before the cards
    if ("result-wrap" in h and "data-vehicle" in h) or "wp-content/plugins/maven-algolia" in h:
        return Platform.DEALER_INSPIRE
    if "pricing-detail" in h or "pictures.dealer.com" in h:
        return Platform.DEALER_COM
    if "dealeron" in h or "searchnew.aspx" in h:
        return Platform.DEALERON
    if "cdkglobal" in h or "assets.cdk" in h or "sincro" in h:
        return Platform.CDK
    if "dealerfire" in h or "js-vehicle-item" in h:
        return Platform.DEALERFIRE
    if "dealerinspire" in h:
        return Platform.DEALER_INSPIRE
    if "ddc-" in h and "dealer.com" in h:
        return Platform.DEALER_COM
    if any(m in h for m in _OTHER_MARKERS):
        return Platform.OTHER
    return Platform.UNKNOWN
