"""Tests de deteccion de navegador."""

from utils.browser_env import find_browser_executable


def test_find_browser_executable_usa_preferred_path(tmp_path):
    browser = tmp_path / "chrome"
    browser.write_text("")

    resolved = find_browser_executable(str(browser))

    assert resolved == str(browser)
