#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
git-sync —— 一键把本地代码同时推送到 GitHub 和 本地 GitLab，并保持两个远端分支一致。

用法（一般由 一键上传.bat 双击调用，也可命令行直接用）：

    python git_sync.py            # 交互式：列出已登记仓库，选编号后推送
    python git_sync.py 1          # 直接推送第 1 个仓库（用默认分支）
    python git_sync.py 1 dev      # 推送第 1 个仓库的 dev 分支
    python git_sync.py --list     # 只列出已登记仓库
    python git_sync.py --check    # 不推送，只检查双远端一致性
    python git_sync.py --add      # 交互式登记一个新仓库

配置文件 repos.json 与本脚本同目录。
"""

import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# ---------------------------------------------------------------- 基础设置

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "repos.json")
LOG_PATH = os.path.join(BASE_DIR, "git_sync.log")

GITHUB_REMOTE = "github"
GITLAB_REMOTE = "gitlab"

# 内网地址不走代理（否则会被本机代理拦截而连不上）。
# 默认值只覆盖私有网段；你自己的内网域名/IP 用下面两种方式追加：
#   1) 环境变量  GIT_SYNC_NO_PROXY="git.mycorp.com,10.1.2.3"
#   2) repos.json 顶层加  "no_proxy": ["git.mycorp.com", "10.1.2.3"]
DEFAULT_NO_PROXY = [
    "127.0.0.1", "localhost",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
]

# 由 load_config() 从 repos.json 读取后填充
_EXTRA_NO_PROXY = []

# Windows 控制台颜色（失败时自动降级为无色）
class C:
    R = "\033[0m"
    B = "\033[1m"
    G = "\033[92m"   # 绿
    Y = "\033[93m"   # 黄
    RD = "\033[91m"  # 红
    C = "\033[96m"   # 青
    GR = "\033[90m"  # 灰


def _enable_ansi():
    """Windows 10+ 开启 ANSI 颜色支持。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        # 不支持颜色就去掉色码
        for a in dir(C):
            if not a.startswith("_"):
                setattr(C, a, "")


_enable_ansi()


# ---------------------------------------------------------------- 输出与日志

def log_line(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def say(msg=""):
    print(msg)


def ok(msg):
    say("%s%s%s" % (C.G, msg, C.R))


def warn(msg):
    say("%s%s%s" % (C.Y, msg, C.R))


def err(msg):
    say("%s%s%s" % (C.RD, msg, C.R))


def head(msg):
    say()
    say("%s%s%s" % (C.B + C.C, msg, C.R))


def rule():
    say("%s%s%s" % (C.GR, "-" * 66, C.R))


# ---------------------------------------------------------------- 子进程

def _find_git():
    """定位 git 可执行文件。

    有些环境里 git 没进 PATH（例如只有便携版，或程序是被双击启动的），
    所以不能假设 `git` 一定可用，得自己找一遍。

    优先级：
      1) PATH 里的 git
      2) 环境变量 GIT_SYNC_GIT 指定的完整路径
      3) 常见安装位置
      4) 便携版 git（版本目录不固定，用通配匹配）
    """
    exe = shutil.which("git")
    if exe:
        return exe

    env_exe = os.environ.get("GIT_SYNC_GIT", "").strip()
    if env_exe and os.path.exists(env_exe):
        return env_exe

    cands = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
        os.path.expandvars(r"%USERPROFILE%\scoop\shims\git.exe"),
        "/usr/bin/git",
        "/usr/local/bin/git",
        "/opt/homebrew/bin/git",
    ]
    # 便携版 / 随编辑器分发的 git
    for pat in (
        os.path.expandvars(r"%USERPROFILE%\.workbuddy\binaries\PortableGit\versions\*\cmd\git.exe"),
        os.path.expandvars(r"%USERPROFILE%\.workbuddy\binaries\PortableGit\versions\*\bin\git.exe"),
        os.path.expandvars(r"%USERPROFILE%\scoop\apps\git\current\cmd\git.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\PortableGit\*\cmd\git.exe"),
    ):
        cands.extend(sorted(glob.glob(pat), reverse=True))

    for c in cands:
        if c and os.path.exists(c):
            return c
    return "git"   # 兜底：交给 PATH 解析


GIT_EXE = _find_git()

# 把 git 所在目录并入 PATH，让 git 的子进程（ssh、credential helper 等）也能找到
if GIT_EXE and GIT_EXE != "git":
    _git_dir = os.path.dirname(GIT_EXE)
    _path = os.environ.get("PATH", "")
    if _git_dir.lower() not in _path.lower():
        os.environ["PATH"] = _git_dir + os.pathsep + _path
    # 便携版通常需要同级的 usr\bin（ssh.exe / git-credential-manager 等）
    _root = os.path.dirname(_git_dir)
    for sub in ("usr\\bin", "mingw64\\bin", "cmd"):
        _p = os.path.join(_root, sub)
        if os.path.isdir(_p) and _p.lower() not in os.environ["PATH"].lower():
            os.environ["PATH"] = _p + os.pathsep + os.environ["PATH"]

_git_ok_cache = None


def git_available():
    """git 是否真的能执行（结果缓存）。"""
    global _git_ok_cache
    if _git_ok_cache is None:
        try:
            r = subprocess.run([GIT_EXE, "--version"], capture_output=True,
                               text=True, timeout=8)
            _git_ok_cache = (r.returncode == 0)
        except Exception:
            _git_ok_cache = False
    return _git_ok_cache


_git_ver_cache = None


def git_version():
    """返回 git 版本字符串，失败返回 None（结果缓存）。"""
    global _git_ver_cache
    if _git_ver_cache is None:
        try:
            r = subprocess.run([GIT_EXE, "--version"], capture_output=True,
                               text=True, timeout=8)
            _git_ver_cache = (r.stdout or "").strip() if r.returncode == 0 else ""
        except Exception:
            _git_ver_cache = ""
    return _git_ver_cache or None


def run_git(args, cwd, env=None, capture=True):
    """执行 git 命令，返回 (returncode, stdout+stderr)。"""
    try:
        p = subprocess.run(
            [GIT_EXE] + args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (p.stdout or "").strip() if capture else ""
        return p.returncode, out
    except FileNotFoundError:
        return 127, "找不到 git 命令（已尝试 %s）。请安装 Git 或确认其路径。" % GIT_EXE
    except Exception as e:
        return 1, "执行 git 出错：%s" % e


def git_env():
    """构造 git 运行环境：把内网地址从代理中排除。"""
    env = os.environ.copy()
    current = env.get("no_proxy", "") or env.get("NO_PROXY", "")
    parts = [x.strip() for x in current.split(",") if x.strip()]

    extra = list(DEFAULT_NO_PROXY)
    extra += list(_EXTRA_NO_PROXY)
    ev = env.get("GIT_SYNC_NO_PROXY", "")
    extra += [x.strip() for x in ev.split(",") if x.strip()]

    for h in extra:
        if h and h not in parts:
            parts.append(h)
    joined = ",".join(parts)
    env["no_proxy"] = joined
    env["NO_PROXY"] = joined
    return env


# ---------------------------------------------------------------- 配置读写

DEFAULT_CONFIG = {
    "_说明": "每个仓库登记一条。name=显示名；path=本地目录；github/gitlab=远端URL；"
             "branch=本地分支；github_branch/gitlab_branch=远端分支名(不填则同 branch)。"
             "顶层可选 no_proxy=[\"git.mycorp.com\"]，把内网地址排除在代理之外。",
    "no_proxy": [],
    "repos": [],
}


def _apply_no_proxy(data):
    """把配置里的 no_proxy 列表同步到模块级 _EXTRA_NO_PROXY。"""
    global _EXTRA_NO_PROXY
    v = data.get("no_proxy")
    if isinstance(v, list):
        _EXTRA_NO_PROXY = [str(x).strip() for x in v if str(x).strip()]
    elif isinstance(v, str):
        _EXTRA_NO_PROXY = [x.strip() for x in v.split(",") if x.strip()]
    else:
        _EXTRA_NO_PROXY = []


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        _apply_no_proxy(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG, repos=[])
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "repos" not in data or not isinstance(data["repos"], list):
            data["repos"] = []
        _apply_no_proxy(data)
        return data
    except Exception as e:
        err("配置文件读取失败：%s" % e)
        err("文件位置：%s" % CONFIG_PATH)
        sys.exit(1)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _apply_no_proxy(cfg)


# ---------------------------------------------------------------- 仓库操作

def current_branch(repo_path):
    rc, out = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path, git_env())
    if rc == 0 and out and out != "HEAD":
        return out
    return None


def is_git_repo(repo_path):
    return os.path.isdir(os.path.join(repo_path, ".git"))


def has_uncommitted(repo_path):
    rc, out = run_git(["status", "--porcelain"], repo_path, git_env())
    return rc == 0 and bool(out)


def ensure_remote(repo_path, remote, url):
    """确保远端存在且地址正确；不存在则添加，存在但地址不同则更新。"""
    rc, out = run_git(["remote", "get-url", remote], repo_path, git_env())
    if rc != 0:
        rc2, out2 = run_git(["remote", "add", remote, url], repo_path, git_env())
        if rc2 != 0:
            return False, "添加远端 %s 失败：%s" % (remote, out2)
        return True, "已添加远端 %s -> %s" % (remote, url)
    if out.strip() != url.strip():
        rc2, out2 = run_git(["remote", "set-url", remote, url], repo_path, git_env())
        if rc2 != 0:
            return False, "更新远端 %s 地址失败：%s" % (remote, out2)
        return True, "已更新远端 %s 地址 -> %s" % (remote, url)
    return True, "远端 %s 已存在" % remote


def push_remote(repo_path, remote, local_branch, remote_branch=None, set_upstream=True):
    """把本地 local_branch 推送到远端 remote 的 remote_branch。
    两者不同名时用 refspec local:remote 实现改名推送。
    """
    remote_branch = remote_branch or local_branch
    args = ["push", remote, "%s:%s" % (local_branch, remote_branch)]
    if set_upstream and local_branch == remote_branch:
        # 只有同名时才设 upstream（改名推送设 upstream 会与本地分支名冲突）
        args.insert(1, "-u")
    rc, out = run_git(args, repo_path, git_env())
    return rc == 0, out


def remote_branch_sha(repo_path, remote, branch):
    rc, out = run_git(["ls-remote", remote, "refs/heads/%s" % branch], repo_path, git_env())
    if rc != 0 or not out:
        return None
    first = out.splitlines()[0].split()
    return first[0] if first else None


def remote_branch_info(repo_path, url, branch):
    """查询远端某分支的状态（不修改本地 remote 配置，无副作用）。

    返回 dict：
      exists   远端是否有该分支
      sha/short 分支最新 commit
      subject/author/date 提交信息（仅当该对象在本地对象库中）
      known    提交对象是否本地可读
      error    出错信息（网络不通 / 认证失败等）
    """
    info = {"exists": False, "sha": None, "short": None, "subject": None,
            "author": None, "date": None, "known": False, "error": None}

    if not url:
        info["error"] = "未配置远端地址"
        return info

    rc, out = run_git(["ls-remote", url, "refs/heads/%s" % branch],
                      repo_path, git_env())
    if rc != 0:
        info["error"] = (out or "ls-remote 失败").strip()
        return info
    if not out.strip():
        return info      # 远端没有这个分支，exists 保持 False

    first = out.splitlines()[0].split()
    sha = first[0] if first else None
    if not sha:
        return info
    info["exists"] = True
    info["sha"] = sha
    info["short"] = sha[:8]

    # 提交对象若在本地，顺便读出提交信息
    rc2, _ = run_git(["cat-file", "-e", sha + "^{commit}"], repo_path, git_env())
    if rc2 == 0:
        info["known"] = True
        rc3, out3 = run_git(
            ["log", "-1", "--format=%s|%an|%ad",
             "--date=format:%Y-%m-%d %H:%M", sha],
            repo_path, git_env())
        if rc3 == 0 and out3:
            parts = out3.split("|")
            info["subject"] = parts[0] if len(parts) > 0 else None
            info["author"] = parts[1] if len(parts) > 1 else None
            info["date"] = parts[2] if len(parts) > 2 else None
    return info


def remote_status(repo):
    """查询单个仓库：本地 + GitHub + GitLab 三方的分支状态与最新提交。"""
    path = repo.get("path", "")
    res = {"ok": False, "error": None, "branches": {},
           "local": None, "github": None, "gitlab": None}

    if not git_available():
        res["error"] = "找不到可用的 git 命令（尝试路径：%s）" % GIT_EXE
        return res
    if not path or not os.path.isdir(path):
        res["error"] = "本地目录不存在"
        return res
    if not is_git_repo(path):
        res["error"] = "不是 git 仓库（缺 .git）"
        return res

    local_branch, gh_branch, gl_branch = resolve_branches(repo)
    res["branches"] = {"local": local_branch, "github": gh_branch, "gitlab": gl_branch}
    res["ok"] = True

    # ---- 本地 ----
    lsha = local_sha(path, local_branch)
    lsub = lwho = ldate = None
    if lsha:
        rc, out = run_git(["log", "-1", "--format=%s|%an|%ad",
                           "--date=format:%Y-%m-%d %H:%M", lsha],
                          path, git_env())
        if rc == 0 and out:
            p = out.split("|")
            lsub = p[0] if len(p) > 0 else None
            lwho = p[1] if len(p) > 1 else None
            ldate = p[2] if len(p) > 2 else None
    res["local"] = {"exists": bool(lsha), "sha": lsha,
                    "short": lsha[:8] if lsha else None,
                    "subject": lsub, "author": lwho, "date": ldate,
                    "known": True, "error": None if lsha else "本地无此分支"}

    # ---- 两个远端 ----
    gh_url = repo.get("github", "")
    gl_url = repo.get("gitlab", "")
    res["github"] = remote_branch_info(path, gh_url, gh_branch)
    res["github"]["url"] = gh_url
    res["gitlab"] = remote_branch_info(path, gl_url, gl_branch)
    res["gitlab"]["url"] = gl_url
    res["sameRemote"] = bool(gh_url and gl_url and same_remote_url(gh_url, gl_url))
    return res


def local_sha(repo_path, branch="HEAD"):
    rc, out = run_git(["rev-parse", branch], repo_path, git_env())
    return out if rc == 0 else None


# ---------------------------------------------------------------- 拉取

PULL_SOURCES = ("github", "gitlab")


def pull_repo(repo, source="github", force=False, on_log=None):
    """从远端拉取到本地。

    source  "github" 或 "gitlab"
    force   True = 本地有未提交改动 / 分叉时，丢弃本地改动硬对齐远端

    返回 (ok: bool, info: dict)

    策略：
      - 远端无该分支 → 报错（没什么可拉的）
      - 本地有未提交改动 → 默认**拒绝**（保护老板的代码），除非 force
      - 拉取用 fetch + merge --ff-only 优先；不能快进时：
          force=False → 报错并说明（避免产生意料外的 merge）
          force=True  → reset --hard 到远端
    """
    def emit(level, text):
        if on_log:
            try:
                on_log(level, text)
            except Exception:
                pass

    def emit_lines(level, text):
        for ln in str(text).splitlines():
            emit(level, ln)

    source = source if source in PULL_SOURCES else "github"
    label = TARGET_LABEL[source]

    name = repo.get("name") or os.path.basename(repo.get("path", "")) or "未命名"
    path = repo.get("path", "")
    local_branch, github_branch, gitlab_branch = resolve_branches(repo)
    remote_branch = github_branch if source == "github" else gitlab_branch
    url = repo.get("github" if source == "github" else "gitlab", "")

    info = {"name": name, "path": path, "source": source, "ok": False,
            "localBranch": local_branch, "remoteBranch": remote_branch,
            "before": None, "after": None, "changed": False, "errors": []}

    emit(LOG_HEAD, "◀ %s ← %s" % (name, label))
    emit(LOG_INFO, "路径        : %s" % path)
    emit(LOG_INFO, "本地分支    : %s" % local_branch)
    emit(LOG_INFO, "%s 分支 : %s" % (label, remote_branch))

    if not git_available():
        msg = "找不到可用的 git 命令（%s）" % GIT_EXE
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info

    if not path or not os.path.isdir(path):
        msg = "本地目录不存在：%s" % path
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    if not is_git_repo(path):
        msg = "不是 git 仓库（没有 .git）：%s" % path
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    if not url:
        msg = "未配置 %s 远端地址" % label
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info

    good, msg = ensure_remote(path, source, url)
    if not good:
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    emit(LOG_INFO, "· " + msg)

    # 两端配成同一个仓库时给个醒目的提醒
    other_url = repo.get("github" if source == "gitlab" else "gitlab", "")
    if other_url and same_remote_url(url, other_url):
        emit(LOG_WARN, "! 注意：GitHub 与 GitLab 填的是同一个地址")
        emit(LOG_WARN, "  所以这次「从 %s 拉取」拉到的其实是同一个仓库。" % label)

    # 当前所在分支
    cur = current_branch(path)
    if cur and cur != local_branch:
        emit(LOG_WARN, "! 当前所在分支是 %s，将在该分支上操作" % cur)
        emit(LOG_WARN, "  （建议先切到 %s 再拉取）" % local_branch)

    # 未提交改动保护
    dirty = has_uncommitted(path)
    if dirty and not force:
        msg = "本地有未提交的改动，已停止（加 force 可强制丢弃并覆盖）"
        emit(LOG_ERR, "✖ " + msg)
        emit(LOG_WARN, "  你的改动还在，先用 git stash / git commit 处理，")
        emit(LOG_WARN, "  或勾选「强制覆盖本地」再拉取。")
        info["errors"].append(msg)
        return False, info
    if dirty and force:
        emit(LOG_WARN, "! 本地有未提交改动，将丢弃它们（force 模式）")

    info["before"] = local_sha(path, local_branch)

    # 远端分支是否存在
    rinfo = remote_branch_info(path, url, remote_branch)
    if rinfo.get("error"):
        emit(LOG_ERR, "✖ 无法访问 %s：%s" % (label, rinfo["error"]))
        info["errors"].append(rinfo["error"])
        return False, info
    if not rinfo.get("exists"):
        msg = "%s 上没有分支 %s，无可拉取内容" % (label, remote_branch)
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    emit(LOG_INFO, "· 远端最新：%s %s" % (rinfo["short"],
                                   ("「%s」" % rinfo["subject"]) if rinfo.get("subject") else ""))

    # fetch
    refspec = "+refs/heads/%s:refs/remotes/%s/%s" % (remote_branch, source, remote_branch)
    rc, out = run_git(["fetch", source, refspec], path, git_env())
    if rc != 0:
        emit(LOG_ERR, "✖ fetch 失败：")
        emit_lines(LOG_ERR, out)
        info["errors"].append("fetch 失败")
        return False, info
    emit(LOG_OK, "✔ 已从 %s 获取最新数据" % label)

    target_rev = "%s/%s" % (source, remote_branch)

    if force:
        rc2, out2 = run_git(["reset", "--hard", target_rev], path, git_env())
        if rc2 != 0:
            emit(LOG_ERR, "✖ 强制对齐失败：")
            emit_lines(LOG_ERR, out2)
            info["errors"].append("reset 失败")
            return False, info
        emit(LOG_OK, "✔ 已强制对齐到 %s" % label)
    else:
        rc2, out2 = run_git(["merge", "--ff-only", target_rev], path, git_env())
        if rc2 != 0:
            emit(LOG_ERR, "✖ 无法快进合并：本地与 %s 已分叉" % label)
            emit_lines(LOG_WARN, out2)
            emit(LOG_WARN, "  两条路可选：")
            emit(LOG_WARN, "   1) 先推送本地覆盖远端（把本地推到远端）")
            emit(LOG_WARN, "   2) 勾选「强制覆盖本地」再拉取（丢弃本地提交/改动）")
            info["errors"].append("本地与远端分叉，未合并")
            return False, info
        if "Already up to date" in out2 or "Already up-to-date" in out2:
            emit(LOG_OK, "✔ 本地已是最新，无需更新")
        else:
            emit(LOG_OK, "✔ 已拉取并快进合并")

    info["after"] = local_sha(path, local_branch)
    info["changed"] = bool(info["before"] != info["after"])
    info["ok"] = True

    emit(LOG_HEAD, "结果")
    emit(LOG_INFO, "本地现在 : %s" % (info["after"][:8] if info["after"] else "?"))
    if info["changed"]:
        emit(LOG_OK, "✔ 本地已更新")
    else:
        emit(LOG_INFO, "· 无变化")

    log_line("pull %s source=%s changed=%s" % (name, source, info["changed"]))
    return True, info


# ---------------------------------------------------------------- 一致性校验

def verify_consistency(repo_path, local_branch, github_branch=None, gitlab_branch=None):
    """对比 本地 / GitHub / GitLab 的 commit。返回 dict。

    分支名可以为 None —— 表示该远端不参与本次比较（例如只推 GitHub 时）。
    三个分支名可以不同（改名推送场景），比较的是 commit 内容是否一致。
    """
    l = local_sha(repo_path, local_branch)
    g = remote_branch_sha(repo_path, GITHUB_REMOTE, github_branch) if github_branch else None
    b = remote_branch_sha(repo_path, GITLAB_REMOTE, gitlab_branch) if gitlab_branch else None

    vals = [l]
    if github_branch:
        vals.append(g)
    if gitlab_branch:
        vals.append(b)
    # 至少要比两个（本地 + 一个远端）才有意义
    consistent = bool(l) and len(vals) > 1 and all(v == l for v in vals)

    return {"local": l, "github": g, "gitlab": b, "consistent": consistent}


def _emit_consistency(emit, res, local_branch, github_branch, gitlab_branch):
    """把一致性结果通过 emit 回调输出。分支名为 None 的远端跳过。"""
    def short(x):
        return x[:8] if x else "（无此分支）"
    emit(LOG_INFO, "本地   [%s] : %s" % (local_branch, short(res["local"])))
    if github_branch:
        emit(LOG_INFO, "GitHub [%s] : %s" % (github_branch, short(res["github"])))
    if gitlab_branch:
        emit(LOG_INFO, "GitLab [%s] : %s" % (gitlab_branch, short(res["gitlab"])))
    if res["consistent"]:
        emit(LOG_OK, "✔ commit 一致")
    else:
        emit(LOG_WARN, "✖ 存在差异")


# ---------------------------------------------------------------- 核心流程

# 日志级别（供 GUI 着色）
LOG_INFO = "info"
LOG_OK = "ok"
LOG_WARN = "warn"
LOG_ERR = "err"
LOG_HEAD = "head"

VALID_TARGETS = ("github", "gitlab")
TARGET_LABEL = {"github": "GitHub", "gitlab": "GitLab"}


def norm_remote_url(u):
    """把远端地址规范化，用于判断两个地址是否其实是同一个仓库。"""
    if not u:
        return ""
    s = str(u).strip().lower()
    s = s.replace("\\", "/")
    if s.endswith("/"):
        s = s[:-1]
    if s.endswith(".git"):
        s = s[:-4]
    # 去掉协议前缀，https 与 http、带不带协议都视为同一个
    for pre in ("https://", "http://", "ssh://", "git://"):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    # 去掉 user@ 部分（如 git@github.com:xxx -> github.com/xxx）
    if "@" in s.split("/")[0]:
        s = s.split("@", 1)[1]
    s = s.replace(":", "/", 1) if ("/" not in s.split(":")[0] and ":" in s) else s
    return s.rstrip("/")


def same_remote_url(a, b):
    """两个远端地址是否指向同一个仓库。"""
    na, nb = norm_remote_url(a), norm_remote_url(b)
    return bool(na) and na == nb


def resolve_branches(repo):
    """解析分支配置，返回 (local, github, gitlab)。

    规则：
      - local 分支 = repo["branch"]（本地要推送的分支，默认 main）
      - github 分支 = repo.get("github_branch") 或回落到 local
      - gitlab 分支 = repo.get("gitlab_branch") 或回落到 local
    兼容旧配置：只写 branch 时，两个远端都用它。
    """
    local = repo.get("branch") or "main"
    gh = repo.get("github_branch") or local
    gl = repo.get("gitlab_branch") or local
    return local, gh, gl


def sync_repo(repo, targets=VALID_TARGETS, branch_override=None,
              check_only=False, on_log=None):
    """推送单个仓库到指定远端（CLI 与 GUI 共用）。

    repo            仓库配置 dict
    targets         要推送的远端，{"github","gitlab"} 的子集
    branch_override 临时覆盖本地分支名
    check_only      只做一致性检查，不推送
    on_log          可选回调 on_log(level, text)，用于 GUI 实时输出

    返回 (all_ok: bool, info: dict)
    """
    targets = tuple(t for t in targets if t in VALID_TARGETS) or VALID_TARGETS

    def emit(level, text):
        if on_log:
            try:
                on_log(level, text)
            except Exception:
                pass

    def emit_lines(level, text):
        for ln in str(text).splitlines():
            emit(level, ln)

    name = repo.get("name") or os.path.basename(repo.get("path", "")) or "未命名"
    path = repo.get("path", "")
    urls = {"github": repo.get("github", ""), "gitlab": repo.get("gitlab", "")}

    local_branch, github_branch, gitlab_branch = resolve_branches(repo)
    if branch_override:
        local_branch = branch_override
        if not repo.get("github_branch"):
            github_branch = branch_override
        if not repo.get("gitlab_branch"):
            gitlab_branch = branch_override

    info = {
        "name": name,
        "path": path,
        "targets": list(targets),
        "branches": {"local": local_branch, "github": github_branch, "gitlab": gitlab_branch},
        "pushed": {},
        "errors": [],
        "consistent": None,
        "ok": False,
    }

    emit(LOG_HEAD, "▶ %s" % name)
    emit(LOG_INFO, "路径        : %s" % path)
    emit(LOG_INFO, "本地分支    : %s" % local_branch)
    emit(LOG_INFO, "GitHub 分支 : %s" % github_branch)
    emit(LOG_INFO, "GitLab 分支 : %s" % gitlab_branch)
    emit(LOG_INFO, "推送目标    : %s" % "、".join(TARGET_LABEL[t] for t in targets))

    # 0. git 是否可用（本机可能只有便携版，双击启动时 PATH 里没有）
    if not git_available():
        msg = "找不到可用的 git 命令（尝试路径：%s）。请确认本机已安装 Git。" % GIT_EXE
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info

    # 1. 前置校验
    if not path or not os.path.isdir(path):
        msg = "本地目录不存在：%s" % path
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    if not is_git_repo(path):
        msg = "不是 git 仓库（没有 .git）：%s" % path
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info
    for t in targets:
        if not urls[t]:
            msg = "未配置 %s 远端地址" % TARGET_LABEL[t]
            emit(LOG_ERR, "✖ " + msg)
            info["errors"].append(msg)
            return False, info

    # 两个远端配成同一个仓库 → 必须拦住
    # （否则"推 GitLab"其实推的是 GitHub，GitLab 一个字节都收不到，还会误报成功）
    if len(targets) == 2 and same_remote_url(urls["github"], urls["gitlab"]):
        msg = ("GitHub 与 GitLab 填的是同一个地址（%s）—— 继续下去会把代码推两遍到"
               "同一个仓库，真正的 GitLab 收不到任何东西。" % urls["github"])
        emit(LOG_ERR, "✖ " + msg)
        emit(LOG_WARN, "  请到「仓库设置」把 GitLab 地址改成真正的 GitLab 仓库地址，再推。")
        info["errors"].append(msg)
        info["sameRemote"] = True
        return False, info

    # 2. 本地分支确认
    cur = current_branch(path)
    if cur and cur != local_branch:
        emit(LOG_WARN, "! 当前所在分支是 %s，与要推送的本地分支 %s 不同" % (cur, local_branch))
        emit(LOG_WARN, "  （将推送本地 %s 分支的内容，不会自动切换）" % local_branch)
    rc, out = run_git(["rev-parse", "--verify", local_branch], path, git_env())
    if rc != 0:
        msg = "本地不存在分支 %s" % local_branch
        emit(LOG_ERR, "✖ " + msg)
        info["errors"].append(msg)
        return False, info

    # 3. 未提交改动提醒（不阻塞）
    if has_uncommitted(path):
        emit(LOG_WARN, "! 有未提交的改动，不会被推送（需先 commit）")

    # 4. 准备远端
    remote_map = {
        "github": (GITHUB_REMOTE, urls["github"], github_branch),
        "gitlab": (GITLAB_REMOTE, urls["gitlab"], gitlab_branch),
    }
    for t in targets:
        remote, url, _ = remote_map[t]
        good, msg = ensure_remote(path, remote, url)
        if not good:
            emit(LOG_ERR, "✖ " + msg)
            info["errors"].append(msg)
            return False, info
        emit(LOG_INFO, "· " + msg)

    # 5. 仅检查模式
    if check_only:
        emit(LOG_HEAD, "一致性检查（未推送）")
        gb = github_branch if "github" in targets else None
        lb = gitlab_branch if "gitlab" in targets else None
        res = verify_consistency(path, local_branch, gb, lb)
        _emit_consistency(emit, res, local_branch, gb, lb)
        info["consistent"] = res["consistent"]
        info["consistency"] = res
        info["ok"] = res["consistent"]
        return res["consistent"], info

    # 6. 推送
    all_ok = True
    for t in targets:
        remote, _, rbranch = remote_map[t]
        label = TARGET_LABEL[t]
        pushed, out = push_remote(path, remote, local_branch, rbranch)
        info["pushed"][t] = pushed
        if pushed:
            if rbranch == local_branch:
                emit(LOG_OK, "✔ 已推送到 %s（%s）" % (label, rbranch))
            else:
                emit(LOG_OK, "✔ 已推送到 %s（本地 %s → 远端 %s）" % (label, local_branch, rbranch))
        else:
            all_ok = False
            emit(LOG_ERR, "✖ 推送到 %s 失败：" % label)
            emit_lines(LOG_ERR, out)
            info["errors"].append("推送到 %s 失败" % label)
            if "non-fast-forward" in out or "rejected" in out or "fetch first" in out:
                emit(LOG_WARN, "↳ 原因：远端 %s 上有本地没有的提交，为避免覆盖已拒绝。" % label)
                emit(LOG_WARN, "  若确认要以本地为准，请执行：")
                emit(LOG_WARN, "  git push --force %s %s:%s" % (remote, local_branch, rbranch))

    # 7. 推送后一致性校验
    emit(LOG_HEAD, "推送结果 · 分支一致性")
    gb = github_branch if "github" in targets else None
    lb = gitlab_branch if "gitlab" in targets else None
    res = verify_consistency(path, local_branch, gb, lb)
    _emit_consistency(emit, res, local_branch, gb, lb)
    info["consistent"] = res["consistent"]
    info["consistency"] = res
    if not res["consistent"]:
        all_ok = False

    info["ok"] = all_ok
    log_line("sync %s targets=%s local=%s result=%s"
             % (name, "+".join(targets), local_branch, "OK" if all_ok else "FAIL"))
    return all_ok, info


def sync_many(repos, targets=VALID_TARGETS, branch_override=None,
              check_only=False, on_log=None):
    """依次推送/检查多个仓库。返回 (all_ok, [info, ...])。"""
    results = []
    all_ok = True
    for i, r in enumerate(repos):
        if i > 0 and on_log:
            on_log(LOG_INFO, "")
        one_ok, info = sync_repo(r, targets, branch_override, check_only, on_log)
        results.append(info)
        all_ok = all_ok and one_ok
    return all_ok, results


def _cli_logger(level, text):
    """把 sync_repo 的日志回调映射到控制台（保留原有缩进风格）。"""
    if text == "":
        say()
        return
    line = "  " + text
    if level == LOG_HEAD:
        head(line)
    elif level == LOG_OK:
        ok(line)
    elif level == LOG_WARN:
        warn(line)
    elif level == LOG_ERR:
        err(line)
    else:
        say(line)


def sync_one(repo, branch_override=None, check_only=False):
    """CLI 单仓库推送（包装 sync_repo，推送到两个远端）。返回 True/False。"""
    return sync_repo(repo, VALID_TARGETS, branch_override, check_only, _cli_logger)[0]


# ---------------------------------------------------------------- 交互界面

def pick_repo(cfg):
    repos = cfg.get("repos", [])
    if not repos:
        head("还没有登记任何仓库")
        say("  请先运行   python git_sync.py --add   登记一个仓库，")
        say("  或直接用文本编辑器填写：%s" % CONFIG_PATH)
        return None
    head("已登记的仓库：")
    for i, r in enumerate(repos, 1):
        name = r.get("name") or os.path.basename(r.get("path", ""))
        local_b, gh_b, gl_b = resolve_branches(r)
        if gh_b == gl_b == local_b:
            br_desc = local_b
        else:
            br_desc = "本地%s → GH:%s / GL:%s" % (local_b, gh_b, gl_b)
        exists = "✔" if os.path.isdir(r.get("path", "")) else "✖目录不存在"
        say("  %s%d.%s %-22s [%s] %s" % (C.B, i, C.R, name, br_desc, exists))
    say()
    sel = input("请输入要推送的仓库编号（直接回车取消）: ").strip()
    if not sel:
        return None
    if not sel.isdigit() or not (1 <= int(sel) <= len(repos)):
        err("无效的编号")
        return None
    return repos[int(sel) - 1]


def cmd_add(cfg):
    head("登记新仓库")
    path = input("  本地仓库目录（可直接把文件夹拖进来）: ").strip().strip('"')
    if not path or not os.path.isdir(path):
        err("  目录不存在，已取消")
        return
    if not is_git_repo(path):
        warn("  ! 该目录还不是 git 仓库")
        if input("  要现在执行 git init 吗？(y/N): ").strip().lower() == "y":
            rc, out = run_git(["init"], path, git_env())
            say("  %s" % (out or "已初始化"))
        else:
            err("  已取消")
            return
    name = input("  显示名称（回车用目录名）: ").strip() or os.path.basename(path)
    gh = input("  GitHub 仓库地址（https://github.com/用户/仓库.git）: ").strip()
    gl = input("  GitLab 仓库地址（http://git.mycompany.com/组/仓库.git）: ").strip()
    auto_br = current_branch(path) or "main"
    br = input("  本地分支（回车用 %s）: " % auto_br).strip() or auto_br
    gh_br = input("  GitHub 上的分支名（回车同本地 %s）: " % br).strip()
    gl_br = input("  GitLab 上的分支名（回车同本地 %s）: " % br).strip()
    entry = {"name": name, "path": path, "github": gh,
             "gitlab": gl, "branch": br}
    if gh_br and gh_br != br:
        entry["github_branch"] = gh_br
    if gl_br and gl_br != br:
        entry["gitlab_branch"] = gl_br
    cfg["repos"].append(entry)
    save_config(cfg)
    ok("  ✔ 已登记：%s" % name)


def main():
    args = [a for a in sys.argv[1:]]

    if "--list" in args:
        cfg = load_config()
        for i, r in enumerate(cfg.get("repos", []), 1):
            say("%d. %s -> %s | %s" % (i, r.get("name"), r.get("github"), r.get("gitlab")))
        if not cfg.get("repos"):
            say("（暂无登记仓库）")
        return

    cfg = load_config()

    if "--add" in args:
        cmd_add(cfg)
        return

    check_only = "--check" in args
    pos = [a for a in args if not a.startswith("--")]

    repo = None
    branch_override = None
    if pos:
        if pos[0].isdigit():
            idx = int(pos[0]) - 1
            repos = cfg.get("repos", [])
            if 0 <= idx < len(repos):
                repo = repos[idx]
            else:
                err("仓库编号越界")
                return
        else:
            # 允许直接用路径
            repo = {"name": os.path.basename(pos[0]), "path": pos[0]}
            cfg_repo = [r for r in cfg.get("repos", []) if r.get("path") == pos[0]]
            if cfg_repo:
                repo = cfg_repo[0]
        if len(pos) > 1:
            branch_override = pos[1]
    else:
        repo = pick_repo(cfg)

    if not repo:
        return

    rule()
    success = sync_one(repo, branch_override, check_only)
    rule()
    if success:
        ok("全部完成 ✔")
    elif check_only:
        warn("检查完成：检测到双远端不一致 ✖")
    else:
        err("存在失败项，请查看上面的红色输出（日志：%s）" % LOG_PATH)

    if os.name == "nt" and not pos:
        say()
        try:
            input("按回车键关闭窗口...")
        except EOFError:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say()
        warn("已取消")
    except EOFError:
        # stdin 被关闭（如管道调用、双击后立即关闭）时静默退出
        pass
