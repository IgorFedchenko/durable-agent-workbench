"""Executed Docker controls, including known-existing sentinels and network controls."""

from __future__ import annotations

import json
import secrets
import subprocess
import time
import uuid

import pytest

from agent_workbench.engine import Engine
from agent_workbench.models import RunConfig
from agent_workbench.sandbox import DockerSandbox, SandboxError
from agent_workbench.util import ROOT
from tests.helpers import raw_run, wait_for

pytestmark = pytest.mark.docker


def docker(*args, check=True, timeout=30):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def runtime(store):
    sandbox = DockerSandbox(store, wall_seconds=12)
    engine = Engine(store, sandbox=sandbox)
    root = engine.create("Boundary probe", RunConfig(scenario="total"))
    yield sandbox, root
    try:
        sandbox.cleanup(root)
        assert not sandbox.staging_namespace(root).exists()
        assert not store.containers(root)
    finally:
        engine.tracer_provider.shutdown()


def execute(runtime, source):
    sandbox, root = runtime
    return sandbox.execute(
        root, root, "probe-" + uuid.uuid4().hex[:8], source, ROOT / "datasets/orders.csv"
    )


def inspection(runtime):
    sandbox, root = runtime
    return next(
        e["payload"] for e in reversed(sandbox.store.events(root)) if e["kind"] == "sandbox_created"
    )


def assert_removed(runtime):
    """No call resources remain; an active root may retain only stable empty metadata."""
    sandbox, root = runtime
    assert not sandbox.store.containers(root)
    namespace = sandbox.staging_namespace(root)
    if namespace.exists():
        assert not namespace.is_symlink() and namespace.is_dir()
        assert not list(namespace.iterdir()), "call inputs/attempts must not survive cleanup"
        metadata = namespace.stat()
        assert metadata.st_mode & 0o777 == 0o700
        assert sandbox.store.run(root)["id"] == root
        previous = getattr(sandbox, "_observed_namespace_inode", None)
        if previous is not None:
            assert metadata.st_ino == previous, "same-root bind ancestor identity changed"
        sandbox._observed_namespace_inode = metadata.st_ino
    assert docker("ps", "-aq", "--filter", f"label=workbench.root={root}").stdout.strip() == ""


def test_sandbox_effective_configuration(runtime):
    result = execute(
        runtime,
        "import json,pathlib\n"
        "print(json.dumps({name:pathlib.Path('/sys/fs/cgroup/'+name+'.max').read_text() "
        "for name in ['cpu','memory','pids']}))",
    )
    assert result["ok"]
    data = result["data"]
    cfg = inspection(runtime)
    host = cfg["host_config"]
    assert host["NetworkMode"] == "none"
    assert cfg["user"] == "65532:65532" and host["ReadonlyRootfs"] and not host["Privileged"]
    assert "ALL" in host["CapDrop"] and "no-new-privileges=true" in host["SecurityOpt"]
    assert not any("unconfined" in x for x in host["SecurityOpt"])
    assert (
        host["NanoCpus"] == 500000000
        and host["Memory"] == 128 * 1024 * 1024
        and host["MemorySwap"] == host["Memory"]
        and host["PidsLimit"] == 32
    )
    assert int(data["memory"]) == host["Memory"] and int(data["pids"]) == 32
    quota, period = map(int, data["cpu"].split())
    assert quota / period == 0.5
    assert (
        set(host["Tmpfs"]) == {"/scratch", "/tmp"} and "size=8388608" in host["Tmpfs"]["/scratch"]
    )
    assert (
        len(host["Mounts"]) == 1
        and host["Mounts"][0]["ReadOnly"]
        and host["Mounts"][0]["Target"] == "/input"
    )
    assert host["PidMode"] == "" and host["IpcMode"] != "host" and not host["Devices"]
    assert_removed(runtime)


def test_sandbox_nonroot_capabilities_rootfs(runtime):
    source = """import json,os,pathlib
lines=pathlib.Path('/proc/self/status').read_text().splitlines()
status=dict(line.split(':',1) for line in lines if ':' in line)
failed=[]
for path in ['/rootfs-write-probe','/input/write-probe']:
 try: pathlib.Path(path).write_text('bad')
 except OSError as error: failed.append(error.errno)
pathlib.Path('/scratch/ok').write_text('allowed')
print(json.dumps({'uid':os.getuid(),'euid':os.geteuid(),'caps':status['CapEff'].strip(),'nnp':status['NoNewPrivs'].strip(),'seccomp':status['Seccomp'].strip(),'failed':failed,'scratch':pathlib.Path('/scratch/ok').read_text()}))"""
    result = execute(runtime, source)
    assert result["ok"]
    data = result["data"]
    assert (
        data["uid"] == data["euid"] == 65532
        and int(data["caps"], 16) == 0
        and data["nnp"] == "1"
        and data["seccomp"] == "2"
    )
    assert (
        len(data["failed"]) == 2
        and set(data["failed"]) <= {13, 30}
        and data["scratch"] == "allowed"
    )
    assert_removed(runtime)


def test_sandbox_host_and_sibling_sentinels(runtime, tmp_path):
    sandbox, root = runtime
    secret = "sentinel-" + secrets.token_hex(20)
    host = tmp_path / "known-existing-host"
    host.write_text(secret)
    assert host.read_text() == secret
    sibling_root = raw_run(sandbox.store)
    sibling = sandbox.staging_namespace(sibling_root) / "attempt-control"
    sibling.mkdir(parents=True)
    (sibling / "sentinel").write_text(secret)
    image = sandbox.store.run(root)["manifest"]["sandbox_image_id"]
    control = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--user",
        "65532:65532",
        "--memory",
        "64m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "16",
        "--mount",
        f"type=bind,src={sibling},dst=/input,readonly",
        "--entrypoint",
        "python",
        image,
        "-c",
        "print(open('/input/sentinel').read())",
    )
    assert secret in control.stdout
    paths = [
        str(host),
        str(sibling / "sentinel"),
        "/input/sentinel",
        "/host" + str(host),
        "/proc/1/root" + str(host),
        "/var/run/docker.sock",
        str(sandbox.store.path),
    ]
    source = f"""import json,pathlib
found=[]
for path in {paths!r}:
 try: found.append(pathlib.Path(path).read_text())
 except (OSError,UnicodeError): pass
print(json.dumps({{'found':found}}))"""
    result = execute(runtime, source)
    assert result["ok"] and secret not in json.dumps(result) and not result["data"]["found"]
    assert host.read_text() == secret and (sibling / "sentinel").read_text() == secret
    assert_removed(runtime)
    assert sibling.exists()
    sandbox.cleanup(sibling_root)


def test_sandbox_network_ip_and_dns(runtime):
    sandbox, root = runtime
    image = sandbox.store.run(root)["manifest"]["sandbox_image_id"]
    name = "daw-net-control-" + uuid.uuid4().hex[:12]
    network = "daw-test-net-" + uuid.uuid4().hex[:12]
    nonce = secrets.token_hex(8)
    listener = """import socket,threading,time,json
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',18080));s.listen()
def tcp():
 while True:
  c,a=s.accept();data=c.recv(100);print('TCP:'+data.decode(),flush=True);c.sendall(data);c.close()
threading.Thread(target=tcp,daemon=True).start()
u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.bind(('0.0.0.0',15353))
print('READY',flush=True)
while True:
 data,a=u.recvfrom(512);print('DNS:'+data.hex(),flush=True);u.sendto(data[:2]+b'\\x81\\x80'+data[4:],a)
"""
    docker("network", "create", network)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--memory",
            "64m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "16",
            "--entrypoint",
            "python",
            image,
            "-u",
            "-c",
            listener,
        )
        address = json.loads(docker("inspect", name).stdout)[0]["NetworkSettings"]["Networks"][
            network
        ]["IPAddress"]
        wait_for(lambda: "READY" in docker("logs", name).stdout, 10)
        query = (
            b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            + bytes([len(nonce)])
            + nonce.encode()
            + b"\x07invalid\x00\x00\x01\x00\x01"
        )
        probe = f"""import socket,json
out={{}}
for kind,port in [('tcp',18080),('dns',15353)]:
 type_=socket.SOCK_STREAM if kind=='tcp' else socket.SOCK_DGRAM
 s=socket.socket(socket.AF_INET,type_);s.settimeout(.7)
 try:
  if kind=='tcp':
   s.connect(({address!r},port));s.sendall({nonce.encode()!r})
   out[kind]=s.recv(100).decode()
  else: s.sendto({query!r},({address!r},port));out[kind]=s.recv(512).hex()
 except OSError as e: out[kind]=type(e).__name__
 finally:s.close()
try:out['resolver']=socket.getaddrinfo({nonce + ".invalid"!r},80)
except OSError as e:out['resolver']=type(e).__name__
print(json.dumps(out))"""
        control = docker(
            "run",
            "--rm",
            "--network",
            network,
            "--memory",
            "64m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "16",
            "--entrypoint",
            "python",
            image,
            "-c",
            probe,
        )
        positive = json.loads(control.stdout)
        assert positive["tcp"] == nonce and positive["dns"].startswith("12348180"), positive
        before = docker("logs", name).stdout
        result = execute(runtime, probe)
        assert result["ok"]
        blocked = result["data"]
        assert (
            blocked["tcp"] != nonce
            and blocked["dns"] != positive["dns"]
            and blocked["resolver"] == "gaierror"
        )
        assert inspection(runtime)["host_config"]["NetworkMode"] == "none"
        assert docker("logs", name).stdout == before, "isolated target reached controlled listener"
    finally:
        docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)
    assert_removed(runtime)


def test_sandbox_output_bounded(runtime):
    sandbox, _ = runtime
    sandbox.output_limit = 4096
    for fd in [1, 2, 0]:
        code = f"import os\nwhile True:\n os.write({fd if fd else 1},b'\\xff'*1024)\n" + (
            ' os.write(2,b"x"*1024)\n' if fd == 0 else ""
        )
        with pytest.raises(SandboxError, match="output bound"):
            execute(runtime, code)
        assert_removed(runtime)


def test_sandbox_scratch_exhaustion(runtime):
    source = """import json,pathlib
count=0; error=None
try:
 for i in range(40): pathlib.Path('/scratch/f'+str(i)).write_bytes(b'x'*524288);count+=1
except OSError as e:error=e.errno
print(json.dumps({'files':count,'error':error}))"""
    result = execute(runtime, source)
    assert result["ok"] and result["data"]["error"] == 28 and result["data"]["files"] <= 16
    assert_removed(runtime)


def test_sandbox_pid_exhaustion(runtime):
    source = """import os,json,time,pathlib
children=[];error=None
try:
 for _ in range(64):
  pid=os.fork()
  if pid==0:time.sleep(4);os._exit(0)
  children.append(pid)
except OSError as e:error=e.errno
peak=int(pathlib.Path('/sys/fs/cgroup/pids.current').read_text())
for pid in children:
 try:os.kill(pid,9);os.waitpid(pid,0)
 except OSError:pass
print(json.dumps({'error':error,'peak':peak,'children':len(children)}))"""
    result = execute(runtime, source)
    assert result["ok"]
    assert (
        result["data"]["error"] == 11
        and result["data"]["peak"] <= 32
        and result["data"]["children"] > 0
    )
    assert_removed(runtime)


def test_sandbox_memory_limit(runtime):
    result = execute(runtime, "x=[]\nwhile True:x.append(bytearray(4*1024*1024))")
    assert (
        not result["ok"]
        and (result["oom_killed"] or "MemoryError" in result["stderr"])
        and result["exit_code"] != 0
    )
    assert inspection(runtime)["host_config"]["Memory"] == 128 * 1024 * 1024
    assert_removed(runtime)


def test_sandbox_cpu_quota(runtime):
    source = """import json,time,pathlib
before=pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text();start=time.monotonic();cpu=time.process_time()
x=0
while time.monotonic()-start<3:x=(x+1)%1000003
print(json.dumps({'wall':time.monotonic()-start,'cpu':time.process_time()-cpu,'before':before,'after':pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text()}))"""
    result = execute(runtime, source)
    assert result["ok"]
    data = result["data"]

    def stats(text):
        return {k: int(v) for k, v in (line.split() for line in text.splitlines())}

    assert data["wall"] >= 3 and data["cpu"] / data["wall"] <= 0.75
    assert stats(data["after"])["nr_throttled"] > stats(data["before"])["nr_throttled"]
    assert_removed(runtime)


def test_sandbox_wall_timeout_cleanup(runtime):
    sandbox, _ = runtime
    sandbox.wall_seconds = 1
    for source in [
        "while True: pass",
        "import subprocess,time\n"
        'subprocess.Popen(["python","-c","import time;time.sleep(50)"])\n'
        "time.sleep(50)",
    ]:
        started = time.monotonic()
        with pytest.raises(SandboxError, match="wall timeout"):
            execute(runtime, source)
        assert time.monotonic() - started < 6
        assert_removed(runtime)


def test_sandbox_cleanup_all_paths(runtime):
    for source in [
        'import json;print(json.dumps({"ok":True}))',
        'raise RuntimeError("bounded error")',
    ]:
        execute(runtime, source)
        assert_removed(runtime)
    with pytest.raises(SandboxError):
        execute(runtime, 'print("not json")')
    assert_removed(runtime)


def test_frozen_image_identity_after_retag(runtime):
    sandbox, root = runtime
    original = sandbox.store.run(root)["manifest"]["sandbox_image_id"]
    tag = "durable-agent-test-retag:" + uuid.uuid4().hex[:12]
    # Give the tag an independently existing harmless image, then retain frozen identity.
    docker("tag", original, tag)
    try:
        sandbox.image = tag
        # Docker metadata-only commit produces a different local image without build/network.
        name = "daw-retag-fixture-" + uuid.uuid4().hex[:10]
        docker("create", "--name", name, "--entrypoint", "python", original, "-c", "pass")
        try:
            changed = docker(
                "commit", "--change", "LABEL workbench.test.retag=true", name
            ).stdout.strip()
        finally:
            docker("rm", name)
        assert changed != original
        docker("tag", changed, tag)
        assert sandbox.image_identity() == changed
        assert execute(runtime, 'import json;print(json.dumps({"total":175}))')["ok"]
        assert inspection(runtime)["image_id"] == original
    finally:
        docker("image", "rm", tag, check=False)
        if "changed" in locals():
            docker("image", "rm", changed, check=False)
