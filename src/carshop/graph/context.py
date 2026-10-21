"""Per-run runtime context: the live objects nodes need but state must not hold.

Passed to the graph as LangGraph's `context`, so it is never checkpointed. The Supervisor
fills the fetchers after admission, and again on resume.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine

from carshop.config.loaders import Registry
from carshop.config.settings import Settings
from carshop.domain.codes import Trigger
from carshop.domain.models import Dealer
from carshop.extract.llm import PackageResolver
from carshop.fetch.artifacts import ArtifactStore
from carshop.fetch.browser import BrowserRenderer
from carshop.fetch.http import HttpFetcher
from carshop.fetch.politeness import HostGate


@dataclass(frozen=True, slots=True)
class Tools:
    """What a dealer branch fetches, stores pages, and asks the model with."""

    settings: Settings
    run_id: str
    http: HttpFetcher
    browser: BrowserRenderer | None
    artifacts: ArtifactStore
    gate: HostGate
    resolver: PackageResolver


@dataclass(slots=True)
class RunContext:
    engine: Engine
    settings: Settings
    trigger: Trigger
    run_id: str
    snapshot_date: str
    now: datetime
    requested_now: datetime | None
    dealer_ids: list[str] | None
    notify: bool
    rerun_of: str | None
    label: str | None
    use_browser: bool
    wall_start: float
    # Resolved at call time by `pipeline.run`, so tests can swap the HTTP layer and the mailer.
    http_factory: Callable[..., HttpFetcher]
    deliver_digest: Callable[..., dict[str, str]]
    registry: Registry | None = None
    targets: dict[str, Dealer] = field(default_factory=dict)
    run_created: bool = False
    resolver: PackageResolver | None = None
    http: HttpFetcher | None = None
    browser: BrowserRenderer | None = None
    gate: HostGate | None = None
    artifacts: ArtifactStore | None = None
    sem: asyncio.Semaphore | None = None
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    http_closed: bool = False

    @property
    def spend_usd(self) -> float:
        return self.resolver.usage.spend_usd if self.resolver is not None else 0.0

    def tools(self) -> Tools:
        assert self.http and self.artifacts and self.gate and self.resolver, "run not prepared"
        return Tools(
            settings=self.settings,
            run_id=self.run_id,
            http=self.http,
            browser=self.browser,
            artifacts=self.artifacts,
            gate=self.gate,
            resolver=self.resolver,
        )

    async def close_fetchers(self) -> None:
        """Closes the browser and the HTTP client. Safe to call more than once."""
        try:
            await self.stack.aclose()
        finally:
            self.browser = None
            if self.http is not None and not self.http_closed:
                self.http_closed = True
                await self.http.aclose()
