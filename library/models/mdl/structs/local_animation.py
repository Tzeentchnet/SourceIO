from collections import defaultdict
from dataclasses import dataclass
from enum import IntFlag

import numpy as np
import numpy.typing as npt

from SourceIO.library.shared.vector_types import Vector4, Vector3
from SourceIO.library.utils.math_utilities import euler_to_quat
from .bone import Bone
from .compressed_vectors import Quat64, Quat48, Quat48S, decode_quat48, decode_quat48s
from .frame_anim import StudioFrameAnim
from SourceIO.library.utils import Buffer

ANIM_DTYPE = np.dtype([
    ("pos", np.float32, (3,)),
    ("rot", np.float32, (4,))
])


class AnimDescFlags(IntFlag):
    LOOPING = 0x0001
    SNAP = 0x0002
    DELTA = 0x0004
    AUTOPLAY = 0x0008
    POST = 0x0010
    ALLZEROS = 0x0020
    FRAMEANIM = 0x0040
    CYCLEPOSE = 0x0080
    REALTIME = 0x0100
    LOCAL = 0x0200
    HIDDEN = 0x0400
    OVERRIDE = 0x0800
    ACTIVITY = 0x1000
    EVENT = 0x2000
    WORLD = 0x4000
    NOFORCELOOP = 0x8000
    EVENT_CLIENT = 0x10000


class AnimBoneFlags(IntFlag):
    RAW_POS = 0x01
    RAW_ROT = 0x02
    ANIM_POS = 0x04
    ANIM_ROT = 0x08
    ANIM_DELTA = 0x10
    ANIM_RAW_ROT2 = 0x20


class AniBoneFlags(IntFlag):
    RAW_POS = 0x1
    RAW_ROT = 0x2
    ANIM_POS = 0x4
    ANIM_ROT = 0x8
    FULL_ANIM_POS = 0x10
    CONST_POS2 = 0x20
    CONST_ROT2 = 0x40
    ANIM_ROT2 = 0x80


@dataclass
class StudioAnimationSection:
    anim_block: int
    anim_offset: int

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        return cls(*buffer.read_fmt("2I"))


def quat_mult(q1, q2):
    """Multiply two quaternions."""
    w1, x1, y1, z1 = q1.T
    w2, x2, y2, z2 = q2

    w = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    x = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    y = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    z = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2

    return np.vstack((w, x, y, z)).T


@dataclass(slots=True)
class StudioAnimDesc:
    _entry_offset: int
    base_prt: int
    name: str
    fps: float
    flags: AnimDescFlags
    frame_count: int

    movement_count: int
    movement_offset: int

    ikrule_zero_frame_offset: int

    animblock_id: int
    animblock_offset: int

    ikrule_count: int
    ikrule_offset: int
    animblock_ikrule_offset: int

    local_hierarchy_count: int
    local_hierarchy_offset: int

    section_offset: int
    section_frame_count: int

    zero_frame_span: int
    zero_frame_count: int
    zero_frame_offset: int
    stall_time: int

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        entry_offset = buffer.tell()
        (base_prt, name_offset, fps, flags, frame_count, movement_count, movement_offset, ikrule_zero_frame_offset,
         *unused1,
         animblock_id, animblock_offset, ikrule_count, ikrule_offset, animblock_ikrule_offset, local_hierarchy_count,
         local_hierarchy_offset, section_offset, section_frame_count, zero_frame_span, zero_frame_count,
         zero_frame_offset, stall_time) = buffer.read_fmt("iIf4I15I2HIf")
        with buffer.read_from_offset(entry_offset + name_offset):
            name = buffer.read_ascii_string()
        return cls(entry_offset, base_prt, name, fps, AnimDescFlags(flags), frame_count, movement_count,
                   movement_offset,
                   ikrule_zero_frame_offset,
                   animblock_id, animblock_offset, ikrule_count, ikrule_offset, animblock_ikrule_offset,
                   local_hierarchy_count,
                   local_hierarchy_offset, section_offset, section_frame_count, zero_frame_span, zero_frame_count,
                   zero_frame_offset, stall_time)

    def get_sections(self, buffer: Buffer) -> list[StudioAnimationSection]:
        section = []
        if self.section_offset != 0 and self.section_frame_count > 0:
            section_count = (self.frame_count // self.section_frame_count) + 2
            buffer.seek(self._entry_offset + self.section_offset)
            for section_id in range(section_count):
                section.append(StudioAnimationSection.from_buffer(buffer))
        return section

    def read_animations(self, buffer: Buffer, bones: list[Bone],
                        ani_buffer: Buffer | None = None,
                        block_table: list | None = None) -> dict[str, npt.NDArray] | None:
        """
        Read animation frames.

        Args:
            buffer: MDL file buffer (for inline data and section table)
            bones: bone list for frame allocation
            ani_buffer: opened .ani file buffer (for external blocks)
            block_table: list of AnimBlockEntry from the MDL header
        """
        frames_per_section = self.section_frame_count
        sections = self.get_sections(buffer)

        if sections:
            frame_buffer = defaultdict(lambda: np.zeros((self.frame_count,), ANIM_DTYPE))
            written = defaultdict(lambda: np.zeros((self.frame_count,), bool))
            frame_offset = 0
            for section_id, section in enumerate(sections):
                if section_id < len(sections) - 2:
                    section_frame_count = frames_per_section
                else:
                    section_frame_count = self.frame_count - (len(sections) - 2) * frames_per_section

                if frame_offset >= self.frame_count:
                    break

                if section.anim_block == 0:
                    adjusted_anim_offset = section.anim_offset + (self.animblock_offset - sections[0].anim_offset)
                    section_buffer = buffer
                    section_buffer.seek(self._entry_offset + adjusted_anim_offset)
                elif ani_buffer is not None and block_table is not None:
                    section_buffer = ani_buffer
                    section_buffer.seek(block_table[section.anim_block].data_offset + section.anim_offset)
                elif ani_buffer is not None:
                    section_buffer = ani_buffer
                    section_buffer.seek(section.anim_offset)
                else:
                    section_buffer = None

                # A section that animates no bones still covers its frames.
                if section_buffer is not None:
                    animation_section = self._read_animation_frames(section_buffer, bones, section_frame_count)
                    for key, data in (animation_section or {}).items():
                        frame_buffer[key][frame_offset:frame_offset + section_frame_count] = data
                        written[key][frame_offset:frame_offset + section_frame_count] = True
                frame_offset += section_frame_count

            # Each section lists only the bones it animates; elsewhere a bone holds its rest pose
            # (identity in a delta animation), as in the engine.
            is_delta = bool(self.flags & AnimDescFlags.DELTA)
            bones_by_name = {bone.name: bone for bone in bones}
            for name, track in frame_buffer.items():
                missing = ~written[name]
                if missing.any():
                    bone = bones_by_name[name]
                    track["pos"][missing] = (0, 0, 0) if is_delta else bone.position
                    track["rot"][missing] = (0, 0, 0, 1) if is_delta else bone.quat
            return frame_buffer
        elif self.animblock_id == 0:
            buffer.seek(self._entry_offset + self.animblock_offset)
            return self._read_animation_frames(buffer, bones, self.frame_count)
        elif ani_buffer is not None and block_table is not None:
            block_entry = block_table[self.animblock_id]
            ani_buffer.seek(block_entry.data_offset + self.animblock_offset)
            return self._read_animation_frames(ani_buffer, bones, self.frame_count)
        elif ani_buffer is not None:
            ani_buffer.seek(self.animblock_offset)
            return self._read_animation_frames(ani_buffer, bones, self.frame_count)
        return None

    def _read_animation_frames(self, buffer: Buffer, bones: list[Bone], section_frame_count: int) \
            -> dict[str, npt.NDArray] | None:
        if self.flags & AnimDescFlags.FRAMEANIM:
            return self._read_frame_animations(buffer, bones, section_frame_count)
        else:
            return self._read_mdl_animations(buffer, bones, section_frame_count)

    def _read_frame_animations(self, buffer: Buffer, bones: list[Bone], section_frame_count: int) \
            -> dict[str, npt.NDArray] | None:
        """Decode one STUDIO_FRAMEANIM block (v49+): a flag byte per bone, then a constants block
        with one value for the whole section and a per-frame block with a fixed stride.

        A bone can mix the two (constant rotation, animated position), and a section usually
        mixes bones of both kinds. Each bone stores its rotation, then its position. A bone with
        no flag for a channel holds its rest pose (identity in a delta animation).
        """
        entry_offset = buffer.tell()
        frame_anim = StudioFrameAnim.from_buffer(buffer)
        bone_flags = [AniBoneFlags(buffer.read_uint8()) for _ in bones]
        is_delta = bool(self.flags & AnimDescFlags.DELTA)

        anim_data: dict[str, npt.NDArray] = {}

        def track(bone: Bone) -> npt.NDArray:
            if bone.name not in anim_data:
                data = np.zeros((section_frame_count,), ANIM_DTYPE)
                data["pos"] = (0, 0, 0) if is_delta else bone.position
                data["rot"] = (0, 0, 0, 1) if is_delta else bone.quat
                anim_data[bone.name] = data
            return anim_data[bone.name]

        constant_flags = AniBoneFlags.CONST_ROT2 | AniBoneFlags.RAW_ROT | AniBoneFlags.RAW_POS | AniBoneFlags.CONST_POS2
        if frame_anim.constant_offset != 0 and any(flag & constant_flags for flag in bone_flags):
            buffer.seek(entry_offset + frame_anim.constant_offset)
            for bone in bones:
                flag = bone_flags[bone.bone_id]
                if not flag & constant_flags:
                    continue
                data = track(bone)
                if flag & AniBoneFlags.CONST_ROT2:
                    data["rot"] = Quat48S.read(buffer)
                elif flag & AniBoneFlags.RAW_ROT:
                    data["rot"] = Quat48.read(buffer)
                if flag & AniBoneFlags.CONST_POS2:
                    data["pos"] = buffer.read_fmt("3f")
                elif flag & AniBoneFlags.RAW_POS:
                    data["pos"] = buffer.read_fmt("3e")

        # Per-frame values: byte offsets within one frame, in bone order.
        frame_fields = []
        stride = 0
        for bone in bones:
            flag = bone_flags[bone.bone_id]
            if flag & AniBoneFlags.ANIM_ROT2:
                frame_fields.append((bone, "rot", decode_quat48s, stride))
                stride += 6
            elif flag & AniBoneFlags.ANIM_ROT:
                frame_fields.append((bone, "rot", decode_quat48, stride))
                stride += 6
            if flag & AniBoneFlags.FULL_ANIM_POS:
                frame_fields.append((bone, "pos", "<f4", stride))
                stride += 12
            elif flag & AniBoneFlags.ANIM_POS:
                frame_fields.append((bone, "pos", "<f2", stride))
                stride += 6

        if frame_fields:
            if frame_anim.frame_offset == 0 or frame_anim.frame_length < stride:
                raise ValueError(f"Frame animation {self.name!r}: bone flags need {stride} bytes per frame, "
                                 f"block has {frame_anim.frame_length} at offset {frame_anim.frame_offset}")
            buffer.seek(entry_offset + frame_anim.frame_offset)
            # Sections other than the last store one more frame (the next section's first); it is not needed.
            frames = np.frombuffer(buffer.read(frame_anim.frame_length * section_frame_count), np.uint8)
            frames = frames.reshape(section_frame_count, frame_anim.frame_length)
            for bone, channel, decoder, offset in frame_fields:
                size = 12 if decoder == "<f4" else 6
                raw = np.ascontiguousarray(frames[:, offset:offset + size])
                if channel == "rot":
                    values = decoder(raw.view("<u2"))
                else:
                    values = raw.view(decoder).astype(np.float32)
                track(bone)[channel] = values

        # Empty when every bone holds its rest pose
        return anim_data

    def _read_anim_rot_value(self, buffer: Buffer, flags: AnimBoneFlags, frame_count: int, base_quat: Vector4,
                             base_rot: Vector3, rot_scale: Vector3) -> list[Vector4]:
        if flags & AnimBoneFlags.RAW_ROT:
            return [Quat48.read(buffer)] * frame_count
        if flags & AnimBoneFlags.ANIM_RAW_ROT2:
            return [Quat64.read(buffer)] * frame_count
        if not flags & AnimBoneFlags.ANIM_ROT:
            if flags & AnimBoneFlags.ANIM_DELTA:
                return [(0, 0, 0, 1)] * frame_count
            return [base_quat] * frame_count

        if flags & AnimBoneFlags.ANIM_ROT:
            entry = buffer.tell()
            x_offset = buffer.read_int16()
            y_offset = buffer.read_int16()
            z_offset = buffer.read_int16()
            anim_rot = np.zeros((frame_count, 3), dtype=np.float32)

            if x_offset > 0:
                with buffer.read_from_offset(entry + x_offset):
                    anim_rot[:, 0] = self._read_mdl_anim_values(buffer, frame_count, rot_scale[0])

            if y_offset > 0:
                with buffer.read_from_offset(entry + y_offset):
                    anim_rot[:, 1] = self._read_mdl_anim_values(buffer, frame_count, rot_scale[1])

            if z_offset > 0:
                with buffer.read_from_offset(entry + z_offset):
                    anim_rot[:, 2] = self._read_mdl_anim_values(buffer, frame_count, rot_scale[2])

            if not flags & AnimBoneFlags.ANIM_DELTA:
                anim_rot = anim_rot + base_rot

            return euler_to_quat(anim_rot)
        return None

    def _read_anim_pos_value(self, buffer: Buffer, flags: AnimBoneFlags, frame_count: int, base_pos: Vector3,
                             pos_scale: Vector3) -> list[Vector3]:
        if flags & AnimBoneFlags.RAW_POS:
            return [buffer.read_fmt("3e")] * frame_count
        elif not flags & AnimBoneFlags.ANIM_POS:
            if flags & AnimBoneFlags.ANIM_DELTA:
                return [(0.0, 0.0, 0.0)] * frame_count
            return [base_pos] * frame_count

        if flags & AnimBoneFlags.ANIM_POS:
            entry = buffer.tell()
            x_offset = buffer.read_int16()
            y_offset = buffer.read_int16()
            z_offset = buffer.read_int16()
            anim_pos = np.zeros((frame_count, 3), dtype=np.float32)
            if x_offset > 0:
                with buffer.read_from_offset(entry + x_offset):
                    anim_pos[:, 0] = self._read_mdl_anim_values(buffer, frame_count, pos_scale[0])

            if y_offset > 0:
                with buffer.read_from_offset(entry + y_offset):
                    anim_pos[:, 1] = self._read_mdl_anim_values(buffer, frame_count, pos_scale[1])

            if z_offset > 0:
                with buffer.read_from_offset(entry + z_offset):
                    anim_pos[:, 2] = self._read_mdl_anim_values(buffer, frame_count, pos_scale[2])
            if not flags & AnimBoneFlags.ANIM_DELTA:
                anim_pos = anim_pos + base_pos
            return anim_pos

    def _read_mdl_animations(self, buffer: Buffer, bones: list[Bone], section_frame_count: int):
        animation_sections = []

        anim_data = defaultdict(lambda: np.zeros((section_frame_count,), ANIM_DTYPE))
        for _ in bones:
            bone_entry = buffer.tell()
            bone_index = buffer.read_uint8()

            if bone_index == 255:
                buffer.skip(3)
                break

            assert bone_index < len(bones)
            used_bone: Bone = bones[bone_index]

            flags = AnimBoneFlags(buffer.read_uint8())

            next_offset = buffer.read_int16()
            animation_sections.append((bone_index, flags, next_offset))
            assert flags & 0x40 == 0
            assert flags & 0x80 == 0

            value = self._read_anim_rot_value(buffer, flags, section_frame_count, used_bone.quat, used_bone.rotation,
                                              used_bone.rotation_scale)
            anim_data[used_bone.name]["rot"] = value

            value = self._read_anim_pos_value(buffer, flags, section_frame_count, used_bone.position,
                                              used_bone.position_scale)
            anim_data[used_bone.name]["pos"] = value

            if next_offset > 0:
                buffer.seek(bone_entry + next_offset)
                continue
            break

        return anim_data

    def _read_rle_compressed_data(self, buffer: Buffer, frame_count: int):
        valid, total = buffer.read_fmt("2B")
        frame_offset = 0
        all_shorts = np.zeros(frame_count + 1, np.int16)
        while frame_offset < frame_count:
            if valid > 0:
                all_shorts[frame_offset:frame_offset + valid] = buffer.read_fmt(f"{valid}h")
                frame_offset += valid
            if total - valid > 0:
                repeat_frames = total - valid
                all_shorts[frame_offset:frame_offset + repeat_frames] = all_shorts[frame_offset - 1]
                frame_offset += repeat_frames
            valid, total = buffer.read_fmt("2B")
        return all_shorts[:-1]

    def _read_mdl_anim_values(self, buffer: Buffer, frame_count: int, scale: float):
        values = self._read_rle_compressed_data(buffer, frame_count)
        return values.astype(np.float32) * scale
