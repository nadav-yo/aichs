import os

from ui.main_window import MainWindow


def test_main_window_wires_current_chat_model_to_git_changes(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    try:
        window._chat.model_combo.addItem("bridge-model")
        window._chat.model_combo.setCurrentText("bridge-model")

        assert window._left._git._changes._current_model_getter() == "bridge-model"
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)


def test_main_window_reveals_opened_file_in_left_panel(qapp, workspace):
    cwd = os.getcwd()
    app_style = qapp.styleSheet()
    app_font = qapp.font()
    window = MainWindow(startup_workspace=str(workspace))
    opened = workspace / "src" / "main.py"
    revealed = []
    try:
        window._left.reveal_file = lambda path: revealed.append(path) or True

        window._open_file(str(opened))

        assert revealed == [str(opened)]
    finally:
        window.close()
        os.chdir(cwd)
        qapp.setFont(app_font)
        qapp.setStyleSheet(app_style)
