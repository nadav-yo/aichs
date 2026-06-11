from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QTextDocument
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QTextBrowser

from ui.markdown_html import code_from_copy_url


class RemoteImageTextBrowser(QTextBrowser):
    """QTextBrowser with HTTPS image loading for Markdown previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_manager = QNetworkAccessManager(self)
        self._remote_images: dict[str, QImage] = {}
        self._pending_images: set[str] = set()

    def setSource(
        self,
        name: QUrl,
        resource_type=QTextDocument.ResourceType.UnknownResource,
    ) -> None:
        if copy_code_url_to_clipboard(name):
            return
        super().setSource(name, resource_type)

    def loadResource(self, resource_type: int, name: QUrl):
        image_type = QTextDocument.ResourceType.ImageResource
        if resource_type == image_type and name.scheme().lower() in {"http", "https"}:
            key = name.toString()
            cached = self._remote_images.get(key)
            if cached is not None:
                return cached
            if key not in self._pending_images:
                self._fetch_remote_image(name)
            return None
        return super().loadResource(resource_type, name)

    def _fetch_remote_image(self, url: QUrl) -> None:
        key = url.toString()
        self._pending_images.add(key)
        request = QNetworkRequest(url)
        reply = self._image_manager.get(request)
        reply.finished.connect(
            lambda reply=reply, key=key, url=QUrl(url): self._remote_image_ready(
                reply,
                key,
                url,
            )
        )

    def _remote_image_ready(self, reply: QNetworkReply, key: str, url: QUrl) -> None:
        self._pending_images.discard(key)
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = bytes(reply.readAll())
            image = image_from_markdown_image_data(data)
            if image.isNull():
                return
            self._remote_images[key] = image
            self.document().addResource(QTextDocument.ResourceType.ImageResource, url, image)
            self.document().markContentsDirty(0, self.document().characterCount())
            self.viewport().update()
        finally:
            reply.deleteLater()


def copy_code_url_to_clipboard(url: QUrl | str) -> bool:
    raw = url.toString() if isinstance(url, QUrl) else str(url)
    code = code_from_copy_url(raw)
    if code is None:
        return False
    QGuiApplication.clipboard().setText(code)
    return True


def image_from_markdown_image_data(data: bytes) -> QImage:
    image = QImage.fromData(data)
    if not image.isNull():
        return image

    renderer = QSvgRenderer(data)
    if not renderer.isValid():
        return QImage()

    size = renderer.defaultSize()
    if not size.isValid() or size.isEmpty():
        size = QSize(120, 20)
    size = _bounded_svg_size(size)
    image = QImage(size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return image


def _bounded_svg_size(size: QSize) -> QSize:
    max_side = max(size.width(), size.height())
    if max_side <= 600:
        return size
    return size.scaled(600, 600, Qt.AspectRatioMode.KeepAspectRatio)
