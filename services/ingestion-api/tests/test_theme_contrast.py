"""WCAG contrast floors for the portal's themed text, enforced per theme.

Why this exists: the palettes live in `portal.css` as custom properties, and a
theme is added by copying a block and changing hex values. Nothing stopped a new
value from landing below the readability floor -- issue #578 found the numbered
step badge at 2.76:1 in the default theme, i.e. it had been below AA since the
badge was introduced, in every theme, unnoticed.

How it works: each case below names a foreground token, the background stack it
is actually painted on, and the threshold. Ratios are computed the way a browser
composites them -- translucent surfaces are alpha-blended onto what is behind
them, because `--surface` here is `rgba` over the page and comparing against the
raw token overstates every ratio.

The load-bearing part is `test_css_still_pairs_tokens_the_way_these_cases_assume`:
a table of token pairings is worthless if the stylesheet stops using those pairs,
since the assertions would keep passing while measuring nothing real. That test
reads the actual declarations back out of `portal.css`.

Not covered here: anything requiring layout. Element geometry, font sizes at a
given viewport, and the non-text contrast of borders and the stepper rail are
measured in a browser instead (borders are tracked in #578 and are *known* to be
below the 3:1 non-text minimum -- deliberately not asserted yet, so this file
stays honest about what it proves).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PORTAL_CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "portal.css").read_text(
    encoding="utf-8"
)

# WCAG 2.x SC 1.4.3. Everything asserted here is normal-size text; the 3:1
# large-text allowance would need font metrics this file deliberately avoids.
AA_NORMAL_TEXT = 4.5

Rgba = tuple[float, float, float, float]


def _declarations(selector_body: str) -> dict[str, str]:
    return dict(re.findall(r"^\s*(--[a-z0-9-]+):\s*([^;]+);", selector_body, re.M))


def _block(selector: str) -> dict[str, str]:
    match = re.search(re.escape(selector) + r" \{(.*?)\n\}", PORTAL_CSS, re.S)
    return _declarations(match.group(1)) if match else {}


_ROOT = _block(":root")
_THEME_BLOCKS = {
    name: _block(f':root[data-theme="{name}"]')
    for name in re.findall(r':root\[data-theme="([a-z]+)"\] \{', PORTAL_CSS)
}
# The default palette on bare :root, which base.html renders when no theme is
# set. Named for the radio that selects it ("Default (midnight)").
THEMES = ["midnight", *sorted(_THEME_BLOCKS)]


def token(theme: str, name: str, _depth: int = 0) -> str:
    """A theme's value for a token, falling back to :root, resolving var()."""
    assert _depth < 10, f"var() indirection cycle resolving {name}"
    raw = (_THEME_BLOCKS.get(theme, {}).get(name) or _ROOT.get(name, "")).strip()
    assert raw, f"token {name} is defined by neither {theme} nor :root"
    alias = re.fullmatch(r"var\((--[a-z0-9-]+)\)", raw)
    return token(theme, alias.group(1), _depth + 1) if alias else raw


def parse_colour(value: str) -> Rgba:
    value = value.strip()
    if long_hex := re.fullmatch(r"#([0-9a-fA-F]{6})", value):
        digits = long_hex.group(1)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16), 1.0)
    if short_hex := re.fullmatch(r"#([0-9a-fA-F]{3})", value):
        return (*(int(d * 2, 16) for d in short_hex.group(1)), 1.0)  # type: ignore[return-value]
    if rgb := re.fullmatch(r"rgba?\(([^)]+)\)", value):
        parts = [float(p) for p in re.split(r"[,\s/]+", rgb.group(1).strip()) if p]
        return (parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 1.0)
    raise AssertionError(f"cannot parse colour {value!r} -- extend parse_colour")


def flatten(top: Rgba, bottom: Rgba) -> Rgba:
    """Composite `top` over `bottom` (what the compositor does with alpha)."""
    alpha = top[3]
    channels = zip(top[:3], bottom[:3], strict=True)
    return (*(t * alpha + b * (1 - alpha) for t, b in channels), 1.0)  # type: ignore[return-value]


def _channel(value: float) -> float:
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: Rgba) -> float:
    r, g, b = (_channel(c) for c in colour[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: Rgba, background: Rgba) -> float:
    """WCAG contrast, blending a translucent foreground onto its background."""
    if foreground[3] < 1:
        foreground = flatten(foreground, background)
    lighter, darker = sorted((relative_luminance(foreground), relative_luminance(background)))[::-1]
    return round((lighter + 0.05) / (darker + 0.05), 2)


def background_stack(theme: str, layers: tuple[str, ...]) -> Rgba:
    """Flatten a background stack, innermost last, onto the opaque page colour."""
    result = parse_colour(token(theme, "--page"))
    for layer in reversed(layers):
        result = flatten(parse_colour(token(theme, layer)), result)
    return result


# (case id, foreground token, background layers over --page, what renders it)
#
# `--primary-soft` over `--surface` is the "chip" -- the tinted square behind the
# numbered step badges, the active nav item, and the sidebar step lists. Issue
# #578: this stack paired with `--primary-dark` failed AA in four of five themes,
# and raising the tint made it worse (the chip moves toward the text colour), so
# the foreground moved to `--text` instead.
TEXT_CASES = [
    ("step-badge", "--text", ("--surface", "--primary-soft"), ".section-number"),
    ("nav-active", "--text", ("--surface", "--primary-soft"), ".main-nav a.active"),
    (
        "account-icon-active",
        "--text",
        ("--surface", "--primary-soft"),
        ".account-icon-action.active",
    ),
    ("workflow-step", "--text", ("--surface", "--primary-soft"), ".workflow-list li > span"),
    ("kb-step", "--text", ("--surface", "--primary-soft"), ".kb-steps li > span"),
    ("body-copy", "--muted", ("--surface",), ".section-heading p"),
    ("card-text", "--text", ("--surface",), ".form-section"),
    ("page-text", "--text", (), "body"),
    ("primary-button-label", "--on-primary", ("--primary",), ".btn-primary"),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(
    ("case", "fg", "layers", "rendered_by"),
    TEXT_CASES,
    ids=[case[0] for case in TEXT_CASES],
)
def test_text_meets_aa(
    theme: str, case: str, fg: str, layers: tuple[str, ...], rendered_by: str
) -> None:
    background = background_stack(theme, layers)
    ratio = contrast_ratio(parse_colour(token(theme, fg)), background)
    assert ratio >= AA_NORMAL_TEXT, (
        f"{theme}: {rendered_by} renders {fg} on "
        f"{' over '.join(reversed(layers)) or '--page'} at {ratio}:1, "
        f"below the {AA_NORMAL_TEXT}:1 AA floor for normal text"
    )


def test_every_theme_is_covered() -> None:
    """A theme block that stops matching the discovery regex must not vanish
    silently from the matrix above."""
    declared = set(re.findall(r':root\[data-theme="([a-z]+)"\]', PORTAL_CSS))
    # "midnight" is an alias of the default :root palette and has no block.
    assert declared - {"midnight"} <= set(THEMES)
    assert len(THEMES) >= 5, f"expected the default plus 4+ themes, found {THEMES}"


def test_css_still_pairs_tokens_the_way_these_cases_assume() -> None:
    """The cases above encode which token sits on which background.

    If a rule is restyled -- the badge given a solid fill, say -- these
    assertions would keep passing while describing a page that no longer exists.
    Check the pairings against the stylesheet so that drift fails loudly here
    instead of going unnoticed.
    """
    for selector in (
        ".section-number",
        ".main-nav a.active",
        ".account-icon-action.active",
        ".workflow-list li > span",
        ".kb-steps li > span",
    ):
        match = re.search(re.escape(selector) + r" \{(.*?)\n\}", PORTAL_CSS, re.S)
        assert match, f"{selector} is gone -- update TEXT_CASES"
        body = match.group(1)
        assert "background: var(--primary-soft)" in body, (
            f"{selector} no longer uses the --primary-soft chip; the "
            f"background stack in TEXT_CASES is stale"
        )
        assert "color: var(--text)" in body, (
            f"{selector} no longer uses --text as its foreground; either the "
            f"regression from #578 returned or TEXT_CASES needs updating"
        )
