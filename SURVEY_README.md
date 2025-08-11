# BTT Eddy Survey Mode - Temperature-Independent Probing

## Overview

Survey mode is a temperature-independent probing method for the BTT Eddy probe, inspired by Cartographer's implementation. Unlike the standard TAP mode which requires temperature compensation (`tap_adjust_z`) and heavily depends on exact temperature ranges, Survey mode uses a polynomial model that provides consistent results across different temperatures.

## Key Differences from TAP Mode

| Feature | TAP Mode | Survey Mode |
|---------|----------|-------------|
| Temperature Dependency | Yes - requires `tap_adjust_z` | No - temperature independent |
| Calibration Model | Piecewise interpolation | Full-range polynomial (degree 5-9) |
| Calibration Process | Manual paper test required | Fully automatic touch detection |
| Accuracy (RMSE) | ~0.01-0.05mm | ~0.10-0.15mm |
| Temperature Profiles | Required for consistency | Not needed |
| Stability | Varies with temperature | Consistent across temperatures |
| Touch Detection | Manual threshold setting | Automatic threshold detection |

## Installation

1. Copy the modified driver to Klipper:
```bash
cp probe_eddy_ng.py ~/klipper/klippy/extras/
sudo systemctl restart klipper
```

2. No configuration changes required - uses existing BTT Eddy config

## Complete Calibration Procedure

### Step 1: Basic Probe Calibration (if not done)
```gcode
# Only if probe is not calibrated
G28 X Y
PROBE_EDDY_NG_SETUP
# Follow instructions for manual Z=0 positioning
SAVE_CONFIG
```

### Step 2: Polynomial Calibration (Automatic)
```gcode
# After restart, home again
G28 X Y

# Polynomial calibration for Survey mode - fully automatic!
PROBE_EDDY_NG_CALIBRATE_POLY
# Automatically detects bed contact - no manual paper test needed
SAVE_CONFIG
```

### Step 3: Automatic Threshold Detection
```gcode
# After restart, home again
G28 X Y

# Automatically find optimal touch threshold
PROBE_EDDY_NG_THRESHOLD_SCAN
# Takes ~5-6 minutes, finds accurate bed contact point

# Check results
PROBE_EDDY_NG_THRESHOLD_STATUS
```

### Step 4: Survey Testing and Fine-tuning
```gcode
# Test Survey mode
PROBE_EDDY_NG_SURVEY Z_OFFSET=0
G1 Z0 F300
# Paper test to verify accuracy

# If adjustment needed (example values):
PROBE_EDDY_NG_SURVEY Z_OFFSET=-0.05  # For tighter contact
PROBE_EDDY_NG_SURVEY Z_OFFSET=0.05   # For looser contact
```

### Step 5: Create Production Macro
```ini
# Add to printer.cfg
[gcode_macro SURVEY]
gcode:
    PROBE_EDDY_NG_SURVEY Z_OFFSET=-0.05 {rawparams}  # Your calibrated offset
```

## Daily Usage

### With Saved Threshold (Recommended)
```gcode
# After initial threshold scan and save
G28 X Y
SURVEY  # Uses saved threshold from config
```

### Without Saved Threshold  
```gcode
# After printer power-on
G28 X Y
PROBE_EDDY_NG_THRESHOLD_SCAN  # Detect threshold (5-6 mins)
PROBE_EDDY_NG_THRESHOLD_SAVE  # Save threshold to config
SAVE_CONFIG                    # Make permanent (restarts Klipper)
# After restart:
G28 X Y
SURVEY                         # Use saved threshold
```

## Commands Reference

### PROBE_EDDY_NG_SURVEY Parameters
- `SAMPLES`: Number of probe samples (default: 3)
- `TOLERANCE`: Maximum standard deviation allowed (default: 0.010)
- `SPEED`: Probing speed (default: from config)
- `START_Z`: Starting height for probing (default: 5.0)
- `Z_OFFSET`: Manual offset adjustment (default: 0.0)
- `HOME_Z`: Set Z=0 after probing (default: 1)

### PROBE_EDDY_NG_CALIBRATE_POLY Parameters
- `DRIVE_CURRENT`: Drive current for calibration (default: current setting, 0-31)
- `START_Z`: Maximum calibration height (default: from config)
- `SPEED`: Probe speed during calibration (default: from config)
- `LIFT_SPEED`: Lift speed between moves (default: from config)
- `APPROACH_SPEED`: Speed when approaching bed (default: 1.0)
- `TOUCH_SPEED`: Speed for touch detection (default: 0.5)

### PROBE_EDDY_NG_THRESHOLD_SCAN Parameters
- `START_Z`: Starting height for scan (default: 2.0)
- `SCAN_SPEED`: Movement speed during scan (default: 0.5)
- `RETRIES`: Number of scan attempts (default: 3)

### Threshold Management Commands
```gcode
PROBE_EDDY_NG_THRESHOLD_STATUS  # Show current threshold and confidence
PROBE_EDDY_NG_THRESHOLD_SAVE    # Save current threshold to config
PROBE_EDDY_NG_THRESHOLD_CLEAR   # Clear saved threshold from config
```

## Technical Details

### Automatic Threshold Detection

The system uses a **sliding median filter algorithm** to detect bed contact:

1. **Frequency Analysis**: Monitors sensor frequency changes as nozzle approaches bed
2. **Rate Calculation**: Calculates first derivative (rate of frequency change)
3. **Acceleration Detection**: Calculates second derivative (acceleration of frequency change)
4. **Contact Detection**: Identifies moment when frequency growth rate significantly slows down
5. **Statistical Validation**: Uses median filtering to eliminate noise and ensure reliability

**Key Algorithm Features:**
- Detects contact when frequency growth rate drops by 50% or more
- Requires minimum frequency increase of 1.5% from baseline
- Uses 10-point sliding window for stability

### Polynomial Model

The Survey mode uses a 9th-degree polynomial fitted to the inverse frequency:
```python
height = polynomial(1/frequency)
```

The system automatically selects the optimal polynomial degree (5-9) based on RMSE during calibration.

### Temperature Independence

Unlike TAP mode which measures at a specific trigger height and applies temperature-based corrections, Survey mode:
1. Uses a full-range polynomial model instead of piecewise interpolation
2. Captures the entire frequency-to-height relationship
3. Provides consistent results without temperature profiles
4. Automatically adapts to current conditions through threshold detection

## Project Status

### ✅ Phase 1: Core Implementation (COMPLETED)
- [x] Polynomial model implementation in ProbeEddyFrequencyMap
- [x] PROBE_EDDY_NG_CALIBRATE_POLY command
- [x] PROBE_EDDY_NG_SURVEY command
- [x] Save/load polynomial calibration
- [x] Multi-sample averaging with deviation checking
- [x] Z_OFFSET parameter for fine-tuning

### ✅ Phase 2: Dynamic Threshold Detection (COMPLETED)
- [x] Implement PROBE_EDDY_NG_THRESHOLD_SCAN with sliding median filter algorithm
- [x] Dynamic threshold determination with high accuracy (~0.1mm)
- [x] Automatic bed contact detection without manual calibration
- [x] Statistical validation with confidence scoring
- [x] PROBE_EDDY_NG_THRESHOLD_STATUS command for monitoring

### 🔄 Phase 3: Enhanced Features (IN PROGRESS)
- [x] Automatic threshold detection eliminates manual Z_OFFSET guesswork
- [x] Persistent threshold storage in config file
- [ ] Surface type detection (textured/smooth)
- [ ] Adaptive sampling based on detected variance
- [ ] Integration with bed mesh for temperature-independent leveling

### Phase 4: Optimization (PENDING)
- [ ] Reduce polynomial degree while maintaining accuracy
- [ ] Implement adaptive polynomial fitting
- [ ] Speed optimization for rapid probing
- [ ] Memory usage optimization

### Phase 5: Advanced Features (PENDING)
- [ ] Multi-point temperature compensation model
- [ ] Automatic calibration validation
- [ ] Self-diagnostic capabilities

## Current Todo List

### High Priority
1. ~~**Implement persistent threshold storage**~~ ✅ COMPLETED - Threshold can now be saved to config
2. **Add configuration parameters for survey mode** - Make algorithm parameters configurable
3. **Create automated test suite** - Ensure reliability across different printer configurations

### Medium Priority
4. **Optimize threshold scan speed** - Reduce 5-6 minute scan time
5. **Add surface detection** - Adapt algorithm for different bed surfaces
6. **Implement bed mesh integration** - Use Survey for temperature-independent bed leveling

### Low Priority
7. **Add advanced diagnostics** - More detailed reporting and troubleshooting
8. **Performance optimization** - Memory usage and calculation speed improvements

## Known Issues and Limitations

1. ~~**Threshold Re-calibration Required**: Must run THRESHOLD_SCAN after each power cycle (~5-6 minutes)~~ - Fixed with persistent storage
2. **Higher RMSE**: Survey mode typically has RMSE of 0.10-0.15mm vs 0.01-0.05mm for TAP
3. **Algorithm Tuning**: May need parameter adjustment for different bed surfaces or probe heights

## Acknowledgments

- Cartographer probe team for the polynomial model inspiration
- BTT for the Eddy probe hardware
- Klipper community for testing and feedback
- Advanced sliding median filter algorithm development and implementation
