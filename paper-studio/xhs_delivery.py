"""Single-note Xiaohongshu delivery with local receipts and no blind retries."""
import hashlib
import json
from pathlib import Path
import time
import uuid

from sources import write_json
from wechat_review import read_json
from xhs_notes import load_note, validate_note

BLOCKED = {'publishing', 'submitted_unverified', 'outcome_unknown', 'unknown', 'published'}


def recover_interrupted(folder):
    path = Path(folder) / 'xhs-submission.json'
    receipt = read_json(path, {})
    if receipt.get('state') == 'publishing':
        receipt.update(state='outcome_unknown', interrupted_at=time.time(),
                       message='上次发布期间程序已关闭，结果未知。请先在小红书核对，不会自动重发。')
        write_json(path, receipt)


def source_stamp(folder):
    """Detect edits to article, metadata, and reused figure paths after preparation."""
    folder = Path(folder).resolve()
    digest = hashlib.sha256()
    for name in ('article.json', 'source.json'):
        path = folder / name
        digest.update(path.read_bytes() if path.is_file() else b'')
    source = read_json(folder / 'source.json', {})
    for figure in source.get('figures', []):
        relative = str(figure.get('image', ''))
        path = (folder / relative).resolve()
        if folder in path.parents and path.is_file():
            stat = path.stat()
            digest.update(f'{relative}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()


def record_source(folder):
    write_json(Path(folder) / 'xhs-source.json', {'stamp': source_stamp(folder)})


def note_status(folder):
    folder = Path(folder)
    note = load_note(folder)
    receipt = read_json(folder / 'xhs-submission.json', {})
    stale = bool(note and read_json(folder / 'xhs-source.json', {}).get('stamp') != source_stamp(folder))
    return {'note': note or None, 'receipt': receipt, 'stale': stale,
            'message': '论文正文或素材已变化，请重新生成小红书笔记后再发布。' if stale else ''}


def require_publishable(folder):
    state = note_status(folder)
    if state['receipt'].get('state') in BLOCKED:
        raise ValueError('已有小红书提交记录。请先核对小红书后台，避免重复发布。')
    if not state['note']:
        raise ValueError('请先生成并保存小红书图文笔记。')
    if state['stale']:
        raise ValueError(state['message'])
    validate_note(folder, state['note'])
    return state['note']


def publish_note(folder, connector, account_id, progress=lambda _: None, cancelled=lambda: False):
    folder = Path(folder)
    note = require_publishable(folder)
    progress('确认小红书登录账号…')
    identity = connector.check_login()
    account = identity.get('account') or {}
    if not identity.get('logged_in') or not account.get('id'):
        raise ValueError('请先扫码登录并确认小红书账号。')
    if not account_id or account.get('id') != account_id:
        raise ValueError('小红书登录账号已变化，请重新确认目标账号后发布。')
    if cancelled():
        raise ValueError('已取消；尚未向小红书发送内容。')
    cards = {item['id']: item for item in note['cards']}
    payload = {key: note[key] for key in ('title', 'content', 'tags', 'visibility')}
    payload['images'] = [str((folder / cards[ident]['image']).resolve()) for ident in note['image_ids']]
    payload['is_original'] = False
    receipt = {'state': 'publishing', 'attempt_id': uuid.uuid4().hex, 'started_at': time.time(),
               'account': {'id': account['id'], 'name': str(account.get('name', ''))[:100]},
               'title': note['title'], 'visibility': note['visibility'],
               'message': '已开始提交。提交期间请勿切换账号；重复操作已锁定。'}
    write_json(folder / 'xhs-submission.json', receipt)
    progress('正在上传图片并提交小红书笔记；结束前请勿切换账号…')
    try:
        connector.publish(payload)
    except Exception:
        receipt.update(state='outcome_unknown', completed_at=time.time(),
                       message='本次提交结果未知。请到小红书创作后台核对；软件不会自动重发。')
        write_json(folder / 'xhs-submission.json', receipt)
        raise ValueError(receipt['message']) from None
    receipt.update(state='submitted_unverified', completed_at=time.time(),
                   message='连接器已提交，请到小红书核实笔记及可见范围。尚未取得可验证的笔记链接。')
    write_json(folder / 'xhs-submission.json', receipt)
    progress(receipt['message'])
    return receipt


def reset_submission(folder, confirmed):
    if confirmed is not True:
        raise ValueError('请先核对小红书后台是否已有本篇笔记，再确认允许重新发布。')
    folder = Path(folder)
    receipt = read_json(folder / 'xhs-submission.json', {})
    if receipt:
        archive = folder / 'xhs-receipts'
        archive.mkdir(exist_ok=True)
        write_json(archive / (uuid.uuid4().hex + '.json'), receipt)
    write_json(folder / 'xhs-submission.json', {})
    return note_status(folder)
