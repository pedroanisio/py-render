The reference `scene-v1.xsd` is the original scene-render 1.0 interchange
contract from the companion C17 renderer, copied without modifications from
`schema/scene-v1.xsd` at commit
`75fdd897253172cb68d622438e21f48abd237e4f` of the local `scene-render` repository
(`/home/admin/codebases/scene-render`). That repository is Apache-2.0 licensed.

SHA-256: `245cfeeeb4ad90b77118ce25d2415beffedb059e047097369952e701d1d4e356`.

`Schema.version_errors` uses its element/type relationships to reject new
sections, elements and asset kinds in documents declaring version 1.0. Actual
document attribute validation continues to use `scene-render-1.1.xsd`, as its
introduction requires compatibility for new attributes on 1.0 elements.
The companion repository's later 1.1 errata are not applied to this project's
1.1 contract.
