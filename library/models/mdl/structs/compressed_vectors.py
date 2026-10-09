import math

import numpy as np
import numpy.typing as npt

from ....utils import Buffer


class Quat:
    @staticmethod
    def read(buffer: Buffer):
        raise NotImplementedError('Override me')


class Quat64(Quat):
    @staticmethod
    def read(buffer: Buffer):
        b0 = buffer.read_uint32()
        b1 = buffer.read_uint32()
        xs = b0 & 0x1FFFFF
        ys = (((b1 & 0x03FF) << 11) | (b0 >> 21)) >> 0
        zs = (b1 >> 10) & 0x1FFFFF
        wn = -1 if (b1 & 0x80000000) else 1

        x = (xs - 1048576) * (1 / 1048576.5)
        y = (ys - 1048576) * (1 / 1048576.5)
        z = (zs - 1048576) * (1 / 1048576.5)
        w = wn * math.sqrt(1.0 - x * x - y * y - z * z)
        return x, y, z, w


class Quat48(Quat):
    @staticmethod
    def read(buffer: Buffer):
        raw_x = buffer.read_uint16()
        raw_y = buffer.read_uint16()
        raw_z = buffer.read_uint16()
        x = (raw_x - 32768) * (1 / 32768)
        y = (raw_y - 32768) * (1 / 32768)
        z = ((raw_z & 0x7FFF) - 16384) * (1 / 16384)
        w_neg = (raw_z >> 15) & 0x1
        w = math.sqrt(max(0.0, 1 - x * x - y * y - z * z))
        if w_neg:
            w = -w
        return x, y, z, w


class Quat48S(Quat):
    SCALE48S = 23168.0
    SHIFT48S = 16384

    @staticmethod
    def read(buffer: Buffer):
        quat = [0, 0, 0, 0]
        data = buffer.read_fmt('3H')
        a = data[0] & 0x7FFF
        offset_h = data[0] >> 15
        b = data[1] & 0x7FFF
        offset_l = data[1] >> 15
        c = data[2] & 0x7FFF
        d_neg = data[2] >> 15

        ia = offset_l + offset_h * 2
        ib = (ia + 1) % 4
        ic = (ia + 2) % 4
        id = (ia + 3) % 4

        quat[ia] = (a - Quat48S.SHIFT48S) * (1 / Quat48S.SCALE48S)
        quat[ib] = (b - Quat48S.SHIFT48S) * (1 / Quat48S.SCALE48S)
        quat[ic] = (c - Quat48S.SHIFT48S) * (1 / Quat48S.SCALE48S)
        quat[id] = math.sqrt(max(0.0, 1.0 - quat[ia] * quat[ia] - quat[ib] * quat[ib] - quat[ic] * quat[ic]))
        if d_neg:
            quat[id] = -quat[id]
        return quat


def decode_quat48(raw: npt.NDArray[np.uint16]) -> npt.NDArray[np.float32]:
    """Vectorized Quat48.read: (..., 3) uint16 words to (..., 4) xyzw."""
    raw = raw.astype(np.int32)
    x = (raw[..., 0] - 32768) * (1 / 32768)
    y = (raw[..., 1] - 32768) * (1 / 32768)
    z = ((raw[..., 2] & 0x7FFF) - 16384) * (1 / 16384)
    w = np.sqrt(np.maximum(0.0, 1 - x * x - y * y - z * z))
    w = np.where(raw[..., 2] & 0x8000, -w, w)
    return np.stack((x, y, z, w), axis=-1).astype(np.float32)


def decode_quat48s(raw: npt.NDArray[np.uint16]) -> npt.NDArray[np.float32]:
    """Vectorized Quat48S.read: (..., 3) uint16 words to (..., 4) xyzw.

    The largest component is dropped and rebuilt from unit length; the top bits of the
    first two words say which component the three stored ones start at.
    """
    raw = raw.astype(np.int32)
    offset = ((raw[..., 0] >> 15) << 1) | (raw[..., 1] >> 15)
    abc = ((raw & 0x7FFF) - Quat48S.SHIFT48S) * (1 / Quat48S.SCALE48S)
    d = np.sqrt(np.maximum(0.0, 1.0 - (abc * abc).sum(axis=-1)))
    d = np.where(raw[..., 2] & 0x8000, -d, d)
    values = np.concatenate((abc, d[..., None]), axis=-1)
    indices = (offset[..., None] + np.arange(4)) % 4
    quat = np.empty_like(values)
    np.put_along_axis(quat, indices, values, axis=-1)
    return quat.astype(np.float32)
