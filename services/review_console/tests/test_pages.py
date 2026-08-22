r"""The three consoles are Python strings containing JavaScript, and that seam bites.

A `\n` written into one of these pages for JavaScript's benefit is consumed by
Python first, so the browser receives an actual line break in the middle of a
string literal and the page stops working -- silently, because a syntax error
in a `<script>` block produces a blank panel rather than an error anyone sees.
The same goes for `…`: doubled, the browser gets an ellipsis; single,
Python eats it and the page prints the escape.

Both have now happened. The page bodies are raw strings now, which removes the
whole class of mistake at the source -- but a raw string is one keystroke away
from not being one, and nothing about the failure is visible. So the symptom is
still checked here rather than the spelling: no string literal may run off the
end of its line.

Also checked: that there is still one stylesheet and one set of helpers. There
were three copies, they drifted, and the drift was invisible for the same
reason -- nothing fails when two pages disagree about what "unknown" looks
like, it just quietly becomes two products.
"""

import re

import pytest
from review_console.admin_page import ADMIN_PAGE
from review_console.assets import BASE_CSS, BASE_JS, QUEUE_JS
from review_console.dashboard_page import DASHBOARD_PAGE
from review_console.review_page import REVIEW_PAGE

PAGES = {
    "admin": ADMIN_PAGE,
    "dashboard": DASHBOARD_PAGE,
    "review": REVIEW_PAGE,
}

# A `/` opens a regex literal only where a value cannot already have ended.
# After an identifier, a number or a closing bracket it is division. This is
# the usual heuristic and it is enough here: the only regex literals in these
# pages are `esc()`'s `/[&<>"']/g` and a couple of `.replace()` calls.
_BEFORE_REGEX = set("(,=:[!&|?{};+-*%~^") | {""}


def _script(page: str) -> str:
    match = re.search(r"<script>(.*)</script>", page, re.DOTALL)
    assert match, "page has no script block"
    return match.group(1)


def _open_string_lines(script: str) -> list[tuple[int, str, str]]:
    """Every line that ends with a string literal still open.

    Scanned across the whole script rather than line by line, because block
    comments and the apostrophes inside them ("the selected batch's funnel")
    are only recognisable with the state carried from earlier lines.
    """
    lines = script.split("\n")
    faults: list[tuple[int, str, str]] = []
    in_block_comment = False

    for number, line in enumerate(lines, start=1):
        quote: str | None = None
        index = 0
        previous = ""
        while index < len(line):
            pair = line[index:index + 2]
            if in_block_comment:
                if pair == "*/":
                    in_block_comment = False
                    index += 2
                    continue
                index += 1
                continue
            if quote:
                if line[index] == "\\":
                    index += 2
                    continue
                if line[index] == quote:
                    quote = None
                index += 1
                continue
            if pair == "/*":
                in_block_comment = True
                index += 2
                continue
            if pair == "//":
                break
            char = line[index]
            if char == "/" and previous in _BEFORE_REGEX:
                index += 1
                while index < len(line):
                    if line[index] == "\\":
                        index += 2
                        continue
                    if line[index] == "/":
                        index += 1
                        break
                    index += 1
                previous = "/"
                continue
            if char in "\"'":
                quote = char
            if not char.isspace():
                previous = char
            index += 1

        # A backslash at the end of a line continues a string legally, which
        # nothing here does, but refusing to flag it keeps the check honest.
        if quote and not line.rstrip().endswith("\\"):
            faults.append((number, quote, line.strip()))

    return faults


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_string_literal_runs_off_the_end_of_a_line(name):
    faults = _open_string_lines(_script(PAGES[name]))
    assert not faults, "\n".join(
        f"{name} page, script line {number}: a {quote} string is still open at "
        f"the end of the line. A '\\n' meant for JavaScript was almost "
        f"certainly consumed by Python -- double it.\n  {text}"
        for number, quote, text in faults
    )


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_escape_sequence_leaks_into_the_markup(name):
    """Outside a script block a backslash escape is just text and the browser
    prints it. `&hellip;` is the spelling that works in both places."""
    page = PAGES[name]
    markup = page[:page.index("<script>")] if "<script>" in page else page
    assert "\\u" not in markup, (
        f"{name} page renders a literal escape in its HTML; use an HTML entity"
    )


@pytest.mark.parametrize("name", sorted(PAGES))
def test_backticks_are_not_used(name):
    """The scanner assumes no template literals, which may legitimately span
    lines. If one is introduced this fails rather than the scanner silently
    going wrong."""
    assert "`" not in _script(PAGES[name])


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_page_uses_the_one_stylesheet_and_the_one_set_of_helpers(name):
    page = PAGES[name]
    assert BASE_CSS in page
    assert BASE_JS in page
    assert page.count("<style>") == 1, "a page grew a second style block"


@pytest.mark.parametrize("name", ["admin", "review"])
def test_the_review_queues_render_from_one_copy(name):
    """Both consoles show the same four queues from the same endpoints.

    They were two copies, and the two had already drifted apart in what they
    call the sides of a duplicate comparison -- which is not a difference
    anybody chose, on a screen where the decision is a permanent merge.
    """
    assert QUEUE_JS in PAGES[name]


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_page_defines_a_helper_the_shared_module_already_defines(name):
    """A local redefinition silently wins over the shared one, which is how
    the three copies came about in the first place."""
    script = _script(PAGES[name]).replace(BASE_JS, "").replace(QUEUE_JS, "")
    shared = re.findall(r"^function (\w+)\(", BASE_JS + QUEUE_JS, re.MULTILINE)
    redefined = [n for n in shared if re.search(rf"^function {n}\(", script, re.MULTILINE)]
    assert not redefined, f"{name} redefines {redefined}"


def test_the_scanner_catches_the_bug_it_exists_for():
    """The check is worth nothing if it cannot fail."""
    broken = 'var x = "a string that never\ncloses";'
    assert _open_string_lines(broken)

    fine = 'var x = "one line";\n/* a comment with an apostrophe: batch\'s */\n'
    fine += "var re = /[&<>\"']/g;\n"
    assert not _open_string_lines(fine)
