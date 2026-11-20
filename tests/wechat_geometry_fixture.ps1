$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$parseErrors=$null
foreach ($name in @('wechat_click_geometry.ps1','wechat_native_ui.ps1')) {
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $taskRoot "scripts/$name"),[ref]$null,[ref]$parseErrors)
    if ($parseErrors) { throw 'powershell_parse_failed' }
}
. (Join-Path $taskRoot 'scripts/wechat_click_geometry.ps1')
$old=[WechatGeometry]::GetThreadDpiAwarenessContext()
$target=[IntPtr](-4)
$previous=[WechatGeometry]::SetThreadDpiAwarenessContext($target)
if ($previous -eq [IntPtr]::Zero) { throw 'fixture_context_change_failed' }
try {
    $changed=[WechatGeometry]::AreDpiAwarenessContextsEqual([WechatGeometry]::GetThreadDpiAwarenessContext(),$target)
    $center=New-Object WechatGeometry+Point
    $center.X=-950; $center.Y=620
    $mapped=New-Object WechatGeometry+Point
    $mapped.X=$center.X; $mapped.Y=$center.Y
    $mapped.X=549; $mapped.Y=520
    $independent=$center.X -eq -950 -and $center.Y -eq 620
} finally {
    [void][WechatGeometry]::SetThreadDpiAwarenessContext($previous)
}
$restored=[WechatGeometry]::AreDpiAwarenessContextsEqual([WechatGeometry]::GetThreadDpiAwarenessContext(),$old)
@{helper_compiled=$true;context_changed=$changed;context_restored=$restored;independent_point_copies=$independent} | ConvertTo-Json -Compress
