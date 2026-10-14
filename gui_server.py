#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
git-sync GUI 后端服务 —— 只为本地浏览器界面提供 API，不对外网开放。

启动后会自动打开浏览器界面 http://127.0.0.1:<port>/
只监听 127.0.0.1，仅本机可访问。

由 git-sync-gui.bat 双击启动，一般无需手动运行。
"""

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler, Request

import git_sync as gs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_PATH = os.path.join(BASE_DIR, "ui.html")
GUI_LOG = os.path.join(BASE_DIR, "gui_server.log")

DEFAULT_PORT = 8765
PORT_SCAN = 20          # 从默认端口往后找的可用端口数
IDLE_TIMEOUT_SEC = 1800  # 空闲多久自动退出（秒），0 表示不自动退出

_server = None
_last_activity = time.time()

# 绕过系统代理访问本机端口（本机代理会拦截 127.0.0.1）
_opener = build_opener(ProxyHandler({}))


def log(msg):
    try:
        with open(GUI_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# pythonw 启动时没有 stdout，重定向到文件，避免 print 抛错
if sys.stdout is None or sys.stderr is None:
    try:
        _f = open(GUI_LOG, "a", encoding="utf-8")
        sys.stdout = sys.stderr = _f
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "git-sync-gui"
    protocol_version = "HTTP/1.0"   # 流式响应靠关闭连接结束

    # ---- 工具方法 ----
    def log_message(self, fmt, *args):
        global _last_activity
        _last_activity = time.time()
        # 默认日志过于啰嗦，仅写调试文件
        log("HTTP %s" % (fmt % args))

    def _touch(self):
        global _last_activity
        _last_activity = time.time()

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _ndjson_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _ndjson_writer(self):
        def w(obj):
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
        return w

    def _serve_ui(self):
        try:
            with open(UI_PATH, "rb") as f:
                data = f.read()
        except Exception as e:
            self._send_json({"error": "找不到界面文件 ui.html：%s" % e}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- GET ----
    def do_GET(self):
        self._touch()
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            return self._serve_ui()
        if p == "/api/state":
            cfg = gs.load_config()
            return self._send_json({
                "repos": cfg.get("repos", []),
                "baseDir": BASE_DIR,
                "configPath": gs.CONFIG_PATH,
                "version": "2.0",
                "git": {
                    "available": gs.git_available(),
                    "exe": gs.GIT_EXE,
                    "version": gs.git_version(),
                },
            })
        if p == "/api/ping":
            return self._send_json({"pong": True})
        return self._send_json({"error": "未知路径"}, 404)

    # ---- POST ----
    def do_POST(self):
        self._touch()
        p = urlparse(self.path).path
        body = self._read_json()

        if p == "/api/save":
            repos = body.get("repos")
            if not isinstance(repos, list):
                return self._send_json({"error": "repos 必须是数组"}, 400)
            cfg = gs.load_config()
            cfg["repos"] = repos
            try:
                gs.save_config(cfg)
            except Exception as e:
                return self._send_json({"error": "保存失败：%s" % e}, 500)
            return self._send_json({"ok": True, "count": len(repos)})

        if p == "/api/validate":
            return self._send_json(self._validate(body))

        if p == "/api/remote-status":
            # 需要访问远端，较慢，由前端异步调用
            try:
                return self._send_json(gs.remote_status(body))
            except Exception as e:
                return self._send_json({"ok": False, "error": "查询失败：%s" % e})

        if p == "/api/open":
            path = (body.get("path") or "").strip()
            if path and os.path.isdir(path):
                try:
                    os.startfile(path)  # noqa: 仅 Windows，打开资源管理器
                    return self._send_json({"ok": True})
                except Exception as e:
                    return self._send_json({"error": str(e)}, 500)
            return self._send_json({"error": "目录不存在"}, 400)

        if p == "/api/push":
            return self._do_push(body)

        if p == "/api/pull":
            return self._do_pull(body)

        if p == "/api/shutdown":
            self._send_json({"ok": True})
            log("收到关闭请求，正在退出")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        return self._send_json({"error": "未知路径"}, 404)

    # ---- 业务 ----
    def _validate(self, body):
        """检查单个仓库配置是否可用。"""
        path = (body.get("path") or "").strip()
        res = {"exists": False, "isGit": False, "branch": None, "currentBranch": None,
               "dirty": False, "message": ""}

        # 先确认 git 命令本身能用（否则后面全是误报）
        if not gs.git_available():
            res["message"] = "找不到可用的 git 命令（尝试路径：%s）" % gs.GIT_EXE
            res["gitMissing"] = True
            return res

        if not path:
            res["message"] = "路径为空"
            return res
        if not os.path.isdir(path):
            res["message"] = "目录不存在"
            return res
        res["exists"] = True
        if not gs.is_git_repo(path):
            res["message"] = "不是 git 仓库（缺 .git）"
            return res
        res["isGit"] = True
        res["currentBranch"] = gs.current_branch(path)
        local, _, _ = gs.resolve_branches(body)
        res["branch"] = local
        rc, out = gs.run_git(["rev-parse", "--verify", local], path, gs.git_env())
        if rc != 0:
            if rc == 127 or "找不到 git" in out or "not found" in out.lower():
                res["message"] = "git 命令执行失败"
                res["gitMissing"] = True
            else:
                res["message"] = "本地无分支 %s" % local
        else:
            res["dirty"] = gs.has_uncommitted(path)
            res["message"] = "当前分支 %s" % (res["currentBranch"] or "?")
            if res["dirty"]:
                res["message"] += " · 有未提交改动"
        return res

    def _do_push(self, body):
        idxs = body.get("indexes") or []
        targets = body.get("targets") or list(gs.VALID_TARGETS)
        check_only = bool(body.get("checkOnly"))

        cfg = gs.load_config()
        repos = cfg.get("repos", [])
        picked = []
        for i in idxs:
            try:
                i = int(i)
            except Exception:
                continue
            if 0 <= i < len(repos):
                picked.append(repos[i])

        self._ndjson_start()
        write = self._ndjson_writer()

        if not picked:
            write({"level": "err", "text": "✖ 没有选中任何仓库"})
            write({"done": True, "ok": False, "results": []})
            return

        targets = tuple(t for t in targets if t in gs.VALID_TARGETS) or gs.VALID_TARGETS
        write({"level": "info", "text": "开始%s：%d 个仓库 → %s"
               % ("检查" if check_only else "推送", len(picked),
                  "、".join(gs.TARGET_LABEL[t] for t in targets))})
        write({"level": "info", "text": ""})

        def emit(level, text):
            write({"level": level, "text": text})

        t0 = time.time()
        try:
            all_ok, results = gs.sync_many(picked, targets,
                                           check_only=check_only, on_log=emit)
        except Exception as e:
            write({"level": "err", "text": "✖ 内部错误：%s" % e})
            all_ok, results = False, []

        dt = time.time() - t0
        write({"level": "info", "text": ""})
        write({"level": "ok" if all_ok else "err",
               "text": ("全部成功 ✔" if all_ok else "存在失败项 ✖") + "　耗时 %.1fs" % dt})
        write({"done": True, "ok": all_ok, "results": results})


    def _do_pull(self, body):
        idxs = body.get("indexes") or []
        source = body.get("source") or "github"
        force = bool(body.get("force"))

        cfg = gs.load_config()
        repos = cfg.get("repos", [])
        picked = []
        for i in idxs:
            try:
                i = int(i)
            except Exception:
                continue
            if 0 <= i < len(repos):
                picked.append(repos[i])

        self._ndjson_start()
        write = self._ndjson_writer()

        if not picked:
            write({"level": "err", "text": "✖ 没有选中仓库"})
            write({"done": True, "ok": False, "results": []})
            return

        label = gs.TARGET_LABEL.get(source, source)
        write({"level": "info", "text": "开始拉取：%s → 本地%s"
               % (label, "（强制覆盖本地）" if force else "")})
        write({"level": "info", "text": ""})

        def emit(level, text):
            write({"level": level, "text": text})

        t0 = time.time()
        all_ok = True
        results = []
        try:
            for k, r in enumerate(picked):
                if k > 0:
                    emit("info", "")
                ok, info = gs.pull_repo(r, source, force, emit)
                results.append(info)
                all_ok = all_ok and ok
        except Exception as e:
            emit("err", "✖ 内部错误：%s" % e)
            all_ok = False

        dt = time.time() - t0
        emit("info", "")
        emit("ok" if all_ok else "err",
             ("全部成功 ✔" if all_ok else "存在失败项 ✖") + "　耗时 %.1fs" % dt)
        write({"done": True, "ok": all_ok, "results": results})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------- 启动逻辑

def is_gui_running(port):
    """检测该端口是否已被本工具占用。"""
    try:
        with _opener.open("http://127.0.0.1:%d/api/state" % port, timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8"))
            return isinstance(data, dict) and "repos" in data
    except Exception:
        return False


def find_free_port(start, count):
    for p in range(start, start + count):
        s = socket.socket()
        try:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
        except Exception:
            return p
        finally:
            s.close()
    return None


def idle_watchdog(server):
    """空闲超时后自动退出。"""
    if IDLE_TIMEOUT_SEC <= 0:
        return
    while True:
        time.sleep(30)
        if time.time() - _last_activity > IDLE_TIMEOUT_SEC:
            log("空闲超过 %d 秒，自动退出" % IDLE_TIMEOUT_SEC)
            try:
                server.shutdown()
            except Exception:
                pass
            return


def main():
    global _server, DEFAULT_PORT, IDLE_TIMEOUT_SEC

    no_browser = "--no-browser" in sys.argv
    if "--keep-alive" in sys.argv:
        IDLE_TIMEOUT_SEC = 0          # 被 Dev Server Launcher 管理时不自动退出
    if "--port" in sys.argv:
        try:
            DEFAULT_PORT = int(sys.argv[sys.argv.index("--port") + 1])
        except Exception:
            pass

    # 1. 已在运行？直接开浏览器
    if is_gui_running(DEFAULT_PORT):
        log("检测到服务已在 %d 运行，直接打开界面" % DEFAULT_PORT)
        if not no_browser:
            webbrowser.open("http://127.0.0.1:%d/" % DEFAULT_PORT)
        return

    # 2. 找可用端口
    port = find_free_port(DEFAULT_PORT, PORT_SCAN)
    if port is None:
        log("找不到可用端口（%d-%d）" % (DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN))
        return

    # 3. 起服务
    _server = Server(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    log("服务已启动：%s" % url)

    threading.Thread(target=idle_watchdog, args=(_server,), daemon=True).start()
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _server.server_close()
        except Exception:
            pass
        log("服务已停止")


if __name__ == "__main__":
    main()
