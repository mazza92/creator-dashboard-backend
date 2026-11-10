"""Crawler/AI discovery probes are not errors; unknown /api paths are warnings."""

import ast
import pathlib


def _level_fn():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "unmatched_route_level")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("_PROBE_PREFIXES", "_PROBE_FILES")
                                                  for t in n.targets))]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "app_subset", "exec"), ns)
    return ns["unmatched_route_level"]


def test_levels():
    level = _level_fn()
    for probe in (".well-known/openid-configuration", ".well-known/oauth-authorization-server",
                  ".well-known/oauth-protected-resource/api/v1/integrations/claude/mcp", ".well-known/ai-catalog.json",
                  ".well-known/ard.json", "robots.txt", "apple-touch-icon.png", "wp-login.php", ".env",
                  "api/v1/integrations/claude/mcp/.well-known/agent.json", "discovery/resources"):
        assert level(probe) == "info", probe
    assert level("api/pr-crm/does-not-exist") == "warning"
