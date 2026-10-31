"""Application services — the accounting module's public interface.

Other modules import from here and never from `..models`. A business module
builds a `PostingRequest` and hands it to `PostingService.post`; it does not
construct journal entries itself, because every invariant check lives on this
side of the boundary.
"""

from app.modules.accounting.services.posting import (
    PostingLine,
    PostingRequest,
    PostingService,
)

__all__ = ["PostingLine", "PostingRequest", "PostingService"]
