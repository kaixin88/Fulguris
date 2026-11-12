<?php
/**
 * upload-rules.php - NAS 发布 site-rules.json 到社区
 *
 * POST upload-rules.php?secret=...
 * Body: 新的 site-rules.json 全量内容（Content-Type: application/json）
 * 原子写入（tmp + rename），失败返回 500，旧版本保留。
 */

require_once __DIR__ . '/config.php';

header('Content-Type: application/json; charset=utf-8');

function json_out(array $payload, int $code = 200): void {
    http_response_code($code);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

$secret = $_GET['secret'] ?? '';
if (!hash_equals(BRIDGE_SECRET, (string)$secret)) {
    json_out(['ok' => false, 'error' => 'unauthorized'], 401);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_out(['ok' => false, 'error' => 'POST only'], 405);
}

$raw = file_get_contents('php://input');
if ($raw === false || strlen($raw) === 0 || strlen($raw) > 4 * 1024 * 1024) {
    json_out(['ok' => false, 'error' => 'bad body'], 400);
}

// 必须是合法的规则文档
$data = json_decode($raw, true);
if (!is_array($data) || !isset($data['sites']) || !is_array($data['sites'])) {
    json_out(['ok' => false, 'error' => 'invalid rules document'], 400);
}

$target = __DIR__ . '/site-rules.json';
$tmp = $target . '.tmp';
if (@file_put_contents($tmp, $raw, LOCK_EX) === false) {
    json_out(['ok' => false, 'error' => 'write failed'], 500);
}
if (!@rename($tmp, $target)) {
    @unlink($tmp);
    json_out(['ok' => false, 'error' => 'rename failed'], 500);
}

json_out(['ok' => true, 'sites' => count($data['sites'])]);
