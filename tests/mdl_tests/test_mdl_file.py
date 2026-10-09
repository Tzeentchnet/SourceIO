"""MDL file reading: animations stay aligned with their descriptions when one fails to decode."""
import os
from pathlib import Path

os.environ['NO_BPY'] = '1'

import pytest

from SourceIO.library.models.mdl.structs.local_animation import StudioAnimDesc
from SourceIO.library.models.mdl.v44.mdl_file import MdlV44
from SourceIO.library.models.mdl.v49.mdl_file import MdlV49
from SourceIO.library.utils import FileBuffer

SAMPLES_DIR = Path(__file__).parent.parent.parent / "samples"


@pytest.mark.parametrize("mdl_class", [MdlV44, MdlV49])
def test_failed_animation_keeps_alignment(monkeypatch, mdl_class):
    path = SAMPLES_DIR / "dog_animations.mdl"
    if not path.exists():
        pytest.skip("dog_animations.mdl not found")

    read_animations = StudioAnimDesc.read_animations
    calls = []

    def failing_read(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 3:
            raise ValueError("bad animation")
        return read_animations(self, *args, **kwargs)

    monkeypatch.setattr(StudioAnimDesc, "read_animations", failing_read)
    mdl = mdl_class.from_buffer(FileBuffer(path))

    assert len(mdl.anim_descs) == 116
    assert len(mdl.animations) == len(mdl.anim_descs)
    assert all(anim is not None for anim in mdl.animations[:2])
    assert all(anim is None for anim in mdl.animations[2:])
