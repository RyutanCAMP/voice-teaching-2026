# 夜間の確かめ（11/7 FINAL_FREEZE_1700_AND_OVERNIGHT の 6章・シクロ）
# 固定した SHA で、テストを決まった順に1回ずつ流し、結果を output\overnight\<日時>\ に残す。無限のやり直しはしない。
# 実機・ファイル転送・外部公開・提出はしない。本物の Claude は評価の1回だけで、料金の上限つき（既定 1.5 ドル）。
# 上限は output\nl-eval\api_usage.jsonl の「累計」（11/7 昼までに約 0.89 ドル）。今夜使えるのは 上限−累計（105件で約 0.41 ドル）
#
#   使い方（release/v1.0-rc の作業フォルダで）：
#   powershell -ExecutionPolicy Bypass -File tools\overnight\run_checks.ps1 -ExpectSha <固定した SHA> -Python <venv の python.exe> [-EnvFile <.env>] [-LiveBudgetUsd 1.5] [-Rounds 1]
#
# 止まる条件（ここで止めて、それ以後は流さない）：
#   - 作業フォルダの SHA が -ExpectSha と違う・未コミットの変更がある
#   - pytest・Node・e2e・承認して保存の確かめのどれかが NG（ログを残して止める。直しは人かシクロが別に）
#   - 本物の Claude の評価で「誤った候補」が1件でも出た・API の失敗が3件以上・料金の上限に届いた
#   - -Rounds の回数を流し終えた（既定 1。2 以上は、時間に左右される e2e の再現を見るときだけ）
param(
  [Parameter(Mandatory = $true)][string]$ExpectSha,
  [Parameter(Mandatory = $true)][string]$Python,
  [string]$EnvFile = "",
  [double]$LiveBudgetUsd = 1.5,
  [int]$Rounds = 1,
  [string]$Node = "node"
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$root = (git rev-parse --show-toplevel).Trim()
Set-Location $root
$sha = (git rev-parse --short HEAD).Trim()
$dirty = (git status --porcelain --untracked-files=no)
if (-not $ExpectSha.StartsWith($sha) -and -not $sha.StartsWith($ExpectSha)) { Write-Output "STOP: SHA が違います（今 $sha・期待 $ExpectSha）"; exit 2 }
if ($dirty) { Write-Output "STOP: 未コミットの変更があります"; exit 2 }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = Join-Path $root "output\overnight\$stamp-$sha"
New-Item -ItemType Directory -Force $out | Out-Null
$summary = Join-Path $out "summary.txt"
"夜間の確かめ  SHA $sha  開始 $(Get-Date -Format s)" | Out-File -Encoding utf8 $summary

function Step($name, [scriptblock]$body) {
  "--- $name 開始 $(Get-Date -Format s)" | Out-File -Append -Encoding utf8 $summary
  $ok = & $body
  "--- $name 終了 $(Get-Date -Format s)  結果 $ok" | Out-File -Append -Encoding utf8 $summary
  if (-not $ok) { "STOP: $name が NG。ここで止めます" | Out-File -Append -Encoding utf8 $summary; Write-Output "STOP: $name"; exit 1 }
}

for ($r = 1; $r -le $Rounds; $r++) {
  Step "pytest（$r）" {
    & $Python -m pytest -q -p no:cacheprovider --ignore=tests/test_e2e_browser.py *> (Join-Path $out "pytest_$r.log")
    $LASTEXITCODE -eq 0
  }
  Step "Node（$r）" {
    & $Node --test tests/*.mjs *> (Join-Path $out "node_$r.log")
    $LASTEXITCODE -eq 0
  }
  Step "承認して保存の確かめ（$r）" {
    & $Python tools\check_approve_save.py *> (Join-Path $out "approve_save_$r.log")
    $LASTEXITCODE -eq 0
  }
  Step "e2e（$r）" {
    & $Python tools\e2e_browser.py *> (Join-Path $out "e2e_$r.log")
    if ($LASTEXITCODE -ne 0) {
      # Playwright の「Response has been disposed」（ツールの不調）なら1回だけ流し直す。アプリの NG なら止める
      $log = Get-Content (Join-Path $out "e2e_$r.log") -Raw -Encoding utf8
      if ($log -match "has been disposed" -and $log -notmatch "(?m)^NG ") {
        & $Python tools\e2e_browser.py *> (Join-Path $out "e2e_${r}_retry.log")
        return ($LASTEXITCODE -eq 0)
      }
      return $false
    }
    $true
  }
  Step "自然言語の評価・モック（$r）" {
    & $Python tools\nl_eval\run_eval.py --mode mock --cases tools/nl_eval/cases_v2.json --out (Join-Path $out "nl_mock_$r") *> (Join-Path $out "nl_mock_$r.log")
    $s = Get-Content (Join-Path $out "nl_mock_$r\summary.md") -Raw -Encoding utf8
    ($LASTEXITCODE -eq 0) -and ($s -match "\*\*誤った候補 0\*\*")
  }
}

if ($EnvFile -ne "") {
  Step "自然言語の評価・本物の Claude（1回・上限 $LiveBudgetUsd ドル）" {
    & $Python tools\nl_eval\run_eval.py --mode live --env-file $EnvFile --cases tools/nl_eval/cases_v2.json --budget-usd $LiveBudgetUsd --out (Join-Path $out "nl_live") *> (Join-Path $out "nl_live.log")
    $s = Get-Content (Join-Path $out "nl_live\summary.md") -Raw -Encoding utf8
    $api = [int]([regex]::Match($s, "API の失敗 (\d+)").Groups[1].Value)
    ($LASTEXITCODE -eq 0) -and ($s -match "\*\*誤った候補 0\*\*") -and ($api -lt 3)
  }
}
"夜間の確かめ  終了 $(Get-Date -Format s)  すべて通りました" | Out-File -Append -Encoding utf8 $summary
Write-Output "DONE: $out"
