"""Host-owned request scheduling, cache and circuit breaker; plugin API is unchanged."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import datetime as dt
from email.utils import parsedate_to_datetime
from functools import wraps
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

POLICY = {'interval_seconds': 2, 'hourly_limit': 120, 'round_limit': 64}
_scope = ContextVar('calendar_network', default=None)
_fallback = None


class NetworkDeferred(ValueError):
    pass


@dataclass
class Response:
    data: bytes
    headers: dict

    @property
    def charset(self):
        from email.message import Message
        message = Message()
        message['content-type'] = self.headers.get('content-type', '')
        return message.get_content_charset() or 'utf-8'


def site_for(url):
    host = urllib.parse.urlsplit(url).hostname or ''
    for root in ('biligame.com', 'gamekee.com', 'hypergryph.com', 'prts.wiki', 'skland.com', 'hoyoverse.com'):
        if host == root or host.endswith('.' + root):
            return root
    return host


def network_job(fn):
    @wraps(fn)
    def run(self, *args, **kwargs):
        manager = getattr(self.store, 'network', None) or default_manager()
        with manager.job():
            return fn(self, *args, **kwargs)
    return run


def default_manager():
    global _fallback
    if _fallback is None:
        _fallback = NetworkManager()
    return _fallback


def read_response(request, redirect, *, limit=6_000_000, timeout=20, image=False, min_interval=0):
    current = _scope.get()
    return (current[0] if current else default_manager()).read(request, redirect, limit=limit, timeout=timeout, image=image, min_interval=min_interval)


class NetworkManager:
    def __init__(self, store=None, *, clock=time.time, waiter=None, opener=None):
        self.store, self.clock, self.waiter, self.opener = store, clock, waiter, opener
        saved = store.get_meta('network-policy', {}) if store else {}
        self.policy = {**POLICY, **saved.get('policy', {})}
        self.paused = saved.get('paused', False)
        self.sites = saved.get('sites', {})
        self.condition = threading.Condition(threading.RLock())
        self.active, self.waiting, self.stopped = 0, 0, False
        self.cache_hits = 0
        self.cache_dir = store.path.parent / 'http-cache' if store else None
        self.memory = {}

    def _site(self, name):
        return self.sites.setdefault(name, {'paused': False, 'next_at': 0, 'cooldown_until': 0, 'window_start': 0, 'requests': 0, 'failures': 0, 'error': '', 'active': False})

    def _save(self):
        if self.store:
            # Active operations are process-local, never restored after a restart.
            self.store.set_meta('network-policy', {'policy': self.policy, 'paused': self.paused, 'sites': {k: {**v, 'active': False} for k, v in self.sites.items()}})

    @contextmanager
    def job(self):
        existing = _scope.get()
        if existing and existing[0] is self:
            yield
            return
        token = _scope.set((self, {}))
        try:
            yield
        finally:
            _scope.reset(token)

    def stop(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()

    def update(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'paused', 'site', 'policy'}:
            raise ValueError('不支持的采集设置')
        with self.condition:
            if 'paused' in payload and type(payload['paused']) is not bool:
                raise ValueError('暂停开关必须为布尔值')
            policy = payload.get('policy', {})
            limits = {'interval_seconds': (2, 60), 'hourly_limit': (20, 600), 'round_limit': (5, 100)}
            if not isinstance(policy, dict) or set(policy) - set(limits) or any(type(n) is not int or not limits[k][0] <= n <= limits[k][1] for k, n in policy.items()):
                raise ValueError('请求间隔应为 2–60 秒，每小时 20–600 次，每轮每站 5–100 次')
            site = payload.get('site')
            if site is not None and (not isinstance(site, str) or site not in self.sites or 'paused' not in payload or policy):
                raise ValueError('请选择已出现的站点，只能调整其暂停状态')
            if 'paused' in payload:
                if site:
                    self.sites[site]['paused'] = payload['paused']
                else:
                    self.paused = payload['paused']
            self.policy.update(policy)
            # Resuming never clears the server's cooldown or the rolling quota.
            self._save()
            self.condition.notify_all()
        return self.snapshot()

    def snapshot(self):
        with self.condition:
            now = self.clock()
            sites = []
            for name, s in sorted(self.sites.items()):
                quota_end = s['window_start'] + 3600 if s['requests'] >= self.policy['hourly_limit'] and now < s['window_start'] + 3600 else 0
                next_at = max(s['next_at'], s.get('last_started', 0) + self.policy['interval_seconds'], s['cooldown_until'], quota_end)
                sites.append({'site': name, 'paused': s['paused'], 'active': s['active'], 'requests': s['requests'] if now < s['window_start'] + 3600 else 0, 'failures': s['failures'], 'error': s['error'], 'next_allowed_at': dt.datetime.fromtimestamp(next_at, dt.timezone.utc).isoformat() if next_at > now else None})
            return {'paused': self.paused, 'policy': dict(self.policy), 'active': self.active, 'waiting': self.waiting, 'cache_hits': self.cache_hits, 'sites': sites, 'concurrency_per_site': 1, 'concurrency_total': 4}

    def _claim(self, site, min_interval=0):
        with self.condition:
            self.waiting += 1
            try:
                while True:
                    now, s = self.clock(), self._site(site)
                    if self.stopped or self.paused or s['paused']:
                        raise NetworkDeferred('联网采集已暂停；保留本地日历')
                    if now < s['cooldown_until']:
                        raise NetworkDeferred('站点冷却中，稍后再同步；手动同步不能绕过冷却')
                    if now >= s['window_start'] + 3600:
                        s.update(window_start=now, requests=0)
                    if s['requests'] >= self.policy['hourly_limit']:
                        raise NetworkDeferred('站点本小时请求额度已用完；保留缓存')
                    current = _scope.get()
                    budget = current[1] if current and current[0] is self else {}
                    if budget.get(site, 0) >= self.policy['round_limit'] or sum(budget.values()) >= 160:
                        raise NetworkDeferred('本轮采集额度已用完；已停止继续请求')
                    delay = max(s['next_at'], s.get('last_started', 0) + max(min_interval, self.policy['interval_seconds'])) - now
                    if not s['active'] and self.active < 4 and delay <= 0:
                        s.update(active=True, last_started=now, next_at=now + max(self.policy['interval_seconds'], min_interval), requests=s['requests'] + 1)
                        budget[site] = budget.get(site, 0) + 1
                        self.active += 1
                        self._save()
                        return
                    wait = min(max(delay, .05), 1)
                    self.waiter(wait) if self.waiter else self.condition.wait(wait)
            finally:
                self.waiting -= 1

    def _finish(self, site, error=None, retry_after=None, blocked=False):
        with self.condition:
            s, now = self._site(site), self.clock()
            self.active -= 1
            s['active'] = False
            if error:
                s['failures'] += 1
                s['error'] = str(error)[:180]
                delay = min(3600, 30 * 2 ** min(s['failures'] - 1, 7))
                if retry_after:
                    try:
                        seconds = float(retry_after)
                    except ValueError:
                        try:
                            seconds = parsedate_to_datetime(retry_after).timestamp() - now
                        except (ValueError, TypeError, OverflowError):
                            seconds = 0
                    if not math.isfinite(seconds) or seconds > 31536000:
                        blocked = True
                        seconds = 31536000
                    delay = max(delay, max(0, seconds))
                s['cooldown_until'] = now + delay
                if blocked or s['failures'] >= 3:
                    s['paused'] = True
            else:
                s.update(failures=0, error='')
            self._save()
            self.condition.notify_all()

    def _cached(self, key):
        if not self.cache_dir:
            return self.memory.get(key)
        try:
            meta = json.loads((self.cache_dir / (key + '.json')).read_text(encoding='utf-8'))
            body = self.cache_dir / (key + '.body')
            if body.stat().st_size > 6_000_000:
                return None
            data = body.read_bytes()
            if hashlib.sha256(data).hexdigest() != meta['sha256']:
                return None
            return {**meta, 'data': data}
        except (OSError, ValueError, KeyError):
            return None

    def _cache(self, key, data, headers):
        if 'no-store' in headers.get('cache-control', '').lower():
            return
        item = {'time': self.clock(), 'headers': {k:v for k,v in headers.items() if k in {'content-type', 'etag', 'last-modified', 'cache-control'}}, 'sha256': hashlib.sha256(data).hexdigest()}
        if not self.cache_dir:
            self.memory[key] = {**item, 'data': data}
            if len(self.memory) > 128:
                self.memory.pop(next(iter(self.memory)))
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for suffix, value in [('body', data), ('json', json.dumps(item).encode())]:
                target = self.cache_dir / (key + '.' + suffix)
                temporary = target.with_suffix(target.suffix + '.' + str(threading.get_ident()) + '.tmp')
                temporary.write_bytes(value)
                temporary.replace(target)
            # Only remove our exact hashed cache pairs, never source/user files.
            bodies = sorted(self.cache_dir.glob('*.body'), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in bodies)
            while bodies and (total > 128_000_000 or len(bodies) > 256):
                old = bodies.pop(0)
                if len(old.stem) == 64 and all(c in '0123456789abcdef' for c in old.stem):
                    total -= old.stat().st_size
                    old.unlink(missing_ok=True)
                    old.with_suffix('.json').unlink(missing_ok=True)
        except OSError:
            pass  # A full/read-only cache must not replace a valid calendar.

    def read(self, request, redirect, *, limit=6_000_000, timeout=20, image=False, min_interval=0):
        if not isinstance(request, urllib.request.Request):
            request = urllib.request.Request(request)
        if request.get_method() != 'GET':
            raise ValueError('采集网络层仅允许只读 GET 请求')
        identity = request.full_url + json.dumps(sorted((k.lower(), v) for k, v in request.header_items()), ensure_ascii=True)
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.condition:
            cached = self._cached(key)
            if cached and len(cached['data']) > limit:
                cached = None
            ttl = 86400 if image else 300
            if cached:
                directives = cached['headers'].get('cache-control', '').lower()
                if 'no-cache' in directives:
                    ttl = 0
                age = re.search(r'max-age\s*=\s*(\d+)', directives)
                if age:
                    ttl = min(ttl, int(age[1]))
            if cached and len(cached['data']) <= limit and self.clock() - cached['time'] < ttl:
                self.cache_hits += 1
                return Response(cached['data'], cached['headers'])
        if cached:
            for header, value in [('If-None-Match', cached['headers'].get('etag')), ('If-Modified-Since', cached['headers'].get('last-modified'))]:
                if value:
                    request.add_header(header, value)

        class Follow(Exception):
            def __init__(self, request):
                self.request = request
        class ControlledRedirect(redirect):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                try:
                    checked = super().redirect_request(req, fp, code, msg, headers, newurl)
                finally:
                    fp.close()
                raise Follow(checked)

        with self.job():
            for _ in range(4):
                site = site_for(request.full_url)
                self._claim(site, min_interval)
                error, retry_after, blocked = None, None, False
                try:
                    opener = self.opener or urllib.request.build_opener(ControlledRedirect())
                    with opener.open(request, timeout=timeout) as response:
                        data = response.read(limit + 1)
                        headers = {k.lower(): v for k, v in response.headers.items()}
                    if len(data) > limit:
                        raise ValueError('响应超过大小限制')
                    head = data[:3000].lower()
                    if 'text/html' in headers.get('content-type', '') and any(t in head for t in (b'<title>just a moment', b'cf-chl-', b'<title>access denied', b'<title>captcha')):
                        blocked = True
                        raise ValueError('站点返回访问验证，已暂停，不绕过验证')
                    self._cache(key, data, headers)
                    return Response(data, headers)
                except Follow as follow:
                    request = follow.request
                except urllib.error.HTTPError as e:
                    if e.code == 304 and cached:
                        headers = {**cached['headers'], **{k.lower(): v for k, v in e.headers.items()}}
                        e.close()
                        self._cache(key, cached['data'], headers)
                        self.cache_hits += 1
                        return Response(cached['data'], headers)
                    error, retry_after, blocked = 'HTTP ' + str(e.code), e.headers.get('Retry-After'), e.code in {401, 403}
                    e.close()
                    raise
                except (OSError, ValueError) as e:
                    error = str(e)
                    raise
                finally:
                    self._finish(site, error, retry_after, blocked)
        raise NetworkDeferred('跳转次数超过上限，已停止请求')
