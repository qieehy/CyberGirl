param(
    [Parameter(Mandatory=$true)][int]$TargetPid,
    [Parameter(Mandatory=$true)][long]$TargetHwnd,
    [Parameter(Mandatory=$true)][long]$CreatedFiletime,
    [switch]$ReadOnly
)
# Private bounded transport, started inside the parent's kill-on-close Job.
# Native identity is checked by the Python worker before every action request.
$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WechatNativeWindow {
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
'@
. (Join-Path $PSScriptRoot 'wechat_input_metadata.ps1')
. (Join-Path $PSScriptRoot 'wechat_click_geometry.ps1')
$walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
$script:surface=$null
$script:focusUsed=$false; $script:inputUsed=$false; $script:submitUsed=$false
$script:bodyHash=$null
$sequence=0
function Assert-SurfaceCondition([bool]$Condition,[string]$Code) {
    if (-not $Condition) { $script:surfaceFailure=$Code; throw 'surface_changed' }
}
function Assert-Target {
    $client=Get-Process -Id $TargetPid -ErrorAction Stop
    $owner=[uint32]0
    [void][WechatNativeWindow]::GetWindowThreadProcessId([IntPtr]$TargetHwnd,[ref]$owner)
    if ($client.Path -cne 'C:\Program Files\Tencent\Weixin\Weixin.exe' -or
        $client.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $CreatedFiletime -or $owner -ne $TargetPid -or
        -not [WechatNativeWindow]::IsWindowVisible([IntPtr]$TargetHwnd) -or
        [WechatNativeWindow]::IsIconic([IntPtr]$TargetHwnd)) { throw 'binding_rejected' }
}
function Find-Ancestor($element,[string]$class) {
    $next=$element
    for ($i=0; $i -lt 24 -and $null -ne $next; $i++) {
        if ($next.Current.ProcessId -ne $TargetPid) { throw 'foreign_ancestor' }
        if ($next.Current.ClassName -ceq $class) { return $next }
        $next=$walker.GetParent($next)
    }
    throw 'ancestor_missing'
}
function Same-Element($left,$right) {
    return ([string]::Join(',',$left.GetRuntimeId()) -ceq [string]::Join(',',$right.GetRuntimeId()))
}
function Discover-Surface {
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$TargetHwnd)
    if ($root.Current.ClassName -cne 'mmui::MainWindow') { throw 'not_main_window' }
    function Find-Class([string]$class,[int]$bound) {
        $condition=New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ClassNameProperty,$class)
        $found=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
        if ($found.Count -gt $bound) { throw 'class_query_bound' }
        foreach ($element in $found) {
            if ($element.Current.ProcessId -ne $TargetPid) { throw 'foreign_node' }
            $element
        }
    }
    $inputs=@(Find-Class 'mmui::ChatInputField' 2)
    $selected=@(Find-Class 'mmui::ChatSessionCell' 64 | Where-Object {
        $_.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected
    })
    if ($inputs.Count -ne 1 -or $selected.Count -ne 1) { throw 'surface_not_unique' }
    $input=$inputs[0]
    $view=Find-Ancestor $input 'mmui::ChatInputView'
    $page=Find-Ancestor $input 'mmui::ChatMessagePage'
    $buttons=@(Find-Class 'mmui::XOutlineButton' 32 | Where-Object {
        $c=$_.Current
        (Get-WechatInputRole '' $c.ClassName $c.ControlType.ProgrammaticName $c.Name).role -eq 'send_button_candidate'
    })
    if ($buttons.Count -ne 1 -or -not (Same-Element (Find-Ancestor $buttons[0] 'mmui::ChatInputView') $view)) {
        throw 'send_button_not_unique_or_wrong_view'
    }
    return @{root=$root; input=$input; row=$selected[0]; view=$view; page=$page; button=$buttons[0]}
}
function Observe-Surface {
    Assert-Target
    if ($null -eq $script:surface) { $script:surface=Discover-Surface }
    $s=$script:surface
    foreach ($pair in @(@('input','mmui::ChatInputField'),@('row','mmui::ChatSessionCell'),
                       @('page','mmui::ChatMessagePage'),@('button','mmui::XOutlineButton'))) {
        $c=$s[$pair[0]].Current
        Assert-SurfaceCondition ($c.ProcessId -eq $TargetPid) ($pair[0]+'_process')
        Assert-SurfaceCondition ($c.ClassName -ceq $pair[1]) ($pair[0]+'_class')
        Assert-SurfaceCondition (-not $c.IsOffscreen) ($pair[0]+'_offscreen')
    }
    Assert-SurfaceCondition ($s.row.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Current.IsSelected) 'row_selection'
    Assert-SurfaceCondition (Same-Element (Find-Ancestor $s.input 'mmui::ChatMessagePage') $s.page) 'input_page'
    Assert-SurfaceCondition (Same-Element (Find-Ancestor $s.button 'mmui::ChatInputView') $s.view) 'button_view'
    Assert-SurfaceCondition (Same-Element (Find-Ancestor $s.input 'mmui::ChatInputView') $s.view) 'input_view'
    $script:candidateChecks=Get-WechatCandidateChecks $s.input.Current.Name $s.row.Current.Name $s.input.Current.AutomationId $s.input.Current.IsEnabled
    if (@($script:candidateChecks.Values | Where-Object { $_ -ne $true }).Count -ne 0) {
        throw 'not_filehelper_candidate'
    }
    $value=$s.input.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($value.Current.IsReadOnly) { throw 'readonly_input' }
    $draft=Protect-WechatDraft $value.Current.Value
    if ($draft.status -cne 'read') { throw 'draft_bound' }
    $ids=@{selected_session=@($s.row.GetRuntimeId()); chat_input=@($s.input.GetRuntimeId());
           page=@($s.page.GetRuntimeId()); button=@($s.button.GetRuntimeId())}
    $token=Protect-WechatDraft ([string]::Join('|',@('selected_session','chat_input','page','button' | ForEach-Object {
        [string]::Join(',',$ids[$_])
    })))
    Assert-Target
    return @{runtime_ids=$ids; input_token=$token.sha256;
             focused=($s.input.Current.HasKeyboardFocus -and [WechatNativeWindow]::GetForegroundWindow().ToInt64() -eq $TargetHwnd);
             empty=$draft.empty; draft_sha256=$draft.sha256; send_enabled=$s.button.Current.IsEnabled}
}
function Read-Frame {
    $builder=New-Object Text.StringBuilder
    for ($i=0; $i -le 16384; $i++) {
        $c=[Console]::In.Read()
        if ($c -lt 0) { if ($builder.Length) { throw 'partial_frame' }; return $null }
        if ($c -eq 10) {
            $line=$builder.ToString()
            if ([Text.Encoding]::UTF8.GetByteCount($line) -gt 16383) { throw 'frame_bound' }
            return $line
        }
        [void]$builder.Append([char]$c)
    }
    throw 'frame_bound'
}
try {
    while ($null -ne ($line=Read-Frame)) {
        $request=$line | ConvertFrom-Json
        if ($ReadOnly -and $request.command -cnotin @('observe','draft_patterns','click_geometry','focus_state')) { throw 'readonly_command_denied' }
        if ($request.sequence -ne ($sequence+1)) { throw 'sequence_rejected' }
        $keys=@($request.PSObject.Properties.Name | Sort-Object)
        $expected=@('command','sequence')
        if ($request.command -in @('focus_once','input_once','submit_once','draft_patterns','click_geometry','focus_state')) { $expected+= 'token' }
        if ($request.command -eq 'input_once') { $expected+= 'text' }
        if ([string]::Join(',',$keys) -cne [string]::Join(',',@($expected | Sort-Object))) { throw 'request_shape' }
        $current=Observe-Surface
        $reply=$null
        if ($request.command -ceq 'observe') { $reply=$current }
        elseif ($request.command -ceq 'focus_state') {
            if ($request.token -cne $current.input_token) { throw 'focus_state_control_changed' }
            $reply=@{input_token=$current.input_token; qt_has_keyboard_focus=$script:surface.input.Current.HasKeyboardFocus}
        }
        elseif ($request.command -ceq 'click_geometry') {
            if ($request.token -cne $current.input_token) { throw 'geometry_control_changed' }
            $reply=Read-WechatClickGeometry $script:surface $current
            Assert-Target
        } elseif ($request.command -ceq 'draft_patterns') {
            if ($request.token -cne $current.input_token) { throw 'diagnostic_control_changed' }
            $value=$script:surface.input.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
            $textPattern=$null
            $supported=$script:surface.input.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$textPattern)
            $text=''
            if ($supported) { $text=$textPattern.DocumentRange.GetText(2001) }
            $reply=@{input_token=$current.input_token; comparison=(Protect-WechatDraftPair $value $text $supported);
                     send_enabled=$current.send_enabled}
        } else {
            if ($request.token -cne $current.input_token) { throw 'control_token_changed' }
            switch -CaseSensitive ($request.command) {
                'focus_once' {
                    if ($script:focusUsed -or $script:inputUsed -or -not $current.empty) { throw 'focus_order_or_draft' }
                    $script:focusUsed=$true
                    if (-not $current.focused) {
                        $script:surface.input.SetFocus()
                        if ([WechatNativeWindow]::GetForegroundWindow().ToInt64() -ne $TargetHwnd) {
                            [void][WechatNativeWindow]::SetForegroundWindow([IntPtr]$TargetHwnd)
                        }
                        if ([WechatNativeWindow]::GetForegroundWindow().ToInt64() -ne $TargetHwnd -or
                            -not $script:surface.input.Current.HasKeyboardFocus) { throw 'foreground_or_focus_denied' }
                    }
                }
                'input_once' {
                    if ($script:inputUsed -or $script:submitUsed -or -not $current.focused -or -not $current.empty -or
                        $request.text -cnotmatch '^W2B-[A-Za-z0-9-]{1,64}$') { throw 'input_order_or_draft' }
                    $script:inputUsed=$true
                    $script:bodyHash=(Protect-WechatDraft $request.text).sha256
                    $script:surface.input.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($request.text)
                }
                'submit_once' {
                    if (-not $script:inputUsed -or $script:submitUsed -or -not $current.focused -or
                        $current.draft_sha256 -cne $script:bodyHash -or -not $current.send_enabled) { throw 'submit_order_or_draft' }
                    $script:submitUsed=$true
                    $script:surface.button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                }
                default { throw 'command_rejected' }
            }
            $reply=@{accepted=$true}
        }
        $sequence++
        [Console]::Out.WriteLine((@{sequence=$sequence; result=$reply} | ConvertTo-Json -Depth 6 -Compress))
        [Console]::Out.Flush()
    }
} catch {
    # No provider exception, raw draft, identifier or request body reaches logs.
    [Console]::Out.WriteLine((@{sequence=($sequence+1); error='native_ui_rejected'; candidate_checks=$script:candidateChecks;
        surface_check=$script:surfaceFailure; line=$_.InvocationInfo.ScriptLineNumber} | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    exit 1
}
