"""P0-only Douyin protocol validation helpers.

This package contains a minimal, independently implemented transport envelope parser,
a privacy-safe WebSocket probe, and an offline replay reducer. It doesn't claim that
the target room has been live-verified.
"""

TARGET_METHOD = "WebcastGroupLiveGiftRecipientRecommendMessage"

__all__ = ["TARGET_METHOD"]
