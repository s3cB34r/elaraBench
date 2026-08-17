"""Provider contracts and deterministic test providers."""

from elarabench.providers.base import ModelProvider
from elarabench.providers.fake import FakeProvider

__all__ = ["FakeProvider", "ModelProvider"]
