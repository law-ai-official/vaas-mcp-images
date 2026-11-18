## 1. 代码：图像产物形态

- [x] 1.1 `scripts/_ciyuan_image.py`：`data[]` 解析从「只认 `b64_json`」改为
      「`b64_json` 优先、缺失时取 `url`」；url 分支下载到 `output_paths[i]`
- [x] 1.2 url 下载的硬约束：仅 http/https、连接/读取超时、体积上限（默认与
      车道既有产物体积同量级）、`Content-Type` 与图像魔数双重校验；
      任一不符抛错并保留上游 URL 的主机名（不含查询串，避免签名泄漏）
- [x] 1.3 响应保持既有形状（`ok/provider/model/images[].local_path/file_size_bytes`），
      MUST NOT 把上游 URL 直接回给调用者
- [x] 1.4 单测（`tests/` 既有风格）：b64 形态回归、url 形态落盘、url 返回 HTML 失败、
      url 超体积失败、`data` 为空失败

- [x] 1.5 b64 形态补同等级校验：解码后 MUST 过魔数识别，认不出即失败且不写盘
      （旧代码对 b64 零校验，base64 的 HTML 会被写成打不开的假图）；`images[]` 带 `format`
- [x] 1.6 `generators/image.py`：扩展名取**实际容器**而非请求的 `output_format`，
      不一致时在 `warnings` 披露（旧行为会产出「叫 .jpeg 的 PNG」，下游按扩展名选上传类型）

- [x] 1.7 契约外容器（tiff/heic/avif）：产物保留 + 如实命名 + `warnings` 点名，
      MUST NOT 丢弃（丢弃=上游白计费）也 MUST NOT 谎报扩展名（见 design D6）

## 2. 换上游（数据，不出片）

- [ ] 2.1 取一条可用的图像上游凭证：组 19 `fed-workbuddy-media`（acct 57，
      ⛔ 需要组 19 的 key 归属决定（新建 VAAS 专用 / 复用 federation@）
      `grok-imagine-image`，`/v1/images/generations`）；确认该分组是否允许
      VAAS 所在账号使用（组 19 `is_exclusive=f`，但密钥属 `federation@` 用户，
      需决定是新建一把 VAAS 专用 key 还是复用）
- [ ] 2.2 写入 vault `vaas/pool/wanxing/image`：`{provider: ciyuan, base_url: <词元网关>,
      ⛔ 需要 vault 写权限（并保留旧记录以便回滚）
      api_key: <组19 key>, model: grok-imagine-image}`；保留一份旧记录以便回滚
- [ ] 2.3 用真实 `generate_image` 打一发（不是 `registry_list_generators` 读状态），
      ⛔ 依赖 2.2 的凭据
      以「产物落盘 + 形状合规」为通过判据

## 3. 台账与镜像

- [ ] 3.1 实调通过后改 `mcp-server/mcp_server/lane_health.json`：
      ⛔ 依赖 2.3 的实调证据（MUST NOT 无证据翻 active）
      `image.ciyuan` → `active`，`last_verified` 写当日，`reason` 写明生效的
      provider/model 与分组；`video.ciyuan` 保持 `down`（同一被封账号未解）
- [ ] 3.2 出片并滚到 china-<node-a>（`VAAS_HOSTED=1` 那台）；判据 = 容器内
      ⛔ 需要构建通道 + china-<node-a> 的 shell（<render-node-a> 现为 Permission denied）
      `lane_health.json` 内容 + 公网 `registry_list_generators` 回读
- [ ] 3.3 滚版后复跑一次 `generate_image`，确认走的是新车道
      ⛔ 依赖 3.2

## 4. 结构成因与台账订正

- [x] 4.1 `docs/debt-ledger.md`：DEBT-LANE-9-01 补记结构成因「组 9 只有一个上游账号、
      `fallback_group_id` 为空 ⇒ 单点」，并记下组 19 这条可用替代车道与其成本（2K/$0.02）
- [x] 4.2 补一条「车道状态自证循环」的说明：`down` 会阻断上游探测，`last_verified`
      不会自己前进；复验是人的动作，不是系统行为
- [x] 4.3 视频车道 `seedance-ciyuan` 的处置：与图像**同源**（词元组 9 唯一账号 acct 40），
      因此不在本 change 内单独翻状态——账号替换/解封后由同一次实调一并恢复。
      若将来只恢复图像（换分组）而视频仍走 acct 40，则两车道必须分别记账，
      MUST NOT 因为图像 active 就把视频也标成可用

## 5. 验证

- [x] 5.1 `openspec validate add-image-lane-fallback --strict` 通过
- [x] 5.2 单测全绿；`scripts/_ciyuan_image.py` 对新形态有红证（先让用例在旧代码上失败）
- [ ] 5.3 现网回读：`registry_list_generators` 的 `image.ciyuan` 为 active 且
      ⛔ 依赖 3.2 滚版
      `last_verified` 是本次实调日期

## 6. 复验探针（把手工配方变成工具）
- [x] 6.1 `scripts/probe_image_lane.py`：复用真实 `_ciyuan_image.generate`（不是平行实现），
      输出三态 `restored` / `still-blocked` / `cannot-judge`，退出码 0/1/2
- [x] 6.2 判据方向：**4xx 一律 `cannot-judge`**（401 key 无效、400 模型不受支持都指向
      我们自己的配置）；只有 200+HTML、5xx、`data[]` 空或形态不合规才算 `still-blocked`；
      网络不通单独记 `network`，不许当成「上游仍封着」
- [x] 6.3 安全：key 只从环境变量读，不外泄到输出；报错只带主机名不带查询串
- [x] 6.4 离线用例 9 条（三态各自成立 + 互不冒充 + key 不出现在输出里）；真打
      `router.finddatatech.cloud` 冒烟一次（无效 key → `credential_rejected`，不消耗额度）
- [ ] 6.5 拿到可用 key 后跑一次真复验，把结论回填 `docs/debt-ledger.md` 的
      ⛔ 依赖 2.2 的凭据
      DEBT-LANE-9-01-REVERIFY 行（前置=任务 2 的凭证决定）

## 7. 台账陈旧度（本轮新增）

- [x] 7.1 `mcp-server/mcp_server/lane_health.py`：台账条目自带陈旧度——
      `DEFAULT_STALE_AFTER_DAYS = 7`，支持条目级 `stale_after_days` 覆盖；
      `lane_health()` 视图新增 `age_days` / `stale`，随 `annotate()` 一路到发现面
- [x] 7.2 判不出不许当新鲜：时间戳缺失或不可解析 → `stale: null`（不是 `false`）
- [x] 7.3 8 条离线用例（新鲜/超期/条目级覆盖/坏日期/缺日期/未知车道/发现面带出/`lane_down` 不受影响）
- [x] 7.4 用真实台账复核：`voice.ciyuan` 为 `active` 但已 8 天未复验 → `stale: true`
      （**这正是陈旧 `active` 的形态：它会被当成「能用」推荐出去**）；
      `image/video.ciyuan` 3 天 → 未超期，不误报
- [ ] 7.5 复验周期是否要按车道类型再分档（小时级更短）、以及发现面展示文案，
      ⛔ 需要先有一次真实复验来观察判定形态，再定分档
      留给实体检后定；当前只做「标出来」不做「自动降级」

## 8. grok 车道（组 19 /v1/videos 协议族，2026-11-15 扩展）

- [x] 8.1 `scripts/_grok_video.py`：四函数接口（submit/poll/status_once/download）
      与 `_ciyuan_video.py` 同名同签名；打 `/v1/videos` 协议族。三个实测事实写死：
      终态 `completed`→`succeeded`（兼容 `done`）、取件 URL 是相对路径
      （下载拼 base_url 并带 Authorization；绝对签名 URL 免鉴权直取）、
      MP4 `ftyp` 魔数通过才写盘
- [x] 8.2 时长夹紧 [3,15]s：越界夹到边界并在 `warnings` 点名原值→生效值，
      不静默改写也不硬拒（design D8）；上游无视请求分辨率同理披露
- [x] 8.3 桥接器 `scripts/litellm-bridge.py`：image 分支 `provider=grok` 委托
      `_ciyuan_image.generate`（凭据 `GROK_API_KEY`/`GROK_BASE_URL`，回落
      `CIYUAN_*`）；video 分支走新驱动，两阶段防重复计费语义
      （`--async` 只提交 / `--status` 只查态）原样保留；两条路的
      `_reject_provider` 文案列出 grok
- [x] 8.4 `lane_health.json`：`image.ciyuan` → `active`，实调证据 =
      台账行 386469（acct 57 / 组 19 / `grok-imagine-image` / $0.02 / 真产物落盘），
      `reason` 披露「请求名 grok-imagine-image、实际 hunyuan-image-alpha 承做」
      的重映射；组 9 的 DEBT-LANE-9-01 不因此销账（车道身份跟着凭据组走，design D7）
- [x] 8.5 单测（`tests/test_grok_video_lane.py` 13 条，全离线）：状态映射三向 /
      相对 URL 拼接与鉴权头 / 绝对 URL 免鉴权 / 非 MP4 不写盘 / 时长夹紧三向 /
      submit 缺 key·base_url 拒绝 / generate_and_wait 全链 + 分辨率披露 /
      桥接器三向（grok 图像委托+凭据回落 / grok 视频两阶段无 POST / 未知 provider 文案）。
      台账翻转牵出的 4 条旧断言（test_correctness_c1）已跟着台账改：
      可用面断言跟上现实，lane_down 拒绝标本改用仍 down 的 video.ciyuan
- [x] 8.6 词元侧授权——**裁决：不动 user 1 的权限面**。一把密钥只绑一个分组
      （spec 既有约束），user 1 的现役钥 162 在组 9，本来就不能服务组 19；
      给 user 1 补 `user_allowed_groups` 只在「复用 user 1 再铸新钥」时有意义。
      VAAS 专用钥应铸在 **user 128**（已在组 19 授权名单 `128→19`）名下，
      与 probe key 181 同法（app 写路径 + AES-256-GCM 补密文列），不动既有用户授权。
- [ ] 8.7 vault 凭据记录（`vaas/pool/wanxing/video`：`{provider: grok, ...}`）
      ⛔ vault 写权限 permission denied（OpenBAO <render-node-a>:8200 未封印、可达，
      但本机无 `bao`/`vault` CLI 且 VAAS `.env` 无 `OPENBAO_TOKEN`；写入需一个
      path 受限 policy token，出处见 deploy/README「解封配方」的 root-only 文件），
      与 2.2 同一阻塞。**凭据本身已备好**（见 8.8a），只差写进 vault 这一步
- [x] 8.8 视频车道实调销账（2026-11-15 真调通过）：经 **真实驱动**
      `scripts/_grok_video.py` 打 `/v1/videos` 全链 —— 任务 `vid_84655a2f5a1f13ee`
      → 真 MP4 落盘 3,140,407 B / h264 1280x720 + aac / 3.04s（ffprobe 独立核过），
      上游自报承做 `kling-video-v3`、`seconds:3`、`resolution:720P`。两条披露均真实
      触发：请求 1s 被夹紧 3s、请求 480p 实得 720P。凭证=组 19 专用钥（api_keys
      id 182 / user 128，`last_used_at`=2026-11-14 19:48:56Z ⇒ 密文鉴权路径已通）。
      台账 `video.grok` → active（reason 内含证据与「视频完成不落 usage_logs」这条
      上游缺陷的实测复现）；`image.grok` 记 **degraded**（同一上游已由 image.ciyuan
      条目带证据，但 provider=grok 直连档尚无独立实调——不借证据翻绿）
- [x] 8.8a 词元侧专用钥已就位：`api_keys id 182`（user 128 / group 19 / active /
      name `vaas-media-grok`；`key_hash`+`key_encrypted` 两列齐备，明文不落仓、
      不落文档）。它就是把 8.7 的 vault 记录写进 `vaas/pool/wanxing/{image,video}`
      时要填的那把钥——vault 通了直接取用，不必重铸
- [x] 8.9 `openspec validate add-image-lane-fallback --strict` 重跑通过
      （中途修过一处 delta 结构：新 requirement 必须在 `## ADDED Requirements` 段、
      且全文件只许一个 `## MODIFIED Requirements` 段——两个 MODIFIED 段 archive 只应用最后一个）
- [x] 8.10 registry 发现面挂 grok 条目：`seedream-grok`/`seedance-grok`
      （provider=grok，model 占位 `${GROK_IMAGE_MODEL}`/`${GROK_VIDEO_MODEL}`，
      通用 `${ENV}` 解析已覆盖；车道闸对未知车道 degraded 放行，凭据入 vault 前即可见不可用）
- [x] 8.11 图像车道实调销账（2026-11-15）：`provider=grok` **独立一次**，不经
      `image.ciyuan` 借证 —— 经 `scripts/litellm-bridge.py image` 打组 19，真产物
      1,500,323 B / PNG 1024x1024 8-bit RGB，`api_latency_ms` 20271；**并且真记账**
      （`usage_logs` 1 行：`grok-imagine-image` / `image_count=1` / $0.02 /
      `client:81685aa3-…`）。凭证复用 8.8a 的 id 182（从密文解回并核 sha256 前缀
      `87460efa6380` 相等，未另铸新钥；明文用后即碎）。`lane_health.json`
      `image.grok` → `active`，并把「承做方会漂」「图片有记账而视频没有」两条披露
      写进条目；`annotate()` 实测 `seedream-grok` 带 `lane.health=active / stale=false`。
      MCP 侧单测 537 passed / 15 skipped
- [x] 8.12 vault→env 注入层补 grok 档（2026-11-16）：`credentials.py::subprocess_env`
      此前只认 volcengine/ciyuan ⇒ 按 8.7 的计划把 `provider: grok` 写进 vault，会在
      **真正注入凭据那一步**抛 `unsupported provider 'grok'`。而 8.4/8.8/8.11 已把
      `image.grok`/`video.grok` 判成 active、8.10 已挂出 `seedream-grok`/`seedance-grok`
      ——**台账与注册表都说这条车道能用，唯独这一层没有它的档位**，只会在部署当刻炸；
      桥接器单测直接喂 `VIDEO_PROVIDER=grok`，正好绕过这一层（同族于「两层各自都对、
      之间没人比」）。现补 `_GROK_ENV`：`GROK_API_KEY` / `GROK_BASE_URL` /
      `GROK_IMAGE_MODEL` / `GROK_VIDEO_MODEL` + `<KIND>_PROVIDER=grok`；grok 无 TTS 档
      显式拒绝（不静默落进 volcengine），未知档位报错文案改列 `volcengine, ciyuan, grok`。
      改前实测 image/video 两个 kind 都抛、改后逐键核对 env（并断言不混进 `CIYUAN_API_KEY`/
      `VIDEO_MODEL`——混进就会走被封的组 9 车道）；新增 4 条用例，`scripts/ci-local.sh`
      541 passed / 15 skipped 全绿。
