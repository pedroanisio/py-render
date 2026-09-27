"""Publishing rendered files to <destination> targets (only with `scenerender render --publish`).

    publish(kind, uri, files, *, credentials=None, job="", metadata=None, env=None, timeout=60.0)
        -> one remote location per file (webhook: the webhook URL once); raises PublishError.

Kind "file" is handled by output.py. Only the standard library is required; boto3 and paramiko
are used when importable. Every failure raises PublishError whose message never contains a
secret: URLs are shown without userinfo, query string (SAS tokens) or fragment, and every
credential value read from the environment is masked in any message.

Naming: the remote name is the URI path used as a prefix plus the file's basename when the path
ends with "/", is empty, or several files are published (frame sequences); otherwise the single
file goes exactly to the URI's key/path. Files are uploaded one after the other and streamed
(never read into memory whole); Content-Type comes from `mimetypes` (default
application/octet-stream). Transient failures (HTTP 5xx/429, connection errors) are retried:
3 attempts with exponential backoff (0.5 s, 1 s; `_sleep` is patchable). Redirects are not
followed.

Credentials: the document only names a profile (`credentials="P"`); secrets come from the
environment. Profile P gives the prefix PFX = "SCENERENDER_" + P uppercased with every
non-alphanumeric changed to "_" + "_" (P = "prod-eu" -> SCENERENDER_PROD_EU_); without a profile
PFX is "SCENERENDER_". Each variable below is looked up as PFX+NAME first, then (where a standard
variable or config exists) the standard source.

s3      s3://bucket/key-or-prefix.  With boto3: Session(profile_name=P) when the AWS config has
        profile P, else a session from the variables below; client("s3").upload_file.
        Without boto3, AWS Signature V4 over urllib: PFX+AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
        / AWS_SESSION_TOKEN / AWS_REGION, then the plain AWS_* variables (AWS_DEFAULT_REGION too),
        then section [P] (else AWS_PROFILE, else "default") of ~/.aws/credentials and
        [profile P] of ~/.aws/config (AWS_SHARED_CREDENTIALS_FILE / AWS_CONFIG_FILE honoured).
        Region default us-east-1. PFX+AWS_ENDPOINT_URL / AWS_ENDPOINT_URL selects a custom
        endpoint with path-style addressing (MinIO etc.); else https://BUCKET.s3.REGION.amazonaws.com
        (path-style for bucket names containing dots). Over https the payload is
        UNSIGNED-PAYLOAD; over http its real SHA-256 is signed. One PUT up to 5 GiB, multipart
        upload above (64 MiB parts, more when 10000 parts would not suffice; aborted on failure).
gcs     gs://bucket/object-or-prefix.  PFX+GOOGLE_OAUTH_ACCESS_TOKEN / GOOGLE_OAUTH_ACCESS_TOKEN
        (bearer), else the JSON key file named by PFX+GOOGLE_APPLICATION_CREDENTIALS /
        GOOGLE_APPLICATION_CREDENTIALS: a service account (RS256 JWT for scope devstorage.read_write
        exchanged at its token_uri) or an authorized_user (refresh token); tokens are cached per
        process. JSON API media upload, resumable upload (8 MiB chunks) above 64 MiB.
        PFX+GCS_ENDPOINT / STORAGE_EMULATOR_HOST override the endpoint (anonymous when no
        credential is configured).
azure-blob  https://ACCOUNT.blob.core.windows.net/container/path or azure://account/container/path.
        PFX+AZURE_STORAGE_SAS_TOKEN (appended as query) or PFX+AZURE_STORAGE_CONNECTION_STRING,
        then the unprefixed variables. Connection string keys: AccountName, AccountKey (Shared Key
        signing, x-ms-version 2021-08-06), BlobEndpoint, SharedAccessSignature. Put Blob
        (BlockBlob); above 256 MiB Put Block (64 MiB) + Put Block List.
http-put  http(s)://...  PUT each file; success on 2xx.  PFX+HTTP_HEADERS ("Name: value" lines
        separated by newlines or ";"), PFX+HTTP_AUTHORIZATION (full Authorization value),
        PFX+HTTP_BEARER_TOKEN.  (No unprefixed fallback.)
sftp    sftp://[user@]host[:port]/abs/path (a path starting with "/~/" is relative to the home
        directory).  With paramiko: key PFX+SFTP_KEY_FILE (+ PFX+SFTP_KEY_PASSPHRASE) or
        PFX+SFTP_PASSWORD (else agent/default keys); known hosts PFX+SFTP_KNOWN_HOSTS or
        ~/.ssh/known_hosts, unknown hosts rejected unless PFX+SFTP_INSECURE_ACCEPT_HOST_KEY=1.
        Without paramiko the OpenSSH `sftp` client runs in batch mode (BatchMode=yes,
        StrictHostKeyChecking=yes, or accept-new with the insecure flag) with key/agent auth;
        a password or key passphrase then needs paramiko. Remote directories are created.
webhook http(s)://...  POST application/json {"event": "render.complete", "job", "files": [{"path",
        "name", "bytes", "sha256", "contentType"}], "metadata", "generator": "scenerender"}
        with the http-put headers; PFX+WEBHOOK_SECRET adds
        X-Scenerender-Signature: sha256=<hex HMAC-SHA256 of the body>.
"""
from __future__ import annotations

import base64
import configparser
import email.utils
import hashlib
import hmac
import importlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import IO, Any

__all__ = ["LEVELS", "PublishError", "publish", "sigv4_headers", "sigv4_signature", "canonical_request"]

LEVELS: dict[str, tuple[str, str]] = {
    "s3": ("full", "boto3 when installed, else built-in SigV4 (static keys/profile files; multipart above 5 GiB)"),
    "gcs": ("full", "JSON API upload (resumable above 64 MiB); OAuth token or service-account/authorized-user JSON key"),
    "azure-blob": ("full", "Put Blob / Put Block List; SAS token or Shared Key connection string"),
    "http-put": ("full", "PUT per file with configurable headers"),
    "sftp": ("full", "paramiko, else the OpenSSH sftp client (key/agent auth)"),
    "webhook": ("full", "JSON POST, optional HMAC-SHA256 signature"),
}

MiB = 1 << 20
S3_SINGLE_MAX = 5 * 1024 * MiB
S3_PART_SIZE = 64 * MiB
GCS_RESUMABLE_MIN = 64 * MiB
GCS_CHUNK = 8 * MiB
AZURE_SINGLE_MAX = 256 * MiB
AZURE_BLOCK = 64 * MiB
AZURE_VERSION = "2021-08-06"
ATTEMPTS, BACKOFF = 3, 0.5
UNSIGNED = "UNSIGNED-PAYLOAD"
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
_sleep: Callable[[float], None] = time.sleep
_TOKENS: dict[tuple[str, str], tuple[str, float]] = {}


class PublishError(RuntimeError):
    """A destination could not be published to (message is free of secrets)."""


# ---------------------------------------------------------------------------------- shared helpers

@dataclass
class _Ctx:
    env: Mapping[str, str]
    profile: str | None
    timeout: float
    secrets: set[str] = field(default_factory=set)

    @property
    def pfx(self) -> str:
        return "SCENERENDER_" + (re.sub(r"[^A-Za-z0-9]", "_", self.profile).upper() + "_" if self.profile else "")

    def get(self, name: str, *, std: bool = True, secret: bool = False) -> str | None:
        for key in (self.pfx + name, name) if std else (self.pfx + name,):
            if value := self.env.get(key):
                if secret:
                    self.secret(value)
                return value
        return None

    def secret(self, value: str) -> str:
        if value and len(value) >= 4:
            self.secrets.update({value, urllib.parse.quote(value, safe=""), urllib.parse.unquote(value)})
        return value

    def scrub(self, text: str) -> str:
        for s in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        return text


def _safe(url: str) -> str:
    """The URL without userinfo, query and fragment."""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc.rpartition("@")[2], p.path, "", ""))


def _ctype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _targets(prefix: str, files: list[str], enc: Callable[[str], str] = lambda s: s) -> list[tuple[str, str]]:
    if len(files) == 1 and prefix and not prefix.endswith("/"):
        return [(files[0], prefix)]
    base = prefix if not prefix or prefix.endswith("/") else prefix + "/"
    names = [os.path.basename(f) for f in files]
    if len(set(names)) != len(names):
        raise PublishError("several files share a basename; they would overwrite each other")
    return [(f, base + enc(n)) for f, n in zip(files, names)]


class _Slice:
    """Read-only window [offset, offset+length) of a file, for parts/blocks/chunks."""

    def __init__(self, path: str, offset: int, length: int):
        self._f, self._left = open(path, "rb"), length
        self._f.seek(offset)

    def read(self, n: int = -1) -> bytes:
        n = self._left if n is None or n < 0 else min(n, self._left)
        data = self._f.read(n)
        self._left -= len(data)
        return data

    def close(self) -> None:
        self._f.close()


def _sha256_file(path: str, offset: int = 0, length: int | None = None) -> str:
    h, src = hashlib.sha256(), _Slice(path, offset, os.path.getsize(path) - offset if length is None else length)
    try:
        while chunk := src.read(MiB):
            h.update(chunk)
    finally:
        src.close()
    return h.hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
Body = bytes | Callable[[], IO[bytes]] | None


def _detail(payload: bytes) -> str:
    text = payload[:2000].decode("utf-8", "replace")
    if m := re.search(r"<Code>([^<]{1,80})</Code>", text):
        return f" ({m.group(1)})"
    try:
        return f" ({str(json.loads(text)['error']['message'])[:200]})"
    except Exception:
        return ""


def _request(ctx: _Ctx, method: str, url: str, headers: Mapping[str, str], body: Body = None, *,
             accept: tuple[int, ...] = ()) -> tuple[int, Any, bytes]:
    """One HTTP exchange with retries; returns (status, headers, body) for 2xx or accepted codes."""
    for attempt in range(ATTEMPTS):
        data = body() if callable(body) else body
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": "scenerender-publish", **headers})
        try:
            with _OPENER.open(req, timeout=ctx.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code in accept:
                return e.code, e.headers, payload
            if not (e.code == 429 or e.code >= 500) or attempt == ATTEMPTS - 1:
                raise PublishError(ctx.scrub(f"{method} {_safe(url)}: HTTP {e.code} {e.reason}{_detail(payload)}")) from None
        except (urllib.error.URLError, OSError) as e:
            if attempt == ATTEMPTS - 1:
                reason = getattr(e, "reason", e)
                raise PublishError(ctx.scrub(f"{method} {_safe(url)}: {reason}")) from None
        finally:
            if hasattr(data, "close"):
                data.close()  # type: ignore[union-attr]
        _sleep(BACKOFF * 2 ** attempt)
    raise AssertionError("unreachable")


def _import_optional(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _file_body(path: str, offset: int = 0, length: int | None = None) -> Callable[[], IO[bytes]]:
    if length is None:
        return lambda: open(path, "rb")
    return lambda: _Slice(path, offset, length)  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------- SigV4

def canonical_request(method: str, url: str, headers: Mapping[str, str], payload_hash: str) -> tuple[str, str]:
    """(canonical request, signed header list); every given header is signed. S3 path rules: each
    segment URI-encoded once (unreserved characters kept, no double encoding)."""
    p = urllib.parse.urlsplit(url)
    path = "/".join(urllib.parse.quote(urllib.parse.unquote(s), safe="") for s in (p.path or "/").split("/"))
    query = "&".join(f"{k}={v}" for k, v in sorted(
        (urllib.parse.quote(k, safe=""), urllib.parse.quote(v, safe=""))
        for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    hs = {k.strip().lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed = ";".join(sorted(hs))
    canon = "".join(f"{k}:{hs[k]}\n" for k in sorted(hs))
    return "\n".join([method.upper(), path, query, canon, signed, payload_hash]), signed


def sigv4_signature(method: str, url: str, headers: Mapping[str, str], payload_hash: str, *,
                    secret_key: str, region: str, service: str, amz_date: str) -> str:
    """Hex AWS Signature V4 over exactly the given headers."""
    creq, _ = canonical_request(method, url, headers, payload_hash)
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    key = ("AWS4" + secret_key).encode()
    for part in (amz_date[:8], region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()


def sigv4_headers(method: str, url: str, headers: Mapping[str, str], payload_hash: str, *, access_key: str,
                  secret_key: str, region: str, service: str, amz_date: str,
                  session_token: str | None = None) -> dict[str, str]:
    """`headers` plus host, x-amz-date, x-amz-content-sha256 (service s3), x-amz-security-token and
    Authorization; all returned headers except Authorization are signed."""
    h = dict(headers)
    lower = {k.lower() for k in h}
    if "host" not in lower:
        h["host"] = urllib.parse.urlsplit(url).netloc.rpartition("@")[2]
    if "x-amz-date" not in lower:
        h["x-amz-date"] = amz_date
    if service == "s3" and "x-amz-content-sha256" not in lower:
        h["x-amz-content-sha256"] = payload_hash
    if session_token:
        h["x-amz-security-token"] = session_token
    _, signed = canonical_request(method, url, h, payload_hash)
    sig = sigv4_signature(method, url, h, payload_hash, secret_key=secret_key, region=region,
                          service=service, amz_date=amz_date)
    h["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{amz_date[:8]}/{region}/{service}/aws4_request, "
                          f"SignedHeaders={signed}, Signature={sig}")
    return h


# ------------------------------------------------------------------------------------------- S3

def _aws_ini(ctx: _Ctx, var: str, default: str, section: str) -> dict[str, str]:
    path = os.path.expanduser(ctx.env.get(var) or default)
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path)
    except configparser.Error:
        return {}
    return dict(cp[section]) if cp.has_section(section) else {}


def _aws_settings(ctx: _Ctx) -> dict[str, str | None]:
    name = ctx.profile or ctx.env.get("AWS_PROFILE") or "default"
    cred = _aws_ini(ctx, "AWS_SHARED_CREDENTIALS_FILE", "~/.aws/credentials", name)
    conf = _aws_ini(ctx, "AWS_CONFIG_FILE", "~/.aws/config", name if name == "default" else f"profile {name}")
    keys = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    out: dict[str, str | None] = {k: None for k in keys}
    if ctx.get(keys[0]) is not None:  # prefixed env, then plain env
        for k in keys:
            out[k] = ctx.get(k, secret=k != keys[0])
    else:
        for k in keys:
            v = cred.get(k.lower()) or conf.get(k.lower())
            out[k] = ctx.secret(v) if v and k != keys[0] else v
    out["region"] = (ctx.get("AWS_REGION") or ctx.env.get("AWS_DEFAULT_REGION") or conf.get("region")
                     or "us-east-1")
    out["endpoint"] = ctx.get("AWS_ENDPOINT_URL") or conf.get("endpoint_url")
    return out


def _publish_s3(ctx: _Ctx, uri: str, files: list[str]) -> list[str]:
    p = urllib.parse.urlsplit(uri)
    bucket, prefix = p.netloc, urllib.parse.unquote(p.path.lstrip("/"))
    if not bucket:
        raise PublishError(f"s3 destination {uri!r} has no bucket")
    targets = _targets(prefix, files)
    st = _aws_settings(ctx)
    if (boto3 := _import_optional("boto3")) is not None:
        return _s3_boto3(ctx, boto3, st, bucket, targets)
    if not st["AWS_ACCESS_KEY_ID"] or not st["AWS_SECRET_ACCESS_KEY"]:
        raise PublishError(f"s3: no AWS credentials ({ctx.pfx}AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_* or ~/.aws)")
    region, endpoint = str(st["region"]), st["endpoint"]
    if endpoint:
        base = f"{endpoint.rstrip('/')}/{bucket}/"
    elif "." in bucket:
        base = f"https://s3.{region}.amazonaws.com/{bucket}/"
    else:
        base = f"https://{bucket}.s3.{region}.amazonaws.com/"

    def send(method: str, url: str, body: Body, length: int, phash: str, extra: dict[str, str] | None = None,
             ) -> tuple[int, Any, bytes]:
        hs = sigv4_headers(method, url, extra or {}, phash, access_key=str(st["AWS_ACCESS_KEY_ID"]),
                           secret_key=str(st["AWS_SECRET_ACCESS_KEY"]), region=region, service="s3",
                           amz_date=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                           session_token=st["AWS_SESSION_TOKEN"])
        return _request(ctx, method, url, {**hs, "Content-Length": str(length)}, body)

    out = []
    for path, key in targets:
        url, size, ctype = base + urllib.parse.quote(key, safe="/-_.~"), os.path.getsize(path), _ctype(path)
        secure = url.startswith("https:")
        if size <= S3_SINGLE_MAX:
            send("PUT", url, _file_body(path), size, UNSIGNED if secure else _sha256_file(path), {"content-type": ctype})
        else:
            _s3_multipart(send, url, path, size, ctype, secure)
        out.append(f"s3://{bucket}/{key}")
    return out


def _s3_multipart(send: Callable[..., tuple[int, Any, bytes]], url: str, path: str, size: int, ctype: str,
                  secure: bool) -> None:
    empty = hashlib.sha256(b"").hexdigest()
    _, _, body = send("POST", url + "?uploads", b"", 0, empty, {"content-type": ctype})
    m = re.search(rb"<UploadId>([^<]+)</UploadId>", body)
    if not m:
        raise PublishError(f"s3: CreateMultipartUpload for {_safe(url)} returned no UploadId")
    upload = urllib.parse.quote(m.group(1).decode(), safe="")
    part_size = max(S3_PART_SIZE, -(-size // 10000))
    try:
        parts = []
        for n, off in enumerate(range(0, size, part_size), 1):
            length = min(part_size, size - off)
            phash = UNSIGNED if secure else _sha256_file(path, off, length)
            _, hs, _ = send("PUT", f"{url}?partNumber={n}&uploadId={upload}", _file_body(path, off, length), length, phash)
            parts.append(f"<Part><PartNumber>{n}</PartNumber><ETag>{hs.get('ETag', '')}</ETag></Part>")
        xml = f"<CompleteMultipartUpload>{''.join(parts)}</CompleteMultipartUpload>".encode()
        _, _, body = send("POST", f"{url}?uploadId={upload}", xml, len(xml), hashlib.sha256(xml).hexdigest(),
                          {"content-type": "application/xml"})
        if b"<Error>" in body:
            raise PublishError(f"s3: CompleteMultipartUpload for {_safe(url)} failed{_detail(body)}")
    except BaseException:
        try:
            send("DELETE", f"{url}?uploadId={upload}", None, 0, empty)
        except PublishError:
            pass
        raise


def _s3_boto3(ctx: _Ctx, boto3: Any, st: dict[str, str | None], bucket: str,
              targets: list[tuple[str, str]]) -> list[str]:
    try:
        if ctx.profile and ctx.profile in boto3.Session().available_profiles and ctx.get("AWS_ACCESS_KEY_ID", std=False) is None:
            session = boto3.Session(profile_name=ctx.profile)
        else:
            session = boto3.Session(aws_access_key_id=st["AWS_ACCESS_KEY_ID"], aws_secret_access_key=st["AWS_SECRET_ACCESS_KEY"],
                                    aws_session_token=st["AWS_SESSION_TOKEN"], region_name=st["region"])
        client = session.client("s3", endpoint_url=st["endpoint"] or None)
        for path, key in targets:
            client.upload_file(path, bucket, key, ExtraArgs={"ContentType": _ctype(path)})
    except Exception as e:
        raise PublishError(ctx.scrub(f"s3 (boto3): {type(e).__name__}: {e}")) from None
    return [f"s3://{bucket}/{key}" for _, key in targets]


# ------------------------------------------------------------------------------------------ GCS

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _gcs_token(ctx: _Ctx) -> str | None:
    if tok := ctx.get("GOOGLE_OAUTH_ACCESS_TOKEN", secret=True):
        return tok
    if not (keyfile := ctx.get("GOOGLE_APPLICATION_CREDENTIALS")):
        return None
    try:
        with open(os.path.expanduser(keyfile), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError) as e:
        raise PublishError(f"gcs: cannot read the credentials file: {type(e).__name__}") from None
    for k in ("private_key", "client_secret", "refresh_token"):
        if info.get(k):
            ctx.secret(str(info[k]))
    token_uri = info.get("token_uri") or "https://oauth2.googleapis.com/token"
    cache_key = (str(info.get("client_email") or info.get("client_id")), token_uri)
    if (hit := _TOKENS.get(cache_key)) and hit[1] > time.time() + 60:
        return ctx.secret(hit[0])
    if info.get("type") == "authorized_user":
        form = {"grant_type": "refresh_token", "client_id": info["client_id"],
                "client_secret": info["client_secret"], "refresh_token": info["refresh_token"]}
    elif info.get("type") == "service_account":
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(time.time())
        head = {"alg": "RS256", "typ": "JWT", **({"kid": info["private_key_id"]} if info.get("private_key_id") else {})}
        claims = {"iss": info["client_email"], "scope": GCS_SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600}
        signing = f"{_b64url(json.dumps(head).encode())}.{_b64url(json.dumps(claims).encode())}"
        key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr,call-arg]
        form = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{signing}.{_b64url(sig)}"}
    else:
        raise PublishError(f"gcs: unsupported credentials file type {info.get('type')!r}")
    data = urllib.parse.urlencode(form).encode()
    _, _, body = _request(ctx, "POST", token_uri, {"Content-Type": "application/x-www-form-urlencoded"}, data)
    try:
        reply = json.loads(body)
        token = ctx.secret(str(reply["access_token"]))
    except (ValueError, KeyError):
        raise PublishError(f"gcs: token endpoint {_safe(token_uri)} returned no access_token") from None
    _TOKENS[cache_key] = (token, time.time() + float(reply.get("expires_in", 3600)))
    return token


def _publish_gcs(ctx: _Ctx, uri: str, files: list[str]) -> list[str]:
    p = urllib.parse.urlsplit(uri)
    bucket, prefix = p.netloc, urllib.parse.unquote(p.path.lstrip("/"))
    if not bucket:
        raise PublishError(f"gcs destination {uri!r} has no bucket")
    endpoint = ctx.get("GCS_ENDPOINT", std=False) or ctx.env.get("STORAGE_EMULATOR_HOST")
    if endpoint and "://" not in endpoint:
        endpoint = "http://" + endpoint
    token = _gcs_token(ctx)
    if token is None and not endpoint:
        raise PublishError(f"gcs: no credentials ({ctx.pfx}GOOGLE_OAUTH_ACCESS_TOKEN or GOOGLE_APPLICATION_CREDENTIALS)")
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    base = f"{(endpoint or 'https://storage.googleapis.com').rstrip('/')}/upload/storage/v1/b/{urllib.parse.quote(bucket, safe='')}/o"
    out = []
    for path, name in _targets(prefix, files):
        size, ctype, qname = os.path.getsize(path), _ctype(path), urllib.parse.quote(name, safe="")
        if size <= GCS_RESUMABLE_MIN:
            _request(ctx, "POST", f"{base}?uploadType=media&name={qname}",
                     {**auth, "Content-Type": ctype, "Content-Length": str(size)}, _file_body(path))
        else:
            _, hs, _ = _request(ctx, "POST", f"{base}?uploadType=resumable&name={qname}",
                                {**auth, "X-Upload-Content-Type": ctype, "X-Upload-Content-Length": str(size),
                                 "Content-Type": "application/json; charset=UTF-8"}, b"{}")
            session = hs.get("Location")
            if not session:
                raise PublishError(f"gcs: no resumable session for gs://{bucket}/{name}")
            off = 0
            while off < size:
                length = min(GCS_CHUNK, size - off)
                status, hs, _ = _request(ctx, "PUT", session, {**auth, "Content-Length": str(length), "Content-Type": ctype,
                                         "Content-Range": f"bytes {off}-{off + length - 1}/{size}"},
                                         _file_body(path, off, length), accept=(308,))
                if status != 308:
                    break
                rng = hs.get("Range")
                off = int(rng.rpartition("-")[2]) + 1 if rng else 0
        out.append(f"gs://{bucket}/{name}")
    return out


# ---------------------------------------------------------------------------------------- Azure

def _azure_sign(method: str, url: str, headers: Mapping[str, str], account: str, key: str) -> str:
    h = {k.lower(): v for k, v in headers.items()}
    p = urllib.parse.urlsplit(url)
    length = h.get("content-length", "")
    fields = [method, h.get("content-encoding", ""), h.get("content-language", ""), "" if length == "0" else length,
              h.get("content-md5", ""), h.get("content-type", ""), "", h.get("if-modified-since", ""),
              h.get("if-match", ""), h.get("if-none-match", ""), h.get("if-unmodified-since", ""), h.get("range", "")]
    canon_h = "".join(f"{k}:{' '.join(v.split())}\n" for k, v in sorted(h.items()) if k.startswith("x-ms-"))
    params: dict[str, list[str]] = {}
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        params.setdefault(k.lower(), []).append(v)
    resource = f"/{account}{p.path or '/'}" + "".join(f"\n{k}:{','.join(sorted(v))}" for k, v in sorted(params.items()))
    sts = "\n".join(fields) + "\n" + canon_h + resource
    sig = base64.b64encode(hmac.new(base64.b64decode(key), sts.encode("utf-8"), hashlib.sha256).digest()).decode()
    return f"SharedKey {account}:{sig}"


def _publish_azure(ctx: _Ctx, uri: str, files: list[str]) -> list[str]:
    sas = conn = None
    for pfx in (ctx.pfx, ""):
        if sas := ctx.env.get(pfx + "AZURE_STORAGE_SAS_TOKEN"):
            break
        if conn := ctx.env.get(pfx + "AZURE_STORAGE_CONNECTION_STRING"):
            break
    cs = dict(kv.split("=", 1) for kv in (conn or "").split(";") if "=" in kv)
    for k in ("AccountKey", "SharedAccessSignature"):
        if cs.get(k):
            ctx.secret(cs[k])
    sas = (sas or cs.get("SharedAccessSignature") or "").lstrip("?") or None
    if sas:
        ctx.secret(sas)
        for _, v in urllib.parse.parse_qsl(sas):
            ctx.secret(v)
    p = urllib.parse.urlsplit(uri)
    if p.scheme == "azure":
        account = p.netloc
        base = (cs.get("BlobEndpoint") or f"https://{account}.blob.core.windows.net").rstrip("/")
        rest = p.path
    else:
        endpoint = cs.get("BlobEndpoint", "").rstrip("/")
        base = endpoint if endpoint and uri.startswith(endpoint + "/") else f"{p.scheme}://{p.netloc}"
        rest = _safe(uri)[len(base):]
        host = p.hostname or ""
        account = host.split(".")[0] if host.endswith(".blob.core.windows.net") else cs.get("AccountName", "")
    account = cs.get("AccountName") or account
    container, _, prefix = urllib.parse.unquote(rest).lstrip("/").partition("/")
    if not container:
        raise PublishError(f"azure-blob destination {_safe(uri)!r} has no container")
    key = None if sas else cs.get("AccountKey")
    if not sas and not key:
        raise PublishError(f"azure-blob: no credentials ({ctx.pfx}AZURE_STORAGE_SAS_TOKEN or AZURE_STORAGE_CONNECTION_STRING)")

    def send(method: str, url: str, headers: dict[str, str], body: Body) -> None:
        url = f"{url}{'&' if '?' in url else '?'}{sas}" if sas else url
        hs = {"x-ms-version": AZURE_VERSION, "x-ms-date": email.utils.formatdate(usegmt=True), **headers}
        if key:
            hs["Authorization"] = _azure_sign(method, url, hs, account, key)
        _request(ctx, method, url, hs, body)

    out = []
    for path, name in _targets(prefix, files):
        url = f"{base}/{urllib.parse.quote(container)}/{urllib.parse.quote(name, safe='/-_.~')}"
        size, ctype = os.path.getsize(path), _ctype(path)
        if size <= AZURE_SINGLE_MAX:
            send("PUT", url, {"x-ms-blob-type": "BlockBlob", "Content-Type": ctype, "Content-Length": str(size)},
                 _file_body(path))
        else:
            ids = []
            for n, off in enumerate(range(0, size, AZURE_BLOCK)):
                bid = base64.b64encode(f"{n:08d}".encode()).decode()
                length = min(AZURE_BLOCK, size - off)
                send("PUT", f"{url}?comp=block&blockid={urllib.parse.quote(bid, safe='')}",
                     {"Content-Type": "application/octet-stream", "Content-Length": str(length)},
                     _file_body(path, off, length))
                ids.append(bid)
            xml = ('<?xml version="1.0" encoding="utf-8"?><BlockList>'
                   + "".join(f"<Latest>{b}</Latest>" for b in ids) + "</BlockList>").encode()
            send("PUT", f"{url}?comp=blocklist", {"x-ms-blob-content-type": ctype, "Content-Type": "application/xml",
                                                  "Content-Length": str(len(xml))}, xml)
        out.append(_safe(url))
    return out


# ------------------------------------------------------------------------------ http-put, webhook

def _http_headers(ctx: _Ctx) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in re.split(r"[\n;]", ctx.get("HTTP_HEADERS", std=False, secret=True) or ""):
        name, sep, value = line.partition(":")
        if sep and name.strip():
            out[name.strip()] = ctx.secret(value.strip())
    if tok := ctx.get("HTTP_BEARER_TOKEN", std=False, secret=True):
        out["Authorization"] = f"Bearer {tok}"
    if auth := ctx.get("HTTP_AUTHORIZATION", std=False, secret=True):
        out["Authorization"] = auth
    return out


def _publish_http_put(ctx: _Ctx, uri: str, files: list[str]) -> list[str]:
    p = urllib.parse.urlsplit(uri)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise PublishError(f"http-put destination {_safe(uri)!r} is not an http(s) URL")
    headers, out = _http_headers(ctx), []
    for path, target in _targets(p.path or "/", files, lambda n: urllib.parse.quote(n)):
        url = urllib.parse.urlunsplit((p.scheme, p.netloc, target, p.query, ""))
        size = os.path.getsize(path)
        _request(ctx, "PUT", url, {**headers, "Content-Type": _ctype(path), "Content-Length": str(size)}, _file_body(path))
        out.append(_safe(url))
    return out


def _publish_webhook(ctx: _Ctx, uri: str, files: list[str], job: str, metadata: dict | None) -> list[str]:
    if urllib.parse.urlsplit(uri).scheme not in ("http", "https"):
        raise PublishError(f"webhook destination {_safe(uri)!r} is not an http(s) URL")
    entries = [{"path": os.path.abspath(f), "name": os.path.basename(f), "bytes": os.path.getsize(f),
                "sha256": _sha256_file(f), "contentType": _ctype(f)} for f in files]
    body = json.dumps({"event": "render.complete", "job": job, "files": entries, "metadata": metadata or {},
                       "generator": "scenerender"}, separators=(",", ":")).encode()
    headers = {**_http_headers(ctx), "Content-Type": "application/json"}
    if secret := ctx.get("WEBHOOK_SECRET", std=False, secret=True):
        headers["X-Scenerender-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    _request(ctx, "POST", uri, headers, body)
    return [_safe(uri)]


# ----------------------------------------------------------------------------------------- SFTP

def _sftp_quote(s: str) -> str:
    if "\n" in s or "\r" in s:
        raise PublishError("sftp: path contains a newline")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _publish_sftp(ctx: _Ctx, uri: str, files: list[str]) -> list[str]:
    p = urllib.parse.urlsplit(uri)
    if not p.hostname:
        raise PublishError(f"sftp destination {_safe(uri)!r} has no host")
    host, port, user = p.hostname, p.port or 22, urllib.parse.unquote(p.username or "") or None
    path = urllib.parse.unquote(p.path)
    path = path[3:] if path.startswith("/~/") else "" if path in ("/~", "") else path
    targets = _targets(path, files)
    key = ctx.get("SFTP_KEY_FILE", std=False)
    key = os.path.expanduser(key) if key else None
    passphrase = ctx.get("SFTP_KEY_PASSPHRASE", std=False, secret=True)
    password = ctx.get("SFTP_PASSWORD", std=False, secret=True)
    known = ctx.get("SFTP_KNOWN_HOSTS", std=False)
    insecure = ctx.get("SFTP_INSECURE_ACCEPT_HOST_KEY", std=False) == "1"
    shown = f"sftp://{user + '@' if user else ''}{host}{f':{port}' if port != 22 else ''}"
    dirs: list[str] = []
    for _, remote in targets:
        parts = os.path.dirname(remote).split("/")
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d and d not in dirs:
                dirs.append(d)
    paramiko = _import_optional("paramiko")
    if paramiko is not None:
        try:
            client = paramiko.SSHClient()
            client.load_host_keys(os.path.expanduser(known)) if known else client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy() if insecure else paramiko.RejectPolicy())
            client.connect(host, port=port, username=user, key_filename=key, passphrase=passphrase, password=password,
                           timeout=ctx.timeout, allow_agent=True, look_for_keys=not (key or password))
            try:
                sftp = client.open_sftp()
                for d in dirs:
                    try:
                        sftp.stat(d)
                    except OSError:
                        sftp.mkdir(d)
                for local, remote in targets:
                    sftp.put(local, remote)
            finally:
                client.close()
        except Exception as e:
            raise PublishError(ctx.scrub(f"{shown}: {type(e).__name__}: {e}")) from None
    else:
        if password or passphrase:
            raise PublishError("sftp: password or key-passphrase authentication needs paramiko (pip install paramiko); "
                               "the OpenSSH fallback supports key files and ssh-agent only")
        exe = shutil.which("sftp")
        if not exe:
            raise PublishError("sftp: neither paramiko nor the OpenSSH sftp client is available")
        cmd = [exe, "-b", "-", "-P", str(port), "-o", "BatchMode=yes",
               "-o", f"StrictHostKeyChecking={'accept-new' if insecure else 'yes'}",
               "-o", f"ConnectTimeout={max(1, int(ctx.timeout))}"]
        if known:
            cmd += ["-o", f"UserKnownHostsFile={os.path.expanduser(known)}"]
        if key:
            cmd += ["-i", key, "-o", "IdentitiesOnly=yes"]
        cmd.append(f"{user}@{host}" if user else host)
        script = "".join(f"-mkdir {_sftp_quote(d)}\n" for d in dirs)
        script += "".join(f"put {_sftp_quote(local)} {_sftp_quote(remote)}\n" for local, remote in targets)
        res = subprocess.run(cmd, input=script, text=True, capture_output=True, check=False)
        if res.returncode != 0:
            tail = " ".join((res.stderr or "").strip().splitlines()[-3:])
            raise PublishError(ctx.scrub(f"{shown}: sftp exited with {res.returncode}: {tail}"))
    return [f"{shown}{r if r.startswith('/') else '/~/' + r}" for _, r in targets]


# ------------------------------------------------------------------------------------------- API

def publish(kind: str, uri: str, files: list[str], *, credentials: str | None = None, job: str = "",
            metadata: dict | None = None, env: Mapping[str, str] | None = None, timeout: float = 60.0) -> list[str]:
    """Upload/notify; returns one remote location string per file (webhook: the webhook URL once).
    Raises PublishError with a message that never contains a secret."""
    ctx = _Ctx(os.environ if env is None else env, credentials or None, timeout)
    files = [os.fspath(f) for f in files]
    for f in files:
        if not os.path.isfile(f):
            raise PublishError(f"{kind}: file to publish not found: {f}")
    handlers: dict[str, Callable[[_Ctx, str, list[str]], list[str]]] = {
        "s3": _publish_s3, "gcs": _publish_gcs, "azure-blob": _publish_azure, "http-put": _publish_http_put,
        "sftp": _publish_sftp, "webhook": lambda c, u, f: _publish_webhook(c, u, f, job, metadata)}
    if kind not in handlers:
        raise PublishError(f"unsupported destination kind {kind!r}" + (" (handled by output.py)" if kind == "file" else ""))
    if not files and kind != "webhook":
        return []
    try:
        return handlers[kind](ctx, uri, files)
    except PublishError as e:
        raise PublishError(ctx.scrub(str(e))) from None
    except (OSError, ValueError, KeyError) as e:
        raise PublishError(ctx.scrub(f"{kind} {_safe(uri)}: {type(e).__name__}: {e}")) from None
