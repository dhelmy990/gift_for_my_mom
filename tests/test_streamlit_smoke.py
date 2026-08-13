"""Runtime coverage for the real Streamlit company-name review renderer."""

from pathlib import Path

from streamlit.testing.v1 import AppTest


FIXTURE_APP = Path(__file__).parent / "fixtures" / "singleton_review_app.py"


def _rendered_text(app: AppTest) -> str:
    values: list[str] = []
    for element_type in ("markdown", "text", "caption", "subheader"):
        values.extend(str(element.value) for element in app.get(element_type))
    values.extend(str(element.label) for element in app.expander)
    values.extend(str(element.label) for element in app.button)
    values.extend(str(element.label) for element in app.text_input)
    values.extend(str(element.label) for element in app.multiselect)
    return "\n".join(values)


def test_review_renderer_executes_all_safe_runtime_sections():
    fixture_source = FIXTURE_APP.read_bytes()
    app = AppTest.from_file(FIXTURE_APP, default_timeout=10).run()

    assert not app.exception
    assert FIXTURE_APP.read_bytes() == fixture_source
    rendered = _rendered_text(app)
    for expected in (
        "Review company names",
        "Search every company name in this report",
        "Separate companies",
        "Working tray",
        "Return to separate",
        "Combined groups",
        "Move to tray",
        "Final company name",
        "Review and save",
        "2 separate companies",
        "1 combined group",
        "1 name combined",
        "Admin password",
        "Save mappings and show totals",
        "Backup and recovery",
    ):
        assert expected in rendered
