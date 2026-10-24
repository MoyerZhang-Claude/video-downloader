"""界面主题：柔和渐变背景 + 半透明磨砂玻璃卡片。

所有交互态（悬停 / 按下）都用样式表实现，由 Qt 直接重绘，没有动画延迟，点击即时有反馈。
"""
import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

ACCENT = '#6366f1'          # 靛蓝
ACCENT2 = '#a855f7'         # 紫
TEXT = '#1e1b4b'
TEXT_SOFT = '#6b6f99'

PLATFORM_COLORS = {
    'B站': '#fb7299', 'B站番剧': '#fb7299', '抖音': '#161823', '快手': '#ff5000', 'YouTube': '#ff0033',
    'X': '#0f1419', 'TikTok': '#fe2c55', '微博': '#e6162d', '小红书': '#ff2442', 'Instagram': '#c13584',
    'Vimeo': '#1ab7ea', 'Facebook': '#1877f2', 'Twitch': '#9146ff', '西瓜视频': '#ff3b30',
}

STATUS_COLORS = {'running': '#4f46e5', 'merging': '#9333ea', 'done': '#059669', 'exists': '#16a34a',
                 'failed': '#dc2626', 'paused': '#d97706', 'canceled': '#6b7280', 'queued': '#6b6f99'}


def _icon_dir():
    d = os.path.join(tempfile.gettempdir(), 'video_downloader_theme')
    os.makedirs(d, exist_ok=True)
    return d


def _make_icons():
    """生成样式表要用的小图标（下拉箭头、勾选），返回 {名字: 路径}。"""
    d = _icon_dir()
    paths = {}

    def save(name, size, draw):
        path = os.path.join(d, f'{name}.png')
        img = QImage(size * 2, size * 2, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(2, 2)
        draw(p)
        p.end()
        img.save(path)
        paths[name] = path.replace('\\', '/')

    def chevron(color):
        def draw(p):
            p.setPen(QPen(QColor(color), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath(QPointF(3.5, 6))
            path.lineTo(7, 9.5)
            path.lineTo(10.5, 6)
            p.drawPath(path)
        return draw

    def check(p):
        p.setPen(QPen(QColor('white'), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(3.5, 7.5))
        path.lineTo(6.2, 10)
        path.lineTo(11, 4.5)
        p.drawPath(path)

    save('chevron', 14, chevron(ACCENT))
    save('chevron_dis', 14, chevron('#b8bbd9'))
    save('check', 14, check)
    return paths


def stylesheet():
    ic = _make_icons()
    return f"""
* {{ font-family: "Microsoft YaHei UI", "Segoe UI"; outline: 0; }}
QWidget {{ font-size: 13px; color: {TEXT}; }}
QLabel#appTitle {{ font-size: 20px; font-weight: 700; color: #312e81; }}
QLabel#appSub {{ color: {TEXT_SOFT}; font-size: 12px; }}
QLabel#section {{ font-size: 14px; font-weight: 700; color: #312e81; }}
QLabel#hint, QLabel#meta {{ color: {TEXT_SOFT}; font-size: 12px; }}
QLabel#metaError {{ color: #dc2626; font-size: 12px; }}
QLabel#metaBusy {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}
QLabel#videoTitle {{ font-size: 13px; font-weight: 600; color: {TEXT}; }}
QLabel#videoTitleDim {{ font-size: 13px; color: #8a8db5; }}
QLabel#statusText {{ color: #4338ca; font-size: 12px; }}

/* ---------- 按钮：按下立即变色并下沉 1px ---------- */
QPushButton {{
    background: rgba(255,255,255,175); color: #3730a3;
    border: 1px solid rgba(255,255,255,235); border-radius: 9px; padding: 5px 14px;
}}
QPushButton:hover {{ background: rgba(255,255,255,245); border-color: #a5b4fc; }}
QPushButton:pressed {{ background: rgba(199,210,254,240); border-color: #818cf8; padding: 6px 14px 4px 14px; }}
QPushButton:disabled {{ color: #a3a6c8; background: rgba(255,255,255,90); border-color: rgba(255,255,255,120); }}
QPushButton#small {{ padding: 3px 11px; font-size: 12px; border-radius: 8px; }}
QPushButton#small:pressed {{ padding: 4px 11px 2px 11px; }}
QPushButton#primary, QPushButton#accent {{
    color: white; border: none; font-weight: 700;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6366f1, stop:1 #a855f7);
}}
QPushButton#primary {{ font-size: 15px; border-radius: 12px; padding: 9px 30px; }}
QPushButton#accent {{ font-size: 12px; border-radius: 8px; padding: 3px 13px; }}
QPushButton#primary:hover, QPushButton#accent:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7b7ef7, stop:1 #bb72fa);
}}
QPushButton#primary:pressed, QPushButton#accent:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4338ca, stop:1 #7e22ce);
}}
QPushButton#primary:pressed {{ padding: 10px 30px 8px 30px; }}
QPushButton#accent:pressed {{ padding: 4px 13px 2px 13px; }}
QPushButton#accent:disabled {{ background: #c7c9e8; }}
QPushButton#done {{ color: white; border: none; border-radius: 8px; padding: 3px 13px; font-size: 12px;
    font-weight: 700; background: #10b981; }}
QPushButton#danger:hover {{ color: #dc2626; border-color: #fca5a5; }}
QPushButton#pill {{ border-radius: 12px; padding: 4px 14px; font-weight: 600; }}
QPushButton#chip {{ background: #ede9fe; color: #6d28d9; border: none; border-radius: 8px; padding: 1px 9px;
    font-size: 11px; font-weight: 700; }}
QPushButton#chip:hover {{ background: #ddd6fe; }}
QPushButton#chip:pressed {{ background: #c4b5fd; padding: 2px 9px 0px 9px; }}
QListWidget {{ background: rgba(255,255,255,190); border: 1px solid rgba(255,255,255,240); border-radius: 10px;
    padding: 4px; outline: 0; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 6px; color: {TEXT}; }}
QListWidget::item:hover {{ background: rgba(224,231,255,180); }}
QListWidget::item:selected {{ background: #e0e7ff; color: {TEXT}; }}
QListWidget::item:disabled {{ color: #a3a6c8; }}
QListView::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #a5b4fc;
    background: white; }}
QListView::indicator:checked {{ border: none; image: url({ic['check']});
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6366f1, stop:1 #a855f7); }}
QPushButton#pill:pressed {{ padding: 5px 14px 3px 14px; }}

/* ---------- 输入 ---------- */
QPlainTextEdit#input {{
    background: rgba(255,255,255,200); border: 1px solid rgba(255,255,255,240); border-radius: 12px;
    padding: 6px 8px; selection-background-color: #c7d2fe; selection-color: {TEXT};
}}
QPlainTextEdit#input:focus {{ background: rgba(255,255,255,240); border: 1px solid #a5b4fc; }}
QPlainTextEdit#log {{
    background: rgba(255,255,255,120); border: none; border-radius: 10px; padding: 6px;
    font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px; color: #374151;
}}
QLineEdit, QSpinBox {{
    background: rgba(255,255,255,195); border: 1px solid rgba(255,255,255,235); border-radius: 8px;
    padding: 4px 8px; selection-background-color: #c7d2fe; selection-color: {TEXT};
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: #a5b4fc; background: rgba(255,255,255,240); }}
QComboBox {{
    background: rgba(255,255,255,200); border: 1px solid rgba(255,255,255,240); border-radius: 8px;
    padding: 4px 28px 4px 10px; min-height: 20px;
}}
QComboBox:hover {{ border-color: #a5b4fc; background: rgba(255,255,255,245); }}
QComboBox:on {{ border-color: #818cf8; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({ic['chevron']}); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url({ic['chevron_dis']}); }}
QComboBox QAbstractItemView {{
    background: white; border: 1px solid #c7d2fe; padding: 4px; outline: 0;
    selection-background-color: #e0e7ff; selection-color: #1e1b4b;
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QCheckBox {{ spacing: 7px; color: #3730a3; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #a5b4fc;
    background: rgba(255,255,255,210); }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ border: none; image: url({ic['check']});
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6366f1, stop:1 #a855f7); }}

/* ---------- 表格 ---------- */
QTableWidget {{
    background: transparent; border: none; gridline-color: transparent;
    selection-background-color: rgba(129,140,248,45); selection-color: {TEXT};
}}
QTableWidget::item {{ border-bottom: 1px solid rgba(99,102,241,22); }}
QTableWidget::item:hover {{ background: rgba(255,255,255,95); }}
QTableWidget::item:selected {{ background: rgba(129,140,248,45); }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: transparent; border: none; border-bottom: 1px solid rgba(99,102,241,45);
    color: #6366f1; font-weight: 600; font-size: 12px; padding: 5px 6px;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle {{ background: rgba(99,102,241,70); border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 30px; }}
QScrollBar::handle:horizontal {{ min-width: 30px; }}
QScrollBar::handle:hover {{ background: rgba(99,102,241,140); }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---------- 进度条：按状态换色 ---------- */
QProgressBar {{
    background: rgba(255,255,255,165); border: 1px solid rgba(255,255,255,230); border-radius: 6px;
    max-height: 14px; min-height: 14px; text-align: center; font-size: 11px; color: #312e81;
}}
QProgressBar::chunk {{ border-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #60a5fa, stop:1 #a78bfa); }}
QProgressBar[state="done"]::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #34d399, stop:1 #10b981); }}
QProgressBar[state="failed"]::chunk {{ background: #f87171; }}
QProgressBar[state="paused"]::chunk {{ background: #fbbf24; }}
QProgressBar[state="merging"]::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c084fc, stop:1 #e879f9); }}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{ border: none; background: transparent; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {TEXT_SOFT}; padding: 7px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent; font-weight: 600; font-size: 13px; }}
QTabBar::tab:hover {{ color: #4338ca; }}
QTabBar::tab:selected {{ color: #4f46e5; border-bottom: 2px solid #6366f1; }}
QSplitter::handle {{ background: transparent; }}

/* ---------- 弹出层 ---------- */
QMenu {{ background: white; border: 1px solid #c7d2fe; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: #e0e7ff; color: #312e81; }}
QMenu::separator {{ height: 1px; background: #e0e7ff; margin: 4px 6px; }}
QToolTip {{ background: white; color: {TEXT}; border: 1px solid #c7d2fe; padding: 5px 8px; }}
/* ---------- 对话框 ---------- */
QDialog, QMessageBox {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #eef2ff, stop:0.55 #f5f3ff, stop:1 #fdf2f8);
}}
QGroupBox {{
    background: rgba(255,255,255,170); border: 1px solid rgba(255,255,255,240); border-radius: 12px;
    margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 700; color: #4338ca;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; }}
"""
