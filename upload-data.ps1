param(
    [string]$Server = "",
    [string]$RemoteDir = "/root/Limit-Up-Sniper-QT",
    [string]$SourceData = "E:\Privy\Limit-Up-Sniper-Commercial\backend\data"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Fail($Text) {
    Write-Host ""
    Write-Host "错误：$Text" -ForegroundColor Red
    exit 1
}

if (-not $Server) {
    $Server = Read-Host "请输入服务器，例如 root@1.2.3.4"
}

if (-not $Server) {
    Fail "没有服务器地址。示例：.\upload-data.ps1 -Server root@1.2.3.4"
}

if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Fail "当前电脑找不到 scp。请先安装 OpenSSH Client，或用 Windows 11 自带终端。"
}

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Fail "当前电脑找不到 ssh。请先安装 OpenSSH Client，或用 Windows 11 自带终端。"
}

if (-not (Test-Path -LiteralPath $SourceData)) {
    Fail "找不到源数据目录：$SourceData"
}

Step "整理旧数据进当前项目 SQLite"
python scripts\migrate_data_to_sqlite.py --source $SourceData --db backend\data\quant_data.sqlite3
if ($LASTEXITCODE -ne 0) {
    Fail "SQLite 迁移失败"
}

Step "生成不含账号密钥的安全数据包"
$packageOutput = python scripts\package_safe_data_export.py --source $SourceData --db backend\data\quant_data.sqlite3
if ($LASTEXITCODE -ne 0) {
    Fail "数据包生成失败"
}
$packageLine = $packageOutput | Where-Object { $_ -like "安全数据包:*" } | Select-Object -Last 1
if (-not $packageLine) {
    Fail "没有读到数据包路径"
}
$packagePath = ($packageLine -replace "^安全数据包:\s*", "").Trim()
if (-not (Test-Path -LiteralPath $packagePath)) {
    Fail "数据包不存在：$packagePath"
}
Write-Host $packageOutput

Step "上传数据包到服务器"
$remotePackage = "/root/" + (Split-Path -Leaf $packagePath)
scp $packagePath "${Server}:${remotePackage}"
if ($LASTEXITCODE -ne 0) {
    Fail "上传失败，请检查服务器地址、密码或 SSH 密钥"
}

Step "服务器备份、解压、重启、检查"
$remoteScript = "set -e; cd '$RemoteDir'; bash qt.sh backup; tar -xzf '$remotePackage' -C '$RemoteDir'; bash qt.sh restart; python scripts/check_data_coverage.py; ls -lh backend/data/quant_data.sqlite3"
ssh $Server $remoteScript
if ($LASTEXITCODE -ne 0) {
    Fail "服务器执行失败，请检查远端目录是否正确：$RemoteDir"
}

Step "完成"
Write-Host "数据已上传并重启服务。"
