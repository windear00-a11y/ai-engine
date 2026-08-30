"""Operation classification for WRITE domain.

Writes are classified into tiers describing their blast radius and
recoverability. The approval gate consults the tier to decide whether a
snapshot is required and how approval is scoped.
"""


class WriteTier:
    T0_DIFF = "t0_diff"              # diff/preview: no mutation
    T1_REVERSIBLE = "t1_reversible"  # bounded reversible single-file edit
    T2_DESTRUCTIVE = "t2_destructive"  # overwrite/delete
    T3_MULTI = "t3_multi"            # multi-file operation

    ALL = (T0_DIFF, T1_REVERSIBLE, T2_DESTRUCTIVE, T3_MULTI)


def classify_write(operation, multi_file=False, exists=False):
    """Map a tool operation name to a WriteTier.

    ``operation`` is the string name of the mutating tool
    (``file.write``, ``file.edit``, ``file.mkdir``, ``file.diff``).
    ``multi_file`` marks a T3 batch (any combination spanning >1 file).
    ``exists`` indicates whether the target currently exists on disk.

    Returns:
        (tier, requires_approval, requires_snapshot)
    """
    if operation == "file.diff":
        return (WriteTier.T0_DIFF, False, False)
    if operation == "file.edit":
        if multi_file:
            return (WriteTier.T3_MULTI, True, True)
        # Editing requires a before-image ONLY when the target already exists
        # (you cannot meaningfully edit a missing file). This preserves the
        # fail-closed stance (approval always required) while making reversible
        # single-file edits rollbackable.
        if exists:
            return (WriteTier.T1_REVERSIBLE, True, True)
        return (WriteTier.T1_REVERSIBLE, True, False)
    if operation in ("file.write",):
        if multi_file:
            return (WriteTier.T3_MULTI, True, True)
        if not exists:
            # Creating a brand-new file: there is no before-state to lose and
            # deleting it fully reverts. Treat as a reversible create.
            return (WriteTier.T1_REVERSIBLE, True, False)
        # Overwriting an existing file loses its prior content -> destructive.
        return (WriteTier.T2_DESTRUCTIVE, True, True)
    if operation == "file.mkdir":
        if multi_file:
            return (WriteTier.T3_MULTI, True, True)
        # mkdir only creates directories (reversible via rm of empty trees).
        return (WriteTier.T1_REVERSIBLE, True, False)
    # Unknown write operation: fail closed (destructive + approval + snapshot).
    return (WriteTier.T2_DESTRUCTIVE, True, True)
