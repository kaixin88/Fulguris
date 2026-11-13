#!/usr/bin/env python3
"""
把 PHP 桥部署到免费主机（FTP）。

用法：
    cp deploy.local.ini.sample deploy.local.ini   # 填 FTP 与密钥
    python deploy.py

做的事：
  1. 读 deploy.local.ini
  2. 用 config.sample.php 生成 config.php（填入密钥/数据库口令）
  3. 通过 FTP 上传到网站根下的 bridge/ 目录
  4. 逐个回读校验大小

只用标准库。
"""

import configparser
import ftplib
import io
import sys
from pathlib import Path

HERE = Path(__file__).parent
PHP_DIR = HERE.parent / "php"
LOCAL_INI = HERE / "deploy.local.ini"

# 需要上传的文件：仓库文件 -> 站点文件名
UPLOADS = [
    ("feedback.php", "feedback.php"),
    ("pull-queue.php", "pull-queue.php"),
    ("upload-rules.php", "upload-rules.php"),
    ("site-rules.sample.json", "site-rules.json"),
    (".htaccess", ".htaccess"),
]


def die(msg: str) -> None:
    print(f"[deploy] FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load():
    if not LOCAL_INI.exists():
        die(f"缺少 {LOCAL_INI}（复制 deploy.local.ini.sample 并填写）")
    c = configparser.ConfigParser()
    c.read(LOCAL_INI, encoding="utf-8")
    return {
        "host": c.get("ftp", "host"),
        "port": c.getint("ftp", "port", fallback=21),
        "user": c.get("ftp", "user"),
        "password": c.get("ftp", "password"),
        "remote_dir": c.get("ftp", "remote_dir").rstrip("/"),
        "secret": c.get("bridge", "secret"),
        "db_pass": c.get("bridge", "db_pass"),
    }


def build_config(cfg) -> bytes:
    sample = (PHP_DIR / "config.sample.php").read_text(encoding="utf-8")
    out = sample.replace("CHANGE_ME_TO_A_LONG_RANDOM_STRING", cfg["secret"])
    out = out.replace("CHANGE_ME'", cfg["db_pass"] + "'")
    if "CHANGE_ME" in out:
        die("config.php 仍有未替换的 CHANGE_ME 占位符")
    return out.encode("utf-8")


def connect(cfg) -> ftplib.FTP:
    ftp = ftplib.FTP()
    ftp.connect(cfg["host"], cfg["port"], timeout=40)
    ftp.login(cfg["user"], cfg["password"])
    ftp.set_pasv(True)
    return ftp


def ensure_dir(ftp: ftplib.FTP, path: str) -> None:
    """必要时逐级创建远端目录。"""
    parts = [p for p in path.split("/") if p]
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def upload(ftp: ftplib.FTP, name: str, data: bytes) -> None:
    ftp.storbinary(f"STOR {name}", io.BytesIO(data))
    size = ftp.size(name)
    if size != len(data):
        die(f"{name} 上传后大小不一致：远端 {size} 本地 {len(data)}")
    print(f"[deploy] OK {name} ({size} B)")


def main() -> None:
    cfg = load()
    ftp = connect(cfg)
    try:
        ensure_dir(ftp, cfg["remote_dir"])
        print(f"[deploy] cwd={ftp.pwd()}")
        ensure_dir(ftp, f"{cfg['remote_dir']}/pending")
        ftp.cwd(cfg["remote_dir"])

        upload(ftp, "config.php", build_config(cfg))
        for src, dst in UPLOADS:
            upload(ftp, dst, (PHP_DIR / src).read_bytes())

        print("[deploy] 远端文件：", sorted(n for n in ftp.nlst() if n not in (".", "..")))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


if __name__ == "__main__":
    main()
