"""Bounded Chat Completions decoding. Provider error bodies are never echoed."""
import http.client
import io
import json
import time
import socket
import ssl
import urllib.error

MAX_RESPONSE = 8_000_000
MAX_STREAM_WIRE = 512_000_000
MAX_SSE_EVENT = 2_000_000


def network_failure(exc, route='system'):
    """Classify only trusted exception types/codes, never leak error URLs or bodies."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'tls_certificate', 'TLS 证书验证失败；请检查系统时间、服务证书或代理证书，软件不会关闭证书校验。', False
    if isinstance(reason, socket.gaierror):
        return 'dns_resolution', '无法解析模型服务域名，请检查网络或 DNS。', True
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'network_timeout', '连接或读取等待超时，尚未收到完整响应。', True
    if isinstance(reason, ssl.SSLError):
        suffix = '当前使用系统代理，可在模型设置选择直连后检测。' if route == 'system' else '当前为直连，请检查网关 TLS 连接。'
        return 'tls_connection', 'TLS 连接提前中断。' + suffix, True
    if isinstance(reason, ConnectionRefusedError):
        return 'connection_refused', '服务或代理拒绝连接，请检查服务是否在线。', True
    return 'transport_' + type(exc).__name__, '连接中断或响应未完整返回，请检查网络与网关。', True


class SafeModelError(ValueError):
    """Application-authored messages only; never wrap a provider exception value."""


class ModelProtocolError(SafeModelError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def checked_choice(result):
    if not isinstance(result, dict):
        raise ModelProtocolError('response_shape', '模型响应不是 Chat Completions 对象。')
    if result.get('error'):
        raise ModelProtocolError('provider_error', '服务在响应正文中返回错误；请检查服务端额度和请求日志。')
    choices = result.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ModelProtocolError('missing_choices', '模型响应缺少有效 choices；请确认使用 Chat Completions 接口。')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict):
        raise ModelProtocolError('message_shape', '模型响应 message 格式不兼容。')
    content = message.get('content')
    if content is not None and not isinstance(content, str):
        raise ModelProtocolError('content_shape', '模型正文不是文本；请使用文本 Chat Completions 输出。')
    if not isinstance(choice.get('finish_reason'), str) or choice['finish_reason'] not in {'stop', 'length', 'tool_calls', 'function_call', 'content_filter'}:
        raise ModelProtocolError('missing_finish', '模型响应缺少有效的完成标记，不能确认正文完整；请检查服务接口兼容性。')
    return choice


def read_completion(response, on_text, cancelled, event):
    content_type = response.headers.get('Content-Type', '')
    if isinstance(content_type, str) and 'text/event-stream' in content_type.lower():
        return read_sse(response, on_text, cancelled, event)
    raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ModelProtocolError('response_size', '模型响应超过本地读取上限。')
    length = response.headers.get('Content-Length')
    if isinstance(length, str) and length.isdigit() and len(raw) < int(length):
        # HTTPResponse.read(n) can silently return short instead of raising.
        raise http.client.IncompleteRead(b'', int(length) - len(raw))
    # Some compatible servers omit the SSE content type. Still decode their
    # completed response safely, although incremental delivery is unavailable.
    if raw.lstrip().startswith((b'data:', b':')):
        return read_sse(io.BytesIO(raw), on_text, cancelled, event)
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ModelProtocolError('response_json', '服务返回的不是有效 API JSON，可能是网关错误或响应截断。') from None
    checked_choice(result)
    return result


def read_sse(response, on_text, cancelled, event):
    parts, data = [], []
    finish, usage = None, {}
    done = False
    received = reasoning = output_bytes = event_bytes = 0
    last_notice = last_save = 0.0
    def dispatch():
        nonlocal finish, usage, done, reasoning, last_notice, last_save, output_bytes, event_bytes
        if not data:
            return
        payload = b'\n'.join(data);data.clear();event_bytes = 0
        if payload.strip() == b'[DONE]':
            done = True;return
        try:
            chunk = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ModelProtocolError('stream_json', '服务返回了无效的流式数据；当前片段已保留。') from None
        if not isinstance(chunk, dict) or chunk.get('error'):
            raise ModelProtocolError('stream_error', '服务返回了异常流式响应；请检查服务端日志。')
        if isinstance(chunk.get('usage'), dict):usage = chunk['usage']
        choices = chunk.get('choices', [])
        if not isinstance(choices, list):
            raise ModelProtocolError('stream_choices', '流式 choices 格式不兼容。')
        if not choices:return  # Usage-only final event.
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelProtocolError('stream_choice', '流式 choice 格式不兼容。')
        delta = choice.get('delta', {})
        if not isinstance(delta, dict):
            raise ModelProtocolError('stream_delta', '流式 delta 格式不兼容。')
        value = delta.get('content')
        if value is not None and not isinstance(value, str):
            raise ModelProtocolError('stream_content', '流式正文不是文本。')
        reasoning_value = delta.get('reasoning_content')
        output_bytes += len((value or '').encode('utf-8'))
        if isinstance(reasoning_value, str):output_bytes += len(reasoning_value.encode('utf-8'))
        if output_bytes > MAX_RESPONSE:
            raise ModelProtocolError('response_size', '模型文本输出超过本地 8 MB 读取上限；当前片段已保留。')
        now = time.monotonic()
        if value:
            parts.append(value)
            if now-last_save >= 1:
                on_text(''.join(parts));last_save = now
        if isinstance(reasoning_value, str):reasoning += len(reasoning_value)
        if now-last_notice >= 2 and (value or reasoning_value):
            event('正在接收正文…' if parts else '模型正在推理，已收到服务响应…')
            last_notice = now
        marker = choice.get('finish_reason')
        if marker is not None:
            if not isinstance(marker, str) or marker not in {'stop', 'length', 'tool_calls', 'function_call', 'content_filter'}:
                raise ModelProtocolError('stream_finish', '流式响应的完成标记无效，不能确认正文完整。')
            finish = marker
    try:
        while not done:
            if cancelled():raise SafeModelError('任务已取消；已返回片段保留在本机。')
            line = response.readline(MAX_SSE_EVENT + 1)
            if not line:
                dispatch();break
            received += len(line)
            if received > MAX_STREAM_WIRE:
                raise ModelProtocolError('stream_size', '流式传输超过本地 512 MB 读取上限；当前片段已保留。')
            if len(line) > MAX_SSE_EVENT:
                raise ModelProtocolError('event_size', '单个流式分片超过本地 2 MB 读取上限。')
            line = line.rstrip(b'\r\n')
            if not line:dispatch()
            elif line.startswith(b'data:'):
                event_bytes += len(line)
                if event_bytes > MAX_SSE_EVENT:
                    raise ModelProtocolError('event_size', '单个流式分片超过本地 2 MB 读取上限。')
                data.append(line[5:].lstrip(b' '))
        if not done and finish is None:
            # A syntactically valid partial JSON is not proof the turn finished.
            raise http.client.IncompleteRead(b'', None)
        return {'choices':[{'message':{'content':''.join(parts), 'reasoning_content': bool(reasoning)},
                            'finish_reason': finish if finish is not None else 'stop'}], 'usage':usage}
    finally:
        if parts:on_text(''.join(parts))
