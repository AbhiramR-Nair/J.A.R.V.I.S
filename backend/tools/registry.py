"""Tool registry for LLM function calling.

Every assistant capability (web search, PDF summary, app launch, etc.) is a tool
the LLM can invoke. This module stores those tools, validates them at registration
time, translates them into the shape each LLM provider expects, and dispatches
LLM-driven calls to the correct handler.

Design decisions: see docs/plans/day_20_plan.md §Q1–Q8.
Key choices:
  - Provider-neutral JSON Schema stored in the registry (Q5)
  - gemini_function_schemas() translates on demand (Q5)
  - Hard vs soft error split in execute() (Q7)
  - All handlers are async (Q3)
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Tool errors
# ---------------------------------------------------------------------------

class ToolError(Exception):
    """Base class for tool-related errors."""


class ToolNotFoundError(ToolError):
    """LLM called a tool name that is not present in the registry.

    This is a hard error (Q7): the LLM hallucinated a tool name. Re-raised by
    execute() so the orchestrator can route to _handle_error and ERROR state.
    """


class ToolSchemaError(ToolError):
    """Arguments passed to a tool did not match its registered schema.

    Also a hard error (Q7): the LLM supplied args with wrong types or missing
    required fields. Re-raised by execute() for the same reason as above.
    """


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------

class ToolSchema(BaseModel):
    """Single tool registration entry.

    Fields:
        name:        Unique identifier. Must be a valid Python identifier and
                     match FunctionDeclaration.name constraints (a-z, A-Z, 0-9,
                     underscores, dots, colons, dashes; max 128 chars).
        description: Plain-English purpose and invocation guidance. The LLM reads
                     this to decide when to call the tool — write it like a
                     docstring for the LLM, not for the developer.
        parameters:  Provider-neutral JSON Schema dict describing the tool's
                     arguments. Must be an "object" schema with a "properties"
                     key. No $ref, $defs, allOf, oneOf, anyOf at the top level
                     (Gemini rejects these). Validated at registration time.
        handler:     The async function that executes the tool. Receives **args
                     from the LLM's function_call; must return a JSON-serialisable
                     value (str, dict, list, int, float, bool, None).
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]

    # Pydantic needs this to allow non-serialisable types like Callable.
    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Stores tool schemas and dispatches LLM-driven calls to handlers.

    Registration model (Q6):
        Tool modules call @registry.register(...) as a decorator, which stores the
        handler in self._tools keyed by name. The FastAPI lifespan imports each
        tool module explicitly, triggering the decorators at startup.

    Execution semantics (Q7):
        execute() distinguishes hard errors (re-raised, route to ERROR state) from
        soft errors (wrapped in {"error": ...} dict, fed back to the LLM as a
        tool result so the LLM can apologise or try a different approach).

        Hard errors: ToolNotFoundError, ToolSchemaError
        Soft errors: any other exception from the handler

    Provider neutrality (Q5):
        self._tools stores provider-neutral OpenAPI-style JSON Schema.
        gemini_function_schemas() translates to list[types.Tool] on demand.
        An openai_tool_schemas() translator can be added later without changing
        how tools are registered.
    """

    def __init__(self) -> None:
        # Dict keyed by tool name for O(1) lookup in execute() and __contains__.
        # Insertion order is preserved (Python 3.7+) so gemini_function_schemas()
        # returns tools in registration order — stable across calls, no surprises.
        self._tools: dict[str, ToolSchema] = {}

    # B-1 — Schema validation
    def _validate_schema(self, name: str, schema: dict[str, Any]) -> None:
        """Assert that `schema` follows the subset of JSON Schema Gemini accepts.

        Gemini rejects schemas that use $ref, $defs, allOf, oneOf, or anyOf at
        the top level. Pydantic's auto-generated schemas emit these freely, which
        is why Q4 mandates hand-written schemas. This method catches the mistake
        at registration time so the error surfaces as an import error, not as a
        cryptic Gemini API 400 during the first voice turn.

        Args:
            name:   Tool name (for the error message).
            schema: The parameters dict to validate.

        Raises:
            ToolSchemaError: if any disallowed key is present at the top level,
                             or if type != "object", or if "properties" is absent.
        """
        # These keys cause Gemini to return a 400 INVALID_ARGUMENT at call time.
        # Catch them here so the error is clear and immediate.
        forbidden = {"$ref", "$defs", "allOf", "oneOf", "anyOf"}
        found = forbidden & schema.keys()
        if found:
            raise ToolSchemaError(
                f"tool '{name}' has forbidden top-level keys {found}; "
                f"Gemini's parameters_json_schema does not accept these. "
                f"Write the schema by hand instead of using Pydantic's .model_json_schema()."
            )
        if schema.get("type") != "object":
            raise ToolSchemaError(
                f"tool '{name}' schema must have type='object' at the top level "
                f"(got {schema.get('type')!r})."
            )
        if "properties" not in schema:
            raise ToolSchemaError(
                f"tool '{name}' schema must have a 'properties' key. "
                f"For a tool with no arguments use: properties={{}}, required=[]."
            )

    # B-2 — Decorator factory
    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Decorator factory. Register `name` against the decorated async handler.

        Usage:
            @registry.register(
                name="web_search",
                description="Search the web for current information.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"]
                }
            )
            async def web_search(query: str) -> str:
                ...

        Args:
            name:        Tool identifier. Collision with an existing name raises ToolError.
            description: Plain-English purpose. Written for the LLM, not the developer.
            parameters:  JSON Schema dict (Q4). Validated by _validate_schema() before
                         storage — bad schemas fail at import time, not at the first call.

        Returns:
            The decorated function, unchanged. The decorator is side-effect-only —
            it registers the tool and returns the original callable so the module
            can still call the handler directly in tests if needed.

        Raises:
            ToolError:       if `name` is already registered (duplicate).
            ToolSchemaError: if `parameters` fails schema validation.
            ToolError:       if the handler is not an async function (Q3 enforcement).
        """
        # Validate schema before doing anything else — fail at decoration time.
        self._validate_schema(name, parameters)

        # Duplicate names mean a programming mistake (two modules registering
        # the same tool). Fail loud rather than silently overwriting.
        if name in self._tools:
            raise ToolError(f"tool '{name}' is already registered; each name must be unique.")

        def decorator(
            handler: Callable[..., Awaitable[Any]],
        ) -> Callable[..., Awaitable[Any]]:
            # Q3: all handlers must be async so they run safely in the FastAPI
            # event loop alongside the voice pipeline. Sync handlers would block.
            if not asyncio.iscoroutinefunction(handler):
                raise ToolError(
                    f"tool '{name}' handler must be an async function "
                    f"(got {handler!r}). Wrap sync work in asyncio.to_thread()."
                )
            self._tools[name] = ToolSchema(
                name=name,
                description=description,
                parameters=parameters,
                handler=handler,
            )
            logger.info(f"registered tool: {name}")
            # Return the original handler unchanged so it stays callable directly.
            return handler

        return decorator

    # B-3 — Gemini schema translation
    def gemini_function_schemas(self) -> list[Any]:
        """Translate registered tools into a list[types.Tool] for Gemini.

        Builds one types.FunctionDeclaration per registered tool using
        parameters_json_schema= (NOT parameters=) — the raw dict passes through
        without needing conversion to types.Schema objects (Day 19 B-1 finding).

        Returns:
            list containing a single types.Tool with all declarations, in
            registration order. Empty list if no tools are registered.

        Note:
            Imports google.genai.types here (not at module top) so registry.py
            remains importable in test environments that don't have the SDK installed.
        """
        if not self._tools:
            return []

        # Lazy import — keeps this module importable without the SDK present.
        from google.genai import types  # noqa: PLC0415

        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters_json_schema=t.parameters,  # NOT parameters= (see Day 19 B-1)
            )
            for t in self._tools.values()
        ]
        # Single Tool wrapping all declarations — the recommended SDK pattern.
        return [types.Tool(function_declarations=declarations)]

    # B-4 — Execution with hard/soft error split
    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch `name` to its handler with `args`.

        Hard errors (re-raised — orchestrator routes to ERROR state):
            ToolNotFoundError: `name` is not in the registry.
            ToolSchemaError:   handler raised TypeError (wrong/missing args from LLM).

        Soft errors (caught — returned as {"error": ..., "type": ...} dict):
            Any other exception from the handler. The LLM receives this dict as
            the tool result and can apologise or try a different approach.

        Args:
            name: Tool name as returned by the LLM's function_call.name.
            args: Arguments dict as returned by the LLM's function_call.args.

        Returns:
            Whatever the handler returns on success, or
            {"error": str(exc), "type": exc.__class__.__name__} on soft failure.

        Raises:
            ToolNotFoundError: if `name` is not registered.
            ToolSchemaError:   if the handler raises TypeError (args mismatch).
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"unknown tool: '{name}'. Registered: {list(self._tools)}")

        tool = self._tools[name]
        try:
            return await tool.handler(**args)
        except (ToolNotFoundError, ToolSchemaError):
            raise  # hard errors propagate unchanged to the orchestrator
        except TypeError as exc:
            # LLM passed wrong/missing args — structurally the same as a schema
            # mismatch. Hard error: a retry with the same args won't fix it.
            raise ToolSchemaError(f"args mismatch for '{name}': {exc}") from exc
        except Exception as exc:
            # Soft error: network timeout, file not found, API failure, etc.
            # Return as a dict so the LLM can respond gracefully ("I wasn't able to…").
            logger.warning(f"tool '{name}' soft error: {exc.__class__.__name__}: {exc}")
            return {"error": str(exc), "type": exc.__class__.__name__}

    # B-5 — Dunder helpers
    def __contains__(self, name: str) -> bool:
        """Allow `if name in registry:` checks."""
        return name in self._tools

    def __len__(self) -> int:
        """Number of registered tools. Used in startup log and smoke tests."""
        return len(self._tools)
