<?php
/**
 * config.sample.php - 桥配置模板
 *
 * 部署时复制为 config.php 并填写真实值。config.php 不提交到仓库，
 * 且由 .htaccess 拒绝直接访问。
 */

// 服务器时区是 America/New_York，必须设为北京时间
date_default_timezone_set('Asia/Shanghai');

// 反馈体积上限（字节）
const MAX_FEEDBACK_BYTES = 256 * 1024;

// DOM 快照字符串上限（字节）
const MAX_DOM_BYTES = 192 * 1024;

// 每 IP 每小时最多反馈条数
const RATE_LIMIT_PER_HOUR = 10;

// 反馈队列目录（相对本文件），由 .htaccess 拒绝直接访问
const QUEUE_DIR = 'pending';

// NAS 下发/拉取密钥：长随机串，与 NAS 侧 config.ini 的 secret 一致
const BRIDGE_SECRET = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';

// 站点标识，用于社区页面与统计
const SITE_NAME = 'AdZero';
const SITE_BASE_URL = 'https://adzero.free.nf';

// MySQL（InfinityFree 免费库，仅服务器内部可连）
const DB_HOST = 'sql213.infinityfree.com';
const DB_PORT = 3306;
const DB_NAME = 'if0_42951426_adzero';
const DB_USER = 'if0_42951426';
const DB_PASS = 'CHANGE_ME';

// 上线后保持 false
const DEBUG = false;
