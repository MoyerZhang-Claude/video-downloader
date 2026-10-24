"""片段下载：输入起止时间，只下载视频中的一段。"""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)


def parse_time(text):
    """'83' / '1:23' / '01:02:03' / '1:23.5' / '1分23秒' → 秒数；无法识别返回 None。"""
    t = (text or '').strip().replace('：', ':')
    if not t:
        return None
    m = re.fullmatch(r'(?:(\d+)\s*[时h小时]+)?\s*(?:(\d+)\s*[分m]+)?\s*(?:(\d+(?:\.\d+)?)\s*[秒s]?)?', t)
    if m and any(m.groups()) and not re.fullmatch(r'\d+(?:\.\d+)?', t):
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)
    parts = t.split(':')
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return None
    if len(nums) > 3:
        return None
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def fmt_time(sec):
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


class SectionDialog(QDialog):
    def __init__(self, video, parent=None):
        super().__init__(parent)
        self.setWindowTitle('下载片段')
        self.setMinimumWidth(500)
        self.duration = float(video.duration or 0)
        self.section = None
        self.precise = False
        self.option = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        title = QLabel(video.title)
        title.setObjectName('section')
        title.setWordWrap(True)
        v.addWidget(title)
        tip = QLabel(f'视频总长 {fmt_time(self.duration)}。时间可以写成 1:23、83（秒）、1:02:03 或 1分23秒。'
                     if self.duration else '时间可以写成 1:23、83（秒）、1:02:03 或 1分23秒。')
        tip.setObjectName('hint')
        tip.setWordWrap(True)
        v.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(10)
        self.start = QLineEdit('0:00')
        self.end = QLineEdit(fmt_time(self.duration) if self.duration else '')
        self.end.setPlaceholderText('例如 2:30')
        for e in (self.start, self.end):
            e.textChanged.connect(self._validate)
        form.addRow('开始时间', self.start)
        form.addRow('结束时间', self.end)
        self.quality = QComboBox()
        for opt in video.options:
            self.quality.addItem(opt.label, opt)
        form.addRow('清晰度', self.quality)
        v.addLayout(form)
        self.precise_box = QCheckBox('精确剪切（重新编码切点附近，起止时间精确到帧；稍慢）')
        v.addWidget(self.precise_box)
        self.info = QLabel()
        self.info.setObjectName('meta')
        v.addWidget(self.info)

        h = QHBoxLayout()
        h.addStretch(1)
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton('下载这一段')
        self.ok.setObjectName('accent')
        self.ok.clicked.connect(self._accept)
        for b in (cancel, self.ok):
            b.setCursor(Qt.PointingHandCursor)
            h.addWidget(b)
        v.addLayout(h)
        self._validate()

    def set_quality(self, index):
        if 0 <= index < self.quality.count():
            self.quality.setCurrentIndex(index)

    def _values(self):
        s, e = parse_time(self.start.text()), parse_time(self.end.text())
        if s is None or e is None:
            return None, '时间格式不对'
        if self.duration and e > self.duration + 1:
            return None, f'结束时间超过了视频总长 {fmt_time(self.duration)}'
        if e - s < 1:
            return None, '结束时间要比开始时间晚至少 1 秒'
        return (s, e), ''

    def _validate(self):
        sec, err = self._values()
        if sec:
            self.info.setText(f'将下载 {fmt_time(sec[0])} ～ {fmt_time(sec[1])}，共 {fmt_time(sec[1] - sec[0])}')
            self.info.setObjectName('meta')
        else:
            self.info.setText(err)
            self.info.setObjectName('metaError')
        self.info.style().unpolish(self.info)
        self.info.style().polish(self.info)
        self.ok.setEnabled(bool(sec))

    def _accept(self):
        sec, _ = self._values()
        if not sec:
            return
        self.section = sec
        self.precise = self.precise_box.isChecked()
        self.option = self.quality.currentData()
        self.accept()
