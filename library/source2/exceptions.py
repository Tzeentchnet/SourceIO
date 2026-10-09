from __future__ import annotations

from typing import Any


class Source2Error(Exception):
    """Base class for Source 2 resource errors."""

    def __init__(
            self,
            message: str,
            *,
            path: str | None = None,
            offset: int | None = None,
            block_name: str | None = None,
            details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.path = path
        self.offset = offset
        self.block_name = block_name
        self.details = details or {}


class ResourceFormatError(Source2Error):
    """The resource bytes are malformed or internally inconsistent."""


class ResourceTruncatedError(ResourceFormatError):
    """The resource ends before a required structure is complete."""


class HeaderError(ResourceFormatError):
    """Base class for compiled-resource header errors."""


class InvalidHeaderError(HeaderError):
    """The compiled-resource header is structurally invalid."""


class UnsupportedHeaderVersionError(HeaderError):
    """The compiled-resource header version is not supported."""

    def __init__(self, version: int, supported: tuple[int, ...], **context: Any):
        super().__init__(
            f"Unsupported Source 2 header version {version}; supported versions: {supported}",
            details={"version": version, "supported": supported},
            **context,
        )
        self.version = version
        self.supported = supported


class FileSizeError(HeaderError):
    """The declared resource size does not match the available bytes."""

    def __init__(self, declared_size: int, actual_size: int, **context: Any):
        super().__init__(
            f"Compiled resource declares {declared_size} bytes, but {actual_size} bytes are available",
            details={"declared_size": declared_size, "actual_size": actual_size},
            **context,
        )
        self.declared_size = declared_size
        self.actual_size = actual_size


class BlockTableError(HeaderError):
    """The block table is outside the file or otherwise invalid."""


class BlockError(ResourceFormatError):
    """Base class for compiled-resource block errors."""


class BlockBoundsError(BlockTableError, BlockError):
    """A block points outside the resource or overlaps structural data."""


class BlockIndexError(BlockError, IndexError):
    """A requested block index is not present in the resource."""


class BlockParseError(BlockError):
    """A known block could not be decoded."""


class MissingBlock(BlockError):
    """A required block is not present."""


class ResourceVersionError(ResourceFormatError):
    """Base class for resource data-version errors."""


class UnsupportedResourceVersionError(ResourceVersionError):
    """No registered parser supports the resource data version."""

    def __init__(
            self,
            version: int,
            *,
            kind: str | None = None,
            supported: tuple[int, ...] | None = None,
            **context: Any,
    ):
        kind_text = f" for {kind}" if kind else ""
        supported_text = f"; supported versions: {supported}" if supported else ""
        super().__init__(
            f"Unsupported resource version {version}{kind_text}{supported_text}",
            details={"version": version, "kind": kind, "supported": supported},
            **context,
        )
        self.version = version
        self.kind = kind
        self.supported = supported


class ResourceRegistrationError(Source2Error):
    """A resource parser registration is invalid or ambiguous."""


class UnknownResourceError(Source2Error):
    """The resource kind cannot be identified."""


class KV3Error(ResourceFormatError):
    """Base class for binary KeyValues 3 errors."""


class KV3UnsupportedVersion(KV3Error):
    """The input uses a recognized but unsupported KV3 version."""


class KV3ValidationError(KV3Error):
    """The KV3 stream violates its declared layout."""


class NTROError(ResourceFormatError):
    """Base class for resource-introspection errors."""


class UnsupportedNTROVersionError(NTROError):
    """The NTRO manifest or a manifest record has an unsupported version."""

    def __init__(self, version: int, *, expected: int = 4, **context: Any):
        super().__init__(
            f"Unsupported NTRO version {version}; expected {expected}",
            details={"version": version, "expected": expected},
            **context,
        )
        self.version = version
        self.expected = expected


class MissingIntrospectionError(NTROError):
    """An NTRO operation was requested without introspection metadata."""


# Concise aliases are retained for callers that prefer exception names without
# an ``Error`` suffix.
UnsupportedHeaderVersion = UnsupportedHeaderVersionError
UnsupportedResourceVersion = UnsupportedResourceVersionError
UnsupportedNTROVersion = UnsupportedNTROVersionError
