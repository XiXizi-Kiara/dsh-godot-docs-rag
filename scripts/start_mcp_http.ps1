# 启动 Godot RAG MCP 服务（streamable-http，常驻）
# 用法：右键「使用 PowerShell 运行」，或在终端执行；保持窗口打开。
# 可选参数：-Root <项目根目录>  -Py <python 解释器>
#   Root 默认取脚本上一级；Py 默认取 $env:GODOT_RAG_PYTHON，其次 PATH 中的 python。
# 服务端点：http://127.0.0.1:8766/mcp
# 停止：Ctrl+C
param(
    [string]$Root = (Split-Path $PSScriptRoot -Parent),
    [string]$Py = $(if ($env:GODOT_RAG_PYTHON) { $env:GODOT_RAG_PYTHON } else { 'python' })
)

$ErrorActionPreference = "Stop"

Set-Location $Root
$env:PYTHONPATH = Join-Path $Root "src"

Write-Host "Godot RAG MCP 服务启动中..." -ForegroundColor Cyan
Write-Host "  Python : $Py"
Write-Host "  索引   : $Root\data\index"
Write-Host "  端点   : http://127.0.0.1:8766/mcp"
Write-Host "  Ctrl+C 停止" -ForegroundColor Yellow
Write-Host ""

& $Py -m godot_rag.mcp_server --transport streamable-http --port 8766
