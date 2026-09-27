"""scenerender.publish: destination uploaders, all offline (local http.server, patched subprocess)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scenerender import publish as pub
from scenerender.publish import PublishError, publish

EMPTY = hashlib.sha256(b"").hexdigest()


# ------------------------------------------------------------------------------ SigV4 vectors

SUITE = dict(secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", region="us-east-1", service="service",
             amz_date="20150830T123600Z")
# aws-sig-v4-test-suite (get-vanilla, get-vanilla-query-order-key-case, post-vanilla): host example.amazonaws.com
SUITE_HEADERS = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}


@pytest.mark.parametrize("method,url,expected", [
    ("GET", "https://example.amazonaws.com/", "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"),
    ("GET", "https://example.amazonaws.com/?Param2=value2&Param1=value1",
     "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"),
    ("POST", "https://example.amazonaws.com/", "5da7c1a2acd57cee7505fc6676e4e544621c30862966e37dddb68e92efbe5d6b"),
])
def test_sigv4_test_suite(method, url, expected):
    assert pub.sigv4_signature(method, url, SUITE_HEADERS, EMPTY, **SUITE) == expected


def test_sigv4_headers_get_vanilla():
    h = pub.sigv4_headers("GET", "https://example.amazonaws.com/", {}, EMPTY, access_key="AKIDEXAMPLE", **SUITE)
    assert h["host"] == "example.amazonaws.com" and h["x-amz-date"] == "20150830T123600Z"
    assert "x-amz-content-sha256" not in h
    assert h["Authorization"] == ("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                                  "SignedHeaders=host;x-amz-date, "
                                  "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_sigv4_s3_get_object_example():
    url = "https://examplebucket.s3.amazonaws.com/test.txt"
    headers = {"Host": "examplebucket.s3.amazonaws.com", "Range": "bytes=0-9", "x-amz-content-sha256": EMPTY,
               "x-amz-date": "20130524T000000Z"}
    creq, signed = pub.canonical_request("GET", url, headers, EMPTY)
    assert creq == ("GET\n/test.txt\n\nhost:examplebucket.s3.amazonaws.com\nrange:bytes=0-9\n"
                    f"x-amz-content-sha256:{EMPTY}\nx-amz-date:20130524T000000Z\n\n"
                    f"host;range;x-amz-content-sha256;x-amz-date\n{EMPTY}")
    sig = pub.sigv4_signature("GET", url, headers, EMPTY, secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                              region="us-east-1", service="s3", amz_date="20130524T000000Z")
    assert sig == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


def test_canonical_uri_encoding_not_doubled():
    creq, _ = pub.canonical_request("PUT", "http://h/b/a%20b/%E1%88%B4~x.png?uploads", {"host": "h"}, EMPTY)
    assert creq.split("\n")[1:3] == ["/b/a%20b/%E1%88%B4~x.png", "uploads="]


# ------------------------------------------------------------------------------ local server

class Server:
    def __init__(self):
        self.records: list[dict] = []
        self.responder = lambda r: (200, {}, b"")
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                rec = {"method": self.command, "path": self.path, "headers": dict(self.headers),
                       "body": self.rfile.read(n) if n else b""}
                outer.records.append(rec)
                status, hs, body = outer.responder(rec)
                self.send_response(status)
                for k, v in hs.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_PUT = do_POST = do_DELETE = _any

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def srv():
    s = Server()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(pub, "_sleep", sleeps.append)
    real = pub._import_optional
    monkeypatch.setattr(pub, "_import_optional", lambda n: None if n in ("boto3", "paramiko") else real(n))
    pub._TOKENS.clear()
    return sleeps


@pytest.fixture
def frames(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"f{i:04d}.png"
        p.write_bytes(bytes([i]) * (10 + i))
        paths.append(str(p))
    return paths


def lower(h):
    return {k.lower(): v for k, v in h.items()}


# ------------------------------------------------------------------------------ http-put / webhook

def test_http_put_single_exact_path(srv, frames):
    env = {"SCENERENDER_CDN_HTTP_HEADERS": "X-One: 1; X-Two: two", "SCENERENDER_CDN_HTTP_BEARER_TOKEN": "tok-secret"}
    out = publish("http-put", srv.url + "/up/final.png", frames[:1], credentials="cdn", env=env)
    assert out == [srv.url + "/up/final.png"]
    (r,) = srv.records
    h = lower(r["headers"])
    assert r["method"] == "PUT" and r["path"] == "/up/final.png" and r["body"] == open(frames[0], "rb").read()
    assert h["content-type"] == "image/png" and h["x-one"] == "1" and h["x-two"] == "two"
    assert h["authorization"] == "Bearer tok-secret"


def test_http_put_prefix_and_error_hides_secret(srv, frames):
    env = {"SCENERENDER_HTTP_AUTHORIZATION": "Basic c2VjcmV0LXZhbHVl"}
    out = publish("http-put", srv.url + "/seq", frames, env=env)
    assert [r["path"] for r in srv.records] == ["/seq/f0000.png", "/seq/f0001.png", "/seq/f0002.png"]
    assert out[2] == srv.url + "/seq/f0002.png"
    assert all(lower(r["headers"])["authorization"] == env["SCENERENDER_HTTP_AUTHORIZATION"] for r in srv.records)
    srv.responder = lambda r: (403, {}, b"denied Basic c2VjcmV0LXZhbHVl")
    with pytest.raises(PublishError) as ei:
        publish("http-put", f"{srv.url}/x/?token=abc", frames, env=env)
    msg = str(ei.value)
    assert "403" in msg and "c2VjcmV0LXZhbHVl" not in msg and "token=abc" not in msg


def test_retry_on_503_then_success(srv, frames, fast):
    calls = iter([503, 429, 200])
    srv.responder = lambda r: (next(calls), {}, b"")
    publish("http-put", srv.url + "/a.png", frames[:1], env={})
    assert len(srv.records) == 3 and fast == [0.5, 1.0]
    assert srv.records[2]["body"] == open(frames[0], "rb").read()


def test_retry_gives_up_and_4xx_not_retried(srv, frames, fast):
    srv.responder = lambda r: (500, {}, b"")
    with pytest.raises(PublishError, match="HTTP 500"):
        publish("http-put", srv.url + "/a.png", frames[:1], env={})
    assert len(srv.records) == 3
    srv.records.clear()
    srv.responder = lambda r: (404, {}, b"")
    with pytest.raises(PublishError, match="HTTP 404"):
        publish("http-put", srv.url + "/a.png", frames[:1], env={})
    assert len(srv.records) == 1


def test_connection_refused_is_publish_error(frames):
    with pytest.raises(PublishError):
        publish("http-put", "http://user:pw@127.0.0.1:9/a.png", frames[:1], env={})


def test_webhook_payload_and_signature(srv, frames):
    env = {"SCENERENDER_HOOK_WEBHOOK_SECRET": "s3cr3t", "SCENERENDER_HOOK_HTTP_HEADERS": "X-Env: yes"}
    out = publish("webhook", srv.url + "/hook", frames, credentials="hook", job="promo", metadata={"k": 1}, env=env)
    assert out == [srv.url + "/hook"]
    (r,) = srv.records
    h = lower(r["headers"])
    assert r["method"] == "POST" and h["content-type"] == "application/json" and h["x-env"] == "yes"
    assert h["x-scenerender-signature"] == "sha256=" + hmac.new(b"s3cr3t", r["body"], hashlib.sha256).hexdigest()
    doc = json.loads(r["body"])
    assert doc["event"] == "render.complete" and doc["job"] == "promo" and doc["metadata"] == {"k": 1}
    assert doc["generator"] == "scenerender" and len(doc["files"]) == 3
    f1 = doc["files"][1]
    data = open(frames[1], "rb").read()
    assert f1 == {"path": frames[1], "name": "f0001.png", "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "contentType": "image/png"}


# ------------------------------------------------------------------------------ S3

def s3_responder(store):
    def respond(r):
        path, _, query = r["path"].partition("?")
        q = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
        if r["method"] == "POST" and "uploads" in q:
            return 200, {}, b"<InitiateMultipartUploadResult><UploadId>up/1+x</UploadId></InitiateMultipartUploadResult>"
        if r["method"] == "PUT" and "partNumber" in q:
            store.setdefault(("parts", path), {})[int(q["partNumber"])] = r["body"]
            return 200, {"ETag": f'"etag{q["partNumber"]}"'}, b""
        if r["method"] == "POST" and "uploadId" in q:
            parts = store[("parts", path)]
            store[path] = b"".join(parts[int(n)] for n in re.findall(rb"<PartNumber>(\d+)</PartNumber>", r["body"]))
            return 200, {}, b"<CompleteMultipartUploadResult/>"
        store[path] = r["body"]
        return 200, {}, b""
    return respond


def check_sigv4(r, secret):
    h = r["headers"]
    auth = h["Authorization"]
    m = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=(\w+)/(\d{8})/([\w-]+)/s3/aws4_request, "
                     r"SignedHeaders=([\w;-]+), Signature=([0-9a-f]{64})", auth)
    assert m, auth
    signed = {k: v for k, v in h.items() if k.lower() in m.group(4).split(";")}
    phash = lower(h)["x-amz-content-sha256"]
    sig = pub.sigv4_signature(r["method"], "http://" + lower(h)["host"] + r["path"], signed, phash,
                              secret_key=secret, region=m.group(3), service="s3", amz_date=lower(h)["x-amz-date"])
    assert sig == m.group(5)
    return m, phash


def test_s3_manual_sigv4_path_style(srv, frames):
    store: dict = {}
    srv.responder = s3_responder(store)
    env = {"SCENERENDER_PROD_AWS_ACCESS_KEY_ID": "AKIDTEST", "SCENERENDER_PROD_AWS_SECRET_ACCESS_KEY": "topsecretkey",
           "SCENERENDER_PROD_AWS_REGION": "eu-west-1", "SCENERENDER_PROD_AWS_SESSION_TOKEN": "sess-token",
           "AWS_ENDPOINT_URL": srv.url, "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent", "AWS_CONFIG_FILE": "/nonexistent"}
    out = publish("s3", "s3://bkt/renders/", frames, credentials="prod", env=env)
    assert out == [f"s3://bkt/renders/f000{i}.png" for i in range(3)]
    for i, r in enumerate(srv.records):
        assert r["path"] == f"/bkt/renders/f000{i}.png"
        m, phash = check_sigv4(r, "topsecretkey")
        assert m.group(1) == "AKIDTEST" and m.group(3) == "eu-west-1"
        assert phash == hashlib.sha256(r["body"]).hexdigest()  # http:// endpoint: real payload hash
        assert lower(r["headers"])["x-amz-security-token"] == "sess-token"
        assert "x-amz-security-token" in m.group(4)


def test_s3_multipart_and_profile_file(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "S3_SINGLE_MAX", 10)
    monkeypatch.setattr(pub, "S3_PART_SIZE", 8)
    big = tmp_path / "movie.mp4"
    big.write_bytes(bytes(range(20)) + b"tail")
    creds = tmp_path / "credentials"
    creds.write_text("[studio]\naws_access_key_id = AKIDFILE\naws_secret_access_key = filesecret\n")
    conf = tmp_path / "config"
    conf.write_text(f"[profile studio]\nregion = ap-south-1\n")
    store: dict = {}
    srv.responder = s3_responder(store)
    env = {"AWS_SHARED_CREDENTIALS_FILE": str(creds), "AWS_CONFIG_FILE": str(conf), "AWS_ENDPOINT_URL": srv.url}
    assert publish("s3", "s3://bkt/out/final.mp4", [str(big)], credentials="studio", env=env) == ["s3://bkt/out/final.mp4"]
    assert store["/bkt/out/final.mp4"] == big.read_bytes()
    methods = [(r["method"], r["path"].partition("?")[2]) for r in srv.records]
    assert methods[0] == ("POST", "uploads") and len(methods) == 5
    assert methods[1][1] == "partNumber=1&uploadId=up%2F1%2Bx"
    assert b'<ETag>"etag3"</ETag>' in srv.records[-1]["body"]
    for r in srv.records:
        m, _ = check_sigv4(r, "filesecret")
        assert m.group(1) == "AKIDFILE" and m.group(3) == "ap-south-1"


def test_s3_missing_credentials(frames):
    env = {"AWS_SHARED_CREDENTIALS_FILE": "/nonexistent", "AWS_CONFIG_FILE": "/nonexistent"}
    with pytest.raises(PublishError, match="no AWS credentials"):
        publish("s3", "s3://bkt/x/", frames, env=env)


def test_s3_multipart_aborts_on_failure(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "S3_SINGLE_MAX", 4)
    monkeypatch.setattr(pub, "S3_PART_SIZE", 4)
    f = tmp_path / "a.bin"
    f.write_bytes(b"0123456789")
    store: dict = {}
    ok = s3_responder(store)
    srv.responder = lambda r: (403, {}, b"<Error><Code>AccessDenied</Code></Error>") if "partNumber=2" in r["path"] else ok(r)
    env = {"AWS_ACCESS_KEY_ID": "AKID", "AWS_SECRET_ACCESS_KEY": "verysecret", "AWS_ENDPOINT_URL": srv.url}
    with pytest.raises(PublishError, match="AccessDenied") as ei:
        publish("s3", "s3://bkt/a.bin", [str(f)], env=env)
    assert "verysecret" not in str(ei.value)
    assert srv.records[-1]["method"] == "DELETE"


# ------------------------------------------------------------------------------ Azure

def test_azure_sas(srv, frames):
    sas = "sv=2021-08-06&ss=b&sig=SuperSecretSig%3D"
    env = {"SCENERENDER_AZ_AZURE_STORAGE_SAS_TOKEN": "?" + sas}
    out = publish("azure-blob", srv.url + "/cont/shots/", frames, credentials="az", env=env)
    assert out == [f"{srv.url}/cont/shots/f000{i}.png" for i in range(3)]
    r = srv.records[0]
    assert r["path"] == f"/cont/shots/f0000.png?{sas}"
    h = lower(r["headers"])
    assert h["x-ms-blob-type"] == "BlockBlob" and h["x-ms-version"] == "2021-08-06" and "authorization" not in h
    srv.responder = lambda r: (403, {}, b"<Error><Code>AuthenticationFailed</Code><M>sig=SuperSecretSig=</M></Error>")
    with pytest.raises(PublishError) as ei:
        publish("azure-blob", srv.url + "/cont/a.png", frames[:1], credentials="az", env=env)
    msg = str(ei.value)
    assert "AuthenticationFailed" in msg and "SuperSecretSig" not in msg and "sv=" not in msg


def azure_env(srv):
    key = base64.b64encode(b"k" * 32).decode()
    return key, {"AZURE_STORAGE_CONNECTION_STRING":
                 f"DefaultEndpointsProtocol=http;AccountName=acct;AccountKey={key};BlobEndpoint={srv.url}/acct;"}


def verify_shared_key(srv, r, key):
    h = {k: v for k, v in r["headers"].items() if k.lower() != "authorization"}
    assert r["headers"]["Authorization"] == pub._azure_sign(r["method"], srv.url + r["path"], h, "acct", key)


def test_azure_shared_key(srv, frames):
    key, env = azure_env(srv)
    out = publish("azure-blob", "azure://acct/cont/one.png", frames[:1], env=env)
    assert out == [f"{srv.url}/acct/cont/one.png"]
    (r,) = srv.records
    assert r["path"] == "/acct/cont/one.png"
    assert re.fullmatch(r"SharedKey acct:[A-Za-z0-9+/]{43}=", r["headers"]["Authorization"])
    verify_shared_key(srv, r, key)
    srv.responder = lambda r: (403, {}, b"")
    with pytest.raises(PublishError) as ei:
        publish("azure-blob", "azure://acct/cont/one.png", frames[:1], env=env)
    assert key not in str(ei.value)


def test_azure_block_list(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "AZURE_SINGLE_MAX", 5)
    monkeypatch.setattr(pub, "AZURE_BLOCK", 4)
    f = tmp_path / "clip.mov"
    f.write_bytes(b"abcdefghij")
    key, env = azure_env(srv)
    publish("azure-blob", "azure://acct/cont/dir/", [str(f)], env=env)
    blocks = {}
    for r in srv.records:
        verify_shared_key(srv, r, key)
        q = dict(urllib.parse.parse_qsl(r["path"].partition("?")[2]))
        assert r["path"].startswith("/acct/cont/dir/clip.mov?")
        if q["comp"] == "block":
            blocks[q["blockid"]] = r["body"]
    last = srv.records[-1]
    assert dict(urllib.parse.parse_qsl(last["path"].partition("?")[2])) == {"comp": "blocklist"}
    ids = re.findall(rb"<Latest>([^<]+)</Latest>", last["body"])
    assert len(ids) == 3 and b"".join(blocks[i.decode()] for i in ids) == f.read_bytes()
    assert lower(last["headers"])["x-ms-blob-content-type"] == "video/quicktime"


# ------------------------------------------------------------------------------ GCS

def test_gcs_bearer_media(srv, frames):
    env = {"SCENERENDER_G_GOOGLE_OAUTH_ACCESS_TOKEN": "ya29.secret", "STORAGE_EMULATOR_HOST": srv.url.split("//")[1]}
    out = publish("gcs", "gs://bkt/seq/", frames, credentials="g", env=env)
    assert out == [f"gs://bkt/seq/f000{i}.png" for i in range(3)]
    r = srv.records[0]
    assert r["path"] == "/upload/storage/v1/b/bkt/o?uploadType=media&name=seq%2Ff0000.png"
    assert lower(r["headers"])["authorization"] == "Bearer ya29.secret"
    assert r["body"] == open(frames[0], "rb").read()


def test_gcs_resumable(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(pub, "GCS_RESUMABLE_MIN", 5)
    monkeypatch.setattr(pub, "GCS_CHUNK", 4)
    f = tmp_path / "big.mp4"
    f.write_bytes(b"0123456789")
    got = bytearray()

    def respond(r):
        if "uploadType=resumable" in r["path"]:
            return 200, {"Location": srv.url + "/session/abc"}, b""
        got.extend(r["body"])
        end = int(r["headers"]["Content-Range"].split("-")[1].split("/")[0])
        return (308, {"Range": f"bytes=0-{end}"}, b"") if end < 9 else (200, {}, b"{}")

    srv.responder = respond
    env = {"GOOGLE_OAUTH_ACCESS_TOKEN": "tok", "SCENERENDER_GCS_ENDPOINT": srv.url}
    assert publish("gcs", "gs://bkt/final.mp4", [str(f)], env=env) == ["gs://bkt/final.mp4"]
    assert bytes(got) == f.read_bytes()
    assert [r["headers"].get("Content-Range") for r in srv.records[1:]] == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]


def test_gcs_service_account_jwt(srv, frames, tmp_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"type": "service_account", "client_email": "r@p.iam.gserviceaccount.com",
                              "private_key_id": "kid1", "private_key": pem, "token_uri": srv.url + "/token"}))

    def respond(r):
        if r["path"] == "/token":
            return 200, {"Content-Type": "application/json"}, json.dumps({"access_token": "minted", "expires_in": 3600}).encode()
        return 200, {}, b"{}"

    srv.responder = respond
    env = {"GOOGLE_APPLICATION_CREDENTIALS": str(sa), "STORAGE_EMULATOR_HOST": srv.url}
    publish("gcs", "gs://bkt/a/", frames, env=env)
    tok, *uploads = srv.records
    assert len(uploads) == 3 and all(lower(u["headers"])["authorization"] == "Bearer minted" for u in uploads)
    form = dict(urllib.parse.parse_qsl(tok["body"].decode()))
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    head, claims, sig = form["assertion"].split(".")
    dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    key.public_key().verify(dec(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert json.loads(dec(head)) == {"alg": "RS256", "typ": "JWT", "kid": "kid1"}
    c = json.loads(dec(claims))
    assert c["iss"] == "r@p.iam.gserviceaccount.com" and c["aud"] == srv.url + "/token"
    assert c["scope"] == pub.GCS_SCOPE and c["exp"] - c["iat"] == 3600
    publish("gcs", "gs://bkt/b.png", frames[:1], env=env)  # cached token: no second exchange
    assert sum(r["path"] == "/token" for r in srv.records) == 1


# ------------------------------------------------------------------------------ SFTP

def test_sftp_password_without_paramiko(frames):
    with pytest.raises(PublishError, match="paramiko"):
        publish("sftp", "sftp://u@h/x/", frames, credentials="box", env={"SCENERENDER_BOX_SFTP_PASSWORD": "pw1234"})


def test_sftp_openssh_command(frames, monkeypatch, tmp_path):
    calls = []

    class Res:
        returncode, stderr = 0, ""

    monkeypatch.setattr(pub.shutil, "which", lambda n: "/usr/bin/sftp")
    monkeypatch.setattr(pub.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Res())
    env = {"SCENERENDER_BOX_SFTP_KEY_FILE": "/keys/id_ed25519", "SCENERENDER_BOX_SFTP_KNOWN_HOSTS": "/keys/kh"}
    out = publish("sftp", "sftp://deploy@files.example:2222/srv/renders/", frames, credentials="box", env=env)
    assert out == [f"sftp://deploy@files.example:2222/srv/renders/f000{i}.png" for i in range(3)]
    (cmd, kw), = calls
    assert cmd[:5] == ["/usr/bin/sftp", "-b", "-", "-P", "2222"] and cmd[-1] == "deploy@files.example"
    assert "StrictHostKeyChecking=yes" in cmd and "BatchMode=yes" in cmd and "UserKnownHostsFile=/keys/kh" in cmd
    assert cmd[cmd.index("-i") + 1] == "/keys/id_ed25519"
    lines = kw["input"].splitlines()
    assert lines[:2] == ['-mkdir "/srv"', '-mkdir "/srv/renders"']
    assert lines[2:] == [f'put "{f}" "/srv/renders/f000{i}.png"' for i, f in enumerate(frames)]

    calls.clear()
    Res.returncode, Res.stderr = 1, "Permission denied (publickey)."
    env2 = {"SCENERENDER_SFTP_INSECURE_ACCEPT_HOST_KEY": "1"}
    with pytest.raises(PublishError, match="Permission denied"):
        publish("sftp", "sftp://h/~/out.png", frames[:1], env=env2)
    cmd, kw = calls[0]
    assert "StrictHostKeyChecking=accept-new" in cmd and "-i" not in cmd and cmd[-1] == "h"
    assert kw["input"] == f'put "{frames[0]}" "out.png"\n'


# ------------------------------------------------------------------------------ misc

def test_profile_prefix_and_kinds(frames):
    assert pub._Ctx({}, "prod-eu.v2", 1).pfx == "SCENERENDER_PROD_EU_V2_"
    assert pub._Ctx({}, None, 1).pfx == "SCENERENDER_"
    with pytest.raises(PublishError, match="output.py"):
        publish("file", "/tmp/x", frames, env={})
    with pytest.raises(PublishError, match="unsupported"):
        publish("ftp", "ftp://x/", frames, env={})
    with pytest.raises(PublishError, match="not found"):
        publish("http-put", "http://x/", ["/nonexistent/file.png"], env={})
    assert set(pub.LEVELS) == {"s3", "gcs", "azure-blob", "http-put", "sftp", "webhook"}
    assert pub.LEVELS["sftp"] == ("full", "paramiko, else the OpenSSH sftp client (key/agent auth)")
