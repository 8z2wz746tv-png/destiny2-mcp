"""Offline tests for the optional public-site archive script."""

import importlib.util
from pathlib import Path

import httpx
import pytest


spec = importlib.util.spec_from_file_location(
    "fetch_starside", Path(__file__).resolve().parents[1] / "scripts" / "fetch_starside.py"
)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


@pytest.mark.parametrize("url", [
    "https://other.example/a.html", "http://starside.work/a.html",
    "https://starside.work:444/a.html", "https://user@starside.work/a.html",
    "/admin/edit.js", "/builds/s29/a/admin/edit.js", "/api/data.json",
    "/docs/private.json", "/%2e%2e/secret.json", "/folder/%2e%2e/secret.json",
    "/a.html?code=private", "/a.html?token=private", "data:image/png;base64,abc",
])
def test_archive_restricts_scope(url):
    assert archive.normalize_url(url, archive.BASE_URL) is None


def test_archive_normalizes_static_filters_and_directory():
    assert archive.normalize_url("/builds/", archive.BASE_URL) == "https://starside.work/builds/index.html"
    assert archive.normalize_url("/a.html?q=test#section", archive.BASE_URL) == "https://starside.work/a.html"
    assert archive.normalize_url("../a.html", "https://starside.work/builds/index.html") == "https://starside.work/a.html"


def test_archive_preserves_multiple_builds_and_table_semantics():
    page = archive.PageParser()
    page.feed('''<title>Sample</title><main><h2>Table</h2>
        <table><tr><th rowspan="2">Name</th><td>49<span>241</span>
        <span class="pvp">[10%]</span><span class="unsure">?</span></td></tr></table>
        <pre class="src" hidden># One\n\nA: B</pre>
        <pre class="src" hidden># Two\n\nC: D</pre>
        </main><script>should_not_be_text()</script>''')
    result = page.result("https://starside.work/builds/one/index.html")
    assert result["source_blocks"] == ["# One\n\nA: B", "# Two\n\nC: D"]
    row = result["tables"][0]["rows"][0]
    assert row[0]["attrs"]["rowspan"] == "2"
    assert "49241" in row[1]["text"]
    assert 'class="pvp"' in row[1]["html"]
    assert "should_not_be_text" not in result["text"]


def test_archive_finds_lazy_resources_without_admin():
    refs = archive.script_assets(
        "lazy('home.js'); x.src='assets/search.js'; replace('admin/edit.js');",
        "https://starside.work/assets/app.js",
    )
    assert refs == {"https://starside.work/assets/home.js", "https://starside.work/assets/search.js"}
    assert archive.css_assets('url("fonts/a.woff2")', "https://starside.work/assets/site.css") == {
        "https://starside.work/assets/fonts/a.woff2"
    }


def test_archive_only_parses_json_assignments():
    assert archive.assigned_json('window.starsideIndex = [{"u":"a.html"}];', "starsideIndex") == [{"u": "a.html"}]
    with pytest.raises(ValueError):
        archive.assigned_json('window.starsideIndex = []; run();', "starsideIndex")


def test_archive_uses_page_stamp_instead_of_list_entry_date():
    page = archive.PageParser()
    page.feed('<span class="entry-stamp">更新 2026.9.8</span><footer><span class="stamp">更新 2026.9.9</span></footer>')
    assert page.result(archive.BASE_URL)["updated_at"] == "2026.9.9"


def test_archive_uses_build_source_date_without_page_stamp():
    page = archive.PageParser()
    page.feed('<pre id="src" hidden># Build\n更新：2026.9.8</pre>')
    assert page.result(archive.BASE_URL)["updated_at"] == "2026.9.8"


def test_archive_does_not_follow_external_redirect(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.example/a.html"})

    downloader = archive.Downloader(tmp_path, 0, 1, False)
    downloader.client.close()
    downloader.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="Redirect leaves"):
            downloader.request("https://starside.work/a.html")
    finally:
        downloader.close()
    assert requests == ["https://starside.work/a.html"]


def test_archive_rejects_asset_soft_404_and_resumes(tmp_path):
    downloader = archive.Downloader(tmp_path, 0, 1, False)
    downloader.client.close()
    downloader.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="<!doctype html><html>Home</html>")
    ))
    downloader.robots.parse([])
    try:
        with pytest.raises(ValueError, match="returned HTML"):
            downloader.get("https://starside.work/missing.js", False)
        assert not (tmp_path / "assets/missing.js").exists()
        cached = tmp_path / "pages/index.html"
        archive.save_bytes(cached, b"<html>saved</html>")
        body, meta = downloader.get("https://starside.work/index.html", True)
        assert body == b"<html>saved</html>"
        assert meta["content_type"] == "cached"
        assert len(meta["sha256"]) == 64
    finally:
        downloader.close()
