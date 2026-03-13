# extronsis-exporter

A [Prometheus](https://prometheus.io/) exporter for [Extron](https://www.extron.com/) AV devices that support the **Simple Instruction Set (SIS)** protocol, such as the **Extron IN1804** video matrix switch.

Communication with the device happens over **SSH on port 22023** (the Extron SIS SSH port).

---

## Features

- Connects to one or more Extron SIS devices over SSH (port 22023)
- Parses the device login banner to expose model, firmware version, part number, and device date/time
- Exposes the **currently selected input** per output
- Exposes **input general information** (video type, audio mute, video mute, horizontal/vertical frequencies) for each input
- Exposes **output audio and video mute** status for each output
- Exposes **internal device temperature** (where supported)
- Exposes **power save mode** (WPSAV command)
- **Supports URL-parameter scraping** — target any device at scrape time without touching the config file
- Graceful shutdown on `SIGINT` / `SIGTERM`
- Docker image included

---

## Exposed Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `extron_up` | Gauge | `device`, `host` | `1` if the last scrape succeeded, `0` otherwise |
| `extron_scrape_duration_seconds` | Gauge | `device`, `host` | Duration of the last scrape in seconds |
| `extron_device_info` | Info | `device`, `host`, `model`, `firmware_version`, `part_number`, `copyright_year`, `device_datetime` | Static device information from the login banner |
| `extron_output_current_input` | Gauge | `device`, `host`, `output` | Input number currently routed to this output (`0` = no input) |
| `extron_input_video_type` | Gauge | `device`, `host`, `input` | Video signal type on the input: 0=No signal, 1=DVI, 2=HDMI, 3=DisplayPort |
| `extron_input_audio_muted` | Gauge | `device`, `host`, `input` | `1` if the input audio is muted, `0` otherwise |
| `extron_input_video_muted` | Gauge | `device`, `host`, `input` | Video mute state on the input: 0=Unmuted, 1=Mute to black, 2=Mute video and sync |
| `extron_input_horizontal_freq_khz` | Gauge | `device`, `host`, `input` | Horizontal frequency of the input signal in kHz |
| `extron_input_vertical_freq_hz` | Gauge | `device`, `host`, `input` | Vertical frequency of the input signal in Hz |
| `extron_output_audio_muted` | Gauge | `device`, `host`, `output` | `1` if the output audio is muted, `0` otherwise |
| `extron_output_video_muted` | Gauge | `device`, `host`, `output` | `1` if the output video is muted, `0` otherwise |
| `extron_temperature_celsius` | Gauge | `device`, `host` | Internal device temperature in °C (omitted if not available) |
| `extron_power_mode` | Gauge | `device`, `host` | Current power save mode (WPSAV): 0=Full power, 1=Lowest power (TP disabled), 2=Lower power (TP links active), 9=Over-heating |

### Example output

```
# HELP extron_up 1 if the last scrape of the device was successful, 0 otherwise.
# TYPE extron_up gauge
extron_up{device="in1804-room-101",host="192.168.1.10"} 1.0

# HELP extron_device_info Static information about the Extron device from the login banner.
# TYPE extron_device_info gauge
extron_device_info{copyright_year="2023",device="in1804-room-101",device_datetime="Mon, 09 Mar 2026 12:08:14",firmware_version="1.08",host="192.168.1.10",model="IN1804 DO",part_number="60-1699-13"} 1.0

# HELP extron_output_current_input The input number currently routed to this output (0 = no input).
# TYPE extron_output_current_input gauge
extron_output_current_input{device="in1804-room-101",host="192.168.1.10",output="1"} 2.0

# HELP extron_input_video_type Video signal type on the input: 0=No signal, 1=DVI, 2=HDMI, 3=DisplayPort.
# TYPE extron_input_video_type gauge
extron_input_video_type{device="in1804-room-101",host="192.168.1.10",input="1"} 2.0
extron_input_video_type{device="in1804-room-101",host="192.168.1.10",input="2"} 2.0
extron_input_video_type{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0

# HELP extron_input_audio_muted 1 if the input audio is muted, 0 otherwise.
# TYPE extron_input_audio_muted gauge
extron_input_audio_muted{device="in1804-room-101",host="192.168.1.10",input="1"} 0.0
extron_input_audio_muted{device="in1804-room-101",host="192.168.1.10",input="2"} 0.0
extron_input_audio_muted{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0

# HELP extron_input_video_muted Video mute state on the input: 0=Unmuted, 1=Mute to black, 2=Mute video and sync.
# TYPE extron_input_video_muted gauge
extron_input_video_muted{device="in1804-room-101",host="192.168.1.10",input="1"} 0.0
extron_input_video_muted{device="in1804-room-101",host="192.168.1.10",input="2"} 0.0
extron_input_video_muted{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0

# HELP extron_input_horizontal_freq_khz Horizontal frequency of the input signal in kHz.
# TYPE extron_input_horizontal_freq_khz gauge
extron_input_horizontal_freq_khz{device="in1804-room-101",host="192.168.1.10",input="1"} 31.47
extron_input_horizontal_freq_khz{device="in1804-room-101",host="192.168.1.10",input="2"} 31.47
extron_input_horizontal_freq_khz{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0

# HELP extron_input_vertical_freq_hz Vertical frequency of the input signal in Hz.
# TYPE extron_input_vertical_freq_hz gauge
extron_input_vertical_freq_hz{device="in1804-room-101",host="192.168.1.10",input="1"} 60.0
extron_input_vertical_freq_hz{device="in1804-room-101",host="192.168.1.10",input="2"} 60.0
extron_input_vertical_freq_hz{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0

# HELP extron_output_audio_muted 1 if the output audio is muted, 0 otherwise.
# TYPE extron_output_audio_muted gauge
extron_output_audio_muted{device="in1804-room-101",host="192.168.1.10",output="1"} 0.0

# HELP extron_output_video_muted 1 if the output video is muted, 0 otherwise.
# TYPE extron_output_video_muted gauge
extron_output_video_muted{device="in1804-room-101",host="192.168.1.10",output="1"} 0.0

# HELP extron_temperature_celsius Internal device temperature in °C (omitted if not available).
# TYPE extron_temperature_celsius gauge
extron_temperature_celsius{device="in1804-room-101",host="192.168.1.10"} 35.5

# HELP extron_power_mode Current power save mode (WPSAV): 0=Full power, 1=Lowest power (TP disabled), 2=Lower power (TP links active), 9=Over-heating.
# TYPE extron_power_mode gauge
extron_power_mode{device="in1804-room-101",host="192.168.1.10"} 0.0
```

---

## Requirements

- Python 3.10+
- Network access to the Extron device's SSH port (**22023** by default)

### Python dependencies

```
prometheus-client>=0.20.0
paramiko>=3.4.0
PyYAML>=6.0.1
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
# Edit config.yaml — the devices list is optional (see Scraping Modes below)
```

### 3. Run

```bash
python -m extronsis_exporter -c config.yaml
```

Metrics are available at `http://localhost:9877/metrics`.

---

## Docker

### Build and run with Docker Compose

```bash
cp config.example.yaml config.yaml
# Edit config.yaml
docker compose up -d
```

### Build and run manually

```bash
docker build -t extronsis-exporter .
docker run -d \
  -p 9877:9877 \
  -v $(pwd)/config.yaml:/etc/extronsis-exporter/config.yaml:ro \
  extronsis-exporter
```

---

## Endpoints

| Path | Description |
|------|-------------|
| `GET /probe` | Probe Extron SIS device(s) and return their metrics |
| `GET /metrics` | Exporter self-metrics (process info, request counters, probe duration histogram) |
| `GET /healthz` | Liveness check — always returns `200 OK` |

## Probing Modes

The `/probe` endpoint supports two modes, which can be used independently or together.

### Config-file mode

Devices are listed in `config.yaml`. A plain `GET /probe` scrapes all of them.

```yaml
devices:
  - name: in1804-room-101
    host: 192.168.1.10
```

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: extron_probe
    metrics_path: /probe
    static_configs:
      - targets: ["localhost:9877"]

  - job_name: extron_exporter
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9877"]
```

### URL-parameter mode

The target device is specified entirely via query parameters on each scrape
request. No config-file entry is required for the target device. This is the
recommended approach when managing many devices through Prometheus relabeling.

```
GET /probe?host=192.168.1.10&name=room-101&num_inputs=4
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `host` | **yes** | — | Hostname or IP address of the device |
| `name` | no | value of `host` | Label used in all Prometheus metrics |
| `port` | no | `22023` | SSH port |
| `username` | no | `admin` | SSH username |
| `password` | no | _(empty)_ | SSH password |
| `timeout` | no | `10.0` | Per-command timeout in seconds |
| `num_inputs` | no | `8` | Number of inputs to query |
| `num_outputs` | no | `1` | Number of outputs to query |

Prometheus scrape config using `params` and relabeling:

```yaml
scrape_configs:
  - job_name: extron_probe
    metrics_path: /probe
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_host
      - source_labels: [__param_host]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9877   # address of the exporter
    static_configs:
      - targets:
          - 192.168.1.10   # Extron device 1
          - 192.168.1.11   # Extron device 2

  - job_name: extron_exporter
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9877"]
```

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: extron
    static_configs:
      - targets: ["localhost:9877"]
```

### URL-parameter mode

The target device is specified entirely via query parameters on each scrape
request. No entry in the config file is required. This is useful when
managing many devices through Prometheus's `relabeling` feature.

```
GET /metrics?host=192.168.1.10&name=room-101&num_inputs=4
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `host` | **yes** | — | Hostname or IP address of the device |
| `name` | no | value of `host` | Label used in all Prometheus metrics |
| `port` | no | `22023` | SSH port |
| `username` | no | `admin` | SSH username |
| `password` | no | _(empty)_ | SSH password |
| `timeout` | no | `10.0` | Per-command timeout in seconds |
| `num_inputs` | no | `8` | Number of inputs to query |
| `num_outputs` | no | `1` | Number of outputs to query |

Prometheus scrape config using `params` and `relabeling`:

```yaml
scrape_configs:
  - job_name: extron
    metrics_path: /metrics
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_host
      - source_labels: [__param_host]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9877   # address of the exporter
    static_configs:
      - targets:
          - 192.168.1.10   # Extron device 1
          - 192.168.1.11   # Extron device 2
```

---

## Configuration Reference

```yaml
# HTTP server
listen_host: "0.0.0.0"   # Interface to listen on
listen_port: 9877          # Port to listen on

# Logging
log_level: INFO            # DEBUG | INFO | WARNING | ERROR | CRITICAL

# Devices
devices:
  - name: in1804-room-101  # Unique name used as a Prometheus label
    host: 192.168.1.10     # Hostname or IP address
    port: 22023            # SSH port (Extron SIS default: 22023)
    username: admin        # SSH username
    password: ""           # SSH password (leave empty if none)
    timeout: 10.0          # Per-command timeout in seconds
    num_inputs: 8          # Number of inputs to query (IN1804 = 8)
    num_outputs: 1         # Number of outputs to query (IN1804 = 1)
```

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EXTRONSIS_CONFIG` | Path to the configuration file | `config.yaml` |

### CLI flags

```
usage: extronsis-exporter [-h] [-c FILE] [--host HOST] [--port PORT] [--log-level LEVEL]

options:
  -c, --config FILE     Path to the YAML configuration file
  --host HOST           Override the HTTP listen host
  --port PORT           Override the HTTP listen port
  --log-level LEVEL     Override the log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
```

---

## Extron SIS Protocol Notes

The exporter communicates with the device using the following SIS commands:

| Command | Description |
|---------|-------------|
| `<n>!` | Query the input currently routed to output *n* |
| `<n>*I` | Query general information for input *n* (video type, audio mute, video mute, horizontal/vertical frequencies) |
| `<n>Z` | Query the audio mute status of output *n* |
| `<n>B` | Query the video mute status of output *n* |
| `WPSAV` | Query the power save mode (0=Full power, 1=Lowest power, 2=Lower power, 9=Over-heating) |
| `28STAT` | Query the internal temperature |
| `Q` | Query the firmware version |
| `N` | Query the part number |

Upon SSH login the device emits a banner in this format:

```
(c) Copyright 2023, Extron Electronics, IN1804 DO, V1.08, 60-1699-13
Mon, 09 Mar 2026 12:08:14
```

All fields from this banner are exposed via the `extron_device_info` metric.

---

## License

MIT
