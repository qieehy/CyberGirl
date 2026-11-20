param(
    [Parameter(Mandatory=$true)][int]$TargetPid,
    [Parameter(Mandatory=$true)][long]$TargetHwnd,
    [switch]$Serve,
    [switch]$AllowFixedInput,
    [switch]$InspectInvokeOnly,
    [ValidateSet('value','deferred','source')][string]$ValueMarkerKind='value'
)
# Read-only, single response. Parent bounds lifetime with a kill-on-close job.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
. (Join-Path $PSScriptRoot 'wechat_input_metadata.ps1')
. (Join-Path $PSScriptRoot 'wechat_click_geometry.ps1')
$walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
function Same-Element($left,$right) {
    return ([string]::Join(',',$left.GetRuntimeId()) -ceq [string]::Join(',',$right.GetRuntimeId()))
}
function Read-StandaloneSurface([switch]$AsObject) {
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$TargetHwnd)
if ($root.Current.ProcessId -ne $TargetPid -or $root.Current.ClassName -cne 'mmui::ChatSingleWindow' -or
    $root.Current.AutomationId -cne 'ChatSingleWindowfilehelper' -or $root.Current.IsOffscreen) {
    throw 'standalone_filehelper_root_not_verified'
}
function Find-Class([string]$Class,[int]$Maximum) {
    $condition=New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty,$Class)
    $items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
    if ($items.Count -gt $Maximum) { throw 'standalone_query_bound' }
    foreach ($item in $items) {
        if ($item.Current.ProcessId -ne $TargetPid) { throw 'standalone_foreign_node' }
        $item
    }
}
$inputs=@(Find-Class 'mmui::ChatInputField' 2)
$pages=@(Find-Class 'mmui::ChatMessagePage' 2)
$buttons=@(Find-Class 'mmui::XOutlineButton' 32 | Where-Object {
    (Get-WechatInputRole '' $_.Current.ClassName $_.Current.ControlType.ProgrammaticName $_.Current.Name).role -eq 'send_button_candidate'
})
if ($inputs.Count -ne 1 -or $pages.Count -ne 1 -or $buttons.Count -ne 1 -or
    $inputs[0].Current.AutomationId -cne 'chat_input_field' -or $inputs[0].Current.IsOffscreen -or
    $buttons[0].Current.IsOffscreen) { throw 'standalone_surface_not_unique' }
$input=$inputs[0]; $button=$buttons[0]
$value=$input.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
if ($value.Current.IsReadOnly) { throw 'standalone_readonly_input' }
$textPattern=$null
$supported=$input.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$textPattern)
$text=''
if ($supported) { $text=$textPattern.DocumentRange.GetText(2001) }
$pair=Protect-WechatDraftPair $value.Current.Value $text $supported
$ids=@{root=@($root.GetRuntimeId()); page=@($pages[0].GetRuntimeId());
       chat_input=@($input.GetRuntimeId()); button=@($button.GetRuntimeId())}
$token=Protect-WechatDraft ([string]::Join('|',@('root','page','chat_input','button' | ForEach-Object {
    [string]::Join(',',$ids[$_])
})))
$current=@{input_token=$token.sha256; focused=$input.Current.HasKeyboardFocus;
           empty=$pair.value_empty; draft_sha256=$pair.value_sha256; send_enabled=$button.Current.IsEnabled}
$surface=@{root=$root; input=$input; button=$button}
$geometry=Read-WechatClickGeometry $surface $current
$result=@{runtime_ids=$ids; input_token=$token.sha256; comparison=$pair; geometry=$geometry;
          root_class='mmui::ChatSingleWindow'; root_matches_filehelper=$true;
          qt_has_keyboard_focus=$input.Current.HasKeyboardFocus}
if ($InspectInvokeOnly) {
    $invokePattern=$null
    $result.button_invoke_supported=$button.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern,[ref]$invokePattern)
}
# Recheck root after querying the surface. Never serialize captions or drafts.
if ($root.Current.AutomationId -cne 'ChatSingleWindowfilehelper') { throw 'standalone_root_changed' }
$script:boundValue=$value; $script:boundToken=$token.sha256
if ($AsObject) { return $result }
$result | ConvertTo-Json -Depth 9 -Compress
}
$script:valueUsed=$false
if ($Serve) {
    while ($null -ne ($command=[Console]::In.ReadLine())) {
        if ($command -ceq 'snapshot') { Read-StandaloneSurface; continue }
        if (-not $AllowFixedInput -or $script:valueUsed -or $command.Length -gt 2048) { throw 'standalone_readonly_command_denied' }
        $request=$command | ConvertFrom-Json
        if ([string]::Join(',',@($request.PSObject.Properties.Name | Sort-Object)) -cne 'command,text_b64,token' -or
            $request.command -cne 'fixed_value_once') { throw 'standalone_value_request_denied' }
        $fixedBase64=if ($ValueMarkerKind -ceq 'source') {
            'VzJCLTIwMjYxMTA3LWJhY2tncm91bmQtc291cmNlLeS4reaWh/CfmYI='
        } elseif ($ValueMarkerKind -ceq 'deferred') {
            'VzJCLTIwMjYxMTA3LWJhY2tncm91bmQtZGVmZXJyZWQt5Lit5paH8J+Zgg=='
        } else { 'VzJCLTIwMjYxMTA3LWJhY2tncm91bmQtdmFsdWUt5Lit5paH8J+Zgg==' }
        $expected=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($fixedBase64))
        $text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($request.text_b64))
        if ($text -cne $expected) { throw 'standalone_value_marker_denied' }
        $before=Read-StandaloneSurface -AsObject
        if ($request.token -cne $script:boundToken -or -not $before.comparison.value_empty -or
            -not $before.comparison.text_empty -or -not $before.comparison.exact_equal) { throw 'standalone_value_draft_or_target_changed' }
        $script:valueUsed=$true
        $script:boundValue.SetValue($text)
        @{accepted=$true} | ConvertTo-Json -Compress
    }
} else { Read-StandaloneSurface }
