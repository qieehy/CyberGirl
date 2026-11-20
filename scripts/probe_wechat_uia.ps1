param(
    [string]$OutputPath,
    [Parameter(Mandatory=$true)][int]$TargetPid,
    [Parameter(Mandatory=$true)][long]$TargetHwnd,
    [Parameter(Mandatory=$true)][long]$CreatedFiletime,
    [ValidateRange(1,32)][int]$MaxDepth=32,
    [ValidateRange(1,800)][int]$MaxNodes=800,
    [switch]$ControlView,
    [switch]$Worker
)

# Read-only diagnostic. No Name, Value, TextPattern, window titles or runtime IDs.
# The worker is bounded by a separate process because UIA provider calls can hang.
$ErrorActionPreference = 'Stop'
if ($TargetPid -le 0 -or $TargetHwnd -le 0 -or $CreatedFiletime -le 0) { throw 'Invalid target binding.' }
if (-not $OutputPath) { throw 'Provide a new JSON OutputPath.' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) { throw 'Output exists; choose a new path.' }
if (-not (Test-Path -LiteralPath ([IO.Path]::GetDirectoryName($OutputPath)))) {
    throw 'Output parent directory must exist.'
}
if (-not $Worker) {
    $workerOutput = $OutputPath + '.worker'
    if (Test-Path -LiteralPath $workerOutput) { throw 'Worker output already exists.' }
    $engine = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -NonInteractive -File "{0}" -Worker -OutputPath "{1}"' -f $PSCommandPath, $workerOutput
    $arguments += ' -TargetPid {0} -TargetHwnd {1} -CreatedFiletime {2} -MaxDepth {3} -MaxNodes {4}' -f $TargetPid,$TargetHwnd,$CreatedFiletime,$MaxDepth,$MaxNodes
    if ($ControlView) { $arguments += ' -ControlView' }
    $child = Start-Process -FilePath $engine -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (-not $child.WaitForExit(30000)) {
        $child.Kill()
        $child.WaitForExit()
        $result = [ordered]@{ status='timeout'; deadline_seconds=30; worker_pid=$child.Id }
    } elseif (Test-Path -LiteralPath $workerOutput) {
        $result = Get-Content -Raw -LiteralPath $workerOutput | ConvertFrom-Json
    } else {
        $result = [ordered]@{ status='worker_failed'; exit_code=$child.ExitCode }
    }
    $result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    Write-Output $OutputPath
    exit
}

$result = [ordered]@{
    recorded_at=[DateTimeOffset]::UtcNow.ToString('o')
    status='started'
    collector='Windows .NET UIAutomationClient; read-only RawViewWalker and ControlViewWalker'
    collector_pid=$PID
    collector_session=(Get-Process -Id $PID).SessionId
    collector_64bit=[Environment]::Is64BitProcess
    target_executable='C:\Program Files\Tencent\Weixin\Weixin.exe'
    limits=@{ max_windows=1; max_nodes_per_view=$MaxNodes; max_depth=$MaxDepth; outer_deadline_seconds=30 }
    automation_id_policy='fixed role matching only; raw IDs never serialized'
    forbidden_fields_read=$false
    input_actions=0
    model_requests=0
    external_messages=0
    processes=@()
    windows=@()
}
try {
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class WechatWindowMetadata {
    public delegate bool EnumCallback(IntPtr hwnd, IntPtr value);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumCallback callback, IntPtr value);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr hwnd, StringBuilder name, int max);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    public static long[] ForProcess(int pid) {
        var handles = new List<long>();
        EnumWindows((hwnd, value) => { uint owner; GetWindowThreadProcessId(hwnd, out owner);
            if(owner == pid) handles.Add(hwnd.ToInt64()); return true; }, IntPtr.Zero);
        return handles.ToArray();
    }
    public static string ClassName(long hwnd) {
        var s = new StringBuilder(256); GetClassName(new IntPtr(hwnd), s, s.Capacity); return s.ToString();
    }
}
'@
    . (Join-Path $PSScriptRoot 'uia_tree.ps1')
    function Read-View($root, $walker) {
        return Read-BoundedUiaTree $root {
            param($element)
            $c = $element.Current
            if ($c.ProcessId -ne $TargetPid) { throw 'Foreign process node.' }
            $role = 'other'
            # Read only to recognize these public control roles, never persist raw IDs.
            $aid = $c.AutomationId
            if ($aid -match '(^|\.)session_list$') { $role='session_list' }
            elseif ($aid -match '(^|\.)chat_input_field$') { $role='chat_input' }
            elseif ($c.ClassName -eq 'mmui::MainTabBar') { $role='navigation' }
            @{ process_id=$c.ProcessId; type=$c.ControlType.ProgrammaticName; class_name=$c.ClassName;
               framework=$c.FrameworkId; offscreen=$c.IsOffscreen; enabled=$c.IsEnabled; role=$role }
        } {param($e) $walker.GetFirstChild($e)} {param($e) $walker.GetNextSibling($e)} $MaxDepth $MaxNodes
    }
    function Assert-Target {
        $client = Get-Process -Id $TargetPid -ErrorAction Stop
        if ($client.Path -ne $result.target_executable -or
            $client.StartTime.ToUniversalTime().ToFileTimeUtc() -ne $CreatedFiletime -or
            $TargetHwnd -notin [WechatWindowMetadata]::ForProcess($TargetPid) -or
            -not [WechatWindowMetadata]::IsWindowVisible([IntPtr]$TargetHwnd)) { throw 'Target identity changed.' }
    }
    try { Assert-Target } catch { $result.status='wrong_window'; throw }
    $matching = @(Get-Process -Id $TargetPid -ErrorAction Stop)
    $result.client_version = (Get-Item -LiteralPath $result.target_executable).VersionInfo.FileVersion
    $result.processes = @($matching | ForEach-Object { @{ pid=$_.Id; session=$_.SessionId } })
    foreach ($process in $matching) {
        foreach ($handle in [WechatWindowMetadata]::ForProcess($process.Id)) {
            if ($handle -ne $TargetHwnd) { continue }
            if ($result.windows.Count -ge 8) { break }
            $visible = [WechatWindowMetadata]::IsWindowVisible([IntPtr]$handle)
            $class = [WechatWindowMetadata]::ClassName($handle)
            # Skip invisible helper windows; no activation or restoration in this probe.
            if (-not $visible) { continue }
            $window = [ordered]@{
                hwnd=$handle; pid=$process.Id; win32_class=$class; visible=$visible
                created_filetime=[string]$CreatedFiletime
                minimized=[WechatWindowMetadata]::IsIconic([IntPtr]$handle)
            }
            $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
            if ($null -eq $root) { $window.error='no_automation_root' }
            else {
                $window.raw = Read-View $root ([System.Windows.Automation.TreeWalker]::RawViewWalker)
                if ($ControlView) { $window.control = Read-View $root ([System.Windows.Automation.TreeWalker]::ControlViewWalker) }
            }
            $result.windows += $window
        }
    }
    try { Assert-Target } catch { $result.windows=@(); $result.status='wrong_window'; throw }
    $result.status = if ($matching.Count -eq 0) { 'no_target_process' } elseif ($result.windows.Count -eq 0) { 'no_visible_target_window' } else { 'observed' }
} catch {
    if ($result.status -ne 'wrong_window') { $result.status='provider_error' }
    $result.error=@{ type=$_.Exception.GetType().FullName; hresult=$_.Exception.HResult }
}
$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
