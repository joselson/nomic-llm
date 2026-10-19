"""DSL features the engine does not implement yet (SPEC §4.10).

validate_mechanic() rejects mechanics using anything listed here and the compile
prompt lists them as unavailable. Goal for the MVP: all sets empty.
"""
UNSUPPORTED_FX: set[str] = set()
UNSUPPORTED_TRIGGERS: set[str] = set()
UNSUPPORTED_OPS: set[str] = set()
UNSUPPORTED_BEHAVIORS: set[str] = set()
