"""Worker MCP Server — task delegation to Ollama/OpenRouter with budget tracking."""

from __future__ import annotations

from fastmcp import FastMCP

from hive.budget import BudgetTracker
from hive.clients import ClientResponse, OllamaClient, OpenRouterClient
from hive.config import settings


def _format_response(resp: ClientResponse) -> str:
    """Format a model response with metadata footer."""
    cost_str = f"${resp.cost_usd:.4f}" if resp.cost_usd > 0 else "$0.00"
    latency_str = f"{resp.latency_ms / 1000:.1f}s"
    header = (
        f"## Worker Response (model: {resp.model}, {resp.tokens} tokens, {cost_str}, {latency_str})"
    )
    return f"{header}\n\n{resp.text}"


def create_server(
    budget_tracker: BudgetTracker | None = None,
    ollama_client: OllamaClient | None = None,
    openrouter_client: OpenRouterClient | None = None,
) -> FastMCP:
    """Create and configure the Worker MCP server."""
    budget = budget_tracker or BudgetTracker(db_path=settings.db_path)
    ollama = ollama_client or OllamaClient(
        endpoint=settings.ollama_endpoint, model=settings.ollama_model
    )
    openrouter: OpenRouterClient | None = None
    if openrouter_client is not None:
        openrouter = openrouter_client
    elif settings.openrouter_api_key:
        openrouter = OpenRouterClient(
            api_key=settings.openrouter_api_key, default_model=settings.openrouter_model
        )

    mcp = FastMCP("Hive Worker")

    @mcp.tool
    async def delegate_task(
        prompt: str,
        context: str = "",
        model: str = "auto",
        max_tokens: int = 2000,
        max_cost_per_request: float = 0.0,
    ) -> str:
        """Delegate a task to a cheaper model (Ollama or OpenRouter).

        Args:
            prompt: The task description or code to process.
            context: Optional system context for the model.
            model: Routing — 'auto', 'ollama', 'openrouter-free', or a model ID.
            max_tokens: Maximum tokens in the response.
            max_cost_per_request: Max USD to spend on this request. 0 = free models only.
        """
        if model == "ollama":
            return await _try_ollama(prompt, context, max_tokens)
        if model == "openrouter-free":
            return await _try_openrouter_free(prompt, context, max_tokens)
        if model == "openrouter":
            return await _try_openrouter_paid(prompt, context, max_tokens, max_cost_per_request)
        if model != "auto":
            return await _try_openrouter_specific(prompt, context, max_tokens, model)

        # Auto routing: Ollama → OpenRouter free → OpenRouter paid → reject
        errors: list[str] = []

        # Tier 1: Ollama
        if await ollama.is_available():
            try:
                resp = await ollama.generate(prompt, context=context, max_tokens=max_tokens)
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"Ollama: {exc}")
        else:
            errors.append("Ollama: offline")

        # Tier 2: OpenRouter free
        if openrouter is not None:
            try:
                resp = await openrouter.generate(prompt, context=context, max_tokens=max_tokens)
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter free: {exc}")
        else:
            errors.append("OpenRouter: no API key configured")

        # Tier 3: OpenRouter paid (only if max_cost > 0 and budget allows)
        if (
            max_cost_per_request > 0
            and openrouter is not None
            and budget.can_spend(settings.openrouter_budget, max_cost_per_request)
        ):
            try:
                resp = await openrouter.generate(
                    prompt,
                    context=context,
                    model="deepseek/deepseek-chat-v3-0324:free",
                    max_tokens=max_tokens,
                )
                _record(resp)
                return _format_response(resp)
            except (ConnectionError, RuntimeError) as exc:
                errors.append(f"OpenRouter paid: {exc}")

        # All tiers exhausted
        reasons = "; ".join(errors)
        return f"All workers unavailable. [{reasons}]. Claude should handle this task directly."

    @mcp.tool
    async def list_models() -> str:
        """List available models across all providers."""
        lines = ["# Available Models", ""]

        # Ollama
        ollama_status = "online" if await ollama.is_available() else "offline / unavailable"
        lines.append(f"## Ollama ({ollama_status})")
        if "online" in ollama_status:
            lines.append(f"- **{ollama._model}** — local, free, no token limit")
        lines.append("")

        # OpenRouter
        lines.append("## OpenRouter")
        if openrouter is not None:
            try:
                models = await openrouter.list_models()
                for m in models:
                    cost_label = "free" if m.is_free else f"${m.cost_per_million_input:.2f}/M in"
                    lines.append(f"- **{m.id}** — {m.name}, ctx: {m.context_length}, {cost_label}")
            except (ConnectionError, RuntimeError) as exc:
                lines.append(f"- Error fetching models: {exc}")
        else:
            lines.append("- No API key configured")

        return "\n".join(lines)

    @mcp.tool
    async def worker_status() -> str:
        """Show worker health: budget, connectivity, usage stats."""
        stats = budget.month_stats(settings.openrouter_budget)
        ollama_up = await ollama.is_available()

        lines = [
            "# Worker Status",
            "",
            "## Budget",
            f"- Spent this month: ${stats['spent']:.2f}",
            f"- Remaining: ${stats['remaining']:.2f} / ${settings.openrouter_budget:.1f}",
            f"- Requests: {stats['request_count']}",
            "",
            "## Connectivity",
            f"- Ollama: {'online' if ollama_up else 'offline / unavailable'}",
            f"- OpenRouter: {'configured' if openrouter is not None else 'no API key'}",
            "",
        ]

        if stats["by_model"]:
            lines.append("## Top Models")
            for model_name, model_stats in stats["by_model"].items():
                lines.append(
                    f"- **{model_name}**: {model_stats['count']} requests, "
                    f"${model_stats['total_cost']:.4f}, avg {model_stats['avg_latency_ms']}ms"
                )

        return "\n".join(lines)

    def _record(resp: ClientResponse) -> None:
        """Record a successful response in the budget tracker."""
        budget.record_request(
            model=resp.model,
            cost_usd=resp.cost_usd,
            tokens=resp.tokens,
            latency_ms=resp.latency_ms,
            task_type="delegate",
        )

    async def _try_ollama(prompt: str, context: str, max_tokens: int) -> str:
        try:
            resp = await ollama.generate(prompt, context=context, max_tokens=max_tokens)
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"Ollama error: {exc}. Claude should handle this task directly."

    async def _try_openrouter_free(prompt: str, context: str, max_tokens: int) -> str:
        if openrouter is None:
            return "OpenRouter not configured. Claude should handle this task directly."
        try:
            resp = await openrouter.generate(prompt, context=context, max_tokens=max_tokens)
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"OpenRouter error: {exc}. Claude should handle this task directly."

    async def _try_openrouter_paid(
        prompt: str, context: str, max_tokens: int, max_cost: float
    ) -> str:
        if openrouter is None:
            return "OpenRouter not configured. Claude should handle this task directly."
        if not budget.can_spend(settings.openrouter_budget, max_cost):
            return "Monthly budget exhausted. Claude should handle this task directly."
        try:
            resp = await openrouter.generate(
                prompt,
                context=context,
                model="deepseek/deepseek-chat-v3-0324:free",
                max_tokens=max_tokens,
            )
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"OpenRouter paid error: {exc}. Claude should handle this task directly."

    async def _try_openrouter_specific(
        prompt: str, context: str, max_tokens: int, model_id: str
    ) -> str:
        if openrouter is None:
            return "OpenRouter not configured. Claude should handle this task directly."
        try:
            resp = await openrouter.generate(
                prompt, context=context, model=model_id, max_tokens=max_tokens
            )
            _record(resp)
            return _format_response(resp)
        except (ConnectionError, RuntimeError) as exc:
            return f"OpenRouter error ({model_id}): {exc}. Claude should handle this task directly."

    return mcp


server = create_server()


def main() -> None:
    """Entry point for the hive-worker CLI command."""
    server.run()


if __name__ == "__main__":
    main()
