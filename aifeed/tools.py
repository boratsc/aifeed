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
    """Zwraca {repo: tag}. Lista releases (najnowsze pierwsze), nie /latest:
    flaga 'latest' bywa ustawiona ręcznie na stary tag (OpenCode v1.19.6 vs v2.3.2)."""
    versions = {}
    for tool in TOOLS:
        url = f"https://api.github.com/repos/{tool['repo']}/releases?per_page=10"
        headers = {"User-Agent": "aifeed/0.1"}
        try:
            resp = client.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            releases = data if isinstance(data, list) else [data]
            tag = next((r["tag_name"] for r in releases
                        if not r.get("prerelease") and not r.get("draft") and _is_stable(r.get("tag_name", ""))), None)
            if tag:
                versions[tool["repo"]] = tag
        except Exception:
            continue
    return versions
