Tokyo Xtreme Racer 0 XMDL Toolkit 0.7.2
========================================

Install txr0_toolkit-0.7.2.zip using Edit > Preferences > Add-ons > Install
from Disk. Then use File > Import > Tokyo Xtreme Racer 0 (.xmdl).

This is an experimental, independent add-on for Genki PlayStation 2 XMDL files.
It does not change or depend on the Shutokou Battle Online MMDL add-on.

Version 0.7.0 reconstructs XMDL components and their ordered draw groups.
Version 0.7.1 treats a zero material word as untextured instead of TIM2 image 0.
Version 0.7.2 reserves material slot zero for untextured, colourable paint faces.
All internal objects from section 0 are visible, while objects belonging to later
top-level XMDL sections are hidden to prevent separate models from overlapping.
Reveal those sections in the Outliner or enable "Show all model sections".

"Correct face winding" uses the packed vertex normals to orient each generated
triangle. This fixes batches whose winding does not follow simple strip parity.
It supports position packets with optional UV and colour packets before packed
normals, as found in multi-model files. It also
corrects the model orientation and filters implausibly long triangles generated
where the original VU program used hidden strip-restart state.

"First internal object only" restores the old partial preview when needed. The
embedded TXR0 PRTS assembly map is not decoded yet, so upgrade variants may all
be visible together until their correct stock/stage grouping is known.

The final three single-object groups in four-group car models are labelled LOD2,
LOD3, and LOD4 and hidden by default. Long-triangle filtering is now disabled by
default because low-detail meshes legitimately use large polygons.

"Weld matching strip vertices" joins duplicated VIF-strip boundaries when both
position and packed normal match. It reduces false shading seams without merging
intentional hard edges.

Embedded TIM2 sections are decoded into packed Blender images automatically.
Standalone .tm2 and .tim2 files can also be loaded from File > Import. Supported
formats are 4-bit indexed, 8-bit indexed, and 32-bit RGBA with PS2 alpha and
256-colour CLUT conversion. Both 16-bit A1B5G5R5 and 32-bit TIM2 palettes are
decoded; the 16-bit palette support fixes incorrect colours in `unknown18` and
similar models. Material-to-texture assignment is not decoded yet.

V2-32 texture coordinates are imported as Blender UV maps for textured VIF
batches. Draw-group control records are decoded into per-face Blender material
assignments. The packed field 0x000C0000 selects TIM2 picture 12 for unknown18's
two door-handle batches, while 0x00120000 selects picture 18 for its two headlight
bezel batches. The following group record supplies the number of VIF batches that
share that texture.

"Geometry grouping" controls the hierarchy represented by Blender objects.
"Complete components" combines all draw groups belonging to one serialized XMDL
component and assigns materials per face. "Draw groups" separates material
regions such as paired handles while retaining their declared VIF-batch count.
"Raw VIF batches" remains the most granular diagnostic view.

The model header's declared logical-object count is retained separately from the
number of serialized component headers. TXR0 files can declare empty logical
slots which have no geometry record; these must not silently shift future PRTS
or MMDL indexes. Imported objects expose both counts and a Blender Text datablock
named `<model>_COMPONENTS.txt` lists every component, draw group, batch range,
texture index and source record offset.

If a later XMDL variant uses an unsupported draw-control layout, its batches are
retained as an explicitly labelled unknown group with no invented material. Draw
groups form a complete, ordered and non-overlapping partition of every imported
component.

PSMT4 and PSMT8 bitmap indices are unswizzled from PlayStation 2 GS memory order
before their palettes are applied.

Embedded PRTS sections are parsed as a 0x10-byte header followed by fixed 0xF0-byte
tables. Tables 1-3 classify and hide LOD2-LOD4. A Blender Text datablock named
`<model>_PRTS.txt` contains each table as a 15x16 slot grid, and relevant PRTS
metadata is stored in the imported objects' custom properties.

The experimental "Header material index" mode reads the upper 16 bits of
the 32-bit object-header word at +0x8C and uses that value as the TIM2 picture
index. The complete word and decoded index are retained as
`xmdl_header_material_word` and `xmdl_header_material_index` custom properties.
This field consistently contains plausible bounded values across the car
samples, but testing shows that it is not a direct TIM2 picture index. The
confirmed draw-group mapping is now the default instead.

The older experimental "Object index -> texture index" mode assigns TIM2 picture N to
internal object N when that object has UVs and the picture exists. The selected
index is stored as `xmdl_tim2_picture_index`. This is a testable assumption, not
a confirmed XMDL material mapping; choose "Do not assign" to disable it.
Known limitations: object/part names, complete upgrade selection, and exact
strip-control flags are not decoded yet. Alternate parts may still overlap.
