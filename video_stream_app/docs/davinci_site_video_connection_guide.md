# 达芬奇视频采集连接与采购说明

更新时间：2026-07-15

适用范围：当前提供的达芬奇接口照片，以及服务器内已经安装的
`DeckLink Mini Recorder 4K` 采集卡。

> 重要说明：根据接口排列、`TilePro`、`Video Out L/R` 和 `DVI (SXGA)`
> 等标识判断，照片很像 **da Vinci Si 医生控制台背面的接口区**。以下对照片中
> 接口用途的判断可信度较高，但正式接线前仍应由医院设备科或 Intuitive 工程师
> 根据机器型号确认。不要拔除照片中已有的连接线。

![达芬奇机器接口照片](../../达芬奇机器接口.jpg)

## 一句话结论

1. 照片右下方标有 `Video Out L` 的 `DVI (SXGA)` 是左眼视频**输出口**。
2. 照片中已经插线的 DVI 位于左侧蓝色输入区域，应该是 `TilePro Input R`，
   它是把外部画面送入达芬奇的**输入口**，不是我们要采集的输出口。
3. 照片上方两个 SDI 也属于 `TilePro Input L/R`，是**输入口**，不能直接拿来
   给 DeckLink 采集。
4. 正式现场的首选方案不是使用照片中的 DVI，而是找到 Vision Cart/Core/CCU
   上可用的 **HD-SDI Video Out**，用一根 75 欧姆 BNC SDI 线直接接到服务器
   DeckLink。采用这种方案时，**不需要 HDMI to SDI 转换器**。
5. `Micro Converter HDMI to SDI 3G wPSU` 主要用于实验室把第二台电脑的 HDMI
   输出模拟成 SDI。它不是正式连接达芬奇 SDI 输出时的必需设备。

## 如果现场确实只有照片中的接口

此时仍然可以使用达芬奇的视频输出，但不是直接裸接：

```text
空着的 Video Out L：DVI (SXGA)
        ↓
支持 SXGA 输入的有源视频缩放器
将 1280 x 1024 缩放为 1920 x 1080p59.94/60
        ↓ HDMI
服务器 DeckLink Mini Recorder 4K 的 HDMI IN
```

也就是说：

- **使用右下方空着的 `Video Out L`**，不是使用照片中已经插线的 DVI。
- **不使用上方两个 SDI**，因为它们是 TilePro 输入口。
- **不能依赖普通 DVI 转 HDMI 被动线直接采集**。DVI 与 HDMI 的数字信号在电气
  层面相近，但当前 DeckLink 驱动报告的输入模式中没有 1280 x 1024 SXGA；普通
  线缆也不会改变分辨率。
- 中间设备必须明确支持 `1280 x 1024 @ 60 Hz` 输入，并能固定输出 DeckLink
  支持的 `1080p59.94/60`、`1080p30` 或 `720p59.94`。
- `Micro Converter HDMI to SDI 3G wPSU` **不能单独解决这个问题**：它没有 DVI
  输入，也不是分辨率缩放器。

如果服务器距离达芬奇较近，缩放器输出 HDMI 后直接进入 DeckLink HDMI 是设备
最少的方案。如果布线距离较长，应选择直接带 HD-SDI 输出的 DVI/SXGA 缩放器，
再用 75 欧姆 SDI 线进入 DeckLink，避免串联多个小转换盒。

## 一、照片中的接口分别是什么

照片中的接口从左到右可以理解为下表：

| 位置 | 面板分组 | 可见接口 | 信号方向 | 主要用途 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| 第 1 列 | TilePro Input L | SDI、S-Video、DVI | 输入达芬奇 | 把外部左眼/辅助画面送入医生控制台 | 未接线 |
| 第 2 列 | TilePro Input R | SDI、S-Video、DVI | 输入达芬奇 | 把外部右眼/辅助画面送入医生控制台 | DVI 已接线 |
| 第 3 列 | Video Out L | DVI (SXGA) | 从达芬奇输出 | 输出左眼手术画面 | 照片中未接线 |
| 第 4 列 | Video Out R | DVI (SXGA) | 从达芬奇输出 | 输出右眼手术画面 | 照片中未接线 |
| 最右侧 | Audio | Line Out、Line In、Headset | 音频输入/输出 | 传输音频 | 与本次视频采集无关 |

一个 TilePro 输入仓虽然同时提供 SDI、S-Video 和 DVI 插座，但它们表示该输入仓
可兼容不同信号类型，不表示这些插座都是视频输出。

## 二、逐项回答

### 1. `DVI (SXGA)` 的 L 是输出口吗？

**是。** 照片右下方写有 `Video Out L` 的 `DVI (SXGA)` 是左眼视频输出；旁边
`Video Out R` 是右眼视频输出。

对我们的单目视频分析而言，L 或 R 取其中一路即可，通常先测试 L。但这并不代表
我们应立即决定使用这个接口，原因如下：

- `SXGA` 通常是 1280 x 1024 的计算机显示格式，不是常见的 16:9 广播视频格式。
- DeckLink Mini Recorder 4K 主要接收标准 SD、HD 和 UHD 视频制式，不应在未经
  实测时假定它能直接采集 SXGA。
- 简单的 DVI 转 HDMI 线只改变接头形状，不会把 SXGA 缩放成 720p 或 1080p。

因此，`Video Out L` 可以输出，但现场首选仍是 Vision Cart/Core/CCU 上的
标准 HD-SDI 视频输出。

### 2. 照片里已经插着的 DVI 是做什么的？

从接口位置判断，这根线插在第二个蓝色 `TilePro Input` 输入仓，而不是右侧橙色
`Video Out L/R` 输出仓。

它的作用应该是把某个外部设备的画面输入达芬奇，例如：

- 超声设备；
- 导航或影像工作站；
- 其他教学、监护或辅助视频源。

仅凭照片无法确认这根线的另一端连接了什么设备，必须沿线检查。**不要为接入采集
系统而拔掉它。** 它与我们从达芬奇取得手术视频不是同一条链路。

### 3. 上面的两个 SDI 接口是做什么的？

照片上方的两个 SDI 位于 `TilePro Input L/R` 输入仓，是将外部 HD-SDI 视频送入
达芬奇医生控制台的输入接口。它们不是手术图像输出口。

如果把这里的 SDI 与服务器 DeckLink 的 SDI 输入相连，就会形成“输入接输入”，
不会得到视频信号。

我们真正需要寻找的是 Vision Cart/Core/CCU 上明确标为以下名称之一的接口：

- `Video Out 1`
- `Video Out 2`
- `Video Out Aux`
- `HD-SDI Out`
- 其他由设备工程师确认的辅助视频输出

### 4. `Micro Converter HDMI to SDI 3G wPSU` 是什么？

产品的正确名称是：

`Blackmagic Micro Converter HDMI to SDI 3G wPSU`

- `HDMI to SDI`：把 HDMI 输入转换为 SDI 输出。
- `3G`：支持最高到 3G-SDI，包括常见的 720p、1080i 和最高 1080p60 制式。
- `wPSU`：包装内包含电源适配器（with Power Supply Unit）。

它不负责理解或生成手术画面，也不是任意分辨率的缩放器。它主要解决这个问题：

```text
普通电脑只有 HDMI 输出
        ↓
HDMI to SDI 3G 转换器
        ↓
得到可供 DeckLink 测试的 SDI 信号
```

Blackmagic 官方标价为 85 美元，国内实际价格取决于经销商。它比普通转接线贵，
是因为它是带时钟恢复的有源广播信号转换器，而不是被动接头。

## 三、如果只考虑医院现场，应该买什么

### 推荐方案：达芬奇 HD-SDI 直接进入 DeckLink

```text
da Vinci Vision Cart/Core/CCU
HD-SDI Video Out L 或 R
        │
        │ 75 欧姆 BNC 公对公 SDI 线
        ▼
服务器 DeckLink Mini Recorder 4K 的 SDI IN
        ▼
本地视频分析软件
```

这种情况下建议购买：

| 物品 | 建议 | 说明 |
| --- | --- | --- |
| 75 欧姆 3G/6G-SDI BNC 公对公线 | **购买** | 主线一根，备用一根；不能用 50 欧姆射频线替代 |
| 5 米 SDI 线 | 建议购买 | 用于服务器距离机器较近的情况 |
| 10 米或按现场测量的 SDI 线 | 建议购买 | 作为较长主线或备用线 |
| HDMI to SDI 3G wPSU | **现场通常不买** | 达芬奇已经输出 SDI 时不需要再次转换 |
| 普通 DVI 转 HDMI 被动线 | 暂不购买 | 不保证 DeckLink 能接受 SXGA 分辨率 |
| 有源 DVI/SXGA 缩放器 | 暂不购买 | 只有确认没有可用 SDI 输出后才考虑 |
| 有源 SDI 分配器 | 按需购买 | 仅当唯一 SDI 输出已被其他设备占用时使用 |

标准 SDI 线两端都是 BNC 公头；达芬奇输出口和 DeckLink 输入口通常都是 BNC
母座。购买时应明确写明：

```text
75Ω、3G-SDI 或 6G-SDI、BNC 公对公、支持 1080p60
```

不能使用无源 BNC 三通把一路 SDI 强行分成两路。如果输出已经被录像机或显示器
占用，应使用带供电和时钟恢复的 SDI Distribution Amplifier。

### 备选方案：现场只有 DVI (SXGA) 输出

如果医院设备工程师确认 Vision Cart/Core 没有可用 HD-SDI 输出，只能使用照片中
的 `DVI (SXGA) Video Out L`，需要先测出其实际信号：

```text
实际分辨率、刷新率、数字/模拟 DVI、是否包含叠加信息
```

若确实为 1280 x 1024 SXGA，则应选择带缩放功能的有源设备：

```text
DVI/SXGA 输入
→ 主动缩放为 720p59.94 或 1080p59.94
→ HDMI 或 HD-SDI 输出
→ DeckLink
```

此时不要直接购买 `Micro Converter HDMI to SDI 3G` 来解决问题，因为它只有 HDMI
输入，而且不会把任意 SXGA 信号自动缩放成标准视频格式。

## 四、实验室如何模拟现场

### 最便宜的功能测试

```text
第二台电脑 HDMI OUT
→ 一根 HDMI 线
→ 服务器 DeckLink HDMI IN
```

这个方案不需要转换器，可以测试采集、实时播放、分析延时和长时间稳定性，但没有
覆盖 SDI 线缆与 SDI 锁相链路。

### 更接近医院现场的 SDI 模拟

```text
第二台电脑 HDMI OUT
→ Micro Converter HDMI to SDI 3G wPSU
→ 75 欧姆 BNC SDI 线
→ 服务器 DeckLink SDI IN
```

第二台电脑通过 OBS 全屏循环播放手术视频，并优先输出：

1. `1280 x 720, 59.94 Hz`，用于稳定复现 da Vinci Si 常见的 720p59.94 信号；
2. 条件允许时再测试 `1920 x 1080, 59.94i`；
3. 额外测试 `1920 x 1080, 59.94p`，验证 3G-SDI 链路上限。

因此，这个转换器的购买价值主要是让我们在去医院前完整测试一次 SDI 链路。它可以
作为实验室测试工具和现场故障排查备件，但不是达芬奇直接输出 SDI 时的正式必需件。

## 五、当前服务器已经具备的能力

2026-07-15 实机检查结果：

```text
设备节点：/dev/blackmagic/io0
设备：DeckLink Mini Recorder 4K
固件状态：OK
接口：一个 6G-SDI 输入、一个 HDMI 输入
```

本机 GStreamer 已识别以下关键采集模式：

- 自动识别 `mode=auto`
- `720p5994`
- `1080i5994`
- `1080p2997`
- `1080p5994`
- `1080p60`

现场第一次确认 SDI 信号时可使用：

```bash
gst-launch-1.0 decklinkvideosrc \
  device-number=0 connection=sdi mode=auto \
  drop-no-signal-frames=true \
  ! queue leaky=downstream max-size-buffers=1 \
  ! deinterlace \
  ! videoconvert \
  ! autovideosink sync=false
```

如果输入是 1080i，采集管线必须正确去隔行，否则运动画面会出现横向梳状边缘，
看起来类似撕裂或断面。完成命令行测试后应关闭该进程，再启动 Electron 应用，避免
两个进程同时占用 DeckLink。

## 六、现场接线前必须确认的问题

请医院设备科或 Intuitive 工程师确认以下内容：

1. 机器确切型号和软件版本，当前照片是否确实为 da Vinci Si 医生控制台。
2. Vision Cart/Core/CCU 后方哪个接口是可用的手术画面 HD-SDI 输出。
3. 该输出是否已经安装并启用；部分系统可能需要 Video Expansion Kit。
4. 输出制式是 720p59.94、1080i59.94，还是其他格式。
5. L/R 哪一路为需要的手术画面，是否含有图标或文字叠加。
6. 输出口是否已被显示器、录像机或其他设备占用。

下一张照片应拍摄：

- Vision Cart/Core/CCU 背面完整接口；
- `Video Out 1/2/Aux` 附近的清晰近照；
- 达芬奇型号和序列号铭牌；
- 现有视频线另一端连接到哪里。

## 七、最终采购建议

如果目标是“只为医院现场准备”，需要按现场实际输出二选一：

### A. 找到可用的 HD-SDI Video Out

1. 购买合格的 75 欧姆 3G/6G-SDI BNC 公对公线，一根主用、一根备用。
2. 达芬奇 HD-SDI 输出直接进入 DeckLink SDI IN。
3. 不购买 HDMI to SDI 3G wPSU。

### B. 确认只有照片中的 DVI (SXGA) Video Out L/R

1. 购买或借测一台支持 `1280 x 1024 @ 60 Hz` 输入的有源缩放器。
2. 优先选择固定输出 `1920 x 1080p59.94/60` HDMI 的型号，直接进入 DeckLink
   HDMI IN；长距离布线则选择带 HD-SDI 输出的型号。
3. 暂时不要购买普通 DVI 转 HDMI 被动线作为正式方案。
4. 不购买 `Micro Converter HDMI to SDI 3G wPSU` 作为唯一转换设备，因为它不做
   SXGA 到标准视频制式的缩放。

在无法确认还有其他视频输出口的前提下，当前更符合照片的现场路线是 **B：使用
空着的 `Video Out L`，经过有源缩放器进入 DeckLink**。

## 参考资料

- [Blackmagic Micro Converter HDMI to SDI 3G 官方规格](https://www.blackmagicdesign.com/products/microconverters/techspecs/W-CONU-09)
- [Blackmagic DeckLink Mini Recorder 4K 官方规格](https://www.blackmagicdesign.com/cn/products/decklink/techspecs)
- [da Vinci Si User Manual：视频输入与输出接口](https://dvrk.lcsr.jhu.edu/downloads/manuals/davinci-si-user-manual.pdf)
- [da Vinci Si OR Staff In-Service Guide：第三方视频连接](https://www.lmc-clients.com/intuitive/2023/Resources/StaffingGuide/210107-USrM-Si-InServiceGuide-ORstaff-OS4v9.pdf)
