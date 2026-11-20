from aifeed.tools import TOOLS, fetch_tool_versions


def test_tools_list_is_kuratorowana_i_poprawna():
    assert len(TOOLS) >= 8
    names = {t["name"] for t in TOOLS}
    assert {"Claude Code", "Codex", "Gemini CLI", "OpenCode", "Cline", "Zed", "Copilot CLI", "VS Code"} <= names
    # każde narzędzie ma repo i nazwę
    for t in TOOLS:
        assert t["repo"].count("/") == 1
        assert t["name"].strip()


def test_fetch_tool_versions_reads_latest_tag():
    # symulujemy dwie odpowiedzi API (repo -> tag)
    responses = iter(["v1.2.3", "2025-01-01"])
    def fake_get(url, **kwargs):
        from types import SimpleNamespace
        repo = url.split("/repos/")[1].split("/releases")[0]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"tag_name": next(responses)},
        )
    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, **kwargs): return fake_get(url, **kwargs)
    versions = fetch_tool_versions(FakeClient())
    # wyniki słownik repo -> tag
    assert isinstance(versions, dict)
    assert list(versions.values())[:2] == ["v1.2.3", "2025-01-01"]
