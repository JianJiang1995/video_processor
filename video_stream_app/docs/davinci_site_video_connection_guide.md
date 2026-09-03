# 达芬奇视频采集连接与采购说明

更新时间：2026-07-24

适用范围：当前提供的达芬奇接口照片，以及服务器内已经安装的
`DeckLink Mini Recorder 4K` 采集卡。

明天现场的逐步接线、软件预检和故障对照表见
[`or_capture_day_runbook_20260725.md`](./or_capture_day_runbook_20260725.md)。应用现已支持
HDMI/SDI 选择、输入制式自动探测、1080i 去隔行、最新帧低延迟显示、掉线重连和黑屏诊断。

> 重要说明：根据接口排列、`TilePro`、`Video Out L/R` 和 `DVI (SXGA)`
> 等标识判断，照片很像 **da Vinci Si 医生控制台背面的接口区**。以下对照片中
> 接口用途的判断可信度较高，但正式接线前仍应由医院设备科或 Intuitive 工程师
> 根据机器型号确认。不要拔除照片中已有的连接线。

## 2026-07-23 最新采购结论

由于目前难以买到规格明确的 DVI/SXGA 转 SDI 缩放器，现场优先方案调整为直接
购买一台能够采集计算机 DVI/SXGA 时序的 USB 采集设备：

```text
达芬奇 Video Out L：DVI (SXGA)
→ DVI-D 24+1 公对公线
→ Magewell USB Capture DVI Plus（P/N 32080）的 DVI IN
→ 随机 USB 3.0 线
→ 服务器 USB 3.0 接口
```

`Magewell USB Capture DVI Plus` 官方明确支持：

- DVI-I 输入和最高 2048 x 2160 的输入分辨率；
- Linux、V4L2 和 GStreamer；
- 硬件缩放、裁剪、宽高比转换和帧率转换；
- 24 x 7 连续运行；
- USB 3.0 输出，随机附带 USB 3.0 线。

项目后端已经具备 `/dev/video*` 和 OpenCV V4L2 采集入口。正式接入前仍应把显示、
存帧和分析统一到一个共享采集器，避免多个模块同时打开同一 USB 设备，但这属于小范围
软件适配，不需要重做分析架构。

购买入口：

- [Magewell USB Capture DVI Plus 官方规格](https://www.magewell.com/products/usb-capture-dvi-plus)
- [京东搜索：美乐威 USB Capture DVI Plus 32080](https://search.jd.com/Search?keyword=%E7%BE%8E%E4%B9%90%E5%A8%81%20USB%20Capture%20DVI%20Plus%2032080)
- [淘宝搜索：美乐威 USB Capture DVI Plus 32080](https://s.taobao.com/search?q=%E7%BE%8E%E4%B9%90%E5%A8%81%20USB%20Capture%20DVI%20Plus%2032080)
- [京东搜索：DVI-D 24+1 公对公 1.5 米](https://search.jd.com/Search?keyword=DVI-D%2024%2B1%20%E5%85%AC%E5%AF%B9%E5%85%AC%201.5%E7%B1%B3)

采用这条路线时，不需要购买 SDI 线、HDMI to SDI 转换器或 DVI to SDI 缩放器，
现有 DeckLink 可以保留为未来连接标准 SDI/HDMI 视频源的备用采集卡。

### 可以先做的低成本 DeckLink 直连试验

被动 DVI-D 转 HDMI 线在电气上可以把数字 DVI 接到 DeckLink HDMI IN，但它不会
把 1280 x 1024 SXGA 转成 720p/1080p。当前 DeckLink/GStreamer 报告的输入模式中
没有 1280 x 1024，因此不能把直连当作确定可用的现场方案。

可购买一根普通 HDMI to HDMI 线，在服务器本地进行以下测试：

```text
服务器 NVIDIA 显卡 HDMI OUT（设置成 1280 x 1024 @ 60 Hz）
→ HDMI to HDMI 线
→ DeckLink HDMI IN
```

如果 DeckLink 无法锁定该信号，就已经证明达芬奇 SXGA 经被动 DVI to HDMI 线也
不会工作。如果它能锁定，再购买 DVI-D 24+1 to HDMI 线去现场做第二次实机测试；
但医院部署仍应携带支持 SXGA 的 USB DVI 采集设备作为确定方案。

![达芬奇机器接口照片](../../达芬奇机器接口.jpg)

## 一句话结论

1. 照片右下方标有 `Video Out L` 的 `DVI (SXGA)` 是左眼视频**输出口**。
2. 照片中已经插线的 DVI 位于左侧蓝色输入区域，应该是 `TilePro Input R`，
   它是把外部画面送入达芬奇的**输入口**，不是我们要采集的输出口。
3. 照片上方两个 SDI 也属于 `TilePro Input L/R`，是**输入口**，不能直接拿来
   给 DeckLink 采集。
4. 如果能找到独立的 **HD-SDI Video Out**，可用 75 欧姆 SDI 线直接进入
   DeckLink；如果现场只有照片中的接口，则首选 `Video Out L` 直接进入
   `Magewell USB Capture DVI Plus`，再通过 USB 3.0 接服务器。
5. `Micro Converter HDMI to SDI 3G wPSU` 主要用于实验室把第二台电脑的 HDMI
   输出模拟成 SDI。它不是正式连接达芬奇 SDI 输出时的必需设备。

## 如果现场确实只有照片中的接口

此时仍然可以使用达芬奇的视频输出，推荐直接进入支持 SXGA 的 USB DVI 采集设备：

```text
空着的 Video Out L：DVI (SXGA)
        ↓
Magewell USB Capture DVI Plus
        ↓ USB 3.0
服务器
```

也就是说：

- **使用右下方空着的 `Video Out L`**，不是使用照片中已经插线的 DVI。
- **不使用上方两个 SDI**，因为它们是 TilePro 输入口。
- **不能依赖普通 DVI 转 HDMI 被动线直接采集**。DVI 与 HDMI 的数字信号在电气
  层面相近，但当前 DeckLink 驱动报告的输入模式中没有 1280 x 1024 SXGA；普通
  线缆也不会改变分辨率。
- 采集设备必须明确支持 `1280 x 1024 @ 60 Hz` 的计算机视频时序；Magewell
  DVI Plus 满足这个要求，并能在硬件内完成缩放与宽高比转换。
- `Micro Converter HDMI to SDI 3G wPSU` **不能单独解决这个问题**：它没有 DVI
  输入，也不是分辨率缩放器。

如果 USB 3.0 距离需要超过约 3 米，应将服务器或采集设备放得更近，或者使用经过
验证的有源 USB 3.0 延长方案。只有必须进行更长距离布线时，才回到带缩放功能的
DVI/SXGA to SDI 方案。

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

因此，`Video Out L` 可以输出。如果没有其他标准 HD-SDI 输出，当前现场首选就是
通过 `Magewell USB Capture DVI Plus` 直接采集这个 DVI/SXGA 输出。

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

### 存在标准 HD-SDI 输出时：直接进入 DeckLink

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

1. 购买 `Magewell USB Capture DVI Plus`（P/N 32080）。
2. 购买一根 1.5 米或 2 米 DVI-D 24+1 公对公线。
3. 使用随机 USB 3.0 线接入服务器，不再经过 DeckLink。
4. 普通 DVI 转 HDMI 被动线只用于低成本兼容性测试，不作为确定的正式方案。
5. 不购买 `Micro Converter HDMI to SDI 3G wPSU`。

在无法确认还有其他视频输出口的前提下，当前更符合照片的现场路线是 **B：使用
空着的 `Video Out L`，直接进入支持 SXGA 的 USB DVI 采集设备**。

## 八、备用路线：DVI to SDI 缩放器购物清单

以下路线仅在能够买到并确认转换器规格时使用。当前首选方案是前文的
`Magewell USB Capture DVI Plus` 直接 DVI 采集。

### 最终连线

```text
达芬奇 Video Out L：DVI (SXGA)
→ 1.5 米 DVI-D 24+1 公对公线
→ 带缩放功能的 DVI to 3G-SDI 扫描转换器
→ 75 欧姆 3G-SDI BNC 公对公线
→ 服务器 DeckLink Mini Recorder 4K：SDI IN
```

转换器放在达芬奇旁边，短距离传输 DVI；较长距离使用 SDI 到服务器。这样比在现场
长距离传输 HDMI 更稳定。

### 需要购买的三项

| 序号 | 物品 | 数量 | 明确规格 | 用途 |
| --- | --- | --- | --- | --- |
| 1 | DVI to 3G-SDI **缩放/扫描转换器** | 1 台 | 必须接收 1280x1024@60，输出 1080p59.94/60 3G-SDI，支持保持画面比例 | 将达芬奇 SXGA 转成 DeckLink 支持的广播格式 |
| 2 | DVI-D 24+1 公对公线 | 1 根 | 1.5 米或 2 米，Single Link 即可 | 达芬奇 Video Out L 到转换器 |
| 3 | 75 欧姆 3G/6G-SDI BNC 公对公成品线 | 2 根 | 推荐佳耐美 L-5CFB 或 Belden 1694A 同级；按现场距离购买，例如 10 米主线加 5 米备用 | 转换器到 DeckLink SDI IN |

### 转换器选择

#### 预算测试方案：CE-LINK 工程级 DVI to SDI 缩放转换器

- 参考价格：页面当前约 1399 元，价格可能变化。
- 商品标题包含“工程级、DVI 转 SDI、变频、可调分辨率、医疗/内窥镜”。
- [天猫商品链接，商品 ID 42052336549](https://detail.tmall.com/item.htm?id=42052336549)
- [淘宝搜索：CE-LINK DVI 转 SDI 缩放](https://s.taobao.com/search?q=CE-LINK%20DVI%E8%BD%ACSDI%20%E7%BC%A9%E6%94%BE)

该商品页面没有公开足够完整的时序表，因此下单前必须让卖家用文字确认下一节列出的
四项要求，并选择支持七天退换的订单。若卖家不能确认 `1280x1024@60` 输入，不买。

类似的可验证国产型号是 `LINK-MI LM-AS01` 或 `LM-PDS01`。其中 LM-AS01 的说明书
明确描述 DVI/VGA 输入缩放为 HD/3G-SDI；购买时仍应核对具体型号，不能只看外壳和
“DVI 转 SDI”标题。

#### 专业确定方案：AJA ROI-DVI

- 官方型号：`AJA ROI-DVI`。
- 明确支持 VGA 至 WUXGA（最高 1920x1200@60）的计算机 DVI 输入。
- 可做分辨率、帧率和宽高比转换，并输出 720p、1080i、1080p 3G-SDI。
- 官方电源适配器包含在内，提供 DVI 环通输出。
- 官方 MSRP 为 1399 美元，国内通常是万元级，明显比预算方案贵。
- [AJA ROI-DVI 官方规格](https://www.aja.com/products/roi-dvi)
- [京东搜索：AJA ROI-DVI](https://search.jd.com/Search?keyword=AJA%20ROI-DVI)
- [淘宝搜索：AJA ROI-DVI](https://s.taobao.com/search?q=AJA%20ROI-DVI)

本节设备仅作为必须走 SDI 长距离布线时的备用选择。一般情况下优先采用前文的
USB DVI 采集方案，设备更容易购买，链路也更短。

### 线材购买入口

- [京东搜索：DVI-D 24+1 公对公 1.5 米](https://search.jd.com/Search?keyword=DVI-D%2024%2B1%20%E5%85%AC%E5%AF%B9%E5%85%AC%201.5%E7%B1%B3)
- [淘宝搜索：DVI-D 24+1 公对公 1.5 米](https://s.taobao.com/search?q=DVI-D%2024%2B1%20%E5%85%AC%E5%AF%B9%E5%85%AC%201.5%E7%B1%B3)
- [京东搜索：佳耐美 L-5CFB 3G-SDI BNC 10 米](https://search.jd.com/Search?keyword=%E4%BD%B3%E8%80%90%E7%BE%8E%20L-5CFB%203G-SDI%20BNC%2010%E7%B1%B3)
- [淘宝搜索：佳耐美 L-5CFB 3G-SDI BNC 10 米](https://s.taobao.com/search?q=%E4%BD%B3%E8%80%90%E7%BE%8E%20L-5CFB%203G-SDI%20BNC%2010%E7%B1%B3)

不要购买 DVI 24+5 转 VGA 模拟线，也不要购买 50 欧姆射频 BNC 线。连接 DeckLink
需要的是 75 欧姆视频 SDI 成品线。

### 下单前发给转换器卖家的确认文字

```text
我的输入源是医疗设备的 DVI 数字输出，固定为 SXGA 1280x1024@60Hz，非 HDCP。
请书面确认该设备可以直接接收这个时序，并主动缩放输出为
3G-SDI 1920x1080p59.94（Level A）或 1080p60。
还需要支持 KEEP/保持原比例，允许左右留黑边，不能把 5:4 画面强行拉伸成 16:9。
请提供对应型号说明书或输入输出时序表截图，并确认电源适配器包含在内。
```

卖家只回复“支持 1080P”是不够的，必须明确回答输入端支持 `1280x1024@60`，并且
设备内部有 scaler/缩放和帧率转换功能。

### 明确不要购买

| 物品 | 原因 |
| --- | --- |
| Blackmagic Micro Converter HDMI to SDI 3G wPSU | 只有 HDMI 输入，而且不负责把 SXGA 缩放成标准 1080p；它只适合实验室用电脑 HDMI 模拟 SDI |
| 普通 DVI 转 HDMI 被动线作为正式方案 | 只改变插头，不改变 1280x1024 分辨率；当前 DeckLink 不报告支持 SXGA |
| 几十元的“DVI 转 SDI”无缩放盒 | 很多只接受标准 720p/1080p DVI 时序，无法接受固定 SXGA 输出 |
| 无源 BNC 三通 | 会破坏 75 欧姆阻抗和 SDI 信号完整性 |

### 到货后的验收要求

1. 先用一台电脑设置为 `1280x1024@60`，代替达芬奇进行实验室测试。
2. 转换器设置为 `1080p59.94 Level A`；没有 59.94 时使用 `1080p60`。
3. 画面比例使用 `KEEP`，允许左右黑边，不能拉伸手术画面。
4. 连续运行至少 8 小时，检查黑屏、掉帧、颜色异常、重连恢复和设备温度。
5. 再连接本机 DeckLink 和 Electron 应用，验证实时预览与分析不会积压。
6. 通过医院设备科电气安全审核后，才能带入现场使用；该链路只能接辅助视频输出，
   不能串入医生主显示链路。

## 参考资料

- [Blackmagic Micro Converter HDMI to SDI 3G 官方规格](https://www.blackmagicdesign.com/products/microconverters/techspecs/W-CONU-09)
- [Blackmagic DeckLink Mini Recorder 4K 官方规格](https://www.blackmagicdesign.com/cn/products/decklink/techspecs)
- [da Vinci Si User Manual：视频输入与输出接口](https://dvrk.lcsr.jhu.edu/downloads/manuals/davinci-si-user-manual.pdf)
- [da Vinci Si OR Staff In-Service Guide：第三方视频连接](https://www.lmc-clients.com/intuitive/2023/Resources/StaffingGuide/210107-USrM-Si-InServiceGuide-ORstaff-OS4v9.pdf)
