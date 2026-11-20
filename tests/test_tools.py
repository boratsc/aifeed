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


def test_fetch_prefers_newer_list_release_over_stale_latest(monkeypatch):
    # GitHub /releases/latest bywa oznaczony ręcznie i wskazuje stary tag
    # (OpenCode: latest=v1.19.6, ale lista ma nowsze v2.3.2).
    import aifeed.tools as tools
    from types import SimpleNamespace
    monkeypatch.setattr(tools, "TOOLS", [{"name": "OpenCode", "repo": "x/y", "group": "cli"}])
    def fake_get(url, **kwargs):
        if url.endswith("/releases/latest"):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"tag_name": "v1.19.6"})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [
            {"tag_name": "v2.3.2", "prerelease": False, "draft": False},
            {"tag_name": "v1.19.6", "prerelease": False, "draft": False},
        ])
    class FakeClient:
        def get(self, url, **kwargs): return fake_get(url, **kwargs)
    assert tools.fetch_tool_versions(FakeClient()) == {"x/y": "v2.3.2"}


def test_fetch_skips_prerelease_and_draft_in_list(monkeypatch):
    import aifeed.tools as tools
    from types import SimpleNamespace
    monkeypatch.setattr(tools, "TOOLS", [{"name": "T", "repo": "x/y", "group": "cli"}])
    def fake_get(url, **kwargs):
        if url.endswith("/releases/latest"):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"tag_name": "v1.0.0"})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [
            {"tag_name": "v3.0.0-rc1", "prerelease": False, "draft": False},
            {"tag_name": "v2.0.0", "prerelease": True, "draft": False},
            {"tag_name": "v1.9.0", "prerelease": False, "draft": True},
            {"tag_name": "v1.5.0", "prerelease": False, "draft": False},
        ])
    class FakeClient:
        def get(self, url, **kwargs): return fake_get(url, **kwargs)
    assert tools.fetch_tool_versions(FakeClient()) == {"x/y": "v1.5.0"}
