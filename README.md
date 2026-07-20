# OrthoInsight Load Cell Dashboard

This project is a load cell dashboard application that provides real-time plots of force and moment data from multiple load cells. The application is designed to run on a Raspberry Pi and utilizes PyQt for the graphical user interface and PyQtGraph for plotting.

## Project Structure

```
OrthoInsight
├── config
│   └── sensors.yaml
├── graphDash.py
└── README.md
```

## Configuration

The sensor configuration is stored in the `config/sensors.yaml` file. This file contains the following fields for each sensor:

- **name**: The name of the sensor.
- **bus**: The SPI bus number.
- **dev**: The device number on the SPI bus.
- **csb_gpio**: The GPIO pin number used for chip select.

### Example `sensors.yaml` Structure

```yaml
sensors:
  - name: "Cell 1"
    bus: 0
    dev: 0
    csb_gpio: 13
  - name: "Cell 2"
    bus: 6
    dev: 0
    csb_gpio: 12
  - name: "Cell 3"
    bus: 0
    dev: 0
    csb_gpio: 26
  - name: "Cell 4"
    bus: 6
    dev: 0
    csb_gpio: 16
```

## Requirements

To run this application, you need to install the following dependencies on your Raspberry Pi:

```bash
sudo apt install python3-pyqt6 python3-pyqtgraph python3-numpy python3-yaml
```

## Running the Application

1. **SSH into your Raspberry Pi** (if running remotely):
   ```bash
   ssh -X pi@<PI_IP>
   ```

2. **Run the application**:
   ```bash
   python3 graphDash.py
   ```

3. **Directly on the Pi with HDMI**:
   ```bash
   python3 graphDash.py
   ```

## Features

- Real-time plotting of force (Fx, Fy, Fz) and moment (Mx, My, Mz) data.
- Controls for starting/stopping data collection, adjusting sampling rate, and setting rolling window size.
- Tare functionality to zero the sensor readings.
- Clear data option to reset the displayed data.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.