#!/usr/bin/env python3
"""
AI 去广告社区桥 - NAS 侧分析管线
=================================

流程（cron 每 30 分钟跑一次即可）：
  1. 从 PHP 桥拉取待分析反馈（pull-queue.php）
  2. 逐条把脱敏 DOM 快照 + 请求拦截记录发给 LLM 分析
  3. 严格校验 AI 输出的规则（白名单 + 正则编译检查 + 数量上限）
  4. 合并进 site-rules.json（按 domain upsert）
  5. 全量上传回 PHP 桥（upload-rules.php），全体用户下次拉取即生效
  6. 删除已处理反馈

只用标准库，无第三方依赖。LLM 走 OpenAI 兼容接口（GLM / DeepSeek 等均可）。

用法：
  cp config.sample.ini config.ini   # 填好桥地址/密钥/LLM key
  python3 pipeline.py               # 单轮处理
  python3 pipeline.py --loop 1800   # 常驻，每 30 分钟一轮
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from configparser import ConfigParser
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.ini"

# AI 可以输出的 DOM 规则白名单（与 AdCleaner.js 的行为规则一一对应）
ALLOWED_RULES = {
    "invisible-hotspot",  # 内联隐形热区（fixed + opacity<0.05 + >=20px）
    "computed-hotspot",    # 计算样式隐形热区（两轮确认）
    "bottom-banner",       # 贴底横幅（>=85% 宽 + 45~320px 高 + 下半屏）
    "random-tag",          # body 直挂非标准自定义标签
    "random-name",         # body 直挂随机命名容器（3~12 位字母 >=2 大写）
    "cover-iframe",        # z-index>=99999 的全屏 iframe 盖层
    "ad-host",             # src/href 命中站点黑名单的节点
    "popup-text",          # 公告/年龄验证弹窗文案形态
}
MAX_HOSTS_PER_SITE = 24
MAX_COSMETIC_PER_SITE = 24
MAX_HTMLSIG_PER_SITE = 12
MAX_NAVALLOW_PER_SITE = 16
MAX_SITES = 2000

PROMPT_TEMPLATE = """你是一个网页广告逆向分析专家。下面是一个用户反馈的站点页面数据：
- host: {host}
- url: {url}
- 被网络层拦截的请求（部分）: {requests_blocked}
- 放行的主要第三方请求（部分）: {requests_allowed}
- 页面 DOM 结构普查（tag/id/class/几何/透明度，已脱敏无正文）:
{dom_excerpt}

任务：判断该站存在哪些广告与劫持形态，输出一个 JSON 对象（只输出 JSON，不要其他文字）：
{{
  "block_hosts": ["确凿的广告/推广域名，从请求数据里来，禁止臆造"],
  "nav_allow": ["页面正常跳转应保留的域名，含主域与镜像；该站无跳转劫持时给空数组"],
  "dom_rules": ["从这些里选: invisible-hotspot, computed-hotspot, bottom-banner, random-tag, random-name, cover-iframe, ad-host, popup-text"],
  "cosmetic": ["确凿的推广位 CSS 选择器，最多 {max_cosmetic} 个，没有就空数组"]
}}

判断原则：
1. 只描述行为形态，域名/类名只作兜底，一切以数据为准；
2. 宁缺勿滥：不确定的不要写；nav_allow 不确定时给空数组（表示不启用防跳转闸门）；
3. block_hosts 必须来自请求数据中真实出现过的域名；
4. 站点干净时输出全空数组。"""


def die(msg: str) -> None:
    print(f"[pipeline] FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        die(f"缺少配置文件：{CONFIG_PATH}（先复制 config.sample.ini）")
    parser = ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")
    cfg = {
        "bridge_base": parser.get("bridge", "base_url").rstrip("/"),
        "bridge_secret": parser.get("bridge", "secret"),
        "llm_base": parser.get("llm", "base_url").rstrip("/"),
        "llm_key": parser.get("llm", "api_key"),
        "llm_model": parser.get("llm", "model"),
    }
    for k, v in cfg.items():
        if not v:
            die(f"配置项为空：{k}")
    return cfg


def http_json(url: str, payload: bytes = None, headers: dict = None, timeout: int = 120):
    """GET/POST 返回 (status, parsed_json_or_text)。失败抛异常由调用方决定。"""
    req = urllib.request.Request(url, data=payload, headers=headers or {}, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"_raw": body}


def queue_list(cfg) -> list:
    url = f"{cfg['bridge_base']}/pull-queue.php?secret={cfg['bridge_secret']}&max=20"
    data = http_json(url)
    return data.get("items", [])


def queue_fetch(cfg, name: str) -> dict:
    import urllib.parse
    quoted = urllib.parse.quote(name)
    url = f"{cfg['bridge_base']}/pull-queue.php?secret={cfg['bridge_secret']}&fetch={quoted}"
    with urllib.request.urlopen(url, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def queue_delete(cfg, name: str) -> None:
    import urllib.parse
    quoted = urllib.parse.quote(name)
    url = f"{cfg['bridge_base']}/pull-queue.php?secret={cfg['bridge_secret']}&delete={quoted}"
    try:
        http_json(url)
    except Exception as exc:
        print(f"[pipeline] WARN: 删除反馈失败 {name}: {exc}")


def llm_analyze(cfg, feedback: dict) -> dict:
    """把一条反馈交给 LLM，返回候选规则 dict（未校验）。"""
    dom = feedback.get("dom", "[]")
    if isinstance(dom, str):
        try:
            dom = json.loads(dom)
        except json.JSONDecodeError:
            dom = []
    # 截断 DOM 摘要，控制 token（保留前 220 个元素）
    dom_excerpt = json.dumps(dom[:220], ensure_ascii=False)
    if len(dom_excerpt) > 60000:
        dom_excerpt = dom_excerpt[:60000] + " ..."

    requests = feedback.get("requests", [])
    blocked = [r.get("url", "") for r in requests if r.get("blocked")][:120]
    allowed = [r.get("url", "") for r in requests if not r.get("blocked")][:120]

    prompt = PROMPT_TEMPLATE.format(
        host=feedback.get("host", ""),
        url=feedback.get("url", ""),
        requests_blocked=json.dumps(blocked, ensure_ascii=False),
        requests_allowed=json.dumps(allowed, ensure_ascii=False),
        dom_excerpt=dom_excerpt,
        max_cosmetic=MAX_COSMETIC_PER_SITE,
    )

    body = json.dumps({
        "model": cfg["llm_model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2000,
    }, ensure_ascii=False).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['llm_key']}",
    }
    result = http_json(f"{cfg['llm_base']}/chat/completions", payload=body, headers=headers, timeout=300)
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    # 剥掉可能的 ```json 围栏
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    return json.loads(content)


HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$", re.I)
SELECTOR_RE = re.compile(r"^[A-Za-z0-9_\-.*#:\[\]=@~'\" +(),>~^$!]+$")


def sanitize_candidates(raw: dict, feedback: dict) -> dict:
    """把 AI 输出清洗成可执行规则。任何不合法字段直接丢弃（fail-safe）。"""
    def hosts_list(value, limit):
        out = []
        if not isinstance(value, list):
            return out
        for h in value:
            h = str(h).strip().lower().lstrip("*.") if isinstance(h, str) else ""
            if h and HOST_RE.match(h) and h not in out:
                out.append(h)
            if len(out) >= limit:
                break
        return out

    def wildcard_hosts(value, limit):
        out = []
        if not isinstance(value, list):
            return out
        for h in value:
            if not isinstance(h, str):
                continue
            h = h.strip().lower()
            if not h:
                continue
            # 允许 example.com 或 *.example.com
            if h.startswith("*."):
                base = h[2:]
            else:
                base = h
            if base and HOST_RE.match(base) and h not in out:
                out.append(h)
            if len(out) >= limit:
                break
        return out

    rules = [r for r in (raw.get("dom_rules") or []) if r in ALLOWED_RULES][:len(ALLOWED_RULES)]

    cosmetic = []
    for sel in (raw.get("cosmetic") or []):
        if not isinstance(sel, str):
            continue
        sel = sel.strip()
        if 0 < len(sel) < 200 and SELECTOR_RE.match(sel) and sel not in cosmetic:
            cosmetic.append(sel)
        if len(cosmetic) >= MAX_COSMETIC_PER_SITE:
            break

    return {
        "hosts": hosts_list(raw.get("block_hosts"), MAX_HOSTS_PER_SITE),
        "navAllow": wildcard_hosts(raw.get("nav_allow"), MAX_NAVALLOW_PER_SITE),
        "rules": rules,
        "cosmetic": cosmetic,
    }


def analyze_and_merge(feedback: dict, rules_doc: dict) -> bool:
    """单条反馈走完 LLM -> 清洗 -> 合并。返回是否有变化。"""
    raw = llm_analyze(CFG, feedback)
    clean = sanitize_candidates(raw, feedback)
    if not (clean["hosts"] or clean["navAllow"] or clean["rules"] or clean["cosmetic"]):
        return False  # 站点干净或 AI 没把握，不动文档

    domain = feedback.get("host", "").lower()
    sites = rules_doc.setdefault("sites", [])
    site = next((s for s in sites if s.get("domain", "").lower() == domain), None)
    if site is None:
        if len(sites) >= MAX_SITES:
            print("[pipeline] WARN: 站点数达上限，跳过新站")
            return False
        site = {"domain": domain, "v": 1}
        sites.append(site)

    # 合并策略：并集合并，版本 +1；navAllow 非空才覆盖（避免单次误判关掉闸门）
    site["hosts"] = sorted(set(site.get("hosts", [])) | set(clean["hosts"]))[:MAX_HOSTS_PER_SITE]
    site["rules"] = sorted(set(site.get("rules", [])) | set(clean["rules"]))
    site["cosmetic"] = sorted(set(site.get("cosmetic", [])) | set(clean["cosmetic"]))[:MAX_COSMETIC_PER_SITE]
    if clean["navAllow"]:
        site["navAllow"] = clean["navAllow"]
    site["v"] = int(site.get("v", 1)) + 1
    return True


def download_current_rules(cfg) -> dict:
    url = f"{cfg['bridge_base']}/site-rules.json"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return {"version": 1, "updated": "1970-01-01T00:00:00Z", "sites": []}


def upload_rules(cfg, rules_doc: dict) -> bool:
    import datetime
    rules_doc["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = json.dumps(rules_doc, ensure_ascii=False).encode("utf-8")
    url = f"{cfg['bridge_base']}/upload-rules.php?secret={cfg['bridge_secret']}"
    result = http_json(url, payload=body, headers={"Content-Type": "application/json"})
    ok = result.get("ok", False)
    if ok:
        print(f"[pipeline] 已发布 {result.get('sites')} 个站点的规则")
    return bool(ok)


def run_once(cfg) -> None:
    items = queue_list(cfg)
    if not items:
        print("[pipeline] 队列为空，无事可做")
        return
    print(f"[pipeline] 待处理反馈 {len(items)} 条")

    changed = False
    rules_doc = download_current_rules(cfg)

    for name in items:
        try:
            feedback = queue_fetch(cfg, name)
        except Exception as exc:
            print(f"[pipeline] WARN: 拉取失败 {name}: {exc}")
            continue
        try:
            if analyze_and_merge(feedback, rules_doc):
                changed = True
                print(f"[pipeline] {feedback.get('host')} 规则已更新")
            else:
                print(f"[pipeline] {feedback.get('host')} 未产生新规则")
        except Exception as exc:
            print(f"[pipeline] WARN: 分析失败 {name}: {exc}")
            continue  # 分析失败的条目保留在队列，下轮重试
        queue_delete(cfg, name)

    if changed:
        if not upload_rules(cfg, rules_doc):
            print("[pipeline] ERROR: 上传规则失败，下一轮重试（队列已清，规则未发布）")
    else:
        print("[pipeline] 无规则变化，不上传")


CFG = None

def main() -> None:
    global CFG
    CFG = load_config()
    if "--loop" in sys.argv:
        try:
            interval = int(sys.argv[sys.argv.index("--loop") + 1])
        except (ValueError, IndexError):
            interval = 1800
        print(f"[pipeline] 常驻模式，每 {interval} 秒一轮")
        while True:
            try:
                run_once(CFG)
            except Exception as exc:
                print(f"[pipeline] ERROR: {exc}")
            time.sleep(interval)
    else:
        run_once(CFG)


if __name__ == "__main__":
    main()
