"""Shared local research labels for discovery and the personal library.

Labels are browsing suggestions, not a scientific quality judgment. No network
or model calls; manual overrides are separate from paper/generation artifacts.
"""
import re
import unicodedata

TOPICS = {
    'driving': ('自动驾驶', r'autonomous driving|self.driving|end.to.end driving|autonomous vehicle|driving polic|driving scene|自动驾驶'),
    'world': ('世界模型', r'world model|world simulator|video generation.*action|世界模型'),
    'embodied': ('具身智能', r'embodied|vision.language.action|robot learning|robotic manipulation|humanoid|具身智能'),
    'infra': ('AI Infra 加速', r'llm serving|inference engine|kv.cache|distributed training|model parallel|speculative decoding|gpu kernel|attention kernel|inference acceleration|efficient inference|flash.attention|tensor parallel|quantization.*(?:llm|language model)'),
    'foundation': ('基础大语言模型', r'large language model|foundation model|reasoning model|multimodal language|test.time scaling|recursive self.improvement'),
    'agents': ('Agent 方法', r'(?:llm|language.model|reasoning|autonomous|multi)[ -]agents?|agentic|tool.use|tool.calling|agent planning|agent harness|智能体'),
    'rsi': ('RSI · 递归自我改进', r'recursive self.improv|self.improving (?:agent|model|system)|self.evolving (?:agent|model)|self.refin(?:e|ement)|self.training.*(?:agent|language model)|recursive agent harness|递归自我改进'),
    'video': ('视频生成', r'video generat|text.to.video|image.to.video|video diffusion|video synthes|video foundation|视频生成'),
    'reconstruction': ('三维重建', r'3d reconstruction|three.dimensional reconstruction|scene reconstruction|gaussian splatting|neural radiance|novel.view synthesis|4d reconstruction|三维重建'),
}
def topic_labels(title, abstract):
    text = title + ' ' + abstract
    result=[key for key, (_, pattern) in TOPICS.items() if re.search(pattern, text, re.I)]
    if 'foundation' in result and not re.search(r'pre.train|post.train|training|reasoning|architect|transformer|scal|alignment|tokeniz|foundation|generalist|recursive|self.improv|language model.*(?:learn|general)',title,re.I):
        result.remove('foundation')
    return result


def catalog():
    return [{'id':key,'label':value[0]} for key,value in TOPICS.items()]


def validate_override(value):
    if value is None:
        return None
    if not isinstance(value,list) or len(value)>len(TOPICS) or any(not isinstance(k,str) or k not in TOPICS for k in value):
        raise ValueError('请选择有效论文分类，或恢复自动分类。')
    return [key for key in TOPICS if key in value]


def normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC',str(text or ''))).strip()


def abstract_text(source):
    if isinstance(source.get('abstract'),str) and source['abstract'].strip():
        return normalize(source['abstract'])[:10000]
    # Existing PDF imports did not save the arXiv abstract. Read only an explicit
    # Abstract heading near the beginning, never the body or reference list.
    pages=source.get('pages',[])
    first='\n'.join(str(p.get('text','')) for p in pages[:2] if isinstance(p,dict)) if isinstance(pages,list) else ''
    match=re.search(r'(?:^|\n)\s*(?:abstract\b|摘\s*要)\s*[:.—–-]?\s*(.*)', first[:18000],re.I|re.S)
    if not match:return ''
    end=re.search(
        r'\n\s*(?:(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+[A-Za-z][^\n]{0,100}(?=\n|$)'
        r'|(?:introduction|background|overview|preliminaries|related work|methods?|methodology|approach|results|experiments|references|引言)\s*[.:]?\s*(?=\n|$)'
        r'|(?:keywords|index terms)\s*[:—–-])',match.group(1),re.I)
    # Without a defensible end boundary, prefer title-only suggestions over
    # mistaking body/background mentions for abstract evidence.
    if not end:return ''
    body=match.group(1)[:end.start()]
    return normalize(body)[:6000]


def classify(source, fallback_title=''):
    info=source.get('paper_info') or source.get('publication_info') or {}
    if not isinstance(info,dict):info={}
    title=normalize(info.get('title') or source.get('title') or fallback_title)
    abstract=abstract_text(source)
    topics=topic_labels(title,abstract)
    subject=source.get('arxiv_categories',[])
    # Robotics is specific enough for this browsing category. Broad AI/CV tags
    # alone cannot distinguish agents, world models, video or foundation models.
    robotics=isinstance(subject,list) and 'cs.RO' in subject
    if robotics and 'embodied' not in topics:topics.append('embodied')
    evidence={}
    for key in TOPICS:
        if key not in topics:continue
        pattern=TOPICS[key][1]
        evidence[key]=[]
        if re.search(pattern,title,re.I):evidence[key].append('标题')
        if re.search(pattern,abstract,re.I):evidence[key].append('摘要')
        if key=='embodied' and robotics:evidence[key].append('arXiv cs.RO')
    return {'topics':list(evidence),'category_evidence':evidence,'category_mode':'auto'}


def with_override(automatic, override):
    if override is None:return dict(automatic)
    # Ignore damaged/obsolete overrides and show an automatic suggestion, rather
    # than breaking the whole library. New writes are strictly validated.
    try:topics=validate_override(override)
    except ValueError:return dict(automatic)
    return {'topics':topics,'category_mode':'manual',
            'category_evidence':{key:['手动选择'] for key in topics}}
