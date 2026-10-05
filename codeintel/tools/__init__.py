from codeintel.tools.branches import inspect_function_branches
from codeintel.tools.dependencies import get_external_dependencies
from codeintel.tools.paths import trace_execution_path
from codeintel.tools.source import get_function_source
from codeintel.tools.scope import list_scope_functions

__all__ = [
    "inspect_function_branches",
    "get_external_dependencies",
    "trace_execution_path",
    "get_function_source",
    "list_scope_functions",
]
