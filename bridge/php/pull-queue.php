<?php
/**
 * pull-queue.php - NAS 拉取待分析反馈
 *
 * GET pull-queue.php?secret=...&max=20
 * 返回待处理反馈文件名列表。
 *
 * GET pull-queue.php?secret=...&fetch=<name>
 * 返回单条反馈的完整 JSON（拉取后由 NAS 侧自行删除：delete=<name>）。
 *
 * GET pull-queue.php?secret=...&delete=<name>
 * 分析完成后删除该条，防止重复处理。
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

$queueDir = __DIR__ . '/' . QUEUE_DIR;

$action = $_GET['fetch'] ?? ($_GET['delete'] ?? null);

if ($action === null) {
    // 列出待处理条目
    $max = min(50, max(1, (int)($_GET['max'] ?? 20)));
    $files = [];
    if (is_dir($queueDir)) {
        foreach (scandir($queueDir) as $f) {
            if (substr($f, -5) === '.json') {
                $files[] = $f;
                if (count($files) >= $max) break;
            }
        }
    }
    sort($files);
    json_out(['ok' => true, 'count' => count($files), 'items' => $files]);
}

// 校验文件名，防目录穿越
$name = basename((string)$action);
if (!preg_match('/^[a-z0-9_.\-]+\.json$/i', $name)) {
    json_out(['ok' => false, 'error' => 'bad name'], 400);
}
$path = $queueDir . '/' . $name;

if (isset($_GET['fetch'])) {
    if (!is_file($path)) { json_out(['ok' => false, 'error' => 'gone'], 404); }
    header('Content-Type: application/json; charset=utf-8');
    readfile($path);
    exit;
}

// delete
if (is_file($path)) { @unlink($path); }
json_out(['ok' => true]);
