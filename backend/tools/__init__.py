"""Tools package.

The global `registry` singleton is the entry point for all tool registration.
Tool modules import this singleton and use @registry.register(...) to declare
themselves. FastAPI's lifespan explicitly imports each tool module, which runs
the decorators at import time and populates the registry before any request arrives.

See docs/plans/day_20_plan.md §Q6 for the registration model and rationale.
"""
from backend.tools.registry import ToolRegistry

# Global singleton. All tool modules register against this at import time.
# Stored here (not in __main__ or main.py) so every importer gets the same
# object regardless of how the module was loaded. See Day 18 __main__ gotcha.
registry = ToolRegistry()
