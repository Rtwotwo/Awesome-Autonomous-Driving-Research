"""Generic model controls; user endpoints and presets belong only in DPAPI storage."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl
from model_transport import SafeModelError

PROFILE_FIELDS = ('output_tokens', 'thinking', 'request_timeout', 'response_mode', 'json_mode',
                  'auth_mode', 'proxy_mode', 'context_window', 'max_output_tokens',
                  'vision_capability', 'tool_calling')


def endpoint(value):
    value = str(value).strip()
    match = re.fullmatch(r'\[([^\]]+)\]\((https?://[^\s]+)\)', value)
    if match:
        value = match[2]
    if any(ord(c) < 33 for c in value):
        raise SafeModelError('模型 API 地址不能包含空格或控制字符。')
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise SafeModelError('模型 API 地址格式不正确。') from None
    if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise SafeModelError('模型 API 地址格式不正确。')
    if parts.scheme == 'http' and parts.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise SafeModelError('远程模型 API 必须使用 HTTPS；本机服务可以使用 HTTP。')
    # Routing/version flags are allowed; secrets must never be put into URLs.
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        if key not in {'openai.azure', 'api-version'} or not re.fullmatch(r'[A-Za-z0-9._-]{1,60}', val):
            raise SafeModelError('API 地址只接受 openai.azure 或 api-version 路由参数；密钥请填写到 API Key。')
    path = parts.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ''))


def extra_options(values):
    auth = values.get('auth_mode') or 'bearer'
    proxy = values.get('proxy_mode') or 'system'
    if auth not in {'bearer', 'x-api-key', 'api-key'}:
        raise SafeModelError('请选择有效的 API 鉴权方式。')
    if proxy not in {'system', 'direct'}:
        raise SafeModelError('请选择系统代理或直连。')
    result = {'auth_mode': auth, 'proxy_mode': proxy}
    for key, high in (('context_window', 4000000), ('max_output_tokens', 393216)):
        value = values.get(key) or 0
        try:
            if isinstance(value, bool): raise ValueError()
            value = int(value)
        except (ValueError, TypeError):
            raise SafeModelError('模型上下文与输出容量须为整数。') from None
        if value and not 1024 <= value <= high:
            raise SafeModelError('模型容量超出支持范围，请按服务商文档填写。')
        result[key] = value
    for key in ('vision_capability', 'tool_calling'):
        value = values.get(key)
        if value is not None and type(value) is not bool:
            raise SafeModelError('模型能力必须为是、否或未声明。')
        result[key] = value
    return result


def request_headers(config):
    headers = {'Content-Type': 'application/json'}
    mode = config.get('auth_mode', 'bearer')
    headers['Authorization' if mode == 'bearer' else mode] = (
        'Bearer ' + config['key'] if mode == 'bearer' else config['key'])
    return headers


def request_budget(config, messages, requested):
    """Conservative preflight, not an exact provider tokenizer or image calculator.

    Count UTF-8 text bytes (including continuation history), framing reserve and
    16K per image. Refuse an oversized prompt rather than silently omit evidence.
    The provider still decides the actual token count for its tokenizer/vision.
    """
    maximum = config.get('max_output_tokens') or requested
    budget = min(requested, maximum)
    context = config.get('context_window') or 0
    if context:
        reserve = 1024
        for message in messages:
            reserve += 64
            content = message.get('content', '')
            if isinstance(content, str):
                reserve += len(content.encode('utf-8'))
            elif isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text':
                        reserve += len(part.get('text', '').encode('utf-8'))
                    elif part.get('type') == 'image_url':
                        reserve += 16384
        budget = min(budget, context - reserve)
    if budget < 1024:
        raise SafeModelError('按保守估算，当前输入与续写记录超出模型上下文可用预算；原文未被删减。请使用更大上下文模型，或关闭读图后重试。')
    return int(budget)
