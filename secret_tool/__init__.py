"""Credential storage backends for Wee Orchestrator.

`secret_tool` was a single module before it became a package, and the move left
this file empty. Anything doing `import secret_tool; secret_tool.FileBackend`
broke, because the names moved one level down into `secret_tool.secret_tool`
without being re-exported here.

Re-exporting restores the original public surface. Both spellings work:

    from secret_tool import FileBackend              # pre-package callers
    from secret_tool.secret_tool import FileBackend  # post-package, e.g. wee_runtime
"""

from secret_tool.secret_tool import (
    FileBackend,
    KeyringBackend,
    PassBackend,
    main,
    parse_args,
)

__all__ = [
    "FileBackend",
    "KeyringBackend",
    "PassBackend",
    "main",
    "parse_args",
]
