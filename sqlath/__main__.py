import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from . import theme
from .mainwindow import MainWindow

# no macOS a versão arredondada com margem segue o padrão dos ícones do Dock
ICON = Path(__file__).parent / "resources" / ("app_icon_macos.png" if sys.platform == "darwin" else "app_icon.png")
FONTS = Path(__file__).parent / "resources" / "fonts"


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Skorpa Data")
    app.setOrganizationName("sqlath")
    app.setDesktopFileName("sqlath")  # associa a janela ao atalho sqlath.desktop
    app.setWindowIcon(QIcon(str(ICON)))
    theme.load_saved()  # padrão: claro
    # Inter na interface (embutida no app); JetBrains Mono só no editor de SQL
    for ttf in FONTS.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    if "Inter Variable" in QFontDatabase.families() or "Inter" in QFontDatabase.families():
        family = "Inter Variable" if "Inter Variable" in QFontDatabase.families() else "Inter"
        app.setFont(QFont(family, theme.pt(10)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
