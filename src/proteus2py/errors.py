"""Exceptions with messages meant for the model/user (the MCP layer relays their text)."""


class SpecError(ValueError):
    """The circuit specification is invalid."""


class SpiceError(ValueError):
    """The SPICE deck cannot be translated."""
