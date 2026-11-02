"""Local technical preparation for user-selected articles; draft-only authority.

An automated report is not a human approval or a scientific correctness claim.
"""
import time
from pathlib import Path

from PIL import Image

from covers import ensure_overview_cover
from formatting import current_baoyu
from sources import write_json
from wechat_api import image_file, submit_automated_draft, recheck
from wechat_fields import adapt_fields, record_adjustments
from wechat_review import asset_path, asset_review_warnings, clean_options, prepare, read_json
from writing import validate_article

DEFAULT_EDITOR = 'Paper Studio'
OPTION_KEYS = {'title', 'digest', 'author', 'cover_id', 'theme', 'baoyu_theme', 'source_url'}
RECEIPT_STATES = {'unknown', 'submitted', 'submitted_unverified', 'uploading'}
REPORT_FILE = 'delivery-checks.json'
COVER_FILE = 'delivery-cover.json'


def receipt_blocker(folder):
    receipt = read_json(Path(folder)/'wechat-submission.json', {})
    if receipt.get('state') in RECEIPT_STATES:
        return '已有提交记录，请查看公众号草稿箱或重新核对已有草稿；不会重复创建。'
    return ''


def selected_cover(folder, source, ident):
    """Only preserve an existing, readable selection, not an invalid ID/path."""
    found = next((f for f in source.get('figures', [])+source.get('covers', []) if f.get('id') == ident), None)
    if not found:
        return None
    try:
        with Image.open(asset_path(folder, found['image'])) as image:
            image.verify()
    except (ValueError, OSError, KeyError, Image.DecompressionBombError):
        return None
    return found


def mark_manual_cover(folder, ident):
    """An explicit cover selection remains deliberate even when it selects our cover."""
    write_json(Path(folder)/COVER_FILE, {'mode':'manual', 'cover_id':ident})


def delivery_cover(folder, source, article, options, overrides=None):
    owner = read_json(Path(folder)/COVER_FILE, {})
    selected = selected_cover(folder, source, options.get('cover_id'))
    explicit = isinstance(overrides, dict) and 'cover_id' in overrides
    automatic = owner.get('mode') == 'automatic' and owner.get('cover_id') == options.get('cover_id')
    if selected and (explicit or not automatic):
        if explicit:
            mark_manual_cover(folder, selected['id'])
        return selected
    theme = options.get('theme', 'ink')
    cover = ensure_overview_cover(folder, source, article, editor=DEFAULT_EDITOR,
                                  theme=theme if theme in {'ink', 'blue', 'orange'} else 'ink')
    write_json(Path(folder)/COVER_FILE, {'mode':'automatic', 'cover_id':cover['id']})
    return cover


def public_report(report):
    return {key: report.get(key) for key in ('ready', 'blockers', 'warnings', 'stages', 'editor', 'cover', 'checked_at')}


def current_report(folder, article, source, options, account):
    """Read-only readiness view; invalidate checks after any payload/asset change."""
    report = read_json(Path(folder)/REPORT_FILE, {})
    if not report:
        report = {'ready': False, 'blockers': [], 'warnings': [], 'stages': [],
                  'editor': DEFAULT_EDITOR, 'cover': {}, 'checked_at': None}
        report['blockers'].append('点击“自动准备”或“一键投递草稿”，软件会整理封面、排版并检查投递条件。')
    else:
        report = dict(report, blockers=list(report.get('blockers', [])))
        try:
            now = prepare(folder, article, source, options, account, automatic=True)
            if report.get('snapshot') != now:
                report['ready'] = False
                report['blockers'] = ['稿件、配图、排版或目标账号已变化；请重新自动准备或直接一键投递。']
        except (ValueError, KeyError, TypeError, OSError):
            report['ready'] = False
    blocker = receipt_blocker(folder)
    if blocker:
        report['ready'] = False
        report['blockers'] = [blocker]
    return public_report(report)


def prepare_delivery(studio, job, overrides=None, progress=lambda _: None, account=None):
    """Adapt delivery fields and validate existing content, without rewriting it."""
    folder = studio.folder(job)
    account = account if account is not None else studio.wechat.public()
    report = {'ready': False, 'blockers': [], 'warnings': [], 'stages': [],
              'editor': DEFAULT_EDITOR, 'cover': {}, 'checked_at': time.time()}
    prepared = None

    def stage(ident, label, status='complete', detail=''):
        report['stages'].append({'id': ident, 'label': label, 'status': status, 'detail': detail})

    try:
        blocker = receipt_blocker(folder)
        if blocker:
            raise ValueError(blocker)
        progress('自动准备 1/4 · 检查已保存的文章与配图引用…')
        article = studio.load(job, 'article.json')
        source = studio.load(job)
        audit = validate_article(article, source)
        report['warnings'] = list(dict.fromkeys(item['message'] for item in audit.get('issues', [])))
        report['warnings'].append('自动检查不证明论文结论或图文解读正确；可在公众号草稿箱继续编辑和复核。')
        stage('article', '文章结构与引用')

        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError('提交设置必须是对象。')
        saved = read_json(folder/'wechat-options.json', {})
        options = {'author': DEFAULT_EDITOR, 'theme': 'ink', **saved,
                   **{k: v for k, v in (overrides or {}).items() if k in OPTION_KEYS}}
        options, adjustments = adapt_fields(options, article)
        progress('自动准备 2/4 · 选择 overview 封面，整理标题与摘要…')
        cover = delivery_cover(folder, source, article, options, overrides)
        options['cover_id'] = cover['id']
        report['cover'] = {k: cover.get(k) for k in ('id', 'image', 'caption', 'selection_reason', 'selection_note', 'source_figure_id', 'editor')}
        if cover.get('selection_reason') in {'figure_fallback', 'editorial_fallback'}:
            report['warnings'].append(cover.get('selection_note') or '未识别到 overview，已使用可用封面。')
        options = clean_options(options, article, source)
        write_json(folder/'wechat-options.json', options)
        record_adjustments(folder, adjustments)
        stage('cover', '封面与投递字段', detail=cover.get('selection_note', '沿用已选封面。'))

        progress('自动准备 3/4 · 排版并检查全部待上传图片…')
        if options['theme'] == 'baoyu':
            try:
                current_baoyu(folder, article, source, options)
            except (ValueError, OSError, KeyError, TypeError):
                try:
                    studio.format_skill(job, progress)
                    current_baoyu(folder, article, source, options)
                except (ValueError, OSError, KeyError, TypeError):
                    # No valid cache exists. Keep all content and expose fallback.
                    options['theme'] = 'ink'
                    write_json(folder/'wechat-options.json', options)
                    report['warnings'].append('宝玉排版当前不可用，已改用内置松墨排版；正文和配图保留。')
        prepared = prepare(folder, article, source, options, account, automatic=True)
        if prepared.get('parts'):
            report['warnings'].append(f'全文超出单篇接口容量，已无损分为 {len(prepared["parts"])} 篇有序图文，将创建同一份多图文草稿。原稿不变，正文、表格和配图均保留。')
        report['warnings'].extend(asset_review_warnings(prepared, source))
        for asset in prepared['assets']:
            path = asset_path(folder, asset['image'])
            try:
                with Image.open(path) as image:
                    if image.width*image.height > 25_000_000:
                        raise ValueError('图片像素过大，请在配图页重新裁剪。')
                    if image.format != 'PNG':
                        raise ValueError('待上传图片格式异常，请重新导入 PNG 图片。')
                    image.verify()
                image_file(path)
            except (OSError, Image.DecompressionBombError):
                raise ValueError('待上传图片无法读取，请在原文与配图页重新导入或裁剪。') from None
        if prepared['video_count']:
            report['warnings'].append('项目视频保留来源链接；视频文件需在公众号后台插入。')
        stage('layout', '排版、图片与正文长度')
        progress('自动准备 4/4 · 检查目标公众号与重复投递记录…')
        if not account.get('appid'):
            raise ValueError('请先配置目标公众号。')
        if receipt_blocker(folder):
            raise ValueError(receipt_blocker(folder))
        stage('account', '目标公众号与投递记录')
        report.update(ready=True, snapshot=prepared)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        report['blockers'].append(str(exc) if isinstance(exc, ValueError) else '文章或素材结构不完整，请检查已保存稿件。')
        stage('blocked', '需要处理', 'blocked', report['blockers'][-1])
    report['checked_at'] = time.time()
    write_json(folder/REPORT_FILE, report)
    return public_report(report), prepared


def deliver_article(studio, job, account, overrides=None, progress=lambda _: None):
    """Fresh preparation plus separate, expiring automated draft permission."""
    folder = studio.folder(job)
    receipt = read_json(folder/'wechat-submission.json', {})
    if receipt.get('state') == 'submitted_unverified' and receipt.get('media_id'):
        progress('草稿已创建；正在重新读回已有草稿，不会重复创建。')
        return recheck(folder, account, progress, allow_unverified=True)
    report, prepared = prepare_delivery(studio, job, overrides, progress, account.public())
    if not report['ready']:
        raise ValueError('；'.join(report['blockers']))
    progress('自动检查完成 · 正在投递到公众号草稿箱…')
    # Rebuild immediately before granting authority to detect a changed payload.
    current = studio.wechat_prepared(job, automatic=True)
    if current != prepared:
        raise ValueError('文章或目标账号已改变，请重新一键投递。')
    write_json(folder/'automatic-draft-permit.json', {
        'mode': 'automatic_draft_only', 'created_at': time.time(), 'checks_passed': True,
        'snapshot': prepared, 'editor': DEFAULT_EDITOR,
        'review_destination': 'WeChat draft box; not human reviewed'})
    return submit_automated_draft(folder, prepared, account, progress)
