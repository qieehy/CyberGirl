param(
    [Parameter(Mandatory=$true)][int]$TargetPid,
    [Parameter(Mandatory=$true)][long]$TargetHwnd,
    [Parameter(Mandatory=$true)][string]$ExpectedLabel
)
# Identity metadata only. Never asks for message text, draft values, focus or input patterns.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$TargetHwnd)
$id=$root.Current.AutomationId
if ($root.Current.ProcessId -ne $TargetPid -or $root.Current.ClassName -cne 'mmui::ChatSingleWindow' -or
    $root.Current.IsOffscreen -or -not $id.StartsWith('ChatSingleWindow') -or
    -not [string]::Equals($root.Current.Name,$ExpectedLabel,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'private_selected_root_label_not_matched'
}
$condition=New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ClassNameProperty,'mmui::ChatInputField')
$inputs=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
if ($inputs.Count -ne 1 -or $inputs[0].Current.ProcessId -ne $TargetPid -or
    $inputs[0].Current.AutomationId -cne 'chat_input_field' -or $inputs[0].Current.IsOffscreen) {
    throw 'private_selected_editor_not_unique'
}
$sha=[Security.Cryptography.SHA256]::Create()
try { $hash=([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($id)))).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
$result=@{input_runtime_ids=@($inputs[0].GetRuntimeId()); root_runtime_ids=@($root.GetRuntimeId());
          root_automation_sha256=$hash; selected_label_matched=$true}
if ($root.Current.AutomationId -cne $id -or
    -not [string]::Equals($root.Current.Name,$ExpectedLabel,[StringComparison]::OrdinalIgnoreCase)) {
    throw 'private_selected_root_changed'
}
$result | ConvertTo-Json -Depth 5 -Compress
