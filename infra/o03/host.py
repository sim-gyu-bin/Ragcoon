"""O03 root 수신기 전용. Docker inspect에서 기존 컨테이너의 환경·비밀은 읽지 않는다."""

import base64
import fcntl
import graphlib
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ETC = Path('/etc/ragcoon-o03')
RUN = Path('/run/ragcoon-o03')
STATE = Path('/var/lib/ragcoon-o03')
PROJECT = 'ragcoon-o03'
PRIVATE = 'ragcoon-private'
O02_VOLUME = 'ragcoon-private_documents'
BASE_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LC_ALL': 'C', 'DOCKER_CONFIG': '/run/ragcoon-o03/empty-docker-config'}


def require(ok):
    if not ok:
        raise ValueError('contract')


def cmd(*args, data=None, timeout=30, extra=None):
    return subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=BASE_ENV | (extra or {}), cwd='/', timeout=timeout, check=True).stdout


def docker(*args):
    return cmd('docker', *args).decode().strip()


def inspect(kind, name, field):
    return docker(kind, 'inspect', '--format', field, name)


def label(kind, name, key):
    labels = '.Config.Labels' if kind == 'container' else '.Labels'
    return inspect(kind, name, '{{index ' + labels + ' "' + key + '"}}')


def containers(project):
    return docker('ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project).split()


def secure(path, mode):
    st = path.lstat()
    require(st.st_uid == 0 and st.st_gid == 0 and stat.S_IMODE(st.st_mode) == mode)
    require(stat.S_ISDIR(st.st_mode) if path in (ETC, RUN, STATE) else stat.S_ISREG(st.st_mode))


def atomic(path, data):
    tmp = path.with_suffix('.new')
    with tmp.open('w') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def lock(name, blocking=True):
    with (RUN / name).open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def unique(pairs):
    result = dict(pairs)
    require(len(pairs) == len(result))
    return result


def site():
    for filename in ('site.json', 'approved-revision', 'deploy.pub'):
        secure(ETC / filename, 0o600)
    cfg = json.loads((ETC / 'site.json').read_bytes(), object_pairs_hook=unique)
    require(set(cfg) == {'host', 'picksy_health_url', 'picksy_project', 'picksy_compose_file',
                         'api_repository', 'caddy_repository', 'ssh_source'})
    host_pattern = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.duckdns\.org'
    require(re.fullmatch(host_pattern, cfg['host']))
    url = urlsplit(cfg['picksy_health_url'])
    require(url.scheme == 'https' and re.fullmatch(host_pattern, url.netloc) and url.netloc != cfg['host']
            and not url.query and not url.fragment and re.fullmatch(r'/[a-zA-Z0-9_./-]*', url.path))
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', cfg['picksy_project']) and cfg['picksy_project'] not in (PROJECT, PRIVATE))
    require(Path(cfg['picksy_compose_file']).is_absolute() and Path(cfg['picksy_compose_file']).is_file())
    for k in ('api_repository', 'caddy_repository'):
        require(re.fullmatch(r'ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*', cfg[k]))
    require(cfg['api_repository'] != cfg['caddy_repository'])
    net = ipaddress.ip_network(cfg['ssh_source'])
    require(any(net.version == n.version and net.subnet_of(n) for n in map(ipaddress.ip_network,
                ('100.64.0.0/10', 'fd7a:115c:a1e0::/48', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))))
    revision = (ETC / 'approved-revision').read_text().strip()
    require(re.fullmatch('[0-9a-f]{40}', revision))
    return cfg, revision


def payload(raw, cfg, revision):
    require(0 < len(raw) <= 8192)
    msg = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    require(set(msg) == {'revision', 'api', 'caddy', 'username', 'token'} and all(type(v) is str for v in msg.values()))
    require(msg['revision'] == revision and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}(?:\\[bot\\])?', msg['username']))
    require(re.fullmatch(r'[A-Za-z0-9_.-]{1,4096}', msg['token']))
    for role in ('api', 'caddy'):
        require(re.fullmatch(re.escape(cfg[role + '_repository']) + '@sha256:[a-f0-9]{64}', msg[role]))
    return msg


class Engine(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def pull(ref, user, token, revision):
    # docker login/config.json 대신 로컬 데몬의 해당 pull 요청에만 인증을 전달한다.
    auth = base64.urlsafe_b64encode(json.dumps({'username': user, 'password': token,
                                               'serveraddress': 'ghcr.io'}).encode()).decode()
    api = docker('version', '--format', '{{.Server.APIVersion}}')
    require(re.fullmatch(r'1\.\d+', api))
    conn = Engine('localhost', timeout=60)
    try:
        conn.request('POST', '/v' + api + '/images/create?' + urlencode({'fromImage': ref, 'platform': 'linux/arm64'}),
                     body=b'', headers={'X-Registry-Auth': auth})
        response = conn.getresponse()
        require(response.status == 200)
        while line := response.readline(1048576):
            require(len(line) < 1048576 and not {'error', 'errorDetail'}.intersection(json.loads(line)))
    finally:
        conn.close()
    require(inspect('image', ref, '{{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}')
            == 'linux/arm64 ' + revision)
    require(ref in json.loads(inspect('image', ref, '{{json .RepoDigests}}')))


def healthy(cid):
    return inspect('container', cid, '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}') == 'true healthy'


def until(check, seconds):
    end = time.monotonic() + seconds
    while not check():
        require(time.monotonic() < end)
        time.sleep(1)


def https(url, path=None, method='GET', data=None, auth=False):
    host = urlsplit(url).netloc
    args = ['curl', '-q', '--proto', '=https', '--noproxy', '*', '--silent', '--max-time', '4', '--max-filesize', '1048576',
            '--resolve', host + ':443:127.0.0.1', '--write-out', '\n%{http_code}', '--request', method]
    if auth:
        args += ['--netrc-file', str(ETC / 'access.netrc')]
    if data is not None:
        args += ['--header', 'Content-Type: application/json' if path == '/api/search' else 'Content-Type: text/markdown',
                 '--data-binary', '@-']
    try:
        body, code = cmd(*args, url + (path or ''), data=data, timeout=6).rsplit(b'\n', 1)
        return int(code), body
    except subprocess.SubprocessError:
        return 0, b''


def ports(cid):
    return json.loads(inspect('container', cid, '{{json .NetworkSettings.Ports}}')) or {}


def picksy(cfg):
    # 완료된 일회성 마이그레이션은 중지·복구 대상이 아니다.
    ids = docker('ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + cfg['picksy_project']).split()
    require(len(ids) == 4)
    by_service, deps, owners = {}, {}, set()
    for cid in ids:
        require(re.fullmatch('[0-9a-f]{64}', cid) and healthy(cid))
        require(label('container', cid, 'com.docker.compose.project.config_files') == cfg['picksy_compose_file'])
        service = label('container', cid, 'com.docker.compose.service')
        require(service and service not in by_service)
        by_service[service] = cid
        depends = label('container', cid, 'com.docker.compose.depends_on')
        deps[service] = [d.split(':')[0] for d in depends.split(',') if d]
        for bindings in ports(cid).values():
            if any(b['HostPort'] == '443' for b in bindings or []):
                owners.add(cid)
    require(set(by_service) == {'proxy-caddy', 'inference-d1', 'pool-manager', 'api-rust'})
    require(len(owners) == 1 and https(cfg['picksy_health_url'])[0] == 200)
    active_deps = {s: [d for d in ds if d in by_service] for s, ds in deps.items()}
    return [by_service[s] for s in graphlib.TopologicalSorter(active_deps).static_order()]


def private():
    require(inspect('volume', O02_VOLUME, '{{.Name}}') == O02_VOLUME)
    ids = containers(PRIVATE)
    require(len(ids) == 2)
    for cid in ids:
        service = label('container', cid, 'com.docker.compose.service')
        require(inspect('container', cid, '{{.State.Running}}') == 'true')
        bindings = [(p, b['HostIp'], b['HostPort']) for p, bs in ports(cid).items() for b in bs or []]
        if service == 'api':
            require(not bindings and inspect('container', cid, '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}') == O02_VOLUME)
        else:
            require(service == 'caddy' and bindings == [('80/tcp', '127.0.0.1', '18730')])
    require(len({label('container', cid, 'com.docker.compose.service') for cid in ids}) == 2)
