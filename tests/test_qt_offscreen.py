import os


def test_qt_tests_default_to_offscreen():
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
