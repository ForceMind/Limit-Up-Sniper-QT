param(
  [string]$HostAddress = "127.0.0.1",
  [int]$Port = 8000,
  [switch]$Reload,
  [switch]$SkipInstall,
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
  # Older PowerShell hosts may not allow changing output encoding.
}

$ScriptPath = $PSCommandPath
if (-not $ScriptPath) {
  $ScriptPath = $MyInvocation.MyCommand.Path
}
if ($ScriptPath) {
  $RootDir = Split-Path -Parent $ScriptPath
} else {
  $RootDir = (Get-Location).Path
}
$BackendDir = Join-Path $RootDir "backend"
$VenvDir = Join-Path $RootDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsFile = Join-Path $BackendDir "requirements.txt"
$EnvExampleFile = Join-Path $RootDir ".env.example"
$EnvFile = Join-Path $RootDir ".env"

function Write-Step {
  param([string]$Message)
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
  param([string]$Message)
  Write-Host "√ $Message" -ForegroundColor Green
}

function Write-WarnCn {
  param([string]$Message)
  Write-Host "! $Message" -ForegroundColor Yellow
}

function Write-FailCn {
  param([string]$Message)
  Write-Host "× $Message" -ForegroundColor Red
}

function Get-PythonCommand {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    try {
      $version = & $python.Source --version 2>&1
      if ($LASTEXITCODE -eq 0) {
        return [pscustomobject]@{
          File = $python.Source
          Args = @()
          Version = "$version"
        }
      }
    } catch {
    }
  }

  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    try {
      $version = & $py.Source -3 --version 2>&1
      if ($LASTEXITCODE -eq 0) {
        return [pscustomobject]@{
          File = $py.Source
          Args = @("-3")
          Version = "$version"
        }
      }
    } catch {
    }
  }

  throw "未找到可用的 Python。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。"
}

function Invoke-SystemPython {
  param(
    [Parameter(Mandatory = $true)][object]$Python,
    [Parameter(Mandatory = $true)][string[]]$Arguments
  )
  $allArgs = @()
  $allArgs += $Python.Args
  $allArgs += $Arguments
  & $Python.File @allArgs
}

function Import-DotEnv {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return
  }

  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith("#")) {
      continue
    }

    $parts = $text -split "=", 2
    if ($parts.Count -ne 2) {
      continue
    }

    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (-not $name) {
      continue
    }

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    Set-Item -Path "Env:$name" -Value $value
  }
}

function Test-PortAvailable {
  param(
    [string]$Address,
    [int]$TestPort
  )

  if ($Address -eq "0.0.0.0") {
    $ip = [System.Net.IPAddress]::Any
  } elseif ($Address -eq "localhost") {
    $ip = [System.Net.IPAddress]::Loopback
  } else {
    try {
      $ip = [System.Net.IPAddress]::Parse($Address)
    } catch {
      $ip = [System.Net.IPAddress]::Loopback
    }
  }

  $listener = [System.Net.Sockets.TcpListener]::new($ip, $TestPort)
  try {
    $listener.Start()
    return $true
  } catch {
    return $false
  } finally {
    try {
      $listener.Stop()
    } catch {
    }
  }
}

function Resolve-Port {
  param(
    [string]$Address,
    [int]$PreferredPort
  )

  for ($candidate = $PreferredPort; $candidate -lt ($PreferredPort + 20); $candidate++) {
    if (Test-PortAvailable -Address $Address -TestPort $candidate) {
      return $candidate
    }
  }

  throw "端口 $PreferredPort 到 $($PreferredPort + 19) 都被占用，请关闭占用进程或用 -Port 指定其他端口。"
}

function Get-AdminEntryPathFromConfig {
  $configFile = Join-Path $BackendDir "data\config.json"
  if (-not (Test-Path $configFile)) {
    return ""
  }

  try {
    $raw = Get-Content -Path $configFile -Raw -Encoding UTF8
    $match = [regex]::Match($raw, '"admin_entry_path"\s*:\s*"([^"]+)"')
    if ($match.Success) {
      return $match.Groups[1].Value
    }
  } catch {
  }

  return ""
}

try {
  Write-Host "涨停狙击手 - 本地一键启动" -ForegroundColor White
  Write-Host "项目目录：$RootDir"

  if (-not (Test-Path $BackendDir)) {
    throw "找不到 backend 目录，请确认脚本放在项目根目录。"
  }

  Write-Step "检查 Python 环境"
  $systemPython = Get-PythonCommand
  Write-Ok "检测到 $($systemPython.Version)"

  if (-not (Test-Path $VenvPython)) {
    Write-Step "创建虚拟环境 .venv"
    Invoke-SystemPython -Python $systemPython -Arguments @("-m", "venv", $VenvDir)
    Write-Ok "虚拟环境已创建"
  } else {
    Write-Ok "已存在虚拟环境 .venv"
  }

  Write-Step "准备配置文件"
  if (-not (Test-Path $EnvFile)) {
    if (-not (Test-Path $EnvExampleFile)) {
      throw "找不到 .env.example，无法生成 .env。"
    }
    Copy-Item -Path $EnvExampleFile -Destination $EnvFile
    Write-Ok "已从 .env.example 生成 .env"
  } else {
    Write-Ok "已存在 .env，保持当前配置"
  }

  Import-DotEnv -Path $EnvFile

  if (-not $SkipInstall) {
    Write-Step "安装或更新 Python 依赖"
    if (-not (Test-Path $RequirementsFile)) {
      throw "找不到依赖文件：$RequirementsFile"
    }
    & $VenvPython -m pip install -r $RequirementsFile
    if ($LASTEXITCODE -ne 0) {
      throw "依赖安装失败，请检查网络或 pip 输出。"
    }
    Write-Ok "依赖已就绪"
  } else {
    Write-WarnCn "已跳过依赖安装"
  }

  $effectivePort = Resolve-Port -Address $HostAddress -PreferredPort $Port
  if ($effectivePort -ne $Port) {
    Write-WarnCn "端口 $Port 已被占用，自动改用 $effectivePort"
  }

  $env:QUANT_HOST = $HostAddress
  $env:QUANT_PORT = "$effectivePort"

  $adminPath = Get-AdminEntryPathFromConfig
  $baseUrl = "http://127.0.0.1:$effectivePort"

  Write-Step "启动信息"
  Write-Host "前台地址：$baseUrl"
  Write-Host "接口文档：$baseUrl/docs"
  if ($adminPath) {
    Write-Host "后台地址：$baseUrl$adminPath"
  } else {
    Write-Host "后台地址：启动后用 bash qt.sh admin-path 查看"
  }
  Write-Host "停止服务：在这个窗口按 Ctrl + C"

  if ($CheckOnly) {
    Write-Ok "检查完成，未启动服务"
    exit 0
  }

  Write-Step "启动 FastAPI 服务"
  $uvicornArgs = @("app.main:app", "--host", $HostAddress, "--port", "$effectivePort")
  if ($Reload) {
    $uvicornArgs += "--reload"
  }

  Push-Location $BackendDir
  try {
    & $VenvPython -m uvicorn @uvicornArgs
  } finally {
    Pop-Location
  }
} catch {
  Write-FailCn $_.Exception.Message
  Write-Host ""
  Write-Host "常见处理："
  Write-Host "1. 确认已安装 Python 3.10 或更高版本。"
  Write-Host "2. 如果依赖安装失败，检查网络后重新运行。"
  Write-Host "3. 如果端口被占用，可以运行：.\start-local.ps1 -Port 8010"
  exit 1
}
