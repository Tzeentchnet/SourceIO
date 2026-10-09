from __future__ import annotations

from .interfaces import Maturity, ResourceCapabilities


UNSUPPORTED_CAPABILITIES = ResourceCapabilities()
CONTAINER_CAPABILITIES = ResourceCapabilities(
    read=Maturity.STABLE,
    extract=Maturity.STABLE,
    write=Maturity.EXPERIMENTAL,
)
READ_ONLY_CAPABILITIES = ResourceCapabilities(read=Maturity.STABLE)
READ_EXTRACT_CAPABILITIES = ResourceCapabilities(
    read=Maturity.STABLE,
    extract=Maturity.STABLE,
)
FULL_CAPABILITIES = ResourceCapabilities(
    read=Maturity.STABLE,
    extract=Maturity.STABLE,
    render=Maturity.STABLE,
    write=Maturity.STABLE,
)


def merge_capabilities(*capabilities: ResourceCapabilities) -> ResourceCapabilities:
    """Combine capabilities by retaining the highest maturity per operation."""
    if not capabilities:
        return UNSUPPORTED_CAPABILITIES
    return ResourceCapabilities(
        read=max(capability.read for capability in capabilities),
        extract=max(capability.extract for capability in capabilities),
        render=max(capability.render for capability in capabilities),
        write=max(capability.write for capability in capabilities),
    )


__all__ = [
    "CONTAINER_CAPABILITIES",
    "FULL_CAPABILITIES",
    "READ_EXTRACT_CAPABILITIES",
    "READ_ONLY_CAPABILITIES",
    "UNSUPPORTED_CAPABILITIES",
    "merge_capabilities",
]
