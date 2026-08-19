<#
.SYNOPSIS
    Masaüstünde BIST Takip kısayolu oluşturur.

.DESCRIPTION
    İki kısayol kurar:
      - "BIST Takip"          → arayüzü açar, zamanlayıcıyı çalıştırır
      - "BIST Takip - Rapor"  → raporu bir kez üretip e-posta gönderir

    İkinci kısayol isteğe bağlıdır; -SadeceAna ile atlanabilir.
#>

[CmdletBinding()]
param(
    [switch]$SadeceAna
)

$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Desktop     = [Environment]::GetFolderPath("Desktop")
$IconPath    = Join-Path $ProjectRoot "static\bist-takip.ico"
$BatPath     = Join-Path $ProjectRoot "BIST-Takip-Baslat.bat"

Write-Host ""
Write-Host "  Masaüstü kısayolu oluşturuluyor…" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $BatPath)) {
    Write-Host "  ✗ BIST-Takip-Baslat.bat bulunamadı: $BatPath" -ForegroundColor Red
    exit 1
}

$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut {
    param(
        [string]$Name,
        [string]$Arguments,
        [string]$Description
    )

    $linkPath = Join-Path $Desktop "$Name.lnk"
    $sc = $shell.CreateShortcut($linkPath)
    $sc.TargetPath       = $BatPath
    $sc.Arguments        = $Arguments
    $sc.WorkingDirectory = $ProjectRoot
    $sc.Description      = $Description
    if (Test-Path $IconPath) { $sc.IconLocation = "$IconPath,0" }
    $sc.WindowStyle      = 1     # normal pencere
    $sc.Save()

    Write-Host "  ✓ $Name" -ForegroundColor Green
    return $linkPath
}

New-AppShortcut -Name "BIST Takip" -Arguments "" `
    -Description "BIST hisse takip arayüzünü açar ve günlük zamanlayıcıyı çalıştırır" | Out-Null

if (-not $SadeceAna) {
    New-AppShortcut -Name "BIST Takip - Raporu Gonder" -Arguments "-RunNow" `
        -Description "Raporu şimdi üretir ve e-posta olarak gönderir" | Out-Null
}

Write-Host ""
Write-Host "  Masaüstüne bak — çift tıklayarak başlatabilirsin." -ForegroundColor White
Write-Host ""
Write-Host "  İlk çalıştırmada kurulum otomatik yapılır (birkaç dakika)." -ForegroundColor DarkGray
Write-Host "  Sonraki açılışlarda doğrudan arayüz açılır." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Kapatmak için bir tuşa bas…" -ForegroundColor DarkGray
try {
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
} catch {
    try { $null = Read-Host } catch { Start-Sleep -Seconds 5 }
}
