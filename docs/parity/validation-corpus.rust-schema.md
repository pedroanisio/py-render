# Validation corpus: Python vs the Rust oracle (schema: /home/admin/src/rs-scene-render/schema/scene-render-1.1.xsd)

Documents: **245** (221 invalid, the rest valid or warning-only).

| measure | value |
|---|---|
| verdict agreement (valid/invalid) | **97.6 %** |
| invalid documents rejected by Python | 215 / 221 |
| code set identical to the manifest | **95.5 %** |
| identical, counting rule families as one (R24-fill = R24) | 95.5 % |
| manifest codes ever reported by Python | 193 / 199 |
| expected codes found (recall over all document x code pairs) | **95.2 %** |
| documents where every expected code was found | 213 / 224 |
| ... and nothing new reported beyond the kitchen-sink baseline | 213 / 224 |

Baseline = codes Python reports for the unmutated `valid/kitchen-sink.scene.xml` (it lacks schema 1.2/1.3): none.

Verdict matrix: expected invalid / python invalid: 215; expected invalid / python valid: 6; expected valid / python valid: 24

Code-set outcome per document: exact 234, none 9, partial 2

## Per code

| code | expected in | detected | same family | reported instead |
|---|---|---|---|---|
| A01 | 1 | 0 | 0 | (none)×1 |
| A02 | 2 | 0 | 0 | (none)×2 |
| A03 | 1 | 0 | 0 | (none)×1 |
| A04 | 3 | 0 | 0 | (none)×2, MSQ2×1 |
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
| C45 | 1 | 1 | 1 |  |
| C46 | 1 | 1 | 1 |  |
| C47 | 1 | 1 | 1 |  |
| C48 | 1 | 1 | 1 |  |
| C50 | 1 | 1 | 1 |  |
| C51 | 1 | 1 | 1 |  |
| C52 | 1 | 1 | 1 |  |
| C53 | 1 | 1 | 1 |  |
| C54 | 1 | 1 | 1 |  |
| C55 | 1 | 1 | 1 |  |
| C56 | 1 | 1 | 1 |  |
| C57 | 1 | 1 | 1 |  |
| C58 | 1 | 1 | 1 |  |
| C59 | 2 | 2 | 2 |  |
| CRT1 | 1 | 1 | 1 |  |
| CRT2 | 1 | 1 | 1 |  |
| CRT3 | 1 | 1 | 1 |  |
| CRT4 | 1 | 1 | 1 |  |
| CRT5 | 1 | 1 | 1 |  |
| FRX1 | 1 | 1 | 1 |  |
| FRX2 | 1 | 1 | 1 |  |
| FRX3 | 1 | 1 | 1 |  |
| FRX4 | 1 | 1 | 1 |  |
| GEO1 | 1 | 1 | 1 |  |
| GEO2 | 1 | 1 | 1 |  |
| GEO3 | 1 | 1 | 1 |  |
| MSQ1 | 1 | 1 | 1 |  |
| MSQ2 | 1 | 1 | 1 |  |
| MSQ3 | 1 | 1 | 1 |  |
| MSQ4 | 1 | 1 | 1 |  |
| OCN1 | 1 | 1 | 1 |  |
| OCN2 | 1 | 1 | 1 |  |
| OCN3 | 1 | 1 | 1 |  |
| OCN4 | 1 | 1 | 1 |  |
| OCN5 | 1 | 1 | 1 |  |
| P3D1 | 1 | 1 | 1 |  |
| P3D2 | 1 | 1 | 1 |  |
| P3D3 | 1 | 1 | 1 |  |
| P3D4 | 1 | 1 | 1 |  |
| P3D5 | 1 | 1 | 1 |  |
| P3D6 | 3 | 3 | 3 |  |
| PYRO1 | 1 | 1 | 1 |  |
| PYRO2 | 1 | 1 | 1 |  |
| PYRO3 | 1 | 1 | 1 |  |
| PYRO4 | 1 | 1 | 1 |  |
| PYRO5 | 1 | 1 | 1 |  |
| PYRO6 | 1 | 1 | 1 |  |
| PYRO7 | 1 | 1 | 1 |  |
| PYRO8 | 3 | 3 | 3 |  |
| R1 | 1 | 1 | 1 |  |
| R2 | 1 | 1 | 1 |  |
| R3 | 1 | 1 | 1 |  |
| R4 | 1 | 1 | 1 |  |
| R5 | 1 | 1 | 1 |  |
| R6 | 2 | 2 | 2 |  |
| R7 | 1 | 1 | 1 |  |
| R8 | 1 | 1 | 1 |  |
| R9 | 1 | 1 | 1 |  |
| R10 | 2 | 2 | 2 |  |
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
| R26 | 1 | 1 | 1 |  |
| R27 | 1 | 1 | 1 |  |
| R28 | 1 | 1 | 1 |  |
| R29 | 1 | 1 | 1 |  |
| R30-colorEnd | 1 | 1 | 1 |  |
| R30-colorHigh | 1 | 1 | 1 |  |
| R30-colorLow | 1 | 1 | 1 |  |
| R30-headFill | 1 | 1 | 1 |  |
| R30-noData | 1 | 1 | 1 |  |
| R30-outline | 1 | 1 | 1 |  |
| R30-paint2 | 1 | 1 | 1 |  |
| R31-attenuationColor | 1 | 1 | 1 |  |
| R31-baseColor | 1 | 1 | 1 |  |
| R31-colorEnd | 1 | 1 | 1 |  |
| R31-colorHigh | 1 | 1 | 1 |  |
| R31-colorLow | 1 | 1 | 1 |  |
| R31-emissive | 1 | 1 | 1 |  |
| R31-foreground | 1 | 1 | 1 |  |
| R31-headFill | 1 | 1 | 1 |  |
| R31-keyColor | 1 | 1 | 1 |  |
| R31-noData | 1 | 1 | 1 |  |
| R31-outline | 1 | 1 | 1 |  |
| R31-paint2 | 1 | 1 | 1 |  |
| R31-shadowColor | 1 | 1 | 1 |  |
| R31-sheenColor | 1 | 1 | 1 |  |
| R31-specularColor | 1 | 1 | 1 |  |
| R32 | 1 | 1 | 1 |  |
| R33 | 1 | 1 | 1 |  |
| R34 | 1 | 1 | 1 |  |
| R35 | 1 | 1 | 1 |  |
| R36 | 1 | 1 | 1 |  |
| R37 | 1 | 1 | 1 |  |
| R38 | 1 | 1 | 1 |  |
| R39 | 2 | 2 | 2 |  |
| R40 | 1 | 1 | 1 |  |
| R41 | 1 | 1 | 1 |  |
| S01 | 1 | 1 | 1 |  |
| S02 | 2 | 2 | 2 |  |
| S03 | 2 | 2 | 2 |  |
| S04 | 1 | 1 | 1 |  |
| S05 | 1 | 1 | 1 |  |
| S06 | 10 | 9 | 9 | (none)×1 |
| S07 | 1 | 1 | 1 |  |
| S09 | 1 | 1 | 1 |  |
| S10 | 1 | 0 | 0 | (none)×1 |
| V1 | 1 | 1 | 1 |  |
| V2 | 1 | 1 | 1 |  |
| V3 | 1 | 1 | 1 |  |
| V4 | 1 | 1 | 1 |  |
| V5 | 1 | 1 | 1 |  |
| V6 | 1 | 1 | 1 |  |
| V7 | 1 | 1 | 1 |  |
| V8 | 1 | 1 | 1 |  |
| VOL1 | 1 | 1 | 1 |  |
| VOL2 | 1 | 1 | 1 |  |
| VOL3 | 1 | 1 | 1 |  |
| VOL4 | 1 | 1 | 1 |  |
| VOL5 | 1 | 1 | 1 |  |
| VOL6 | 1 | 1 | 1 |  |
| VOL7 | 1 | 1 | 1 |  |
| VOL8 | 1 | 1 | 1 |  |
| VOL9 | 1 | 1 | 1 |  |
| W01 | 2 | 0 | 0 | (none)×1, FRX4×1 |

## Documents with the wrong verdict

| document | expected | python | python reported |
|---|---|---|---|
| a01-missing-file.scene.xml | invalid A01 | valid |  |
| a02-generated-cache.scene.xml | invalid A02 | valid |  |
| a02-hash-mismatch.scene.xml | invalid A02 | valid |  |
| a04-sequence-frames.scene.xml | invalid A04 | valid |  |
| s06-idrefs-empty.scene.xml | invalid S06 | valid |  |
| s10-dangling-idref.scene.xml | invalid S10 | valid |  |
