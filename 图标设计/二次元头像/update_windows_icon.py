"""Replace only packaged Windows icons; verify all executable code stays intact."""
import hashlib
import json
import shutil
from pathlib import Path

import pefile
from PyInstaller import config
from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.utils.win32.icon import CopyIcons
from PyInstaller.utils.win32.winutils import update_exe_pe_checksum

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
EXE = ROOT / 'dist/视频解析下载器/视频解析下载器.exe'
STAGED = OUT / 'windows-staging/视频解析下载器.exe'
STAGED.parent.mkdir(exist_ok=True)
config.CONF['workpath'] = str(STAGED.parent)


def inspect(path):
    with pefile.PE(str(path)) as pe:
        sections = {section.Name.decode().rstrip('\0'): hashlib.sha256(section.get_data()).hexdigest()
                    for section in pe.sections if section.Name.rstrip(b'\0') != b'.rsrc'}
        resources = {}
        for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if kind.id in (3, 14):
                continue  # RT_ICON, RT_GROUP_ICON
            for name in kind.directory.entries:
                for language in name.directory.entries:
                    entry = language.data.struct
                    data = pe.get_data(entry.OffsetToData, entry.Size)
                    resources[f'{kind.id}:{name.id}:{language.id}'] = hashlib.sha256(data).hexdigest()
        overlay = hashlib.sha256(pe.get_overlay() or b'').hexdigest()
    archive = CArchiveReader(str(path))
    return dict(sections=sections, non_icon_resources=resources, archive_hash=overlay,
                archive_entry_names=sorted(archive.toc))


before = inspect(EXE)
with pefile.PE(str(EXE)) as original_pe:
    original_payload = original_pe.get_overlay()
shutil.copy2(EXE, STAGED)
CopyIcons(str(STAGED), str(ROOT / 'app/icon.ico'))
# Windows' resource writer drops the appended PyInstaller archive. Reattach
# the identical byte sequence, just as PyInstaller does after adding icons.
with pefile.PE(str(STAGED)) as staged_pe:
    staged_payload = staged_pe.get_overlay()
if not staged_payload:
    with STAGED.open('ab') as f:
        f.write(original_payload)
elif staged_payload != original_payload:
    raise RuntimeError('Unexpected executable overlay; original EXE was not replaced')
update_exe_pe_checksum(str(STAGED))
after = inspect(STAGED)
if before != after:
    raise RuntimeError('Executable content changed beyond icon resources; original EXE was not replaced')
shutil.copy2(STAGED, EXE)
shutil.copy2(ROOT / 'app/icon.ico', ROOT / 'dist/视频解析下载器/_internal/app/icon.ico')
(OUT / 'Windows图标更新校验.json').write_text(json.dumps({
    'result': 'PASS', 'unchanged': ['executable code sections', 'non-icon PE resources', 'entire PyInstaller payload'],
    'original_and_updated': after,
}, ensure_ascii=False, indent=2), encoding='utf-8')
print('PASS: Windows EXE and runtime icon updated; executable code and bundled Python payload are unchanged.')
