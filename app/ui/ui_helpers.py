from __future__ import annotations
from PySide6 import QtWidgets


def vbox(parent=None, margins=(0, 0, 0, 0), spacing=6) -> QtWidgets.QVBoxLayout:
    lay = QtWidgets.QVBoxLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(parent=None, margins=(0, 0, 0, 0), spacing=6) -> QtWidgets.QHBoxLayout:
    lay = QtWidgets.QHBoxLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def grid(parent=None, margins=(0, 0, 0, 0), spacing=6) -> QtWidgets.QGridLayout:
    lay = QtWidgets.QGridLayout(parent)
    lay.setContentsMargins(*margins)
    lay.setHorizontalSpacing(spacing)
    lay.setVerticalSpacing(spacing)
    return lay


def set_expand(widget: QtWidgets.QWidget):
    widget.setSizePolicy(
        QtWidgets.QSizePolicy.Policy.Expanding,
        QtWidgets.QSizePolicy.Policy.Expanding
    )
    return widget