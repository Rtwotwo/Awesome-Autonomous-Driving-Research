"""Local, evidence-preserving Xiaohongshu notes and portrait image cards.

This module does not call a model or a social platform. Prepared text is an
explicit excerpt of the saved manuscript, not a new assessment of the paper.
"""
import copy
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from covers import font, wrap
from figure_plan import roles, usable
from publication import clean_public_text, paper_details, resource_url
from sources import write_json
from wechat_review import asset_path

NOTE_FILE = 'xhs-note.json'
TITLE_LIMIT, CONTENT_LIMIT, IMAGE_LIMIT = 20, 1000, 9
CARD_PATTERN = r'xhs/card-[0-9a-f]{16}\.png'
BG, INK, ACCENT, MUTED = '#f2f4ed', '#204f42', '#d97448', '#778b7b'
WIDTH, HEIGHT, MARGIN = 1080, 1440, 72
EDITABLE = {'title', 'content', 'tags', 'visibility', 'editor', 'image_ids'}


def _text(value):
    text = clean_public_text(value if isinstance(value, str) else '')
    text = re.sub(r'\[([^\]]+)\]\((?:https?://)[^)]+\)', r'\1', text)
    return re.sub(r'[*_`]{1,3}', '', text).strip()


def _excerpt(value, limit):
    """Retain complete sentences where possible; mark any clipped excerpt."""
    text = _text(value)
    if len(text) <= limit:
        return text
    points = [m.end() for m in re.finditer(r'[。！？][”’」』）)]*|[.!?]["\')]*(?=\s|$)', text[:limit])]
    if points and points[-1] >= min(30, limit // 3):
        return text[:points[-1]].strip()
    prefix = text[:max(0, limit - 1)].rstrip()
    # Avoid half an English token or decimal in a visibly marked excerpt.
    prefix = re.sub(r'[A-Za-z0-9_.+-]+$', '', prefix).rstrip()
    return (prefix or text[:max(0, limit - 1)]).rstrip() + '…'


def _local_dir(folder):
    folder = Path(folder).resolve()
    directory = folder / 'xhs'
    if not directory.resolve().is_relative_to(folder):
        raise ValueError('小红书素材目录必须位于当前作品中。')
    directory.mkdir(exist_ok=True)
    return directory


def _card_path(folder, name):
    if not isinstance(name, str) or not re.fullmatch(CARD_PATTERN, name):
        raise ValueError('小红书素材路径无效，请重新生成图文。')
    folder = Path(folder).resolve()
    path = (folder / name).resolve()
    if not path.is_relative_to(folder) or not path.is_file():
        raise ValueError('小红书素材不存在，请重新生成图文。')
    return path


def _check_fields(note):
    for name in ('title', 'content', 'editor', 'visibility'):
        if not isinstance(note.get(name), str):
            raise ValueError('小红书标题、正文、署名和可见范围需要文字。')
        if any(ord(char) < 32 and not (name == 'content' and char in '\n\t') for char in note[name]):
            raise ValueError('标题或署名不能换行，文字中不能包含控制字符。')
    if not note['title'].strip() or len(note['title']) > TITLE_LIMIT:
        raise ValueError('小红书标题需为 1–20 个字符，请调整后保存。')
    if not note['content'].strip():
        raise ValueError('小红书正文不能为空。')
    if not note['editor'].strip() or len(note['editor']) > 30:
        raise ValueError('编辑署名需为 1–30 个字符。')
    if note['visibility'] not in {'公开可见', '仅自己可见'}:
        raise ValueError('请选择公开可见或仅自己可见。')
    tags = note.get('tags')
    if (not isinstance(tags, list) or len(tags) > 10 or
            any(not isinstance(tag, str) or not re.fullmatch(r'[\w\u3400-\u9fff-]{1,20}', tag) for tag in tags)
            or len(tags) != len(set(tags))):
        raise ValueError('话题需为不重复的文字列表，每项 1–20 字，不包含 #、空格或标点。')
    if len(note['content'] + ('\n\n' + ' '.join('#' + tag for tag in tags) if tags else '')) > CONTENT_LIMIT:
        raise ValueError('小红书正文与话题合计最多 1000 个字符，请精简后保存；原稿不会改变。')


def validate_note(folder, note):
    """Validate the actual local payload, including server-owned image paths."""
    if not isinstance(note, dict) or note.get('version') != 1:
        raise ValueError('小红书图文格式不正确，请重新准备。')
    _check_fields(note)
    cards, chosen = note.get('cards'), note.get('image_ids')
    if not isinstance(cards, list) or not 1 <= len(cards) <= IMAGE_LIMIT:
        raise ValueError('小红书素材需要 1–9 张图片。')
    known = {}
    for card in cards:
        if not isinstance(card, dict) or not re.fullmatch(r'card-[0-9a-f]{16}', str(card.get('id', ''))):
            raise ValueError('小红书素材标识无效。')
        if card['id'] in known or card.get('image') != 'xhs/' + card['id'] + '.png':
            raise ValueError('小红书素材标识重复或路径不匹配。')
        path = _card_path(folder, card['image'])
        try:
            with Image.open(path) as image:
                if image.format != 'PNG' or image.size != (WIDTH, HEIGHT):
                    raise ValueError('小红书素材需要完整的 1080 × 1440 PNG 图卡。')
                image.verify()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ValueError('小红书图卡损坏，请重新生成。') from exc
        known[card['id']] = card
    if (not isinstance(chosen, list) or not 1 <= len(chosen) <= IMAGE_LIMIT
            or any(not isinstance(ident, str) or ident not in known for ident in chosen)
            or len(set(chosen)) != len(chosen)):
        raise ValueError('请选择 1–9 张已生成的图片，不能重复。')
    return note


def load_note(folder):
    path = Path(folder) / NOTE_FILE
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise ValueError('小红书笔记文件过大。')
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ValueError('小红书笔记无法读取，请重新准备。') from exc
    return validate_note(folder, result)


def _new_card(folder, kind, title, caption=''):
    _local_dir(folder)
    ident = 'card-' + uuid.uuid4().hex[:16]
    return {'id': ident, 'image': 'xhs/' + ident + '.png', 'kind': kind,
            'title': title, 'caption': caption}


def _canvas(kicker, editor, number=None):
    image = Image.new('RGB', (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((MARGIN, 65, MARGIN + 56, 73), radius=4, fill=ACCENT)
    draw.text((MARGIN, 102), kicker, font=font(25, True), fill=INK)
    draw.line((MARGIN, HEIGHT - 116, WIDTH - MARGIN, HEIGHT - 116), fill='#d5dfd2', width=2)
    draw.text((MARGIN, HEIGHT - 90), '论文研读 · ' + editor, font=font(24), fill=INK)
    # Cards can be reordered or omitted later; no baked-in page numbers.
    return image, draw


def _paragraph(draw, text, x, y, width, size=34, line_gap=17, fill=INK, max_lines=None, bold=False):
    face = font(size, bold)
    lines = wrap(draw, text, face, width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + '…', font=face) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += '…'
    for line in lines:
        draw.text((x, y), line, font=face, fill=fill)
        y += size + line_gap
    return y


def _eligible_figures(folder, source, article):
    refs = {item.get('id') for section in article.get('sections', [])
            for item in section.get('figures', []) if isinstance(item, dict)}
    candidates = []
    for figure in source.get('figures', []):
        if not isinstance(figure, dict) or not (figure.get('selected') or figure.get('id') in refs):
            continue
        try:
            path = asset_path(folder, figure['image'])
            with Image.open(path) as image:
                if image.width * image.height > 25_000_000 or getattr(image, 'n_frames', 1) != 1:
                    continue
                image.verify()
            if not usable(figure, folder):
                continue
        except (ValueError, OSError, KeyError, Image.DecompressionBombError):
            continue
        caption = figure.get('original_caption') or figure.get('caption', '')
        score = 4 if re.search(r'\boverview\b|overall|总体|整体|概览', caption, re.I) else 0
        score += 3 if 'method' in roles(figure) else (2 if 'results' in roles(figure) else 1)
        candidates.append((score, figure))
    return [item[1] for item in sorted(candidates, key=lambda item: -item[0])]


def _paste_complete(canvas, path, panel):
    """Contain the source image, preserving axes, legends and surrounding labels."""
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(panel, radius=20, fill='white', outline='#d7e1d5', width=2)
    with Image.open(path) as original:
        image = original.convert('RGBA')
        image.thumbnail((panel[2] - panel[0] - 34, panel[3] - panel[1] - 34), Image.Resampling.LANCZOS)
        x, y = (panel[0] + panel[2] - image.width) // 2, (panel[1] + panel[3] - image.height) // 2
        canvas.paste(image, (x, y), image)


def _render_cover(folder, article, source, note, figure=None):
    card = _new_card(folder, 'cover', note['title'], '论文研读封面；原图完整适配，非新增科研图。')
    canvas, draw = _canvas('PAPER STUDIO / RESEARCH NOTES', note['editor'], 1)
    y = _paragraph(draw, note['title'], MARGIN, 190, WIDTH - MARGIN * 2, 68, 22, max_lines=3, bold=True)
    details = paper_details(article, source)
    _paragraph(draw, _text(details['title']), MARGIN, y + 30, WIDTH - MARGIN * 2, 28, 12, fill=MUTED, max_lines=4)
    if figure:
        card['source_figure_id'] = figure['id']
        _paste_complete(canvas, asset_path(folder, figure['image']), (MARGIN, 660, WIDTH - MARGIN, 1195))
        draw.text((MARGIN, 1222), '论文原图 · 完整适配', font=font(24), fill=MUTED)
    else:
        draw.rounded_rectangle((MARGIN, 735, WIDTH - MARGIN, 1125), radius=25, fill='#e3ebde')
        _paragraph(draw, '从研究问题\n到方法与证据', MARGIN + 46, 800, 780, 52, 28, bold=True)
        draw.text((MARGIN, 1180), '文字导读 · 本组未采用论文原图', font=font(25), fill=MUTED)
    canvas.save(Path(folder) / card['image'])
    return card


def _render_text(folder, title, paragraphs, editor, number, kind='excerpt'):
    card = _new_card(folder, kind, title, '摘自已保存稿件，保留具体实验条件与结论边界。')
    canvas, draw = _canvas('READING NOTES / 稿件摘录', editor, number)
    y = _paragraph(draw, title, MARGIN, 190, WIDTH - MARGIN * 2, 52, 20, max_lines=2, bold=True) + 45
    for index, text in enumerate(paragraphs[:3]):
        if y > 1080:
            break
        draw.text((MARGIN, y + 7), f'{index + 1:02}', font=font(26, True), fill=ACCENT)
        y = _paragraph(draw, _excerpt(text, 220), MARGIN + 65, y, WIDTH - MARGIN * 2 - 65,
                       42, 20, max_lines=max(1, min(4 if len(paragraphs) > 2 else 6, (1215 - y) // 62))) + 40
    canvas.save(Path(folder) / card['image'])
    return card


def _render_figure(folder, figure, explanation, editor, number):
    caption = _text(figure.get('original_caption') or figure.get('caption', ''))
    role = roles(figure)
    title = '方法与总体架构' if 'method' in role else ('定性展示' if 'qualitative' in role else ('结果与指标' if 'results' in role else '论文原图'))
    card = _new_card(folder, 'figure', title, caption)
    card['source_figure_id'] = figure['id']
    canvas, draw = _canvas('FROM THE PAPER / 原图', editor, number)
    _paragraph(draw, title, MARGIN, 185, WIDTH - MARGIN * 2, 52, 20, bold=True)
    _paste_complete(canvas, asset_path(folder, figure['image']), (MARGIN, 290, WIDTH - MARGIN, 1010))
    # Captions remain attributed and excerpts are visually marked; never crop the data.
    y = _paragraph(draw, _excerpt(caption, 160), MARGIN, 1045, WIDTH - MARGIN * 2, 25, 12, max_lines=3, fill=MUTED)
    if explanation and y < 1190:
        _paragraph(draw, _excerpt(explanation, 90), MARGIN, y + 14, WIDTH - MARGIN * 2, 25, 12, max_lines=2)
    canvas.save(Path(folder) / card['image'])
    return card


def _render_reference(folder, details, editor, number):
    card = _new_card(folder, 'references', '论文与资源', '论文、作者与资源链接来自当前作品资料卡。')
    canvas, draw = _canvas('SOURCE / 回到原文', editor, number)
    y = _paragraph(draw, '论文与资源', MARGIN, 190, WIDTH - MARGIN * 2, 56, 22, bold=True) + 40
    y = _paragraph(draw, _text(details['title']), MARGIN, y, WIDTH - MARGIN * 2, 32, 14, max_lines=6) + 35
    authors = '、'.join(_text(name) for name in details.get('authors', []) if isinstance(name, str))
    if authors:
        y = _paragraph(draw, '作者：' + _excerpt(authors, 150), MARGIN, y, WIDTH - MARGIN * 2, 27, 12, max_lines=3, fill=MUTED) + 28
    for label, key in [('论文', 'paper_url'), ('项目', 'project_url'), ('代码', 'code_url')]:
        value = resource_url(details.get(key, ''))
        if value and y < 1160:
            y = _paragraph(draw, label + '：' + value, MARGIN, y, WIDTH - MARGIN * 2, 25, 10, max_lines=3) + 24
    canvas.save(Path(folder) / card['image'])
    return card


def _default_title(article, source):
    candidates = [article.get('title')] + (article.get('title_options') or [])
    for candidate in candidates:
        if isinstance(candidate, str) and 0 < len(_text(candidate)) <= TITLE_LIMIT:
            return _text(candidate), False
    title = _text(article.get('title') or source.get('title') or '论文研读')
    return _excerpt(title, TITLE_LIMIT), len(title) > TITLE_LIMIT


def _default_content(article, details, tags):
    ending = []
    paper_title = _text(details.get('title', ''))
    if paper_title:
        ending.append('论文：' + _excerpt(paper_title, 180))
    for label, key in [('原文', 'paper_url'), ('项目', 'project_url'), ('代码', 'code_url')]:
        url = resource_url(details.get(key, ''))
        if url and len(url) <= 180:
            ending.append(label + '：' + url)
    suffix = '\n'.join(ending)
    remaining = CONTENT_LIMIT - len('\n\n' + ' '.join('#' + tag for tag in tags)) - len(suffix) - 2
    parts = []
    lead = _text(article.get('digest') or article.get('lead') or '')
    if lead:
        parts.append(_excerpt(lead, min(300, remaining)))
    for takeaway in (article.get('takeaways') or [])[:3]:
        text = _text(takeaway)
        available = remaining - len('\n\n'.join(parts)) - 4
        if text and available > 35:
            parts.append('· ' + _excerpt(text, min(150, available)))
    if not parts:
        parts.append('这篇论文的研究问题、方法与实验整理在配图中。以下内容摘自已保存稿件，完整论证请参阅原文。')
    return '\n\n'.join(parts + ([suffix] if suffix else []))


def _default_tags(source):
    title = source.get('title', '')
    tags = ['论文阅读', '人工智能']
    for pattern, label in [(r'driv|autonomous', '自动驾驶'), (r'world.model', '世界模型'),
                           (r'embod|robot', '具身智能'), (r'agent|self.improv', 'Agent'),
                           (r'inference|serving|kernel', 'AIInfra')]:
        if re.search(pattern, title, re.I):
            tags.append(label)
    return tags


def _diverse_figures(figures):
    if not figures:
        return []
    result = [figures[0]]
    for role in ('results', 'qualitative'):
        found = next((figure for figure in figures if role in roles(figure) and figure not in result), None)
        if found:
            result.append(found)
    result.extend(figure for figure in figures if figure not in result)
    return result


def _apply_values(note, values):
    if not isinstance(values, dict) or set(values) - EDITABLE:
        raise ValueError('小红书笔记只接受标题、正文、话题、署名、可见范围及已有图片顺序。')
    for key, value in values.items():
        note[key] = value.strip() if isinstance(value, str) else copy.deepcopy(value)
    _check_fields(note)


def prepare_note(folder, article, source, overrides=None):
    if not isinstance(article, dict) or not article.get('sections'):
        raise ValueError('请先生成并保存完整论文稿件，再准备小红书图文。')
    folder = Path(folder)
    details = paper_details(article, source)
    title, shortened = _default_title(article, source)
    tags = _default_tags(source)
    note = {'version': 1, 'title': title, 'content': _default_content(article, details, tags),
            'tags': tags, 'visibility': '公开可见', 'editor': 'Paper Studio', 'cards': [],
            'image_ids': [], 'warnings': ['文字为已保存稿件的节选整理，请在发布前检查语义和配图。'],
            'prepared_at': time.time()}
    if shortened:
        note['warnings'].append('原题超过平台标题长度，已生成短版；完整题名保留在资料图卡中。')
    if overrides:
        if 'image_ids' in overrides:
            raise ValueError('重新生成时请先使用默认图序，生成后再选择图片。')
        _apply_values(note, overrides)
    figures = _eligible_figures(folder, source, article)
    if not figures:
        note['warnings'].append('没有已选用且可确认完整的论文原图，本次仅生成文字卡片；可先补充或审核原图再生成。')
    elif not any('method' in roles(figure) for figure in figures):
        note['warnings'].append('未识别到已选中的 overview／方法图，封面使用首张可用原图。')
    cards = [_render_cover(folder, article, source, note, figures[0] if figures else None)]
    takeaway = [_text(value) for value in article.get('takeaways', []) if isinstance(value, str) and _text(value)]
    if takeaway:
        cards.append(_render_text(folder, '这篇论文值得看什么', takeaway[:3], note['editor'], len(cards) + 1))
    for category, label in [('method', '方法：如何解决问题'), ('experiments', '实验：依据与边界')]:
        sections = [section for section in article['sections'] if section.get('category') == category or section.get('kind') == category]
        if not sections:
            pattern = r'方法|架构|机制|method|architecture' if category == 'method' else r'实验|结果|边界|experiment|result'
            sections = [section for section in article['sections'] if re.search(pattern, section.get('heading', ''), re.I)]
        paragraphs = [p['text'] for section in sections for p in section.get('paragraphs', []) if isinstance(p, dict) and p.get('text')]
        if paragraphs:
            cards.append(_render_text(folder, label, paragraphs[:2], note['editor'], len(cards) + 1))
    explanations = {item.get('id'): _text(item.get('explanation', '')) for section in article['sections']
                    for item in section.get('figures', []) if isinstance(item, dict)}
    figure_slots = max(0, IMAGE_LIMIT - len(cards) - 1)
    chosen_figures = _diverse_figures(figures)[:figure_slots]
    if len(chosen_figures) < len(figures):
        note['warnings'].append(f'当前共 {len(figures)} 张可用原图，轮播中精选 {len(chosen_figures)} 张，优先保留总体架构、指标与定性展示；其余仍在原稿中。')
    for figure in chosen_figures:
        cards.append(_render_figure(folder, figure, explanations.get(figure['id'], ''), note['editor'], len(cards) + 1))
    if len(cards) == 1:
        text = _text(article.get('digest') or article.get('lead'))
        if text:
            cards.append(_render_text(folder, '研究导读', [text], note['editor'], len(cards) + 1))
    cards.append(_render_reference(folder, details, note['editor'], len(cards) + 1))
    note['cards'] = cards
    note['image_ids'] = [card['id'] for card in cards]
    validate_note(folder, note)
    write_json(folder / NOTE_FILE, note)
    return note


def save_note(folder, article, source, values):
    original = load_note(folder)
    if not original:
        raise ValueError('请先准备小红书图文。')
    note = copy.deepcopy(original)
    _apply_values(note, values)
    # Validate selection before any file mutation. Input never controls asset paths.
    validate_note(folder, note)
    if note['title'] != original['title'] or note['editor'] != original['editor']:
        cover = next((card for card in note['cards'] if card['kind'] == 'cover'), None)
        if cover:
            figures = _eligible_figures(folder, source, article)
            chosen = next((figure for figure in figures if figure['id'] == cover.get('source_figure_id')), None)
            replacement = _render_cover(folder, article, source, note, chosen)
            note['cards'] = [replacement if card['id'] == cover['id'] else card for card in note['cards']]
            note['image_ids'] = [replacement['id'] if ident == cover['id'] else ident for ident in note['image_ids']]
        if note['editor'] != original['editor']:
            # The footer is editorial decoration outside the original figure panel.
            # Redraw only that strip, keeping source-derived content untouched.
            for number, card in enumerate(note['cards'], 1):
                if card['kind'] == 'cover':
                    continue
                replacement = copy.deepcopy(card)
                replacement['id'] = 'card-' + uuid.uuid4().hex[:16]
                replacement['image'] = 'xhs/' + replacement['id'] + '.png'
                with Image.open(_card_path(folder, card['image'])) as old_image:
                    canvas = old_image.convert('RGB')
                draw = ImageDraw.Draw(canvas)
                draw.rectangle((0, HEIGHT - 112, WIDTH, HEIGHT), fill=BG)
                draw.text((MARGIN, HEIGHT - 90), '论文研读 · ' + note['editor'], font=font(24), fill=INK)
                canvas.save(Path(folder) / replacement['image'])
                note['cards'][number - 1] = replacement
                note['image_ids'] = [replacement['id'] if ident == card['id'] else ident for ident in note['image_ids']]
    note['updated_at'] = time.time()
    validate_note(folder, note)
    write_json(Path(folder) / NOTE_FILE, note)
    return note


def export_note(folder):
    note = load_note(folder)
    if not note:
        raise ValueError('请先准备小红书图文。')
    by_id = {card['id']: card for card in note['cards']}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        text = note['title'] + '\n\n' + note['content']
        if note['tags']:
            text += '\n\n' + ' '.join('#' + tag for tag in note['tags'])
        archive.writestr('note.txt', text + '\n')
        public_note = copy.deepcopy(note)
        public_note['cards'] = []
        for number, ident in enumerate(note['image_ids'], 1):
            card = copy.deepcopy(by_id[ident])
            name = f'images/{number:02}-{ident}.png'
            archive.writestr(name, _card_path(folder, card['image']).read_bytes())
            card['image'] = name
            public_note['cards'].append(card)
        archive.writestr('note.json', json.dumps(public_note, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', '小红书图文素材包\n\n按 images 中的序号上传图片，使用 note.txt 的标题、正文与话题。\n导出不代表已发布。请检查作者署名、实验条件和原图来源；素材包不包含账号或登录凭据。\n')
    return buffer.getvalue()
