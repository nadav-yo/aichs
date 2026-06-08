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
