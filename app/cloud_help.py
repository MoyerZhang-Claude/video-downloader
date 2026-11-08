"""谷歌云盘的使用教程与常见问题（设置 → 谷歌云盘 →「使用教程」）。

每个人要用自己的 Google Cloud 项目创建一个「桌面应用」密钥：软件用这个密钥向谷歌申请访问你的云盘。
免费，只需要做一次，约 10 分钟。
"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

STEPS = [
    ('新建项目',
     '用你要存视频的那个谷歌账号登录 Google Cloud 控制台（第一次进要同意服务条款）。\n'
     '项目名称随便填，比如 link-downloader，点「创建」。创建好后确认左上角选中的是这个项目。',
     'https://console.cloud.google.com/projectcreate'),
    ('启用 Google Drive API',
     '打开下面的页面，点「启用」（Enable）。',
     'https://console.cloud.google.com/apis/library/drive.googleapis.com'),
    ('设置授权页面',
     '打开 Google Auth Platform，点「开始」（Get started）：\n'
     '· 应用名称：随便填，比如「链接解析下载器」\n'
     '· 用户支持邮箱、联系邮箱：选你自己的 Gmail\n'
     '· 受众群体（Audience）：选「外部」（External）\n'
     '勾选同意条款，点「创建」。注意：品牌页里不要上传应用徽标（上传了就要谷歌审核）。',
     'https://console.cloud.google.com/auth/overview'),
    ('发布应用',
     '打开「受众群体」页，点「发布应用」（Publish app）并确认。\n'
     '不发布的话停在「测试」状态，谷歌每 7 天会让登录失效，需要重新连接。',
     'https://console.cloud.google.com/auth/audience'),
    ('创建密钥并下载',
     '打开「客户端」页，点「创建客户端」（Create client）：\n'
     '· 应用类型选「桌面应用」（Desktop app），名称随意，点「创建」\n'
     '· 在弹出的窗口里点「下载 JSON」，文件名类似 client_secret_….json',
     'https://console.cloud.google.com/auth/clients'),
    ('导入并连接',
     '回到软件：设置 → 谷歌云盘 →「导入密钥文件」，选刚下载的 JSON 文件；\n'
     '再点「连接」，在浏览器里登录谷歌账号并允许访问，就完成了。',
     ''),
]

FAQ = [
    ('「发布应用」按钮是灰的，点不了',
     '多半是在「品牌」页上传了应用徽标：点徽标栏右边的 ✕ 去掉，拉到最下面点「保存」，再回「受众群体」页发布。\n'
     '还是灰的：在「品牌」页的「应用首页」和「应用隐私权政策链接」都填 '
     'https://github.com/MoyerZhang-Claude/video-downloader ，保存后再试。\n'
     '实在发布不了：在「受众群体」页最下面「Add users」把自己的 Gmail 加成测试用户也能用，只是每 7 天要重新连接一次。'),
    ('授权时提示「Google 尚未验证此应用」',
     '这是你自己建的应用，没有提交谷歌审核，所以会这样提示。点「高级」→「转到 …（不安全）」→「继续」即可。'),
    ('找不到下载的 JSON 文件',
     '在浏览器里按 Ctrl + J 打开下载列表，点文件下面的「在文件夹中显示」。'),
    ('连接或上传失败，提示网络错误',
     '谷歌的网站在国内需要能正常访问谷歌的网络环境。浏览器能打开 drive.google.com，软件就能上传。'),
    ('软件会不会动我云盘里的其它文件？',
     '不会。软件只申请了「本软件创建的文件」这一项权限，只能看到、管理它自己上传的文件和文件夹。\n'
     '登录信息加密保存在这台电脑上，只有当前 Windows 用户能解开。密钥文件请不要发给别人。'),
    ('要不要钱？',
     'Google Cloud 项目和这个密钥都是免费的；文件存在你自己的谷歌云盘里，占用你自己的空间。'),
    ('换电脑、换账号怎么办',
     '新电脑上重新「导入密钥文件」再「连接」即可（同一个密钥文件可以继续用）。想换谷歌账号：先「断开」再「连接」。'),
]


def _label(text, name, wrap=True):
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


class CloudHelpDialog(QDialog):
    def __init__(self, parent, notify=None):
        super().__init__(parent)
        self.notify = notify or (lambda text, kind='info': None)
        self.setWindowTitle('谷歌云盘 · 使用教程')
        self.resize(720, 760)
        from .gui import scroll_area
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 24)
        v.setSpacing(6)
        v.addWidget(_label('谷歌云盘 · 使用教程', 'pageTitle'))
        v.addWidget(_label('软件需要用你自己的 Google Cloud 项目创建一个「桌面应用」密钥，才能访问你的谷歌云盘。'
                           '免费，只需要做一次，大约 10 分钟。每一步的链接可以直接打开，也可以复制到浏览器里。', 'rowDesc'))
        v.addSpacing(10)
        for i, (title, desc, url) in enumerate(STEPS, 1):
            v.addSpacing(10)
            v.addWidget(_label(f'第 {i} 步  {title}', 'rowLabel'))
            v.addWidget(_label(desc, 'rowDesc'))
            if url:
                v.addLayout(self._link_row(url))
        v.addSpacing(26)
        v.addWidget(_label('常见问题', 'sectionTitle'))
        for q, a in FAQ:
            v.addSpacing(10)
            v.addWidget(_label(q, 'rowLabel'))
            v.addWidget(_label(a, 'rowDesc'))
        v.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area(body))

    def _link_row(self, url):
        h = QHBoxLayout()
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(6)
        link = _label(url, 'meta', wrap=False)
        h.addWidget(link)
        copy = QPushButton('复制')
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(url), self._copied(copy)))
        openb = QPushButton('打开')
        openb.setCursor(Qt.PointingHandCursor)
        openb.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        h.addWidget(copy)
        h.addWidget(openb)
        h.addStretch(1)
        return h

    @staticmethod
    def _copied(btn):
        btn.setText('已复制')
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: btn.setText('复制'))


def show_help(parent, notify=None):
    dlg = CloudHelpDialog(parent, notify)
    dlg.exec()
