# USB 摄像头接入

当前先接实时画面，明火模型、推理、截图与视觉告警另行接入。监控总览和明火检测页共用一个后台采集进程；设备管理显示摄像头的实际采集状态。关闭网页不会停止采集，关闭后台则释放摄像头。

## 已确认的实机条件

2026-10-15 用户在树莓派提供的输出确认：icSpring USB 摄像头，采集节点 `/dev/video0`，固定链接位于 `/dev/v4l/by-id/`，支持 MJPEG 1280×720 / 30 fps，已安装 FFmpeg 和 v4l2-ctl。单帧 FFmpeg 命令成功取得 26 kB 图像；退出出现缓冲区释放提示。网页持续预览、拔插重连与长时间稳定性仍待本版本上传后验证。

## 更新与启动

1. 在运行工厂后台的终端按 Ctrl+C 停止本项目。
2. 将更新文件覆盖到 `~/factory_monitoring/`，保持子目录结构。不要覆盖本机 `mail.ini`、`runtime.env`、`data/` 或 `.venv/`。
3. 在项目目录执行：

   ```bash
   cd ~/factory_monitoring
   bash deploy/raspberry-pi/enable-camera.sh
   bash deploy/raspberry-pi/run.sh
   ```

配置脚本仅在恰好发现一个 USB 摄像头的 `video-index0` 固定链接时自动选择，备份 `runtime.env` 后更新五个摄像头配置项；已有 MQTT、模式等设置保留。多摄像头时明确指定路径：

```bash
bash deploy/raspberry-pi/enable-camera.sh /dev/v4l/by-id/你的摄像头-video-index0
```

初次使用且尚无运行环境时，先运行 `bash deploy/raspberry-pi/prepare.sh`。未安装采集工具的主机可执行 `sudo apt install ffmpeg v4l-utils`；本次用户树莓派已安装，无须重复安装。当前只支持 Linux V4L2 的 MJPEG 摄像头。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FACTORY_CAMERA_ENABLED` | `0` | `1` 开启；示例默认关闭 |
| `FACTORY_CAMERA_DEVICE` | `/dev/video0` | 建议固定 `/dev/v4l/by-id/...-video-index0` 路径 |
| `FACTORY_CAMERA_WIDTH` | `1280` | 采集宽度 |
| `FACTORY_CAMERA_HEIGHT` | `720` | 采集高度 |
| `FACTORY_CAMERA_FPS` | `30` | 摄像头支持的输入帧率；不是推理帧率 |
| `FACTORY_CAMERA_ROTATION` | `0` | `180` 将倒置画面转正，`0` 保留原方向 |

默认 720p 与 30 fps 已由本次摄像头格式列表确认。换摄像头时先用 `v4l2-ctl -d /dev/video0 --list-formats-ext` 核对；不支持 MJPEG 的设备不能直接沿用本配置。

采集使用系统 FFmpeg。默认不旋转时，以 `-c:v copy` 保留压缩图像，使用 `mjpeg2jpeg` 补齐 JPEG 头部兼容性。旋转 180° 时，按预览需要取最多 10 fps，通过 `hflip,vflip` 转正后编码为 JPEG；会增加 CPU 开销，实际性能需在树莓派验证。参数依据 [FFmpeg V4L2 文档](https://ffmpeg.org/ffmpeg-devices.html#video4linux2_002c-v4l2)、[mjpeg2jpeg 文档](https://ffmpeg.org/ffmpeg-bitstream-filters.html#mjpeg2jpeg) 和 [视频翻转滤镜文档](https://www.ffmpeg.org/ffmpeg-filters.html#vflip)。FFmpeg 为系统依赖，本仓库不打包其二进制。

只缓存最新一帧，不录像、不积累图片文件。网页依次读取 `/api/camera/frame`，每次完成后等 100 ms 再读，最多约 10 帧/秒；慢网络会自动降低刷新速度，不叠加请求。隐藏标签页暂停请求，回来重新加载。采集帧率、网页刷新速度与未来模型推理 FPS 分别统计，当前推理栏仍为“—”。

## 倒装摄像头转正

更新 `camera_capture.py` 后，在树莓派本项目的 `deploy/raspberry-pi/runtime.env` 中添加或修改：

```bash
FACTORY_CAMERA_ROTATION=180
```

重启工厂后台后生效。总览、明火检测页和单帧接口共用旋转后的图像；后续模型应读取同一份图像。180° 旋转会同时改变上下和左右位置，文字仍保持正常方向，与仅上下镜像不同。需要恢复原方向时改为 `0` 并重启。

## 验收

1. 浏览器 Ctrl+F5，总览左侧出现真实画面，顶部摄像头“已接入”。
2. 打开明火检测页，移动手或物体确认画面连续更新；图像完整、不拉伸。模型仍显示待部署。
3. 同时打开两页，确认都能看；温湿度等读数和历史继续更新。
4. 拔掉摄像头，画面应消失并显示重连；插回后通常在下一轮重连与状态刷新内恢复。
5. 持续预览至少 5 分钟，检查有无卡住及终端是否持续报错。不要用明火做本步骤的视频连通测试。

摄像头 3 秒无新帧即视为过期，旧画面不再通过接口返回；启动等待最多 12 秒，无帧会终止采集并重试，重连间隔 5 秒。网页采集失败或图像无法解码会清除旧图并重试。

## 排查

- **未启用**：检查 `runtime.env` 的 `FACTORY_CAMERA_ENABLED=1`，重启工厂后台。
- **重连中**：在新终端执行 `ls -l /dev/v4l/by-id/`，核对设备仍存在。
- **Device or resource busy**：用 `fuser /dev/video0` 查看占用者；不要直接停止原文物项目服务或杀进程，先确认使用者。
- **Permission denied**：检查 `id` 及 `ls -l /dev/video0`，确认运行工厂后台的用户拥有设备读写权限。
- **格式错误 / 无画面**：检查 FFmpeg 日志和格式列表，确认 MJPEG、分辨率、帧率一致。
- **摄像头已接入但网页没有图**：浏览器直接访问 `http://树莓派IP:5001/api/camera/frame` 检查单帧；保留终端日志进一步定位。

关闭采集：在本机 `runtime.env` 改为 `FACTORY_CAMERA_ENABLED=0`，重启工厂后台；不影响传感器功能。
