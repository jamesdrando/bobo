from __future__ import annotations

from .base import ChatResult, LLMProvider, ProviderRegistry, ProviderRequest
from .bedrock import BedrockProvider
from .openrouter import OpenRouterProvider


def build_default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("bedrock", BedrockProvider())
    registry.register("openrouter", OpenRouterProvider())
    return registry


DEFAULT_PROVIDER_REGISTRY = build_default_registry()

__all__ = [
    "BedrockProvider",
    "ChatResult",
    "DEFAULT_PROVIDER_REGISTRY",
    "LLMProvider",
    "OpenRouterProvider",
    "ProviderRegistry",
    "ProviderRequest",
    "build_default_registry",
]
