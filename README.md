# extronsis-exporter

A [Prometheus](https://prometheus.io/) exporter for [Extron](https://www.extron.com/) AV devices that support the **Simple Instruction Set (SIS)** protocol, such as the **Extron IN1804** video matrix switch.

Communication with the device happens over **SSH on port 22023** (the Extron SIS SSH port).

---

## Features

- Connects to one or more Extron SIS devices over SSH (port 22023)
- Parses the device login banner to expose model, firmware version, part number, and device date/time
- Exposes the **currently selected input** per output
- Exposes **input signal lock** status for each input
- Exposes **output audio and video mute** status for each output
- Exposes **internal device temperature** (where supported)
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
| `extron_input_signal_locked` | Gauge | `device`, `host`, `input` | `1` if the input has an active/locked signal, `0` otherwise |
| `extron_output_audio_muted` | Gauge | `device`, `host`, `output` | `1` if the output audio is muted, `0` otherwise |
| `extron_output_video_muted` | Gauge | `device`, `host`, `output` | `1` if the output video is muted, `0` otherwise |
| `extron_temperature_celsius` | Gauge | `device`, `host` | Internal device temperature in °C (omitted if not available) |

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

# HELP extron_input_signal_locked 1 if the input has a locked (active) signal, 0 otherwise.
# TYPE extron_input_signal_locked gauge
extron_input_signal_locked{device="in1804-room-101",host="192.168.1.10",input="1"} 1.0
extron_input_signal_locked{device="in1804-room-101",host="192.168.1.10",input="2"} 1.0
extron_input_signal_locked{device="in1804-room-101",host="192.168.1.10",input="3"} 0.0
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
# Edit config.yaml with your device details
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

## Configuration Reference

```yaml
# HTTP server
listen_host: "0.0.0.0"   # Interface to listen on
listen_port: 9877          # Port to listen on
metrics_path: /metrics     # Prometheus scrape path

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

## Prometheus Scrape Configuration

```yaml
scrape_configs:
  - job_name: extron
    static_configs:
      - targets: ["localhost:9877"]
```

---

## Extron SIS Protocol Notes

The exporter communicates with the device using the following SIS commands:

| Command | Description |
|---------|-------------|
| `<n>!` | Query the input currently routed to output *n* |
| `<n>LS` | Query the signal lock status of input *n* |
| `<n>Z` | Query the audio mute status of output *n* |
| `<n>B` | Query the video mute status of output *n* |
| `20STAT` | Query the internal temperature |
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
