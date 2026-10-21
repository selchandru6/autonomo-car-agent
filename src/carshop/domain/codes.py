"""Normative vocabularies from requirements section 7.3, 7.8, 7.9 and 10."""

from __future__ import annotations

from enum import StrEnum


class DealerStatus(StrEnum):
    REPORTED = "REPORTED"
    PARTIAL = "PARTIAL"
    NOT_REPORTED = "NOT_REPORTED"
    NO_SOURCE = "NO_SOURCE"


class ReasonCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    HTTP_403 = "HTTP_403"
    HTTP_404 = "HTTP_404"
    HTTP_429 = "HTTP_429"
    CAPTCHA = "CAPTCHA"
    UNRENDERED_SHELL = "UNRENDERED_SHELL"
    LAYOUT_UNKNOWN = "LAYOUT_UNKNOWN"
    PAGINATION_INCOMPLETE = "PAGINATION_INCOMPLETE"
    PARSER_FAILED = "PARSER_FAILED"
    DETAILS_FAILED = "DETAILS_FAILED"
    BUDGET = "BUDGET"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    OFF_DOMAIN = "OFF_DOMAIN"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    # A refused connection or a TLS failure. Not retried (review G1, ADR 0006).
    CONNECT_FAILED = "CONNECT_FAILED"
    TLS_FAILED = "TLS_FAILED"
    SOURCE_NOT_APPROVED = "SOURCE_NOT_APPROVED"
    COUNT_ANOMALY = "COUNT_ANOMALY"
    # v1.3 (F25): a locator dealer with no discoverable public inventory URL is NO_SOURCE.
    # The discovery reason itself (no hostname, homepage unreachable, ...) is on the dealer row.
    NO_INVENTORY_URL = "NO_INVENTORY_URL"


class ExclusionCode(StrEnum):
    NOT_NEW = "NOT_NEW"
    NOT_MY2026 = "NOT_MY2026"
    NOT_X5 = "NOT_X5"
    NOT_XDRIVE40I = "NOT_XDRIVE40I"
    MODEL_X5M = "MODEL_X5M"
    POWERTRAIN_OTHER = "POWERTRAIN_OTHER"
    USED_OR_CPO = "USED_OR_CPO"
    DEMO_MILEAGE = "DEMO_MILEAGE"
    SALE_PENDING = "SALE_PENDING"
    OUT_OF_PATH = "OUT_OF_PATH"
    OUT_OF_REGION = "OUT_OF_REGION"
    VIN_CHECKSUM_FAIL = "VIN_CHECKSUM_FAIL"


class Flag(StrEnum):
    DAP_YES = "DAP_YES"
    DAP_NO = "DAP_NO"
    DAP_UNKNOWN = "DAP_UNKNOWN"
    PRICE_MISSING = "PRICE_MISSING"
    PRICE_IMPLAUSIBLE = "PRICE_IMPLAUSIBLE"
    PRICE_IS_PAYMENT = "PRICE_IS_PAYMENT"
    CALL_FOR_PRICE = "CALL_FOR_PRICE"
    CONDITIONAL_ONLY = "CONDITIONAL_ONLY"
    CLASSIFICATION_CONFLICT = "CLASSIFICATION_CONFLICT"
    IN_TRANSIT = "IN_TRANSIT"
    PKG_M_SPORT = "PKG_M_SPORT"
    TRANSFER_SUSPECTED = "TRANSFER_SUSPECTED"
    COLOR_UNMAPPED = "COLOR_UNMAPPED"
    SCRAPE_ANOMALY_SUSPECTED = "SCRAPE_ANOMALY_SUSPECTED"
    PRICE_LABEL_UNKNOWN = "PRICE_LABEL_UNKNOWN"


class Tri(StrEnum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class Sufficiency(StrEnum):
    VERIFIED = "VERIFIED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICT = "CONFLICT"


class SourceKind(StrEnum):
    LISTING = "listing"
    DETAIL = "detail"
    STICKER = "sticker"
    JSONLD = "jsonld"


class Feature(StrEnum):
    DAP = "DAP"
    M_SPORT = "M_SPORT"
    MASSAGE = "MASSAGE"
    PANORAMIC_ROOF = "PANORAMIC_ROOF"
    SKY_LOUNGE = "SKY_LOUNGE"
    EXECUTIVE = "EXECUTIVE"
    CLIMATE_COMFORT = "CLIMATE_COMFORT"
    AIR_SUSPENSION = "AIR_SUSPENSION"


class PriceKind(StrEnum):
    MSRP = "MSRP"
    ADVERTISED = "ADVERTISED"
    CONDITIONAL_INCENTIVE = "CONDITIONAL_INCENTIVE"
    PAYMENT = "PAYMENT"
    FEE = "FEE"
    ADDON = "ADDON"
    UNCLASSIFIED = "UNCLASSIFIED"


class PriceBasis(StrEnum):
    """What a comparison price is measured against (review C4, vehicle-only basis)."""

    VEHICLE_ONLY = "vehicle_only"
    INCLUDES_FEES = "includes_fees"
    UNKNOWN = "unknown"


class LifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    MISSING_UNVERIFIED = "MISSING_UNVERIFIED"
    DELISTED = "DELISTED"
    SOLD_CONFIRMED = "SOLD_CONFIRMED"
    TRANSFERRED = "TRANSFERRED"
    RELISTED = "RELISTED"
    UNKNOWN = "UNKNOWN"


class StagingStatus(StrEnum):
    VALIDATED = "VALIDATED"
    PENDING_REVIEW = "PENDING_REVIEW"
    REJECTED = "REJECTED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class Trigger(StrEnum):
    CRON = "cron"
    MANUAL_DEALER = "manual_dealer"
    MANUAL_DATE = "manual_date"
    CATCHUP = "catchup"
    REPLAY = "replay"


class Platform(StrEnum):
    DEALER_COM = "dealer_com"
    DEALER_INSPIRE = "dealer_inspire"
    DEALERON = "dealeron"
    DEALERFIRE = "dealerfire"
    CDK = "cdk"
    OTHER = "other"
    UNKNOWN = "unknown"


class DiscoveryReason(StrEnum):
    """Why a registry row has (or lacks) a start URL (F25)."""

    OVERRIDE = "override"  # operator-supplied start URL takes precedence
    HOMEPAGE_LINK = "homepage_link"  # X5 new-inventory link found on the homepage
    PLATFORM_CONVENTION = "platform_convention"  # platform's conventional URL verified live
    SITEMAP = "sitemap"  # found in the sitemap
    UNFILTERED_NEW = "unfiltered_new"  # new-inventory page found but no X5 filter; wide page
    NO_HOSTNAME = "no_hostname"  # locator lists no website for the dealer
    HOMEPAGE_UNREACHABLE = "homepage_unreachable"  # homepage failed for an unclassified reason
    HOMEPAGE_CONNECT_FAILED = "homepage_connect_failed"  # connection refused / DNS, not a timeout
    HOMEPAGE_TLS_FAILED = "homepage_tls_failed"  # certificate or handshake failure
    HOMEPAGE_TIMEOUT = "homepage_timeout"  # the homepage timed out
    HOMEPAGE_BLOCKED = "homepage_blocked"  # 403 / captcha on the homepage
    ROBOTS_DISALLOWED = "robots_disallowed"  # robots forbids the inventory paths
    NO_INVENTORY_LINK = "no_inventory_link"  # homepage and sitemap had no new-inventory URL
    OFF_HOST = "off_host"  # inventory lives on another host (not the registry host)


class TermsStatus(StrEnum):
    ALLOWED = "allowed"
    OWNER_ACCEPTED_RISK = "owner_accepted_risk"
    DISALLOWED = "disallowed"
    PENDING = "pending"


class ExtractionMethod(StrEnum):
    PARSER = "parser"
    LLM = "llm"
    HUMAN = "human"


class PriceEvent(StrEnum):
    DROP = "DROP"
    INCREASE = "INCREASE"
    APPEARED = "APPEARED"
    WITHDRAWN = "WITHDRAWN"
