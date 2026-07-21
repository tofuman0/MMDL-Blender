SB Online Toolkit - Stage 3 v1.4.12

Fixes:
- Overfenders now expose an implicit Stock/none state followed by actual mesh stages.
- Interior/body upgrade meshes are mutually exclusive instead of progressively stacked.
- Interior/body controls include Stock/none even when no variant-0 mesh exists.
- Recognised MADO window layers and single-S GLAS material names as transparent glass.
- Multi-shell windows such as PS13 now apply stored opacity to both inner and outer layers.
- Recognised the game's GRASS/GRASS1 spelling as glass material metadata.
- Untextured panes now use their stored MMDL opacity, including AE86T3's 50% glass.
- Environment-glass passes use a light transparent overlay instead of obscuring the pane.
- LOD B, LOD C and Auxiliary D are hidden by default while LOD A remains visible.
- Semi-transparent glass now uses Blender's true blended surface mode.
- MIA archives are now indexed quickly and texture pixels are decoded only when used by the model.
- Shared-library scans no longer expand thousands of unrelated textures during every import.
- Replaced corrupted selected-stage and ellipsis characters with ASCII-safe UI text.
- Added a live two-colour paint preview using the confirmed material flag selectors.
- Textured paint materials multiply their texture by the selected preview colour.
- The material inspector now displays raw flags and the decoded paint channel.
- MMDL pointer placeholders are now labelled according to the confirmed game relocation routine.
- Parent records and serialized pointer fields are preserved for diagnostics and future export.
- Object-table parsing validates the confirmed 0x48/0x28/0x88/0xA0 record layout.
- PRTS files are now found both beside the MMDL and in its PRTS subfolder.
- The four PRTS tables are decoded as canonical-slot to MMDL-child mappings.
- Part-stage resolution now prefers the canonical PRTS slot over object-name ordering.
- The object inspector and diagnostic JSON expose parent, child and PRTS slot metadata.
- CARMODS array lengths now match the game structure, including muffler[9].
- Window materials preserve their real MIA alpha values, including semi-transparent glass.
- Projected-light textures use a brightness/black-key mask, removing black rectangular backgrounds even when the MIA alpha channel is fully opaque.
- Headlight and road-reflection textures are made emissive.
- BL floor-light geometry is treated as a shared light effect and remains linked to every selected headlight stage instead of being misread as stage 0 only.
- Existing Stage 3 CARMODS, overfender, number-plate and collision behaviour remains included.

Reimport the MMDL after installing because material nodes and object metadata are built during import.

Build
-----
From the repository root, run:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Version 1.4.4

The installable archive is written to dist\sb_online_toolkit-1.4.4.zip.
Install that ZIP from Blender's Add-ons preferences. The build synchronises the
version in bl_info and this README, and excludes Python cache files.
