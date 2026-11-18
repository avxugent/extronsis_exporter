"""extract_version() is a pure function; cover the HTML and JS-literal paths."""

import pytest

from extronsis_exporter.prober import extract_version


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<div id="version">1.08</div>', "1.08"),
        ('<div id="version">  1.08\n</div>', "1.08"),
        ('<div id="version"><span>v</span><b>2.3</b></div>', "v2.3"),
        ('<html><body><div id="x"><div id="version">3.0</div></div></body></html>', "3.0"),
        # void elements inside the version div must not break depth tracking
        ('<div id="version">1.0<br>beta<img src="a.png"></div><p>after</p>', "1.0beta"),
        # first match wins
        ('<div id="version">1.0</div><div id="version">2.0</div>', "1.0"),
    ],
)
def test_div_version(html, expected):
    assert extract_version(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><body>nothing here</body></html>",
        '<div id="other">1.0</div>',
        '<div id="version"></div>',
        '<div id="version">   </div>',
    ],
)
def test_no_version(html):
    assert extract_version(html) is None


def test_js_literal_fallback_when_div_empty():
    html = '<div id="version"></div><script>var version_number = "1.2.3";</script>'
    assert extract_version(html) == "1.2.3"


def test_div_takes_precedence_over_js_literal():
    html = '<div id="version">9.9</div><script>var version_number = "1.2.3";</script>'
    assert extract_version(html) == "9.9"


@pytest.mark.parametrize("literal", ['"$VERSION"', '""', '"   "'])
def test_js_placeholder_or_blank_is_not_a_version(literal):
    assert extract_version(f"<script>var version_number = {literal};</script>") is None


def test_unclosed_div_yields_none_rather_than_raising():
    assert extract_version('<div id="version">1.0') is None
