from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ..interfaces import Diagnostic, Maturity, ResourceCapabilities
from ..provenance import ResourceProvenance
from .reporting import CapabilityDiagnostic

if TYPE_CHECKING:
    from ..blocks.base import BaseBlock
    from ..compiled_file_header import BlockInfo
    from ..compiled_resource import CompiledResource


class SerializationScope(str, Enum):
    RESOURCE = "resource"
    BLOCK = "block"


class SerializationMode(str, Enum):
    UNSUPPORTED = "unsupported"
    SERIALIZE = "serialize"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True, slots=True)
class SemanticValidation:
    valid: bool
    diagnostics: tuple[Diagnostic, ...] = ()

    @classmethod
    def success(cls) -> "SemanticValidation":
        return cls(True)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        **context: object,
    ) -> "SemanticValidation":
        return cls(False, (CapabilityDiagnostic.error(code, message, **context),))


@dataclass(frozen=True, slots=True)
class BlockSerializationContext:
    resource: "CompiledResource"
    block_index: int
    block_name: str
    block_info: "BlockInfo"
    block: "BaseBlock"
    original_bytes: bytes
    provenance: ResourceProvenance | None
    resource_capabilities: ResourceCapabilities | None


BlockSerializer = Callable[[BlockSerializationContext], bytes]
BlockSemanticValidator = Callable[[BlockSerializationContext, bytes], SemanticValidation]
BlockCompatibilityCheck = Callable[[BlockSerializationContext], str | None]


@dataclass(frozen=True, slots=True)
class BlockSerializationRule:
    block_type: type
    serializer: BlockSerializer
    semantic_validator: BlockSemanticValidator
    evidence: tuple[str, ...]
    compatibility_check: BlockCompatibilityCheck | None = None
    include_subclasses: bool = False
    maturity: Maturity = Maturity.EXPERIMENTAL

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("Block serialization rules require verification evidence")


ResourceSemanticValidator = Callable[
    ["CompiledResource", bytes, tuple[bytes, ...], object | None],
    SemanticValidation,
]
ResourceCapabilityGate = Callable[[ResourceCapabilities | None], str | None]
ProvenanceValidator = Callable[[ResourceProvenance, "CompiledResource"], str | None]


@dataclass(frozen=True, slots=True)
class ResourceSerializationRule:
    resource_type: type
    semantic_validator: ResourceSemanticValidator
    evidence: tuple[str, ...]
    requires_provenance: bool = True
    capability_gate: ResourceCapabilityGate | None = None
    provenance_validator: ProvenanceValidator | None = None
    maturity: Maturity = Maturity.EXPERIMENTAL

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("Resource serialization rules require verification evidence")


@dataclass(frozen=True, slots=True)
class SerializationCapability:
    scope: SerializationScope
    subject: str
    supported: bool
    mode: SerializationMode
    reason: str | None = None
    block_index: int | None = None
    block_name: str | None = None
    serializer: str | None = None
    maturity: Maturity = Maturity.EXPERIMENTAL
    evidence: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class SerializationPreflightReport:
    resource: SerializationCapability
    blocks: tuple[SerializationCapability, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def supported(self) -> bool:
        return self.resource.supported and all(block.supported for block in self.blocks)

    @property
    def unsupported_blocks(self) -> tuple[SerializationCapability, ...]:
        return tuple(block for block in self.blocks if not block.supported)

    @property
    def unsupported_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not self.resource.supported and self.resource.reason:
            reasons.append(self.resource.reason)
        reasons.extend(
            block.reason
            for block in self.unsupported_blocks
            if block.reason is not None
        )
        return tuple(reasons)


class SerializationRegistry:
    def __init__(self):
        self._resources: dict[type, ResourceSerializationRule] = {}
        self._blocks: dict[type, BlockSerializationRule] = {}

    def register_resource(self, rule: ResourceSerializationRule) -> None:
        if rule.resource_type in self._resources:
            raise ValueError(f"Resource serializer already registered for {rule.resource_type.__name__}")
        self._resources[rule.resource_type] = rule

    def register_block(self, rule: BlockSerializationRule) -> None:
        if rule.block_type in self._blocks:
            raise ValueError(f"Block serializer already registered for {rule.block_type.__name__}")
        self._blocks[rule.block_type] = rule

    def resource_rule_for(self, resource: object) -> ResourceSerializationRule | None:
        return self._resources.get(type(resource))

    def block_rule_for_type(self, block_type: type) -> BlockSerializationRule | None:
        exact = self._blocks.get(block_type)
        if exact is not None:
            return exact
        for parent in block_type.__mro__[1:]:
            rule = self._blocks.get(parent)
            if rule is not None and rule.include_subclasses:
                return rule
        return None

    def block_rule_for(self, block: object) -> BlockSerializationRule | None:
        return self.block_rule_for_type(type(block))
