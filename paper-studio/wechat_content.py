"""Lossless balanced-HTML parts for the WeChat article-content limit."""
from html import escape, unescape
from html.parser import HTMLParser
import re

CHAR_LIMIT = 20000
BYTE_LIMIT = 1_000_000
PLAN_LIMIT = CHAR_LIMIT
MAX_PARTS = 8
IMAGE_URL_RESERVE = 1024
VOID = {'img', 'br', 'hr', 'wbr'}


class Tree(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=False)
        self.root = {'tag': '', 'open': '', 'children': []}
        self.stack = [self.root]
        self.feed(content)
        self.close()
        if len(self.stack) != 1:
            raise ValueError('投递排版标签未闭合，请重新排版；原稿保留。')

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'open': self.get_starttag_text(), 'children': []}
        self.stack[-1]['children'].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1]['children'].append({'tag': tag, 'open': self.get_starttag_text(), 'children': [], 'void': True})

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if len(self.stack) == 1 or self.stack[-1]['tag'] != tag:
            raise ValueError('投递排版标签不匹配，请重新排版；原稿保留。')
        self.stack.pop()

    def handle_data(self, text):
        self.stack[-1]['children'].append(text)

    def handle_entityref(self, name):
        self.handle_data('&' + name + ';')

    def handle_charref(self, name):
        self.handle_data('&#' + name + ';')


def closing(node):
    return '</' + node['tag'] + '>' if node['tag'] and node['tag'] not in VOID and not node.get('void') else ''


def render(node):
    if isinstance(node, str):
        return node
    return node['open'] + ''.join(render(child) for child in node['children']) + closing(node)


class Signature(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.text = []; self.images = []
        self.feed(content)

    def handle_data(self, text):
        self.text.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.images.append(dict(attrs).get('src', ''))


def planned_size(content):
    # Returned CDN addresses are longer than local paths. Still recheck actual
    # uploaded URLs before the only draft/add call; this is a planning reserve.
    images = Signature(content).images
    return len(content) + sum(max(0, IMAGE_URL_RESERVE - len(escape(src, quote=True))) for src in images)


def fits(content, limit=PLAN_LIMIT):
    return planned_size(content) < limit and len(content.encode('utf-8')) < BYTE_LIMIT


def check_final_content(content):
    if len(content) >= CHAR_LIMIT or len(content.encode('utf-8')) >= BYTE_LIMIT:
        raise ValueError('该分篇在替换微信图片地址后仍超出接口限制；完整原稿保留，尚未创建草稿。')


def grouped(children):
    """Keep headings with following text and figures/tables with their captions."""
    groups = []; index = 0
    while index < len(children):
        group = [children[index]]; node = children[index]; index += 1
        tag = node.get('tag') if isinstance(node, dict) else ''
        extra = 1 if tag in {'h1', 'h2', 'h3', 'h4', 'table'} else 2 if '<img' in render(node) else 0
        while extra and index < len(children):
            child = children[index]
            if not isinstance(child, dict) or child['tag'] not in {'p', 'blockquote'}:
                break
            group.append(child); index += 1; extra -= 1
        groups.append(group)
    return groups


def split_text(text, overhead):
    text = unescape(text); chunks = []
    while text:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = escape(text[:middle], quote=False)
            if overhead + len(candidate) < PLAN_LIMIT and overhead + len(candidate.encode('utf-8')) < BYTE_LIMIT:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise ValueError('单个排版元素过大，无法无损分篇；原稿保留。')
        end = low
        if end < len(text):
            stops = list(re.finditer(r'[。！？.!?；;\n]', text[:end]))
            if stops and stops[-1].end() > end // 2:
                end = stops[-1].end()
        chunks.append(escape(text[:end], quote=False)); text = text[end:]
    return chunks


def split_node(node, overhead=0):
    value = render(node)
    if fits('#' * overhead + value):
        return [value]
    if isinstance(node, str):
        return split_text(node, overhead)
    if node['tag'] in {'table', 'img', 'pre', 'code'}:
        raise ValueError('单张表格或不可分割的图文块超过单篇接口容量；原稿保留，请将该表格转为清晰图片后投递。')
    prefix, suffix = node['open'], closing(node)
    shell = overhead + len(prefix) + len(suffix)
    chunks = []; current = ''; current_size = 0; current_bytes = 0
    for group in grouped(node['children']):
        combined = ''.join(render(child) for child in group)
        fragments = [combined] if fits('#' * shell + combined) else [part for child in group for part in split_node(child, shell)]
        for fragment in fragments:
            size, byte_size = planned_size(fragment), len(fragment.encode('utf-8'))
            if current and (shell + current_size + size >= PLAN_LIMIT or shell + current_bytes + byte_size >= BYTE_LIMIT):
                chunks.append(prefix + current + suffix); current = ''; current_size = 0; current_bytes = 0
            if shell + size >= PLAN_LIMIT or shell + byte_size >= BYTE_LIMIT:
                raise ValueError('排版块无法在接口限制内完整保留；原稿未修改。')
            current += fragment
            current_size += size; current_bytes += byte_size
    if current:
        chunks.append(prefix + current + suffix)
    return chunks


def plan_parts(content, options):
    if fits(content, CHAR_LIMIT):
        return [{'content': content, 'options': dict(options)}]
    tree = Tree(content)
    parts = split_node(tree.root)
    if not parts or len(parts) > MAX_PARTS:
        raise ValueError(f'全文需要超过 {MAX_PARTS} 篇图文；原稿保留，请另行组织系列文章后投递。')
    before, after = Signature(content), Signature(''.join(parts))
    if ''.join(before.text) != ''.join(after.text) or before.images != after.images:
        raise ValueError('自动分篇内容核对未通过；原稿保留，未投递。')
    result = []
    for index, part in enumerate(parts, 1):
        values = dict(options)
        if len(parts) > 1:
            suffix = f'（{index}/{len(parts)}）'
            values['title'] = values['title'][:32 - len(suffix)].rstrip() + suffix
            # Official draft/add: multi-article messages have no digest field.
            values['digest'] = ''
        check_final_content(part)
        result.append({'content': part, 'options': values})
    return result


def preview_parts(prepared):
    parts = prepared.get('parts') or [{'content': prepared['content'], 'options': prepared['options']}]
    if len(parts) == 1:
        return parts[0]['content']
    return ''.join('<section style="margin:20px 0;padding:12px;border:1px solid #cdd7ce">'
                   + '<p style="color:#234d43;font-weight:bold">第 ' + str(index) + '/' + str(len(parts))
                   + ' 篇 · ' + escape(part['options']['title']) + '</p>' + part['content'] + '</section>'
                   for index, part in enumerate(parts, 1))
