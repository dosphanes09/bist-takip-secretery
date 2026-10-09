<#
.SYNOPSIS
    BIST Takip - tek tıkla başlatıcı.

.DESCRIPTION
    İlk çalıştırmada kurulumu kendisi yapar (sanal ortam + paketler + .env),
    sonraki çalıştırmalarda doğrudan uygulamayı başlatır ve tarayıcıyı açar.

    Adımlar:
      1. Python'u bul (py launcher ya da python)
      2. .venv yoksa oluştur
      3. Paketler eksikse kur
      4. .env yoksa .env.example'dan kopyala ve Not Defteri'nde aç
      5. Web arayüzü + zamanlayıcıyı başlat
      6. Sunucu ayağa kalkınca tarayıcıyı aç

.PARAMETER RunNow
    Arayüzü açmak yerine raporu bir kez üretip e-posta gönderir ve çıkar.

.PARAMETER DryRun
    -RunNow ile birlikte: e-posta göndermez, HTML önizleme üretir.
#>

[CmdletBinding()]
param(
    [switch]$RunNow,
    [switch]$DryRun
)

# UTF-8 çıktı — Türkçe karakterler düzgün görünsün
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Proje kökü: bu betik scripts\ altında olduğu için bir üst dizin
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Host.UI.RawUI.WindowTitle = "BIST Takip"

function Write-Step    { param($m) Write-Host "  → $m" -ForegroundColor Cyan }
function Write-Ok      { param($m) Write-Host "  ✓ $m" -ForegroundColor Green }
function Write-Warn    { param($m) Write-Host "  ! $m" -ForegroundColor Yellow }
function Write-Err     { param($m) Write-Host "  ✗ $m" -ForegroundColor Red }

function Wait-ForKey {
    <#
        Kullanıcının pencereyi okumasına fırsat verir.

        RawUI.ReadKey konsol yoksa ya da girdi yönlendirilmişse istisna
        fırlatır (zamanlanmış görevden çağrıldığında olur). Bu yüzden
        kademeli olarak geri çekiliyoruz: tuş → satır → kısa bekleme.
    #>
    param([string]$Message = "Kapatmak için bir tuşa bas…")

    Write-Host ""
    Write-Host "  $Message" -ForegroundColor DarkGray
    try {
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    } catch {
        try { $null = Read-Host } catch { Start-Sleep -Seconds 5 }
    }
}

Write-Host ""
Write-Host "  BIST TAKİP" -ForegroundColor White
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Python + sanal ortam
# ---------------------------------------------------------------------------
# Sanal ortamın VAR olması yetmez, ÇALIŞIYOR olması gerekir.
#
# .venv\Scripts\python.exe gerçek bir Python değildir; .venv\pyvenv.cfg
# içindeki "home = ..." satırında yazan asıl Python'a yönlendiren ince bir
# köprüdür. O asıl Python silinir, taşınır ya da sürüm yükseltmesiyle klasörü
# değişirse köprü kırılır ve şu hatayı verir:
#     No Python at '"C:\...\Python312\python.exe'
# Aynı şey proje klasörünün adı/yeri değiştiğinde de olur; Windows'ta sanal
# ortamlar taşınabilir değildir.
#
# Bu yüzden artık "dosya duruyor mu" diye değil, "çalışıyor mu" diye soruyoruz;
# çalışmıyorsa bozuk ortamı silip sıfırdan kuruyoruz.
$VenvDir    = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

function Test-VenvHealthy {
    <#
        Sanal ortamın gerçekten çalışıp çalışmadığını sınar.
        Kırık bir köprü hem sıfırdan farklı bir çıkış kodu döndürür hem de
        beklenen kelimeyi yazdıramaz; iki şartı birden arıyoruz.
    #>
    param([string]$PythonExe)

    if (-not (Test-Path $PythonExe)) { return $false }
    try {
        $probe = & $PythonExe -c "print('VENV_OK')" 2>&1 | Out-String
        return (($LASTEXITCODE -eq 0) -and ($probe -match 'VENV_OK'))
    } catch {
        return $false
    }
}

function Find-BasePython {
    <#
        Sistemde kurulu Python 3.10+ arar.
        Önce `py` (Windows Python Launcher), sonra PATH'teki `python`.
    #>
    foreach ($candidate in @("py", "python")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $verText = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($verText -match '^(\d+)\.(\d+)$') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -eq 3 -and $minor -ge 10) { return $candidate }
            }
        } catch { }
    }
    return $null
}

if (-not (Test-VenvHealthy $VenvPython)) {

    if (Test-Path $VenvDir) {
        Write-Warn "Sanal ortam çalışmıyor — sıfırdan kuruluyor"
        Write-Host "    (Python taşınmış/silinmiş ya da proje klasörü yer değiştirmiş olabilir)" -ForegroundColor DarkGray
        try {
            Remove-Item $VenvDir -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Err "Eski sanal ortam silinemedi."
            Write-Host "  Uygulama hâlâ açık olabilir. Tüm 'BIST Takip' pencerelerini kapat," -ForegroundColor Yellow
            Write-Host "  sonra şu klasörü elle sil ve tekrar dene:" -ForegroundColor Yellow
            Write-Host "     $VenvDir" -ForegroundColor White
            exit 1
        }
    } else {
        Write-Step "İlk kurulum yapılıyor (yalnızca bir kez, birkaç dakika sürebilir)"
    }

    $BasePython = Find-BasePython
    if (-not $BasePython) {
        Write-Err "Python 3.10 veya üzeri bulunamadı."
        Write-Host ""
        Write-Host "  python.org/downloads adresinden Python kur ve kurulum" -ForegroundColor Yellow
        Write-Host "  sırasında 'Add Python to PATH' kutusunu işaretle." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
    Write-Ok "Python bulundu ($BasePython)"

    Write-Step "Sanal ortam oluşturuluyor…"
    & $BasePython -m venv $VenvDir

    if (-not (Test-VenvHealthy $VenvPython)) {
        Write-Err "Sanal ortam oluşturulamadı."
        Write-Host "  Bu klasörün yazma izni olduğundan emin ol (Program Files ya da" -ForegroundColor Yellow
        Write-Host "  System32 gibi korumalı bir konumda olmamalı)." -ForegroundColor Yellow
        exit 1
    }
    Write-Ok "Sanal ortam hazır"
}

# ---------------------------------------------------------------------------
# 2. Paketler kurulu mu?
# ---------------------------------------------------------------------------
# Not: işaret dosyasının adı nokta ile BAŞLAMAZ. Nokta ile başlayan dosyalar
# bazı platformlarda "gizli" sayılır ve Get-Item bunları -Force olmadan
# okuyamaz; bu da her açılışta paketlerin gereksiz yere yeniden kurulmasına
# yol açıyordu (canlı testte yakalandı).
$depsMarker = Join-Path $ProjectRoot ".venv\deps-installed.txt"
$reqFile    = Join-Path $ProjectRoot "requirements.txt"

# requirements.txt son kurulumdan sonra değiştiyse paketleri tazele.
# Zaman damgaları .NET ile okunuyor — gizli dosya özniteliğinden etkilenmez.
$needsInstall = $true
if (Test-Path $depsMarker) {
    try {
        $markerTime = [System.IO.File]::GetLastWriteTimeUtc($depsMarker)
        $reqTime    = [System.IO.File]::GetLastWriteTimeUtc($reqFile)
        if ($markerTime -gt $reqTime) { $needsInstall = $false }
    } catch {
        $needsInstall = $true   # okunamadıysa güvenli tarafta kal
    }
}

if ($needsInstall) {
    Write-Step "Paketler kuruluyor (ilk kurulumda birkaç dakika sürer)…"

    # Çıktıyı yakalıyoruz: başarılıysa kimse görmesin, başarısızsa GERÇEK
    # sebep ekrana gelsin. Eskiden --quiet yüzünden yalnızca "başarısız oldu"
    # yazıyordu ve asıl hata (ör. kırık sanal ortam) gizli kalıyordu.
    $pipLog = & $VenvPython -m pip install --upgrade pip setuptools --disable-pip-version-check 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $pipLog = & $VenvPython -m pip install -r requirements.txt --disable-pip-version-check 2>&1 | Out-String
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Paket kurulumu başarısız oldu."
        Write-Host ""
        Write-Host "  ── pip çıktısı ─────────────────────────────" -ForegroundColor DarkGray
        Write-Host $pipLog -ForegroundColor DarkGray
        Write-Host "  ────────────────────────────────────────────" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  İnternet bağlantını kontrol edip tekrar dene." -ForegroundColor Yellow
        exit 1
    }
    Set-Content -Path $depsMarker -Value (Get-Date -Format o) -Encoding UTF8
    Write-Ok "Paketler kuruldu"
}

# ---------------------------------------------------------------------------
# 3. .env dosyası
# ---------------------------------------------------------------------------
$envFile = Join-Path $ProjectRoot ".env"
$envJustCreated = $false

if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $envFile
    $envJustCreated = $true
    Write-Warn ".env dosyası oluşturuldu — e-posta ayarlarını doldurman gerekiyor"
}

# SMTP doldurulmuş mu? Kararı uygulamanın kendisine sorarız — kuralı burada
# ikinci kez yazmak, iki yerin birbiriyle çelişmesine yol açıyordu
# (başlatıcı "eksik" derken uygulama göndermeye kalkıyordu).
$smtpReady = $false
if (Test-Path $envFile) {
    & $VenvPython app.py --smtp-status 2>&1 | Out-Null
    $smtpReady = ($LASTEXITCODE -eq 0)
}

if ($envJustCreated) {
    Write-Host ""
    Write-Host "  E-posta göndermek için .env dosyasında şu üç satırı doldur:" -ForegroundColor Yellow
    Write-Host "     SMTP_USER, SMTP_PASSWORD, MAIL_TO" -ForegroundColor Yellow
    Write-Host "  Gmail için 16 haneli Uygulama Şifresi gerekiyor (README bölüm 3)." -ForegroundColor DarkYellow
    Write-Host "  Şimdilik doldurmadan da arayüzü kullanıp önizleme üretebilirsin." -ForegroundColor DarkGray
    Write-Host ""
    Start-Process notepad.exe $envFile
    Start-Sleep -Seconds 1
}
elseif (-not $smtpReady) {
    Write-Warn "SMTP ayarları eksik — e-posta gönderilemez, önizleme çalışır"
}

# ---------------------------------------------------------------------------
# 4. Tek seferlik rapor modu (-RunNow)
# ---------------------------------------------------------------------------
if ($RunNow) {
    Write-Host ""

    # Takip listesi boşsa rapor üretmenin anlamı yok — kullanıcıyı yönlendir.
    $listOutput = & $VenvPython app.py --list 2>&1 | Out-String
    if ($listOutput -match "Takip listesi boş") {
        Write-Warn "Takip listesi boş — önce hisse eklemen gerekiyor."
        Write-Host ""
        Write-Host "  'BIST Takip' kısayoluna çift tıklayıp arayüzden hisse ekle," -ForegroundColor Yellow
        Write-Host "  sonra bu kısayolu tekrar çalıştır." -ForegroundColor Yellow
        Wait-ForKey
        exit 0
    }

    if ($DryRun) {
        Write-Step "Rapor üretiliyor (e-posta gönderilmeyecek)…"
        & $VenvPython app.py --run-now --dry-run
    } else {
        Write-Step "Rapor üretiliyor ve e-posta gönderiliyor…"
        & $VenvPython app.py --run-now
    }

    Wait-ForKey
    exit 0
}

# ---------------------------------------------------------------------------
# 5. Port'u .env'den oku
# ---------------------------------------------------------------------------
$port = 5000
if (Test-Path $envFile) {
    $portLine = Select-String -Path $envFile -Pattern '^\s*FLASK_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($portLine) { $port = [int]$portLine.Matches[0].Groups[1].Value }
}
$url = "http://127.0.0.1:$port"

# ---------------------------------------------------------------------------
# 6. Sunucu ayağa kalkınca tarayıcıyı aç
# ---------------------------------------------------------------------------
# Ayrı bir gizli PowerShell süreci, sunucunun portu dinlemesini bekleyip
# tarayıcıyı açar. (Start-Job yerine ayrı süreç kullanılıyor: arka plan
# işlerinin masaüstü bağlamı olmayabilir ve tarayıcı hiç açılmayabilir.)
$openerCommand = @"
for (`$i = 0; `$i -lt 40; `$i++) {
    try {
        Invoke-WebRequest -Uri '$url' -UseBasicParsing -TimeoutSec 2 | Out-Null
        Start-Process '$url'
        break
    } catch {
        Start-Sleep -Milliseconds 750
    }
}
"@

# Hangi PowerShell çalıştırılabilirinin mevcut olduğunu bul
$psExe = "powershell"
if (-not (Get-Command $psExe -ErrorAction SilentlyContinue)) { $psExe = "pwsh" }

# $IsWindows yalnızca PowerShell 6+ ile geldi; Windows PowerShell 5.1'de
# tanımsızdır — tanımsızsa zaten Windows'tayız demektir.
$onWindows = ($null -eq $IsWindows) -or $IsWindows

$openerArgs = @("-NoProfile")
if ($onWindows) { $openerArgs += @("-WindowStyle", "Hidden") }
$openerArgs += @("-Command", $openerCommand)

# -WindowStyle her PowerShell sürümünde Start-Process parametresi olarak
# desteklenmiyor; desteklenmiyorsa onsuz dene. Tarayıcı açılamazsa bile
# uygulama çalışmaya devam etmeli — bu yüzden tüm blok korumalı.
$opener = $null
try {
    if ($onWindows) {
        $opener = Start-Process -FilePath $psExe -ArgumentList $openerArgs `
            -WindowStyle Hidden -PassThru -ErrorAction Stop
    } else {
        $opener = Start-Process -FilePath $psExe -ArgumentList $openerArgs `
            -PassThru -ErrorAction Stop
    }
} catch {
    Write-Warn "Tarayıcı otomatik açılamadı — adresi elle aç: $url"
}

# ---------------------------------------------------------------------------
# 7. Uygulamayı başlat
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  Arayüz:    $url" -ForegroundColor White
Write-Host "  Zamanlayıcı: etkin (günlük rapor saatinde otomatik gönderim)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Bu pencereyi kapatmak uygulamayı durdurur." -ForegroundColor DarkGray
Write-Host "  ─────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

try {
    & $VenvPython app.py --serve --schedule
} finally {
    # Tarayıcı açıcı hâlâ bekliyorsa (sunucu hiç ayağa kalkmadıysa) sonlandır
    if ($opener -and -not $opener.HasExited) {
        Stop-Process -Id $opener.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "  Uygulama durduruldu." -ForegroundColor DarkGray
Start-Sleep -Seconds 2
