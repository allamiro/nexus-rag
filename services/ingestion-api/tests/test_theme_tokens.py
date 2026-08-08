"""Every selectable portal theme must be fully defined in all three places.

A theme is spread across the server allowlist (`THEMES`), a token block in
portal.css, and a radio option in admin.html. Miss one and the failure is quiet
rather than loud: a value absent from `THEMES` is silently rejected on POST, a
value with no token block renders as the default palette, and a block missing
one token inherits that single value from the default -- a half-themed page that
reads as a CSS bug rather than a missing declaration.

These tests are deliberately structural (they read the stylesheet and template
as text) because nothing here resolves CSS custom properties at runtime.
Contrast ratios are measured out-of-band in a browser, not asserted here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.routes.admin import THEMES

APP_DIR = Path(__file__).resolve().parent.parent / "app"
PORTAL_CSS = (APP_DIR / "static" / "portal.css").read_text(encoding="utf-8")
ADMIN_HTML = (APP_DIR / "templates" / "admin.html").read_text(encoding="utf-8")

# base.html emits data-theme only when `theme` is truthy, so "" means "render the
# default :root palette" and is offered as the "Default (midnight)" radio.
OFFERED = frozenset(re.findall(r'name="theme"\s+value="([^"]*)"', ADMIN_HTML))
OFFERED_THEMES = sorted(t for t in OFFERED if t)

# "midnight" is a legacy alias for the default palette: allowlisted so an older
# stored preference still validates, but it has no [data-theme] block because it
# *is* :root. Any other allowlisted value without a block would be a typo that
# silently renders as the default -- test_no_undefined_allowlist_entries catches
# that. Update this set only when adding another deliberate alias.
DEFAULT_PALETTE_ALIASES = frozenset({"midnight"})


def _theme_block(theme: str) -> str:
    match = re.search(rf':root\[data-theme="{re.escape(theme)}"\] \{{(.*?)\n\}}', PORTAL_CSS, re.S)
    assert match, f"no :root[data-theme={theme!r}] token block in portal.css"
    return match.group(1)


def _theme_tokens(theme: str) -> frozenset[str]:
    return frozenset(re.findall(r"^\s*(--[a-z0-9-]+):", _theme_block(theme), re.M))


def test_the_selector_offers_several_themes() -> None:
    # Guards the parametrised tests below: an empty OFFERED_THEMES would make
    # every one of them pass vacuously.
    assert len(OFFERED_THEMES) >= 4
    assert "" in OFFERED, 'the "Default (midnight)" radio (value="") disappeared'


@pytest.mark.parametrize("theme", OFFERED_THEMES)
def test_offered_theme_has_a_token_block(theme: str) -> None:
    assert _theme_tokens(theme), f"{theme} declares no custom properties"


@pytest.mark.parametrize("theme", OFFERED_THEMES)
def test_offered_theme_is_server_accepted(theme: str) -> None:
    assert theme in THEMES, f"{theme} has a radio but POSTing it is rejected"


@pytest.mark.parametrize("theme", OFFERED_THEMES)
def test_offered_theme_defines_the_tokens_its_peers_all_define(theme: str) -> None:
    """No theme may omit a token that every other theme defines.

    Compared against the intersection of the peers rather than their union, so a
    single theme's extra token (daylight carries two shadow tokens the dark
    themes have no use for) is not imposed on the rest.
    """
    common: frozenset[str] | None = None
    for other in OFFERED_THEMES:
        if other == theme:
            continue
        tokens = _theme_tokens(other)
        common = tokens if common is None else (common & tokens)
    missing = (common or frozenset()) - _theme_tokens(theme)
    assert not missing, f"{theme} is missing tokens every other theme defines: {sorted(missing)}"


def test_no_undefined_allowlist_entries() -> None:
    """An allowlisted value with neither a block nor alias status is a typo.

    It would validate on POST and then render as the default palette, which
    looks like "the theme did not apply" rather than "that theme is misspelled".
    """
    undefined = {
        theme
        for theme in THEMES
        if theme
        and theme not in DEFAULT_PALETTE_ALIASES
        and not re.search(rf':root\[data-theme="{re.escape(theme)}"\]', PORTAL_CSS)
    }
    assert not undefined, (
        f"allowlisted with no token block and not a known alias: {sorted(undefined)}"
    )


def test_stylesheet_fetches_nothing_external() -> None:
    """NFR-1: the portal must not reach the network to render.

    Dracula ships as a CDN stylesheet and an npm package; this repo transcribes
    the palette into tokens instead, and this test is what stops a later "just
    @import it" change from quietly breaking the air gap.
    """
    external = re.findall(r"@import[^;]+;|url\(\s*['\"]?(?:https?:)?//[^)]+\)", PORTAL_CSS)
    assert not external, f"portal.css references external resources: {external}"
