"""界面主题：向 Claude 应用看齐的暖色调浅色 / 深色主题。

- 颜色全部是「令牌」（P['text']、P['surface'] …），样式表和自绘控件都从这里取色，切换深浅色只要重新 apply()。
- 中文用微软雅黑；英文正文 VDSans、标题 VDSerif（程序自带的开源字体，最接近 Claude 的字体）。
- 带透明度的颜色写成 Qt 的 #AARRGGBB，样式表和 QColor 都认。
"""
import os
import sys
import tempfile

from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath, QPalette, QPen


def _a(hex_rgb, alpha):
    """'#rrggbb' + 不透明度(0~1) → Qt 的 '#AARRGGBB'"""
    return f'#{round(alpha * 255):02x}{hex_rgb[1:]}'


LIGHT = {
    'bg': '#faf9f5', 'sidebar': '#f3f1ea', 'surface': '#ffffff', 'field': '#ffffff', 'menu': '#ffffff',
    'subtle': '#f0eee6', 'chip': '#f5f4ef',
    'hover': _a('#1f1e1d', 0.05), 'press': _a('#1f1e1d', 0.09), 'selected': '#e8e5da', 'menu_hover': '#f0eee6',
    'border': _a('#1f1e1d', 0.11), 'border_strong': _a('#1f1e1d', 0.20), 'border_focus': _a('#1f1e1d', 0.38),
    'text': '#141413', 'text2': '#3d3d3a', 'text3': '#73726c', 'text4': '#a5a39a',
    'brand': '#d97757', 'accent': '#c96442', 'accent_hover': '#b65838', 'accent_press': '#a14d31',
    'accent_off': '#e7b9a7', 'on_accent': '#ffffff', 'tint_accent': _a('#d97757', 0.13),
    'primary': '#141413', 'primary_hover': '#33322f', 'on_primary': '#faf9f5', 'primary_off': '#c9c7bf',
    'ok': '#55733a', 'tint_ok': _a('#788c5d', 0.16), 'err': '#b3452f', 'tint_err': _a('#c4553f', 0.12),
    'warn': '#9a6a12', 'info': '#3c6a9c', 'tint_info': _a('#6a9bcc', 0.16),
    'track': '#ebe8de', 'scroll': _a('#1f1e1d', 0.18), 'scroll_hover': _a('#1f1e1d', 0.32),
    'scrim': _a('#141413', 0.32), 'shadow': _a('#3d2c20', 0.10), 'selection': _a('#d97757', 0.28),
    'tooltip': '#141413', 'on_tooltip': '#faf9f5', 'avatar': '#c96442',
}

DARK = {
    'bg': '#262624', 'sidebar': '#1f1e1d', 'surface': '#30302e', 'field': '#30302e', 'menu': '#30302e',
    'subtle': '#141413', 'chip': '#2b2b29',
    'hover': _a('#faf9f5', 0.06), 'press': _a('#faf9f5', 0.10), 'selected': '#383734', 'menu_hover': '#3d3c39',
    'border': _a('#dedcd1', 0.13), 'border_strong': _a('#dedcd1', 0.22), 'border_focus': _a('#dedcd1', 0.40),
    'text': '#faf9f5', 'text2': '#e5e3dc', 'text3': '#a6a39a', 'text4': '#74726b',
    'brand': '#d97757', 'accent': '#c96442', 'accent_hover': '#d6714f', 'accent_press': '#b65838',
    'accent_off': '#5e3b2e', 'on_accent': '#ffffff', 'tint_accent': _a('#d97757', 0.18),
    'primary': '#faf9f5', 'primary_hover': '#e3e1d8', 'on_primary': '#141413', 'primary_off': '#4a4945',
    'ok': '#9cba76', 'tint_ok': _a('#788c5d', 0.26), 'err': '#ec7b66', 'tint_err': _a('#c4553f', 0.22),
    'warn': '#dcaa4f', 'info': '#86b1dd', 'tint_info': _a('#6a9bcc', 0.22),
    'track': '#3d3c39', 'scroll': _a('#faf9f5', 0.16), 'scroll_hover': _a('#faf9f5', 0.30),
    'scrim': _a('#000000', 0.48), 'shadow': _a('#000000', 0.30), 'selection': _a('#d97757', 0.38),
    'tooltip': '#faf9f5', 'on_tooltip': '#141413', 'avatar': '#c96442',
}

P = dict(LIGHT)          # 当前主题的颜色
MODES = {'system': '跟随系统', 'light': '浅色', 'dark': '深色'}

PLATFORM_COLORS = {
    'B站': '#fb7299', 'B站番剧': '#fb7299', '抖音': '#4b4b52', '快手': '#ff5000', 'YouTube': '#ff0033',
    'X': '#6b7280', 'TikTok': '#fe2c55', '微博': '#e6162d', '小红书': '#ff2442', 'Instagram': '#c13584',
    'Vimeo': '#1ab7ea', 'Facebook': '#1877f2', 'Twitch': '#9146ff', '西瓜视频': '#ff3b30',
}

# 任务状态 → 颜色令牌
STATUS_ROLE = {'running': 'brand', 'merging': 'info', 'done': 'ok', 'exists': 'ok', 'failed': 'err',
               'paused': 'text3', 'canceled': 'text4', 'queued': 'text3'}

# 字体和 Claude 应用一样：中文一律用系统黑体（微软雅黑），英文正文用 VDSans、标题用 VDSerif（最接近 Claude 的开源字体）。
# Claude 的「周一快乐，Moyer」就是中文黑体 + 英文衬线。
SERIF = 'VDSerif'
LATIN = 'VDSans'
SANS = 'Microsoft YaHei UI'


class _Signals(QObject):
    changed = Signal()


signals = _Signals()
_state = {'mode': 'system', 'dark': False, 'fonts': False}


def c(role, alpha=None):
    """取当前主题颜色（QColor）。alpha 可覆盖不透明度（0~1）。"""
    col = QColor(P.get(role, role))
    if alpha is not None:
        col.setAlphaF(alpha)
    return col


def status_color(status):
    return c(STATUS_ROLE.get(status, 'text3'))


def is_dark():
    return _state['dark']


def mode():
    return _state['mode']


def _system_dark():
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - 旧版 Qt 没有这个接口
        return False


def font_dirs():
    base = os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(base, 'fonts'), os.path.join(getattr(sys, '_MEIPASS', base), 'app', 'fonts')]


def load_fonts():
    """注册自带的英文字体（VDSans / VDSerif）；万一没找到，英文退回 Segoe UI / Georgia。"""
    global SERIF, LATIN
    if _state['fonts']:
        return
    _state['fonts'] = True
    fams = set()
    for d in font_dirs():
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(('.ttf', '.otf')):
                    fid = QFontDatabase.addApplicationFont(os.path.join(d, name))
                    fams.update(QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else [])
            if fams:
                break
    if 'VDSerif' not in fams:
        SERIF = 'Georgia'
    if 'VDSans' not in fams:
        LATIN = 'Segoe UI'


def ui_families():
    return [LATIN, SANS]


def apply(app, new_mode=None):
    """应用主题（new_mode: system / light / dark）。系统深浅色变化时再调用一次即可。"""
    if new_mode:
        _state['mode'] = new_mode if new_mode in MODES else 'system'
    m = _state['mode']
    dark = _system_dark() if m == 'system' else m == 'dark'
    _state['dark'] = dark
    P.clear()
    P.update(DARK if dark else LIGHT)
    load_fonts()
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    signals.changed.emit()


def _palette():
    pal = QPalette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        pal.setColor(group, QPalette.Window, c('bg'))
        pal.setColor(group, QPalette.Base, c('field'))
        pal.setColor(group, QPalette.AlternateBase, c('subtle'))
        pal.setColor(group, QPalette.Button, c('surface'))
        pal.setColor(group, QPalette.ToolTipBase, c('tooltip'))
        pal.setColor(group, QPalette.ToolTipText, c('on_tooltip'))
        pal.setColor(group, QPalette.Highlight, c('selection'))
        pal.setColor(group, QPalette.HighlightedText, c('text'))
        pal.setColor(group, QPalette.PlaceholderText, c('text4'))
        pal.setColor(group, QPalette.Link, c('accent'))
        dim = group == QPalette.Disabled
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(group, role, c('text4' if dim else 'text'))
    return pal


# ---------------------------------------------------------------- 样式表用的小图标

def _make_icons():
    d = os.path.join(tempfile.gettempdir(), 'video_downloader_theme')
    os.makedirs(d, exist_ok=True)
    tag = 'd' if _state['dark'] else 'l'
    paths = {}

    def save(name, draw, size=14):
        path = os.path.join(d, f'{name}_{tag}.png')
        img = QImage(size * 3, size * 3, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(3, 3)
        draw(p)
        p.end()
        img.save(path)
        paths[name] = path.replace('\\', '/')

    def stroke(color, pts, width=1.6):
        def draw(p):
            p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath(QPointF(*pts[0]))
            for pt in pts[1:]:
                path.lineTo(*pt)
            p.drawPath(path)
        return draw

    save('chevron', stroke(P['text3'], [(3.5, 5.5), (7, 9), (10.5, 5.5)]))
    save('chevron_off', stroke(P['text4'], [(3.5, 5.5), (7, 9), (10.5, 5.5)]))
    save('chevron_right', stroke(P['text3'], [(5.5, 3.5), (9, 7), (5.5, 10.5)]))
    save('check', stroke(P['on_primary'], [(3.5, 7.2), (6, 9.8), (10.8, 4.4)], 1.8))
    save('check_menu', stroke(P['text'], [(3.5, 7.2), (6, 9.8), (10.8, 4.4)], 1.6))
    return paths


def stylesheet():
    p = P
    ic = _make_icons()
    return f"""
QWidget {{ font-size: 13px; color: {p['text']}; }}
QMainWindow, QDialog, QMessageBox {{ background: {p['bg']}; }}
QWidget#central, QWidget#page {{ background: {p['bg']}; }}
QWidget#clear {{ background: transparent; }}
QFrame#sidebar {{ background: {p['sidebar']}; border: none; border-right: 1px solid {p['border']}; }}
QLabel {{ background: transparent; }}

QLabel#greeting {{ font-family: "{SERIF}", "{SANS}"; font-size: 34px; color: {p['text']}; }}
QLabel#pageTitle {{ font-family: "{SERIF}", "{SANS}"; font-size: 26px; color: {p['text']}; }}
QLabel#brandName {{ font-family: "{SERIF}", "{SANS}"; font-size: 17px; color: {p['text']}; }}
QLabel#serifSmall {{ font-family: "{SERIF}", "{SANS}"; font-size: 15px; color: {p['text2']}; }}
QLabel#panelTitle {{ font-size: 18px; font-weight: 600; color: {p['text']}; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 600; color: {p['text']}; }}
QLabel#headTitle {{ font-size: 14px; font-weight: 600; color: {p['text']}; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; color: {p['text']}; }}
QLabel#cardTitleDim {{ font-size: 13px; color: {p['text3']}; }}
QLabel#rowLabel {{ font-size: 14px; color: {p['text']}; }}
QLabel#rowDesc, QLabel#meta, QLabel#hint {{ font-size: 12px; color: {p['text3']}; }}
QLabel#metaStrong {{ font-size: 12px; color: {p['text2']}; }}
QLabel#metaError {{ font-size: 12px; color: {p['err']}; }}
QLabel#metaOk {{ font-size: 12px; color: {p['ok']}; }}
QLabel#metaBusy {{ font-size: 12px; color: {p['brand']}; }}
QLabel#sideSection {{ font-size: 12px; color: {p['text3']}; padding-left: 10px; }}
QLabel#tagOk {{ background: {p['tint_ok']}; color: {p['ok']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#tagInfo {{ background: {p['tint_info']}; color: {p['info']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#tagAccent {{ background: {p['tint_accent']}; color: {p['accent']}; border-radius: 6px; padding: 1px 7px; font-size: 12px; }}
QLabel#kbd {{ background: {p['subtle']}; color: {p['text3']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 0 5px; font-size: 11px; }}
QLabel#link {{ color: {p['accent']}; }}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border_strong']};
    border-radius: 8px; padding: 6px 14px;
}}
QPushButton:hover {{ background: {p['chip']}; border-color: {p['border_focus']}; }}
QPushButton:pressed {{ background: {p['subtle']}; }}
QPushButton:disabled {{ color: {p['text4']}; border-color: {p['border']}; background: transparent; }}
QPushButton#primary {{ background: {p['primary']}; color: {p['on_primary']}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#primary:hover {{ background: {p['primary_hover']}; }}
QPushButton#primary:disabled {{ background: {p['primary_off']}; color: {p['on_primary']}; }}
QPushButton#accent {{ background: {p['accent']}; color: {p['on_accent']}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#accent:hover {{ background: {p['accent_hover']}; }}
QPushButton#accent:pressed {{ background: {p['accent_press']}; }}
QPushButton#accent:disabled {{ background: {p['accent_off']}; }}
QPushButton#done {{ background: {p['ok']}; color: {p['on_primary'] if is_dark() else '#ffffff'}; border: none; font-weight: 600; padding: 7px 15px; }}
QPushButton#ghost {{ background: transparent; border: none; color: {p['text2']}; padding: 6px 10px; }}
QPushButton#ghost:hover {{ background: {p['hover']}; color: {p['text']}; }}
QPushButton#ghost:pressed {{ background: {p['press']}; }}
QPushButton#ghost:disabled {{ color: {p['text4']}; }}
QPushButton#danger {{ color: {p['err']}; }}
QPushButton#danger:hover {{ background: {p['tint_err']}; border-color: {p['err']}; }}
QPushButton#small {{ padding: 4px 10px; font-size: 12px; }}

/* ---------- 输入 ---------- */
QLineEdit, QSpinBox, QPlainTextEdit#field {{
    background: {p['field']}; color: {p['text']}; border: 1px solid {p['border_strong']}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {p['selection']}; selection-color: {p['text']};
}}
QLineEdit:hover, QSpinBox:hover {{ border-color: {p['border_focus']}; }}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit#field:focus {{ border-color: {p['border_focus']}; }}
QLineEdit:disabled, QSpinBox:disabled {{ color: {p['text4']}; border-color: {p['border']}; }}
QLineEdit#search {{ background: {p['field']}; border-radius: 10px; padding: 7px 10px; font-size: 14px; }}
QLineEdit#searchBig {{ background: transparent; border: none; padding: 8px 2px; font-size: 16px; }}
QPlainTextEdit#composer {{
    background: transparent; border: none; padding: 0; font-size: 15px; color: {p['text']};
    selection-background-color: {p['selection']}; selection-color: {p['text']};
}}
QPlainTextEdit#log {{
    background: transparent; border: none; padding: 4px; color: {p['text2']};
    font-family: Consolas, "{SANS}"; font-size: 12px; selection-background-color: {p['selection']};
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QComboBox {{
    background: {p['field']}; color: {p['text']}; border: 1px solid {p['border_strong']}; border-radius: 8px;
    padding: 5px 30px 5px 10px; min-height: 20px;
}}
QComboBox:hover {{ border-color: {p['border_focus']}; }}
QComboBox:on {{ border-color: {p['border_focus']}; }}
QComboBox:disabled {{ color: {p['text4']}; border-color: {p['border']}; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({ic['chevron']}); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url({ic['chevron_off']}); }}
QComboBox QAbstractItemView {{
    background: {p['menu']}; color: {p['text']}; border: none; padding: 5px; outline: 0;
    selection-background-color: {p['menu_hover']}; selection-color: {p['text']};
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 6px; color: {p['text']}; }}
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{ background: {p['menu_hover']}; }}
QComboBox QAbstractItemView::item:disabled {{ color: {p['text4']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; color: {p['text']}; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border_focus']};
    background: {p['field']}; }}
QCheckBox::indicator:hover {{ border-color: {p['text3']}; }}
QCheckBox::indicator:checked {{ border: none; background: {p['primary']}; image: url({ic['check']}); }}
QCheckBox::indicator:disabled {{ border-color: {p['border']}; }}

/* ---------- 列表（选集） ---------- */
QListWidget {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 4px;
    outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; color: {p['text']}; }}
QListWidget::item:hover {{ background: {p['hover']}; }}
QListWidget::item:selected {{ background: {p['selected']}; color: {p['text']}; }}
QListWidget::item:disabled {{ color: {p['text4']}; }}
QListView::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p['border_focus']};
    background: {p['field']}; }}
QListView::indicator:checked {{ border: none; background: {p['primary']}; image: url({ic['check']}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 3px; }}
QScrollBar::handle {{ background: {p['scroll']}; border-radius: 3px; }}
QScrollBar::handle:vertical {{ min-height: 36px; }}
QScrollBar::handle:horizontal {{ min-width: 36px; }}
QScrollBar::handle:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{ background: {p['track']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p['brand']}; border-radius: 3px; }}

QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ background: transparent; color: {p['text3']}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['brand']}; }}
QGroupBox {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px;
    margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; }}

/* ---------- 弹出层 ---------- */
QMenu {{ background: {p['menu']}; border: 1px solid {p['border']}; padding: 6px; }}
QMenu::item {{ padding: 7px 30px 7px 12px; border-radius: 6px; color: {p['text']}; background: transparent; }}
QMenu::item:selected {{ background: {p['menu_hover']}; }}
QMenu::item:disabled {{ color: {p['text3']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::indicator {{ width: 14px; height: 14px; padding-left: 8px; }}
QMenu::indicator:checked {{ image: url({ic['check_menu']}); }}
QMenu::right-arrow {{ image: url({ic['chevron_right']}); width: 12px; height: 12px; padding-right: 8px; }}
QToolTip {{ background: {p['tooltip']}; color: {p['on_tooltip']}; border: none; padding: 5px 9px; font-size: 12px; }}
QMessageBox QLabel {{ color: {p['text']}; }}
"""
