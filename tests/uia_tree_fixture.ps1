$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../scripts/uia_tree.ps1')
$script:tree=@{}
for ($i=0; $i -le 12; $i++) { $script:tree[$i]=@{ child=($i+1); sibling=$null } }
$script:tree[12].child=$null
$meta={param($n) @{ class_name='mmui::XView'; type='ControlType.Custom'; role=$(if($n -eq 12){'chat_input'}else{'other'}) }}
$first={param($n) $script:tree[$n].child}
$next={param($n) $script:tree[$n].sibling}
$full=Read-BoundedUiaTree 0 $meta $first $next 12 20
if ($full.bounded -or $full.nodes.Count -ne 13 -or $full.nodes[12].role -ne 'chat_input') { throw 'Leaf boundary/deep traversal failure.' }
$cut=Read-BoundedUiaTree 0 $meta $first $next 8 20
if (-not $cut.bounded -or 'depth' -notin $cut.truncation_reasons) { throw 'Depth truncation lost.' }
$cut=Read-BoundedUiaTree 0 $meta $first $next 32 4
if (-not $cut.bounded -or $cut.nodes.Count -gt 4) { throw 'Node limit lost.' }
$broken=Read-BoundedUiaTree 0 $meta {param($n) throw 'synthetic private data'} $next 32 20
if ($broken.errors.Count -ne 1 -or ($broken | ConvertTo-Json -Depth 20) -match 'synthetic private data') { throw 'Error redaction failed.' }
# Parse the real collector without executing it or contacting any desktop provider.
$tokens=$null; $parseErrors=$null
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot '../scripts/probe_wechat_uia.ps1'),[ref]$tokens,[ref]$parseErrors)
if ($parseErrors.Count) { throw 'Collector syntax error.' }
Write-Output 'synthetic traversal and collector syntax passed'
