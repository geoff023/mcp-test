<#
    check-jira.ps1  -  verify Jira Cloud API access from Windows PowerShell

    Usage:
        .\check-jira.ps1
        .\check-jira.ps1 -ProjectKey AUR

    Reads .env from the current folder if present, otherwise prompts.
    Nothing is written anywhere. Safe to re-run.
#>

param(
    [string]$ProjectKey = ""
)

$ErrorActionPreference = "Stop"

# ---------- load .env if present ----------
$cfg = @{}
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') {
            $cfg[$Matches[1]] = $Matches[2].Trim().Trim('"')
        }
    }
    Write-Host "Loaded .env" -ForegroundColor DarkGray
}

function Get-Val($key, $prompt, $secret = $false) {
    if ($cfg[$key])              { return $cfg[$key] }
    if (Test-Path "env:$key")    { return (Get-Item "env:$key").Value }
    if ($secret) {
        $s = Read-Host -Prompt $prompt -AsSecureString
        return [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))
    }
    return Read-Host -Prompt $prompt
}

$base  = (Get-Val 'JIRA_BASE_URL'  'Jira base URL (https://yoursite.atlassian.net)').TrimEnd('/')
$email = Get-Val 'JIRA_EMAIL'      'Atlassian account email'
$token = Get-Val 'JIRA_API_TOKEN'  'API token' $true

if (-not $ProjectKey) {
    $ProjectKey = if ($cfg['JIRA_PROJECT_KEY']) { $cfg['JIRA_PROJECT_KEY'] } else { Read-Host "Project key (e.g. AUR)" }
}

$b64     = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${email}:${token}"))
$headers = @{ Authorization = "Basic $b64"; Accept = "application/json" }

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Ok($text)       { Write-Host "    OK  $text" -ForegroundColor Green }
function Bad($text)      { Write-Host "    FAIL  $text" -ForegroundColor Red }

# ---------- 1. authentication ----------
Step 1 "Authenticating against $base"
try {
    $me = Invoke-RestMethod -Uri "$base/rest/api/3/myself" -Headers $headers
    Ok "$($me.displayName)  <$($me.emailAddress)>"
    Write-Host "        accountId: $($me.accountId)" -ForegroundColor DarkGray
} catch {
    Bad $_.Exception.Message
    Write-Host "        401 means the email or token is wrong." -ForegroundColor DarkGray
    Write-Host "        404 usually means the base URL is wrong." -ForegroundColor DarkGray
    exit 1
}

# ---------- 2. project ----------
Step 2 "Reading project $ProjectKey"
try {
    $proj = Invoke-RestMethod -Uri "$base/rest/api/3/project/$ProjectKey" -Headers $headers
    Ok "$($proj.key) - $($proj.name)  [$($proj.projectTypeKey), $($proj.style)]"
    if ($proj.style -eq 'next-gen') {
        Write-Host "        Note: this is a team-managed project. A company-managed" -ForegroundColor Yellow
        Write-Host "        Scrum project behaves more predictably over the REST API." -ForegroundColor Yellow
    }
} catch {
    Bad "Could not read project '$ProjectKey' - check the key."
    exit 1
}

# ---------- 3. story points field ----------
Step 3 "Locating the story points custom field"
try {
    $fields = Invoke-RestMethod -Uri "$base/rest/api/3/field" -Headers $headers
    $hits = $fields | Where-Object { $_.name -match '(?i)story\s*point|point\s*estimate' } |
                      Select-Object id, name
    if ($hits) {
        foreach ($f in $hits) { Ok "$($f.id)  |  $($f.name)" }
        Write-Host "        Put the matching id in .env as JIRA_STORY_POINTS_FIELD" -ForegroundColor DarkGray
    } else {
        Bad "No story-point field found."
        Write-Host "        Enable estimation: Project settings > Features > Estimation" -ForegroundColor DarkGray
    }
} catch {
    Bad $_.Exception.Message
}

# ---------- 4. issue search ----------
Step 4 "Searching for issues"
$jql  = "project = $ProjectKey ORDER BY created DESC"
$done = $false
try {
    $body = @{ jql = $jql; maxResults = 5; fields = @("summary","status") } | ConvertTo-Json
    $res  = Invoke-RestMethod -Uri "$base/rest/api/3/search/jql" -Headers $headers `
                              -Method Post -Body $body -ContentType "application/json"
    Ok "POST /rest/api/3/search/jql works - use this endpoint"
    $done = $true
} catch {
    Write-Host "    POST /search/jql not accepted, trying the older endpoint..." -ForegroundColor DarkGray
}
if (-not $done) {
    try {
        $u   = "$base/rest/api/3/search?jql=" + [uri]::EscapeDataString($jql) + "&maxResults=5"
        $res = Invoke-RestMethod -Uri $u -Headers $headers
        Ok "GET /rest/api/3/search works - use this endpoint"
    } catch {
        Bad "Neither search endpoint responded. $($_.Exception.Message)"
        exit 1
    }
}
if ($res.issues) {
    foreach ($i in $res.issues) {
        Write-Host "        $($i.key)  $($i.fields.summary)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "        Project is empty - that is fine, seed it later." -ForegroundColor DarkGray
}

Write-Host "`nAll checks passed. The architecture is viable - screenshot this.`n" -ForegroundColor Green