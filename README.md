# ⚡️ 国家电网电费数据获取（点选验证码版）

将国家电网（95598）的电费、用电量数据接入 Home Assistant，支持腾讯点选验证码自动识别。

## 致谢

本项目基于以下开源项目参考和改进：

- [ARC-MX/sgcc_electricity_new](https://github.com/ARC-MX/sgcc_electricity_new) — 项目基础框架和数据抓取逻辑
- [renxiaoyaoo/ha-95598](https://github.com/renxiaoyaoo/ha-95598) — 点选验证码识别方案参考

原始项目采用 Apache License 2.0 协议开源，本项目遵循相同协议。

## 与原项目的主要区别

| 特性 | 原项目 (ARC-MX) | 本项目 |
|------|-----------------|--------|
| 验证码类型 | 滑动验证码（YOLOv3/ONNX） | **点选验证码**（图像匹配） |
| 验证码方案 | 需要 `captcha.onnx` 模型文件 | 纯算法离线识别，无需额外模型 |
| 数据推送 | Home Assistant REST API | Home Assistant REST API（保持不变） |
| 验证码依赖 | onnxruntime | ddddocr（可选）+ opencv（可选，有 numpy fallback） |

## 功能

- 自动登录国家电网（支持点选验证码自动识别）
- 通过 Home Assistant REST API 推送以下传感器数据：

| 实体 entity_id | 说明 |
|---|---|
| sensor.last_electricity_usage_xxxx | 最近一天用电量（KWH） |
| sensor.electricity_charge_balance_xxxx | 电费余额（CNY） |
| sensor.yearly_electricity_usage_xxxx | 今年总用电量（KWH） |
| sensor.yearly_electricity_charge_xxxx | 今年总电费（CNY） |
| sensor.month_electricity_usage_xxxx | 最近一个月用电量（KWH） |
| sensor.month_electricity_charge_xxxx | 上月总电费（CNY） |

- 可选将每日用电量保存到数据库（SQLite / MySQL）
- 密码登录失败自动切换二维码登录兜底
- 电费余额不足通知（PushPlus / URL Push）

## 适用范围

- 适用于国家电网覆盖省份（广东、广西、云南、贵州、海南等南方电网省份不可用）
- 支持 Docker 部署（`linux/amd64`、`linux/arm64`）
- 支持本地运行（Windows / macOS / Linux + Python 3.11+）

## Docker 部署（推荐）

### 1. 创建配置文件

```bash
# 下载 docker-compose.yml 和配置模板
mkdir sgcc_electricity && cd sgcc_electricity
curl -O https://raw.githubusercontent.com/Poiig/sgcc_electricity_new/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/Poiig/sgcc_electricity_new/master/example.env
cp example.env .env
```

### 2. 编辑 `.env`

修改以下必填项（完整配置项说明见 `example.env`）：

```ini
# 国网登录信息
PHONE_NUMBER="你的手机号"
PASSWORD="你的密码"

# Home Assistant
HASS_URL="http://你的HA地址:8123/"
HASS_TOKEN="你的HA长期访问令牌"

# 登录失败备选方案
LOGIN_FALLBACK='qrcode'
```

### 3. 启动

```bash
docker compose up -d
```

### 4. 查看日志

```bash
docker compose logs -f sgcc_electricity
```

成功运行后日志示例：

```
[INFO] DataFetcher 初始化完成: 用户=138xxxx, 步骤等待=10s
[INFO] 正在初始化 WebDriver, 平台: Linux
[INFO] 使用 Chromium 浏览器 (Docker 模式)
[INFO] 已打开登录页面
[INFO] 已输入账号密码, 账号: 138xxxx
[INFO] 检测到验证码: 类型=point_click, 提示文字=请依次点击...
[INFO] 点选验证码识别成功, 已通过验证!
[INFO] 登录成功!
[INFO] 共获取到 1 个用户: ['1234567890'], 其中忽略列表将被忽略
[INFO] ===== 开始处理第 1/1 个用户: 1234567890 =====
[INFO] [1234567890] 电费余额: 102.3 元
[INFO] [1234567890] 年度用电量: 1691 kWh
[INFO] [1234567890] 最近用电: 2026-05-17 用电 6.56 kWh
[INFO] [1234567890] Home Assistant 传感器数据更新完成!
```

### 5. 更新

```bash
docker compose pull
docker compose up -d
```

### 镜像地址

| 来源 | 地址 |
|------|------|
| GHCR（推荐） | `ghcr.io/poiig/sgcc_electricity_new:latest` |
| Docker Hub | `poiig/sgcc_electricity:latest` |

支持架构：`linux/amd64`、`linux/arm64`

## 本地运行

详见 [LOCAL_DEV_GUIDE.md](LOCAL_DEV_GUIDE.md)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp example.env .env
# 编辑 .env
cd scripts
python main.py
```

## 登录流程

```
输入账号密码 → 点击登录
  → 检测到点选验证码 → 截取题目+背景图片
    → 图像匹配算法找到目标位置 → 模拟点击 → 提交验证
  → 验证码识别失败 → 自动刷新重试
  → 多次失败 → 二维码登录兜底
```

验证码处理过程会自动保存调试图片到 `data/pages/` 目录，方便排查问题。

## Home Assistant 配置

程序通过 REST API 自动创建实体，但需要配置 `configuration.yaml` 以确保重启后实体可用：

```yaml
template:
  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.electricity_charge_balance_xxxx
    sensor:
      - name: electricity_charge_balance_xxxx
        unique_id: electricity_charge_balance_xxxx
        state: "{{ states('sensor.electricity_charge_balance_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.last_electricity_usage_xxxx
    sensor:
      - name: last_electricity_usage_xxxx
        unique_id: last_electricity_usage_xxxx
        state: "{{ states('sensor.last_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.month_electricity_usage_xxxx
    sensor:
      - name: month_electricity_usage_xxxx
        unique_id: month_electricity_usage_xxxx
        state: "{{ states('sensor.month_electricity_usage_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.month_electricity_charge_xxxx
    sensor:
      - name: month_electricity_charge_xxxx
        unique_id: month_electricity_charge_xxxx
        state: "{{ states('sensor.month_electricity_charge_xxxx') }}"
        state_class: measurement
        unit_of_measurement: "CNY"
        device_class: monetary

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_usage_xxxx
    sensor:
      - name: yearly_electricity_usage_xxxx
        unique_id: yearly_electricity_usage_xxxx
        state: "{{ states('sensor.yearly_electricity_usage_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "kWh"
        device_class: energy

  - trigger:
      - platform: event
        event_type: state_changed
        event_data:
          entity_id: sensor.yearly_electricity_charge_xxxx
    sensor:
      - name: yearly_electricity_charge_xxxx
        unique_id: yearly_electricity_charge_xxxx
        state: "{{ states('sensor.yearly_electricity_charge_xxxx') }}"
        state_class: total_increasing
        unit_of_measurement: "CNY"
        device_class: monetary
```

（将 `xxxx` 替换为日志中显示的后缀）

## 常见问题

### Q: 验证码识别失败

- 检查 `data/pages/` 下的调试截图
- 国网每天有登录次数限制，频繁测试会导致 RK001 错误

### Q: RK001 网络连接超时

国网检测到异常登录频率，等待几小时后重试。

### Q: Docker 镜像比较大

镜像包含完整 Chromium 浏览器、中文字体和验证码识别依赖。

## License

本项目采用 [Apache License 2.0](LICENSE) 协议开源。
