<#
    setup-story-points.ps1

    One script. Diagnoses, fixes (with your confirmation), and then proves
    empirically which custom field your Jira project uses for story points.

    Usage:   .\setup-story-points.ps1
    Reads .env from the current folder. Prints no credentials - safe to paste.
#>

$ErrorActionPreference = "Stop"
$PROBE_VALUE = 5

# ------------------------------------------------------------ config
if (-not (Test-Path ".env")) { Write-Host "No .env in this folder - see SETUP.md step 6." -ForegroundColor Red; exit 1 }
$cfg = @{}
Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim().Trim('"') }
}
foreach ($k in 'JIRA_BASE_URL','JIRA_EMAIL','JIRA_API_TOKEN','JIRA_PROJECT_KEY') {
    if (-not $cfg[$k]) { Write-Host "Missing $k in .env" -ForegroundColor Red; exit 1 }
}
$base    = $cfg['JIRA_BASE_URL'].TrimEnd('/')
$project = $cfg['JIRA_PROJECT_KEY']
$b64     = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$($cfg['JIRA_EMAIL']):$($cfg['JIRA_API_TOKEN'])"))
$headers = @{ Authorization = "Basic $b64"; Accept = "application/json" }
$jsonHdr = @{ Authorization = "Basic $b64"; Accept = "application/json"; "Content-Type" = "application/json" }

function Step($n,$t){ Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "    OK      $t" -ForegroundColor Green }
function Warn($t)   { Write-Host "    ISSUE   $t" -ForegroundColor Yellow }
function Info($t)   { Write-Host "            $t" -ForegroundColor DarkGray }
function Act($t)    { Write-Host "    FIXED   $t" -ForegroundColor Magenta }
function TryGet($u) { try { return Invoke-RestMethod -Uri $u -Headers $headers } catch { return $null } }
function Ask($q)    { $a = Read-Host "    $q [y/N]"; return ($a -match '^(y|Y)') }

# ------------------------------------------------------------ 1. project
Step 1 "Project and issue type"
$proj  = Invoke-RestMethod -Uri "$base/rest/api/3/project/$project" -Headers $headers
$itype = $proj.issueTypes | Where-Object { $_.name -eq 'Story' -and -not $_.subtask } | Select-Object -First 1
if (-not $itype) { $itype = $proj.issueTypes | Where-Object { -not $_.subtask } | Select-Object -First 1 }
Ok "$($proj.key) (id $($proj.id), style '$($proj.style)') / '$($itype.name)' (id $($itype.id))"

# ------------------------------------------------------------ 2. candidates
Step 2 "Point-like custom fields on this site"
# NOTE: PowerShell 5.1 passes a JSON array from Invoke-RestMethod down the pipeline
# as ONE object. Assign first, then wrap in @() so it enumerates properly.
$allFields = Invoke-RestMethod -Uri "$base/rest/api/3/field" -Headers $headers
$cands = @($allFields) | Where-Object { $_.custom -eq $true -and $_.name -match '(?i)point' } |
         ForEach-Object { [pscustomobject]@{ Id = [string]$_.id; Name = [string]$_.name } }
$cands = @($cands)
if (-not $cands) { Write-Host "    None exist on this site." -ForegroundColor Red; exit 1 }
foreach ($c in $cands) { Ok "$($c.Id)  |  $($c.Name)" }

# ------------------------------------------------------------ 3. contexts
Step 3 "Do their contexts cover project $project / '$($itype.name)'?"
$usable = @()
foreach ($c in $cands) {
    $ctxs = TryGet "$base/rest/api/3/field/$($c.Id)/context"
    if (-not $ctxs) { Info "$($c.Id): cannot read contexts (needs site admin)"; continue }
    $pm = TryGet "$base/rest/api/3/field/$($c.Id)/context/projectmapping"
    $tm = TryGet "$base/rest/api/3/field/$($c.Id)/context/issuetypemapping"
    foreach ($ctx in $ctxs.values) {
        $pOk = $ctx.isGlobalContext -or ($pm.values | Where-Object { $_.contextId -eq $ctx.id -and $_.projectId -eq $proj.id })
        $tOk = $ctx.isAnyIssueType  -or ($tm.values | Where-Object { $_.contextId -eq $ctx.id -and $_.issueTypeId -eq $itype.id })
        if ($pOk -and $tOk) { Ok "$($c.Id) covered by context '$($ctx.name)'"; $usable += $c; break }
        if (-not $pOk) {
            Warn "$($c.Id) context '$($ctx.name)' excludes project $project"
            if (Ask "Add project $project to that context?") {
                try { Invoke-RestMethod -Uri "$base/rest/api/3/field/$($c.Id)/context/$($ctx.id)/project" -Headers $jsonHdr -Method Put -Body ('{"projectIds":["' + $proj.id + '"]}') | Out-Null
                      Act "project added"; $usable += $c; break } catch { Info $_.Exception.Message }
            }
        } elseif (-not $tOk) {
            Warn "$($c.Id) context '$($ctx.name)' excludes issue type '$($itype.name)'"
            if (Ask "Add issue type '$($itype.name)' to that context?") {
                try { Invoke-RestMethod -Uri "$base/rest/api/3/field/$($c.Id)/context/$($ctx.id)/issuetype" -Headers $jsonHdr -Method Put -Body ('{"issueTypeIds":["' + $itype.id + '"]}') | Out-Null
                      Act "issue type added"; $usable += $c; break } catch { Info $_.Exception.Message }
            }
        }
    }
}
$usable = @($usable | Sort-Object Id -Unique)
if (-not $usable) { Write-Host "`nNo usable field. Stopping." -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------ 4. probe issue + editmeta
Step 4 "Creating a probe issue and reading its edit screen"
$cb  = @{ fields = @{ project = @{ key = $project }; summary = "Story-points probe - safe to delete"; issuetype = @{ id = $itype.id } } } | ConvertTo-Json -Depth 6
$key = (Invoke-RestMethod -Uri "$base/rest/api/3/issue" -Headers $jsonHdr -Method Post -Body $cb).key
Ok "created $key"

$em      = Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key/editmeta" -Headers $headers
$onEdit  = @()
foreach ($c in $usable) { if ($em.fields.PSObject.Properties.Name -contains $c.Id) { $onEdit += $c } }

if ($onEdit) {
    foreach ($c in $onEdit) { Ok "$($c.Id) '$($c.Name)' is on the edit screen" }
} else {
    Warn "None of the usable fields are on the edit screen for '$($itype.name)'"
    # resolve screens and offer to add
    $screenIds = @()
    $itss = TryGet "$base/rest/api/3/issuetypescreenscheme/project?projectId=$($proj.id)"
    if ($itss -and $itss.values) {
        $map = TryGet "$base/rest/api/3/issuetypescreenscheme/mapping?issueTypeScreenSchemeId=$($itss.values[0].issueTypeScreenScheme.id)"
        foreach ($m in $map.values) {
            if ($m.issueTypeId -eq $itype.id -or $m.issueTypeId -eq 'default') {
                $ss = TryGet "$base/rest/api/3/screenscheme?id=$($m.screenSchemeId)"
                foreach ($s in $ss.values) { foreach ($k in 'default','create','edit','view') { if ($s.screens.$k) { $screenIds += $s.screens.$k } } }
            }
        }
    }
    $screenIds = $screenIds | Sort-Object -Unique
    $target = $usable[0]
    foreach ($sid in $screenIds) {
        $tabs = @(TryGet "$base/rest/api/3/screens/$sid/tabs")
        if (-not $tabs) { continue }
        $avail = @(TryGet "$base/rest/api/3/screens/$sid/availableFields")
        if (-not ($avail | Where-Object { $_.id -eq $target.Id })) { continue }
        Warn "screen $sid is missing $($target.Id)"
        if (Ask "Add it to screen $sid?") {
            try { Invoke-RestMethod -Uri "$base/rest/api/3/screens/$sid/tabs/$($tabs[0].id)/fields" -Headers $jsonHdr -Method Post -Body ('{"fieldId":"' + $target.Id + '"}') | Out-Null
                  Act "added to screen $sid" } catch { Info $_.Exception.Message }
        }
    }
    $em = Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key/editmeta" -Headers $headers
    foreach ($c in $usable) { if ($em.fields.PSObject.Properties.Name -contains $c.Id) { $onEdit += $c } }
    if (-not $onEdit) {
        Write-Host "`nStill not on the edit screen. Set Story Points by hand on $base/browse/$key" -ForegroundColor Red
        Write-Host "then re-run this script." -ForegroundColor Red
        exit 1
    }
}

# ------------------------------------------------------------ 5. empirical probe
Step 5 "Writing $PROBE_VALUE to each candidate and reading it back"
$winner = $null
foreach ($c in $onEdit) {
    $f = @{}; $f[$c.Id] = $PROBE_VALUE
    try {
        Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key" -Headers $jsonHdr -Method Put -Body (@{ fields = $f } | ConvertTo-Json -Depth 4) | Out-Null
        $val = (Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key" -Headers $headers).fields.($c.Id)
        if ($val -eq $PROBE_VALUE) { Ok "$($c.Id) persisted the value"; if (-not $winner) { $winner = $c } }
        else { Info "$($c.Id) wrote but read back as '$val'" }
        $f2 = @{}; $f2[$c.Id] = $null
        Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key" -Headers $jsonHdr -Method Put -Body (@{ fields = $f2 } | ConvertTo-Json -Depth 4) | Out-Null
    } catch { Info "$($c.Id) rejected the write" }
}

# ------------------------------------------------------------ 6. verdict
Write-Host ""
if ($winner) {
    Write-Host "Story points field for ${project}: $($winner.Id)  ($($winner.Name))" -ForegroundColor Green
    Write-Host ""
    Write-Host "Add to .env:" -ForegroundColor Yellow
    Write-Host "    JIRA_STORY_POINTS_FIELD=$($winner.Id)"
    Write-Host ""
    Write-Host "Also set Board > Board settings > Estimation to '$($winner.Name)'" -ForegroundColor DarkGray
    try { Invoke-RestMethod -Uri "$base/rest/api/3/issue/$key" -Headers $headers -Method Delete | Out-Null
          Write-Host "Probe issue $key deleted." -ForegroundColor DarkGray } catch { Write-Host "Delete $key manually." -ForegroundColor DarkGray }
} else {
    Write-Host "No candidate persisted the value. Set it by hand on $base/browse/$key and re-run." -ForegroundColor Red
}