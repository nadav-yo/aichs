from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QWidget,
)
from PyQt6.QtCore import Qt

from services.context_budget import ContextBudget, format_bytes
from ui.theme import (
    palette,
    meta_font_pt,
    chat_font_pt,
    MONO_FONT_CSS,
    dialog_shell_style,
    separator_frame_style,
    hint_label_style,
    primary_button_style,
    title_label_style,
)


class ContextBreakdownDialog(QDialog):
    def __init__(self, budget: ContextBudget, model: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Context usage")
        self.setMinimumWidth(420)

        p = palette()
        fs = chat_font_pt()
        meta = meta_font_pt()
        hint = hint_label_style()
        mono_hint = hint_label_style(font_family=MONO_FONT_CSS)
        self.setStyleSheet(dialog_shell_style())

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = QLabel("Context window")
        header.setStyleSheet(title_label_style(font_pt=fs + 2, font_weight="bold"))
        root.addWidget(header)

        summary = QLabel(
            f"{budget.used_tokens:,} / {budget.window_tokens:,} tokens  "
            f"({budget.pct:.0f}%)  ·  {format_bytes(budget.used_bytes)}"
        )
        summary.setStyleSheet(hint)
        root.addWidget(summary)

        model_lbl = QLabel(f"Model: {model}")
        model_lbl.setStyleSheet(hint)
        root.addWidget(model_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(separator_frame_style())
        root.addWidget(sep)

        cols = QHBoxLayout()
        cols.addStretch()
        tok_hdr = QLabel("tokens")
        tok_hdr.setFixedWidth(56)
        tok_hdr.setAlignment(Qt.AlignmentFlag.AlignRight)
        tok_hdr.setStyleSheet(hint)
        size_hdr = QLabel("size")
        size_hdr.setFixedWidth(64)
        size_hdr.setAlignment(Qt.AlignmentFlag.AlignRight)
        size_hdr.setStyleSheet(hint)
        cols.addWidget(tok_hdr)
        cols.addWidget(size_hdr)
        root.addLayout(cols)

        for seg in budget.segments:
            root.addWidget(self._row(seg.label, seg.byte_count, seg.token_count, seg.detail, p, fs, hint, mono_hint))

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(separator_frame_style())
        root.addWidget(sep2)

        footer = QLabel(
            f"Auto-compaction when context exceeds {budget.compaction_limit_tokens:,} tokens "
            f"({budget.reserve_tokens:,} reserved for the next response)."
        )
        footer.setWordWrap(True)
        footer.setStyleSheet(hint)
        root.addWidget(footer)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close.setStyleSheet(primary_button_style(font_size=meta, font_weight="600"))
        btn_row.addWidget(close)
        root.addLayout(btn_row)

    @staticmethod
    def _row(label: str, nbytes: int, tokens: int, detail: str, p: dict, fs: int, hint: str, mono_hint: str) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 4)

        left = QVBoxLayout()
        left.setSpacing(2)
        title = QLabel(label)
        title.setStyleSheet(title_label_style(font_pt=fs, font_weight="normal"))
        left.addWidget(title)
        if detail:
            sub = QLabel(detail)
            sub.setStyleSheet(hint)
            left.addWidget(sub)

        size = QLabel(format_bytes(nbytes))
        size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        size.setStyleSheet(hint_label_style(text_color=p["TEXT"], font_family=MONO_FONT_CSS))

        tokens_lbl = QLabel(f"{tokens:,}")
        tokens_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tokens_lbl.setFixedWidth(56)
        tokens_lbl.setStyleSheet(mono_hint)

        wrap = QWidget()
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(left, 1)
        outer.addWidget(tokens_lbl)
        outer.addWidget(size)
        return wrap
