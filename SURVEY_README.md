# BTT Eddy Survey Mode - Temperature-Independent Probing

## Overview

Survey mode is a temperature-independent probing method for the BTT Eddy probe, inspired by Cartographer's implementation. Unlike the standard TAP mode which requires temperature compensation (`tap_adjust_z`), Survey mode uses a polynomial model that provides consistent results across different temperatures.

## Key Differences from TAP Mode

| Feature | TAP Mode | Survey Mode |
|---------|----------|-------------|
| Temperature Dependency | Yes - requires `tap_adjust_z` | No - temperature independent |
| Calibration Model | Piecewise interpolation | Full-range polynomial (degree 5-9) |
| Accuracy (RMSE) | ~0.01-0.05mm | ~0.10-0.15mm |
| Temperature Profiles | Required for consistency | Not needed |
| Stability | Varies with temperature | Consistent across temperatures |

## Installation

1. Copy the modified driver to Klipper:
```bash
cp probe_eddy_ng.py ~/klipper/klippy/extras/
sudo systemctl restart klipper
```

2. No configuration changes required - uses existing BTT Eddy config

## Usage

### Initial Calibration (One-time)

```gcode
# Home X and Y first
G28 X Y

# Run polynomial calibration
PROBE_EDDY_NG_CALIBRATE_POLY

# Follow manual probe instructions for Z=0 reference
# Save configuration
SAVE_CONFIG
```

### Daily Use

```gcode
# Home all axes using Survey
G28 X Y
G28 Z
PROBE_EDDY_NG_SURVEY

# Or with parameters
PROBE_EDDY_NG_SURVEY SAMPLES=5 TOLERANCE=0.005 Z_OFFSET=-0.2
```

### Parameters

- `SAMPLES`: Number of probe samples (default: 3)
- `TOLERANCE`: Maximum standard deviation allowed (default: 0.010)
- `SPEED`: Probing speed (default: from config)
- `START_Z`: Starting height for probing (default: 5.0)
- `Z_OFFSET`: Manual offset adjustment (default: 0.0)
- `HOME_Z`: Set Z=0 after probing (default: 1)

### Z_OFFSET Calibration

1. Test without offset:
```gcode
PROBE_EDDY_NG_SURVEY
G1 Z0 F300
# Paper test - note the gap
```

2. Adjust if needed:
```gcode
# If nozzle is 0.2mm too high
PROBE_EDDY_NG_SURVEY Z_OFFSET=-0.2
G1 Z0 F300
# Verify with paper test
```

3. Save preferred offset in macro:
```ini
[gcode_macro SURVEY]
gcode:
    PROBE_EDDY_NG_SURVEY Z_OFFSET=-0.2 {rawparams}
```

## Log Filtering

Polynomial operations are logged with `_pol` suffix for easy filtering:
- `btt_eddy_pol: Using polynomial model...` - Polynomial operations
- `btt_eddy: ...` - Standard operations

```bash
# View only polynomial-related logs
grep "btt_eddy_pol" /tmp/klippy.log
```

## Technical Details

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

## Project Roadmap

### Phase 1: Core Implementation (COMPLETED)
- [x] Polynomial model implementation in ProbeEddyFrequencyMap
- [x] PROBE_EDDY_NG_CALIBRATE_POLY command
- [x] PROBE_EDDY_NG_SURVEY command
- [x] Save/load polynomial calibration
- [x] Multi-sample averaging with deviation checking
- [x] Z_OFFSET parameter for fine-tuning

### Phase 2: Dynamic Threshold Detection (IN PROGRESS)
- [ ] Implement PROBE_EDDY_NG_THRESHOLD_SCAN
- [ ] Dynamic threshold determination algorithm
- [ ] Automatic threshold optimization based on surface type

### Phase 3: Enhanced Features
- [ ] Automatic Z_OFFSET detection using multiple surface points
- [ ] Surface type detection (textured/smooth)
- [ ] Adaptive sampling based on detected variance
- [ ] Integration with bed mesh for temperature-independent leveling

### Phase 4: Optimization
- [ ] Reduce polynomial degree while maintaining accuracy
- [ ] Implement adaptive polynomial fitting
- [ ] Speed optimization for rapid probing
- [ ] Memory usage optimization

### Phase 5: Advanced Features
- [ ] Multi-point temperature compensation model
- [ ] Automatic calibration validation
- [ ] Self-diagnostic capabilities

## Known Issues and Limitations

1. **Higher RMSE**: Survey mode typically has RMSE of 0.10-0.15mm vs 0.01-0.05mm for TAP
2. **Z_OFFSET Required**: May need manual Z_OFFSET adjustment per printer

## Future Development Notes

### Priority Tasks

1. **Threshold Scan Implementation**
   - Port Cartographer's threshold scanning logic
   - Implement iterative threshold refinement
   - Add threshold validation mechanism

2. **Configuration Parameters**
   - Add `survey_polynomial_degree` config option
   - Add `survey_default_z_offset` config option
   - Add `survey_rmse_threshold` for calibration validation

3. **Testing Framework**
   - Create automated test suite for polynomial fitting
   - Add temperature cycling tests
   - Implement calibration validation tests

### Research Areas

1. **Polynomial Optimization**
   - Investigate lower-degree polynomials for speed
   - Research piecewise polynomial approaches
   - Study frequency domain filtering techniques

2. **Temperature Modeling**
   - Analyze residual temperature effects
   - Develop hybrid model combining polynomial and temperature data

3. **Surface Detection**
   - Implement FFT-based texture analysis
   - Develop surface classification algorithm
   - Create surface-specific probing strategies

## Acknowledgments

- Cartographer probe team for the polynomial model approach
- BTT for the Eddy probe hardware
- Klipper community for testing and feedback
