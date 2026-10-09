# sync_to_skill.ps1 — 把本地 demo 资源同步到 cat-care-conversational-agent 技能包
#
# 默认行为：
#   1. 拷贝 data/ 下的权威文件 + 兜底文件 → 技能包 references/
#   2. 在 SKILL.md 末尾追加「本地 Demo 与知识库位置」一节（不动原 8 原则；通过 Python 写 UTF-8）
#   3. 拷贝 docs/api-contract.md → 技能包 templates/
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File sync_to_skill.ps1
#
# 参数（可选）：
#   -SkillDir <path>     指定技能包目录
#   -DryRun              仅打印，不复制

param(
    [string]$SkillDir = "C:\Users\86147\.workbuddy\skills\cat-care-conversational-agent",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$DemoRoot  = (Resolve-Path (Join-Path $ScriptDir ".")).Path
$AppendPy  = Join-Path $ScriptDir "scripts\sync_append_skill.py"

Write-Host "== 同步开始 =="
Write-Host "  Demo 目录: $DemoRoot"
Write-Host "  技能包目录: $SkillDir"
Write-Host ""

if (-not (Test-Path $SkillDir)) {
    Write-Error "Skill package directory missing: $SkillDir"
    exit 1
}
$RefDir   = Join-Path $SkillDir "references"
$TplDir   = Join-Path $SkillDir "templates"
$SkillMd  = Join-Path $SkillDir "SKILL.md"

if (-not (Test-Path $SkillMd)) {
    Write-Error "SKILL.md missing: $SkillMd"
    exit 1
}
if (-not (Test-Path $RefDir)) {
    New-Item -ItemType Directory -Path $RefDir | Out-Null
}

# 1) 拷贝权威数据 → references/
$sources = @(
    @{ Src = "data\knowledge_base_v1.jsonl";     Dst = "knowledge_base_v1.jsonl" },
    @{ Src = "data\source_facts.json";           Dst = "source_facts.json" },
    @{ Src = "data\source-registry.md";          Dst = "source-registry.md" },
    @{ Src = "data\fallback_entries.jsonl";      Dst = "fallback_entries.jsonl" }
)
foreach ($pair in $sources) {
    $src = Join-Path $DemoRoot $pair.Src
    $dst = Join-Path $RefDir $pair.Dst
    if (-not (Test-Path $src)) {
        Write-Warning "Source missing, skip: $src"
        continue
    }
    if ($DryRun) {
        Write-Host "[DRY] copy $src -> $dst"
    } else {
        Copy-Item -Path $src -Destination $dst -Force
        Write-Host "[OK]   copy $($pair.Src) -> references/$($pair.Dst)"
    }
}

# 2) 调 Python 写 SKILL.md（避免 PS 5.1 here-string 中文的 ANSI 陷阱）
if ($DryRun) {
    Write-Host "[DRY] invoke $AppendPy $SkillMd"
} else {
    & python $AppendPy $SkillMd
    Write-Host "[OK]   append SKILL.md marker (via Python)"
}

# 3) 拷贝 docs/api-contract.md → templates/
$apiContract = Join-Path $DemoRoot "docs\api-contract.md"
if (Test-Path $apiContract) {
    $dst = Join-Path $TplDir "api-contract.md"
    if ($DryRun) {
        Write-Host "[DRY] copy $apiContract -> $dst"
    } else {
        Copy-Item -Path $apiContract -Destination $dst -Force
        Write-Host "[OK]   copy docs/api-contract.md -> templates/api-contract.md"
    }
}

Write-Host ""
Write-Host "== Sync done =="
