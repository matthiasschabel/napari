from napari.components._viewer_constants import CursorStyle
from napari.components.cursor import Cursor


def test_cursor():
    """Test creating cursor object"""
    cursor = Cursor()
    assert cursor is not None


def test_operation_cursor_styles():
    cursor = Cursor()

    cursor.style = 'add'
    assert cursor.style is CursorStyle.ADD
    cursor.style = 'remove'
    assert cursor.style is CursorStyle.REMOVE


def test_operation_cursor_contract_version():
    assert Cursor.OPERATION_CURSOR_VERSION >= 1
