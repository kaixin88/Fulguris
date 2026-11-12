<?php
/**
 * config.php - 桥配置（上传到 PHP 空间后先改这里）
 *
 * 注意：本文件包含密钥，不要提交到公开仓库；.htaccess 已拒绝对其直接访问。
 */

// 反馈体积上限（字节）
define('MAX_FEEDBACK_BYTES', 256 * 1024);

// DOM 快照字符串上限（字节）
define('MAX_DOM_BYTES', 192 * 1024);

// 每 IP 每小时最多反馈条数
define('RATE_LIMIT_PER_HOUR', 10);

// 反馈队列目录（相对本文件），NAS 通过 pull-queue.php 用密钥拉取
define('QUEUE_DIR', 'pending');

// NAS 下发/拉取密钥：改成你自己的长随机串（NAS 侧脚本填同一个值）
define('BRIDGE_SECRET', 'CHANGE_ME_TO_A_LONG_RANDOM_STRING');
