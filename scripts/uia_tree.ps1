# Pure traversal shared by the native collector and synthetic tests. No desktop APIs.
function Read-BoundedUiaTree {
    param($Root, [scriptblock]$Metadata, [scriptblock]$FirstChild,
          [scriptblock]$NextSibling, [int]$MaxDepth=32, [int]$MaxNodes=800)
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@{ element=$Root; depth=0; parent=$null })
    $nodes = New-Object System.Collections.ArrayList
    $errors = New-Object System.Collections.ArrayList
    $reasons = New-Object 'System.Collections.Generic.HashSet[string]'
    while ($queue.Count -gt 0 -and $nodes.Count -lt $MaxNodes) {
        $item = $queue.Dequeue()
        try {
            $row = & $Metadata $item.element
            $row.index=$nodes.Count; $row.parent=$item.parent; $row.depth=$item.depth
            [void]$nodes.Add($row)
            $next = & $FirstChild $item.element
            if ($item.depth -ge $MaxDepth) {
                if ($null -ne $next) { [void]$reasons.Add('depth') }
                continue
            }
            while ($null -ne $next) {
                if (($queue.Count + $nodes.Count) -ge $MaxNodes) {
                    [void]$reasons.Add('nodes'); break
                }
                $queue.Enqueue(@{ element=$next; depth=($item.depth+1); parent=$row.index })
                $next = & $NextSibling $next
            }
        } catch {
            [void]$errors.Add(@{ depth=$item.depth; type=$_.Exception.GetType().FullName; hresult=$_.Exception.HResult })
        }
    }
    if ($queue.Count -gt 0) { [void]$reasons.Add('nodes') }
    return @{ nodes=@($nodes.ToArray()); errors=@($errors.ToArray()); bounded=($reasons.Count -gt 0);
              truncation_reasons=@($reasons | Sort-Object) }
}
