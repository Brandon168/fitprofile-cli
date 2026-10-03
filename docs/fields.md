# Measurement record fields

Meanings come from static review of the Android app (v1.34.0), not from vendor documentation. "Confirmed" means a direct code reference; "likely" means inferred from display labels. Values the scale cannot measure are often sent as `0`, so zero is not evidence of a real zero. Everything is preserved raw; only `weight` and the timestamps get converted in reports.

| Field(s) | Meaning | Confidence |
|---|---|---|
| `measurement_id`, `user_id` | Opaque record and profile IDs (keep as strings) | confirmed |
| `time_stamp` | Weigh-in time, epoch seconds UTC | confirmed |
| `weight` | kg | confirmed |
| `height` | Profile height used in the calculation, cm (not measured stature) | confirmed |
| `stature` | Measured height, cm; only on height/weight devices | confirmed |
| `bmi` | Body-mass index, dimensionless | likely |
| `bmr` | Basal metabolic rate estimate, kcal/day | likely |
| `bodyage` | Metabolic/"body" age, integer; not calendar age | confirmed |
| `bodyfat`, `water`, `protein`, `subfat` | Percent (0-100, not fractions) | confirmed |
| `muscle` | Skeletal-muscle percent | confirmed |
| `sinew` | Muscle mass, kg (different from `muscle`) | confirmed |
| `bone` | Bone mass, kg | confirmed |
| `visfat` | Visceral-fat grade (integer level, not kg or %) | confirmed |
| `fat_free_weight` | Lean mass, kg | confirmed |
| `body_fat_mass`, `body_water_mass`, `protein_mass` | kg | confirmed |
| `heart_rate` | Pulse, beats/minute; 0 means not measured | confirmed |
| `score` | Vendor body score, dimensionless | confirmed |
| `body_shape` | Vendor body-type code 1-9 (1 hidden obesity, 2 hypokinetic, 3 lean, 4 normal, 5 lean-muscle, 6 obese, 7 overweight, 8 standard-muscle, 9 very muscular) | confirmed |
| `bodyfat_{left,right}_{arm,leg}`, `bodyfat_trunk` | Percent of whole-body weight that is that segment's fat (kg = raw x weight / 100) | confirmed |
| `sinew_{left,right}_{arm,leg}`, `sinew_trunk` | Segment muscle mass, kg | confirmed |
| `sea_*` (waist, hip, chest, abdomen, neck, arms, thighs) | Circumferences in cm; may be algorithm-derived, not tape measurements | confirmed |
| `resistance`, `sec_resistance`, `actual_*`, `resistance20_*`, `resistance100_*` | Impedance channels, conventionally ohms at 50/500, 20 and 100 kHz; raw algorithm inputs | mapping confirmed, units likely |
| `origin_resistances` | Comma-separated raw resistance values; order and scaling unknown | unresolved |
| `method`, `algorithm_8_version`, `mea_category`, `data_behavior`, `source_type`, bitmask fields (`measure_mode_flags`, `accuracy_flag`, `hall_sensor`, `parameter`) | Internal algorithm/provenance selectors. `accuracy_flag` is a weight-display bitmask, not measurement accuracy. Do not interpret as physiology | partly confirmed |
| `kalman`, `ma_distance`, `maclan` | Opaque internal filter state | internal |
| `scale_name`, `internal_model`, `device_name`, `mac`, `firmware_version`, `hw_ble_version`, `app_revision` | Device and app provenance (`mac` identifies your scale; treat exports as private) | confirmed |
| `remark`, scenario lists | User notes and labels | confirmed |

Body composition from bioimpedance is an estimate, not a clinical measurement. Multi-segment, girth and multi-frequency fields exist for other devices; their presence in a record does not mean your scale measured them.
