from __future__ import annotations

import pytest

from molecule_ranker.runtime_agents.tool_discovery import (
    DynamicToolDiscovery,
    ToolDiscoveryError,
)
from molecule_ranker.tool_ecosystem.registry import ToolRegistryV2


def test_relevant_tools_selected_for_goal() -> None:
    registry = ToolRegistryV2.default()
    discovery = DynamicToolDiscovery(registry=registry)

    result = discovery.discover(
        user_goal="Rank disease candidates and summarize the ranking.",
        user_permissions=_all_permissions(registry),
        max_tools=5,
    )

    assert "builtins.ranking.run_ranking" in result.selected_tool_names
    assert "builtins.ranking.summarize_ranking" in result.selected_tool_names
    assert len(result.selected_tools) <= 5
    assert any(
        suggestion.name == "rank disease"
        for suggestion in result.selected_skill_suggestions
    )


def test_unauthorized_tools_hidden() -> None:
    registry = ToolRegistryV2.default()
    discovery = DynamicToolDiscovery(registry=registry)
    all_tools = list(registry.runtime_specs.values())

    result = discovery.discover(
        user_goal="Rank disease candidates.",
        available_tools=all_tools,
        user_permissions={"run:read"},
    )

    assert "builtins.ranking.run_ranking" not in result.selected_tool_names
    assert any(
        excluded.tool_name == "builtins.ranking.run_ranking"
        for excluded in result.excluded_tools
    )


def test_disabled_tools_hidden() -> None:
    registry = ToolRegistryV2.default()
    disabled_spec = registry.resolve_tool("builtins.ranking.run_ranking")
    registry.disable_tool("builtins.ranking.run_ranking")
    discovery = DynamicToolDiscovery(registry=registry)

    result = discovery.discover(
        user_goal="Rank disease candidates.",
        available_tools=[disabled_spec],
        user_permissions=_all_permissions(registry),
    )

    assert result.selected_tools == []
    assert result.excluded_tools[0].tool_name == "builtins.ranking.run_ranking"
    assert result.excluded_tools[0].reason == "not visible"


def test_codex_request_unknown_tool_rejected() -> None:
    registry = ToolRegistryV2.default()
    discovery = DynamicToolDiscovery(registry=registry)
    result = discovery.discover(
        user_goal="Rank disease candidates.",
        user_permissions=_all_permissions(registry),
    )

    with pytest.raises(ToolDiscoveryError, match="unknown or unauthorized tool"):
        discovery.validate_codex_tool_request(
            ["plugin.fake.nonexistent"],
            discovery_result=result,
            user_permissions=_all_permissions(registry),
        )


def _all_permissions(registry: ToolRegistryV2) -> set[str]:
    return {
        permission
        for spec in registry.runtime_specs.values()
        for permission in spec.required_permissions
    }
