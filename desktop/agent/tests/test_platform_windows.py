"""Only the pure part of the Windows backend can be tested without Windows."""

from kmr_desktop.platforms.windows_uia import map_role


def test_control_types_map_to_the_shared_roles() -> None:
    assert map_role("ButtonControl") == "button"
    assert map_role("EditControl") == "text_field"
    assert map_role("EditControl", multiline=True) == "text_area"
    assert map_role("HyperlinkControl") == "link"
    assert map_role("TextControl") == "label"
    assert map_role("SomethingNew") == "other"
