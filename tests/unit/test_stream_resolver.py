from __future__ import annotations

import asyncio
from dataclasses import replace

from app.douyin.live_page import (
    LivePageResult,
    StreamCandidate,
    inspect_live_page,
    stream_candidate_from_url,
)
from app.douyin.stream_resolver import BrowserObservation, DouyinStreamResolver

ROOM_A = "https://live.douyin.com/79907888978"
ROOM_B = "https://live.douyin.com/94771623313"
SECRET_URL = (
    "https://pull.example.douyincdn.com/live/PATH-SECRET/room.flv"
    "?sign=QUERY-SECRET&expire=9"
)


class FakePageClient:
    def __init__(self, result: LivePageResult) -> None:
        self.result = result
        self.closed = False
        self.calls: list[str] = []

    async def check(self, room_reference: str) -> LivePageResult:
        self.calls.append(room_reference)
        return replace(
            self.result,
            snapshot=replace(self.result.snapshot, room_url=room_reference),
        )

    async def close(self) -> None:
        self.closed = True


def _unknown_result(room_url: str = ROOM_A) -> LivePageResult:
    return inspect_live_page(
        b"<html><body>public room page</body></html>",
        room_url=room_url,
        http_status=200,
        final_url=room_url,
    )


def _live_result(room_url: str = ROOM_A) -> LivePageResult:
    payload = (
        b'{"stream_url":{"flv":"https://pull.example.douyincdn.com/'
        b'live/static.flv?sign=STATIC-SECRET"}}'
    )
    return inspect_live_page(
        payload,
        room_url=room_url,
        http_status=200,
        final_url=room_url,
    )


def test_static_candidates_skip_browser_and_close_client() -> None:
    async def scenario() -> None:
        page = FakePageClient(_live_result())
        browser_calls = 0

        async def browser(_room_url: str, _duration: float) -> BrowserObservation:
            nonlocal browser_calls
            browser_calls += 1
            return BrowserObservation()

        resolver = DouyinStreamResolver(page, browser_observer=browser)
        result = await resolver.resolve(ROOM_A)
        assert result.snapshot.live_state == "live"
        assert result.candidates
        assert resolver.cached_candidates(ROOM_A) == result.candidates
        assert browser_calls == 0
        await resolver.close()
        assert page.closed is True
        assert resolver.cached_candidates(ROOM_A) == ()

    asyncio.run(scenario())


def test_browser_fallback_keeps_private_url_only_in_memory() -> None:
    async def scenario() -> None:
        candidate = stream_candidate_from_url(
            SECRET_URL,
            source_path="browser/network-response",
        )
        assert candidate is not None
        page = FakePageClient(_unknown_result())

        async def browser(room_url: str, duration: float) -> BrowserObservation:
            assert room_url == ROOM_A
            assert duration == 12.0
            return BrowserObservation(
                candidates=(candidate,),
                page_loaded=True,
                page_http_status=200,
                final_host="live.douyin.com",
                final_path="/79907888978",
                room_api_response_count=1,
            )

        resolver = DouyinStreamResolver(page, browser_observer=browser)
        result = await resolver.resolve(ROOM_A)
        assert result.snapshot.live_state == "live"
        assert result.candidates == (candidate,)
        assert resolver.cached_candidates(ROOM_A) == (candidate,)
        rendered = repr(result) + repr(result.snapshot) + repr(resolver)
        public = repr(result.snapshot.to_public_dict())
        for marker in ("PATH-SECRET", "QUERY-SECRET", SECRET_URL):
            assert marker not in rendered
            assert marker not in public
        assert result.snapshot.to_public_dict()["stream_candidates"][0]["query_keys"] == [
            "expire",
            "sign",
        ]

    asyncio.run(scenario())


def test_cache_ttl_eviction_discard_and_failed_refresh() -> None:
    async def scenario() -> None:
        now = [100.0]
        first = stream_candidate_from_url(SECRET_URL, source_path="browser/network-response")
        second = stream_candidate_from_url(
            SECRET_URL.replace("PATH-SECRET", "SECOND-PATH"),
            source_path="browser/network-response",
        )
        assert first is not None and second is not None
        page = FakePageClient(_unknown_result())
        observations = [
            BrowserObservation(
                candidates=(first,),
                final_host="live.douyin.com",
                final_path="/79907888978",
            ),
            BrowserObservation(
                candidates=(second,),
                final_host="live.douyin.com",
                final_path="/94771623313",
            ),
            BrowserObservation(error_code="chrome_not_found"),
        ]

        async def browser(_room_url: str, _duration: float) -> BrowserObservation:
            return observations.pop(0)

        resolver = DouyinStreamResolver(
            page,
            browser_observer=browser,
            cache_ttl_seconds=5,
            max_cached_rooms=1,
            monotonic=lambda: now[0],
        )
        await resolver.resolve(ROOM_A)
        assert resolver.cached_candidates(ROOM_A) == (first,)
        await resolver.resolve(ROOM_B)
        assert resolver.cached_candidates(ROOM_A) == ()
        assert resolver.cached_candidates(ROOM_B) == (second,)
        now[0] = 106.0
        assert resolver.cached_candidates(ROOM_B) == ()
        await resolver.resolve(ROOM_A)
        assert resolver.cached_candidates(ROOM_A) == ()
        resolver.discard(ROOM_A)
        assert resolver.cached_candidates(ROOM_A) == ()

    asyncio.run(scenario())


def test_browser_exception_is_redacted_and_does_not_raise() -> None:
    async def scenario() -> None:
        page = FakePageClient(_unknown_result())

        async def browser(_room_url: str, _duration: float) -> BrowserObservation:
            raise RuntimeError(SECRET_URL)

        resolver = DouyinStreamResolver(page, browser_observer=browser)
        result = await resolver.resolve(ROOM_A)
        assert result.snapshot.live_state == "unknown"
        assert result.candidates == ()
        rendered = repr(result.snapshot.to_public_dict())
        assert "PATH-SECRET" not in rendered
        assert "QUERY-SECRET" not in rendered

    asyncio.run(scenario())


def test_resolver_revalidates_observer_candidates_and_redacts_final_path() -> None:
    async def scenario() -> None:
        page = FakePageClient(_unknown_result())
        unsafe = StreamCandidate(
            protocol="flv",
            quality="origin",
            url="https://attacker.invalid/PATH-SECRET/live.flv?sign=QUERY-SECRET",
            source_path="browser/network-response",
        )

        async def browser(_room_url: str, _duration: float) -> BrowserObservation:
            return BrowserObservation(
                candidates=(unsafe,),
                final_host="attacker.invalid",
                final_path="/PATH-SECRET",
            )

        resolver = DouyinStreamResolver(page, browser_observer=browser)
        result = await resolver.resolve(ROOM_A)
        assert result.snapshot.live_state == "unknown"
        assert result.candidates == ()
        assert resolver.cached_candidates(ROOM_A) == ()
        assert "PATH-SECRET" not in repr(result.snapshot.to_public_dict())
        assert "QUERY-SECRET" not in repr(result.snapshot.to_public_dict())

    asyncio.run(scenario())
