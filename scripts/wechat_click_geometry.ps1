# Read-only geometry. Thread DPI context is restored; no window activation,
# cursor movement, mouse messages, capture changes, or keyboard input.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WechatGeometry {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X, Y; }
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
 [StructLayout(LayoutKind.Sequential)] public struct GuiInfo {
  public uint Size, Flags;
  public IntPtr Active, Focus, Capture, MenuOwner, MoveSize, Caret;
  public Rect CaretRect;
 }
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
 [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
 [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr left, IntPtr right);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);
 [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
 [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hwnd, ref Point point);
 [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd, ref Point point);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
 [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread, ref GuiInfo info);
 [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
'@

function Read-WechatClickGeometry($Surface, $Observation) {
    $context=[IntPtr](-4) # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, this helper thread only.
    $old=[WechatGeometry]::SetThreadDpiAwarenessContext($context)
    if ($old -eq [IntPtr]::Zero) { throw 'geometry_dpi_context_unavailable' }
    $result=$null
    try {
        if (-not [WechatGeometry]::AreDpiAwarenessContextsEqual([WechatGeometry]::GetThreadDpiAwarenessContext(),$context)) {
            throw 'geometry_dpi_context_not_verified'
        }
        $bounds=$Surface.button.Current.BoundingRectangle
        if ($bounds.IsEmpty -or $bounds.Width -le 0 -or $bounds.Height -le 0) { throw 'geometry_empty_button' }
        $center=New-Object WechatGeometry+Point
        $center.X=[int][Math]::Floor(($bounds.Left+$bounds.Right)/2)
        $center.Y=[int][Math]::Floor(($bounds.Top+$bounds.Bottom)/2)
        $origin=New-Object WechatGeometry+Point
        $client=New-Object WechatGeometry+Rect
        if (-not [WechatGeometry]::GetClientRect([IntPtr]$TargetHwnd,[ref]$client) -or
            -not [WechatGeometry]::ClientToScreen([IntPtr]$TargetHwnd,[ref]$origin)) { throw 'geometry_client_unavailable' }
        $mapped=New-Object WechatGeometry+Point
        $mapped.X=$center.X; $mapped.Y=$center.Y
        if (-not [WechatGeometry]::ScreenToClient([IntPtr]$TargetHwnd,[ref]$mapped)) { throw 'geometry_mapping_failed' }
        $roundtrip=New-Object WechatGeometry+Point
        $roundtrip.X=$mapped.X; $roundtrip.Y=$mapped.Y
        if (-not [WechatGeometry]::ClientToScreen([IntPtr]$TargetHwnd,[ref]$roundtrip) -or
            $roundtrip.X -ne $center.X -or $roundtrip.Y -ne $center.Y) { throw 'geometry_mapping_roundtrip_failed' }
        $owner=[uint32]0
        $thread=[WechatGeometry]::GetWindowThreadProcessId([IntPtr]$TargetHwnd,[ref]$owner)
        if ($owner -ne $TargetPid -or $thread -eq 0) { throw 'geometry_receiver_changed' }
        $dpi=[WechatGeometry]::GetDpiForWindow([IntPtr]$TargetHwnd)
        if ($dpi -eq 0) { throw 'geometry_dpi_unavailable' }
        $info=New-Object WechatGeometry+GuiInfo
        $info.Size=[Runtime.InteropServices.Marshal]::SizeOf($info)
        if (-not [WechatGeometry]::GetGUIThreadInfo($thread,[ref]$info)) { throw 'geometry_thread_unavailable' }
        $hit=[WechatGeometry]::WindowFromPoint($center)
        $hitRoot=[WechatGeometry]::GetAncestor($hit,2)
        $hitOwner=[uint32]0
        [void][WechatGeometry]::GetWindowThreadProcessId($hit,[ref]$hitOwner)
        $buttonToken=(Protect-WechatDraft ([string]::Join(',',$Surface.button.GetRuntimeId()))).sha256
        $hitToken=''
        if ($hitRoot.ToInt64() -eq $TargetHwnd -and $hitOwner -eq $TargetPid) {
            $point=[System.Windows.Point]::new($center.X,$center.Y)
            $element=[System.Windows.Automation.AutomationElement]::FromPoint($point)
            for ($i=0; $i -lt 24 -and $null -ne $element; $i++) {
                if ($element.Current.ProcessId -ne $TargetPid) { break }
                if (Same-Element $element $Surface.button) { $hitToken=$buttonToken; break }
                $element=$walker.GetParent($element)
            }
        }
        $busy=$false
        foreach ($key in @(1,2,4,5,6,16,17,18,91,92)) {
            if (([int][WechatGeometry]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { $busy=$true }
        }
        $focus='other'
        if ($Observation.focused) { $focus='chat_input' }
        elseif ($Surface.button.Current.HasKeyboardFocus) { $focus='send_button' }
        $after=$Surface.button.Current.BoundingRectangle
        if ($after -ne $bounds) { throw 'geometry_moved_during_observation' }
        $result=@{
            input_token=$Observation.input_token; button_token=$buttonToken
            button_bounds=@($bounds.Left,$bounds.Top,$bounds.Right,$bounds.Bottom)
            client_bounds=@(($origin.X+$client.Left),($origin.Y+$client.Top),($origin.X+$client.Right),($origin.Y+$client.Bottom))
            client_point=@($mapped.X,$mapped.Y); dpi=[int]$dpi; coordinate_space='physical_per_monitor_v2'
            receiver_hwnd=$TargetHwnd; receiver_pid=$TargetPid; receiver_thread=[int]$thread
            foreground_hwnd=[WechatGeometry]::GetForegroundWindow().ToInt64()
            hit_root_hwnd=$hitRoot.ToInt64(); hit_pid=[int]$hitOwner; hit_button_token=$hitToken
            capture_hwnd=$info.Capture.ToInt64(); focus_kind=$focus; enabled=[bool]$Observation.send_enabled
            visible=(-not $Surface.button.Current.IsOffscreen); input_busy=$busy
        }
    } finally {
        $restored=[WechatGeometry]::SetThreadDpiAwarenessContext($old)
        if ($restored -eq [IntPtr]::Zero -or -not [WechatGeometry]::AreDpiAwarenessContextsEqual(
            [WechatGeometry]::GetThreadDpiAwarenessContext(),$old)) { throw 'geometry_dpi_restore_unverified' }
    }
    $result.dpi_context_restored=$true
    return $result
}
