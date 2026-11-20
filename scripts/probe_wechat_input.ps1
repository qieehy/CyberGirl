param(
    [Parameter(Mandatory=$true)][int]$TargetPid,
    [Parameter(Mandatory=$true)][long]$TargetHwnd,
    [Parameter(Mandatory=$true)][long]$CreatedFiletime,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [switch]$RuntimePipe
)

# Standalone project capability test, read only. Parent bounds lifetime and owns restoration.
$ErrorActionPreference = 'Stop'
if ($TargetPid -le 0 -or $TargetHwnd -le 0 -or $CreatedFiletime -le 0) { throw 'Invalid binding.' }
if (Test-Path -LiteralPath $OutputPath) { throw 'Output exists.' }
$result = [ordered]@{
    status='started'; pid=$TargetPid; hwnd=$TargetHwnd; created_filetime=[string]$CreatedFiletime
    input_actions=0; model_requests=0; external_messages=0
    raw_values_serialized=$false; canonical_identity_verified=$false
    limits=@{ max_depth=32; max_nodes=800; max_draft_chars=2000; parent_timeout_seconds=25 }
}
try {
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WechatInputWindow {
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
}
'@
    . (Join-Path $PSScriptRoot 'uia_tree.ps1')
    . (Join-Path $PSScriptRoot 'wechat_input_metadata.ps1')
    function Assert-Target {
        $client = Get-Process -Id $TargetPid -ErrorAction Stop
        $owner = [uint32]0
        [void][WechatInputWindow]::GetWindowThreadProcessId([IntPtr]$TargetHwnd, [ref]$owner)
        if ($client.Path -cne 'C:\Program Files\Tencent\Weixin\Weixin.exe' -or
            $client.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $CreatedFiletime -or
            $owner -ne $TargetPid -or -not [WechatInputWindow]::IsWindowVisible([IntPtr]$TargetHwnd) -or
            [WechatInputWindow]::IsIconic([IntPtr]$TargetHwnd)) { throw 'Target changed or minimized.' }
    }
    Assert-Target
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$TargetHwnd)
    $script:nativeRuntime = @{}
    $result.tree = Read-BoundedUiaTree $root {
        param($element)
        $c = $element.Current
        if ($c.ProcessId -ne $TargetPid) { throw 'Foreign node.' }
        $aid = $c.AutomationId
        $patterns = @($element.GetSupportedPatterns() | ForEach-Object { $_.Id })
        if ($patterns.Count -gt 32) { throw 'Pattern bound.' }
        $selected = $false
        if ($c.ClassName -eq 'mmui::ChatSessionCell' -and $patterns -contains 10010) {
            $selected = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected
        }
        $scoped = ($aid -cmatch '(^|\.)chat_input_field$' -or $selected -or
                   $c.ClassName -in @('mmui::ChatTitleBarChatSingleView','mmui::ChatTitleBarMasterView',
                       'mmui::ChatMessagePage','mmui::ChatInputView','mmui::ChatMasterView'))
        $nameValue = ''
        if ($scoped -or $c.ControlType.ProgrammaticName -eq 'ControlType.Button') { $nameValue = $c.Name }
        $row = Get-WechatInputRole $aid $c.ClassName $c.ControlType.ProgrammaticName $nameValue
        $row.class_name=$c.ClassName; $row.type=$c.ControlType.ProgrammaticName
        $row.enabled=$c.IsEnabled; $row.offscreen=$c.IsOffscreen; $row.focused=$c.HasKeyboardFocus
        $row.pattern_ids=$patterns; $row.selected=$selected
        if ($selected -and $c.ClassName -eq 'mmui::ChatSessionCell') {
            if ($RuntimePipe) { $script:nativeRuntime.selected_session=@($element.GetRuntimeId()) }
            $sessionShape=Protect-WechatSessionPayload $aid
            $row.session_item_marker_present=$sessionShape.marker_present
            $row.canonical_word_anywhere=$sessionShape.canonical_word_anywhere
            if ($sessionShape.marker_present) { $row.session_payload_shape=$sessionShape.shape }
        }
        if ($c.ClassName -cmatch '^mmui::Chat(Text|Image|Bubble)?ItemView$') {
            # Check whether a platform-message-ID bridge is possible, without reading bodies or IDs into evidence.
            $row.message_id_shape='other'
            if (-not $aid) { $row.message_id_shape='empty' }
            elseif ($aid -cmatch '^\d+$') { $row.message_id_shape='digits' }
            elseif ($aid -cmatch '^(chat_item|chat_message|message|msg)[_.:-]\d+$') { $row.message_id_shape='message_prefix_digits' }
            elseif ($aid -cmatch '^[a-f0-9]{32,64}$') { $row.message_id_shape='hex_token' }
            $row.numeric_component_lengths=@([regex]::Matches($aid,'\d{6,}') | ForEach-Object { $_.Length })
        }
        if ($row.role -eq 'chat_info_candidate') {
            $row.under_chat_title=$false
            $ancestor = $walker.GetParent($element)
            for ($i=0; $i -lt 8 -and $null -ne $ancestor; $i++) {
                if ($ancestor.Current.ClassName -cmatch '^mmui::ChatTitleBar') { $row.under_chat_title=$true; break }
                $ancestor = $walker.GetParent($ancestor)
            }
        }
        if ($scoped) {
            $row.help_is_canonical_filehelper=($c.HelpText -ceq 'filehelper')
            $row.item_status_is_canonical_filehelper=($c.ItemStatus -ceq 'filehelper')
            $row.item_type_is_canonical_filehelper=($c.ItemType -ceq 'filehelper')
            $row.name_is_canonical_filehelper=($nameValue -ceq 'filehelper')
            $helperName = [string]::Join('', [char[]](0x6587,0x4EF6,0x4F20,0x8F93,0x52A9,0x624B))
            $row.name_contains_filehelper_label=$nameValue.Contains($helperName)
            if ($patterns -contains 10002 -and $row.role -ne 'chat_input') {
                $value = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $row.value_is_canonical_filehelper=($value.Current.Value -ceq 'filehelper')
            }
        }
        if ($row.role -eq 'chat_input') {
            if ($RuntimePipe) { $script:nativeRuntime.chat_input=@($element.GetRuntimeId()) }
            $runtime = [string]::Join(',', $element.GetRuntimeId())
            $row.runtime_token=(Protect-WechatDraft $runtime).sha256
            $row.draft=@{ status='unsupported' }
            if ($patterns -contains 10002) {
                $value = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $row.value_read_only=$value.Current.IsReadOnly
                $row.draft=Protect-WechatDraft $value.Current.Value
                $row.draft_source='ValuePattern'
            } elseif ($patterns -contains 10014) {
                $text = $element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
                $row.draft=Protect-WechatDraft ($text.DocumentRange.GetText(2001))
                $row.draft_source='TextPattern'
            }
        }
        return $row
    } {param($e) $walker.GetFirstChild($e)} {param($e) $walker.GetNextSibling($e)} 32 800
    Assert-Target
    $result.status='observed'
} catch {
    $result.status='not_verified'
    $result.error=@{ type=$_.Exception.GetType().FullName; hresult=$_.Exception.HResult }
}
$result.recorded_at=[DateTimeOffset]::UtcNow.ToString('o')
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
if ($RuntimePipe) {
    # Transient parent pipe only. These values never enter evidence files.
    @{ pid=$TargetPid; hwnd=$TargetHwnd; created_filetime=[string]$CreatedFiletime;
       runtime_ids=$script:nativeRuntime } | ConvertTo-Json -Depth 4 -Compress | Write-Output
}
