from .kv3_block import KVBlock


class AseqBlock(KVBlock):
    @staticmethod
    def _struct_name():
        return 'SequenceGroupResourceData_t'

    @property
    def sequence_names(self) -> tuple[str, ...]:
        return tuple(str(name) for name in self.get('m_localSequenceNameArray', []) or [])

    @property
    def sequence_descriptors(self) -> tuple:
        return tuple(self.get('m_localS1SeqDescArray', []) or [])
