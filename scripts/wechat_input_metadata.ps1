# Pure, lossy projection: never return raw UI IDs, names, help or draft text.
function Get-WechatCandidateChecks([string]$InputName,[string]$RowName,[string]$Aid,[bool]$Enabled) {
    $helper=[string]::Join('',[char[]](0x6587,0x4EF6,0x4F20,0x8F93,0x52A9,0x624B))
    return @{
        input_name_match=($InputName -ceq $helper -or $InputName -ceq 'File Transfer')
        row_name_match=$RowName.Contains($helper)
        input_id_match=($Aid -cmatch '(^|\.)chat_input_field$')
        input_enabled=$Enabled
    }
}

function Get-WechatInputRole($Aid, $ClassName, $TypeName, $NameValue) {
    $sendName = [string]::Join('', [char[]](0x53D1,0x9001))
    $helperName = [string]::Join('', [char[]](0x6587,0x4EF6,0x4F20,0x8F93,0x52A9,0x624B))
    $infoNames = @(
        [string]::Join('', [char[]](0x804A,0x5929,0x4FE1,0x606F)),
        [string]::Join('', [char[]](0x8BE6,0x7EC6,0x4FE1,0x606F)),
        [string]::Join('', [char[]](0x67E5,0x770B,0x8D44,0x6599)),
        [string]::Join('', [char[]](0x804A,0x5929,0x8BE6,0x60C5))
    )
    $role = 'other'
    if ($Aid -cmatch '(^|\.)chat_input_field$') { $role = 'chat_input' }
    elseif ($Aid -cmatch '(^|\.)session_list$') { $role = 'session_list' }
    elseif ($ClassName -eq 'mmui::ChatTitleBarChatSingleView') { $role = 'chat_title' }
    elseif ($TypeName -eq 'ControlType.Button' -and
            ($NameValue -ceq $sendName -or $NameValue -ceq ($sendName + '(S)') -or $NameValue -ceq 'Send')) {
        $role = 'send_button_candidate'
    }
    elseif ($TypeName -eq 'ControlType.Button' -and $NameValue -cin $infoNames) { $role='chat_info_candidate' }
    $md5 = [Security.Cryptography.MD5]::Create()
    try { $helperMd5 = -join ($md5.ComputeHash([Text.Encoding]::UTF8.GetBytes('filehelper')) | ForEach-Object { $_.ToString('x2') }) }
    finally { $md5.Dispose() }
    return @{
        role=$role
        automation_id_present=([bool]$Aid)
        automation_id_filehelper_token=([bool]($Aid -cmatch '(^|[./:_-])filehelper($|[./:_-])'))
        automation_id_filehelper_md5=([bool]($Aid -match ('(?<![0-9A-Fa-f])' + $helperMd5 + '(?![0-9A-Fa-f])')))
        display_is_filehelper=([bool]($NameValue -ceq $helperName -or $NameValue -ceq 'File Transfer'))
    }
}

function Protect-WechatDraft([string]$Text, [int]$Limit=2000) {
    if ($Text.Length -gt $Limit) { return @{ status='too_long'; empty=$false; length_bound=$Limit } }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)) | ForEach-Object { $_.ToString('x2') }) }
    finally { $sha.Dispose() }
    return @{ status='read'; empty=($Text.Length -eq 0); length=$Text.Length; sha256=$hash }
}

function Protect-WechatSessionPayload([string]$Aid) {
    $hasMarker = $Aid.Contains('session_item_')
    $result = @{ marker_present=$hasMarker; canonical_word_anywhere=$Aid.Contains('filehelper') }
    if ($hasMarker) {
        $payload=$Aid.Substring($Aid.LastIndexOf('session_item_')+13)
        $result.shape=@{
            length=$payload.Length; decimal=($payload -cmatch '^\d+$'); hex=($payload -cmatch '^[a-fA-F0-9]+$')
            has_wxid_prefix=$payload.StartsWith('wxid_'); canonical_word_present=$payload.Contains('filehelper')
            ascii_separator_count=([regex]::Matches($payload,'[\x20-\x2F\x3A-\x40\x5B-\x60\x7B-\x7E]').Count)
            non_ascii_count=([regex]::Matches($payload,'[^\x00-\x7F]').Count)
        }
    }
    return $result
}

function Protect-WechatDraftPair([string]$Value, [string]$Text, [bool]$TextSupported) {
    $valueShape=Protect-WechatDraft $Value
    if ($valueShape.status -ne 'read') { throw 'value_bound' }
    $textShape=@{status='unsupported';sha256=$null;length=$null;empty=$null}
    if ($TextSupported) { $textShape=Protect-WechatDraft $Text }
    if ($textShape.status -cne 'read') {
        $textShape=@{status=$textShape.status;sha256=$null;length=$null;empty=$null}
    }
    $comparable=($textShape.status -ceq 'read')
    return @{
        value_sha256=$valueShape.sha256; value_length=$valueShape.length; value_empty=$valueShape.empty
        text_status=$textShape.status; text_sha256=$textShape.sha256; text_length=$textShape.length; text_empty=$textShape.empty
        exact_equal=($comparable -and $Value -ceq $Text)
        trailing_linebreaks_only_difference=($comparable -and $Value -cne $Text -and $Value -ceq $Text.TrimEnd([char[]]"`r`n"))
    }
}
