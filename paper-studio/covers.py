"""Local editorial cover composition; never presented as scientific evidence."""
from pathlib import Path
import uuid
import re
from PIL import Image, ImageDraw, ImageFont
from publication import paper_details
from sources import write_json


def ensure_overview_cover(folder, source, article, editor='Paper Studio', theme='ink'):
    """Compose a complete original figure into a wide editorial cover, no crop.

    Caller preserves any deliberate existing cover selection. Caption matching
    is a selection aid, not visual verification of scientific meaning.
    """
    from figure_plan import usable, roles
    from wechat_review import asset_path
    folder = Path(folder)
    editor = str(editor or 'Paper Studio').strip()[:50]
    theme = theme if theme in PALETTES else 'ink'
    title = str(article.get('title') or source.get('title') or '论文研读').strip()[:100]
    candidates = []
    for figure in source.get('figures', []):
        if figure.get('kind') == 'table' or not usable(figure, folder):
            continue
        try:
            path = asset_path(folder, figure['image'])
        except (ValueError, KeyError):
            continue
        caption = figure.get('original_caption') or figure.get('caption', '')
        score = 2 if re.search(r'\boverview\b|overall|总体|整体|概览', caption, re.I) else (1 if 'method' in roles(figure) else 0)
        candidates.append((score, figure, path))
    if not candidates:
        for cover in source.get('covers', []):
            if cover.get('selection_reason') == 'editorial_fallback' and cover.get('editor') == editor and cover.get('title') == title and cover.get('theme') == theme:
                try: asset_path(folder, cover['image']); return cover
                except (ValueError, KeyError): pass
        cover = generate_cover(folder, source, article, {'title':title, 'theme':theme, 'label':'论文研读 · '+editor})[0]
        cover.update(editor=editor, selection_reason='editorial_fallback', selection_note='未找到可自动使用的完整原图，已使用文字封面。')
        write_json(folder/'source.json', source)
        return cover
    score, figure, path = max(candidates, key=lambda candidate: candidate[0])
    stat = path.stat()
    for cover in source.get('covers', []):
        if (cover.get('kind') == 'overview_cover' and cover.get('source_figure_id') == figure['id']
                and cover.get('source_size') == stat.st_size and cover.get('source_mtime_ns') == stat.st_mtime_ns
                and cover.get('editor') == editor and cover.get('title') == title and cover.get('theme') == theme):
            try: asset_path(folder, cover['image']); return cover
            except (ValueError, KeyError): pass
    background, foreground, accent = PALETTES[theme]
    canvas = Image.new('RGB', (1200, 510), background)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((48, 27, 85, 33), radius=3, fill=accent)
    draw.text((98, 16), 'PAPER NOTES', font=font(17, True), fill=foreground)
    author_line = '编辑 · '+editor
    draw.text((1152-draw.textlength(author_line, font=font(18)), 15), author_line, font=font(18), fill=foreground)
    for size in range(32, 19, -2):
        face = font(size, True); lines = wrap(draw, title, face, 1104)
        if len(lines) <= 2: break
    y = 50
    for line in lines[:2]:
        draw.text((48, y), line, font=face, fill=foreground); y += size+6
    top = max(116, y+12)
    # Fit the entire figure on a white panel, preserving labels and all margins.
    panel = (48, top, 1152, 461)
    draw.rounded_rectangle(panel, radius=10, fill='white', outline='#dce4d8', width=1)
    with Image.open(path) as original:
        image = original.convert('RGBA')
        image.thumbnail((int(panel[2]-panel[0]-32), int(panel[3]-panel[1]-22)), Image.Resampling.LANCZOS)
        x = round((panel[0]+panel[2]-image.width)/2)
        y = round((panel[1]+panel[3]-image.height)/2)
        canvas.paste(image, (x, y), image)
    draw.text((48, 477), 'OVERVIEW' if score else 'PAPER FIGURE', font=font(14, True), fill=foreground)
    draw.text((936, 475), '原图完整保留 · 论文研读', font=font(15), fill=foreground)
    ident = 'cover-'+uuid.uuid4().hex[:16]
    name = 'covers/'+ident+'.png'; (folder/'covers').mkdir(exist_ok=True)
    canvas.save(folder/name)
    cover = {'id':ident, 'image':name, 'caption':('Overview' if score else '论文配图')+' · '+title,
             'kind':'overview_cover', 'title':title, 'theme':theme, 'editor':editor,
             'width':1200, 'height':510, 'source_figure_id':figure['id'],
             'source_size':stat.st_size, 'source_mtime_ns':stat.st_mtime_ns,
             'selection_reason':'overview' if score else 'figure_fallback',
             'selection_note':'按原图注选择 overview／方法架构图，完整适配横版封面。' if score else '未识别到 overview 图注，使用可用原图作为封面。'}
    source.setdefault('covers', []).append(cover)
    write_json(folder/'source.json', source)
    return cover

PALETTES={'ink':('#edf2e8','#153e35','#e66c39'), 'orange':('#fbefe4','#532c22','#dc5426'), 'blue':('#e8eef6','#153354','#3077d4')}


def font(size, bold=False):
    for name in (('msyhbd.ttc' if bold else 'msyh.ttc'), 'simhei.ttf', 'arial.ttf'):
        path=Path('C:/Windows/Fonts')/name
        if path.is_file():return ImageFont.truetype(str(path),size)
    return ImageFont.load_default(size=size)


def wrap(draw,text,face,width):
    lines=[];line=''
    for ch in text:
        if ch=='\n' or (line and draw.textlength(line+ch,font=face)>width):
            lines.append(line);line='' if ch=='\n' else ch
        else:line+=ch
    if line:lines.append(line)
    return lines


def generate_cover(folder,source,article,values):
    title=str(values.get('title') or article.get('title') or source['title']).strip()
    if not title or len(title)>100:raise ValueError('封面标题需为 1–100 字，可单独调整，不影响正文。')
    theme=values.get('theme','ink')
    if theme not in PALETTES:raise ValueError('无效封面主题。')
    label=str(values.get('label') or '论文研读 · Paper Studio').strip()[:50]
    background,foreground,accent=PALETTES[theme]
    info=paper_details(article,source);paper_title=info['title']
    assets=[];folder=Path(folder);(folder/'covers').mkdir(exist_ok=True)
    for width,height,shape in [(1200,510,'横版'),(800,800,'方形')]:
        im=Image.new('RGB',(width,height),background);d=ImageDraw.Draw(im)
        # Geometric decoration belongs to the cover design, not the paper.
        for r in (110,170,230): d.arc((width-r,height-r,width+r,height+r),180,270,fill=accent,width=3)
        margin=64;available=width-margin*2
        d.rounded_rectangle((margin,48,margin+64,55),radius=3,fill=accent)
        d.text((margin,78),label,font=font(22),fill=foreground)
        for size in range(58,23,-2):
            face=font(size,True);lines=wrap(d,title,face,available)
            if len(lines)*(size+18)<height*.46:break
        y=139
        for line in lines:d.text((margin,y),line,font=face,fill=foreground);y+=size+18
        subtitle=wrap(d,paper_title,font(20),available)
        # Complete original title is available in article metadata; cover uses a preview.
        if len(subtitle)>3:subtitle=subtitle[:3];subtitle[-1]=subtitle[-1][:-3]+'…'
        sy=max(y+20,height-145)
        for line in subtitle:d.text((margin,sy),line,font=font(20),fill=foreground);sy+=29
        ident='cover-'+uuid.uuid4().hex[:16];name='covers/'+ident+'.png';im.save(folder/name)
        assets.append({'id':ident,'image':name,'caption':shape+' · '+title,'kind':'designed_cover','title':title,'theme':theme,'width':width,'height':height})
    source.setdefault('covers',[]).extend(assets)
    write_json(folder/'source.json',source)
    return assets
