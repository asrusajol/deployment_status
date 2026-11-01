"""The two company themes (Standard / Schertech) — chosen per browser via a cookie,
rendered server-side as <html data-theme="..."> so the page never flashes the wrong
theme before a script could run. `?theme=` deep links set the cookie, matching the
design reference (Deployment_Tracker.html V12.1)."""

import re

from app.models.user import UserRole
from tests.conftest import login_as, make_user

THEME_COOKIE = "dt-theme"


def _html_theme(text):
    match = re.search(r'<html[^>]*\bdata-theme="([^"]*)"', text)
    assert match, "no data-theme on <html>"
    return match.group(1)


def _logged_in(web, role=UserRole.developer):
    test_client, session = web
    make_user(session, id=1, name="Theme Tester", role=role, username="tester", password="correct-horse-1")
    session.commit()
    login_as(test_client, "tester")
    return test_client


def test_default_theme_is_standard(client):
    assert _html_theme(client.get("/login").text) == "standard"


def test_cookie_selects_schertech(client):
    client.cookies.set(THEME_COOKIE, "schertech")
    assert _html_theme(client.get("/login").text) == "schertech"


def test_unknown_cookie_value_falls_back_to_standard(client):
    client.cookies.set(THEME_COOKIE, "neon")
    assert _html_theme(client.get("/login").text) == "standard"


def test_query_param_applies_and_persists_theme(client):
    response = client.get("/login?theme=schertech")
    assert _html_theme(response.text) == "schertech"
    assert response.cookies.get(THEME_COOKIE) == "schertech"
    # Persisted: a later plain request keeps it.
    assert _html_theme(client.get("/login").text) == "schertech"


def test_unknown_query_param_is_ignored(client):
    response = client.get("/login?theme=neon")
    assert _html_theme(response.text) == "standard"
    assert THEME_COOKIE not in response.cookies


def test_theme_menu_offers_both_themes(web):
    test_client = _logged_in(web)
    text = test_client.get("/dashboard").text
    assert re.search(r'href="[^"]*\btheme=standard"', text)
    assert re.search(r'href="[^"]*\btheme=schertech"', text)


def test_theme_menu_keeps_existing_query_params(web):
    test_client = _logged_in(web)
    text = test_client.get("/dashboard/history?page=2").text
    link = re.search(r'href="([^"]*\btheme=schertech[^"]*)"', text).group(1)
    assert "page=2" in link


def test_standard_shell_uses_blue_logo_and_text_title(web):
    test_client = _logged_in(web)
    text = test_client.get("/dashboard").text
    assert "/static/img/logo-blue.svg" in text
    assert "/static/img/logo-white.svg" not in text
    assert "/static/img/wordmark.svg" not in text


def test_schertech_shell_uses_white_logo_and_wordmark(web):
    test_client = _logged_in(web)
    test_client.cookies.set(THEME_COOKIE, "schertech")
    text = test_client.get("/dashboard").text
    assert "/static/img/logo-white.svg" in text
    assert "/static/img/wordmark.svg" in text
    assert "/static/img/logo-blue.svg" not in text


def test_shell_has_nav_toggle_when_nav_is_shown(web):
    # The reference's hamburger folds the side nav to its icon rail.
    test_client = _logged_in(web)
    text = test_client.get("/dashboard").text
    assert 'id="nav-toggle"' in text
    assert 'aria-controls="sidenav"' in text
    assert 'id="sidenav"' in text


def test_login_page_has_no_nav_toggle(client):
    # No nav before login, so nothing for a hamburger to fold.
    assert 'id="nav-toggle"' not in client.get("/login").text


def test_change_password_stays_reachable_from_user_menu(web):
    # The reference's shell has no Change Password link; ours keeps it under the name.
    test_client = _logged_in(web)
    text = test_client.get("/dashboard").text
    menu = re.search(r'<details class="shell-menu" id="user-menu">.*?</details>', text, re.S)
    assert menu, "no user menu in the shell"
    assert "Theme Tester" in menu.group(0)
    assert 'href="/change-password"' in menu.group(0)


def test_nav_icons_are_the_reference_glyphs(web):
    # SAP-icons codepoints from the reference's icon map (Request = U+E139), and the
    # Dashboard item from BusinessSuiteInAppSymbols — not hand-drawn SVGs.
    test_client = _logged_in(web)
    nav = re.search(r"<nav>.*?</nav>", test_client.get("/dashboard").text, re.S).group(0)
    assert '<span class="ico" aria-hidden="true">&#xE139;</span>' in nav
    assert '<span class="ico ico-bs" aria-hidden="true">&#xE11F;</span>' in nav
    assert "<svg" not in nav


def test_reference_fonts_are_served(client):
    for name in ("SAP-icons", "BusinessSuiteInAppSymbols", "72-Regular", "72-Bold", "72-Semibold", "72-SemiboldDuplex"):
        response = client.get(f"/static/fonts/{name}.woff2")
        assert response.status_code == 200, name
        assert response.content[:4] == b"wOF2", name
