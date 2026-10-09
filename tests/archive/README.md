# Archived tests

Unit tests that an end-to-end runner in `tests/e2e/` already covers: if what they check broke, an E2E run would fail too. They are kept for reference and still pass, but a plain `pytest SourceIO/tests` leaves them out (`tests/conftest.py`). Run them by naming the folder or a file:

```
cd D:/Github && "<blender>/5.2/python/bin/python.exe" -m pytest SourceIO/tests/archive -q -p no:cacheprovider   # 2 pass
```

The E2E runners fail only when an import raises, logs an error, creates nothing, or decodes a texture's top mip differently from SourceIO's own decoder, or when the renamed package can't import or register. A test that checks values those runs don't compare (counts, transforms, node wiring, parser edge cases) or code that no sample reaches stays in the main suite.

| Test | Covered by |
|------|------------|
| `kv1/test_kv1_r.py`: CS2's gameinfo `SearchPaths`, duplicate keys in order, `[$MOBILE]` conditions | Every CS2 run of `e2e/run_game_imports.py` mounts the game through its real `gameinfo.gi`; `kv1/test_kv1_parser.py::test_duplicate_keys_preserved_in_order` covers the duplicate keys. |
| `test_relative_imports.py`: no shipped module imports `SourceIO` absolutely | `e2e/run_renamed_smoke.py` blocks the `SourceIO` package with an import hook and imports every shipped module under another name. |
