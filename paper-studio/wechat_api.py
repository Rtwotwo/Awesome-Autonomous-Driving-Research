"""Official WeChat draft API. Secrets never enter article files or error strings."""
import base64,io,json,re,secrets,time,ipaddress
import urllib.request,urllib.error
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from PIL import Image
from model_store import ModelStore
from sources import write_json
from wechat_review import asset_path,read_json,require_approved

APPID=re.compile(r'wx[0-9a-fA-F]{16}')
READBACK_RETRY_DELAYS=(0.25, 0.75)

def whitelist_ip(message):
    """Extract only a validated IP from official 40164 errors, never echo raw errors."""
    match=re.search(r'\binvalid\s+ip\s+([0-9a-fA-F:.]+)(?=[\s,;]|$)',str(message)[:2048],re.I)
    if not match:return ''
    try:
        address=ipaddress.ip_address(match.group(1))
        address=getattr(address,'ipv4_mapped',None) or address
        return str(address) if address.is_global else ''
    except ValueError:return ''

class WechatError(ValueError):
    def __init__(self,code,message=''):
        self.code=int(code)
        self.observed_ip=whitelist_ip(message) if self.code==40164 else ''
        hints={40001:'AppSecret 无效，请在本机重新填写。',40013:'AppID 无效。',40125:'AppSecret 无效。',40164:'当前出口 IP 不在白名单。请在微信开发平台配置本机网络的公网出口 IP；软件不会代改白名单。',48001:'账号没有该接口权限，请到微信开发平台的接口管理中检查。',48002:'账号接口权限被限制。',45009:'微信接口调用次数已达限制，请稍后再试。',40007:'封面素材 ID 无效。',40009:'图片大小不符合微信要求。',40005:'图片格式不符合微信要求。',42001:'访问凭证过期，请重新检测连接。',40014:'访问凭证无效，请重新检测连接。',45003:'标题超过微信限制。',45004:'正文超过微信限制。',45005:'阅读原文链接超过微信限制。',45006:'摘要超过微信限制。'}
        hints.update({89503:'此次 API 调用需要管理员在微信中确认，请完成确认后再检测连接。',89506:'该 IP 调用已被管理员拒绝，请按微信提示等待后重试。',89507:'该 IP 调用已被管理员拒绝，请按微信提示等待后重试。'})
        if self.code==40164:
            address=f'微信本次识别的公网出口 IP：{self.observed_ip}。' if self.observed_ip else '微信未返回可识别的公网出口 IP。'
            hints[40164]=address+'请核对当前公众号的“API IP 白名单”，添加该出口地址并保存后再检测连接。不要填 127.0.0.1、192.168.x.x 或网页域名。软件直连微信，不使用系统 HTTP 代理；VPN/TUN、换网或宽带重拨仍可能改变出口。'
        super().__init__(f'微信返回错误 {self.code}：'+hints.get(self.code,'请求未被接受，请在微信开发平台诊断此错误码。'))

class TransportError(ValueError):pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def transport(path,token='',payload=None,file=None):
    if path not in {'stable_token','draft/count','draft/add','draft/get','media/uploadimg','material/add_material'}:
        raise ValueError('不支持的微信接口。')
    params={}
    if token:params['access_token']=token
    if path=='material/add_material':params['type']='image'
    url='https://api.weixin.qq.com/cgi-bin/'+path+('?' + urlencode(params) if params else '')
    headers={'User-Agent':'PaperStudio/1.0'}
    data=None
    if file:
        name,mime,blob=file;boundary='PaperStudio'+secrets.token_hex(16)
        data=(f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n').encode()+blob+f'\r\n--{boundary}--\r\n'.encode()
        headers['Content-Type']='multipart/form-data; boundary='+boundary
    elif payload is not None:
        data=json.dumps(payload,ensure_ascii=False).encode();headers['Content-Type']='application/json; charset=utf-8'
    req=urllib.request.Request(url,data=data,headers=headers)
    # Credentials go only to the fixed official host. Do not inherit proxy settings.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(req,timeout=45) as response:
            raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise TransportError('微信响应超过本地限制，请检查草稿箱后再操作。')
            result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError()
    except TransportError:raise
    except Exception:raise TransportError('微信请求超时或连接中断；如已提交草稿，请先检查后台，不要重复提交。') from None
    if result.get('errcode'):raise WechatError(result['errcode'],result.get('errmsg',''))
    return result


class Account:
    def __init__(self,path=None,request=transport):
        self.store=ModelStore(path) if path else None;self.request=request
        self.config={};self.remember=False;self.warning='';self.token='';self.expires=0;self.checked=None
        if self.store:
            try:
                value=self.store.load()
                if value:self.config=self.validate(value.get('account',{}));self.remember=True
            except (ValueError,OSError):self.warning='公众号加密配置无法读取，请重新填写；原文件保留。'
    @staticmethod
    def validate(value):
        result={k:str(value.get(k,'')).strip() for k in ('appid','secret','name')}
        if not APPID.fullmatch(result['appid']):raise ValueError('请填写有效的公众号 AppID（wx 开头）。')
        if not 16<=len(result['secret'])<=128 or not re.fullmatch(r'[A-Za-z0-9_-]+',result['secret']):
            raise ValueError('请填写微信平台提供的 AppSecret。')
        if len(result['name'])>80:raise ValueError('公众号名称过长。')
        return result
    def save(self,body):
        if body.get('clear'):
            if self.store:self.store.clear()
            self.config={};self.remember=False
        else:
            values=dict(body)
            if not values.get('secret') and values.get('appid')==self.config.get('appid'):values['secret']=self.config.get('secret','')
            config=self.validate(values);remember=body.get('remember') is True
            if remember:
                if not self.store:raise ValueError('请使用桌面版加密保存公众号配置。')
                self.store.save({'version':1,'account':config})
            elif self.store:self.store.clear()
            self.config=config;self.remember=remember
        self.warning='';self.token='';self.expires=0;self.checked=None
    def public(self):
        return {'configured':bool(self.config),'appid':self.config.get('appid',''),'name':self.config.get('name',''),
                'remember':self.remember,'warning':self.warning,'check':self.checked}
    def get_token(self):
        if not self.config:raise ValueError('请先在公众号设置中填写 AppID 与 AppSecret。')
        if not self.token or time.time()>=self.expires:
            r=self.request('stable_token',payload={'grant_type':'client_credential','appid':self.config['appid'],'secret':self.config['secret'],'force_refresh':False})
            token=r.get('access_token');ttl=r.get('expires_in')
            if not isinstance(token,str) or not token or len(token)>4096 or type(ttl) not in (int,float):raise TransportError('微信未返回有效访问凭证，请重新检测连接。')
            self.token=token;self.expires=time.time()+max(0,ttl-120)
        return self.token
    def call(self,path,*,check_cancel=lambda:None,before_send=lambda:None,**kwargs):
        try:
            check_cancel()
            token=self.get_token()
            check_cancel()
            before_send()
            return self.request(path,token=token,**kwargs)
        except WechatError as e:
            if e.code in (40014,42001):self.token='';self.expires=0
            raise
    def check(self):
        try:
            r=self.call('draft/count')
            if type(r.get('total_count')) is not int:raise TransportError('微信没有返回有效草稿数量。')
        except (WechatError,TransportError) as error:
            self.checked={'time':time.time(),'draft_read':False,'message':str(error),
                          'code':getattr(error,'code',None),'observed_ip':getattr(error,'observed_ip','')}
            raise
        self.checked={'time':time.time(),'draft_read':True,'message':'已连接并可读取草稿数量。写入图片和草稿的权限仍以实际提交结果为准。'}
        return self.public()
    def close(self):self.config.clear();self.token='';self.expires=0


def image_file(path,limit=1_000_000):
    raw=path.read_bytes()
    if len(raw)<limit:return path.name,'image/png',raw
    with Image.open(io.BytesIO(raw)) as im:
        if im.width*im.height>25_000_000:raise ValueError('图片像素过大，请先在配图页重新裁剪。')
        image=im.convert('RGB')
        for quality in (94,88,82,76):
            buf=io.BytesIO();image.save(buf,'JPEG',quality=quality,optimize=True)
            if len(buf.getvalue())<limit:return path.stem+'.jpg','image/jpeg',buf.getvalue()
    raise ValueError('图片保持原尺寸后仍超过微信 1 MB 限制，请重新裁剪或压缩后再审核。')


def verify_image_url(url):
    p=urlsplit(str(url));host=p.hostname or ''
    if p.scheme not in ('http','https') or p.username or p.password or p.port not in (None,80,443) or not (host=='mmbiz.qpic.cn' or host.endswith('.qpic.cn') or host=='mmbiz.qlogo.cn'):
        raise TransportError('微信未返回受支持的图片地址。')
    return str(url)


def placeholder_image_src(value):
    """Only accept empty URLs or a demonstrably transparent one-pixel image."""
    if not value or value.lower()=='about:blank':return True
    if len(value)>4096:return False
    match=re.fullmatch(r'data:image/(?:png|gif);base64,([A-Za-z0-9+/=]+)',value,re.I)
    if not match:return False
    try:
        with Image.open(io.BytesIO(base64.b64decode(match[1],validate=True))) as image:
            return image.size==(1,1) and getattr(image,'n_frames',1)==1 and image.convert('RGBA').getpixel((0,0))[3]==0
    except (ValueError,OSError):return False


class Contents(HTMLParser):
    def __init__(self,s):
        super().__init__(convert_charrefs=True);self.text=[];self.images=[];self.feed(s)
    def handle_data(self,t):self.text.append(t)
    def handle_starttag(self,t,attrs):
        if t=='img':
            attrs=dict(attrs);value=attrs.get('src') or ''
            # Lazy-loaded HTML may keep a data URI in src and the real image in
            # data-src. A different non-placeholder src must still fail checks.
            if placeholder_image_src(value):
                value=attrs.get('data-src') or value
            # A fragment is never sent to the image server. Keep paths and every
            # query parameter: no observed evidence permits dropping CDN queries.
            self.images.append(re.sub(r'^https?:','',value.split('#',1)[0],flags=re.I))
    def plain(self):return ''.join(''.join(self.text).split())


def compare_item(expected,item):
    fields=('title','author','digest','content_source_url','thumb_media_id')
    want=Contents(expected['content']);content=str(item.get('content',''));actual=Contents(content)
    return {'field_matches':{k:item.get(k,'')==expected.get(k,'') for k in fields},
        'text_match':actual.plain()==want.plain(),'image_count_match':len(actual.images)==len(want.images),
        'image_addresses_match':actual.images==want.images,'image_matches':[a==b for a,b in zip(actual.images,want.images)],
        'expected_html_chars':len(expected['content']),'actual_html_chars':len(content),
        'expected_text_chars':len(want.plain()),'actual_text_chars':len(actual.plain()),
        'expected_image_count':len(want.images),'actual_image_count':len(actual.images)}


def verify_draft(account,record,check_cancel=lambda:None):
    expected=record.get('payloads') or [record['payload']]
    fields=('title','author','digest','content_source_url','thumb_media_id')
    verification={'checked_at':time.time(),'status':'reading',
        'field_matches':{k:None for k in fields},'text_match':None,
        'image_count_match':None,'image_addresses_match':None,'image_matches':[],
        'expected_html_chars':sum(len(p['content']) for p in expected),'actual_html_chars':None,
        'expected_text_chars':sum(len(Contents(p['content']).plain()) for p in expected),'actual_text_chars':None,
        'expected_image_count':sum(len(Contents(p['content']).images) for p in expected),'actual_image_count':None,
        'expected_part_count':len(expected),'actual_part_count':None,'part_count_match':None}
    record['verification']=verification
    try:
        r=account.call('draft/get',payload={'media_id':record['media_id']},check_cancel=check_cancel)
    except Exception as exc:
        verification['status']='unavailable'
        if isinstance(exc,WechatError):verification['error_code']=exc.code
        raise
    items=r.get('news_item',[])
    if not isinstance(items,list) or not items or not all(isinstance(item,dict) for item in items):
        verification['status']='empty_response'
        raise ValueError('草稿已创建，但读回内容为空；可重新核对已有草稿，不会重复创建。')
    checks=[compare_item(want,item) for want,item in zip(expected,items)]
    verification.update(actual_part_count=len(items),part_count_match=len(items)==len(expected),
        field_matches={k:all(c['field_matches'][k] for c in checks) for k in fields},
        text_match=all(c['text_match'] for c in checks),image_count_match=all(c['image_count_match'] for c in checks),
        image_addresses_match=all(c['image_addresses_match'] for c in checks),
        image_matches=[m for c in checks for m in c['image_matches']],
        actual_html_chars=sum(len(str(item.get('content',''))) for item in items),
        actual_text_chars=sum(len(Contents(str(item.get('content',''))).plain()) for item in items),
        actual_image_count=sum(len(Contents(str(item.get('content',''))).images) for item in items))
    if len(expected)>1:verification['parts']=checks
    if not verification['part_count_match'] or not all(verification['field_matches'].values()) or not verification['text_match'] or not verification['image_addresses_match']:
        verification['status']='mismatch'
        raise ValueError('草稿已提交，但读回资料、正文或图片与提交稿不完全一致。请在公众号后台检查；不会自动重发。')
    verification['status']='verified'
    label=f'已将全文分为 {len(expected)} 篇，创建同一份多图文草稿并逐篇核对。' if len(expected)>1 else '已提交公众号草稿箱并读回核对资料、正文文字、图片地址和封面。'
    record.update(state='submitted',verified_at=time.time(),message=label+'排版与正文外链仍请手机预览确认；尚未公开发表或群发。')
    return record


def verify_with_retries(account,record,progress=lambda _:None):
    """Only retry readback; a known media ID never enters draft/add again."""
    attempts=len(READBACK_RETRY_DELAYS)+1
    for attempt in range(1,attempts+1):
        try:
            return verify_draft(account,record,check_cancel=lambda:progress(f'草稿已创建，正在读回核对（{attempt}/{attempts}）…'))
        except (TransportError,WechatError) as exc:
            transient=isinstance(exc,TransportError) or exc.code in {40014,42001}
            if not transient or attempt==attempts:raise
            progress(f'草稿已创建，读回暂未完成；正在重试核对（{attempt+1}/{attempts}），不会重复创建…')
            time.sleep(READBACK_RETRY_DELAYS[attempt-1])
        finally:
            if 'verification' in record:record['verification']['attempts']=attempt


def readback_message(record,exc):
    """Do not expose raw exception text, article contents, URLs or credentials."""
    if record.get('verification',{}).get('status')=='mismatch':
        return '草稿已创建，但读回资料、正文或图片存在差异；请查看核对结果。不会自动重发。'
    if isinstance(exc,WechatError):
        return f'草稿已创建；微信读回返回错误 {exc.code}，草稿编号已保留。可重新核对已有草稿，不会重复创建。'
    return '草稿已创建，读回核对尚未完成；草稿编号已保留，可重新核对已有草稿，不会重复创建。'


def submit_draft(folder,prepared,account,progress=lambda x:None):
    folder=Path(folder);require_approved(folder,prepared)
    return _submit_payload(folder,prepared,account,progress)


def submit_automated_draft(folder,prepared,account,progress=lambda x:None):
    """Separate draft-only authority. Never marks a draft as human reviewed."""
    folder=Path(folder)
    permit=read_json(folder/'automatic-draft-permit.json',{})
    if (permit.get('mode')!='automatic_draft_only' or permit.get('checks_passed') is not True
            or permit.get('snapshot')!=prepared or time.time()-permit.get('created_at',0)>3600):
        raise ValueError('自动投递授权不存在、已过期或稿件已变更。请重新运行检查或走人工审核。')
    return _submit_payload(folder,prepared,account,progress,allow_unverified=True)


def _submit_payload(folder,prepared,account,progress,*,allow_unverified=False):
    from wechat_content import check_final_content
    if prepared['account']['appid']!=account.config.get('appid'):raise ValueError('目标公众号已改变，请重新审核。')
    state_path=folder/'wechat-submission.json';old=read_json(state_path,{})
    if old.get('state') in {'submitted','submitted_unverified','unknown'}:
        raise ValueError('已有提交记录。请先查看公众号草稿箱，或使用“重新核对草稿”；不会重复创建。')
    record={'state':'uploading','started_at':time.time(),'account':prepared['account'],'message':'准备上传图片。'}
    write_json(state_path,record)
    try:
        # Prepare every image before remote changes, so over-limit images fail early.
        files={x['id']:image_file(asset_path(folder,x['image'])) for x in prepared['assets']}
        parts=prepared.get('parts') or [{'content':prepared['content'],'options':prepared['options']}]
        contents=[part['content'] for part in parts];index={x['id']:x for x in prepared['assets']}
        for i,ident in enumerate(prepared['inline_ids'],1):
            progress(f'上传正文图片 {i}/{len(prepared["inline_ids"])}…')
            url=verify_image_url(account.call('media/uploadimg',file=files[ident],check_cancel=lambda:progress(f'上传正文图片 {i}/{len(prepared["inline_ids"])}…')).get('url'))
            contents=[content.replace('src="'+escape(index[ident]['image'],quote=True)+'"','src="'+escape(url,quote=True)+'"') for content in contents]
        for content in contents:check_final_content(content)
        progress('上传封面…')
        cover=account.call('material/add_material',file=files[prepared['options']['cover_id']],check_cancel=lambda:progress('上传封面…')).get('media_id')
        if not isinstance(cover,str) or not cover or len(cover)>256:raise TransportError('微信未返回有效封面素材 ID。')
        payloads=[]
        for part,content in zip(parts,contents):
            o=part['options'];payloads.append({'article_type':'news','title':o['title'],'author':o['author'],'digest':o['digest'],
                'content':content,'content_source_url':o['source_url'],'thumb_media_id':cover,'need_open_comment':0,'only_fans_can_comment':0})
        # Before the only create call, persist uncertainty. Timeout/crash must not resend.
        def mark_creating():
            record.update(state='unknown',payload=payloads[0],message='已发起创建草稿；结果尚未确认，请勿重复提交。')
            if len(payloads)>1:record['payloads']=payloads
            write_json(state_path,record)
        progress('创建公众号草稿…')
        response=account.call('draft/add',payload={'articles':payloads},check_cancel=lambda:progress('创建公众号草稿…'),before_send=mark_creating);media=response.get('media_id')
        if not isinstance(media,str) or not media or len(media)>256:raise TransportError('微信没有返回有效草稿 ID；请先检查公众号草稿箱。')
        record.update(state='submitted_unverified',media_id=media,message='草稿已创建，正在读回核对。');write_json(state_path,record)
        progress('读回核对标题、正文、图片和封面…')
        verify_with_retries(account,record,progress);write_json(state_path,record)
        return record
    except Exception as exc:
        if record.get('media_id') and record['state']=='submitted_unverified':
            record['message']=readback_message(record,exc)
            write_json(state_path,record)
            if allow_unverified:return record
            raise ValueError(record['message']) from None
        if record['state']=='uploading' or (record['state']=='unknown' and isinstance(exc,WechatError)):
            record['state']='failed'
        record['message']=str(exc) if isinstance(exc,ValueError) else '提交未完成，请检查公众号草稿箱后重试。'
        write_json(state_path,record)
        raise ValueError(record['message']) from None


def recheck(folder,account,progress=lambda _:None,*,allow_unverified=False):
    path=Path(folder)/'wechat-submission.json';record=read_json(path,{})
    if not record.get('media_id'):raise ValueError('没有已知草稿 ID，请先到公众号后台检查是否创建成功。')
    if record.get('account',{}).get('appid')!=account.config.get('appid'):raise ValueError('请切换回原提交公众号后核对。')
    try:verify_with_retries(account,record,progress)
    except Exception as exc:
        record.update(state='submitted_unverified',message=readback_message(record,exc))
        record.pop('verified_at',None)
        write_json(path,record)
        if allow_unverified:return record
        raise ValueError(record['message']) from None
    write_json(path,record);return record


def reset_submission(folder,confirmed):
    if confirmed is not True:raise ValueError('请先确认已经检查公众号草稿箱；再次提交可能产生新草稿。')
    path=Path(folder)/'wechat-submission.json';old=read_json(path,{})
    if old:
        archive=Path(folder)/'history';archive.mkdir(exist_ok=True)
        write_json(archive/('wechat-submission-'+secrets.token_hex(8)+'.json'),old)
    write_json(path,{'state':'ready','message':'已由用户确认开始下一次提交；原公众号草稿未删除。'})
