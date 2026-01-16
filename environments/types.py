class InvalidArgException(Exception):
    """Raised for invalid arguments supplied to the simulator controller."""


class EnvException(Exception):
    """Raised when the simulator environment fails to respond as expected."""