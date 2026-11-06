"""云端 WSGI 入口：访问密码保护 + 转发到本机内部的原计算服务。

Gunicorn 用 cloud_wsgi:create_application() 加载。原有 Windows 启动入口不变。
"""
import base64
import binascii
import hmac
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY = 8_000_000


class CloudGateway:
    def __init__(self, password, username="avopt", backend_factory=None):
        if not isinstance(password,str) or len(password) < 12:
            raise ValueError("请在云平台设置至少 12 位的 AVOPT_ACCESS_PASSWORD，密码不要写入源码。")
        self._password = password
        self._username = username
        if backend_factory is None:
            from AVOpt.webapp import create_server
            backend_factory = create_server
        self._server = backend_factory(0)
        self._origin = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever,daemon=True)
        self._thread.start()
        # 内部请求只到本机，不经过环境中可能设置的外部代理。
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _authorized(self, header):
        try:
            method, payload = header.split(" ",1)
            if method.lower() != "basic":
                return False
            username,password = base64.b64decode(payload,validate=True).decode("utf-8").split(":",1)
            return hmac.compare_digest(username.encode(),self._username.encode()) and hmac.compare_digest(password.encode(),self._password.encode())
        except (ValueError,UnicodeError,binascii.Error):
            return False

    @staticmethod
    def _json(start_response,code,message,extra=()):
        body = json.dumps({"ok":False,"error":message},ensure_ascii=False).encode("utf-8")
        start_response(code,[("Content-Type","application/json; charset=utf-8"),("Content-Length",str(len(body))),("Cache-Control","no-store"),*extra])
        return [body]

    def __call__(self,environ,start_response):
        path = environ.get("PATH_INFO","/")
        method = environ.get("REQUEST_METHOD","GET").upper()
        health = path == "/api/health" and method in ("GET","HEAD")
        if not health and not self._authorized(environ.get("HTTP_AUTHORIZATION","")):
            return self._json(start_response,"401 Unauthorized","请输入访问用户名和密码",[("WWW-Authenticate",'Basic realm="AVOpt", charset="UTF-8"')])
        if method not in ("GET","HEAD","POST"):
            return self._json(start_response,"405 Method Not Allowed","不支持该请求方法")
        origin = environ.get("HTTP_ORIGIN")
        if method == "POST" and origin and urllib.parse.urlsplit(origin).netloc.lower() != environ.get("HTTP_HOST","").lower():
            return self._json(start_response,"403 Forbidden","请从程序本身的网页提交计算或导入请求")
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (ValueError,TypeError):
            return self._json(start_response,"400 Bad Request","无效请求长度")
        if not 0 <= length <= MAX_BODY:
            return self._json(start_response,"413 Content Too Large","请求超过 8 MB")
        data = environ["wsgi.input"].read(length) if method == "POST" else None
        url = self._origin + urllib.parse.quote(path,safe="/")
        if environ.get("QUERY_STRING"):
            url += "?" + environ["QUERY_STRING"]
        headers = {"Content-Type":environ.get("CONTENT_TYPE","application/json")}
        req = urllib.request.Request(url,data=data,headers=headers,method="GET" if method == "HEAD" else method)
        try:
            try:
                result = self._opener.open(req,timeout=180)
            except urllib.error.HTTPError as exc:
                result = exc
            with result:
                body = result.read()
                status = f"{result.code} {result.reason}"
                content_type = result.headers.get("Content-Type","application/json; charset=utf-8")
            start_response(status,[("Content-Type",content_type),("Content-Length",str(len(body))),("Cache-Control","no-store"),("X-Content-Type-Options","nosniff")])
            return [] if method == "HEAD" else [body]
        except (urllib.error.URLError,TimeoutError,OSError):
            return self._json(start_response,"502 Bad Gateway","计算服务暂时不可用，请稍后重试")


def create_application():
    return CloudGateway(os.environ.get("AVOPT_ACCESS_PASSWORD",""),os.environ.get("AVOPT_ACCESS_USER","avopt"))
