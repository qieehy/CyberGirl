# 私聊媒体识别

2026-11-18：原接收器只接纳 local_type=1 文本，图片(3)、语音(34)、表情包(47)被记作不支持消息。新增显式 `--media`，只作用于已选中的私聊，不改变群历史读取器或默认文字模式。

已实现：

- 图片：准确关联 message_resource 索引，读取当前会话附件；在本机解密 V2 文件、解码 wxgf/HEVC 或常见图片，去除元数据、缩小后作为 input_image 交给现有回复模型。
- 表情包：按消息中的精确 MD5 定位当前账号缓存，解密本地/CDN 加密文件，抽取最多三帧供看图。未缓存时明确报告；不跟随消息中的 URL 下载。
- 语音：只查询当前 peer、local_id 和时间匹配的 VoiceInfo，SILK 在本机转为 PCM，使用本地 Whisper base（CPU int8）转写，再以文本交给回复模型。原始音频不上传。当前支持约60秒以内语音，以中文转写为主。
- 媒体与文字使用相同的每天30次上限和每来源一次草稿规则。启动前旧消息不补回复。解码失败的单条消息不重试，后续文字仍继续。
- 缓存结果只保存类型、转写、状态和错误码；不保存解密密钥、原始图像、原始音频或含凭据的媒体 URL。读源核对账号、peer、数据库文件身份、local_id、序号和时间。

安装：`pip install -e ".[wechat-read,wechat-media]"`。本地语音模型目录为 `.state/models/whisper-base`；模型在启动前下载，运行转写使用 local_files_only。

使用：在原有已授权私聊命令后添加 `--media`，不增加模型或发送次数。首次启用前须明确批准将该 peer 的新图片/表情包图像、语音转写交给模型；原先持续聊天授权仅包括新文本。

用户随后明确批准上述媒体范围，已按原账号、原peer、原gpt-6.1-sol和每天30次范围启动媒体模式，reading/auto_reply/media_enabled均true。旧媒体不补回复，真实看图结果仍需以之后自然入站的实际请求为准。

本地已使用现有 peer 的三条媒体完成验证：表情包、wxgf 图片均可解码为 JPEG；语音完成离线转写。开发中的外部模型调用0、微信发送0。关键模拟测试覆盖媒体请求、输入边界、去重、媒体失败后继续文字及原有模型链路；尚未据此宣称真实看图请求成功或自动回复已启用媒体。

格式研究参考：[微信媒体实现](https://github.com/fanyuantaier/wechatauto-replica/blob/main/wechatauto/media.py)、[表情缓存格式](https://github.com/yanyanbabyi/Wechat-Emoticon-Parser-fix)、[本地语音转写](https://github.com/SYSTRAN/faster-whisper)、[SILK 解码](https://github.com/synodriver/pysilk)。实现只提取所选消息，不运行参考项目的批量导出或下载逻辑。
