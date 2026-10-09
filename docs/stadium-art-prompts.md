# Spectator artwork

The built-in ImageGen tool generated and refined the project asset
`static/textures/stadium-spectators-v2.png`. The final image is a 1254 × 1254
transparent RGBA atlas (about 1.2 MB). It contains eight seated spectators and
eight matching goal-reaction poses. Blue clothing lets the crowd shader change
shirt colors while preserving skin, hair, trousers, and shoes.

The generator did not place all figures inside equal-size cells. Rendering uses
the measured figure bounds so hands and shoes remain intact. The source image
was copied into the project with its original transparency.

## Generation prompt

Use case: stylized-concept. Asset type: production football-stadium spectator sprite atlas for a 3D browser game. Create a square 2048 by 2048 RGBA image with genuinely transparent background. EXACTLY 16 separate full-body spectator cutouts arranged in an exact 4-column by 4-row equal-cell grid, each cell 512 by 512; no grid lines, labels, lettering, shadows on a backdrop, chairs, scenery, or objects crossing cell boundaries. Photorealistic game-quality human cutouts, natural proportions, detailed faces, fabric folds, subtle neutral studio lighting, realistic skin tones and hair. Eight varied adults, men and women with diverse skin tones and hairstyles, all wearing plain vivid BLUE football supporter shirts or jackets, neutral gray/dark trousers, sneakers, some small blue scarves. Shirt blue must be distinct from natural skin and hair so a shader can tint the shirts only. First row: four distinct spectators seated, knees bent, hands in lap or casually clapping. Second row: four more distinct seated spectators with varied relaxed attentive poses. Third row: the SAME first four individuals, now standing and cheering, hands raised or clapping, natural asymmetrical poses. Fourth row: the SAME second four individuals, now standing and cheering. Keep matching neutral/cheer characters in the same column with exactly two rows separation. Each person centered horizontally within its cell. Feet end 24px above bottom of each cell, all body parts contained with 24px transparent margin at each edge. Standing people occupy most of cell height; seated people occupy roughly lower two thirds of cell, preserving believable shorter seated height. Front-facing or subtle three-quarter view, readable silhouette, full shoes visible. High detail rather than cartoon blocks. No text, logos, watermark, additional people, floating body parts, merged limbs or baked background. Transparent alpha everywhere between people.

## Refinement prompt

Edit this transparent spectator sprite atlas. Preserve these same sixteen blue-shirted spectator figures, realistic style, identities, clothing colors and transparency. Fix ONLY the production sprite layout: resize and reposition every figure into an EXACT four-column by four-row grid of equal square cells, aligned to the canvas edges. Every cell must contain exactly ONE whole figure, with all limbs and shoes strictly inside that cell and at least 8 percent of the cell width as empty transparent padding at ALL edges. No parts may cross into the next cell: especially shrink and lower the raised hands of the third and fourth rows so they do not spill into the seated rows. First and second rows are seated, third and fourth rows are matching standing cheers. Seated people should occupy only the lower two-thirds of their cell; standing cheers should fit into eighty percent of cell height. Align each figure's feet near its own cell bottom and center each figure in its cell. All gaps remain genuinely transparent alpha. No visible cell borders, no text, no background, no extra people. Use a square image with dimensions divisible by four, preferably 2048x2048.
