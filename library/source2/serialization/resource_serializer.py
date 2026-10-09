from __future__ import annotations

import os
import struct
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ...utils import MemoryBuffer, WritableMemoryBuffer
from ..blocks.all_blocks import guess_block_type
from ..blocks.base import BaseBlock
from ..blocks.binary_blob import BinaryBlob
from ..blocks.kv3_block import KVBlock
from ..blocks.resource_external_reference_list import ResourceExternalReferenceList
from ..compiled_file_header import CompiledHeader
from ..compiled_resource import CompiledResource
from ..exceptions import Source2Error
from ..interfaces import (
    Diagnostic,
    Maturity,
    ResourceCapabilities,
    ResourceOperation,
)
from ..keyvalues3.binary_keyvalues import read_valve_keyvalue3
from ..keyvalues3.enums import KV3Signature
from ..keyvalues3.types import Array, BaseType, NullObject, Object, TypedArray
from ..particles.kv3 import deep_clone_kv3
from ..provenance import ResourceProvenance, normalize_resource_path
from ..utils.ntro_reader import NTROBuffer
from .capabilities import (
    BlockSerializationContext,
    BlockSerializationRule,
    ResourceSerializationRule,
    SemanticValidation,
    SerializationCapability,
    SerializationMode,
    SerializationPreflightReport,
    SerializationRegistry,
    SerializationScope,
)
from .reporting import CapabilityDiagnostic, DiagnosticSeverity


_PREFLIGHT_ERRORS = (
    AssertionError,
    BufferError,
    EOFError,
    IndexError,
    KeyError,
    NotImplementedError,
    RuntimeError,
    Source2Error,
    TypeError,
    UnicodeError,
    ValueError,
    OverflowError,
    struct.error,
)


@dataclass(frozen=True, slots=True)
class PreparedBlock:
    index: int
    name: str
    data: bytes
    capability: SerializationCapability


@dataclass(frozen=True, slots=True)
class SerializationPlan:
    report: SerializationPreflightReport
    blocks: tuple[PreparedBlock, ...]
    data: bytes | None

    def require_supported(self) -> bytes:
        if not self.report.supported or self.data is None:
            raise SerializationBlockedError(self.report)
        return self.data


@dataclass(frozen=True, slots=True)
class AtomicSerializationResult:
    path: Path
    bytes_written: int
    report: SerializationPreflightReport


class SerializationBlockedError(RuntimeError):
    def __init__(self, report: SerializationPreflightReport):
        details = []
        if not report.resource.supported:
            details.append(f"resource: {report.resource.reason or 'unsupported'}")
        details.extend(
            f"block {block.block_index} {block.block_name}: {block.reason or 'unsupported'}"
            for block in report.unsupported_blocks
        )
        super().__init__("Serialization preflight failed: " + "; ".join(details))
        self.report = report


def _read_original_block(resource: CompiledResource, index: int) -> bytes:
    data = resource.get_block_bytes(block_id=index)
    if data is None:
        raise BufferError(f"Block {index} has no source bytes")
    return data


def _expected_block_type(resource: CompiledResource, index: int) -> type[BaseBlock]:
    info = resource._header.blocks[index]
    loaded = resource._blocks.get(index)
    if loaded is not None:
        return type(loaded)
    if info.name == "DATA":
        data_type = resource.get_data_block_type()
        if data_type is not None:
            return data_type
    return guess_block_type(info.name)


def _unsupported_block(
    index: int,
    name: str,
    reason: str,
    *,
    diagnostic: Diagnostic | None = None,
    maturity: Maturity = Maturity.EXPERIMENTAL,
) -> SerializationCapability:
    return SerializationCapability(
        scope=SerializationScope.BLOCK,
        subject=f"{name}[{index}]",
        supported=False,
        mode=SerializationMode.UNSUPPORTED,
        reason=reason,
        block_index=index,
        block_name=name,
        maturity=maturity,
        diagnostics=(diagnostic,) if diagnostic is not None else (),
    )


def _resource_capability(
    resource: CompiledResource,
    rule: ResourceSerializationRule | None,
    *,
    provenance: ResourceProvenance | None,
    resource_capabilities: ResourceCapabilities | None,
) -> SerializationCapability:
    subject = type(resource).__name__
    if rule is None:
        return SerializationCapability(
            SerializationScope.RESOURCE,
            subject,
            False,
            SerializationMode.UNSUPPORTED,
            "No exact resource serialization rule is registered",
        )
    if rule.requires_provenance and not isinstance(provenance, ResourceProvenance):
        return SerializationCapability(
            SerializationScope.RESOURCE,
            subject,
            False,
            SerializationMode.UNSUPPORTED,
            "A ResourceProvenance instance is required",
            maturity=rule.maturity,
            evidence=rule.evidence,
        )
    if provenance is not None and rule.provenance_validator is not None:
        reason = rule.provenance_validator(provenance, resource)
        if reason is not None:
            return SerializationCapability(
                SerializationScope.RESOURCE,
                subject,
                False,
                SerializationMode.UNSUPPORTED,
                reason,
                maturity=rule.maturity,
                evidence=rule.evidence,
            )
    if rule.capability_gate is not None:
        reason = rule.capability_gate(resource_capabilities)
        if reason is not None:
            return SerializationCapability(
                SerializationScope.RESOURCE,
                subject,
                False,
                SerializationMode.UNSUPPORTED,
                reason,
                maturity=rule.maturity,
                evidence=rule.evidence,
            )
    return SerializationCapability(
        SerializationScope.RESOURCE,
        subject,
        True,
        SerializationMode.SERIALIZE,
        serializer="compiled-resource-container",
        maturity=rule.maturity,
        evidence=rule.evidence,
    )


def prepare_resource_serialization(
    resource: CompiledResource,
    *,
    registry: SerializationRegistry | None = None,
    provenance: ResourceProvenance | None = None,
    resource_capabilities: ResourceCapabilities | None = None,
) -> SerializationPlan:
    registry = registry or create_default_serialization_registry()
    resource_capabilities = resource_capabilities or resource.capabilities
    resource_rule = registry.resource_rule_for(resource)
    resource_capability = _resource_capability(
        resource,
        resource_rule,
        provenance=provenance,
        resource_capabilities=resource_capabilities,
    )
    prepared: list[PreparedBlock] = []
    block_capabilities: list[SerializationCapability] = []
    diagnostics: list[Diagnostic] = []

    for index, info in enumerate(resource._header.blocks):
        if len(info.name) != 4 or not info.name.isascii():
            capability = _unsupported_block(
                index,
                info.name,
                "Block names must be four ASCII characters",
            )
            block_capabilities.append(capability)
            continue

        try:
            original_bytes = _read_original_block(resource, index)
        except _PREFLIGHT_ERRORS as error:
            diagnostic = CapabilityDiagnostic.error(
                "serialization.block.read_failed",
                "The original block bytes could not be read during preflight.",
                block_index=index,
                block_name=info.name,
                error_type=type(error).__name__,
                error=str(error),
            )
            diagnostics.append(diagnostic)
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                "Original block bytes are unavailable",
                diagnostic=diagnostic,
            ))
            continue

        expected_type = _expected_block_type(resource, index)
        rule = registry.block_rule_for_type(expected_type)
        if rule is None:
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                f"No block serialization rule is registered for {expected_type.__name__}",
            ))
            continue

        try:
            block = resource.get_block(expected_type, block_id=index)
        except _PREFLIGHT_ERRORS as error:
            diagnostic = CapabilityDiagnostic.error(
                "serialization.block.parse_failed",
                "The block could not be parsed for serialization.",
                block_index=index,
                block_name=info.name,
                error_type=type(error).__name__,
                error=str(error),
            )
            diagnostics.append(diagnostic)
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                "Block parsing failed",
                diagnostic=diagnostic,
                maturity=rule.maturity,
            ))
            continue

        if block is None:
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                "The resource returned no block instance",
                maturity=rule.maturity,
            ))
            continue

        actual_rule = registry.block_rule_for(block)
        if actual_rule is None:
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                f"No block serialization rule is registered for {type(block).__name__}",
            ))
            continue
        rule = actual_rule
        context = BlockSerializationContext(
            resource,
            index,
            info.name,
            info,
            block,
            original_bytes,
            provenance,
            resource_capabilities,
        )

        if rule.compatibility_check is not None:
            reason = rule.compatibility_check(context)
            if reason is not None:
                block_capabilities.append(_unsupported_block(
                    index,
                    info.name,
                    reason,
                    maturity=rule.maturity,
                ))
                continue

        try:
            serialized = rule.serializer(context)
            if not isinstance(serialized, bytes):
                raise TypeError("Block serializer must return bytes")
            validation = rule.semantic_validator(context, serialized)
        except _PREFLIGHT_ERRORS as error:
            diagnostic = CapabilityDiagnostic.error(
                "serialization.block.serializer_failed",
                "The block serializer failed during preflight.",
                block_index=index,
                block_name=info.name,
                error_type=type(error).__name__,
                error=str(error),
            )
            diagnostics.append(diagnostic)
            block_capabilities.append(_unsupported_block(
                index,
                info.name,
                "Block serializer failed during preflight",
                diagnostic=diagnostic,
                maturity=rule.maturity,
            ))
            continue

        if not validation.valid:
            diagnostics.extend(validation.diagnostics)
            block_capabilities.append(SerializationCapability(
                SerializationScope.BLOCK,
                f"{info.name}[{index}]",
                False,
                SerializationMode.UNSUPPORTED,
                "Serialized block failed semantic validation",
                block_index=index,
                block_name=info.name,
                serializer=rule.serializer.__name__,
                maturity=rule.maturity,
                evidence=rule.evidence,
                diagnostics=validation.diagnostics,
            ))
            continue

        capability = SerializationCapability(
            SerializationScope.BLOCK,
            f"{info.name}[{index}]",
            True,
            SerializationMode.SERIALIZE,
            block_index=index,
            block_name=info.name,
            serializer=rule.serializer.__name__,
            maturity=rule.maturity,
            evidence=rule.evidence,
            diagnostics=validation.diagnostics,
        )
        block_capabilities.append(capability)
        prepared.append(PreparedBlock(index, info.name, serialized, capability))

    data: bytes | None = None
    if resource_capability.supported and all(capability.supported for capability in block_capabilities):
        prepared.sort(key=lambda item: item.index)
        try:
            data = _build_compiled_resource(resource, tuple(prepared))
            if resource_rule is None:
                raise RuntimeError("Resource rule disappeared during serialization")
            validation = resource_rule.semantic_validator(
                resource,
                data,
                tuple(block.data for block in prepared),
                provenance,
            )
        except _PREFLIGHT_ERRORS as error:
            diagnostic = CapabilityDiagnostic.error(
                "serialization.resource.serializer_failed",
                "The resource container serializer failed during preflight.",
                resource_type=type(resource).__name__,
                error_type=type(error).__name__,
                error=str(error),
            )
            diagnostics.append(diagnostic)
            resource_capability = replace(
                resource_capability,
                supported=False,
                mode=SerializationMode.UNSUPPORTED,
                reason="Resource container serialization failed during preflight",
                diagnostics=(diagnostic,),
            )
            data = None
        else:
            diagnostics.extend(validation.diagnostics)
            if not validation.valid:
                resource_capability = replace(
                    resource_capability,
                    supported=False,
                    mode=SerializationMode.UNSUPPORTED,
                    reason="Serialized resource failed semantic validation",
                    diagnostics=validation.diagnostics,
                )
                data = None

    report = SerializationPreflightReport(
        resource_capability,
        tuple(block_capabilities),
        tuple(diagnostics),
    )
    return SerializationPlan(report, tuple(prepared), data)


def preflight_resource_serialization(
    resource: CompiledResource,
    *,
    registry: SerializationRegistry | None = None,
    provenance: ResourceProvenance | None = None,
    resource_capabilities: ResourceCapabilities | None = None,
) -> SerializationPreflightReport:
    return prepare_resource_serialization(
        resource,
        registry=registry,
        provenance=provenance,
        resource_capabilities=resource_capabilities,
    ).report


def serialize_resource_to_bytes(
    resource: CompiledResource,
    *,
    registry: SerializationRegistry | None = None,
    provenance: ResourceProvenance | None = None,
    resource_capabilities: ResourceCapabilities | None = None,
) -> bytes:
    return prepare_resource_serialization(
        resource,
        registry=registry,
        provenance=provenance,
        resource_capabilities=resource_capabilities,
    ).require_supported()


def write_resource_atomic(
    resource: CompiledResource,
    destination: str | os.PathLike[str],
    *,
    registry: SerializationRegistry | None = None,
    provenance: ResourceProvenance | None = None,
    resource_capabilities: ResourceCapabilities | None = None,
) -> AtomicSerializationResult:
    plan = prepare_resource_serialization(
        resource,
        registry=registry,
        provenance=provenance,
        resource_capabilities=resource_capabilities,
    )
    data = plan.require_supported()
    path = Path(destination)
    if not path.parent.is_dir():
        raise FileNotFoundError(f"Destination directory does not exist: {path.parent}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return AtomicSerializationResult(path, len(data), plan.report)


def _build_compiled_resource(
    resource: CompiledResource,
    blocks: tuple[PreparedBlock, ...],
) -> bytes:
    count = len(blocks)
    header_size = 16 + 12 * count
    total_size = header_size + sum(len(block.data) for block in blocks)
    output = bytearray(total_size)
    struct.pack_into(
        "<IHHII",
        output,
        0,
        total_size,
        resource._header.header_version,
        resource._header.resource_version,
        8,
        count,
    )

    data_offset = header_size
    for table_index, block in enumerate(blocks):
        entry_offset = 16 + table_index * 12
        name = block.name.encode("ascii")
        relative_offset = data_offset - (entry_offset + 4)
        struct.pack_into(
            "<4sII",
            output,
            entry_offset,
            name,
            relative_offset,
            len(block.data),
        )
        output[data_offset:data_offset + len(block.data)] = block.data
        data_offset += len(block.data)
    return bytes(output)


def validate_compiled_resource_structure(
    resource: CompiledResource,
    serialized: bytes,
    block_payloads: tuple[bytes, ...],
    provenance: ResourceProvenance | None,
) -> SemanticValidation:
    try:
        header = CompiledHeader.from_buffer(MemoryBuffer(serialized))
    except _PREFLIGHT_ERRORS as error:
        return SemanticValidation.failure(
            "serialization.resource.reparse_failed",
            "Serialized resource header could not be reparsed.",
            error_type=type(error).__name__,
            error=str(error),
        )

    expected_names = tuple(block.name for block in resource._header.blocks)
    actual_names = tuple(block.name for block in header.blocks)
    actual_sizes = tuple(block.size for block in header.blocks)
    expected_sizes = tuple(len(payload) for payload in block_payloads)
    if (
        header.file_size != len(serialized)
        or header.header_version != resource._header.header_version
        or header.resource_version != resource._header.resource_version
        or actual_names != expected_names
        or actual_sizes != expected_sizes
    ):
        return SemanticValidation.failure(
            "serialization.resource.structure_mismatch",
            "Serialized resource structure does not match the preflight plan.",
            expected_names=expected_names,
            actual_names=actual_names,
            expected_sizes=expected_sizes,
            actual_sizes=actual_sizes,
        )
    return SemanticValidation.success()


def _serialize_existing_block(context: BlockSerializationContext) -> bytes:
    output = WritableMemoryBuffer()
    context.block.to_buffer(output)
    return bytes(output.data)


def _serialize_kv3_block(context: BlockSerializationContext) -> bytes:
    block = context.block
    if not isinstance(block, KVBlock):
        raise TypeError(f"Expected KVBlock, got {type(block).__name__}")
    cloned_data = deep_clone_kv3(block)
    if not isinstance(cloned_data, Object):
        raise TypeError("KV3 block root must be an object")
    clone = type(block)(
        cloned_data,
        getattr(block, "_version"),
        getattr(block, "_format"),
    )
    clone.custom_name = block.custom_name
    output = WritableMemoryBuffer()
    clone.to_buffer(output)
    return bytes(output.data)


def _kv3_block_compatibility(context: BlockSerializationContext) -> str | None:
    if len(context.original_bytes) < 4 or not KV3Signature.is_valid(context.original_bytes[:4]):
        return "Only blocks originally encoded as KV3 are supported; NTRO-derived objects are refused"
    return None


def _validate_binary_blob(
    context: BlockSerializationContext,
    serialized: bytes,
) -> SemanticValidation:
    block = context.block
    if not isinstance(block, BinaryBlob):
        return SemanticValidation.failure(
            "serialization.binary_blob.type_mismatch",
            "Binary blob validator received another block type.",
        )
    expected = bytes(block.data.data)
    if serialized != expected:
        return SemanticValidation.failure(
            "serialization.binary_blob.mismatch",
            "Serialized binary blob differs from its source bytes.",
        )
    return SemanticValidation.success()


def _validate_rerl(
    context: BlockSerializationContext,
    serialized: bytes,
) -> SemanticValidation:
    block = context.block
    if not isinstance(block, ResourceExternalReferenceList):
        return SemanticValidation.failure(
            "serialization.rerl.type_mismatch",
            "RERL validator received another block type.",
        )
    reparsed = ResourceExternalReferenceList.from_buffer(NTROBuffer(serialized, None, None))
    expected = tuple((item.hash, item.r_id, item.name, item.unk) for item in block)
    actual = tuple((item.hash, item.r_id, item.name, item.unk) for item in reparsed)
    if actual != expected:
        return SemanticValidation.failure(
            "serialization.rerl.semantic_mismatch",
            "Serialized external references differ from the source references.",
            expected=expected,
            actual=actual,
        )
    return SemanticValidation.success()


def _validate_kv3(
    context: BlockSerializationContext,
    serialized: bytes,
) -> SemanticValidation:
    block = context.block
    if not isinstance(block, KVBlock):
        return SemanticValidation.failure(
            "serialization.kv3.type_mismatch",
            "KV3 validator received another block type.",
        )
    reparsed = read_valve_keyvalue3(MemoryBuffer(serialized))
    if not _kv3_semantically_equal(block, reparsed):
        return SemanticValidation.failure(
            "serialization.kv3.semantic_mismatch",
            "Serialized KV3 data does not semantically match the source block.",
        )
    return SemanticValidation.success()


def _kv3_semantically_equal(left: object, right: object) -> bool:
    if isinstance(left, Mapping):
        if not isinstance(right, Mapping) or set(left) != set(right):
            return False
        return all(_kv3_semantically_equal(left[key], right[key]) for key in left)

    if isinstance(left, np.ndarray):
        if isinstance(right, np.ndarray):
            return left.dtype == right.dtype and left.shape == right.shape and np.array_equal(left, right)
        if isinstance(right, Sequence) and not isinstance(right, (str, bytes)):
            return len(left) == len(right) and all(
                _kv3_semantically_equal(item, other)
                for item, other in zip(left.tolist(), right)
            )
        return False

    if isinstance(left, (Array, TypedArray, list, tuple)):
        if isinstance(right, np.ndarray):
            return _kv3_semantically_equal(right, left)
        if not isinstance(right, Sequence) or isinstance(right, (str, bytes)):
            return False
        if len(left) != len(right):
            return False
        if isinstance(left, TypedArray) and isinstance(right, TypedArray):
            if left.data_type != right.data_type or left.data_specifier != right.data_specifier:
                return False
        return all(_kv3_semantically_equal(item, other) for item, other in zip(left, right))

    if isinstance(left, NullObject):
        return isinstance(right, (NullObject, type(None)))

    if isinstance(left, BaseType):
        if isinstance(left, bytes):
            values_equal = bytes(left) == bytes(right) if isinstance(right, bytes) else False
        else:
            values_equal = left == right
        return values_equal and getattr(left, "specifier", None) == getattr(right, "specifier", None)

    return left == right


def require_experimental_write_capability(
    capabilities: ResourceCapabilities | None,
) -> str | None:
    if not isinstance(capabilities, ResourceCapabilities):
        return "ResourceCapabilities are required for serialization"
    if not capabilities.supports(ResourceOperation.WRITE, Maturity.EXPERIMENTAL):
        return "Resource write capability is below experimental maturity"
    return None


def validate_resource_provenance(
    provenance: ResourceProvenance,
    resource: CompiledResource,
) -> str | None:
    expected = normalize_resource_path(resource.path)
    if provenance.root_resource != expected:
        return (
            f"Provenance root {provenance.root_resource!r} does not match "
            f"resource path {expected!r}"
        )
    return None


def create_default_serialization_registry() -> SerializationRegistry:
    registry = SerializationRegistry()
    registry.register_resource(ResourceSerializationRule(
        CompiledResource,
        validate_compiled_resource_structure,
        (
            "SourceIO compiled resource header parser",
            "SourceIO tests/experimental/test_serialization.py",
        ),
        requires_provenance=True,
        capability_gate=require_experimental_write_capability,
        provenance_validator=validate_resource_provenance,
    ))
    registry.register_block(BlockSerializationRule(
        BinaryBlob,
        _serialize_existing_block,
        _validate_binary_blob,
        (
            "BinaryBlob.to_buffer exact-byte validation",
            "SourceIO tests/experimental/test_serialization.py",
        ),
    ))
    registry.register_block(BlockSerializationRule(
        ResourceExternalReferenceList,
        _serialize_existing_block,
        _validate_rerl,
        (
            "ResourceExternalReferenceList round-trip semantic comparison",
            "SourceIO tests/experimental/test_serialization.py",
        ),
    ))
    registry.register_block(BlockSerializationRule(
        KVBlock,
        _serialize_kv3_block,
        _validate_kv3,
        (
            "SourceIO KV3 writer semantic tree comparison",
            "SourceIO tests/kv3_tests/test_kv3_rw.py",
            "SourceIO tests/experimental/test_serialization.py",
        ),
        compatibility_check=_kv3_block_compatibility,
        include_subclasses=True,
    ))
    return registry
