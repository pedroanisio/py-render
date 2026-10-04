# Validation corpus: Python vs the Rust oracle

Documents: **245** (221 invalid, the rest valid or warning-only).

| measure | value |
|---|---|
| verdict agreement (valid/invalid) | **90.2 %** |
| invalid documents rejected by Python | 219 / 221 |
| code set identical to the manifest | **3.7 %** |
| identical, counting rule families as one (R24-fill = R24) | 3.7 % |
| manifest codes ever reported by Python | 95 / 199 |
| expected codes found (recall over all document x code pairs) | **49.6 %** |
| documents where every expected code was found | 113 / 224 |
| ... and nothing new reported beyond the kitchen-sink baseline | 113 / 224 |

Baseline = codes Python reports for the unmutated `valid/kitchen-sink.scene.xml` (it lacks schema 1.2/1.3): C20, C33, R14, S02, S04, S06.

Verdict matrix: expected invalid / python invalid: 219; expected invalid / python valid: 2; expected valid / python invalid: 22; expected valid / python valid: 2

Code-set outcome per document: exact 9, extra 19, none 111, superset 106

## Per code

| code | expected in | detected | same family | reported instead |
|---|---|---|---|---|
| A01 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| A02 | 2 | 0 | 0 | C20×2, C33×2, R14×2, S02×2 |
| A03 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| A04 | 3 | 0 | 0 | S02×3, S06×3, C20×2, C33×2 |
| C1 | 1 | 1 | 1 |  |
| C2 | 1 | 1 | 1 |  |
| C3 | 1 | 1 | 1 |  |
| C4 | 1 | 1 | 1 |  |
| C5 | 1 | 1 | 1 |  |
| C6 | 1 | 1 | 1 |  |
| C7 | 1 | 1 | 1 |  |
| C8 | 1 | 1 | 1 |  |
| C9 | 1 | 1 | 1 |  |
| C10 | 1 | 1 | 1 |  |
| C11 | 1 | 1 | 1 |  |
| C12 | 1 | 1 | 1 |  |
| C13 | 2 | 2 | 2 |  |
| C14 | 1 | 1 | 1 |  |
| C15 | 2 | 2 | 2 |  |
| C16 | 1 | 1 | 1 |  |
| C17 | 1 | 1 | 1 |  |
| C18 | 1 | 1 | 1 |  |
| C19 | 1 | 1 | 1 |  |
| C20 | 1 | 1 | 1 |  |
| C21 | 1 | 1 | 1 |  |
| C22 | 1 | 1 | 1 |  |
| C23 | 1 | 1 | 1 |  |
| C24 | 1 | 1 | 1 |  |
| C25 | 1 | 1 | 1 |  |
| C26 | 1 | 1 | 1 |  |
| C27 | 1 | 1 | 1 |  |
| C28 | 1 | 1 | 1 |  |
| C29 | 1 | 1 | 1 |  |
| C30 | 1 | 1 | 1 |  |
| C31 | 1 | 1 | 1 |  |
| C32 | 1 | 1 | 1 |  |
| C33 | 1 | 1 | 1 |  |
| C34 | 1 | 1 | 1 |  |
| C35 | 1 | 1 | 1 |  |
| C36 | 1 | 1 | 1 |  |
| C37 | 1 | 1 | 1 |  |
| C38 | 2 | 2 | 2 |  |
| C39 | 1 | 1 | 1 |  |
| C40 | 1 | 1 | 1 |  |
| C41 | 1 | 1 | 1 |  |
| C42 | 1 | 1 | 1 |  |
| C43 | 1 | 1 | 1 |  |
| C44 | 1 | 1 | 1 |  |
| C45 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C46 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C47 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C48 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C50 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C51 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C52 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C53 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C54 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C55 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C56 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C57 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C58 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| C59 | 2 | 0 | 0 | C20×2, C33×2, R14×2, S02×2 |
| CRT1 | 1 | 0 | 0 | S02×1, S06×1 |
| CRT2 | 1 | 0 | 0 | S02×1, S06×1 |
| CRT3 | 1 | 0 | 0 | S02×1, S06×1 |
| CRT4 | 1 | 0 | 0 | S02×1, S06×1 |
| CRT5 | 1 | 0 | 0 | S02×1, S06×1 |
| FRX1 | 1 | 0 | 0 | S02×1, S06×1 |
| FRX2 | 1 | 0 | 0 | S02×1, S06×1 |
| FRX3 | 1 | 0 | 0 | S02×1, S06×1 |
| FRX4 | 1 | 0 | 0 | S02×1, S06×1 |
| GEO1 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| GEO2 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| GEO3 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| MSQ1 | 1 | 0 | 0 | R5×1, S02×1, S06×1 |
| MSQ2 | 1 | 0 | 0 | R5×1, S02×1, S06×1 |
| MSQ3 | 1 | 0 | 0 | R5×1, S02×1, S06×1 |
| MSQ4 | 1 | 0 | 0 | R5×1, S02×1, S06×1 |
| OCN1 | 1 | 0 | 0 | S02×1, S06×1 |
| OCN2 | 1 | 0 | 0 | S02×1, S06×1 |
| OCN3 | 1 | 0 | 0 | S02×1, S06×1 |
| OCN4 | 1 | 0 | 0 | S02×1, S06×1 |
| OCN5 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D1 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D2 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D3 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D4 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D5 | 1 | 0 | 0 | S02×1, S06×1 |
| P3D6 | 3 | 0 | 0 | S02×3, S06×3, S04×1 |
| PYRO1 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO2 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO3 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO4 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO5 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO6 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO7 | 1 | 0 | 0 | S02×1, S06×1 |
| PYRO8 | 3 | 0 | 0 | S02×3, S06×3, S04×1 |
| R1 | 1 | 1 | 1 |  |
| R2 | 1 | 1 | 1 |  |
| R3 | 1 | 1 | 1 |  |
| R4 | 1 | 1 | 1 |  |
| R5 | 1 | 1 | 1 |  |
| R6 | 2 | 2 | 2 |  |
| R7 | 1 | 1 | 1 |  |
| R8 | 1 | 1 | 1 |  |
| R9 | 1 | 1 | 1 |  |
| R10 | 2 | 1 | 1 | C20×1, C33×1, R14×1, S02×1 |
| R11 | 1 | 1 | 1 |  |
| R12 | 1 | 1 | 1 |  |
| R13 | 1 | 1 | 1 |  |
| R14 | 1 | 1 | 1 |  |
| R15 | 2 | 2 | 2 |  |
| R16 | 1 | 1 | 1 |  |
| R17 | 1 | 1 | 1 |  |
| R18 | 1 | 1 | 1 |  |
| R19 | 1 | 1 | 1 |  |
| R20 | 1 | 1 | 1 |  |
| R21 | 2 | 2 | 2 |  |
| R22 | 2 | 2 | 2 |  |
| R23 | 1 | 1 | 1 |  |
| R24-activeColor | 1 | 1 | 1 |  |
| R24-background | 1 | 1 | 1 |  |
| R24-color | 1 | 1 | 1 |  |
| R24-fill | 1 | 1 | 1 |  |
| R24-highlight | 1 | 1 | 1 |  |
| R24-paint | 1 | 1 | 1 |  |
| R24-stroke | 1 | 1 | 1 |  |
| R24-strokeColor | 1 | 1 | 1 |  |
| R25-activeColor | 1 | 1 | 1 |  |
| R25-background | 1 | 1 | 1 |  |
| R25-color | 1 | 1 | 1 |  |
| R25-fill | 1 | 1 | 1 |  |
| R25-highlight | 1 | 1 | 1 |  |
| R25-paint | 1 | 1 | 1 |  |
| R25-stroke | 1 | 1 | 1 |  |
| R25-strokeColor | 1 | 1 | 1 |  |
| R26 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R27 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R28 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R29 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-colorEnd | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-colorHigh | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-colorLow | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-headFill | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-noData | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-outline | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R30-paint2 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-attenuationColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-baseColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-colorEnd | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-colorHigh | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-colorLow | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-emissive | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-foreground | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-headFill | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-keyColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-noData | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-outline | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-paint2 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-shadowColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-sheenColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R31-specularColor | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R32 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R33 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R34 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R35 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R36 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R37 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R38 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R39 | 2 | 0 | 0 | C20×2, C33×2, R14×2, S02×2 |
| R40 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| R41 | 1 | 0 | 0 | C20×1, R14×1, S02×1, S04×1 |
| S01 | 1 | 1 | 1 |  |
| S02 | 2 | 2 | 2 |  |
| S03 | 2 | 2 | 2 |  |
| S04 | 1 | 1 | 1 |  |
| S05 | 1 | 1 | 1 |  |
| S06 | 10 | 10 | 10 |  |
| S07 | 1 | 1 | 1 |  |
| S09 | 1 | 1 | 1 |  |
| S10 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| V1 | 1 | 1 | 1 |  |
| V2 | 1 | 1 | 1 |  |
| V3 | 1 | 1 | 1 |  |
| V4 | 1 | 1 | 1 |  |
| V5 | 1 | 0 | 0 | C20×1, C33×1, R14×1, S02×1 |
| V6 | 1 | 0 | 0 | (none)×1 |
| V7 | 1 | 0 | 0 | (none)×1 |
| V8 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL1 | 1 | 0 | 0 | S02×1, S06×1 |
| VOL2 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL3 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL4 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL5 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL6 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL7 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL8 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| VOL9 | 1 | 0 | 0 | S02×1, S04×1, S06×1 |
| W01 | 2 | 0 | 0 | S02×2, S06×2, C20×1, C33×1 |

## Codes Python reports that the manifest does not expect (beyond the baseline)

R5×5

## Documents with the wrong verdict

| document | expected | python | python reported |
|---|---|---|---|
| advected-volume.scene.xml | valid  | invalid | S02, S04, S06 |
| baked-volume.scene.xml | valid  | invalid | S02, S04, S06 |
| crater.scene.xml | valid  | invalid | S02, S06 |
| fracture.scene.xml | valid  | invalid | S02, S06 |
| globe-relief.scene.xml | valid  | invalid | S02, S04, S06 |
| kitchen-sink.scene.xml | valid  | invalid | C20, C33, R14, S02, S04, S06 |
| mesh-sequence.scene.xml | valid  | invalid | R5, S02, S06 |
| ocean.scene.xml | valid  | invalid | S02, S06 |
| openvdb-sequence.scene.xml | valid  | invalid | S02, S04, S06 |
| openvdb.scene.xml | valid  | invalid | S02, S04, S06 |
| particles3d.scene.xml | valid  | invalid | S02, S06 |
| pyro-colliders.scene.xml | valid  | invalid | S02, S06 |
| pyro-fields.scene.xml | valid  | invalid | S02, S06 |
| pyro-mesh.scene.xml | valid  | invalid | S02, S06 |
| pyro.scene.xml | valid  | invalid | S02, S06 |
| solid-colliders.scene.xml | valid  | invalid | S02, S06 |
| thermal-volume.scene.xml | valid  | invalid | S02, S04, S06 |
| volume-sequence.scene.xml | valid  | invalid | S02, S04, S06 |
| volume.scene.xml | valid  | invalid | S02, S04, S06 |
| a03-remote.scene.xml | valid A03 | invalid | C20, C33, R14, S02, S04, S06 |
| a04-sequence-hold.scene.xml | valid A04 | invalid | C20, C33, R14, S02, S04, S06 |
| w01-non-finite.scene.xml | valid W01 | invalid | C20, C33, R14, S02, S04, S06 |
| v6-elements.scene.xml | invalid V6 | valid |  |
| v7-primitives.scene.xml | invalid V7 | valid |  |
