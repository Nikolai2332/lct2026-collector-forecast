# Сборка сопроводительной документации одной командой (PowerShell):
#   powershell -ExecutionPolicy Bypass -File docs/submission/build.ps1
# Всё выполняется в одноразовом контейнере (образ collector-docs:1 из Dockerfile рядом); в Windows ничего не ставится.
# Перечень библиотек (deps/*.json) обновляется, если запущен контейнер api основного стека и есть окружение ml/.venv;
# иначе берутся сохранённые deps/*.json.
# Второй вариант со скриншотами реальных данных (срез июня 2026; файлы берутся из backups\, в git не попадают):
#   powershell -ExecutionPolicy Bypass -File docs/submission/build.ps1 -Real
param([switch]$Real)
$ErrorActionPreference = "Stop"
$sub = $PSScriptRoot
$root = (Resolve-Path (Join-Path $sub "..\..")).Path
$out = Join-Path $root "dist\submission"
New-Item -ItemType Directory -Force $out | Out-Null
$envArgs = @()
if ($Real) {
    $src = Join-Path $root "backups\prompt10_real\clean_slice"
    $dst = Join-Path $sub "build\real"
    New-Item -ItemType Directory -Force $dst | Out-Null
    foreach ($f in "01_dashboard", "04_channel", "03_objects_scheme", "05b_events", "07_quality") {
        Copy-Item (Join-Path $src "dispatcher_ods\1366x768\$f.png") $dst -Force
    }
    Copy-Item (Join-Path $src "admin\1366x768\08_users.png") $dst -Force
    $envArgs = @("-e", "DOC_SCREENS=real")
}
docker build -q -t collector-docs:1 $sub | Out-Null
Push-Location $root
try {
    $api = docker compose ps -q api 2>$null
    if ($api) {
        docker compose cp "$sub\gen_deps.py" api:/tmp/gen_deps.py | Out-Null
        docker compose exec -T api python /tmp/gen_deps.py inventory requirements.txt /tmp/backend.json | Out-Null
        docker compose cp api:/tmp/backend.json "$sub\deps\backend.json" | Out-Null
    } else { Write-Host "api не запущен — перечень бэкенда из deps/backend.json" }
    if (Test-Path "ml\.venv\Scripts\python.exe") {
        & "ml\.venv\Scripts\python.exe" "$sub\gen_deps.py" inventory "ml\requirements.txt" "$sub\deps\ml.json"
    }
    python "$sub\gen_deps.py" markdown "$sub\deps" "frontend\package-lock.json" "$sub\deps\deps.md"
} finally { Pop-Location }
docker run --rm @envArgs -v "${root}\docs:/docs" -v "${out}:/out" collector-docs:1 sh /docs/submission/build.sh
if ($LASTEXITCODE -ne 0) { throw "Сборка не удалась (код $LASTEXITCODE)" }
Write-Host "Готово: $out"
