class SifaError(Exception):
    pass


class SchemaError(SifaError):
    pass


class LeakageError(SifaError):
    pass


class VectorIndexError(SifaError):
    pass


class NotTrainedError(SifaError):
    pass


class ExperimentError(SifaError):
    pass


class RegistryError(SifaError):
    pass
