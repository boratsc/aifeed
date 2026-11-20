"""Curated developer-tool and SDK registry with version lookup."""

TOOLS = [
    {"name": "Claude Code", "repo": "anthropics/claude-code", "group": "cli"},
    {"name": "Codex", "repo": "openai/codex", "group": "cli"},
    {"name": "Gemini CLI", "repo": "google-gemini/gemini-cli", "group": "cli"},
    {"name": "OpenCode", "repo": "anomalyco/opencode", "group": "cli"},
    {"name": "Cline", "repo": "cline/cline", "group": "cli"},
    {"name": "Zed", "repo": "zed-industries/zed", "group": "cli"},
    {"name": "Copilot CLI", "repo": "github/copilot-cli", "group": "cli"},
    {"name": "VS Code", "repo": "microsoft/vscode", "group": "cli"},
    {"name": "Node.js", "repo": "nodejs/node", "group": "sdk", "pre": True},
    {"name": "Anthropic SDK (Py)", "repo": "anthropics/anthropic-sdk-python", "group": "sdk"},
    {"name": "Anthropic SDK (TS)", "repo": "anthropics/anthropic-sdk-typescript", "group": "sdk"},
    {"name": "OpenAI (Py)", "repo": "openai/openai-python", "group": "sdk"},
    {"name": "OpenAI (Node)", "repo": "openai/openai-node", "group": "sdk"},
    {"name": "LangChain", "repo": "langchain-ai/langchain", "group": "sdk"},
    {"name": "Vercel AI SDK", "repo": "vercel/ai", "group": "sdk"},
]

_PRERELEASE = ("alpha", "beta", "rc", "preview", "pre")


def _is_stable(tag: str) -> bool:
    low = tag.lower()
    return not any(marker in low for marker in _PRERELEASE)


def fetch_tool_versions(client) -> dict[str, str]:
    """Zwraca {repo: tag}. Dla repo oznaczonych pre=True bierze najnowszy stabilny."""
    versions = {}
    for tool in TOOLS:
        url = f"https://api.github.com/repos/{tool['repo']}/releases/latest"
        headers = {"User-Agent": "aifeed/0.1"}
        if tool.get("pre"):
            # Node: przeszukaj ostatnie 5, wybierz pierwszy stabilny
            url = f"https://api.github.com/repos/{tool['repo']}/releases?per_page=5"
        try:
            resp = client.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            tags = [data["tag_name"]] if isinstance(data, dict) and "tag_name" in data else [d["tag_name"] for d in data]
            tag = next((t for t in tags if _is_stable(t)), None)
            if tag:
                versions[tool["repo"]] = tag
        except Exception:
            continue
    return versions
