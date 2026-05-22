# GMSL2 Camera Layout Diff

## Summary
- Source changed from `ea1fcd5a245b299b40f34ff45708b6cfcebc09e2eb756995fd41e5e66e3c06b8` to `d1bcf3cb08ff07581326370efcc58d85144e74c590c3c3eea9d4638e812acfe8`.
- Element counts: symbols 49 -> 49, wires 146 -> 142, labels 108 -> 84, junctions 15 -> 20.
- Drawing bbox moved from `{'max_x': 391.16, 'max_y': 254.0, 'min_x': 27.94, 'min_y': 35.56}` to `{'max_x': 391.16, 'max_y': 247.65, 'min_x': 45.72, 'min_y': 35.56}`.
- Symbol UUID continuity: 47 common, 2 removed, 2 added.
- Most references are currently unannotated (`?`) in the after snapshot, so UUID and value are the reliable comparison keys.

## Largest Symbol Moves
- `C55` -> `C?` `100nF`: (330.2, 142.24) -> (88.9, 62.23), d=254.219 mm
- `C38` -> `C?` `10nF 50V`: (381.0, 50.8) -> (175.26, 143.51), d=225.664 mm
- `L3` -> `L?` `SRN4018-4R7M`: (312.42, 132.08) -> (102.87, 57.15), d=222.544 mm, rot 0.0 -> 90.0
- `J14` -> `J?` `KH-FAKRA-Z-CB`: (38.1, 45.72) -> (181.61, 189.23), d=202.954 mm
- `U11` -> `U?` `AP63200WU-7`: (243.84, 132.08) -> (55.88, 67.31), d=198.807 mm
- `L2` -> `L?` `100uH PoC`: (312.42, 101.6) -> (156.21, 173.99), d=172.168 mm
- `L6` -> `L?` `6.8uH PoC`: (312.42, 223.52) -> (156.21, 158.75), d=169.106 mm
- `L5` -> `L?` `0.47uH PoC`: (312.42, 193.04) -> (156.21, 151.13), d=161.734 mm
- `L4` -> `L?` `0.47uH PoC`: (312.42, 162.56) -> (156.21, 143.51), d=157.367 mm
- `R52` -> `R?` `10k 1% CFG1=6G coax GMSL2`: (203.2, 243.84) -> (208.28, 97.79), d=146.138 mm
- `L7` -> `L?` `22uH PoC`: (287.02, 198.12) -> (156.21, 166.37), d=134.608 mm
- `R51` -> `R?` `10k CFG0=I2C 0x50`: (203.2, 198.12) -> (194.31, 67.31), d=131.112 mm
- `R31` -> `R?` `5.1k PoC damp`: (304.8, 154.94) -> (176.53, 173.99), d=129.677 mm
- `Y3` -> `Y?` `25MHz`: (152.4, 170.18) -> (153.67, 54.61), d=115.577 mm, rot 0.0 -> 270.0
- `F4` -> `F?` `500mA camera PoC fuse`: (106.68, 50.8) -> (175.26, 134.62), d=108.301 mm
- `R56` -> `R?` `402R 1%`: (177.8, 246.38) -> (284.48, 228.6), d=108.152 mm
- `R53` -> `R?` `49.9R 1% SIOP term`: (177.8, 154.94) -> (194.31, 87.63), d=69.305 mm
- `R59` -> `R?` `62.0k 1%`: (152.4, 111.76) -> (100.33, 83.82), d=59.093 mm
- `R55` -> `R?` `5.1k PoC damp`: (177.8, 215.9) -> (176.53, 166.37), d=49.546 mm
- `R57` -> `R?` `10k`: (152.4, 50.8) -> (160.02, 81.28), d=31.418 mm
- `R54` -> `R?` `5.1k PoC damp`: (177.8, 185.42) -> (176.53, 158.75), d=26.7 mm
- `C46` -> `C?` `100nF 50V`: (355.6, 81.28) -> (355.6, 81.28), d=0 mm
- `C44` -> `C?` `100nF GMSL AC-couple`: (381.0, 233.68) -> (381.0, 233.68), d=0 mm
- `C45` -> `C?` `100nF SIOP AC term`: (355.6, 50.8) -> (355.6, 50.8), d=0 mm

## Added/Removed Symbol UUIDs
Removed:
- `C58` `100pF` `Device:C` at `{'rot': 180.0, 'x': 330.2, 'y': 210.82}`
- `R60` `76.8k 1%` `Device:R` at `{'rot': 180.0, 'x': 152.4, 'y': 149.86}`
Added:
- `R1` `76.8k 1%` `Device:R` at `{'rot': 0.0, 'x': 100.33, 'y': 73.66}`
- `C1` `100pF` `Device:C` at `{'rot': 180.0, 'x': 105.41, 'y': 73.66}`

## Raw UUID Churn
- `hierarchical_label`: before=18, after=18, common=18, added=0, removed=0
- `junction`: before=15, after=20, common=0, added=20, removed=15
- `label`: before=108, after=84, common=82, added=2, removed=26
- `no_connect`: before=6, after=6, common=6, added=0, removed=0
- `symbol`: before=49, after=49, common=47, added=2, removed=2
- `wire`: before=146, after=142, common=97, added=45, removed=49
