class AssembleError(RuntimeError):
    """The inputs don't produce a valid USB tree (layout drift, conflicts, unsafe paths, bad options)."""
