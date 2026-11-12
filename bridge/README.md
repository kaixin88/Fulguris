# 社区桥（免费 PHP 空间）部署与联调

## 目录

```
bridge/
├── php/                 # 上传到 PHP 空间的全部文件（放同一目录，如 /bridge）
│   ├── config.php           ← 先改密钥再上传
│   ├── feedback.php         # 浏览器反馈入口（匿名 POST）
│   ├── pull-queue.php       # NAS 拉取/删除反馈（密钥鉴权）
│   ├── upload-rules.php     # NAS 发布 site-rules.json（密钥鉴权）
│   ├── site-rules.sample.json
│   └── .htaccess            # 保护 config.php 与 pending/（nginx 见下方）
└── nas/                 # 跑在 NAS 上
    ├── pipeline.py          # 轮询→AI分析→校验→发布，仅标准库
    └── config.sample.ini     ← 复制为 config.ini 填好
```

## 部署步骤

1. **PHP 空间**：把 `php/` 下所有文件传到空间某个目录（设为 `https://你的空间/bridge`）。
   - 先改 `config.php` 里的 `BRIDGE_SECRET`（长随机串）
   - `site-rules.json` 初始内容 = `site-rules.sample.json`（或空文档 `{"version":1,"sites":[]}`）
   - Apache 直接用自带 `.htaccess`；nginx 在 server 段加：
     ```
     location ~ ^/bridge/(config\.php|pending/) { return 403; }
     ```
2. **NAS**：
   ```bash
   cp config.sample.ini config.ini   # 填 base_url / secret / LLM key
   python3 pipeline.py               # 手动跑一轮验证
   crontab -e                        # 之后定时跑，例如每 30 分钟
   */30 * * * * cd /path/to/bridge/nas && python3 pipeline.py
   ```
3. **浏览器**（设置 → 广告拦截 → AI 社区规则）：
   - 规则地址：`https://你的空间/bridge/site-rules.json`
   - 反馈接口：`https://你的空间/bridge/feedback.php`
   - 网页菜单里会出现「AI 清理此站点」

## 数据流

```
用户点「AI 清理此站点」
   → 脱敏 DOM 快照 + 请求记录 POST feedback.php   （匿名、限流 10条/小时/IP）
   → NAS pipeline.py 定时拉取 → LLM 行为形态分析
   → 白名单校验（规则枚举/域名正则/选择器/数量上限）→ 按 domain 合并
   → upload-rules.php 原子发布 site-rules.json
   → 所有客户端拉取生效（规则地址可配，也支持 ETag 304 省流量）
```

## 网络层 ABP 规则（可选）

NAS 分析出纯 host 拦截规则时，也可以生成一份 ABP 语法清单存为 `community.txt`，
用户在「广告拦截 → 过滤器」手动添加该 URL 即获得独立开关与自动更新。

## 安全要点

- `config.php` 与 `pending/` 已拒直连；`BRIDGE_SECRET` 泄露立即换新
- feedback.php 只收结构化 JSON、单条 256KB 上限、每 IP 每小时 10 条
- AI 输出经白名单清洗，臆造域名/非法选择器直接丢弃，宁缺勿滥
- 反馈不含页面正文；DOM 快照只有 tag/id/class/几何/透明度
