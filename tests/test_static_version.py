"""The ?v= cache-buster covers every static file a page loads as code — a change to
grid.js must reach browsers as surely as a change to style.css."""

from app.static_version import compute_static_version


def test_version_changes_with_any_of_the_files(tmp_path):
    css, js = tmp_path / "style.css", tmp_path / "grid.js"
    css.write_text("a {}")
    js.write_text("var x = 1;")
    before = compute_static_version([css, js])

    js.write_text("var x = 2;")

    assert compute_static_version([css, js]) != before


def test_a_missing_file_still_gives_a_version(tmp_path):
    assert compute_static_version([tmp_path / "nope.css"])
