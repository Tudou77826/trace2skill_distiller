"""Comprehensive CLI test suite — framework, config, sessions, usability.

NOTE: Does NOT test commands that invoke LLM (inspect session, run).
      Those should be tested manually or in a separate integration test.
"""

import os
import re
from pathlib import Path

from click.testing import CliRunner

from trace2skill_distiller.cli.main import cli


def _load_env():
    env_file = Path.home() / ".trace2skill" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                if k.strip().startswith("TRACE2SKILL_"):
                    os.environ[k.strip()] = v.strip()


_load_env()
runner = CliRunner()
results = []


def test(name, cmd, expect_exit=0, check=None):
    r = runner.invoke(cli, cmd)
    ok = r.exit_code == expect_exit
    detail = ""
    if check and ok:
        ok = check(r.output)
        if not ok:
            detail = "output check failed"
    if r.exception and not ok:
        import traceback
        detail = "".join(
            traceback.format_exception(type(r.exception), r.exception, r.exception.__traceback__)
        )[:300]
    status = "PASS" if ok else "FAIL"
    results.append({
        "name": name, "cmd": " ".join(cmd),
        "exit": r.exit_code, "expect": expect_exit,
        "status": status, "detail": detail,
    })
    print(f"  [{status}] {name}  (exit={r.exit_code})")
    return r


def section(title):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# T1: 基础 CLI 框架
# ═══════════════════════════════════════════════════════════════
section("T1: 基础 CLI 框架")

test("--help 有命令列表", ["--help"], 0, lambda o: "Commands:" in o)
test("--version 有版本号", ["--version"], 0, lambda o: "0.1" in o)
test("无效命令返回错误", ["foobar"], 2)

# 所有顶级命令
test("顶级命令完整(7个)", ["--help"], 0,
     lambda o: all(c in o for c in ["init", "config", "doctor", "inspect", "run", "runs", "sessions"]))

# 每个命令的 --help
for cmd in ["init", "config", "doctor", "inspect", "run", "runs", "sessions"]:
    test(f"{cmd} --help", [cmd, "--help"], 0, lambda o: len(o.strip()) > 50)

# 二级子命令 --help
for sub in [
    ["config", "show", "--help"],
    ["config", "set", "--help"],
    ["config", "edit", "--help"],
    ["sessions", "list", "--help"],
    ["runs", "list", "--help"],
    ["inspect", "session", "--help"],
    ["inspect", "run", "--help"],
    ["run", "--help"],
]:
    label = " ".join(sub[:-1])
    test(f"{label} --help", sub, 0, lambda o: len(o.strip()) > 50)


# ═══════════════════════════════════════════════════════════════
# T2: 配置管理
# ═══════════════════════════════════════════════════════════════
section("T2: 配置管理")

test("config show 成功", ["config", "show"], 0, lambda o: "model" in o)
test("config show 含 Fast Model", ["config", "show"], 0, lambda o: "Fast" in o)
test("config show 含 Strong Model", ["config", "show"], 0, lambda o: "Strong" in o)
test("config show API Key 脱敏", ["config", "show"], 0, lambda o: "*" in o)
test("config show 含 Source 信息", ["config", "show"], 0, lambda o: "source" in o.lower() or "opencode" in o.lower())

# config set 各种数据类型
test("set float (timeout)", ["config", "set", "fast.timeout", "180"], 0)
test("set bool (verify_ssl)", ["config", "set", "fast.verify_ssl", "true"], 0)
test("set int (max_tokens)", ["config", "set", "fast.max_tokens", "8192"], 0)
test("set string (model)", ["config", "set", "fast.model", "test-model-temp"], 0)
# 恢复
runner.invoke(cli, ["config", "set", "fast.model", "fast-m"])
runner.invoke(cli, ["config", "set", "fast.max_tokens", "4096"])

# 错误处理
test("set 无效 key", ["config", "set", "invalid.key", "x"], 1)
test("set 缺少参数", ["config", "set"], 2)
test("set 缺少 value", ["config", "set", "fast.model"], 2)

# set 后 show 能反映变化
runner.invoke(cli, ["config", "set", "fast.timeout", "200"])
test("set 后 show 反映变化", ["config", "show"], 0, lambda o: "200" in o)
runner.invoke(cli, ["config", "set", "fast.timeout", "120"])


# ═══════════════════════════════════════════════════════════════
# T3: 会话浏览（不调 LLM）
# ═══════════════════════════════════════════════════════════════
section("T3: 会话浏览")

test("sessions list 默认", ["sessions", "list"], 0, lambda o: len(o.strip()) > 0)
test("sessions list --limit 5", ["sessions", "list", "--limit", "5"], 0,
     lambda o: "5/" in o or len(o.strip()) > 50)
test("sessions list -p 过滤", ["sessions", "list", "-p", "chrys"], 0)
test("sessions list --include-low-quality", ["sessions", "list", "--include-low-quality", "--limit", "3"], 0)
test("sessions list 不存在项目", ["sessions", "list", "-p", "nonexistent_xyz"], 0,
     lambda o: "0/" in o or len(o.strip()) < 100)
test("sessions list --limit 0", ["sessions", "list", "--limit", "0"], 0)

# 获取真实 session id
r = runner.invoke(cli, ["sessions", "list", "--limit", "1"])
sid_match = re.search(r"(ses_\w+)", r.output)
sid = sid_match.group(1) if sid_match else None
if sid:
    print(f"  [INFO] 测试用会话: {sid[:20]}...")
else:
    print("  [WARN] 未找到可用会话")

# inspect 不存在的 session
test("inspect 不存在的 session", ["inspect", "session", "nonexistent_abc123"], 1,
     lambda o: "Error" in o or "error" in o.lower() or len(o) > 0)


# ═══════════════════════════════════════════════════════════════
# T4: 运行历史与诊断
# ═══════════════════════════════════════════════════════════════
section("T4: 运行历史与诊断")

test("runs list", ["runs", "list"], 0)
test("doctor", ["doctor"], 0, lambda o: len(o.strip()) > 100)


# ═══════════════════════════════════════════════════════════════
# T5: run 命令参数验证（不执行 pipeline）
# ═══════════════════════════════════════════════════════════════
section("T5: run 命令参数验证")

test("run --help 显示 mode 选项", ["run", "--help"], 0,
     lambda o: "preprocess" in o and "analyze" in o and "full" in o)
test("run --help 显示 preview 选项", ["run", "--help"], 0,
     lambda o: "preview" in o)
test("run --help 显示 output 选项", ["run", "--help"], 0,
     lambda o: "skill" in o and "knowledge" in o)
test("run --mode 无效值报错", ["run", "--mode", "invalid"], 2)
test("run --output 无效值报错", ["run", "--output", "invalid"], 2)


# ═══════════════════════════════════════════════════════════════
# T6: 易用性与边界条件
# ═══════════════════════════════════════════════════════════════
section("T6: 易用性与边界条件")

# 中文
r = runner.invoke(cli, ["--help"])
has_chinese = any(ord(c) > 0x4E00 for c in r.output)
test("帮助信息含中文", ["--help"], 0, lambda _: has_chinese)

# 拼写错误提示
test("拼写错误有提示", ["sesions", "list"], 2,
     lambda o: "Error" in o or "No such command" in o or "Did you mean" in o)

# --verbose
test("--verbose 模式不崩溃", ["--verbose", "doctor"], 0)

# 空结果
test("空结果优雅处理", ["sessions", "list", "-p", "zzz_nonexistent"], 0)

# 超大 limit
test("sessions list 超大 limit", ["sessions", "list", "--limit", "9999"], 0)

# 帮助含示例
test("顶级帮助含示例", ["--help"], 0, lambda o: "$" in o or "trace2skill" in o)

# 帮助各命令详细度
for cmd_path in [
    ["init", "--help"],
    ["sessions", "list", "--help"],
    ["run", "--help"],
    ["doctor", "--help"],
]:
    test(" ".join(cmd_path) + " 足够详细", cmd_path, 0, lambda o: len(o.strip()) > 80)


# ═══════════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════════
section("测试汇总")

passed = sum(1 for r in results if r["status"] == "PASS")
failed = sum(1 for r in results if r["status"] == "FAIL")
print(f"总计: {len(results)} 项  |  通过: {passed}  |  失败: {failed}")

if failed:
    print()
    print("失败项详情:")
    for r in results:
        if r["status"] == "FAIL":
            print(f"  - {r['name']}")
            print(f"    cmd: {r['cmd']}")
            print(f"    exit_code: {r['exit']} (expect: {r['expect']})")
            if r["detail"]:
                print(f"    detail: {r['detail'][:200]}")
    exit(1)
else:
    print()
    print("全部通过!")
