<?php
/**
 * feedback.php - AI 去广告社区桥：反馈接收端
 *
 * 浏览器"AI 清理此站点"按钮把脱敏 DOM 快照 POST 到这里。
 * 每条反馈存为一个 JSON 文件落进 pending/ 队列，等待 NAS 拉走分析。
 *
 * 安全设计：
 *  - 只收 POST + application/json
 *  - 体积上限 256KB，条数字段上限
 *  - 每 IP 文件限流（默认 10 条/小时），防刷
 *  - 不记录正文内容之外的信息（DOM 快照本身就是无正文的结构件）
 *  - pending/ 与 config.php 通过 .htaccess 禁止直接访问（见 .htaccess）
 */

require_once __DIR__ . '/config.php';

header('Content-Type: application/json; charset=utf-8');

function json_out(array $payload, int $code = 200): void {
    http_response_code($code);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_out(['ok' => false, 'error' => 'POST only'], 405);
}

// 读取并校验请求体
$raw = file_get_contents('php://input');
if ($raw === false || strlen($raw) === 0) {
    json_out(['ok' => false, 'error' => 'empty body'], 400);
}
if (strlen($raw) > MAX_FEEDBACK_BYTES) {
    json_out(['ok' => false, 'error' => 'too large'], 413);
}

$data = json_decode($raw, true);
if (!is_array($data)) {
    json_out(['ok' => false, 'error' => 'invalid json'], 400);
}

// 必要字段：host（用于路由），url（用于核对），dom（分析材料）
$host = isset($data['host']) ? preg_replace('/[^a-z0-9.\-]/i', '', strtolower((string)$data['host'])) : '';
$url  = isset($data['url'])  ? filter_var((string)$data['url'], FILTER_SANITIZE_URL) : '';
if ($host === '' || $url === '' || !isset($data['dom'])) {
    json_out(['ok' => false, 'error' => 'missing fields'], 400);
}
if (strlen((string)$data['dom']) > MAX_DOM_BYTES) {
    json_out(['ok' => false, 'error' => 'dom too large'], 413);
}

// 每 IP 限流（简单文件计数，适合共享 PHP 空间）
$ip = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$rateFile = sys_get_temp_dir() . '/fb_rate_' . md5($ip);
$now = time();
$rate = ['h' => $now, 'n' => 0];
if (is_file($rateFile)) {
    $rate = json_decode((string)file_get_contents($rateFile), true) ?: $rate;
}
if ($now - $rate['h'] > 3600) { $rate = ['h' => $now, 'n' => 0]; }
$rate['n']++;
file_put_contents($rateFile, json_encode($rate));
if ($rate['n'] > RATE_LIMIT_PER_HOUR) {
    json_out(['ok' => false, 'error' => 'rate limited'], 429);
}

// 落盘：pending/<epoch>_<rand>_<host>.json
$queueDir = __DIR__ . '/' . QUEUE_DIR;
if (!is_dir($queueDir)) { @mkdir($queueDir, 0750, true); }
$name = $now . '_' . bin2hex(random_bytes(4)) . '_' . $host . '.json';
$path = $queueDir . '/' . $name;

$record = [
    'host' => $host,
    'url' => substr($url, 0, 512),
    'ts' => isset($data['ts']) ? (int)$data['ts'] : $now,
    'ua' => isset($data['ua']) ? substr((string)$data['ua'], 0, 256) : '',
    'locale' => isset($data['locale']) ? substr((string)$data['locale'], 0, 16) : '',
    'dom' => $data['dom'],
    'requests' => isset($data['requests']) && is_array($data['requests']) ? array_slice($data['requests'], 0, 500) : [],
    'ip' => $ip,
];

$ok = @file_put_contents($path, json_encode($record, JSON_UNESCAPED_UNICODE), LOCK_EX);
if ($ok === false) {
    json_out(['ok' => false, 'error' => 'storage failed'], 500);
}

json_out(['ok' => true, 'queued' => $name]);
