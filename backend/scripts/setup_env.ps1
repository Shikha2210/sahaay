<#
.SYNOPSIS
  Interactive, secret-safe creation of backend\.env  (run this yourself in the VS Code terminal).

.DESCRIPTION
  * Asks for one value at a time. Secrets are typed with hidden input and are NEVER printed or logged.
  * Generates JWT_SECRET locally (cryptographically random) if it is missing.
  * Never overwrites values that are already set: it only asks for what is missing.
    Use -Redo to re-enter specific values, e.g.  -Redo MONGODB_URI,BOOTSTRAP_SUPERVISOR_PASSWORD
  * Does not contact the internet. Run scripts\check_atlas.py afterwards to test the Atlas ping.

.EXAMPLE
  cd backend
  powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1
#>
param([string[]]$Redo = @())
$ErrorActionPreference = 'Stop'

$backend = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $backend '.env'
$examplePath = Join-Path $backend '.env.example'
if (-not (Test-Path $examplePath)) { throw "Cannot find $examplePath" }

# ---- load current values (without printing them) -------------------------------------------------------------
$values = [ordered]@{}
$raw = @{}; $orig = @{}   # untouched values are written back exactly as they were
if (Test-Path $envPath) {
  foreach ($line in Get-Content $envPath) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
      $v = $Matches[2].Trim(); $raw[$Matches[1]] = $v
      if ($v.Length -ge 2 -and $v.StartsWith("'") -and $v.EndsWith("'")) { $v = $v.Substring(1, $v.Length - 2).Replace("\'", "'").Replace('\\', '\') }
      $values[$Matches[1]] = $v; $orig[$Matches[1]] = $v
    }
  }
  Write-Host "Found existing backend\.env - keeping values that are already set." -ForegroundColor Cyan
} else {
  # start from the template defaults (non-secret placeholders are blanked below)
  foreach ($line in Get-Content $examplePath) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
  }
  foreach ($k in 'MONGODB_URI', 'JWT_SECRET', 'BOOTSTRAP_SUPERVISOR_PASSWORD', 'GROQ_API_KEY') { $values[$k] = '' }
  Write-Host "Creating backend\.env from backend\.env.example" -ForegroundColor Cyan
}
function Needs($name) { return ($Redo -contains $name) -or [string]::IsNullOrWhiteSpace($values[$name]) -or ($name -eq 'MONGODB_URI' -and $values[$name] -match '[<>]') }

function Read-Secret($prompt) {
  $secure = Read-Host -Prompt $prompt -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# ---- 1. MONGODB_URI (hidden) ---------------------------------------------------------------------------------
if (Needs 'MONGODB_URI') {
  Write-Host "`n[1/5] MONGODB_URI" -ForegroundColor Yellow
  Write-Host "  Atlas > your cluster > Connect > Drivers > copy the connection string, then replace <db_password> with your database user's password."
  Write-Host "  Paste it below. Nothing you type will be shown."
  while ($true) {
    $uri = (Read-Secret '  Atlas connection string').Trim()
    if ($uri -notmatch '^mongodb(\+srv)?://') { Write-Host "  That does not start with mongodb:// or mongodb+srv:// - try again." -ForegroundColor Red; continue }
    if ($uri -match '[<>]') { Write-Host "  It still contains a <placeholder> (like <db_password>). Replace it with the real value - try again." -ForegroundColor Red; continue }
    if ($uri -notmatch '@') { Write-Host "  It has no username/password section (user:password@host) - try again." -ForegroundColor Red; continue }
    $values['MONGODB_URI'] = $uri; break
  }
}
# The database name is set separately (MONGODB_DATABASE). We deliberately do NOT add "/sahaay" to the URI: that would
# change the authentication database and break logins for normal Atlas users.
$values['MONGODB_DATABASE'] = 'sahaay'

# ---- 2. JWT_SECRET (generated locally) -----------------------------------------------------------------------
if ((Needs 'JWT_SECRET') -or $values['JWT_SECRET'].Length -lt 32) {
  $bytes = New-Object byte[] 48
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); $rng.Dispose()
  $values['JWT_SECRET'] = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
  Write-Host "`n[2/5] JWT_SECRET generated locally (64 random characters). It is not displayed." -ForegroundColor Yellow
} else { Write-Host "`n[2/5] JWT_SECRET already set - kept." -ForegroundColor DarkGray }

# ---- 3. BOOTSTRAP_SUPERVISOR_EMAIL ---------------------------------------------------------------------------
if ((Needs 'BOOTSTRAP_SUPERVISOR_EMAIL') -or $values['BOOTSTRAP_SUPERVISOR_EMAIL'] -eq 'supervisor@example.com') {
  Write-Host "`n[3/5] BOOTSTRAP_SUPERVISOR_EMAIL" -ForegroundColor Yellow
  Write-Host "  The login email of the first supervisor account. Use a normal-looking address (not .local / .test / .invalid)."
  while ($true) {
    $email = (Read-Host -Prompt '  Supervisor email').Trim()
    if ($email -match '^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$' -and $email -notmatch '\.(local|test|invalid|localhost|example)$') { $values['BOOTSTRAP_SUPERVISOR_EMAIL'] = $email; break }
    Write-Host "  Not a usable email address - try again." -ForegroundColor Red
  }
} else { Write-Host "`n[3/5] BOOTSTRAP_SUPERVISOR_EMAIL already set - kept." -ForegroundColor DarkGray }

# ---- 4. BOOTSTRAP_SUPERVISOR_PASSWORD (hidden, entered twice) ------------------------------------------------
if (Needs 'BOOTSTRAP_SUPERVISOR_PASSWORD') {
  Write-Host "`n[4/5] BOOTSTRAP_SUPERVISOR_PASSWORD" -ForegroundColor Yellow
  Write-Host "  At least 12 characters with upper-case, lower-case, a digit and a symbol. Avoid words like 'password' or 'sahaay'."
  Write-Host "  Remember it: you need it to sign in as the first supervisor. Nothing you type will be shown."
  while ($true) {
    $p1 = Read-Secret '  Supervisor password'
    if ($p1.Length -lt 12 -or $p1 -cnotmatch '[a-z]' -or $p1 -cnotmatch '[A-Z]' -or $p1 -notmatch '\d' -or $p1 -notmatch '[^A-Za-z0-9]') {
      Write-Host "  Too weak - needs 12+ chars with upper, lower, digit and symbol." -ForegroundColor Red; continue }
    $p2 = Read-Secret '  Repeat the password'
    if ($p1 -ne $p2) { Write-Host "  The two entries did not match - try again." -ForegroundColor Red; continue }
    $values['BOOTSTRAP_SUPERVISOR_PASSWORD'] = $p1; break
  }
  $p1 = $null; $p2 = $null
} else { Write-Host "`n[4/5] BOOTSTRAP_SUPERVISOR_PASSWORD already set - kept." -ForegroundColor DarkGray }

# ---- 5. everything else keeps its safe default ---------------------------------------------------------------
if (-not $values['JWT_EXPIRE_MINUTES']) { $values['JWT_EXPIRE_MINUTES'] = '60' }
if (-not $values['SEED_DEMO_DATA']) { $values['SEED_DEMO_DATA'] = 'false' }
Write-Host "`n[5/5] GROQ_API_KEY left empty on purpose (added in the AI milestone). Other settings use safe defaults." -ForegroundColor DarkGray

# ---- write the file (values quoted so special characters are safe) --------------------------------------------
function Emit($k) { if ($raw.ContainsKey($k) -and $orig.ContainsKey($k) -and ($values[$k] -ceq $orig[$k])) { return "$k=$($raw[$k])" } return "$k=$(Q $values[$k])" }
function Q($v) { return "'" + $v.Replace('\', '\\').Replace("'", "\'") + "'" }
$order = 'MONGODB_URI', 'MONGODB_DATABASE', 'JWT_SECRET', 'JWT_EXPIRE_MINUTES', 'CORS_ORIGINS', 'GROQ_API_KEY', 'GROQ_MODEL', 'BOOTSTRAP_SUPERVISOR_EMAIL', 'BOOTSTRAP_SUPERVISOR_PASSWORD', 'SEED_DEMO_DATA'
$out = @('# Created by scripts\setup_env.ps1. Private - never commit or share this file.')
foreach ($k in $order) { if ($values.Contains($k)) { $out += (Emit $k) } }
foreach ($k in $values.Keys) { if ($order -notcontains $k) { $out += (Emit $k) } }
[IO.File]::WriteAllLines($envPath, $out, (New-Object Text.UTF8Encoding($false)))

# restrict the file to the current Windows user (best effort)
try { & icacls $envPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null } catch { }

Write-Host "`nSaved backend\.env (it is listed in .gitignore, so git will not track it)." -ForegroundColor Green
Write-Host "Next: test the connection with"
Write-Host "  .\.venv\Scripts\python.exe scripts\check_atlas.py" -ForegroundColor Cyan
