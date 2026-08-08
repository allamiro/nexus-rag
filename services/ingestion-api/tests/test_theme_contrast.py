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

Not covered here: anything requiring layout -- element geometry and font sizes at
a given viewport are measured in a browser instead. Text thresholds assume normal
size, since the 3:1 large-text allowance would need font metrics this file avoids.
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
# WCAG 2.1 SC 1.4.11: the visual boundary of a control needs 3:1 against what is
# adjacent. Field borders, card edges and the stepper rail are all --line-strong.
AA_NON_TEXT = 3.0

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
    """Composite a background stack onto the opaque page colour.

    `layers` runs **outermost first** — the order the DOM nests them, furthest
    from the viewer first — and each is painted over the result so far. So
    `("--surface", "--primary-soft")` means the card, then the tint on top of it,
    which is what the browser does.

    This previously iterated `reversed(layers)`, painting the card *over* the
    tint. It never failed a test because the affected values stayed far from
    their thresholds, but it computed the wrong colour and **overstated**
    contrast — the step badge came out at 13.09:1 against a browser-measured
    10.63:1, and overstating is the one direction a safety check must not err in.
    `test_model_matches_browser_measurements` now pins the arithmetic to numbers
    read out of a real render, so an inversion here fails loudly.
    """
    result = parse_colour(token(theme, "--page"))
    for layer in layers:
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
    # The accent labels. `--primary-dark` also has to serve as a solid hover fill
    # behind `--on-primary`, so on a dark theme it cannot also be light enough to
    # read as 11-12px bold text: measured 4.04 / 3.76 / 3.70:1 on the default
    # theme and two failures on slate (#578's second half). These use `--primary`,
    # which clears AA in all six themes while keeping the accent — moving them to
    # `--text` would have removed the colour that is the point of them.
    #
    # The stacks are the real DOM nesting, read out of a render rather than
    # assumed: the avatar sits on `--surface-muted` inside the translucent header,
    # and the callout's icon chip sits on `--surface-muted` inside a callout that
    # is *also* `--surface-muted`, inside the card.
    ("eyebrow", "--primary", (), ".eyebrow"),
    ("user-avatar", "--primary", ("--header-bg", "--surface-muted"), ".user-avatar"),
    (
        "callout-icon",
        "--primary",
        ("--surface", "--surface-muted", "--surface-muted"),
        ".info-callout > span",
    ),
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


# (case id, token, background layers over --page, what renders it)
#
# Borders sit over both the card and the page depending on the component, so each
# is asserted against both -- the weaker of the two is what a viewer may get.
NON_TEXT_CASES = [
    ("field-border-on-card", "--line-strong", ("--surface",), "input, select, textarea"),
    ("field-border-on-page", "--line-strong", (), ".form-section (card edge)"),
    ("stepper-rail", "--line-strong", ("--surface",), ".form-section::after (#566 rail)"),
]


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(
    ("case", "fg", "layers", "rendered_by"),
    NON_TEXT_CASES,
    ids=[case[0] for case in NON_TEXT_CASES],
)
def test_non_text_meets_aa(
    theme: str, case: str, fg: str, layers: tuple[str, ...], rendered_by: str
) -> None:
    background = background_stack(theme, layers)
    ratio = contrast_ratio(parse_colour(token(theme, fg)), background)
    assert ratio >= AA_NON_TEXT, (
        f"{theme}: {rendered_by} draws {fg} on "
        f"{' over '.join(reversed(layers)) or '--page'} at {ratio}:1, "
        f"below the {AA_NON_TEXT}:1 SC 1.4.11 floor for a control boundary"
    )


def test_borders_actually_use_the_token_these_cases_measure() -> None:
    """As above: measuring --line-strong proves nothing if borders stopped using it."""
    uses = PORTAL_CSS.count("var(--line-strong)")
    assert uses >= 10, f"only {uses} uses of --line-strong left -- NON_TEXT_CASES may be stale"
    assert "border: 1px solid var(--line-strong)" in PORTAL_CSS, (
        "no field/card border uses --line-strong; NON_TEXT_CASES is measuring an unused token"
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


def test_accent_labels_still_use_the_brighter_accent_token() -> None:
    """#578's second half: these three read as accent-coloured text.

    `--primary-dark` cannot serve here — it doubles as a solid hover fill behind
    `--on-primary`, so it is too dark to read as small bold text on a dark theme.
    Reverting any of them to `--primary-dark` puts the failure straight back.
    """
    for selector in (".eyebrow", ".user-avatar", ".info-callout > span"):
        match = re.search(re.escape(selector) + r" \{(.*?)\n\}", PORTAL_CSS, re.S)
        assert match, f"{selector} is gone -- update TEXT_CASES"
        body = match.group(1)
        assert "color: var(--primary)" in body, (
            f"{selector} no longer uses --primary as its foreground; if it went "
            f"back to --primary-dark, #578's second half has regressed"
        )


def test_primary_dark_is_only_used_as_a_fill_now() -> None:
    """Every surviving `color: var(--primary-dark)` must be a hover fill's own rule.

    Those rules set `background: var(--primary-dark)` too and take their text
    colour from the non-hover rule (`--on-primary`), so they are the fill role.
    A *new* `color: var(--primary-dark)` on a rule without that background would
    be a fresh instance of the defect this issue is about, and nothing else would
    catch it -- TEXT_CASES only measures the pairings it already knows.
    """
    offenders = []
    for match in re.finditer(r"([.#][^{}\n]+) \{([^}]*)\}", PORTAL_CSS):
        selector, body = match.group(1).strip(), match.group(2)
        if "color: var(--primary-dark)" in body and "background: var(--primary-dark)" not in body:
            offenders.append(selector)
    assert not offenders, (
        f"--primary-dark used as a text colour outside a hover fill: {offenders}. "
        f"It is too dark to meet AA as small text on the dark themes (#578) -- "
        f"use --primary, or --text on a --primary-soft chip."
    )


# Ratios read out of a real render (headless Chrome compositing each element's
# effective background through its ancestors) on the commit that added #578's
# second half. Pinning a handful of them keeps the pure-Python model above honest:
# the arithmetic can drift from what a browser actually paints without any
# assertion noticing, which is exactly what happened with background_stack()'s
# layer order -- it overstated the step badge as 13.09:1 against a measured
# 10.63:1. Tolerance is 0.05, since both sides compute the same sRGB formula.
BROWSER_MEASURED = [
    ("midnight", "step-badge", 10.63),
    ("dracula", "step-badge", 8.54),
    ("daylight", "step-badge", 14.4),
    ("midnight", "eyebrow", 6.78),
    ("midnight", "user-avatar", 6.31),
    ("midnight", "callout-icon", 6.21),
    ("slate", "user-avatar", 6.91),
    ("daylight", "callout-icon", 5.47),
    ("dracula", "eyebrow", 5.9),
]


@pytest.mark.parametrize(("theme", "case_id", "measured"), BROWSER_MEASURED)
def test_model_matches_browser_measurements(theme: str, case_id: str, measured: float) -> None:
    case = next((c for c in TEXT_CASES if c[0] == case_id), None)
    assert case is not None, f"case {case_id} was renamed; update BROWSER_MEASURED"
    _, fg, layers, _ = case
    computed = contrast_ratio(parse_colour(token(theme, fg)), background_stack(theme, layers))
    assert abs(computed - measured) <= 0.05, (
        f"{theme}/{case_id}: this file computes {computed}:1, a real browser render "
        f"measured {measured}:1. The compositing model has drifted from what is "
        f"actually painted -- check background_stack()'s layer order first."
    )
