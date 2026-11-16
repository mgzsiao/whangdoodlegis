"""'How things are computed': one page per process, with formulas and sources; searchable, saves to PDF."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSplitter, QTextBrowser)

from ..core import methods, system
from .pages_setup import Page

CSS = ("h2 { color:#1F3F77; font-size:17px; } h3 { color:#1A2233; font-size:14px; margin-top:14px; } "
       "p, li { font-size:13px; line-height:140%; } th { background:#1F3F77; color:white; } "
       "td, th { font-size:12px; }")


class MethodsPage(Page):
    CRUMB = "Help"
    TITLE = "How things are computed"

    def __init__(self, session):
        super().__init__(session)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search, e.g. volume, K value, HAND, datum")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self._filter)
        pdf = QPushButton("Save as PDF…")
        pdf.clicked.connect(self.save_pdf)
        self.set_actions([self.search, pdf])
        split = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.setMaximumWidth(340)
        self.list.setWordWrap(True)
        self.list.setStyleSheet("QListWidget { background: white; } QListWidget::item { padding: 8px 6px; "
                                "border-bottom: 1px solid #EEF1F6; }")
        self.topics = methods.topics()
        for key, title, _h in self.topics:
            it = QListWidgetItem(title)
            it.setData(Qt.UserRole, key)
            self.list.addItem(it)
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.document().setDefaultStyleSheet(CSS)
        self.view.setStyleSheet("QTextBrowser { background: white; padding: 12px 18px; }")
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        self.root.addWidget(split, 1)
        self.list.setCurrentRow(0)

    def show_topic(self, key: str):
        for i, (k, _t, _h) in enumerate(self.topics):
            if k == key:
                self.list.setCurrentRow(i)
                return

    def _show(self, i):
        if 0 <= i < len(self.topics):
            _k, title, body = self.topics[i]
            self.view.setHtml(f"<h2>{title}</h2>{body}")
            q = self.search.text().strip()
            if q:
                self.view.find(q)

    def _filter(self, text):
        q = text.strip().lower()
        first = -1
        for i, (_k, title, body) in enumerate(self.topics):
            hit = not q or q in title.lower() or q in body.lower()
            self.list.item(i).setHidden(not hit)
            if hit and first < 0:
                first = i
        if first >= 0 and (self.list.currentRow() < 0 or self.list.item(self.list.currentRow()).isHidden()):
            self.list.setCurrentRow(first)
        else:
            self._show(self.list.currentRow())

    def save_pdf(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", os.path.join(os.path.expanduser("~"),
                                                  "How things are computed.pdf"), "PDF (*.pdf)")
        if not path:
            return None
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument
        w = QPdfWriter(path)
        w.setPageSize(QPageSize(QPageSize.A4))
        w.setPageMargins(QMarginsF(16, 16, 16, 18), QPageLayout.Millimeter)
        w.setTitle("How things are computed")
        doc = QTextDocument()
        doc.setDefaultFont(QFont("Segoe UI", 9))
        doc.setDefaultStyleSheet(CSS.replace("13px", "9pt").replace("12px", "8pt").replace("17px", "13pt")
                                 .replace("14px", "10.5pt"))
        doc.setPageSize(QSizeF(510, 750))
        doc.setHtml(methods.document_html())
        try:
            doc.print_(w)
        except Exception as e:  # shown to the user
            QMessageBox.warning(self, "Cannot save", str(e))
            return None
        if self.isVisible():
            system.open_path(os.path.dirname(path))
        return path
