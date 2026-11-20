$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/wechat_input_metadata.ps1')
$helper = [string]::Join('', [char[]](0x6587,0x4EF6,0x4F20,0x8F93,0x52A9,0x624B))
$send = [string]::Join('', [char[]](0x53D1,0x9001))
$info = [string]::Join('', [char[]](0x804A,0x5929,0x4FE1,0x606F))
$secret = 'SYNTHETIC-DRAFT-DO-NOT-SERIALIZE'
$results = @(
    (Get-WechatInputRole 'client.chat_input_field' 'mmui::ChatInputField' 'ControlType.Edit' $secret),
    (Get-WechatInputRole 'session_list' 'mmui::XTableView' 'ControlType.List' ''),
    (Get-WechatInputRole 'row.filehelper' 'mmui::ChatSessionCell' 'ControlType.ListItem' $helper),
    (Get-WechatInputRole 'row.fakefilehelperperson' 'mmui::ChatSessionCell' 'ControlType.ListItem' $helper),
    (Get-WechatInputRole '' 'mmui::XButton' 'ControlType.Button' $send),
    (Protect-WechatDraft ''),
    (Protect-WechatDraft $secret),
    (Protect-WechatDraft ('x' * 2001)),
    (Get-WechatInputRole '' 'mmui::XButton' 'ControlType.Button' $info),
    (Get-WechatInputRole 'session_item_9e20f478899dc29eb19741386f9343c8' 'mmui::ChatSessionCell' 'ControlType.ListItem' ''),
    (Get-WechatInputRole '9e20f478899dc29eb19741386f9343c80' 'mmui::ChatSessionCell' 'ControlType.ListItem' ''),
    (Protect-WechatSessionPayload ('prefix.session_item_' + $helper)),
    (Protect-WechatDraftPair $secret $secret $true),
    (Protect-WechatDraftPair $secret '' $true),
    (Protect-WechatDraftPair '' "`r`n" $true),
    (Protect-WechatDraftPair $secret '' $false),
    (Protect-WechatDraftPair $secret ('x' * 2001) $true),
    (Get-WechatCandidateChecks $helper $helper 'client.chat_input_field' $true),
    (Get-WechatCandidateChecks $secret $secret $secret $false)
)
$parseErrors = $null; $tokens = $null
$path = Join-Path $PSScriptRoot '../scripts/probe_wechat_input.ps1'
[void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Native collector syntax invalid.' }
$results | ConvertTo-Json -Depth 8 -Compress
