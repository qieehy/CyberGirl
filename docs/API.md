# 前后端接口约定

浏览器只请求同源 `/api/*`。生产后端默认 `http://127.0.0.1:8765`；开发前端默认5173，代理至后端，业务路径和JSON内容不改变。

## 认证与请求

- 后端启动打印含 `#auth=<token>` 的链接；共享前端客户端读取fragment，写入该标签页sessionStorage并从地址栏清除。
- API请求带 `Authorization: Bearer <token>`；POST使用`Content-Type: application/json`。
- POST要求同源Origin；开发代理只接受它自身来源，再向固定本机后端转发对应Origin。后端继续校验Host和Origin。
- 静态构建资源不含令牌或本机数据，只允许访问清单声明的文件。
- 客户端不自动重试任何请求，尤其是生成、发送和配置变更。

## 路由分组

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/wechat/state` | 当前普通私聊状态与能力 |
| `GET /api/wechat/preferences`、`POST /api/wechat/config` | 回复参数与版本控制 |
| `POST /api/wechat/rules`、`POST /api/wechat/rules/preview` | 关键词规则与只读预览 |
| `GET /api/wechat/models`、`POST /api/wechat/model` | 账号模型列表与显式切换 |
| `GET/POST /api/persona`、`GET /api/wechat/personas` | 人格与版本 |
| `POST /api/wechat/auto-reply` | 自动回复开关 |
| `POST /api/wechat/pause`、`POST /api/wechat/disconnect` | 全局暂停／断开 |
| `POST /api/wechat/drafts` | 当前范围的一次模型草稿 |
| `POST /api/wechat/local-drafts`、`POST /api/wechat/local-drafts/preview` | 本地草稿与预览 |
| `/api/wechat/drafts/<id>`及`/edit`、`/cancel` | 草稿读取、编辑与取消 |
| `POST /api/wechat/private-send` | 已装载发送器的指定私聊发送 |
| `GET /api/wechat/companion` | 陪伴状态、记忆、任务及共享额度 |
| `POST /api/wechat/companion/config` | 人物卡、角色状态、记忆与主动联系设置 |
| `POST /api/wechat/companion/memory`、`/forget`、`/clear` | 记忆增改、遗忘／恢复、全部遗忘 |
| `POST /api/wechat/companion/preview`、`/summary`、`/pause` | 只读召回、一次摘要、一键暂停 |
| `GET /api/wechat/logs` | 管理日志与计数 |
| `POST /api/wechat/group-history` | 指定群历史缓存读取／只读更新 |
| `GET /api/state`及沙盒`/api/messages`、`/api/dispatch` | 离线模拟工作台，仅沙盒服务支持 |

已有逐条确认、投递核对与沙盒群接口保持原业务实现；不是所有模式都开放表内每个能力。浏览器根据状态响应中的能力字段显示或禁用入口。

## 变更、失败与一致性

配置请求携带`expected_revision`，人格用`expected_version`，模型用`expected_model`。版本变化返回409，页面应刷新后由用户重新决定；不能自动覆盖新版本。

生成／发送沿用`request_id`和来源唯一约束。网络异常不表示没有执行，不可直接新建请求补发；先读取原状态。`UNKNOWN`、`UNCONFIRMED`与本机回显保持原有含义。

错误体为`{"error":"machine_code"}`。常见状态：400格式或能力不符、401缺少令牌、403来源不符、404接口不存在、409版本冲突、429已有任务、408超时。不同模块的具体码由领域层提供，前端集中解释，不解析异常堆栈。

## 持续读取订阅

普通微信工作台与独立history服务支持以下同源认证接口：

| 路由 | 内容 |
| --- | --- |
| `GET /api/wechat/reads` | 订阅及读取状态、最近成功／错误、下次读取时间 |
| `POST /api/wechat/reads/add` | `{kind: "group"或"dm", name: 完整名称}`，首次成功核验才登记 |
| `POST /api/wechat/reads/control` | `{id, enabled: 布尔, remove: 布尔}`，暂停／恢复／移除 |
| `POST /api/wechat/reads/messages` | `{id}`，读取该订阅的最近200条本地缓存 |

最多20项；新订阅只读，不接入自动回复。`read_manager_in_use`表示同一账号已有另一服务管理订阅。完整行为见[持续读取](CONTINUOUS_READS.md)。
