from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from typing import Collection, Iterable, TypeVar

from ..utils import Buffer, MemoryBuffer, TinyPath
from .capabilities import CONTAINER_CAPABILITIES
from .compiled_resource import CompiledResource
from .exceptions import (
    ResourceRegistrationError,
    Source2Error,
    UnsupportedResourceVersionError,
)
from .interfaces import (
    Diagnostic,
    DiagnosticSeverity,
    Maturity,
    ResourceCapabilities,
    ResourceIdentity,
    ResourceKind,
)
from .keyvalues3.enums import KV3Signature

CompiledResourceType = TypeVar("CompiledResourceType", bound=CompiledResource)


_EXTENSION_KINDS: dict[str, ResourceKind] = {
    ".vmdl": ResourceKind.MODEL,
    ".vmdl_c": ResourceKind.MODEL,
    ".vmesh": ResourceKind.MESH,
    ".vmesh_c": ResourceKind.MESH,
    ".vmat": ResourceKind.MATERIAL,
    ".vmat_c": ResourceKind.MATERIAL,
    ".vtex": ResourceKind.TEXTURE,
    ".vtex_c": ResourceKind.TEXTURE,
    ".tga": ResourceKind.TEXTURE,
    ".png": ResourceKind.TEXTURE,
    ".jpg": ResourceKind.TEXTURE,
    ".jpeg": ResourceKind.TEXTURE,
    ".exr": ResourceKind.TEXTURE,
    ".psd": ResourceKind.TEXTURE,
    ".vphys": ResourceKind.PHYSICS,
    ".vphys_c": ResourceKind.PHYSICS,
    ".vmorf": ResourceKind.MORPH,
    ".vmorf_c": ResourceKind.MORPH,
    ".vanim": ResourceKind.ANIMATION,
    ".vanim_c": ResourceKind.ANIMATION,
    ".vagrp": ResourceKind.ANIMATION_GROUP,
    ".vagrp_c": ResourceKind.ANIMATION_GROUP,
    ".vseq": ResourceKind.ANIMATION_SEQUENCE,
    ".vseq_c": ResourceKind.ANIMATION_SEQUENCE,
    ".vnmgraph": ResourceKind.ANIMATION_GRAPH,
    ".vnmgraph_c": ResourceKind.ANIMATION_GRAPH,
    ".vnmclip": ResourceKind.ANIMATION_CLIP,
    ".vnmclip_c": ResourceKind.ANIMATION_CLIP,
    ".vwrld": ResourceKind.WORLD,
    ".vwrld_c": ResourceKind.WORLD,
    ".vwnod": ResourceKind.WORLD_NODE,
    ".vwnod_c": ResourceKind.WORLD_NODE,
    ".vmap": ResourceKind.MAP,
    ".vmap_c": ResourceKind.MAP,
    ".vents": ResourceKind.ENTITY_LUMP,
    ".vents_c": ResourceKind.ENTITY_LUMP,
    ".vrman": ResourceKind.RESOURCE_MANIFEST,
    ".vrman_c": ResourceKind.RESOURCE_MANIFEST,
    ".vmanifest": ResourceKind.RESOURCE_MANIFEST,
    ".vmanifest_c": ResourceKind.RESOURCE_MANIFEST,
    ".vpcf": ResourceKind.PARTICLE_SYSTEM,
    ".vpcf_c": ResourceKind.PARTICLE_SYSTEM,
    ".vsnap": ResourceKind.PARTICLE_SNAPSHOT,
    ".vsnap_c": ResourceKind.PARTICLE_SNAPSHOT,
    ".vsnd": ResourceKind.SOUND,
    ".vsnd_c": ResourceKind.SOUND,
    ".wav": ResourceKind.SOUND,
    ".mp3": ResourceKind.SOUND,
    ".vsndevts": ResourceKind.SOUND_EVENT,
    ".vsndevts_c": ResourceKind.SOUND_EVENT,
    ".vcs": ResourceKind.SHADER,
    ".vcs_c": ResourceKind.SHADER,
    ".vfont": ResourceKind.FONT,
    ".vfont_c": ResourceKind.FONT,
    ".vnav": ResourceKind.NAVIGATION,
    ".vnav_c": ResourceKind.NAVIGATION,
    ".vpost": ResourceKind.POST_PROCESS,
    ".vpost_c": ResourceKind.POST_PROCESS,
}

_TEXT_KIND_HINTS: tuple[tuple[str, ResourceKind], ...] = (
    ("animationgraph", ResourceKind.ANIMATION_GRAPH),
    ("animgraph", ResourceKind.ANIMATION_GRAPH),
    ("animationgroup", ResourceKind.ANIMATION_GROUP),
    ("sequencegroup", ResourceKind.ANIMATION_SEQUENCE),
    ("worldnode", ResourceKind.WORLD_NODE),
    ("entitylump", ResourceKind.ENTITY_LUMP),
    ("resourcemanifest", ResourceKind.RESOURCE_MANIFEST),
    ("particlesnapshot", ResourceKind.PARTICLE_SNAPSHOT),
    ("particlesystem", ResourceKind.PARTICLE_SYSTEM),
    ("particle", ResourceKind.PARTICLE_SYSTEM),
    ("soundevent", ResourceKind.SOUND_EVENT),
    ("material", ResourceKind.MATERIAL),
    ("texture", ResourceKind.TEXTURE),
    ("physics", ResourceKind.PHYSICS),
    ("vphys", ResourceKind.PHYSICS),
    ("morph", ResourceKind.MORPH),
    ("model", ResourceKind.MODEL),
    ("mesh", ResourceKind.MESH),
    ("world", ResourceKind.WORLD),
    ("vmap", ResourceKind.MAP),
    ("animation", ResourceKind.ANIMATION),
    ("sound", ResourceKind.SOUND),
    ("shader", ResourceKind.SHADER),
    ("font", ResourceKind.FONT),
)

_NTRO_KIND_HINTS: dict[str, ResourceKind] = {
    "PermModelData_t": ResourceKind.MODEL,
    "MaterialResourceData_t": ResourceKind.MATERIAL,
    "VPhysXAggregateData_t": ResourceKind.PHYSICS,
    "MorphSetData_t": ResourceKind.MORPH,
    "AnimationGroupResourceData_t": ResourceKind.ANIMATION_GROUP,
    "SequenceGroupResourceData_t": ResourceKind.ANIMATION_SEQUENCE,
    "World_t": ResourceKind.WORLD,
    "WorldNode_t": ResourceKind.WORLD_NODE,
    "EntityLump_t": ResourceKind.ENTITY_LUMP,
    "ResourceManifest_t": ResourceKind.RESOURCE_MANIFEST,
}

_KV3_SIGNATURES = tuple(signature.value for signature in KV3Signature)


def _normalize_extension(extension: str) -> str:
    extension = extension.strip().lower()
    if not extension:
        raise ValueError("Resource extension must not be empty")
    if "/" in extension or "\\" in extension:
        extension = TinyPath(extension).suffix.lower()
    if not extension.startswith("."):
        extension = "." + extension
    return extension


def _normalize_strings(values: Iterable[str]) -> frozenset[str]:
    return frozenset(value.strip().casefold() for value in values if value.strip())


def _kind_from_text(value: str) -> ResourceKind | None:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    for token, kind in _TEXT_KIND_HINTS:
        if token in normalized:
            return kind
    return None


@dataclass(frozen=True, slots=True)
class ResourceRegistration:
    kind: ResourceKind
    resource_type: type[CompiledResource]
    extensions: frozenset[str]
    capabilities: ResourceCapabilities
    supported_versions: frozenset[int] | None = None
    minimum_version: int | None = None
    maximum_version: int | None = None
    compiler_identifiers: frozenset[str] = frozenset()
    input_extensions: frozenset[str] = frozenset()
    ntro_structs: frozenset[str] = frozenset()
    control_signatures: frozenset[str] = frozenset()
    data_magics: tuple[bytes, ...] = ()
    priority: int = 0

    def supports_version(self, version: int) -> bool:
        if self.supported_versions is not None:
            return version in self.supported_versions
        if self.minimum_version is not None and version < self.minimum_version:
            return False
        if self.maximum_version is not None and version > self.maximum_version:
            return False
        return True

    @property
    def version_description(self) -> tuple[int, ...] | tuple[int | None, int | None] | None:
        if self.supported_versions is not None:
            return tuple(sorted(self.supported_versions))
        if self.minimum_version is not None or self.maximum_version is not None:
            return self.minimum_version, self.maximum_version
        return None


@dataclass(slots=True)
class _Evidence:
    extension: str | None
    compiler_identifiers: tuple[str, ...]
    metadata_strings: tuple[str, ...]
    input_paths: tuple[str, ...]
    ntro_structs: tuple[str, ...]
    control_tokens: frozenset[str]
    data_prefix: bytes
    diagnostics: list[Diagnostic]


class ResourceRegistry:
    def __init__(self, *, include_builtins: bool = True):
        self._registrations: list[ResourceRegistration] = []
        if include_builtins:
            self._register_builtin_resources()

    @property
    def registrations(self) -> tuple[ResourceRegistration, ...]:
        return tuple(self._registrations)

    def register(
            self,
            kind: ResourceKind,
            resource_type: type[CompiledResourceType],
            *,
            extensions: Iterable[str] = (),
            capabilities: ResourceCapabilities | None = None,
            supported_versions: Collection[int] | range | None = None,
            minimum_version: int | None = None,
            maximum_version: int | None = None,
            compiler_identifiers: Iterable[str] = (),
            input_extensions: Iterable[str] = (),
            ntro_structs: Iterable[str] = (),
            control_signatures: Iterable[str] = (),
            data_magics: Iterable[bytes] = (),
            priority: int = 0,
            replace: bool = False,
    ) -> ResourceRegistration:
        if not isinstance(kind, ResourceKind):
            raise TypeError("kind must be a ResourceKind")
        if not isinstance(resource_type, type) or not issubclass(resource_type, CompiledResource):
            raise TypeError("resource_type must be a CompiledResource subclass")
        if supported_versions is not None and (
                minimum_version is not None or maximum_version is not None
        ):
            raise ResourceRegistrationError(
                "Use supported_versions or minimum/maximum version bounds, not both"
            )

        exact_versions = None
        if supported_versions is not None:
            exact_versions = frozenset(supported_versions)
            if not exact_versions:
                raise ResourceRegistrationError("supported_versions must not be empty")
            if any(not 0 <= version <= 0xFFFF for version in exact_versions):
                raise ResourceRegistrationError("Resource versions must fit in uint16")
        if minimum_version is not None and not 0 <= minimum_version <= 0xFFFF:
            raise ResourceRegistrationError("minimum_version must fit in uint16")
        if maximum_version is not None and not 0 <= maximum_version <= 0xFFFF:
            raise ResourceRegistrationError("maximum_version must fit in uint16")
        if (
                minimum_version is not None
                and maximum_version is not None
                and minimum_version > maximum_version
        ):
            raise ResourceRegistrationError(
                "minimum_version must not exceed maximum_version"
            )

        normalized_extensions = frozenset(
            _normalize_extension(extension) for extension in extensions
        )
        normalized_input_extensions = frozenset(
            _normalize_extension(extension) for extension in input_extensions
        )
        normalized_magics = tuple(bytes(magic) for magic in data_magics)
        if any(not magic for magic in normalized_magics):
            raise ResourceRegistrationError("DATA magic values must not be empty")

        registration = ResourceRegistration(
            kind=kind,
            resource_type=resource_type,
            extensions=normalized_extensions,
            capabilities=capabilities or resource_type.declared_capabilities,
            supported_versions=exact_versions,
            minimum_version=minimum_version,
            maximum_version=maximum_version,
            compiler_identifiers=_normalize_strings(compiler_identifiers),
            input_extensions=normalized_input_extensions,
            ntro_structs=frozenset(ntro_structs),
            control_signatures=_normalize_strings(control_signatures),
            data_magics=normalized_magics,
            priority=priority,
        )

        if replace:
            self._registrations = [
                existing
                for existing in self._registrations
                if not self._registration_replaced_by(existing, registration)
            ]
        else:
            for existing in self._registrations:
                if existing == registration:
                    return existing

        self._registrations.append(registration)
        return registration

    @staticmethod
    def _registration_replaced_by(
            existing: ResourceRegistration,
            replacement: ResourceRegistration,
    ) -> bool:
        if existing.kind is not replacement.kind:
            return False
        if not replacement.extensions:
            return True
        return bool(existing.extensions & replacement.extensions)

    def unregister(
            self,
            kind: ResourceKind,
            resource_type: type[CompiledResource] | None = None,
    ) -> tuple[ResourceRegistration, ...]:
        removed = tuple(
            registration
            for registration in self._registrations
            if registration.kind is kind
            and (resource_type is None or registration.resource_type is resource_type)
        )
        self._registrations = [
            registration
            for registration in self._registrations
            if registration not in removed
        ]
        return removed

    def identify(
            self,
            buffer: Buffer | bytes | bytearray | memoryview,
            path: str | PathLike[str] | None = None,
    ) -> ResourceIdentity:
        data = self._snapshot(buffer)
        identity, _ = self._identify_bytes(data, path)
        return identity

    def parse(
            self,
            buffer: Buffer | bytes | bytearray | memoryview,
            path: str | PathLike[str] | None = None,
    ) -> CompiledResource:
        data = self._snapshot(buffer)
        identity, evidence = self._identify_bytes(data, path)
        registration = self._select_registration(identity, evidence)
        resource_type = (
            registration.resource_type
            if registration is not None
            else CompiledResource
        )
        capabilities = (
            registration.capabilities
            if registration is not None
            else CONTAINER_CAPABILITIES
        )
        normalized_path = (
            TinyPath(str(path)) if path is not None else TinyPath("<memory>")
        )
        if registration is None:
            resource = self._container_from_bytes(data, normalized_path)
        else:
            resource = resource_type.from_buffer(
                MemoryBuffer(data),
                normalized_path,
            )
        resource._set_registry_metadata(identity, capabilities)
        resource.preload_metadata()
        return resource

    @staticmethod
    def _snapshot(buffer: Buffer | bytes | bytearray | memoryview) -> bytes:
        if isinstance(buffer, (bytes, bytearray, memoryview)):
            return bytes(buffer)
        if not isinstance(buffer, Buffer):
            raise TypeError(
                "buffer must be a SourceIO Buffer or bytes-like object, "
                f"got {type(buffer).__name__}"
            )
        offset = buffer.tell()
        return bytes(buffer.ro_view(offset, buffer.size() - offset))

    def _identify_bytes(
            self,
            data: bytes,
            path: str | PathLike[str] | None,
    ) -> tuple[ResourceIdentity, _Evidence]:
        normalized_path = str(path) if path is not None else None
        resource = self._container_from_bytes(
            data,
            TinyPath(normalized_path)
            if normalized_path is not None else TinyPath("<memory>"),
        )
        evidence = self._collect_evidence(resource, normalized_path)
        signals: dict[ResourceKind, dict[str, int]] = {}

        def add_signal(kind: ResourceKind, description: str, weight: int):
            kind_signals = signals.setdefault(kind, {})
            kind_signals[description] = max(kind_signals.get(description, 0), weight)

        if evidence.extension is not None:
            extension_kind = _EXTENSION_KINDS.get(evidence.extension)
            if extension_kind is not None:
                add_signal(
                    extension_kind,
                    f"path extension {evidence.extension}",
                    35,
                )

        for compiler_identifier in evidence.compiler_identifiers:
            compiler_kind = _kind_from_text(compiler_identifier)
            if compiler_kind is not None:
                add_signal(
                    compiler_kind,
                    f"compiler metadata {compiler_identifier!r}",
                    100,
                )
        for metadata_string in evidence.metadata_strings:
            metadata_kind = _kind_from_text(metadata_string)
            if metadata_kind is not None:
                add_signal(
                    metadata_kind,
                    f"REDI/RED2 metadata {metadata_string!r}",
                    70,
                )
        for input_path in evidence.input_paths:
            input_extension = TinyPath(input_path).suffix.lower()
            input_kind = _EXTENSION_KINDS.get(input_extension)
            if input_kind is not None:
                add_signal(
                    input_kind,
                    f"input dependency extension {input_extension}",
                    80,
                )
        for struct_name in evidence.ntro_structs:
            ntro_kind = _NTRO_KIND_HINTS.get(struct_name)
            if ntro_kind is not None:
                add_signal(
                    ntro_kind,
                    f"NTRO structure {struct_name}",
                    90,
                )
        for token in evidence.control_tokens:
            control_kind = _kind_from_text(token)
            if control_kind is not None:
                add_signal(
                    control_kind,
                    f"CTRL token {token!r}",
                    60,
                )

        if evidence.data_prefix[:4] in _KV3_SIGNATURES:
            add_signal(ResourceKind.KEYVALUES3, "DATA contains KV3 magic", 10)
        elif (
                len(evidence.data_prefix) >= 4
                and evidence.data_prefix[1:4] == b"3VK"
        ):
            evidence.diagnostics.append(Diagnostic(
                code="source2.kv3.unsupported_version",
                message=(
                    f"DATA uses unrecognized KV3 version "
                    f"{evidence.data_prefix[0]}"
                ),
                severity=DiagnosticSeverity.ERROR,
                path=normalized_path,
                block_name="DATA",
                details={"signature": evidence.data_prefix[:4].hex()},
            ))

        for registration in self._registrations:
            for description, weight in self._registration_signals(
                    registration,
                    evidence,
            ):
                add_signal(registration.kind, description, weight)

        scored = sorted(
            (
                (sum(kind_signals.values()), kind, tuple(kind_signals))
                for kind, kind_signals in signals.items()
            ),
            key=lambda item: (-item[0], item[1].value),
        )
        diagnostics = list(evidence.diagnostics)
        if not scored:
            kind = ResourceKind.UNKNOWN
            confidence = 0.0
            winning_evidence: tuple[str, ...] = ()
        elif len(scored) > 1 and scored[0][0] == scored[1][0]:
            kind = ResourceKind.UNKNOWN
            confidence = 0.0
            winning_evidence = ()
            diagnostics.append(Diagnostic(
                code="source2.identity.ambiguous",
                message=(
                    f"Resource evidence is tied between "
                    f"{scored[0][1].value} and {scored[1][1].value}"
                ),
                severity=DiagnosticSeverity.WARNING,
                path=normalized_path,
                details={
                    "candidates": (
                        (scored[0][1].value, scored[0][0]),
                        (scored[1][1].value, scored[1][0]),
                    ),
                },
            ))
        else:
            top_score, kind, winning_evidence = scored[0]
            confidence = min(1.0, top_score / 100.0)
            if len(scored) > 1 and scored[1][1] is not ResourceKind.KEYVALUES3:
                diagnostics.append(Diagnostic(
                    code="source2.identity.conflicting_evidence",
                    message=(
                        f"Selected {kind.value}; secondary evidence indicates "
                        f"{scored[1][1].value}"
                    ),
                    severity=DiagnosticSeverity.WARNING,
                    path=normalized_path,
                    details={
                        "selected_score": top_score,
                        "secondary_score": scored[1][0],
                    },
                ))

        registrations_for_kind = [
            registration
            for registration in self._registrations
            if registration.kind is kind
        ]
        if (
                kind is not ResourceKind.UNKNOWN
                and registrations_for_kind
                and not any(
                    registration.supports_version(resource.header.resource_version)
                    for registration in registrations_for_kind
                )
        ):
            diagnostics.append(Diagnostic(
                code="source2.resource.unsupported_version",
                message=(
                    f"No {kind.value} parser supports resource version "
                    f"{resource.header.resource_version}"
                ),
                severity=DiagnosticSeverity.ERROR,
                path=normalized_path,
                offset=resource.header.file_offset + 6,
                details={
                    "version": resource.header.resource_version,
                    "supported": tuple(
                        registration.version_description
                        for registration in registrations_for_kind
                    ),
                },
            ))

        identity = ResourceIdentity(
            kind=kind,
            resource_version=resource.header.resource_version,
            header_version=resource.header.header_version,
            path=normalized_path,
            extension=evidence.extension,
            compiler=evidence.compiler_identifiers[0]
            if evidence.compiler_identifiers else None,
            input_path=evidence.input_paths[0] if evidence.input_paths else None,
            confidence=confidence,
            evidence=winning_evidence,
            diagnostics=tuple(diagnostics),
        )
        return identity, evidence

    @staticmethod
    def _container_from_bytes(data: bytes, path: TinyPath) -> CompiledResource:
        declared_size = int.from_bytes(data[:4], "little") if len(data) >= 4 else len(data)
        header_bytes = (
            data[:declared_size]
            if 0 < declared_size <= len(data)
            else data
        )
        try:
            resource = CompiledResource.from_buffer(
                MemoryBuffer(header_bytes),
                path,
            )
        except Source2Error as exc:
            if exc.path is None:
                exc.path = str(path)
            raise
        if len(header_bytes) != len(data):
            resource._buffer = MemoryBuffer(data)
        return resource

    @staticmethod
    def _collect_evidence(
            resource: CompiledResource,
            path: str | None,
    ) -> _Evidence:
        from .blocks.kv3_block import KVBlock
        from .blocks.resource_edit_info import ResourceEditInfo, ResourceEditInfo2
        from .blocks.resource_introspection_manifest.manifest import (
            ResourceIntrospectionManifest,
        )

        extension = TinyPath(path).suffix.lower() if path is not None else None
        compiler_identifiers: list[str] = []
        metadata_strings: list[str] = []
        input_paths: list[str] = []
        ntro_structs: list[str] = []
        control_tokens: set[str] = set()

        ntro = resource.get_block(
            ResourceIntrospectionManifest,
            block_name="NTRO",
        )
        if ntro is not None:
            ntro_structs.extend(struct.name for struct in ntro.info.structs)

        for block_name, block_type in (
                ("REDI", ResourceEditInfo),
                ("RED2", ResourceEditInfo2),
        ):
            for edit_info in resource.get_blocks(block_type, block_name):
                for dependency in edit_info.special_deps:
                    if dependency.compiler_id:
                        compiler_identifiers.append(str(dependency.compiler_id))
                    if dependency.string:
                        metadata_strings.append(str(dependency.string))
                for dependency in (
                        tuple(edit_info.inputs)
                        + tuple(edit_info.additional_inputs)
                ):
                    if dependency.relative_name:
                        input_paths.append(str(dependency.relative_name))

        for control in resource.get_blocks(KVBlock, "CTRL"):
            ResourceRegistry._collect_control_tokens(control, control_tokens)

        data = resource.get_block_bytes(block_name="DATA") or b""
        return _Evidence(
            extension=extension,
            compiler_identifiers=tuple(dict.fromkeys(compiler_identifiers)),
            metadata_strings=tuple(dict.fromkeys(metadata_strings)),
            input_paths=tuple(dict.fromkeys(input_paths)),
            ntro_structs=tuple(dict.fromkeys(ntro_structs)),
            control_tokens=frozenset(control_tokens),
            data_prefix=data[:32],
            diagnostics=[],
        )

    @staticmethod
    def _collect_control_tokens(
            value: object,
            output: set[str],
            *,
            depth: int = 0,
    ):
        if depth > 8 or len(output) >= 4096:
            return
        if isinstance(value, str):
            if value:
                output.add(value.casefold())
            return
        if isinstance(value, dict):
            for key, child in value.items():
                ResourceRegistry._collect_control_tokens(key, output, depth=depth + 1)
                ResourceRegistry._collect_control_tokens(child, output, depth=depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                ResourceRegistry._collect_control_tokens(child, output, depth=depth + 1)

    @staticmethod
    def _registration_signals(
            registration: ResourceRegistration,
            evidence: _Evidence,
    ) -> list[tuple[str, int]]:
        signals: list[tuple[str, int]] = []
        if evidence.extension in registration.extensions:
            signals.append((f"path extension {evidence.extension}", 35))

        compiler_values = tuple(
            value.casefold()
            for value in (
                evidence.compiler_identifiers + evidence.metadata_strings
            )
        )
        for pattern in registration.compiler_identifiers:
            if any(pattern in value for value in compiler_values):
                signals.append((f"registered compiler pattern {pattern!r}", 100))

        for input_path in evidence.input_paths:
            input_extension = TinyPath(input_path).suffix.lower()
            if input_extension in registration.input_extensions:
                signals.append((
                    f"registered input extension {input_extension}",
                    80,
                ))

        for struct_name in evidence.ntro_structs:
            if struct_name in registration.ntro_structs:
                signals.append((f"registered NTRO structure {struct_name}", 90))

        for signature in registration.control_signatures:
            if any(
                    signature in token
                    for token in evidence.control_tokens
            ):
                signals.append((f"registered CTRL signature {signature!r}", 60))

        for magic in registration.data_magics:
            if evidence.data_prefix.startswith(magic):
                signals.append((f"registered DATA magic {magic.hex()}", 95))
        return signals

    def _select_registration(
            self,
            identity: ResourceIdentity,
            evidence: _Evidence,
    ) -> ResourceRegistration | None:
        if identity.kind is ResourceKind.UNKNOWN:
            return None
        registrations = [
            registration
            for registration in self._registrations
            if registration.kind is identity.kind
        ]
        version_matches = [
            registration
            for registration in registrations
            if registration.supports_version(identity.resource_version)
        ]
        if registrations and not version_matches:
            supported: list[int] = []
            for registration in registrations:
                if registration.supported_versions is not None:
                    supported.extend(registration.supported_versions)
            raise UnsupportedResourceVersionError(
                identity.resource_version,
                kind=identity.kind.value,
                supported=tuple(sorted(set(supported))) or None,
                path=identity.path,
                offset=6,
            )
        if not version_matches:
            return None

        ranked = sorted(
            (
                (
                    sum(
                        weight
                        for _, weight in self._registration_signals(
                            registration,
                            evidence,
                        )
                    ),
                    registration.priority,
                    registration,
                )
                for registration in version_matches
            ),
            key=lambda item: (-item[0], -item[1], item[2].resource_type.__name__),
        )
        if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
            raise ResourceRegistrationError(
                f"Ambiguous {identity.kind.value} parsers for resource version "
                f"{identity.resource_version}: "
                f"{ranked[0][2].resource_type.__name__}, "
                f"{ranked[1][2].resource_type.__name__}"
            )
        return ranked[0][2]

    def _register_builtin_resources(self):
        from .animation import register_animation_resources
        from .resource_types.compiled_manifest_resource import CompiledManifestResource
        from .resource_types.compiled_material_resource import CompiledMaterialResource
        from .resource_types.compiled_mesh_resource import CompiledMeshResource
        from .resource_types.compiled_model_resource import CompiledModelResource
        from .resource_types.compiled_particle_resource import register_particle_resource
        from .resource_types.compiled_physics_resource import CompiledPhysicsResource
        from .resource_types.compiled_sound_resource import register_sound_resource
        from .resource_types.compiled_texture_resource import CompiledTextureResource
        from .resource_types.compiled_vmorf_resource import CompiledMorphResource
        from .resource_types.compiled_world_resource import (
            CompiledEntityLumpResource,
            CompiledMapResource,
            CompiledWorldNodeResource,
            CompiledWorldResource,
        )

        extract_render = ResourceCapabilities(
            read=Maturity.STABLE,
            extract=Maturity.STABLE,
            render=Maturity.PARTIAL,
        )
        extract_partial = ResourceCapabilities(
            read=Maturity.STABLE,
            extract=Maturity.PARTIAL,
        )
        registrations = (
            (
                ResourceKind.MODEL,
                CompiledModelResource,
                (".vmdl_c",),
                extract_render,
                ("PermModelData_t",),
            ),
            (
                ResourceKind.MESH,
                CompiledMeshResource,
                (".vmesh_c",),
                extract_render,
                (),
            ),
            (
                ResourceKind.MATERIAL,
                CompiledMaterialResource,
                (".vmat_c",),
                extract_render,
                ("MaterialResourceData_t",),
            ),
            (
                ResourceKind.TEXTURE,
                CompiledTextureResource,
                (".vtex_c",),
                ResourceCapabilities(
                    read=Maturity.STABLE,
                    extract=Maturity.STABLE,
                    render=Maturity.STABLE,
                ),
                (),
            ),
            (
                ResourceKind.PHYSICS,
                CompiledPhysicsResource,
                (".vphys_c",),
                extract_partial,
                ("VPhysXAggregateData_t",),
            ),
            (
                ResourceKind.MORPH,
                CompiledMorphResource,
                (".vmorf_c",),
                extract_partial,
                ("MorphSetData_t",),
            ),
            (
                ResourceKind.WORLD,
                CompiledWorldResource,
                (".vwrld_c",),
                extract_partial,
                ("World_t",),
            ),
            (
                ResourceKind.WORLD_NODE,
                CompiledWorldNodeResource,
                (".vwnod_c",),
                extract_partial,
                ("WorldNode_t",),
            ),
            (
                ResourceKind.MAP,
                CompiledMapResource,
                (".vmap_c",),
                extract_partial,
                (),
            ),
            (
                ResourceKind.ENTITY_LUMP,
                CompiledEntityLumpResource,
                (".vents_c",),
                extract_partial,
                ("EntityLump_t",),
            ),
            (
                ResourceKind.RESOURCE_MANIFEST,
                CompiledManifestResource,
                (".vrman_c", ".vmanifest_c"),
                ResourceCapabilities(
                    read=Maturity.STABLE,
                    extract=Maturity.STABLE,
                    write=Maturity.EXPERIMENTAL,
                ),
                ("ResourceManifest_t",),
            ),
        )
        for kind, resource_type, extensions, capabilities, ntro_structs in registrations:
            self.register(
                kind,
                resource_type,
                extensions=extensions,
                capabilities=capabilities,
                ntro_structs=ntro_structs,
            )
        register_animation_resources(self)
        register_particle_resource(self)
        register_sound_resource(self)


default_registry = ResourceRegistry()
resource_registry = default_registry

__all__ = [
    "ResourceRegistration",
    "ResourceRegistry",
    "default_registry",
    "resource_registry",
]
