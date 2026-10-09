from .kv3_block import KVBlock


class AgrpBlock(KVBlock):
    @staticmethod
    def _struct_name():
        return 'AnimationGroupResourceData_t'

    @property
    def decode_key(self):
        return self.get('m_decodeKey')

    @property
    def animation_references(self) -> tuple[str, ...]:
        return tuple(
            str(reference)
            for reference in self.get('m_localHAnimArray', []) or []
            if isinstance(reference, str) and reference
        )

    @property
    def user_channel_names(self) -> tuple[str, ...]:
        decode_key = self.decode_key or {}
        names = []
        for user in decode_key.get('m_userArray', []) or []:
            name = user.get('m_name', user.get('name', '')) if hasattr(user, 'get') else user
            if name:
                names.append(str(name))
        return tuple(names)
